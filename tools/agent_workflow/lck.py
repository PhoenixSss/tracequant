#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""Local Control Kernel v1 live-state resolution and Delivery Prepare.

This module is intentionally limited to the first LCK migration boundary:
resolving current mechanical facts, evaluating phase eligibility, and preparing
the local Task workspace.  Commit, push, PR mutation, lifecycle writes, merge,
and closeout effects remain outside this Task.

The resolver delegates Issue, relationship, Git snapshot, and PR query details
to the existing workflow helpers.  It does not use a snapshot, expected SHA,
or session handoff as authority.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

if __name__ != "__main__" or not any(p.endswith("agent_workflow") for p in sys.path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from pr_resolve import (
    PrResolveError,
    list_matching_prs,
    resolve_open_pr,
)
from workflow_common import (
    CommandRunner,
    WorkflowToolError,
    command_warning,
    is_sha,
    print_json,
)
from workflow_evidence import (
    _git_snapshot,
    _issue_view,
    _normalize_checks,
    _relationship_snapshot,
    _formal_blockers_gate,
    _repository_slug,
)


BASE_BRANCH: Final = "main"
LCK_SCHEMA_VERSION: Final = 1
TASK_BRANCH_PATTERN: Final = re.compile(
    r"^(?:task/(?P<slash>\d+)(?:-.+)?|task-(?P<dash>\d+)(?:-.+)?|(?P<legacy>\d+)-.+)$"
)


class Phase(StrEnum):
    """LCK phase capabilities exposed by the v1 core."""

    DELIVERY_PREPARE = "Delivery Prepare"
    REVIEW_PREPARE = "Review Prepare"
    REMEDIATION_PREPARE = "Remediation Prepare"
    CLOSEOUT = "Closeout"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    STOP = "stop"


class LckStopError(WorkflowToolError):
    """A deterministic LCK gate could not identify one safe action."""


def canonical_task_branch(task_number: int, title: str) -> str:
    """Derive the canonical Task branch from live Issue identity.

    ASCII words in the title are retained after Unicode normalization.  This
    keeps the branch stable for mixed-language titles without trusting a
    branch name supplied by an Agent.
    """
    title_without_prefix = re.sub(r"^\s*\[[^]]+\]\s*", "", title)
    normalized = unicodedata.normalize("NFKD", title_without_prefix)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-zA-Z0-9]+", ascii_title.casefold())
    slug = "-".join(words[:12]).strip("-")
    if not slug:
        slug = "task"
    return f"task/{task_number}-{slug}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def _items(value: Any) -> list[Any]:
    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        return list(value["items"])
    return []


def _branch_matches_task(branch: str, task_number: int) -> bool:
    match = TASK_BRANCH_PATTERN.fullmatch(branch)
    if match is None:
        return False
    number = next(
        (value for value in match.groups() if value is not None),
        None,
    )
    return number == str(task_number)


def _is_clean_current_main(git: Mapping[str, Any]) -> bool:
    """Return whether a new Task branch can safely be based on current main."""
    head_sha = git.get("head_sha")
    local_main_sha = git.get("local_main_sha")
    origin_main_sha = git.get("origin_main_sha")
    return (
        git.get("branch") == BASE_BRANCH
        and git.get("clean") is True
        and is_sha(head_sha)
        and is_sha(local_main_sha)
        and is_sha(origin_main_sha)
        and head_sha == local_main_sha == origin_main_sha
    )


