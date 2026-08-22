#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""Local Control Kernel v1 live-state resolution and Delivery control.

The resolver reacquires current Git/GitHub authority. Delivery effects are
bounded, operation-local, and fail closed: Critical Outcome + formal validation
bind the candidate tree before commit; only the resulting head may be pushed
and attached to one OPEN PR. No snapshot, expected SHA, or Agent-provided
branch/PR identity is workflow authority.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

if __name__ != "__main__" or not any(p.endswith("agent_workflow") for p in sys.path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from critical_outcome import (
    CriticalOutcomeError,
    contract_from_snapshot,
    verify_critical_outcome,
)
from pr_resolve import (
    PrResolveError,
    list_matching_prs,
    resolve_open_pr,
    resolve_or_create_pr,
)
from project_status import set_project_status_with_runner
from workflow_common import (
    CommandRunner,
    WorkflowToolError,
    command_warning,
    is_sha,
    print_json,
    read_json_text,
)
from workflow_evidence import (
    _git_snapshot,
    _issue_view,
    _normalize_checks,
    _relationship_snapshot,
    _formal_blockers_gate,
    _required_checks,
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
    DELIVERY_COMPLETE = "Delivery Complete"
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
        # Tip differences are live facts, not global resolver ambiguity.
        # Delivery Prepare rejects them; Delivery Complete may legitimately
        # observe a local validated commit that is not pushed yet.

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
                Phase.DELIVERY_COMPLETE: {"Ready", "In Progress"},
                Phase.REVIEW_PREPARE: {"Review", "In Progress"},
                Phase.REMEDIATION_PREPARE: {"Review"},
                Phase.CLOSEOUT: {"In Progress", "Review", "Done"},
            }[phase]
            if project not in allowed_projects:
                reasons.append("Project Status is unavailable or unknown")
            if phase in {Phase.DELIVERY_PREPARE, Phase.DELIVERY_COMPLETE}:
                critical = issue.get("critical_outcome")
                if (
                    not isinstance(critical, Mapping)
                    or critical.get("status") != "valid"
                ):
                    detail = (
                        critical.get("detail")
                        if isinstance(critical, Mapping)
                        else "contract unavailable"
                    )
                    reasons.append(f"Critical Outcome contract invalid: {detail}")

        blocker_gate = _formal_blockers_gate(relationships)
        if blocker_gate.get("status") != "pass":
            detail = blocker_gate.get("detail") or "formal blocker gate did not pass"
            reasons.append(f"formal blocker gate: {detail}")

        if phase is Phase.DELIVERY_PREPARE:
            if state.merged is True:
                reasons.append("Task already has a merged PR")
            if (
                state.local_task_branch is not None
                and state.git.get("branch") == state.local_task_branch
                and state.git.get("clean") is not True
            ):
                reasons.append("existing Task branch reuse requires a clean worktree")
            if state.local_task_head and state.remote_task_oid:
                if state.local_task_head != state.remote_task_oid:
                    reasons.append("Task branch has divergent local and remote tips")
            if state.open_pr is not None and state.local_task_head is not None:
                pr_head = state.open_pr.get("headRefOid")
                if is_sha(pr_head) and state.local_task_head != pr_head:
                    reasons.append(
                        "current OPEN PR head OID differs from local Task branch tip"
                    )
            if not state.local_task_branch and not state.remote_task_branch:
                if not _is_clean_current_main(state.git):
                    reasons.append(
                        "new workspace bootstrap requires clean main with "
                        "HEAD == local main == origin/main"
                    )
            capabilities = ("prepare_task_workspace",)
        elif phase is Phase.DELIVERY_COMPLETE:
            if state.merged is True:
                reasons.append("Task already has a merged PR")
            if state.local_task_branch is None:
                reasons.append("Delivery Complete requires a local Task branch")
            if state.git.get("branch") != state.target_branch:
                reasons.append(
                    "Delivery Complete requires the resolved Task branch selected"
                )
            if not is_sha(state.local_task_head):
                reasons.append("Delivery Complete requires a current local Task head")
            if state.open_pr is not None and state.open_pr.get("isDraft") is not False:
                reasons.append("Delivery Complete cannot continue with a Draft OPEN PR")
            capabilities = (
                "verify_critical_outcome",
                "run_formal_validation",
                "commit_current_tree",
                "ensure_remote_branch",
                "ensure_open_pr",
                "set_review_status",
            )
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


@dataclass(frozen=True)
class EffectReceipt:
    effect: str
    action: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect,
            "action": self.action,
            "details": _jsonable(self.details),
        }