def _remote_refs(stdout: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in stdout.splitlines():
        oid, separator, ref = line.partition("\t")
        if not separator or not is_sha(oid) or not ref.startswith("refs/heads/"):
            continue
        refs[ref.removeprefix("refs/heads/")] = oid
    return refs


@dataclass(frozen=True)
class LiveState:
    """Current mechanical facts acquired from Git and GitHub."""

    task_number: int
    repository: str | None
    issue: Mapping[str, Any] | None
    relationships: Mapping[str, Any]
    git: Mapping[str, Any]
    target_branch: str
    local_task_branch: str | None
    local_task_head: str | None
    remote_task_branch: str | None
    remote_task_oid: str | None
    open_pr: Mapping[str, Any] | None
    merged_pr_numbers: tuple[int, ...]
    merged: bool | None
    checks: Mapping[str, Any]
    cleanup: Mapping[str, Any]
    status: ResolutionStatus = ResolutionStatus.RESOLVED
    stop_reasons: tuple[str, ...] = ()
    warnings: tuple[Mapping[str, Any], ...] = ()

    @property
    def project_status(self) -> str | None:
        return (
            self.issue.get("project_status")
            if isinstance(self.issue, Mapping)
            and isinstance(self.issue.get("project_status"), str)
            else None
        )

    @property
    def issue_state(self) -> str | None:
        return (
            self.issue.get("state")
            if isinstance(self.issue, Mapping)
            and isinstance(self.issue.get("state"), str)
            else None
        )

    def to_dict(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _jsonable(
                {
                    "schema_version": LCK_SCHEMA_VERSION,
                    "task_number": self.task_number,
                    "repository": self.repository,
                    "issue": self.issue,
                    "relationships": self.relationships,
                    "git": self.git,
                    "target_branch": self.target_branch,
                    "local_task_branch": self.local_task_branch,
                    "local_task_head": self.local_task_head,
                    "remote_task_branch": self.remote_task_branch,
                    "remote_task_oid": self.remote_task_oid,
                    "open_pr": self.open_pr,
                    "merged_pr_numbers": self.merged_pr_numbers,
                    "merged": self.merged,
                    "checks": self.checks,
                    "cleanup": self.cleanup,
                    "status": self.status,
                    "stop_reasons": self.stop_reasons,
                    "warnings": self.warnings,
                }
            ),
        )


class LiveStateResolver:
    """Resolve one Task from current local and GitHub authority."""

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: CommandRunner | None = None,
        repository: str | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runner = runner or CommandRunner(self.repo_root)
        self.repository = repository

    def _git_lines(
        self,
        args: Sequence[str],
        *,
        command_id: str,
        warnings: list[dict[str, Any]],
    ) -> list[str]:
        result = self.runner.run(["git", *args], command_id=command_id)
        if result.returncode != 0:
            warnings.append(command_warning(result))
            return []
        return [line for line in result.stdout.splitlines() if line]

    def _task_branches(
        self,
        task_number: int,
        warnings: list[dict[str, Any]],
    ) -> tuple[set[str], dict[str, str], bool]:
        local_lines = self._git_lines(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads"],
            command_id="lck-local-task-branches",
            warnings=warnings,
        )
        local = {
            branch
            for branch in local_lines
            if _branch_matches_task(branch, task_number)
        }
        remote_result = self.runner.run(
            ["git", "ls-remote", "--heads", "origin"],
            command_id="lck-remote-task-branches",
        )
        if remote_result.returncode != 0:
            warnings.append(command_warning(remote_result))
            return local, {}, False
        remote_all = _remote_refs(remote_result.stdout)
        remote = {
            branch: oid
            for branch, oid in remote_all.items()
            if _branch_matches_task(branch, task_number)
        }
        return local, remote, True

    def resolve(self, task_number: int) -> LiveState:
        if task_number <= 0:
            raise LckStopError(f"Task number must be positive: {task_number}")

        warnings: list[dict[str, Any]] = []
        reasons: list[str] = []
        repository = _repository_slug(self.runner, self.repository, warnings)
        issue: Mapping[str, Any] | None = None
        relationships: Mapping[str, Any] = {"available": False}
        if repository is not None:
            issue = _issue_view(self.runner, repository, task_number, warnings)
            relationships = _relationship_snapshot(
                self.runner, repository, task_number, warnings
            )
        else:
            reasons.append("repository identity unavailable")

        git_warning_count = len(warnings)
        git = _git_snapshot(self.runner, warnings)
        if len(warnings) > git_warning_count:
            reasons.append("local Git snapshot contains unavailable facts")
        if git.get("origin_fetch") != "pass":
            reasons.append("current origin/main facts are unavailable")
        if not is_sha(git.get("local_main_sha")) or not is_sha(
            git.get("origin_main_sha")
        ):
            reasons.append("current main identity is unavailable")
        if issue is None:
            reasons.append("Task metadata unavailable")
        if relationships.get("available") is not True:
            reasons.append("Task relationship facts unavailable")
        branch_warning_count = len(warnings)
        local_branches, remote_branches, remote_available = self._task_branches(
            task_number, warnings
        )
        if len(warnings) > branch_warning_count:
            reasons.append("Task branch inventory contains unavailable facts")
        if not remote_available:
            reasons.append("remote Task branch facts unavailable")
        title = issue.get("title") if isinstance(issue, Mapping) else None
        title_text = title if isinstance(title, str) else f"Task {task_number}"
        canonical = canonical_task_branch(task_number, title_text)
        candidates = sorted(local_branches | set(remote_branches))
        if len(candidates) > 1:
            reasons.append(f"multiple Task branch candidates: {candidates}")
        target_branch = candidates[0] if len(candidates) == 1 else canonical
        local_branch = target_branch if target_branch in local_branches else None
        remote_branch = target_branch if target_branch in remote_branches else None

        local_head: str | None = None
        if local_branch is not None:
            result = self.runner.run(
                ["git", "rev-parse", f"refs/heads/{local_branch}"],
                command_id="lck-local-task-head",
            )
            if result.returncode == 0 and is_sha(result.stdout.strip()):
                local_head = result.stdout.strip()
            else:
                warnings.append(command_warning(result))
                reasons.append("local Task branch tip unavailable")
        remote_oid = remote_branches.get(target_branch)
        if (
            local_head is not None
            and remote_oid is not None
            and local_head != remote_oid
        ):
            reasons.append("local and remote Task branch tips differ")

        open_pr: Mapping[str, Any] | None = None
        merged_numbers: tuple[int, ...] = ()
        if repository is not None:
            try:
                open_pr = resolve_open_pr(
                    self.runner,
                    repository,
                    target_branch,
                    BASE_BRANCH,
                    warnings,
                )
            except PrResolveError as exc:
                reasons.append(str(exc))
            try:
                history = list_matching_prs(
                    self.runner,
                    repository,
                    target_branch,
                    BASE_BRANCH,
                    warnings,
                )
                merged_numbers = tuple(
                    sorted(
                        int(item["number"])
                        for item in history
                        if str(item.get("state", "")).upper() == "MERGED"
                        and isinstance(item.get("number"), int)
                    )
                )
            except PrResolveError as exc:
                reasons.append(str(exc))

        if open_pr is not None:
            checks = _normalize_checks(open_pr.get("statusCheckRollup"))
        else:
            checks = _normalize_checks([])
        if len(merged_numbers) > 1:
            reasons.append(
                f"multiple merged PRs for the Task branch: {list(merged_numbers)}"
            )
        if open_pr is not None:
            pr_head_oid = open_pr.get("headRefOid")
            if not is_sha(pr_head_oid):
                reasons.append("current OPEN PR head OID is unavailable")
            elif local_branch is None and remote_branch is None:
                reasons.append("current OPEN PR has no local or remote Task branch")
            else:
                if local_head is not None and local_head != pr_head_oid:
                    reasons.append(
                        "current OPEN PR head OID differs from local Task branch tip"
                    )
                if remote_oid is not None and remote_oid != pr_head_oid:
                    reasons.append(
                        "current OPEN PR head OID differs from remote Task branch tip"
                    )
        if open_pr is not None:
            merged: bool | None = False
        elif len(merged_numbers) == 1:
            merged = True
        elif len(merged_numbers) == 0:
            merged = False
        else:
            merged = None

        cleanup = {
            "business_delivery": "complete" if merged is True else "pending",
            "cleanup": "pending" if merged is True else "not-applicable",
            "local_branch_present": local_branch is not None,
            "remote_branch_present": remote_branch is not None,
            "open_pr_number": (
                open_pr.get("number") if isinstance(open_pr, Mapping) else None
            ),
            "merged_pr_numbers": merged_numbers,
        }
        status = ResolutionStatus.STOP if reasons else ResolutionStatus.RESOLVED
        return LiveState(
            task_number=task_number,
            repository=repository,
            issue=issue,
            relationships=relationships,
            git=git,
            target_branch=target_branch,
            local_task_branch=local_branch,
            local_task_head=local_head,
            remote_task_branch=remote_branch,
            remote_task_oid=remote_oid,
            open_pr=open_pr,
            merged_pr_numbers=merged_numbers,
            merged=merged,
            checks=checks,
            cleanup=cleanup,
            status=status,
            stop_reasons=tuple(reasons),
            warnings=tuple(warnings),
        )


@dataclass(frozen=True)
class PhaseDecision:
    phase: Phase
    eligible: bool
    reasons: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "capabilities": list(self.capabilities),
        }


class PhaseEligibilityResolver:
    """Apply static phase capabilities to current live preconditions."""

    def resolve(self, state: LiveState, phase: Phase) -> PhaseDecision:
        reasons = list(state.stop_reasons)
        issue = state.issue
        relationships = state.relationships
        if state.status is not ResolutionStatus.RESOLVED:
            reasons.append("live state resolution stopped")
        if state.repository is None:
            reasons.append("repository identity is unavailable")
        if not isinstance(issue, Mapping):
            reasons.append("Task metadata is unavailable")
        else:
            issue_state = str(issue.get("state", "")).upper()
            if phase is not Phase.CLOSEOUT and issue_state != "OPEN":
                reasons.append("Task is not OPEN")
            if phase is not Phase.CLOSEOUT:
                labels = set(_items(issue.get("labels")))
                lifecycle_labels = labels & {
                    "codex:needs-spec",
                    "codex:ready",
                    "codex:blocked",
                }
                if lifecycle_labels != {"codex:ready"}:
                    reasons.append(
                        "lifecycle labels must be exactly ['codex:ready']: "
                        f"{sorted(lifecycle_labels) or 'none'}"
                    )
                issue_type = relationships.get("issue_type")
                is_task = "type:task" in labels or (
                    isinstance(issue_type, str) and issue_type.casefold() == "task"
                )
                if not is_task:
                    reasons.append("target Issue is not a type:task")
            project = issue.get("project_status")
            allowed_projects = {
                Phase.DELIVERY_PREPARE: {"Ready", "In Progress"},
                Phase.REVIEW_PREPARE: {"Review", "In Progress"},
                Phase.REMEDIATION_PREPARE: {"Review"},
                Phase.CLOSEOUT: {"In Progress", "Review", "Done"},
            }[phase]
            if project not in allowed_projects:
                reasons.append("Project Status is unavailable or unknown")

        blocker_gate = _formal_blockers_gate(relationships)
        if blocker_gate.get("status") != "pass":
            detail = blocker_gate.get("detail") or "formal blocker gate did not pass"
            reasons.append(f"formal blocker gate: {detail}")

        if phase is Phase.DELIVERY_PREPARE:
            if state.merged is True:
                reasons.append("Task already has a merged PR")
            if (
                state.local_task_branch is not None
                and state.git.get("clean") is not True
            ):
                reasons.append("existing Task branch reuse requires a clean worktree")
            if state.local_task_head and state.remote_task_oid:
                if state.local_task_head != state.remote_task_oid:
                    reasons.append("Task branch has divergent local and remote tips")
            if not state.local_task_branch and not state.remote_task_branch:
                if not _is_clean_current_main(state.git):
                    reasons.append(
                        "new workspace bootstrap requires clean main with "
                        "HEAD == local main == origin/main"
                    )
            capabilities = ("prepare_task_workspace",)
        elif phase is Phase.REVIEW_PREPARE:
            if state.open_pr is None:
                reasons.append("no current OPEN PR")
            elif state.open_pr.get("isDraft") is not False:
                reasons.append("Review Prepare requires a non-Draft OPEN PR")
            if state.project_status not in {"Review", "In Progress"}:
                reasons.append("Task is not eligible for Review Prepare")
            capabilities = ("prepare_read_only_review_context",)
        elif phase is Phase.REMEDIATION_PREPARE:
            if state.open_pr is None:
                reasons.append("no current OPEN PR")
            if state.project_status != "Review":
                reasons.append("Remediation requires Project Status Review")
            capabilities = ("prepare_task_workspace",)
        else:
            if state.merged is not True:
                reasons.append("Closeout requires one current merged PR")
            if state.open_pr is not None:
                reasons.append("Closeout cannot proceed while an OPEN PR exists")
            capabilities = ("resolve_cleanup_state",)
        return PhaseDecision(
            phase=phase,
            eligible=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
            capabilities=capabilities,
        )