class FormalValidationGate:
    """Run the repository-owned deterministic Delivery validation plan."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def run(self, base_sha: str) -> dict[str, Any]:
        if not is_sha(base_sha):
            raise LckStopError("formal validation base SHA is unavailable")
        tool = (
            self.resolver.repo_root
            / "tools"
            / "agent_workflow"
            / "workflow_validation.py"
        )
        result = self.resolver.runner.run(
            [
                sys.executable,
                str(tool),
                "run",
                "--repo-root",
                str(self.resolver.repo_root),
                "--phase",
                "delivery",
                "--base-sha",
                base_sha,
                "--include-skill-validators",
                "--require-skill-validator",
            ],
            command_id="lck-formal-delivery-validation",
            validation=True,
        )
        if not result.stdout.strip():
            raise LckStopError(
                "formal Delivery validation produced no structured result: "
                + (result.stderr.strip() or f"exit {result.returncode}")
            )
        try:
            payload = read_json_text(
                result.stdout, field="lck-formal-delivery-validation"
            )
        except WorkflowToolError as exc:
            raise LckStopError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise LckStopError("formal Delivery validation result is not an object")
        if result.returncode != 0 or payload.get("status") != "pass":
            raise LckStopError(
                "formal Delivery validation failed: "
                + str(payload.get("status") or result.returncode)
            )
        return payload


class CommitCurrentTreeEffect:
    """Commit exactly the staged tree that passed Critical Outcome + validation."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _run(self, argv: Sequence[str], command_id: str) -> Any:
        result = self.resolver.runner.run(argv, command_id=command_id)
        if result.returncode != 0:
            raise LckStopError(
                f"{command_id} failed with exit code {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def stage_candidate_tree(self) -> str:
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-delivery-status-before-stage",
        )
        if not status.stdout.strip():
            raise LckStopError("Delivery Complete found no uncommitted Task changes")
        self._run(["git", "add", "-A", "--", ":/"], "lck-stage-current-tree")
        cached = self.resolver.runner.run(
            ["git", "diff", "--cached", "--quiet"],
            command_id="lck-staged-diff-present",
        )
        if cached.returncode == 0:
            raise LckStopError("Delivery candidate contains no staged changes")
        if cached.returncode not in {0, 1}:
            raise LckStopError("unable to determine staged Delivery diff")
        check = self.resolver.runner.run(
            ["git", "diff", "--cached", "--check"],
            command_id="lck-staged-diff-check",
        )
        if check.returncode != 0:
            raise LckStopError(
                "staged Delivery diff failed git diff --check: "
                + (check.stderr.strip() or check.stdout.strip())
            )
        tree = self._run(
            ["git", "write-tree"], "lck-write-candidate-tree"
        ).stdout.strip()
        if not is_sha(tree):
            raise LckStopError("candidate tree OID is unavailable")
        return tree

    def current_head_tree(self) -> str:
        tree = self._run(
            ["git", "rev-parse", "HEAD^{tree}"],
            "lck-current-head-tree",
        ).stdout.strip()
        if not is_sha(tree):
            raise LckStopError("current HEAD tree OID is unavailable")
        return tree

    def verify_tree_unchanged(self, expected_tree: str) -> None:
        unstaged = self.resolver.runner.run(
            ["git", "diff", "--quiet"],
            command_id="lck-post-validation-unstaged-check",
        )
        if unstaged.returncode != 0:
            if unstaged.returncode == 1:
                raise LckStopError("working tree changed during formal validation")
            raise LckStopError("unable to verify post-validation working tree")
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-post-validation-status",
        )
        # Staged entries are expected. Any untracked/unstaged entry makes status
        # contain a line not represented by the staged tree; git diff --quiet
        # already rejects tracked unstaged changes, and untracked paths start '??'.
        if any(line.startswith("??") for line in status.stdout.splitlines()):
            raise LckStopError("untracked files appeared during formal validation")
        current_tree = self._run(
            ["git", "write-tree"], "lck-write-tree-post-validation"
        ).stdout.strip()
        if current_tree != expected_tree:
            raise LckStopError("validated candidate tree changed before commit")

    def execute(self, expected_tree: str, message: str) -> EffectReceipt:
        if not message.strip() or "\x00" in message or len(message) > 240:
            raise LckStopError("commit message must be non-empty and <= 240 characters")
        current_tree = self._run(
            ["git", "write-tree"], "lck-write-tree-pre-commit"
        ).stdout.strip()
        if current_tree != expected_tree:
            raise LckStopError(
                "commit precondition failed: staged tree is not validated tree"
            )
        self._run(["git", "commit", "-m", message], "lck-commit-current-tree")
        head = self._run(
            ["git", "rev-parse", "HEAD"], "lck-head-after-commit"
        ).stdout.strip()
        tree = self._run(
            ["git", "rev-parse", "HEAD^{tree}"], "lck-tree-after-commit"
        ).stdout.strip()
        if not is_sha(head) or tree != expected_tree:
            raise LckStopError(
                "commit postcondition failed: committed tree != validated tree"
            )
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-status-after-commit",
        )
        if status.stdout.strip():
            raise LckStopError("commit postcondition failed: worktree is not clean")
        return EffectReceipt(
            effect="commit_current_tree",
            action="committed",
            details={"head_sha": head, "tree_oid": tree},
        )