@dataclass(frozen=True)
class DeliveryContext:
    task_number: int
    repository: str | None
    branch: str
    base_sha: str | None
    action: str
    state: LiveState
    eligibility: PhaseDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "delivery-prepare",
            "task_number": self.task_number,
            "repository": self.repository,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "action": self.action,
            "state": self.state.to_dict(),
            "eligibility": self.eligibility.to_dict(),
        }


class DeliveryPreparer:
    """Prepare or restore one local Task workspace with bounded Git effects."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
    ) -> None:
        self.resolver = resolver
        self.eligibility = eligibility or PhaseEligibilityResolver()

    def _run_git(self, args: Sequence[str], command_id: str) -> None:
        result = self.resolver.runner.run(
            ["git", *args],
            command_id=command_id,
        )
        if result.returncode != 0:
            raise LckStopError(
                f"{command_id} failed with exit code {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def prepare(self, task_number: int) -> DeliveryContext:
        state = self.resolver.resolve(task_number)
        decision = self.eligibility.resolve(state, Phase.DELIVERY_PREPARE)
        if not decision.eligible:
            raise LckStopError(
                f"Delivery Prepare STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )

        branch = state.target_branch
        current_branch = state.git.get("branch")
        clean = state.git.get("clean") is True
        base_sha = state.git.get("local_main_sha")
        action = "already-prepared"
        local_exists = state.local_task_branch is not None

        if local_exists:
            if current_branch != branch:
                if not clean:
                    raise LckStopError(
                        "cannot switch to Task branch with a dirty unrelated worktree"
                    )
                self._run_git(
                    ["switch", branch],
                    "lck-select-existing-task-branch",
                )
                action = "selected-existing"
        elif state.remote_task_branch is not None:
            if current_branch != BASE_BRANCH or not clean:
                raise LckStopError(
                    "restoring a remote Task branch requires a clean main worktree"
                )
            self._run_git(
                ["switch", "--track", "-c", branch, f"origin/{branch}"],
                "lck-restore-remote-task-branch",
            )
            action = "restored-from-remote"
        else:
            if not _is_clean_current_main(state.git):
                raise LckStopError(
                    "new Task workspace requires clean main with "
                    "HEAD == local main == origin/main"
                )
            self._run_git(
                ["switch", "-c", branch, base_sha],
                "lck-create-task-branch",
            )
            action = "created-from-main"

        final_state = self.resolver.resolve(task_number)
        if (
            final_state.status is not ResolutionStatus.RESOLVED
            or final_state.git.get("branch") != branch
        ):
            raise LckStopError(
                "workspace postcondition failed: current live branch is not the "
                "resolved Task branch"
            )
        final_decision = self.eligibility.resolve(final_state, Phase.DELIVERY_PREPARE)
        if not final_decision.eligible:
            raise LckStopError(
                "workspace postcondition is no longer eligible: "
                + "; ".join(final_decision.reasons)
            )
        return DeliveryContext(
            task_number=task_number,
            repository=final_state.repository,
            branch=branch,
            base_sha=(
                final_state.git.get("local_main_sha")
                if isinstance(final_state.git.get("local_main_sha"), str)
                else None
            ),
            action=action,
            state=final_state,
            eligibility=final_decision,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LCK v1 live state operations")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--repository")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("task", type=int)

    delivery = commands.add_parser("delivery")
    delivery_commands = delivery.add_subparsers(dest="delivery_command", required=True)
    prepare = delivery_commands.add_parser("prepare")
    prepare.add_argument("task", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    resolver = LiveStateResolver(
        args.repo_root,
        repository=args.repository,
    )
    try:
        if args.command == "status":
            state = resolver.resolve(args.task)
            print_json(state.to_dict(), pretty=True)
            return 0 if state.status is ResolutionStatus.RESOLVED else 2
        if args.delivery_command == "prepare":
            context = DeliveryPreparer(resolver).prepare(args.task)
            print_json(context.to_dict(), pretty=True)
            return 0
        raise LckStopError("unsupported LCK command")
    except WorkflowToolError as exc:
        print(json.dumps({"status": "stop", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