class EnsureRemoteBranchEffect:
    """Ensure origin Task branch equals current local HEAD without force push."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _identity(self, branch: str) -> tuple[str, str | None]:
        branch_result = self.resolver.runner.run(
            ["git", "branch", "--show-current"], command_id="lck-push-current-branch"
        )
        head_result = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"], command_id="lck-push-current-head"
        )
        if branch_result.returncode != 0 or head_result.returncode != 0:
            raise LckStopError("cannot resolve local branch identity before push")
        current_branch = branch_result.stdout.strip()
        head = head_result.stdout.strip()
        if current_branch != branch or not is_sha(head):
            raise LckStopError("push precondition failed: local Task identity changed")
        remote_result = self.resolver.runner.run(
            ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
            command_id="lck-push-remote-head",
        )
        if remote_result.returncode != 0:
            raise LckStopError("cannot resolve remote Task branch before push")
        refs = _remote_refs(remote_result.stdout)
        remote_oid = refs.get(branch)
        return head, remote_oid

    def execute(self, branch: str) -> EffectReceipt:
        head, remote_oid = self._identity(branch)
        if remote_oid == head:
            return EffectReceipt(
                effect="ensure_remote_branch",
                action="already-synced",
                details={"head_sha": head, "remote_oid": remote_oid},
            )
        if remote_oid is not None:
            fetch = self.resolver.runner.run(
                ["git", "fetch", "--no-tags", "origin", f"refs/heads/{branch}"],
                command_id="lck-fetch-observed-remote-task-branch",
            )
            if fetch.returncode != 0:
                raise LckStopError("cannot verify existing remote Task branch ancestry")
            fetched = self.resolver.runner.run(
                ["git", "rev-parse", "FETCH_HEAD"],
                command_id="lck-fetched-remote-task-head",
            )
            if fetched.returncode != 0 or fetched.stdout.strip() != remote_oid:
                raise LckStopError(
                    "remote Task branch changed during push precondition check"
                )
            ancestor = self.resolver.runner.run(
                ["git", "merge-base", "--is-ancestor", remote_oid, head],
                command_id="lck-remote-fast-forward-check",
            )
            if ancestor.returncode != 0:
                raise LckStopError(
                    "remote Task branch is ahead/diverged; LCK will not rebase or force push"
                )
            action = "fast-forwarded"
        else:
            action = "created"

        push = self.resolver.runner.run(
            ["git", "push", "-u", "origin", f"HEAD:refs/heads/{branch}"],
            command_id="lck-push-task-branch",
        )
        if push.returncode != 0:
            raise LckStopError(
                "Task branch push failed: "
                + (push.stderr.strip() or push.stdout.strip())
            )
        final_head, final_remote = self._identity(branch)
        if final_head != head or final_remote != head:
            raise LckStopError(
                "push postcondition failed: local and remote heads differ"
            )
        return EffectReceipt(
            effect="ensure_remote_branch",
            action=action,
            details={"head_sha": head, "remote_oid": final_remote},
        )


class EnsureOpenPrEffect:
    """Resolve or create exactly one non-Draft OPEN PR from live identity."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        task_number: int,
        *,
        summary: str,
        risks: str,
        critical_outcome: Mapping[str, Any],
        validation: Mapping[str, Any],
        expected_base_sha: str,
        expected_body_sha256: str,
    ) -> EffectReceipt:
        state = self.resolver.resolve(task_number)
        if state.status is not ResolutionStatus.RESOLVED:
            raise LckStopError(
                "cannot ensure OPEN PR from unresolved live state: "
                + "; ".join(state.stop_reasons)
            )
        repository = state.repository
        head = state.local_task_head
        remote = state.remote_task_oid
        base = state.git.get("origin_main_sha")
        issue = state.issue
        if (
            not isinstance(repository, str)
            or not is_sha(head)
            or remote != head
            or not is_sha(base)
            or not isinstance(issue, Mapping)
        ):
            raise LckStopError("OPEN PR preconditions are incomplete")
        if base != expected_base_sha:
            raise LckStopError("OPEN PR precondition failed: origin/main changed")
        if issue.get("body_sha256") != expected_body_sha256:
            raise LckStopError("OPEN PR precondition failed: Task body changed")
        title = issue.get("title")
        if not isinstance(title, str) or not title.strip():
            raise LckStopError("Task title is unavailable for PR creation")
        body = (
            f"Closes #{task_number}\n\n"
            "## Summary\n"
            f"{summary.strip()}\n\n"
            "## Validation\n"
            f"- Critical Outcome: {critical_outcome.get('status')}\n"
            f"- Formal Delivery validation: {validation.get('status')}\n\n"
            "## Risks / limitations\n"
            f"{risks.strip() or 'None identified.'}\n"
        )
        warnings: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="lck-pr-") as temp_dir:
            body_file = Path(temp_dir) / "body.md"
            body_file.write_text(body, encoding="utf-8", newline="\n")
            try:
                result = resolve_or_create_pr(
                    self.resolver.runner,
                    repository,
                    state.target_branch,
                    BASE_BRANCH,
                    title,
                    body_file,
                    head,
                    str(base),
                    warnings,
                )
            except PrResolveError as exc:
                raise LckStopError(str(exc)) from exc
        return EffectReceipt(
            effect="ensure_open_pr",
            action=str(result.get("action")),
            details={
                "number": result.get("number"),
                "url": result.get("url"),
                "head_sha": result.get("head_sha"),
                "base_sha": result.get("base_sha"),
                "warnings": warnings,
            },
        )


class SetReviewStatusEffect:
    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(self, task_number: int) -> EffectReceipt:
        state = self.resolver.resolve(task_number)
        if state.status is not ResolutionStatus.RESOLVED or state.repository is None:
            raise LckStopError("cannot set Review status from unresolved live state")
        if state.open_pr is None:
            raise LckStopError(
                "Project Status may move to Review only after OPEN PR exists"
            )
        if state.project_status == "Review":
            return EffectReceipt(
                effect="set_review_status", action="already-review", details={}
            )
        set_project_status_with_runner(
            self.resolver.runner,
            state.repository,
            task_number,
            value="Review",
        )
        final = self.resolver.resolve(task_number)
        if final.project_status != "Review":
            raise LckStopError("Project Status postcondition failed: expected Review")
        return EffectReceipt(
            effect="set_review_status", action="updated", details={"status": "Review"}
        )


class DeliveryChecksGate:
    """Wait boundedly for applicable PR checks using current GitHub facts."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        timeout_seconds: float = 600.0,
        poll_seconds: float = 5.0,
    ) -> None:
        self.resolver = resolver
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.poll_seconds = max(0.0, poll_seconds)

    @staticmethod
    def _required_names(required: Mapping[str, Any]) -> set[str]:
        contexts = required.get("contexts")
        if not isinstance(contexts, Mapping):
            return set()
        items = contexts.get("items")
        if not isinstance(items, list):
            return set()
        return {str(item) for item in items if isinstance(item, str) and item}

    @staticmethod
    def _observed_categories(checks: Mapping[str, Any]) -> dict[str, str]:
        bounded = checks.get("items")
        if not isinstance(bounded, Mapping):
            return {}
        items = bounded.get("items")
        if not isinstance(items, list):
            return {}
        values: dict[str, str] = {}
        for item in items:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            category = item.get("category")
            if isinstance(name, str) and isinstance(category, str):
                values[name] = category
        return values

    @staticmethod
    def _pr_identity(state: LiveState) -> dict[str, Any]:
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            return {}
        return {
            "number": pr.get("number"),
            "head_sha": pr.get("headRefOid"),
            "base_sha": pr.get("baseRefOid"),
        }

    def run(self, task_number: int) -> dict[str, Any]:
        state = self.resolver.resolve(task_number)
        if state.status is not ResolutionStatus.RESOLVED or state.repository is None:
            raise LckStopError("cannot evaluate checks from unresolved live state")
        warnings: list[dict[str, Any]] = []
        required = _required_checks(
            self.resolver.runner,
            state.repository,
            BASE_BRANCH,
            warnings,
        )
        required_names = self._required_names(required)
        config = required.get("configuration")
        deadline = time.monotonic() + self.timeout_seconds

        while True:
            current = self.resolver.resolve(task_number)
            if (
                current.status is not ResolutionStatus.RESOLVED
                or current.open_pr is None
            ):
                raise LckStopError("PR identity changed while waiting for checks")
            checks = current.checks
            observed = self._observed_categories(checks)
            failed = int(checks.get("failed", 0) or 0)
            unknown = int(checks.get("skipped_or_unknown", 0) or 0)
            pending = int(checks.get("pending", 0) or 0)

            if failed > 0 or unknown > 0:
                raise LckStopError("PR checks failed, cancelled, skipped, or unknown")

            if pending == 0:
                if required_names:
                    failed_required = {
                        name
                        for name in required_names
                        if observed.get(name) not in {None, "success", "pending"}
                    }
                    if failed_required:
                        raise LckStopError(
                            "required PR checks failed: "
                            + ", ".join(sorted(failed_required))
                        )
                    if all(observed.get(name) == "success" for name in required_names):
                        return {
                            "status": "pass",
                            "configuration": config,
                            "required": sorted(required_names),
                            "observed": checks,
                            "pr": self._pr_identity(current),
                            "warnings": warnings,
                        }
                elif config in {"configured-empty", "not-configured"}:
                    if checks.get("count") == 0 or checks.get("all_success") is True:
                        return {
                            "status": "pass",
                            "configuration": config,
                            "required": [],
                            "observed": checks,
                            "pr": self._pr_identity(current),
                            "warnings": warnings,
                        }
                elif checks.get("count", 0) > 0 and checks.get("all_success") is True:
                    # GitHub can hide branch-protection configuration behind a plan
                    # boundary. Successful observed checks are preserved as a
                    # capability-limited fact instead of being upgraded to proof of
                    # required-check configuration.
                    return {
                        "status": "pass",
                        "configuration": config,
                        "required": [],
                        "observed": checks,
                        "pr": self._pr_identity(current),
                        "warnings": warnings,
                        "limitation": "required-check configuration unavailable",
                    }

            if time.monotonic() >= deadline:
                detail = (
                    f"required={sorted(required_names)}, observed={sorted(observed)}, "
                    f"pending={pending}, configuration={config}"
                )
                raise LckStopError(
                    "timed out waiting for successful PR checks: " + detail
                )
            time.sleep(self.poll_seconds)


@dataclass(frozen=True)
class DeliveryCompletionResult:
    task_number: int
    status: str
    branch: str
    head_sha: str
    critical_outcome: Mapping[str, Any]
    validation: Mapping[str, Any]
    checks: Mapping[str, Any]
    effects: tuple[EffectReceipt, ...]
    final_state: LiveState

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "delivery-complete",
            "task_number": self.task_number,
            "status": self.status,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "critical_outcome": _jsonable(self.critical_outcome),
            "validation": _jsonable(self.validation),
            "checks": _jsonable(self.checks),
            "effects": [item.to_dict() for item in self.effects],
            "final_state": self.final_state.to_dict(),
            "human_boundary": "Independent Review must be started separately",
        }


class DeliveryCompleter:
    """Complete initial Delivery using bounded LCK-owned lifecycle effects."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        formal_validation: FormalValidationGate | None = None,
        commit_effect: CommitCurrentTreeEffect | None = None,
        remote_effect: EnsureRemoteBranchEffect | None = None,
        pr_effect: EnsureOpenPrEffect | None = None,
        status_effect: SetReviewStatusEffect | None = None,
        checks_gate: DeliveryChecksGate | None = None,
    ) -> None:
        self.resolver = resolver
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.formal_validation = formal_validation or FormalValidationGate(resolver)
        self.commit_effect = commit_effect or CommitCurrentTreeEffect(resolver)
        self.remote_effect = remote_effect or EnsureRemoteBranchEffect(resolver)
        self.pr_effect = pr_effect or EnsureOpenPrEffect(resolver)
        self.status_effect = status_effect or SetReviewStatusEffect(resolver)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)

    def _run_critical_outcome(self, state: LiveState) -> dict[str, Any]:
        issue = state.issue
        snapshot = issue.get("critical_outcome") if isinstance(issue, Mapping) else None
        try:
            contract = contract_from_snapshot(snapshot)
            result = verify_critical_outcome(
                self.resolver.repo_root,
                self.resolver.runner,
                contract,
            )
        except CriticalOutcomeError as exc:
            raise LckStopError(f"Critical Outcome contract invalid: {exc}") from exc
        payload = result.to_dict()
        if result.status != "pass":
            raise LckStopError(
                "Critical Outcome FAIL: "
                f"{contract.verification_test} exited {result.exit_code}"
            )
        return payload

    def _has_task_diff(self, base_sha: str) -> bool:
        result = self.resolver.runner.run(
            ["git", "diff", "--quiet", f"{base_sha}...HEAD"],
            command_id="lck-task-diff-present",
        )
        if result.returncode == 1:
            return True
        if result.returncode == 0:
            return False
        raise LckStopError("unable to verify Task diff against current origin/main")

    def _operation_guard(
        self,
        task_number: int,
        *,
        base_sha: str,
        body_sha256: str,
        branch: str,
        head_sha: str | None = None,
    ) -> LiveState:
        """Reacquire the facts that must stay stable within this invocation."""
        state = self.resolver.resolve(task_number)
        decision = self.eligibility.resolve(state, Phase.DELIVERY_COMPLETE)
        if not decision.eligible:
            raise LckStopError(
                "Delivery operation guard stopped: " + "; ".join(decision.reasons)
            )
        issue = state.issue
        if state.git.get("origin_main_sha") != base_sha:
            raise LckStopError("Delivery operation guard stopped: origin/main changed")
        if not isinstance(issue, Mapping) or issue.get("body_sha256") != body_sha256:
            raise LckStopError("Delivery operation guard stopped: Task body changed")
        if state.target_branch != branch:
            raise LckStopError(
                "Delivery operation guard stopped: Task branch identity changed"
            )
        if head_sha is not None and state.local_task_head != head_sha:
            raise LckStopError(
                "Delivery operation guard stopped: local Task head changed"
            )
        return state

    @staticmethod
    def _checks_postcondition(
        state: LiveState,
        checks_result: Mapping[str, Any],
    ) -> bool:
        """Require the latest live PR checks to match the completed gate."""
        if checks_result.get("status") != "pass":
            return False
        current_pr = state.open_pr
        gated_pr = checks_result.get("pr")
        if not isinstance(current_pr, Mapping) or not isinstance(gated_pr, Mapping):
            return False
        if (
            gated_pr.get("number") != current_pr.get("number")
            or gated_pr.get("head_sha") != current_pr.get("headRefOid")
            or gated_pr.get("base_sha") != current_pr.get("baseRefOid")
        ):
            return False
        checks = state.checks
        try:
            failed = int(checks.get("failed", 0) or 0)
            pending = int(checks.get("pending", 0) or 0)
            unknown = int(checks.get("skipped_or_unknown", 0) or 0)
            count = int(checks.get("count", 0) or 0)
        except (TypeError, ValueError):
            return False
        if failed > 0 or pending > 0 or unknown > 0:
            return False

        required = checks_result.get("required")
        required_names = (
            {item for item in required if isinstance(item, str) and item}
            if isinstance(required, list)
            else set()
        )
        if required_names:
            observed = DeliveryChecksGate._observed_categories(checks)
            return all(observed.get(name) == "success" for name in required_names)

        configuration = checks_result.get("configuration")
        if configuration in {"configured-empty", "not-configured"}:
            return count == 0 or checks.get("all_success") is True
        return count > 0 and checks.get("all_success") is True

    def _final_verify(
        self,
        state: LiveState,
        head: str,
        *,
        base_sha: str,
        body_sha256: str,
        branch: str,
        checks_result: Mapping[str, Any],
    ) -> LiveState:
        if state.status is not ResolutionStatus.RESOLVED:
            raise LckStopError(
                "Delivery final verification unresolved: "
                + "; ".join(state.stop_reasons)
            )
        issue = state.issue
        pr_head = state.open_pr.get("headRefOid") if state.open_pr else None
        pr_base = state.open_pr.get("baseRefOid") if state.open_pr else None
        if not (
            state.target_branch == branch
            and state.git.get("branch") == branch
            and state.git.get("clean") is True
            and state.git.get("origin_main_sha") == base_sha
            and isinstance(issue, Mapping)
            and issue.get("body_sha256") == body_sha256
            and state.local_task_head == head
            and state.remote_task_oid == head
            and pr_head == head
            and pr_base == base_sha
            and state.open_pr is not None
            and state.open_pr.get("isDraft") is False
            and state.project_status == "Review"
            and self._checks_postcondition(state, checks_result)
        ):
            raise LckStopError(
                "Delivery final verification failed: Task/base/local/remote/PR/check/lifecycle "
                "state is not aligned"
            )
        return state

    def complete(
        self,
        task_number: int,
        *,
        commit_message: str,
        summary: str,
        risks: str = "",
    ) -> DeliveryCompletionResult:
        if not summary.strip():
            raise LckStopError("Delivery summary must be non-empty")
        state = self.resolver.resolve(task_number)
        decision = self.eligibility.resolve(state, Phase.DELIVERY_COMPLETE)
        if not decision.eligible:
            raise LckStopError(
                f"Delivery Complete STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )
        base_sha = state.git.get("origin_main_sha")
        if not is_sha(base_sha):
            raise LckStopError("current origin/main identity is unavailable")
        issue = state.issue
        body_sha256 = issue.get("body_sha256") if isinstance(issue, Mapping) else None
        if not isinstance(body_sha256, str) or not body_sha256:
            raise LckStopError("current Task body identity is unavailable")
        branch = state.target_branch

        effects: list[EffectReceipt] = []
        if state.git.get("clean") is True:
            if not self._has_task_diff(base_sha):
                raise LckStopError(
                    "Delivery Complete found no Task diff against origin/main"
                )
            # Stateless recovery path: re-run all gates on the existing clean head
            # instead of trusting a receipt from a previous interrupted invocation.
            validated_tree = self.commit_effect.current_head_tree()
            critical = self._run_critical_outcome(state)
            validation = self.formal_validation.run(base_sha)
            head = state.local_task_head
            if not is_sha(head):
                raise LckStopError("current Task head is unavailable")
            effects.append(
                EffectReceipt(
                    effect="commit_current_tree",
                    action="already-committed-revalidated",
                    details={"head_sha": head, "tree_oid": validated_tree},
                )
            )
        else:
            validated_tree = self.commit_effect.stage_candidate_tree()
            critical = self._run_critical_outcome(state)
            validation = self.formal_validation.run(base_sha)
            self._operation_guard(
                task_number,
                base_sha=base_sha,
                body_sha256=body_sha256,
                branch=branch,
            )
            self.commit_effect.verify_tree_unchanged(validated_tree)
            commit = self.commit_effect.execute(validated_tree, commit_message)
            effects.append(commit)
            head = commit.details.get("head_sha")
            if not is_sha(head):
                raise LckStopError("commit receipt did not contain a valid head SHA")

        self._operation_guard(
            task_number,
            base_sha=base_sha,
            body_sha256=body_sha256,
            branch=branch,
            head_sha=str(head),
        )
        effects.append(self.remote_effect.execute(branch))
        self._operation_guard(
            task_number,
            base_sha=base_sha,
            body_sha256=body_sha256,
            branch=branch,
            head_sha=str(head),
        )
        effects.append(
            self.pr_effect.execute(
                task_number,
                summary=summary,
                risks=risks,
                critical_outcome=critical,
                validation=validation,
                expected_base_sha=base_sha,
                expected_body_sha256=body_sha256,
            )
        )
        self._operation_guard(
            task_number,
            base_sha=base_sha,
            body_sha256=body_sha256,
            branch=branch,
            head_sha=str(head),
        )
        checks = self.checks_gate.run(task_number)
        self._operation_guard(
            task_number,
            base_sha=base_sha,
            body_sha256=body_sha256,
            branch=branch,
            head_sha=str(head),
        )
        effects.append(self.status_effect.execute(task_number))
        # The status effect may race with GitHub check transitions. Reacquire
        # one final live state after the effect and verify that exact state.
        final_state = self.resolver.resolve(task_number)
        final_state = self._final_verify(
            final_state,
            str(head),
            base_sha=base_sha,
            body_sha256=body_sha256,
            branch=branch,
            checks_result=checks,
        )
        return DeliveryCompletionResult(
            task_number=task_number,
            status="READY_FOR_REVIEW",
            branch=final_state.target_branch,
            head_sha=str(head),
            critical_outcome=critical,
            validation=validation,
            checks=checks,
            effects=tuple(effects),
            final_state=final_state,
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
    complete = delivery_commands.add_parser("complete")
    complete.add_argument("task", type=int)
    complete.add_argument("--commit-message", required=True)
    complete.add_argument("--summary", required=True)
    complete.add_argument("--risks", default="")
    complete.add_argument("--check-timeout-seconds", type=float, default=600.0)
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
        if args.delivery_command == "complete":
            result = DeliveryCompleter(
                resolver,
                checks_gate=DeliveryChecksGate(
                    resolver, timeout_seconds=args.check_timeout_seconds
                ),
            ).complete(
                args.task,
                commit_message=args.commit_message,
                summary=args.summary,
                risks=args.risks,
            )
            print_json(result.to_dict(), pretty=True)
            return 0
        raise LckStopError("unsupported LCK command")
    except WorkflowToolError as exc:
        print(json.dumps({"status": "stop", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
