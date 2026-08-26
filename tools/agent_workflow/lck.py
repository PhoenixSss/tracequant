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
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
import uuid

import yaml
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
    atomic_write_json,
    command_warning,
    is_sha,
    print_json,
    read_json_file,
    read_json_text,
    safe_text,
    sha256_json,  # noqa: F401
    stderr_tail,
)
from workflow_evidence import (
    _find_project_status,
    _git_snapshot,
    _issue_view_with_contract,
    _normalize_checks,
    _relationship_snapshot,
    _formal_blockers_gate,
    _repository_slug,
)


BASE_BRANCH: Final = "main"
REQUIRED_CHECKS_WORKFLOW: Final = ".github/workflows/ci.yml"
LCK_SCHEMA_VERSION: Final = 1
TASK_BRANCH_PATTERN: Final = re.compile(
    r"^(?:task/(?P<slash>\d+)(?:-.+)?|task-(?P<dash>\d+)(?:-.+)?|(?P<legacy>\d+)-.+)$"
)
RECOVERY_PR_FIELDS: Final = (
    "number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
    "mergeCommit,mergedAt,mergeable,reviewDecision,headRepository,"
    "closingIssuesReferences"
)


class Phase(StrEnum):
    """LCK phase capabilities exposed by the v1 core."""

    DELIVERY_PREPARE = "Delivery Prepare"
    DELIVERY_COMPLETE = "Delivery Complete"
    REVIEW_PREPARE = "Review Prepare"
    REVIEW_COMPLETE = "Review Complete"
    REMEDIATION_PREPARE = "Remediation Prepare"
    REMEDIATION_NO_CHANGE = "Remediation No Change"
    REMEDIATION_COMPLETE = "Remediation Complete"
    CLOSEOUT = "Closeout"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    STOP = "stop"


class LckStopError(WorkflowToolError):
    """A deterministic LCK gate could not identify one safe action."""


class ReviewStaleError(LckStopError):
    """The reviewed target is no longer current at a later operation boundary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


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
    remote_main_sha = _authoritative_remote_main_sha(git)
    return (
        git.get("branch") == BASE_BRANCH
        and git.get("clean") is True
        and is_sha(head_sha)
        and is_sha(local_main_sha)
        and is_sha(remote_main_sha)
        and head_sha == local_main_sha == remote_main_sha
    )


def _authoritative_remote_main_sha(git: Mapping[str, Any]) -> str | None:
    """Return remote main authority, with a test/legacy input compatibility fallback."""
    remote_main_sha = git.get("remote_main_sha")
    if isinstance(remote_main_sha, str):
        return remote_main_sha
    legacy_sha = git.get("origin_main_sha")
    return legacy_sha if isinstance(legacy_sha, str) else None


def _remote_refs(stdout: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in stdout.splitlines():
        oid, separator, ref = line.partition("\t")
        if not separator or not is_sha(oid) or not ref.startswith("refs/heads/"):
            continue
        refs[ref.removeprefix("refs/heads/")] = oid
    return refs


def _merge_commit_sha(pr: Mapping[str, Any] | None) -> str | None:
    """Return a normalized squash-merge commit identity from a PR fact."""
    if not isinstance(pr, Mapping):
        return None
    value = pr.get("mergeCommit")
    if isinstance(value, Mapping):
        value = value.get("oid")
    if not is_sha(value):
        value = pr.get("merge_commit_sha")
    return value if is_sha(value) else None


def _pr_head_sha(pr: Mapping[str, Any] | None) -> str | None:
    if not isinstance(pr, Mapping):
        return None
    value = pr.get("headRefOid", pr.get("head_sha"))
    return value if is_sha(value) else None


def _pr_base_sha(pr: Mapping[str, Any] | None) -> str | None:
    if not isinstance(pr, Mapping):
        return None
    value = pr.get("baseRefOid", pr.get("base_sha"))
    return value if is_sha(value) else None


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
    merged_pr: Mapping[str, Any] | None = None
    task_contract: Mapping[str, Any] | None = None

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
                    "task_contract": (
                        {
                            "number": self.task_contract.get("number"),
                            "title": self.task_contract.get("title"),
                            "url": self.task_contract.get("url"),
                            "body_sha256": self.task_contract.get("body_sha256"),
                            "critical_outcome": self.task_contract.get(
                                "critical_outcome"
                            ),
                        }
                        if isinstance(self.task_contract, Mapping)
                        else None
                    ),
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
                    "merged_pr": self.merged_pr,
                }
            ),
        )


@dataclass(frozen=True)
class OperationSnapshot:
    """Immutable authoritative inputs for one LCK lifecycle operation.

    ``state`` is acquired exactly once at the operation boundary. Optional
    phase-specific deterministic facts (currently the exact-base required-check
    contract) are bound in the same bounded start window, after immutable Git
    objects are materialized when Review isolation requires it. Downstream
    helpers consume this object; they do not refresh Git/GitHub authority.
    """

    operation: str
    state: LiveState
    required_checks: Mapping[str, Any] | None = None
    acquisition_warnings: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": self.operation,
            "state": self.state.to_dict(),
            "required_checks": _jsonable(self.required_checks),
            "acquisition_warnings": _jsonable(self.acquisition_warnings),
        }


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
            retries=1,
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

    def _recover_merged_pr_branch(
        self,
        task_number: int,
        repository: str,
        issue: Mapping[str, Any] | None,
        warnings: list[dict[str, Any]],
    ) -> tuple[str | None, Mapping[str, Any] | None, str | None]:
        """Recover a deleted Task ref from authoritative closing-PR facts."""
        if not isinstance(issue, Mapping):
            return None, None, None
        if str(issue.get("state", "")).upper() != "CLOSED":
            return None, None, None

        closure = issue.get("issue_closure")
        if not isinstance(closure, Mapping):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "Issue closure facts are unavailable",
            )
        if closure.get("evidence_status") != "complete":
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "Issue closure facts are incomplete",
            )
        refs = closure.get("closed_by_pull_requests")
        if not isinstance(refs, Mapping) or refs.get("truncated") is True:
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "closing PR references are incomplete",
            )
        items = refs.get("items")
        if not isinstance(items, list):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "closing PR references are malformed",
            )
        merged_refs = [
            item
            for item in items
            if isinstance(item, Mapping)
            and item.get("repository") == repository
            and item.get("merged") is True
            and str(item.get("state", "")).upper() == "MERGED"
            and isinstance(item.get("number"), int)
            and not isinstance(item.get("number"), bool)
        ]
        if len(merged_refs) != 1:
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                f"expected one merged closing PR, found {len(merged_refs)}",
            )

        pr_number = merged_refs[0]["number"]
        result = self.runner.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repository,
                "--json",
                RECOVERY_PR_FIELDS,
            ],
            command_id="lck-recover-merged-pr",
        )
        if result.returncode != 0 or not result.stdout.strip():
            warnings.append(command_warning(result))
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR identity cannot be read",
            )
        try:
            value = read_json_text(result.stdout, field="lck-recover-merged-pr")
        except WorkflowToolError as exc:
            warnings.append(
                {
                    "command_id": result.command_id,
                    "exit_code": result.returncode,
                    "error": str(exc),
                }
            )
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR identity is malformed",
            )
        if not isinstance(value, Mapping):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR identity is not an object",
            )
        if (
            value.get("number") != pr_number
            or str(value.get("state", "")).upper() != "MERGED"
            or value.get("baseRefName") != BASE_BRANCH
            or not _branch_matches_task(str(value.get("headRefName", "")), task_number)
        ):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR identity does not match this Task",
            )
        head_repository = value.get("headRepository")
        if (
            not isinstance(head_repository, Mapping)
            or head_repository.get("nameWithOwner") != repository
        ):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR head repository is not this repository",
            )
        closing = value.get("closingIssuesReferences")
        closing_numbers = (
            [
                item.get("number")
                for item in closing
                if isinstance(item, Mapping) and isinstance(item.get("number"), int)
            ]
            if isinstance(closing, list)
            else []
        )
        if task_number not in closing_numbers:
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR does not close this Task",
            )
        head_branch = value.get("headRefName")
        return str(head_branch), dict(value), None

    def resolve(self, task_number: int) -> LiveState:
        if task_number <= 0:
            raise LckStopError(f"Task number must be positive: {task_number}")

        warnings: list[dict[str, Any]] = []
        reasons: list[str] = []
        repository = _repository_slug(self.runner, self.repository, warnings)
        issue: Mapping[str, Any] | None = None
        task_contract: Mapping[str, Any] | None = None
        relationships: Mapping[str, Any] = {"available": False}
        if repository is not None:
            issue, task_contract = _issue_view_with_contract(
                self.runner,
                repository,
                task_number,
                warnings,
            )
            relationships = _relationship_snapshot(
                self.runner, repository, task_number, warnings
            )
        else:
            reasons.append("repository identity unavailable")

        git = _git_snapshot(self.runner, warnings, read_only_local_refs=True)
        if not isinstance(git.get("branch"), str) or not git.get("branch"):
            reasons.append("current local branch is unavailable")
        if not is_sha(git.get("head_sha")):
            reasons.append("current local HEAD is unavailable")
        if not is_sha(git.get("local_main_sha")):
            reasons.append("local main ref unavailable")
        if not is_sha(_authoritative_remote_main_sha(git)):
            reasons.append("remote main query failed")
        if git.get("clean") not in {True, False}:
            reasons.append("current worktree cleanliness is unavailable")
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
        recovered_branch: str | None = None
        recovered_pr: Mapping[str, Any] | None = None
        if not candidates and repository is not None:
            recovery_reason: str | None
            recovered_branch, recovered_pr, recovery_reason = (
                self._recover_merged_pr_branch(
                    task_number,
                    repository,
                    issue,
                    warnings,
                )
            )
            if recovery_reason:
                reasons.append(recovery_reason)
        target_branch = (
            candidates[0] if len(candidates) == 1 else recovered_branch or canonical
        )
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
        merged_pr: Mapping[str, Any] | None = None
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
                if not history and recovered_pr is not None:
                    history = [dict(recovered_pr)]
                merged_items = [
                    item
                    for item in history
                    if str(item.get("state", "")).upper() == "MERGED"
                    and isinstance(item.get("number"), int)
                ]
                merged_numbers = tuple(
                    sorted(int(item["number"]) for item in merged_items)
                )
                if len(merged_items) == 1:
                    merged_pr = dict(merged_items[0])
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
            "merged_pr_number": (
                merged_pr.get("number") if isinstance(merged_pr, Mapping) else None
            ),
        }
        status = ResolutionStatus.STOP if reasons else ResolutionStatus.RESOLVED
        return LiveState(
            task_number=task_number,
            repository=repository,
            issue=issue,
            task_contract=task_contract,
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
            merged_pr=merged_pr,
        )


def _parse_required_checks_workflow(
    text: str,
    *,
    source_sha: str,
) -> dict[str, Any]:
    """Derive required GitHub check contexts from canonical CI at one base commit.

    Required-check policy is lifecycle control policy, so a candidate must not be
    able to weaken the checks that authorize its own transition. LCK therefore
    reads the canonical CI workflow from the immutable trusted base commit.
    Only statically named, non-matrix jobs are supported in v1; anything dynamic
    fails closed rather than guessing the check-run identity.
    """

    if not is_sha(source_sha):
        raise LckStopError("required PR check policy source SHA is invalid")
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LckStopError(
            "required PR check policy is malformed at trusted base "
            f"{source_sha}: {REQUIRED_CHECKS_WORKFLOW}: {exc}"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise LckStopError(
            "required PR check policy is unresolved at trusted base "
            f"{source_sha}: {REQUIRED_CHECKS_WORKFLOW} must contain a mapping"
        )
    jobs = parsed.get("jobs")
    if not isinstance(jobs, Mapping) or not jobs:
        raise LckStopError(
            "required PR check policy is unresolved at trusted base "
            f"{source_sha}: {REQUIRED_CHECKS_WORKFLOW} must define static jobs"
        )

    names: list[str] = []
    job_bindings: list[dict[str, str]] = []
    for raw_job_id, raw_job in jobs.items():
        if not isinstance(raw_job_id, str) or not raw_job_id.strip():
            raise LckStopError(
                "required PR check policy is invalid: CI job id must be a non-empty string"
            )
        job_id = raw_job_id.strip()
        if not isinstance(raw_job, Mapping):
            raise LckStopError(
                f"required PR check policy is invalid: CI job {job_id!r} is malformed"
            )
        strategy = raw_job.get("strategy")
        if isinstance(strategy, Mapping) and "matrix" in strategy:
            raise LckStopError(
                "required PR check policy is unresolved: matrix CI jobs are not "
                f"supported by LCK v1 ({job_id!r})"
            )
        raw_name = raw_job.get("name")
        if raw_name is None:
            check_name = job_id
        elif isinstance(raw_name, str) and raw_name.strip():
            check_name = raw_name.strip()
        else:
            raise LckStopError(
                f"required PR check policy is invalid: CI job {job_id!r} has an invalid name"
            )
        if "${{" in check_name:
            raise LckStopError(
                "required PR check policy is unresolved: dynamic CI job names are not "
                f"supported by LCK v1 ({job_id!r})"
            )
        if check_name in names:
            raise LckStopError(
                f"required PR check policy is invalid: duplicate check {check_name!r}"
            )
        names.append(check_name)
        job_bindings.append({"job_id": job_id, "check_name": check_name})

    contract_payload = {
        "workflow": REQUIRED_CHECKS_WORKFLOW,
        "required-checks": names,
    }
    return {
        "configuration": "repository-base-ci",
        "source": f"git:{source_sha}:{REQUIRED_CHECKS_WORKFLOW}:jobs",
        "source_sha": source_sha,
        "workflow_path": REQUIRED_CHECKS_WORKFLOW,
        "contract_sha256": sha256_json(contract_payload),
        "jobs": job_bindings,
        "contexts": {
            "items": names,
            "count": len(names),
            "truncated": False,
        },
    }


def _repository_required_checks_at_commit(
    resolver: LiveStateResolver,
    source_sha: str,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Read required checks from canonical CI at an exact trusted-base commit.

    ``repo_root`` may be the source repository or an isolated Review clone.
    Working-tree files and candidate-head policy are deliberately ignored.
    """

    if not is_sha(source_sha):
        raise LckStopError("required PR check policy trusted base is unavailable")
    result = resolver.runner.run(
        ["git", "show", f"{source_sha}:{REQUIRED_CHECKS_WORKFLOW}"],
        command_id="lck-required-check-policy-from-base-ci",
        cwd=repo_root or resolver.repo_root,
        env={"GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        raise LckStopError(
            "required PR check policy is unavailable at trusted base "
            f"{source_sha}: {REQUIRED_CHECKS_WORKFLOW}: {detail}"
        )
    return _parse_required_checks_workflow(result.stdout, source_sha=source_sha)


def _required_checks_policy_source_sha(
    state: LiveState,
    operation: str,
) -> str:
    """Return the immutable base commit that governs required-check policy.

    Candidate heads cannot authorize their own checks.  Delivery is governed by
    current authoritative main; operations on an existing PR are governed by the
    exact PR base commit.
    """

    if operation == Phase.DELIVERY_COMPLETE.value:
        source_sha = _authoritative_remote_main_sha(state.git)
    else:
        pr = state.open_pr
        source_sha = _pr_base_sha(pr) if isinstance(pr, Mapping) else None
    if not is_sha(source_sha):
        raise LckStopError(
            f"required PR check policy trusted base is unavailable for {operation}"
        )
    return str(source_sha)


def _required_check_contract(required: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Validate and return the exact-base-bound required-check set.

    Legacy GitHub-discovery states and mutable working-tree configuration are
    diagnostic inputs only and can never authorize an LCK lifecycle effect.
    """

    if not isinstance(required, Mapping):
        raise LckStopError("required PR check policy was not acquired")
    if required.get("configuration") != "repository-base-ci":
        raise LckStopError(
            "required PR check policy is unresolved; LCK requires the "
            "exact trusted-base canonical-CI check contract before lifecycle effects"
        )
    source_sha = required.get("source_sha")
    if not is_sha(source_sha):
        raise LckStopError("required PR check policy source SHA is malformed")
    contract_sha256 = required.get("contract_sha256")
    if (
        not isinstance(contract_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", contract_sha256) is None
    ):
        raise LckStopError("required PR check policy contract hash is malformed")
    contexts = required.get("contexts")
    if not isinstance(contexts, Mapping):
        raise LckStopError("required PR check policy contexts are malformed")
    items = contexts.get("items")
    if not isinstance(items, list):
        raise LckStopError("required PR check policy items are malformed")
    names: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            raise LckStopError(
                "required PR check policy contains an invalid check name"
            )
        if item in names:
            raise LckStopError(
                f"required PR check policy contains duplicate check {item!r}"
            )
        names.append(item)
    workflow_path = required.get("workflow_path")
    if workflow_path != REQUIRED_CHECKS_WORKFLOW:
        raise LckStopError("required PR check policy workflow path is malformed")
    if (
        sha256_json({"workflow": REQUIRED_CHECKS_WORKFLOW, "required-checks": names})
        != contract_sha256
    ):
        raise LckStopError("required PR check policy contract hash does not match")
    return tuple(names)


def _required_check_contract_for_snapshot(
    snapshot: OperationSnapshot,
) -> tuple[str, ...]:
    """Validate that required-check policy is bound to this snapshot's base."""

    required = snapshot.required_checks
    names = _required_check_contract(required)
    required_mapping = cast(Mapping[str, Any], required)
    expected_sha = _required_checks_policy_source_sha(
        snapshot.state, snapshot.operation
    )
    if required_mapping.get("source_sha") != expected_sha:
        raise LckStopError(
            "required PR check policy is not bound to the operation trusted base: "
            "expected "
            f"{expected_sha}, got "
            f"{required_mapping.get('source_sha') or 'unavailable'}"
        )
    return names


class OperationSnapshotBuilder:
    """Acquire one phase-specific authoritative snapshot at operation entry."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def acquire(
        self,
        task_number: int,
        *,
        operation: str,
        include_required_checks: bool = False,
    ) -> OperationSnapshot:
        state = copy.deepcopy(self.resolver.resolve(task_number))
        snapshot = OperationSnapshot(
            operation=operation,
            state=state,
            required_checks=None,
            acquisition_warnings=(),
        )
        if include_required_checks:
            snapshot = self.bind_required_checks(snapshot)
        return snapshot

    def bind_required_checks(
        self,
        snapshot: OperationSnapshot,
        *,
        repo_root: Path | None = None,
    ) -> OperationSnapshot:
        """Bind one snapshot to the policy stored at its immutable trusted base.

        This method never re-resolves Git/GitHub state.  ``repo_root`` is only a
        repository object source; Review may pass its standalone clone after the
        exact base/head objects have been materialized there.
        """

        if snapshot.required_checks is not None:
            _required_check_contract(snapshot.required_checks)
            return snapshot
        source_sha = _required_checks_policy_source_sha(
            snapshot.state, snapshot.operation
        )
        required = _repository_required_checks_at_commit(
            self.resolver,
            source_sha,
            repo_root=repo_root,
        )
        _required_check_contract(required)
        return OperationSnapshot(
            operation=snapshot.operation,
            state=snapshot.state,
            required_checks=required,
            acquisition_warnings=snapshot.acquisition_warnings,
        )


def _task_contract_from_state(state: LiveState) -> dict[str, Any]:
    """Return the Task contract captured by the operation-start live snapshot."""
    if state.status is not ResolutionStatus.RESOLVED:
        raise LckStopError("cannot read Task Contract from unresolved live state")
    value = state.task_contract
    if not isinstance(value, Mapping):
        raise LckStopError("current Task Contract is unavailable in operation snapshot")
    body = value.get("body")
    body_sha256 = value.get("body_sha256")
    if not isinstance(body, str) or not isinstance(body_sha256, str):
        raise LckStopError("current Task Contract body is unavailable")
    issue = state.issue
    if (
        not isinstance(issue, Mapping)
        or issue.get("body_sha256") != body_sha256
        or value.get("number") != state.task_number
        or value.get("title") != issue.get("title")
        or str(issue.get("state", "")).upper() != "OPEN"
    ):
        raise LckStopError("Task Contract is inconsistent inside operation snapshot")
    return dict(value)


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
                # A prior status write may have moved the Task to Review before
                # a later final verification stopped.  Allow the same LCK
                # Delivery Complete path to reacquire and safely finish that
                # partial invocation.
                Phase.DELIVERY_COMPLETE: {"Ready", "In Progress", "Review"},
                Phase.REVIEW_PREPARE: {"Review", "In Progress"},
                Phase.REVIEW_COMPLETE: {"Review", "In Progress"},
                Phase.REMEDIATION_PREPARE: {"Review"},
                Phase.REMEDIATION_NO_CHANGE: {"Review"},
                Phase.REMEDIATION_COMPLETE: {"Review"},
                Phase.CLOSEOUT: {
                    "Inbox",
                    "Specifying",
                    "Ready",
                    "In Progress",
                    "Review",
                    "Blocked",
                    "Done",
                },
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
        elif phase in {Phase.REVIEW_PREPARE, Phase.REVIEW_COMPLETE}:
            if state.open_pr is None:
                reasons.append("no current OPEN PR")
            elif state.open_pr.get("isDraft") is not False:
                reasons.append(f"{phase.value} requires a non-Draft OPEN PR")
            if state.project_status not in {"Review", "In Progress"}:
                reasons.append(f"Task is not eligible for {phase.value}")
            capabilities = (
                ("prepare_read_only_review_context",)
                if phase is Phase.REVIEW_PREPARE
                else ("accept_semantic_review_verdict",)
            )
        elif phase in {
            Phase.REMEDIATION_PREPARE,
            Phase.REMEDIATION_NO_CHANGE,
            Phase.REMEDIATION_COMPLETE,
        }:
            if state.open_pr is None:
                reasons.append("no current OPEN PR")
            elif state.open_pr.get("isDraft") is not False:
                reasons.append("Remediation requires a non-Draft OPEN PR")
            if state.project_status != "Review":
                reasons.append("Remediation requires Project Status Review")
            pr_head = state.open_pr.get("headRefOid") if state.open_pr else None
            if not is_sha(pr_head):
                reasons.append("current OPEN PR head OID is unavailable")
            pr_base = state.open_pr.get("baseRefOid") if state.open_pr else None
            remote_main = _authoritative_remote_main_sha(state.git)
            if not is_sha(pr_base) or not is_sha(remote_main) or pr_base != remote_main:
                reasons.append("current OPEN PR base must match current origin/main")
            if state.remote_task_oid != pr_head:
                reasons.append("remote Task branch must match current OPEN PR head")
            if state.local_task_head is not None and state.local_task_head != pr_head:
                reasons.append("local Task branch must match current OPEN PR head")
            if phase is Phase.REMEDIATION_PREPARE:
                capabilities = ("prepare_task_workspace", "load_review_findings")
            else:
                if state.local_task_branch is None:
                    reasons.append(f"{phase.value} requires a local Task branch")
                if state.git.get("branch") != state.target_branch:
                    reasons.append(
                        f"{phase.value} requires the resolved Task branch selected"
                    )
                capabilities = (
                    ("close_no_change_remediation",)
                    if phase is Phase.REMEDIATION_NO_CHANGE
                    else (
                        "verify_critical_outcome",
                        "run_formal_validation",
                        "commit_current_tree",
                        "ensure_remote_branch",
                        "reuse_open_pr",
                    )
                )
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
    operation_snapshot: OperationSnapshot
    eligibility: PhaseDecision

    @property
    def state(self) -> LiveState:
        return self.operation_snapshot.state

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "delivery-prepare",
            "task_number": self.task_number,
            "repository": self.repository,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "action": self.action,
            "operation_snapshot": self.operation_snapshot.to_dict(),
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
        self.snapshots = OperationSnapshotBuilder(resolver)
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

    def _verify_workspace(self, branch: str, expected_head: str | None) -> None:
        current = self.resolver.runner.run(
            ["git", "branch", "--show-current"],
            command_id="lck-delivery-prepare-post-branch",
        )
        head = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-delivery-prepare-post-head",
        )
        if (
            current.returncode != 0
            or current.stdout.strip() != branch
            or head.returncode != 0
            or not is_sha(head.stdout.strip())
            or (expected_head is not None and head.stdout.strip() != expected_head)
        ):
            raise LckStopError(
                "workspace postcondition failed: selected branch/head is not the "
                "operation snapshot target"
            )

    def prepare(self, task_number: int) -> DeliveryContext:
        snapshot = self.snapshots.acquire(
            task_number,
            operation=Phase.DELIVERY_PREPARE.value,
        )
        state = snapshot.state
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

        expected_head = (
            state.local_task_head
            if local_exists
            else state.remote_task_oid
            if state.remote_task_branch is not None
            else base_sha
        )
        self._verify_workspace(branch, expected_head)
        return DeliveryContext(
            task_number=task_number,
            repository=state.repository,
            branch=branch,
            base_sha=base_sha if isinstance(base_sha, str) else None,
            action=action,
            operation_snapshot=snapshot,
            eligibility=decision,
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


@dataclass(frozen=True)
class ReviewTargetRefs:
    """Authoritative Review target facts acquired before repository materialization."""

    task_number: int
    pr_number: int
    base_sha: str
    head_sha: str
    task_body_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_number": self.task_number,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "task_body_sha256": self.task_body_sha256,
        }


@dataclass(frozen=True)
class ReviewIdentity:
    task_number: int
    pr_number: int
    base_sha: str
    head_sha: str
    task_body_sha256: str
    merge_base_sha: str
    effective_diff_sha256: str
    changed_files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_number": self.task_number,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "task_body_sha256": self.task_body_sha256,
            "merge_base_sha": self.merge_base_sha,
            "effective_diff_sha256": self.effective_diff_sha256,
            "changed_files": list(self.changed_files),
        }


def _review_target_refs(
    state: LiveState,
    task_contract: Mapping[str, Any],
) -> ReviewTargetRefs:
    """Extract immutable Git/GitHub identities without requiring local Git objects."""
    pr = state.open_pr
    if not isinstance(pr, Mapping):
        raise LckStopError("Review target has no current OPEN PR")
    pr_number = pr.get("number")
    base_sha = pr.get("baseRefOid")
    head_sha = pr.get("headRefOid")
    task_body_sha256 = task_contract.get("body_sha256")
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number <= 0:
        raise LckStopError("Review target PR number is unavailable")
    if not is_sha(base_sha) or not is_sha(head_sha):
        raise LckStopError("Review target base/head identity is unavailable")
    if not isinstance(task_body_sha256, str) or not task_body_sha256:
        raise LckStopError("Review target Task Contract identity is unavailable")
    return ReviewTargetRefs(
        task_number=state.task_number,
        pr_number=pr_number,
        base_sha=str(base_sha),
        head_sha=str(head_sha),
        task_body_sha256=task_body_sha256,
    )


def _review_identity(
    resolver: LiveStateResolver,
    state: LiveState,
    task_contract: Mapping[str, Any],
    *,
    repo_root: Path,
) -> ReviewIdentity:
    """Derive the effective diff from frozen refs inside a materialized repository.

    GitHub supplies the authoritative PR/base/head/Task identities.  Merge-base,
    effective diff, and changed-file inventory are derived facts and therefore
    must be computed only after those exact commits exist in the isolated Review
    clone.  The source repository is not required to contain the current PR head.
    """
    target = _review_target_refs(state, task_contract)
    merge_base = resolver.runner.run(
        ["git", "merge-base", target.base_sha, target.head_sha],
        command_id="lck-review-merge-base",
        cwd=repo_root,
    )
    if merge_base.returncode != 0 or not is_sha(merge_base.stdout.strip()):
        raise LckStopError("Review effective-diff merge base is unavailable")
    diff = resolver.runner.run(
        [
            "git",
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            f"{target.base_sha}...{target.head_sha}",
        ],
        command_id="lck-review-effective-diff",
        cwd=repo_root,
    )
    if diff.returncode != 0:
        raise LckStopError(
            "Review effective diff is unavailable: "
            + (diff.stderr.strip() or f"exit {diff.returncode}")
        )
    names = resolver.runner.run(
        ["git", "diff", "--name-only", f"{target.base_sha}...{target.head_sha}"],
        command_id="lck-review-changed-files",
        cwd=repo_root,
    )
    if names.returncode != 0:
        raise LckStopError("Review changed-file inventory is unavailable")
    changed_files = tuple(line for line in names.stdout.splitlines() if line)
    return ReviewIdentity(
        task_number=target.task_number,
        pr_number=target.pr_number,
        base_sha=target.base_sha,
        head_sha=target.head_sha,
        task_body_sha256=target.task_body_sha256,
        merge_base_sha=merge_base.stdout.strip(),
        effective_diff_sha256=hashlib.sha256(
            diff.stdout.encode("utf-8", errors="replace")
        ).hexdigest(),
        changed_files=changed_files,
    )


class ReviewValidationGate:
    """Run current Review validation inside the isolated reviewed clone."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _persist_validation_artifacts(
        self,
        review_root: Path,
        payload: dict[str, Any],
        *,
        base_sha: str,
        head_sha: str,
    ) -> dict[str, Any]:
        raw_output_dir = payload.get("output_dir")
        if not isinstance(raw_output_dir, str) or not raw_output_dir:
            raise LckStopError(
                "formal Review validation did not provide an output directory"
            )
        output_dir = Path(raw_output_dir)
        if output_dir.is_absolute():
            raise LckStopError(
                "formal Review validation output directory must be relative"
            )
        review_root = review_root.resolve()
        source = (review_root / output_dir).resolve()
        try:
            source.relative_to(review_root)
        except ValueError as exc:
            raise LckStopError(
                "formal Review validation output escaped the Review clone"
            ) from exc
        if not source.is_dir():
            raise LckStopError(
                "formal Review validation output directory is unavailable"
            )

        durable_root = (
            self.resolver.repo_root / ".workflow.local" / "lck" / "review-validation"
        ).resolve()
        durable_root.mkdir(parents=True, exist_ok=True)
        destination = durable_root / f"lck-review-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, destination)
        except OSError as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise LckStopError(
                f"cannot preserve formal Review validation artifacts: {exc}"
            ) from exc

        durable_relative = destination.relative_to(
            self.resolver.repo_root.resolve()
        ).as_posix()
        preserved = dict(payload)
        preserved["output_dir"] = durable_relative
        preserved["evidence_path"] = durable_relative
        preserved["validated_base_sha"] = base_sha
        preserved["validated_head_sha"] = head_sha
        commands = preserved.get("commands")
        if isinstance(commands, list):
            for command in commands:
                if not isinstance(command, dict):
                    continue
                raw_log_path = command.get("log_path")
                if not isinstance(raw_log_path, str) or not raw_log_path:
                    continue
                log_path = Path(raw_log_path)
                try:
                    log_relative = log_path.relative_to(output_dir)
                except ValueError as exc:
                    raise LckStopError(
                        "formal Review validation log path escaped its output directory"
                    ) from exc
                command["log_path"] = (Path(durable_relative) / log_relative).as_posix()
        evidence_file = (
            Path(durable_relative) / "lck-review-validation-result.json"
        ).as_posix()
        preserved["evidence_file"] = evidence_file
        atomic_write_json(destination / "lck-review-validation-result.json", preserved)
        return preserved

    def _persist_unstructured_failure(
        self,
        result: Any,
        *,
        base_sha: str,
        head_sha: str,
    ) -> dict[str, Any]:
        durable_root = (
            self.resolver.repo_root / ".workflow.local" / "lck" / "review-validation"
        ).resolve()
        durable_root.mkdir(parents=True, exist_ok=True)
        destination = durable_root / f"lck-review-{uuid.uuid4().hex}"
        destination.mkdir()
        durable_relative = destination.relative_to(
            self.resolver.repo_root.resolve()
        ).as_posix()
        diagnostic = stderr_tail(result.stderr or result.stdout, limit=2000)
        payload: dict[str, Any] = {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "workflow-validation",
            "phase": "review",
            "status": "fail",
            "base_sha": base_sha,
            "validated_base_sha": base_sha,
            "validated_head_sha": head_sha,
            "commands": [
                {
                    "command_id": result.command_id,
                    "status": "fail",
                    "exit_code": result.returncode,
                    "diagnostic": diagnostic,
                }
            ],
            "output_dir": durable_relative,
            "evidence_path": durable_relative,
        }
        payload["evidence_file"] = (
            Path(durable_relative) / "lck-review-validation-result.json"
        ).as_posix()
        atomic_write_json(destination / "lck-review-validation-result.json", payload)
        return payload

    def run(self, review_root: Path, base_sha: str, head_sha: str) -> dict[str, Any]:
        if not is_sha(base_sha):
            raise LckStopError("formal Review validation base SHA is unavailable")
        if not is_sha(head_sha):
            raise LckStopError("formal Review validation head SHA is unavailable")
        tool = review_root / "tools" / "agent_workflow" / "workflow_validation.py"
        if not tool.is_file():
            raise LckStopError("reviewed head does not contain workflow_validation.py")
        result = self.resolver.runner.run(
            [
                sys.executable,
                str(tool),
                "run",
                "--repo-root",
                str(review_root),
                "--phase",
                "review",
                "--base-sha",
                base_sha,
                "--include-skill-validators",
                "--require-skill-validator",
            ],
            command_id="lck-formal-review-validation",
            cwd=review_root,
            validation=True,
        )
        if not result.stdout.strip():
            return self._persist_unstructured_failure(
                result, base_sha=base_sha, head_sha=head_sha
            )
        try:
            parsed = read_json_text(result.stdout, field="lck-formal-review-validation")
        except WorkflowToolError:
            return self._persist_unstructured_failure(
                result, base_sha=base_sha, head_sha=head_sha
            )
        if not isinstance(parsed, dict):
            return self._persist_unstructured_failure(
                result, base_sha=base_sha, head_sha=head_sha
            )
        payload = dict(parsed)
        if result.returncode != 0 and payload.get("status") == "pass":
            payload["status"] = "fail"
        try:
            return self._persist_validation_artifacts(
                review_root,
                payload,
                base_sha=base_sha,
                head_sha=head_sha,
            )
        except LckStopError:
            if payload.get("status") != "fail" and result.returncode == 0:
                raise
            return self._persist_unstructured_failure(
                result, base_sha=base_sha, head_sha=head_sha
            )


class ReviewWorkspaceManager:
    """Own one standalone temporary clone for an Independent Review session.

    Review is read-only with respect to the source repository.  The reviewed
    workspace is therefore a self-contained temporary clone rather than a Git
    worktree registered in the source repository.  All Review Git metadata writes
    stay inside the temporary clone; durable validation evidence is copied only
    to the ignored LCK local evidence root before clone cleanup.
    """

    OWNER_FILE: Final = "lck-review-owner.json"

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    @staticmethod
    def _validated_workspace_path(path: Path) -> Path:
        resolved = path.resolve(strict=False)
        temp_root = Path(tempfile.gettempdir()).resolve()
        try:
            relative = resolved.relative_to(temp_root)
        except ValueError as exc:
            raise LckStopError(
                "Review workspace cleanup path is outside the temporary root"
            ) from exc
        if len(relative.parts) != 1 or not resolved.name.startswith(
            "tracequant-lck-review-"
        ):
            raise LckStopError("Review workspace cleanup path is not LCK-owned")
        return resolved

    def path_for(self, task_number: int, operation_id: str) -> Path:
        """Return an uncreated, operation-owned temporary clone path."""
        return self._validated_workspace_path(
            Path(tempfile.gettempdir())
            / f"tracequant-lck-review-{task_number}-{operation_id}"
        )

    def _source_remote_url(self) -> str:
        result = self.resolver.runner.run(
            ["git", "remote", "get-url", "origin"],
            command_id="lck-review-source-origin",
        )
        remote_url = result.stdout.strip()
        if result.returncode != 0 or not remote_url:
            raise LckStopError(
                "cannot resolve source repository origin for isolated Review clone: "
                + (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "origin unavailable"
                )
            )
        return remote_url

    @classmethod
    def _owner_path(cls, path: Path) -> Path:
        return path / ".git" / cls.OWNER_FILE

    def _write_owner(
        self,
        path: Path,
        *,
        task_number: int,
        base_sha: str,
        head_sha: str,
    ) -> None:
        atomic_write_json(
            self._owner_path(path),
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "lck-review-standalone-clone",
                "task_number": task_number,
                "review_root": str(path),
                "source_repo": str(self.resolver.repo_root),
                "expected_base_sha": base_sha,
                "expected_head_sha": head_sha,
                "authority": "operation-owned temporary Review workspace only",
            },
        )

    def _assert_owned_clone(
        self,
        path: Path,
        *,
        expected_head_sha: str | None = None,
    ) -> Mapping[str, Any]:
        path = self._validated_workspace_path(path)
        if not path.is_dir() or not (path / ".git").is_dir():
            raise LckStopError("isolated Review clone is unavailable")
        owner_path = self._owner_path(path)
        try:
            value = read_json_file(owner_path)
        except WorkflowToolError as exc:
            raise LckStopError(
                "isolated Review clone ownership marker is unavailable"
            ) from exc
        if not isinstance(value, Mapping):
            raise LckStopError("isolated Review clone ownership marker is invalid")
        if value.get("kind") != "lck-review-standalone-clone":
            raise LckStopError("isolated Review clone ownership marker is invalid")
        if value.get("review_root") != str(path):
            raise LckStopError("isolated Review clone ownership path does not match")
        if value.get("source_repo") != str(self.resolver.repo_root):
            raise LckStopError("isolated Review clone belongs to another repository")
        if (
            expected_head_sha is not None
            and value.get("expected_head_sha") != expected_head_sha
        ):
            raise LckStopError("isolated Review clone expected HEAD does not match")
        return value

    def _ensure_commit(self, path: Path, sha: str, *, label: str) -> None:
        available = self.resolver.runner.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            command_id=f"lck-review-clone-{label}-available",
            cwd=path,
        )
        if available.returncode == 0:
            return
        fetched = self.resolver.runner.run(
            ["git", "fetch", "--no-tags", "origin", sha],
            command_id=f"lck-review-clone-fetch-{label}",
            cwd=path,
            retries=1,
        )
        if fetched.returncode != 0:
            raise LckStopError(
                f"cannot materialize reviewed {label} commit in temporary clone: "
                + (
                    fetched.stderr.strip()
                    or fetched.stdout.strip()
                    or f"exit {fetched.returncode}"
                )
            )
        verified = self.resolver.runner.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            command_id=f"lck-review-clone-{label}-verified",
            cwd=path,
        )
        if verified.returncode != 0:
            raise LckStopError(
                f"temporary Review clone does not contain expected {label} commit"
            )

    def create(
        self,
        task_number: int,
        base_sha: str,
        head_sha: str,
        path: Path | None = None,
    ) -> Path:
        path = self.path_for(task_number, uuid.uuid4().hex) if path is None else path
        path = self._validated_workspace_path(path)
        if path.exists():
            raise LckStopError("isolated Review clone path is already occupied")
        remote_url = self._source_remote_url()
        clone = self.resolver.runner.run(
            [
                "git",
                "clone",
                "--no-checkout",
                "--no-hardlinks",
                str(self.resolver.repo_root),
                str(path),
            ],
            command_id="lck-review-clone-create",
        )
        if clone.returncode != 0:
            shutil.rmtree(path, ignore_errors=True)
            raise LckStopError(
                "cannot create isolated Review clone: "
                + (
                    clone.stderr.strip()
                    or clone.stdout.strip()
                    or f"exit {clone.returncode}"
                )
            )
        try:
            self._write_owner(
                path,
                task_number=task_number,
                base_sha=base_sha,
                head_sha=head_sha,
            )
            set_origin = self.resolver.runner.run(
                ["git", "remote", "set-url", "origin", remote_url],
                command_id="lck-review-clone-set-origin",
                cwd=path,
            )
            if set_origin.returncode != 0:
                raise LckStopError(
                    "cannot restore authoritative origin in isolated Review clone: "
                    + (set_origin.stderr.strip() or set_origin.stdout.strip())
                )
            self._ensure_commit(path, base_sha, label="base")
            self._ensure_commit(path, head_sha, label="head")
            checkout = self.resolver.runner.run(
                ["git", "checkout", "--detach", head_sha],
                command_id="lck-review-clone-checkout",
                cwd=path,
            )
            if checkout.returncode != 0:
                raise LckStopError(
                    "cannot checkout exact reviewed HEAD in isolated Review clone: "
                    + (checkout.stderr.strip() or checkout.stdout.strip())
                )
            self._assert_clean_exact(path, head_sha)
        except BaseException:
            self._make_removable(path)
            shutil.rmtree(path, ignore_errors=True)
            raise
        return path

    def _assert_clean_exact(self, path: Path, expected_head_sha: str) -> None:
        if not path.is_dir():
            raise LckStopError("isolated Review clone is unavailable")
        status = self.resolver.runner.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            command_id="lck-review-clone-clean",
            cwd=path,
            env={"GIT_OPTIONAL_LOCKS": "0"},
        )
        head = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-review-clone-head",
            cwd=path,
            env={"GIT_OPTIONAL_LOCKS": "0"},
        )
        if status.returncode != 0 or head.returncode != 0:
            raise LckStopError("cannot verify isolated Review clone")
        if status.stdout.strip():
            raise LckStopError("formal Review validation changed the isolated clone")
        if head.stdout.strip() != expected_head_sha:
            raise LckStopError("isolated Review clone HEAD changed during validation")

    @staticmethod
    def _assert_read_only(path: Path) -> None:
        for root, dirs, files in os.walk(path, topdown=True, followlinks=False):
            root_path = Path(root)
            if root_path.stat().st_mode & 0o222:
                raise LckStopError("isolated Review clone is not read-only")
            for name in (*dirs, *files):
                target = root_path / name
                if not target.is_symlink() and target.stat().st_mode & 0o222:
                    raise LckStopError("isolated Review clone is not read-only")

    def seal_for_review(self, path: Path, expected_head_sha: str) -> None:
        """Verify exact contents, seal the independent clone, and keep it clean."""
        path = self._validated_workspace_path(path)
        self._assert_owned_clone(path, expected_head_sha=expected_head_sha)
        self._assert_clean_exact(path, expected_head_sha)
        self.seal_read_only(path)
        self._assert_clean_exact(path, expected_head_sha)
        self._assert_read_only(path)

    def assert_ready_for_completion(self, path: Path, expected_head_sha: str) -> None:
        """Fail closed unless the prepared standalone Review clone is intact."""
        path = self._validated_workspace_path(path)
        self._assert_owned_clone(path, expected_head_sha=expected_head_sha)
        self._assert_clean_exact(path, expected_head_sha)
        self._assert_read_only(path)

    @staticmethod
    def _remove_write_bits(path: Path) -> None:
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, mode & ~0o222)

    @classmethod
    def seal_read_only(cls, path: Path) -> None:
        for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
            root_path = Path(root)
            for name in files:
                target = root_path / name
                if not target.is_symlink():
                    cls._remove_write_bits(target)
            for name in dirs:
                target = root_path / name
                if not target.is_symlink():
                    cls._remove_write_bits(target)
        cls._remove_write_bits(path)

    @staticmethod
    def _make_removable(path: Path) -> None:
        if not path.exists():
            return
        os.chmod(path, 0o755)
        for root, dirs, files in os.walk(path, topdown=True, followlinks=False):
            root_path = Path(root)
            os.chmod(root_path, 0o755)
            for name in dirs:
                target = root_path / name
                if not target.is_symlink():
                    os.chmod(target, 0o755)
            for name in files:
                target = root_path / name
                if not target.is_symlink():
                    os.chmod(target, 0o644)

    def remove(self, path: Path) -> None:
        path = self._validated_workspace_path(path)
        if not path.exists():
            return
        self._assert_owned_clone(path)
        self._make_removable(path)
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            raise LckStopError("failed to remove isolated Review clone directory")

    def remove_recovered(self, path: Path) -> None:
        """Remove a marker-owned clone, including an interrupted partial clone."""
        path = self._validated_workspace_path(path)
        if not path.exists():
            return
        self._make_removable(path)
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            raise LckStopError("failed to remove recovered Review clone directory")


@dataclass
class ReviewPrepareInvocation:
    """Own one operation-local Review Prepare marker until it finishes."""

    path: Path
    operation_id: str
    _lock_fd: int
    recovered: Mapping[str, Any] | None = None

    def update(self, payload: Mapping[str, Any]) -> None:
        atomic_write_json(self.path, payload)

    def release_lock(self) -> None:
        """Release the process lock while retaining durable handoff state."""
        if self._lock_fd < 0:
            return
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)
        self._lock_fd = -1

    def finish(self) -> None:
        try:
            self.path.unlink()
        finally:
            self.release_lock()


class ReviewInvocationStore:
    """Persist invocation-local guards and diagnostic review records only."""

    _ID = re.compile(r"^[0-9a-f]{32}$")

    def __init__(self, repo_root: Path) -> None:
        self.root = repo_root / ".workflow.local" / "lck"

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def _validate_id(self, review_id: str) -> None:
        if self._ID.fullmatch(review_id) is None:
            raise LckStopError("invalid Review invocation id")

    def guard_path(self, review_id: str) -> Path:
        self._validate_id(review_id)
        return self.root / "review-invocations" / f"{review_id}.json"

    def record_path(self, task_number: int, review_id: str) -> Path:
        self._validate_id(review_id)
        return self.root / "reviews" / f"task-{task_number}" / f"{review_id}.json"

    def latest_review_path(self, task_number: int) -> Path:
        return self.root / "review-state" / f"task-{task_number}-latest.json"

    def review_required_path(self, task_number: int) -> Path:
        return self.root / "review-state" / f"task-{task_number}-required.json"

    def remediation_session_path(self, task_number: int) -> Path:
        return self.root / "remediation-sessions" / f"task-{task_number}.json"

    def remediation_no_change_receipt_path(
        self, task_number: int, review_id: str
    ) -> Path:
        self._validate_id(review_id)
        return (
            self.root
            / "remediation-receipts"
            / f"task-{task_number}"
            / f"{review_id}-no-change.json"
        )

    def review_prepare_inflight_path(self, task_number: int) -> Path:
        return self.root / "review-inflight" / f"task-{task_number}.json"

    def review_prepare_lock_path(self, task_number: int) -> Path:
        return self.root / "review-inflight" / f"task-{task_number}.lock"

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def begin_review_prepare(self, task_number: int) -> ReviewPrepareInvocation:
        """Claim one Task-local Review Prepare operation before side effects."""
        path = self.review_prepare_inflight_path(task_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.review_prepare_lock_path(task_number)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                try:
                    active = read_json_file(path)
                except WorkflowToolError:
                    active = {}
                operation_id = (
                    active.get("operation_id") if isinstance(active, Mapping) else None
                )
                state = active.get("state") if isinstance(active, Mapping) else None
                raise LckStopError(
                    f"Review Prepare already in flight for Task #{task_number}"
                    + (
                        f" (operation {operation_id}, state {state})"
                        if operation_id
                        else ""
                    )
                ) from exc

            existing: Mapping[str, Any] | None = None
            if path.exists():
                parsed = read_json_file(path)
                if not isinstance(parsed, Mapping):
                    raise LckStopError("Review Prepare in-flight state is invalid")
                if parsed.get("task_number") != task_number:
                    raise LckStopError(
                        "Review Prepare in-flight Task identity is invalid"
                    )
                owner_pid = parsed.get("pid")
                if (
                    not isinstance(owner_pid, int)
                    or isinstance(owner_pid, bool)
                    or owner_pid <= 0
                ):
                    raise LckStopError(
                        "Review Prepare in-flight owner identity is invalid"
                    )
                if self._pid_is_alive(owner_pid):
                    raise LckStopError(
                        f"Review Prepare already in flight for Task #{task_number}"
                    )
                if parsed.get("state") == "handed-off":
                    review_id = parsed.get("review_id")
                    review_root = parsed.get("review_root")
                    guard_exists = (
                        isinstance(review_id, str)
                        and self.guard_path(review_id).exists()
                    )
                    root_exists = (
                        isinstance(review_root, str) and Path(review_root).exists()
                    )
                    if guard_exists and root_exists:
                        raise LckStopError(
                            "Review Prepare handoff is still owned by the prior "
                            f"operation (review {review_id})"
                        )
                existing = dict(parsed)

            operation_id = self.new_id()
            payload: dict[str, Any] = {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "review-prepare-in-flight",
                "operation_id": operation_id,
                "task_number": task_number,
                "pid": os.getpid(),
                "state": "starting",
                "review_root": None,
                "authority": "operation-local in-flight protection only",
            }
            if existing is not None:
                payload["recovered_from"] = existing.get("operation_id")
                payload["previous_review_root"] = existing.get("review_root")
            invocation = ReviewPrepareInvocation(
                path=path,
                operation_id=operation_id,
                _lock_fd=lock_fd,
                recovered=existing,
            )
            invocation.update(payload)
            return invocation
        except BaseException:
            os.close(lock_fd)
            raise

    def release_review_prepare(self, task_number: int, review_id: str) -> None:
        """Release a successful Prepare handoff after Review cleanup."""
        path = self.review_prepare_inflight_path(task_number)
        if not path.exists():
            return
        lock_path = self.review_prepare_lock_path(task_number)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if not path.exists():
                return
            value = read_json_file(path)
            if not isinstance(value, Mapping):
                raise LckStopError("Review Prepare in-flight state is invalid")
            owner_review_id = value.get("review_id")
            if owner_review_id != review_id:
                raise LckStopError(
                    "Review Prepare handoff belongs to a different Review invocation"
                )
            path.unlink()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def write_guard(self, review_id: str, payload: Mapping[str, Any]) -> None:
        atomic_write_json(self.guard_path(review_id), payload)

    def read_guard(self, review_id: str) -> dict[str, Any]:
        value = read_json_file(self.guard_path(review_id))
        if not isinstance(value, dict):
            raise LckStopError("Review invocation guard is not an object")
        return value

    def delete_guard(self, review_id: str) -> None:
        try:
            self.guard_path(review_id).unlink()
        except FileNotFoundError:
            pass

    def write_record(
        self, task_number: int, review_id: str, payload: Mapping[str, Any]
    ) -> Path:
        path = self.record_path(task_number, review_id)
        atomic_write_json(path, payload)
        return path

    def read_record(self, task_number: int, review_id: str) -> dict[str, Any]:
        value = read_json_file(self.record_path(task_number, review_id))
        if not isinstance(value, dict):
            raise LckStopError("Review record is not an object")
        return value

    def write_latest_review(
        self, task_number: int, review_id: str, verdict: str
    ) -> None:
        self._validate_id(review_id)
        atomic_write_json(
            self.latest_review_path(task_number),
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "latest-independent-review",
                "task_number": task_number,
                "review_id": review_id,
                "verdict": verdict,
                "authority": "semantic predecessor only; never mechanical target authority",
            },
        )

    def read_latest_review(self, task_number: int) -> dict[str, Any] | None:
        path = self.latest_review_path(task_number)
        if not path.exists():
            return None
        value = read_json_file(path)
        if not isinstance(value, dict) or value.get("task_number") != task_number:
            raise LckStopError("latest Review state is invalid")
        return value

    def write_review_required(
        self, task_number: int, review_id: str, head_sha: str
    ) -> None:
        self._validate_id(review_id)
        if not is_sha(head_sha):
            raise LckStopError("review-required state needs a valid remediation head")
        atomic_write_json(
            self.review_required_path(task_number),
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "fresh-review-required",
                "task_number": task_number,
                "source_review_id": review_id,
                "remediated_head": head_sha,
                "authority": "negative lifecycle boundary only; current target remains live-resolved",
            },
        )

    def read_review_required(self, task_number: int) -> dict[str, Any] | None:
        path = self.review_required_path(task_number)
        if not path.exists():
            return None
        value = read_json_file(path)
        if not isinstance(value, dict) or value.get("task_number") != task_number:
            raise LckStopError("review-required state is invalid")
        return value

    def clear_review_required(self, task_number: int) -> None:
        try:
            self.review_required_path(task_number).unlink()
        except FileNotFoundError:
            pass

    def write_remediation_session(
        self, task_number: int, payload: Mapping[str, Any]
    ) -> Path:
        path = self.remediation_session_path(task_number)
        existing: Mapping[str, Any] | None = None
        if path.exists():
            value = read_json_file(path)
            if not isinstance(value, Mapping):
                raise LckStopError("Remediation session state is invalid")
            existing = value
        if existing is not None and (
            existing.get("review_id") != payload.get("review_id")
            or existing.get("start_head_sha") != payload.get("start_head_sha")
        ):
            raise LckStopError(
                "another Remediation session is already prepared for this Task"
            )
        atomic_write_json(path, payload)
        return path

    def read_remediation_session(self, task_number: int) -> dict[str, Any] | None:
        path = self.remediation_session_path(task_number)
        if not path.exists():
            return None
        value = read_json_file(path)
        if not isinstance(value, dict) or value.get("task_number") != task_number:
            raise LckStopError("Remediation session state is invalid")
        return value

    def clear_remediation_session(self, task_number: int) -> None:
        try:
            self.remediation_session_path(task_number).unlink()
        except FileNotFoundError:
            pass

    def write_remediation_no_change_receipt(
        self, task_number: int, review_id: str, payload: Mapping[str, Any]
    ) -> Path:
        path = self.remediation_no_change_receipt_path(task_number, review_id)
        if path.exists():
            existing = read_json_file(path)
            if existing != dict(payload):
                raise LckStopError(
                    "existing Remediation no-change receipt does not match this completion"
                )
            return path
        atomic_write_json(path, payload)
        return path

    def read_remediation_no_change_receipt(
        self, task_number: int, review_id: str
    ) -> dict[str, Any] | None:
        path = self.remediation_no_change_receipt_path(task_number, review_id)
        if not path.exists():
            return None
        value = read_json_file(path)
        if (
            not isinstance(value, dict)
            or value.get("task_number") != task_number
            or value.get("review_id") != review_id
            or value.get("kind") != "remediation-no-change-receipt"
        ):
            raise LckStopError("Remediation no-change receipt is invalid")
        return value


def _identity_from_mapping(value: Mapping[str, Any]) -> ReviewIdentity:
    changed = value.get("changed_files")
    if not isinstance(changed, list) or not all(
        isinstance(item, str) for item in changed
    ):
        raise LckStopError("Review invocation identity has invalid changed-files data")
    fields = {
        "task_number": value.get("task_number"),
        "pr_number": value.get("pr_number"),
        "base_sha": value.get("base_sha"),
        "head_sha": value.get("head_sha"),
        "task_body_sha256": value.get("task_body_sha256"),
        "merge_base_sha": value.get("merge_base_sha"),
        "effective_diff_sha256": value.get("effective_diff_sha256"),
    }
    if not isinstance(fields["task_number"], int) or not isinstance(
        fields["pr_number"], int
    ):
        raise LckStopError("Review invocation identity is incomplete")
    for name in ("base_sha", "head_sha", "merge_base_sha"):
        if not is_sha(fields[name]):
            raise LckStopError(f"Review invocation identity has invalid {name}")
    for name in ("task_body_sha256", "effective_diff_sha256"):
        item = fields[name]
        if not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None:
            raise LckStopError(f"Review invocation identity has invalid {name}")
    return ReviewIdentity(
        task_number=cast(int, fields["task_number"]),
        pr_number=cast(int, fields["pr_number"]),
        base_sha=cast(str, fields["base_sha"]),
        head_sha=cast(str, fields["head_sha"]),
        task_body_sha256=cast(str, fields["task_body_sha256"]),
        merge_base_sha=cast(str, fields["merge_base_sha"]),
        effective_diff_sha256=cast(str, fields["effective_diff_sha256"]),
        changed_files=tuple(changed),
    )


def _assert_review_target_facts_applicable(
    reviewed: ReviewIdentity,
    state: LiveState,
    task_contract: Mapping[str, Any],
) -> None:
    """Reject obvious stale Review identity before computing the current diff.

    This ordering matters when another actor pushed a new head that is visible on
    GitHub but whose Git object is not materialized locally.  Head/base/Task
    staleness must still produce the precise REVIEW_STALE_* result without a
    fetch or a failed local diff probe.
    """
    pr = state.open_pr
    if not isinstance(pr, Mapping):
        raise ReviewStaleError(
            "REVIEW_STALE_PR",
            f"reviewed PR #{reviewed.pr_number} is no longer the unique current OPEN PR",
        )
    current_number = pr.get("number")
    if current_number != reviewed.pr_number:
        raise ReviewStaleError(
            "REVIEW_STALE_PR",
            f"OPEN PR changed from #{reviewed.pr_number} to #{current_number}",
        )
    current_head = _pr_head_sha(pr)
    if current_head != reviewed.head_sha:
        raise ReviewStaleError(
            "REVIEW_STALE_HEAD",
            f"PR head changed from {reviewed.head_sha} to {current_head or 'unavailable'}",
        )
    current_base = _pr_base_sha(pr)
    if current_base != reviewed.base_sha:
        raise ReviewStaleError(
            "REVIEW_STALE_BASE",
            f"PR base changed from {reviewed.base_sha} to {current_base or 'unavailable'}",
        )
    current_task = task_contract.get("body_sha256")
    if current_task != reviewed.task_body_sha256:
        raise ReviewStaleError(
            "REVIEW_STALE_TASK",
            "Task Contract changed since Review Prepare",
        )


def _assert_review_applicable(start: ReviewIdentity, current: ReviewIdentity) -> None:
    """Compare exact reviewed/current identities after basic target facts match."""
    if current.pr_number != start.pr_number:
        raise ReviewStaleError(
            "REVIEW_STALE_PR",
            f"OPEN PR changed from #{start.pr_number} to #{current.pr_number}",
        )
    if current.head_sha != start.head_sha:
        raise ReviewStaleError(
            "REVIEW_STALE_HEAD",
            f"PR head changed from {start.head_sha} to {current.head_sha}",
        )
    if current.base_sha != start.base_sha:
        raise ReviewStaleError(
            "REVIEW_STALE_BASE",
            f"PR base changed from {start.base_sha} to {current.base_sha}",
        )
    if current.task_body_sha256 != start.task_body_sha256:
        raise ReviewStaleError(
            "REVIEW_STALE_TASK",
            "Task Contract changed during this Review invocation",
        )
    if (
        current.merge_base_sha != start.merge_base_sha
        or current.effective_diff_sha256 != start.effective_diff_sha256
        or current.changed_files != start.changed_files
    ):
        raise ReviewStaleError(
            "REVIEW_STALE_DIFF",
            "effective diff changed during this Review invocation",
        )


@dataclass(frozen=True)
class ReviewContext:
    review_id: str
    task_contract: Mapping[str, Any]
    identity: ReviewIdentity
    checks: Mapping[str, Any]
    validation: Mapping[str, Any]
    review_root: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "review-prepare",
            "status": "READY_FOR_SEMANTIC_REVIEW",
            "review_id": self.review_id,
            "task_contract": _jsonable(self.task_contract),
            "review_target": self.identity.to_dict(),
            "checks": _jsonable(self.checks),
            "validation": _jsonable(self.validation),
            "review_root": str(self.review_root),
            "workspace_mode": "implementation-read-only",
            "agent_role": ["Inspect", "Reason", "Judge", "Report"],
            "mechanical_authority": "live Git/GitHub state resolved by LCK",
            "forbidden_handoff_authority": [
                "Delivery SHA",
                "Delivery base SHA",
                "Delivery PR identity",
                "Delivery checks snapshot",
                "Delivery validation snapshot",
            ],
        }


def _review_validation_failure(payload: Mapping[str, Any]) -> str:
    failed_command: Mapping[str, Any] | None = None
    commands = payload.get("commands")
    if isinstance(commands, list):
        failed_command = next(
            (
                item
                for item in commands
                if isinstance(item, Mapping) and item.get("status") == "fail"
            ),
            None,
        )
    evidence = payload.get("evidence_path") or payload.get("output_dir")
    detail = [f"formal Review validation failed: {payload.get('status', 'fail')}"]
    if failed_command is not None:
        command_id = failed_command.get("command_id", "unknown")
        exit_code = failed_command.get("exit_code", "unknown")
        diagnostic = safe_text(failed_command.get("diagnostic"), limit=1200)
        detail.append(f"failed command {command_id} (exit {exit_code})")
        if diagnostic:
            detail.append(f"diagnostic: {diagnostic}")
    base_sha = payload.get("validated_base_sha")
    head_sha = payload.get("validated_head_sha")
    if is_sha(base_sha) and is_sha(head_sha):
        detail.append(f"validated base {base_sha}, head {head_sha}")
    if isinstance(evidence, str) and evidence:
        detail.append(f"evidence: {evidence}")
    return "; ".join(detail)


class ReviewPreparer:
    """Resolve a fresh review target and construct one bounded read-only context."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        validation: ReviewValidationGate | None = None,
        checks_gate: DeliveryChecksGate | None = None,
        workspace: ReviewWorkspaceManager | None = None,
        store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.validation = validation or ReviewValidationGate(resolver)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)
        self.workspace = workspace or ReviewWorkspaceManager(resolver)
        self.store = store or ReviewInvocationStore(resolver.repo_root)

    def prepare(self, task_number: int) -> ReviewContext:
        if self.store.read_remediation_session(task_number) is not None:
            raise LckStopError(
                "Review Prepare STOP: a prepared Remediation session must be completed "
                "or closed with remediation no-change first"
            )
        invocation = self.store.begin_review_prepare(task_number)
        review_root: Path | None = None

        def mark(state: str, **fields: Any) -> None:
            payload: dict[str, Any] = {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "review-prepare-in-flight",
                "operation_id": invocation.operation_id,
                "task_number": task_number,
                "pid": os.getpid(),
                "state": state,
                "review_root": str(review_root) if review_root else None,
                "authority": "operation-local in-flight protection only",
            }
            payload.update(fields)
            invocation.update(payload)

        try:
            recovered = invocation.recovered
            if recovered is not None:
                previous_root = recovered.get("review_root")
                if previous_root is not None:
                    if not isinstance(previous_root, str) or not previous_root:
                        raise LckStopError(
                            "stale Review Prepare has an invalid isolated clone path"
                        )
                    previous_path = Path(previous_root)
                    if previous_path.exists():
                        self.workspace.remove_recovered(previous_path)
                previous_review_id = recovered.get("review_id")
                if isinstance(previous_review_id, str):
                    self.store.delete_guard(previous_review_id)
            mark("resolving-live-state")
            snapshot = self.snapshots.acquire(
                task_number,
                operation="review-prepare",
            )
            state = snapshot.state
            decision = self.eligibility.resolve(state, Phase.REVIEW_PREPARE)
            if not decision.eligible:
                raise LckStopError(
                    f"Review Prepare STOP for Task #{task_number}: "
                    + "; ".join(decision.reasons)
                )
            task_contract = _task_contract_from_state(state)
            target = _review_target_refs(state, task_contract)
            review_root = self.workspace.path_for(task_number, invocation.operation_id)
            mark(
                "clone-reserved",
                target=target.to_dict(),
                review_root=str(review_root),
            )
            self.workspace.create(
                task_number, target.base_sha, target.head_sha, review_root
            )
            mark(
                "clone-created",
                target=target.to_dict(),
                review_root=str(review_root),
            )
            snapshot = self.snapshots.bind_required_checks(
                snapshot, repo_root=review_root
            )
            mark(
                "checking-current-pr",
                target=target.to_dict(),
                required_checks=snapshot.required_checks,
            )
            checks = self.checks_gate.evaluate(snapshot)
            identity = _review_identity(
                self.resolver,
                state,
                task_contract,
                repo_root=review_root,
            )
            mark(
                "review-target-derived",
                identity=identity.to_dict(),
                review_root=str(review_root),
            )
            mark("formal-validation", identity=identity.to_dict())
            validation = self.validation.run(
                review_root, identity.base_sha, identity.head_sha
            )
            mark(
                "validation-persisted",
                identity=identity.to_dict(),
                validation=validation,
            )
            if validation.get("status") != "pass":
                raise LckStopError(_review_validation_failure(validation))
            mark(
                "sealing-review-context",
                identity=identity.to_dict(),
                validation=validation,
                checks=checks,
            )
            self.workspace.seal_for_review(review_root, identity.head_sha)
            review_id = self.store.new_id()
            guard = {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "review-invocation-guard",
                "review_id": review_id,
                "task_number": task_number,
                "identity": identity.to_dict(),
                "review_root": str(review_root),
                "validation": validation,
                "checks": checks,
                "snapshot": snapshot.to_dict(),
                "authority": (
                    "sealed Review Prepare target; historical identity for Review Complete "
                    "applicability comparison, never current authority"
                ),
            }
            self.store.write_guard(review_id, guard)
            mark(
                "handed-off",
                identity=identity.to_dict(),
                validation=validation,
                checks=checks,
                review_id=review_id,
            )
            context = ReviewContext(
                review_id=review_id,
                task_contract=task_contract,
                identity=identity,
                checks=checks,
                validation=validation,
                review_root=review_root,
            )
            invocation.release_lock()
        except BaseException as exc:
            cleanup_error: BaseException | None = None
            try:
                mark("failed", error=safe_text(str(exc), limit=1200))
            except BaseException:
                pass
            if review_root is not None and review_root.exists():
                try:
                    self.workspace.remove(review_root)
                except BaseException as remove_exc:
                    cleanup_error = remove_exc
            if cleanup_error is not None:
                raise cleanup_error from exc
            invocation.finish()
            raise
        return context


@dataclass(frozen=True)
class ReviewCompletionResult:
    review_id: str
    task_number: int
    verdict: str
    status: str
    identity: ReviewIdentity
    record_path: Path

    def to_dict(self) -> dict[str, Any]:
        human_boundary = (
            "STOP; run deterministic Merge Preflight before any manual merge"
            if self.verdict == "PASS"
            else "STOP; Human must explicitly choose remediation, redesign, or abandon"
        )
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "review-complete",
            "review_id": self.review_id,
            "task_number": self.task_number,
            "verdict": self.verdict,
            "status": self.status,
            "review_target": self.identity.to_dict(),
            "record_path": str(self.record_path),
            "human_boundary": human_boundary,
            "automatic_remediation": False,
        }


class ReviewCompleter:
    """Accept a semantic verdict only if a fresh completion snapshot still applies.

    ``review prepare`` and ``review complete`` are separate LCK operations.
    Prepare seals the exact target that the semantic reviewer inspected. Complete
    acquires one fresh, phase-specific authoritative snapshot, compares it with
    that sealed target, and accepts the verdict only when the PR, base, head,
    Task Contract, and effective diff are still identical. Downstream helpers do
    not reacquire authority inside the Review Complete operation.
    """

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        checks_gate: DeliveryChecksGate | None = None,
        store: ReviewInvocationStore | None = None,
        workspace: ReviewWorkspaceManager | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.workspace = workspace or ReviewWorkspaceManager(resolver)

    @staticmethod
    def _read_findings(path: Path | None, verdict: str) -> str:
        if path is None:
            if verdict == "FAIL":
                raise LckStopError("FAIL verdict requires --findings-file")
            return ""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LckStopError(f"cannot read findings file: {exc}") from exc
        if verdict == "FAIL" and not text.strip():
            raise LckStopError("FAIL verdict requires non-empty findings")
        return text

    def complete(
        self,
        task_number: int,
        review_id: str,
        *,
        verdict: str,
        findings_file: Path | None = None,
    ) -> ReviewCompletionResult:
        verdict = verdict.upper()
        if verdict not in {"PASS", "FAIL"}:
            raise LckStopError("Review verdict must be PASS or FAIL")
        findings = self._read_findings(findings_file, verdict)
        guard = self.store.read_guard(review_id)
        if guard.get("task_number") != task_number:
            raise LckStopError("Review invocation does not belong to this Task")
        raw_identity = guard.get("identity")
        if not isinstance(raw_identity, Mapping):
            raise LckStopError("Review invocation guard has no identity")
        reviewed_identity = _identity_from_mapping(raw_identity)
        review_root_value = guard.get("review_root")
        if not isinstance(review_root_value, str) or not review_root_value:
            raise LckStopError("Review invocation guard has no review root")
        review_root = Path(review_root_value)

        completion_terminal = False
        try:
            validation = guard.get("validation")
            if (
                not isinstance(validation, Mapping)
                or validation.get("status") != "pass"
            ):
                raise LckStopError(
                    "Review invocation has no successful formal validation"
                )
            if reviewed_identity.task_number != task_number:
                raise LckStopError(
                    "Review invocation identity does not belong to this Task"
                )
            prepared_checks = guard.get("checks")
            if (
                not isinstance(prepared_checks, Mapping)
                or prepared_checks.get("status") != "pass"
            ):
                raise LckStopError("Review invocation has no successful PR check gate")
            prepared_snapshot = guard.get("snapshot")
            if not isinstance(prepared_snapshot, Mapping):
                raise LckStopError("Review invocation has no sealed operation snapshot")
            if prepared_snapshot.get("operation") != "review-prepare":
                raise LckStopError("Review invocation snapshot has the wrong operation")

            completion_snapshot = self.snapshots.acquire(
                task_number,
                operation="review-complete",
            )
            state = completion_snapshot.state
            decision = self.eligibility.resolve(state, Phase.REVIEW_COMPLETE)
            if not decision.eligible:
                raise LckStopError(
                    f"Review Complete STOP for Task #{task_number}: "
                    + "; ".join(decision.reasons)
                )
            current_contract = _task_contract_from_state(state)
            _assert_review_target_facts_applicable(
                reviewed_identity,
                state,
                current_contract,
            )
            self.workspace.assert_ready_for_completion(
                review_root, reviewed_identity.head_sha
            )
            completion_snapshot = self.snapshots.bind_required_checks(
                completion_snapshot, repo_root=review_root
            )
            current_identity = _review_identity(
                self.resolver,
                state,
                current_contract,
                repo_root=review_root,
            )
            _assert_review_applicable(reviewed_identity, current_identity)
            completion_checks = self.checks_gate.evaluate(completion_snapshot)
            record = {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "independent-review-record",
                "review_id": review_id,
                "task_number": task_number,
                "verdict": verdict,
                "status": (
                    "READY_FOR_MERGE_PREFLIGHT"
                    if verdict == "PASS"
                    else "STOP_REQUIRED"
                ),
                "identity": reviewed_identity.to_dict(),
                "findings": findings,
                "findings_sha256": hashlib.sha256(findings.encode("utf-8")).hexdigest(),
                "validation": validation,
                "checks": dict(prepared_checks),
                "completion_checks": completion_checks,
                "review_snapshot": dict(prepared_snapshot),
                "completion_snapshot": completion_snapshot.to_dict(),
                "authority_note": (
                    "Review Prepare target and fresh Review Complete snapshot matched; "
                    "this record is audit evidence, while Merge Preflight must reacquire "
                    "current Git/GitHub authority before human merge"
                ),
            }
            record_path = self.store.write_record(task_number, review_id, record)
            self.store.write_latest_review(task_number, review_id, verdict)
            self.store.clear_review_required(task_number)
            completion_terminal = True
            return ReviewCompletionResult(
                review_id=review_id,
                task_number=task_number,
                verdict=verdict,
                status=cast(str, record["status"]),
                identity=reviewed_identity,
                record_path=record_path,
            )
        except ReviewStaleError:
            # Stale is a formal terminal outcome for this prepared target.  It
            # cannot be retried safely with the old target, so a fresh Prepare
            # must reclaim this operation-owned state.
            completion_terminal = True
            raise
        finally:
            if completion_terminal:
                cleanup_error: BaseException | None = None
                try:
                    self.workspace.remove(review_root)
                except BaseException as exc:
                    cleanup_error = exc
                if cleanup_error is None:
                    self.store.delete_guard(review_id)
                    self.store.release_review_prepare(task_number, review_id)
                else:
                    raise cleanup_error
            # Otherwise preserve the guard, sealed clone, and Prepare marker
            # for a retry when live-state resolution, validation, checks, or
            # persistence fails before a terminal result is recorded.


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

    def verify_tree_unchanged(
        self,
        expected_tree: str,
        *,
        expected_head_sha: str | None = None,
    ) -> None:
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
        if expected_head_sha is not None:
            current_head = self._run(
                ["git", "rev-parse", "HEAD"],
                "lck-post-validation-head",
            ).stdout.strip()
            if current_head != expected_head_sha:
                raise LckStopError("local Task HEAD changed during formal validation")

    def execute(
        self,
        expected_tree: str,
        message: str,
        *,
        expected_parent_sha: str | None = None,
    ) -> EffectReceipt:
        if not message.strip() or "\x00" in message or len(message) > 240:
            raise LckStopError("commit message must be non-empty and <= 240 characters")
        current_tree = self._run(
            ["git", "write-tree"], "lck-write-tree-pre-commit"
        ).stdout.strip()
        if current_tree != expected_tree:
            raise LckStopError(
                "commit precondition failed: staged tree is not validated tree"
            )
        if expected_parent_sha is not None:
            current_parent = self._run(
                ["git", "rev-parse", "HEAD"],
                "lck-commit-parent-head",
            ).stdout.strip()
            if current_parent != expected_parent_sha:
                raise LckStopError(
                    "commit precondition failed: local Task HEAD changed"
                )
        self._run(["git", "commit", "-m", message], "lck-commit-current-tree")
        head = self._run(
            ["git", "rev-parse", "HEAD"], "lck-head-after-commit"
        ).stdout.strip()
        if expected_parent_sha is not None:
            parent = self._run(
                ["git", "rev-parse", "HEAD^"],
                "lck-parent-after-commit",
            ).stdout.strip()
            if parent != expected_parent_sha:
                raise LckStopError(
                    "commit postcondition failed: commit parent HEAD changed"
                )
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

    def _identity(
        self,
        branch: str,
        *,
        expected_head_sha: str | None = None,
    ) -> tuple[str, str | None]:
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
        if expected_head_sha is not None and head != expected_head_sha:
            raise LckStopError(
                "push precondition failed: validated local Task HEAD changed"
            )
        remote_result = self.resolver.runner.run(
            ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
            command_id="lck-push-remote-head",
        )
        if remote_result.returncode != 0:
            raise LckStopError("cannot resolve remote Task branch before push")
        refs = _remote_refs(remote_result.stdout)
        remote_oid = refs.get(branch)
        return head, remote_oid

    def _ensure_upstream(self, branch: str) -> str:
        expected = f"origin/{branch}"
        upstream = self.resolver.runner.run(
            [
                "git",
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ],
            command_id="lck-task-branch-upstream",
        )
        current = upstream.stdout.strip() if upstream.returncode == 0 else ""
        if current != expected:
            set_upstream = self.resolver.runner.run(
                [
                    "git",
                    "branch",
                    "--set-upstream-to",
                    expected,
                    branch,
                ],
                command_id="lck-set-task-branch-upstream",
            )
            if set_upstream.returncode != 0:
                raise LckStopError(
                    "cannot establish Task branch upstream: "
                    + (
                        set_upstream.stderr.strip()
                        or set_upstream.stdout.strip()
                        or f"exit {set_upstream.returncode}"
                    )
                )
            upstream = self.resolver.runner.run(
                [
                    "git",
                    "rev-parse",
                    "--abbrev-ref",
                    "--symbolic-full-name",
                    "@{upstream}",
                ],
                command_id="lck-verify-task-branch-upstream",
            )
            current = upstream.stdout.strip() if upstream.returncode == 0 else ""
        if current != expected:
            raise LckStopError(
                "Task branch upstream postcondition failed: "
                f"expected {expected!r}, observed {current or 'unavailable'!r}"
            )
        return current

    def execute(
        self,
        branch: str,
        *,
        expected_head_sha: str | None = None,
    ) -> EffectReceipt:
        head, remote_oid = self._identity(
            branch,
            expected_head_sha=expected_head_sha,
        )
        if remote_oid == head:
            upstream = self._ensure_upstream(branch)
            return EffectReceipt(
                effect="ensure_remote_branch",
                action="already-synced",
                details={
                    "head_sha": head,
                    "remote_oid": remote_oid,
                    "upstream": upstream,
                },
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
            [
                "git",
                "push",
                "-u",
                "origin",
                f"{head}:refs/heads/{branch}",
            ],
            command_id="lck-push-task-branch",
        )
        if push.returncode != 0:
            raise LckStopError(
                "Task branch push failed: "
                + (push.stderr.strip() or push.stdout.strip())
            )
        final_head, final_remote = self._identity(
            branch,
            expected_head_sha=expected_head_sha,
        )
        if final_head != head or final_remote != head:
            raise LckStopError(
                "push postcondition failed: local and remote heads differ"
            )
        upstream = self._ensure_upstream(branch)
        return EffectReceipt(
            effect="ensure_remote_branch",
            action=action,
            details={
                "head_sha": head,
                "remote_oid": final_remote,
                "upstream": upstream,
            },
        )


class EnsureOpenPrEffect:
    """Ensure one OPEN PR using only operation-snapshot authority.

    The PR helper may query/create the exact PR as the bounded effect itself,
    but it never re-runs lifecycle resolution.
    """

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        state: LiveState,
        *,
        head_sha: str,
        summary: str,
        risks: str,
        critical_outcome: Mapping[str, Any],
        validation: Mapping[str, Any],
        expected_base_sha: str,
        expected_body_sha256: str,
    ) -> EffectReceipt:
        repository = state.repository
        base = _authoritative_remote_main_sha(state.git)
        issue = state.issue
        if (
            not isinstance(repository, str)
            or not is_sha(head_sha)
            or not is_sha(base)
            or not isinstance(issue, Mapping)
        ):
            raise LckStopError("OPEN PR snapshot preconditions are incomplete")
        if state.merged is not False:
            raise LckStopError(
                "OPEN PR precondition failed: Task merge state is unavailable "
                "or already merged"
            )
        if state.target_branch == BASE_BRANCH:
            raise LckStopError("OPEN PR precondition failed: Task branch is invalid")
        if base != expected_base_sha:
            raise LckStopError("OPEN PR precondition failed: snapshot base mismatch")
        if issue.get("body_sha256") != expected_body_sha256:
            raise LckStopError(
                "OPEN PR precondition failed: snapshot Task body mismatch"
            )
        title = issue.get("title")
        if not isinstance(title, str) or not title.strip():
            raise LckStopError("Task title is unavailable for PR creation")
        body = (
            f"Closes #{state.task_number}\n\n"
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
                    head_sha,
                    expected_base_sha,
                    warnings,
                )
            except PrResolveError as exc:
                raise LckStopError(str(exc)) from exc
        if (
            result.get("head_sha") != head_sha
            or result.get("base_sha") != expected_base_sha
            or not isinstance(result.get("number"), int)
        ):
            raise LckStopError("OPEN PR postcondition returned an unexpected identity")
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


class ReuseExistingOpenPrEffect:
    """Verify the pushed repair is attached to the snapshot's existing OPEN PR."""

    PR_IDENTITY_FIELDS: Final = (
        "number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid"
    )

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        state: LiveState,
        *,
        head_sha: str,
        summary: str,
        risks: str,
        critical_outcome: Mapping[str, Any],
        validation: Mapping[str, Any],
        expected_base_sha: str,
        expected_body_sha256: str,
    ) -> EffectReceipt:
        del summary, risks, critical_outcome, validation
        pr = state.open_pr
        issue = state.issue
        repository = state.repository
        if (
            not isinstance(pr, Mapping)
            or pr.get("isDraft") is not False
            or not isinstance(repository, str)
        ):
            raise LckStopError("Remediation requires the existing non-Draft OPEN PR")
        if _authoritative_remote_main_sha(state.git) != expected_base_sha:
            raise LckStopError(
                "Remediation PR precondition failed: snapshot base mismatch"
            )
        if (
            not isinstance(issue, Mapping)
            or issue.get("body_sha256") != expected_body_sha256
        ):
            raise LckStopError(
                "Remediation PR precondition failed: snapshot Task body mismatch"
            )
        pr_number = pr.get("number")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool):
            raise LckStopError("Remediation PR number is unavailable")
        result = self.resolver.runner.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repository,
                "--json",
                self.PR_IDENTITY_FIELDS,
            ],
            command_id="lck-remediation-pr-postcondition",
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise LckStopError("Remediation PR postcondition cannot be queried")
        current = read_json_text(
            result.stdout, field="lck-remediation-pr-postcondition"
        )
        if not isinstance(current, Mapping):
            raise LckStopError("Remediation PR postcondition is malformed")
        if (
            current.get("number") != pr_number
            or str(current.get("state", "")).upper() != "OPEN"
            or current.get("isDraft") is not False
            or current.get("headRefOid") != head_sha
            or current.get("baseRefOid") != expected_base_sha
            or current.get("headRefName") != state.target_branch
            or current.get("baseRefName") != BASE_BRANCH
        ):
            raise LckStopError(
                "Remediation PR postcondition failed: existing PR is not on the pushed head"
            )
        return EffectReceipt(
            effect="reuse_open_pr",
            action="reused-current-open-pr",
            details={
                "number": pr_number,
                "url": current.get("url"),
                "head_sha": head_sha,
                "base_sha": expected_base_sha,
            },
        )


class SetReviewStatusEffect:
    """Move the Task to Review and verify only that metadata effect."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _query_project_status(self, repository: str, task_number: int) -> str | None:
        result = self.resolver.runner.run(
            [
                "gh",
                "issue",
                "view",
                str(task_number),
                "--repo",
                repository,
                "--json",
                "projectItems",
            ],
            command_id="lck-review-status-postcondition",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        value = read_json_text(result.stdout, field="lck-review-status-postcondition")
        if not isinstance(value, Mapping):
            return None
        return _find_project_status(value.get("projectItems"))

    def execute(
        self,
        state: LiveState,
        *,
        expected_pr: Mapping[str, Any] | None = None,
        checks_result: Mapping[str, Any] | None = None,
    ) -> EffectReceipt:
        if state.repository is None:
            raise LckStopError("cannot set Review status without repository identity")
        if expected_pr is None or checks_result is None:
            raise LckStopError(
                "Project Status Review requires the exact checks-gated PR receipt"
            )
        gated_pr = checks_result.get("pr")
        expected_identity = {
            "number": expected_pr.get("number"),
            "head_sha": expected_pr.get("head_sha"),
            "base_sha": expected_pr.get("base_sha"),
        }
        if (
            not isinstance(gated_pr, Mapping)
            or {
                "number": gated_pr.get("number"),
                "head_sha": gated_pr.get("head_sha"),
                "base_sha": gated_pr.get("base_sha"),
            }
            != expected_identity
        ):
            raise LckStopError(
                "Project Status precondition failed: checks receipt PR identity mismatch"
            )
        if checks_result.get("status") != "pass":
            raise LckStopError(
                "Project Status precondition failed: PR checks are not passing"
            )
        if state.project_status == "Review":
            return EffectReceipt(
                effect="set_review_status", action="already-review", details={}
            )
        previous_status = state.project_status
        if previous_status not in {"Ready", "In Progress"}:
            raise LckStopError(
                "Project Status precondition failed: prior status cannot be restored"
            )
        set_project_status_with_runner(
            self.resolver.runner,
            state.repository,
            state.task_number,
            value="Review",
        )
        observed = self._query_project_status(state.repository, state.task_number)
        if observed == "Review":
            return EffectReceipt(
                effect="set_review_status",
                action="updated",
                details={"status": "Review"},
            )

        try:
            set_project_status_with_runner(
                self.resolver.runner,
                state.repository,
                state.task_number,
                value=previous_status,
            )
        except Exception as restore_exc:
            raise LckStopError(
                "Project Status postcondition failed and compensation could not "
                f"restore {previous_status!r}"
            ) from restore_exc
        restored = self._query_project_status(state.repository, state.task_number)
        if restored != previous_status:
            raise LckStopError(
                "Project Status postcondition failed and compensation did not "
                f"restore {previous_status!r}"
            )
        raise LckStopError(
            "Project Status postcondition failed; restored Project Status to "
            f"{previous_status!r}"
        )


class DeliveryChecksGate:
    """Evaluate PR checks from one operation snapshot or one exact PR query.

    This gate never resolves lifecycle state and never polls.  If checks are
    pending, the current operation stops and a later lifecycle invocation
    acquires a fresh snapshot.
    """

    PR_FIELDS: Final = (
        "number,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
        "statusCheckRollup,mergeable,url"
    )

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.runner = resolver.runner

    @staticmethod
    def _required_names(required: Mapping[str, Any]) -> set[str]:
        return set(_required_check_contract(required))

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
    def _pr_identity_from_pr(pr: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "number": pr.get("number"),
            "head_sha": pr.get("headRefOid"),
            "base_sha": pr.get("baseRefOid"),
        }

    @staticmethod
    def _pr_identity(state: LiveState) -> dict[str, Any]:
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            return {}
        return DeliveryChecksGate._pr_identity_from_pr(pr)

    @classmethod
    def _evaluate_pr_checks(
        cls,
        pr: Mapping[str, Any],
        required: Mapping[str, Any],
    ) -> dict[str, Any]:
        checks = _normalize_checks(pr.get("statusCheckRollup"))
        required_names = cls._required_names(required)
        config = required.get("configuration")
        observed = cls._observed_categories(checks)
        try:
            failed = int(checks.get("failed", 0) or 0)
            unknown = int(checks.get("skipped_or_unknown", 0) or 0)
            pending = int(checks.get("pending", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise LckStopError("PR check summary is malformed") from exc

        if failed > 0 or unknown > 0:
            raise LckStopError("PR checks failed, cancelled, skipped, or unknown")
        if pending > 0:
            raise LckStopError(
                "PR checks are pending; start a new lifecycle operation after CI completes"
            )

        if required_names:
            failed_required = {
                name
                for name in required_names
                if observed.get(name) not in {None, "success"}
            }
            if failed_required:
                raise LckStopError(
                    "required PR checks are not successful: "
                    + ", ".join(sorted(failed_required))
                )
            missing = required_names - set(observed)
            if missing:
                raise LckStopError(
                    "required PR checks are not present: " + ", ".join(sorted(missing))
                )

        return {
            "status": "pass",
            "configuration": config,
            "required": sorted(required_names),
            "pr": cls._pr_identity_from_pr(pr),
            "checks": checks,
        }

    def evaluate(self, snapshot: OperationSnapshot) -> dict[str, Any]:
        state = snapshot.state
        if state.status is not ResolutionStatus.RESOLVED:
            raise LckStopError(
                "cannot evaluate checks from unresolved operation snapshot: "
                + "; ".join(state.stop_reasons)
            )
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            raise LckStopError("PR checks require one OPEN PR in operation snapshot")
        required = snapshot.required_checks
        if not isinstance(required, Mapping):
            raise LckStopError("required PR check configuration was not acquired")
        _required_check_contract_for_snapshot(snapshot)
        return self._evaluate_pr_checks(pr, required)

    def query_exact_pr(
        self,
        repository: str,
        pr_number: int,
        *,
        expected_head_sha: str,
        expected_base_sha: str,
        required_checks: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Targeted post-effect query for a PR created/updated by this operation."""
        result = self.runner.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repository,
                "--json",
                self.PR_FIELDS,
            ],
            command_id="lck-checks-exact-pr",
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise LckStopError(
                "cannot query the exact PR after the PR effect: "
                + (result.stderr.strip() or f"exit {result.returncode}")
            )
        value = read_json_text(result.stdout, field="lck-checks-exact-pr")
        if not isinstance(value, Mapping):
            raise LckStopError("exact PR check query returned a non-object")
        if (
            value.get("number") != pr_number
            or str(value.get("state", "")).upper() != "OPEN"
            or value.get("isDraft") is not False
            or value.get("headRefOid") != expected_head_sha
            or value.get("baseRefOid") != expected_base_sha
        ):
            raise LckStopError("exact PR identity changed after the PR effect")
        return self._evaluate_pr_checks(value, required_checks)

    @staticmethod
    def _checks_postcondition(
        state: LiveState,
        checks_result: Mapping[str, Any],
    ) -> bool:
        """Compare a snapshot PR/check fact to a previously completed check gate."""
        if checks_result.get("status") != "pass":
            return False
        current_pr = state.open_pr
        gated_pr = checks_result.get("pr")
        if not isinstance(current_pr, Mapping) or not isinstance(gated_pr, Mapping):
            return False
        if DeliveryChecksGate._pr_identity_from_pr(current_pr) != {
            "number": gated_pr.get("number"),
            "head_sha": gated_pr.get("head_sha"),
            "base_sha": gated_pr.get("base_sha"),
        }:
            return False
        current_checks = _normalize_checks(current_pr.get("statusCheckRollup"))
        expected_checks = checks_result.get("checks")
        if not isinstance(expected_checks, Mapping):
            return False
        return current_checks == expected_checks


class ReviewPassGate:
    """Prove that the latest accepted Review PASS still matches live facts."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.store = store or ReviewInvocationStore(resolver.repo_root)

    def run(self, task_number: int, state: LiveState) -> dict[str, Any]:
        latest = self.store.read_latest_review(task_number)
        if not isinstance(latest, Mapping) or latest.get("verdict") != "PASS":
            raise LckStopError(
                "Merge Preflight requires the latest Independent Review PASS"
            )
        review_id = latest.get("review_id")
        if not isinstance(review_id, str):
            raise LckStopError("latest Independent Review PASS has no review id")
        record = self.store.read_record(task_number, review_id)
        if (
            record.get("task_number") != task_number
            or record.get("review_id") != review_id
            or record.get("verdict") != "PASS"
            or record.get("status") != "READY_FOR_MERGE_PREFLIGHT"
        ):
            raise LckStopError("latest Independent Review PASS record is invalid")
        raw_identity = record.get("identity")
        if not isinstance(raw_identity, Mapping):
            raise LckStopError("Independent Review PASS has no identity")
        recorded = _identity_from_mapping(raw_identity)
        current_contract = _task_contract_from_state(state)
        try:
            _assert_review_target_facts_applicable(
                recorded,
                state,
                current_contract,
            )
        except ReviewStaleError as exc:
            raise LckStopError(f"Review PASS is stale: {exc}") from exc

        # Git commit objects are content-addressed. Once current PR/base/head and
        # Task Contract identity still match the accepted Review receipt, the
        # recorded merge-base/effective-diff identity remains mechanically bound
        # to those same commits. Merge Preflight therefore needs no source-repo
        # object materialization or duplicate local diff probe.
        return {
            "status": "pass",
            "review_id": review_id,
            "identity": recorded.to_dict(),
            "recorded_identity": recorded.to_dict(),
        }


@dataclass(frozen=True)
class MergePreflightResult:
    task_number: int
    status: str
    pr: Mapping[str, Any]
    review: Mapping[str, Any]
    checks: Mapping[str, Any]
    blockers: Mapping[str, Any]
    mergeability: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "merge-preflight",
            "task_number": self.task_number,
            "status": self.status,
            "pr": _jsonable(self.pr),
            "review": _jsonable(self.review),
            "checks": _jsonable(self.checks),
            "blockers": _jsonable(self.blockers),
            "mergeability": self.mergeability,
            "human_boundary": (
                "STOP — maintainer must perform the manual Squash Merge; "
                "LCK has no auto-merge path"
            ),
            "automatic_merge": False,
        }


class MergePreflight:
    """Run deterministic merge gates without mutating GitHub."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        review_gate: ReviewPassGate | None = None,
        checks_gate: DeliveryChecksGate | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.review_gate = review_gate or ReviewPassGate(resolver)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)

    def run(self, task_number: int) -> MergePreflightResult:
        snapshot = self.snapshots.acquire(
            task_number,
            operation="merge-preflight",
            include_required_checks=True,
        )
        state = snapshot.state
        if state.status is not ResolutionStatus.RESOLVED:
            raise LckStopError("Merge Preflight STOP: " + "; ".join(state.stop_reasons))
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            raise LckStopError("Merge Preflight requires one current OPEN PR")
        if pr.get("isDraft") is not False:
            raise LckStopError("Merge Preflight requires a non-Draft OPEN PR")
        if str(pr.get("state", "")).upper() != "OPEN":
            raise LckStopError("Merge Preflight requires an OPEN PR")
        if pr.get("baseRefName") != BASE_BRANCH:
            raise LckStopError("Merge Preflight PR base branch is not main")
        if pr.get("headRefName") != state.target_branch:
            raise LckStopError(
                "Merge Preflight PR head branch is not the resolved Task branch"
            )
        head_sha = _pr_head_sha(pr)
        base_sha = _pr_base_sha(pr)
        if head_sha is None or base_sha is None:
            raise LckStopError("Merge Preflight PR head/base identity is unavailable")
        if state.remote_task_oid != head_sha:
            raise LckStopError(
                "Merge Preflight remote Task branch diverges from PR head"
            )
        if state.local_task_head is not None and state.local_task_head != head_sha:
            raise LckStopError(
                "Merge Preflight local Task branch diverges from PR head"
            )
        if state.project_status != "Review":
            raise LckStopError("Merge Preflight requires Project Status Review")

        blockers = _formal_blockers_gate(state.relationships)
        if blockers.get("status") != "pass":
            raise LckStopError(
                "Merge Preflight unresolved blockers: "
                + str(blockers.get("detail") or blockers.get("status"))
            )
        review = self.review_gate.run(task_number, state)
        checks = self.checks_gate.evaluate(snapshot)
        if checks.get("limitation"):
            raise LckStopError(
                "Merge Preflight cannot prove required checks: "
                + str(checks["limitation"])
            )
        mergeability_value = pr.get("mergeable")
        mergeability = str(mergeability_value or "").upper()
        if mergeability not in {"MERGEABLE", "TRUE"}:
            raise LckStopError(
                "Merge Preflight mergeability is not proven: "
                + (mergeability or "UNKNOWN")
            )
        return MergePreflightResult(
            task_number=task_number,
            status="READY_FOR_HUMAN_MERGE",
            pr=pr,
            review=review,
            checks=checks,
            blockers=blockers,
            mergeability=mergeability,
        )


MergePreflightRunner = MergePreflight


def _label_names(issue: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(issue, Mapping):
        return set()
    raw = issue.get("labels")
    if isinstance(raw, Mapping):
        raw = raw.get("items")
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def _pending_receipt(
    effect: str,
    action: str,
    *,
    reason: str,
    details: Mapping[str, Any] | None = None,
) -> EffectReceipt:
    payload = {"reason": reason}
    if details:
        payload.update(details)
    return EffectReceipt(effect=effect, action=action, details=payload)


class MainSynchronizationEffect:
    """Fast-forward the local main to origin/main and prove merge reachability."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        state: LiveState,
        *,
        merge_sha: str | None,
    ) -> EffectReceipt:
        if not is_sha(merge_sha):
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="merged PR merge commit identity is unavailable",
            )
        if state.git.get("clean") is not True:
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="current worktree is not clean",
            )
        if state.git.get("branch") != BASE_BRANCH:
            switched = self.resolver.runner.run(
                ["git", "switch", BASE_BRANCH],
                command_id="lck-closeout-switch-main",
            )
            if switched.returncode != 0:
                return _pending_receipt(
                    "synchronize_main",
                    "pending",
                    reason="cannot switch to main",
                )
        fetched = self.resolver.runner.run(
            ["git", "fetch", "--prune", "origin"],
            command_id="lck-closeout-fetch-origin",
        )
        if fetched.returncode != 0:
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="cannot refresh origin/main",
            )
        merged = self.resolver.runner.run(
            ["git", "merge", "--ff-only", f"refs/remotes/origin/{BASE_BRANCH}"],
            command_id="lck-closeout-fast-forward-main",
        )
        if merged.returncode != 0:
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="local main cannot fast-forward to origin/main",
            )
        head = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-closeout-main-head",
        )
        origin = self.resolver.runner.run(
            ["git", "rev-parse", f"refs/remotes/origin/{BASE_BRANCH}"],
            command_id="lck-closeout-origin-main-head",
        )
        ancestry = self.resolver.runner.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                merge_sha,
                f"refs/remotes/origin/{BASE_BRANCH}",
            ],
            command_id="lck-closeout-merge-reachable",
        )
        if (
            head.returncode != 0
            or origin.returncode != 0
            or not is_sha(head.stdout.strip())
            or head.stdout.strip() != origin.stdout.strip()
            or ancestry.returncode != 0
        ):
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="main synchronization postcondition is not proven",
            )
        return EffectReceipt(
            effect="synchronize_main",
            action="synchronized",
            details={"main_sha": head.stdout.strip(), "merge_sha": merge_sha},
        )


class CloseoutMetadataEffect:
    """Converge only the exact closed Task's Project status and lifecycle labels."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _query_metadata(
        self, repository: str, task_number: int
    ) -> tuple[str | None, str | None, set[str]]:
        result = self.resolver.runner.run(
            [
                "gh",
                "issue",
                "view",
                str(task_number),
                "--repo",
                repository,
                "--json",
                "state,labels,projectItems",
            ],
            command_id="lck-closeout-metadata-postcondition",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None, None, set()
        value = read_json_text(
            result.stdout, field="lck-closeout-metadata-postcondition"
        )
        if not isinstance(value, Mapping):
            return None, None, set()
        raw_labels = value.get("labels")
        labels: set[str] = set()
        if isinstance(raw_labels, list):
            for item in raw_labels:
                if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                    labels.add(str(item["name"]))
                elif isinstance(item, str):
                    labels.add(item)
        return (
            safe_text(value.get("state")),
            _find_project_status(value.get("projectItems")),
            labels,
        )

    def execute(self, state: LiveState) -> EffectReceipt:
        if state.issue_state != "CLOSED":
            return _pending_receipt(
                "converge_task_metadata",
                "pending",
                reason="Issue is not closed by authoritative GitHub state",
            )
        if state.repository is None:
            return _pending_receipt(
                "converge_task_metadata",
                "pending",
                reason="repository identity is unavailable",
            )
        actions: list[str] = []
        try:
            if state.project_status != "Done":
                set_project_status_with_runner(
                    self.resolver.runner,
                    state.repository,
                    state.task_number,
                    value="Done",
                )
                actions.append("project-status-done")
            labels = _label_names(state.issue)
            if "codex:ready" not in labels or "codex:blocked" in labels:
                label_result = self.resolver.runner.run(
                    [
                        "gh",
                        "issue",
                        "edit",
                        str(state.task_number),
                        "--repo",
                        state.repository,
                        "--add-label",
                        "codex:ready",
                        "--remove-label",
                        "codex:blocked",
                    ],
                    command_id="lck-closeout-lifecycle-labels",
                )
                if label_result.returncode != 0:
                    return _pending_receipt(
                        "converge_task_metadata",
                        "pending",
                        reason="lifecycle label convergence failed",
                        details={"actions": actions},
                    )
                actions.append("lifecycle-labels-converged")
        except WorkflowToolError:
            return _pending_receipt(
                "converge_task_metadata",
                "pending",
                reason="Project Status convergence failed",
                details={"actions": actions},
            )
        final_state, final_project_status, final_labels = self._query_metadata(
            state.repository, state.task_number
        )
        if (
            str(final_state or "").upper() != "CLOSED"
            or final_project_status != "Done"
            or "codex:ready" not in final_labels
            or "codex:blocked" in final_labels
        ):
            return _pending_receipt(
                "converge_task_metadata",
                "pending",
                reason="metadata convergence postcondition is not proven",
                details={"actions": actions},
            )
        return EffectReceipt(
            effect="converge_task_metadata",
            action="already-converged" if not actions else "updated",
            details={"actions": actions},
        )


class CleanupTaskRefsEffect:
    """Clean only the verified Task branch and recognize GitHub auto-deletion."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        state: LiveState,
        *,
        expected_head_sha: str | None,
        merge_sha: str | None,
    ) -> EffectReceipt:
        branch = state.target_branch
        if branch == BASE_BRANCH or not _branch_matches_task(branch, state.task_number):
            raise LckStopError("Cleanup target is not the verified Task branch")
        worktrees = self.resolver.runner.run(
            ["git", "worktree", "list", "--porcelain"],
            command_id="lck-closeout-worktree-precondition",
        )
        if worktrees.returncode != 0:
            return _pending_receipt(
                "cleanup_task_refs",
                "pending",
                reason="current worktree ownership cannot be verified",
            )
        worktree_branches = {
            line.removeprefix("branch refs/heads/")
            for line in worktrees.stdout.splitlines()
            if line.startswith("branch refs/heads/")
        }
        if branch in worktree_branches:
            return _pending_receipt(
                "cleanup_task_refs",
                "pending",
                reason="verified Task branch is still used by a worktree",
            )

        if not is_sha(expected_head_sha) or not is_sha(merge_sha):
            raise LckStopError(
                "Cleanup STOP: merged PR head and squash merge identities are required"
            )
        tree_result = self.resolver.runner.run(
            ["git", "diff", "--quiet", expected_head_sha, merge_sha],
            command_id="lck-closeout-squash-tree-equality",
        )
        if tree_result.returncode != 0:
            raise LckStopError(
                "Cleanup STOP: PR head tree does not equal squash merge tree"
            )

        if state.local_task_branch is None and state.remote_task_branch is None:
            return EffectReceipt(
                effect="cleanup_task_refs",
                action="already-clean",
                details={"branch": branch, "remote_branch": "already-deleted"},
            )
        if state.local_task_branch is not None:
            local_result = self.resolver.runner.run(
                ["git", "rev-parse", f"refs/heads/{branch}"],
                command_id="lck-closeout-local-branch-tip",
            )
            local_tip = local_result.stdout.strip()
            if local_result.returncode != 0 or local_tip != expected_head_sha:
                raise LckStopError(
                    "Cleanup STOP: local Task branch diverges from merged PR head"
                )
            deleted = self.resolver.runner.run(
                ["git", "branch", "-d", branch],
                command_id="lck-closeout-local-branch-delete",
            )
            if deleted.returncode != 0:
                deleted = self.resolver.runner.run(
                    ["git", "branch", "-D", branch],
                    command_id="lck-closeout-local-branch-force-delete-after-proof",
                )
            if deleted.returncode != 0:
                return _pending_receipt(
                    "cleanup_task_refs",
                    "pending",
                    reason="verified local Task branch could not be deleted",
                )
        if state.remote_task_branch is not None:
            if state.remote_task_oid != expected_head_sha:
                raise LckStopError(
                    "Cleanup STOP: remote Task branch diverges from merged PR head"
                )
            remote_ref = self.resolver.runner.run(
                ["git", "ls-remote", "--heads", "origin", branch],
                command_id="lck-closeout-remote-branch-precondition",
            )
            if remote_ref.returncode != 0:
                return _pending_receipt(
                    "cleanup_task_refs",
                    "pending",
                    reason="remote Task branch precondition could not be verified",
                )
            remote_refs = _remote_refs(remote_ref.stdout)
            observed_remote_oid = remote_refs.get(branch)
            if observed_remote_oid is None:
                if remote_ref.stdout.strip():
                    raise LckStopError(
                        "Cleanup STOP: remote Task branch identity is malformed"
                    )
                return EffectReceipt(
                    effect="cleanup_task_refs",
                    action="already-clean",
                    details={"branch": branch, "remote_branch": "already-deleted"},
                )
            if observed_remote_oid != expected_head_sha:
                raise LckStopError(
                    "Cleanup STOP: remote Task branch diverges from merged PR head"
                )
            removed = self.resolver.runner.run(
                [
                    "git",
                    "push",
                    "origin",
                    "--delete",
                    branch,
                ],
                command_id="lck-closeout-remote-branch-delete",
            )
            if removed.returncode != 0:
                return _pending_receipt(
                    "cleanup_task_refs",
                    "pending",
                    reason="verified remote Task branch could not be deleted",
                )
            verified = self.resolver.runner.run(
                ["git", "ls-remote", "--heads", "origin", branch],
                command_id="lck-closeout-remote-branch-verify",
            )
            if verified.returncode != 0 or verified.stdout.strip():
                return _pending_receipt(
                    "cleanup_task_refs",
                    "pending",
                    reason="remote Task branch deletion postcondition is not proven",
                )
        return EffectReceipt(
            effect="cleanup_task_refs",
            action="cleaned",
            details={"branch": branch, "expected_head_sha": expected_head_sha},
        )


@dataclass(frozen=True)
class CloseoutResult:
    task_number: int
    status: str
    business_delivery: str
    cleanup: str
    effects: tuple[EffectReceipt, ...]
    operation_snapshot: OperationSnapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "closeout",
            "task_number": self.task_number,
            "status": self.status,
            "business_delivery": self.business_delivery,
            "cleanup": self.cleanup,
            "effects": [item.to_dict() for item in self.effects],
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "automatic_merge": False,
            "manual_issue_close": False,
        }


class CloseoutCompleter:
    """Resolve and converge post-merge state without trusting prior snapshots."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        main_effect: MainSynchronizationEffect | None = None,
        metadata_effect: CloseoutMetadataEffect | None = None,
        cleanup_effect: CleanupTaskRefsEffect | None = None,
        review_store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.main_effect = main_effect or MainSynchronizationEffect(resolver)
        self.metadata_effect = metadata_effect or CloseoutMetadataEffect(resolver)
        self.cleanup_effect = cleanup_effect or CleanupTaskRefsEffect(resolver)
        self.review_store = review_store or ReviewInvocationStore(resolver.repo_root)

    @staticmethod
    def _validate_merged_identity(state: LiveState) -> tuple[str, str]:
        pr = state.merged_pr
        if not isinstance(pr, Mapping):
            raise LckStopError("Closeout STOP: merged PR identity is unavailable")
        if str(pr.get("state", "")).upper() != "MERGED":
            raise LckStopError("Closeout STOP: merged PR state is not MERGED")
        number = pr.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise LckStopError("Closeout STOP: merged PR number is unavailable")
        if pr.get("baseRefName") != BASE_BRANCH:
            raise LckStopError("Closeout STOP: merged PR base branch is not main")
        if pr.get("headRefName") != state.target_branch:
            raise LckStopError(
                "Closeout STOP: merged PR head branch is not the resolved Task branch"
            )
        expected_head = _pr_head_sha(pr)
        base_sha = _pr_base_sha(pr)
        merge_sha = _merge_commit_sha(pr)
        merged_at = pr.get("mergedAt", pr.get("merged_at"))
        if (
            expected_head is None
            or base_sha is None
            or merge_sha is None
            or not isinstance(merged_at, str)
            or not merged_at
        ):
            raise LckStopError("Closeout STOP: merged PR identity is incomplete")
        closing = pr.get("closingIssuesReferences")
        if not isinstance(closing, list):
            raise LckStopError(
                "Closeout STOP: merged PR closing-Task linkage is unavailable"
            )
        closing_numbers = [
            item.get("number")
            for item in closing
            if isinstance(item, Mapping) and isinstance(item.get("number"), int)
        ]
        if len(closing_numbers) != 1 or closing_numbers[0] != state.task_number:
            raise LckStopError(
                "Closeout STOP: merged PR does not close exactly this Task"
            )
        if state.issue_state == "CLOSED":
            closure = (
                state.issue.get("issue_closure")
                if isinstance(state.issue, Mapping)
                else None
            )
            if (
                not isinstance(closure, Mapping)
                or closure.get("evidence_status") != "complete"
                or closure.get("status") != "closed-by-pr"
                or closure.get("closer_repository") != state.repository
                or closure.get("closer_number") != number
            ):
                raise LckStopError(
                    "Closeout STOP: closed Task is not proven closed by the merged PR"
                )
        if state.local_task_head is not None and state.local_task_head != expected_head:
            raise LckStopError(
                "Closeout STOP: local Task branch diverges from merged PR head"
            )
        if state.remote_task_oid is not None and state.remote_task_oid != expected_head:
            raise LckStopError(
                "Closeout STOP: remote Task branch diverges from merged PR head"
            )
        return expected_head, merge_sha

    def _validate_reviewed_identity(
        self, state: LiveState, merged_pr: Mapping[str, Any]
    ) -> None:
        latest = self.review_store.read_latest_review(state.task_number)
        if not isinstance(latest, Mapping) or latest.get("verdict") != "PASS":
            raise LckStopError(
                "Closeout STOP: latest Independent Review PASS is unavailable"
            )
        review_id = latest.get("review_id")
        if not isinstance(review_id, str):
            raise LckStopError("Closeout STOP: latest Review PASS has no review id")
        record = self.review_store.read_record(state.task_number, review_id)
        if (
            record.get("task_number") != state.task_number
            or record.get("review_id") != review_id
            or record.get("verdict") != "PASS"
            or record.get("status") != "READY_FOR_MERGE_PREFLIGHT"
        ):
            raise LckStopError("Closeout STOP: latest Review PASS record is invalid")
        raw_identity = record.get("identity")
        if not isinstance(raw_identity, Mapping):
            raise LckStopError("Closeout STOP: Review PASS identity is unavailable")
        reviewed = _identity_from_mapping(raw_identity)
        if (
            reviewed.task_number != state.task_number
            or reviewed.pr_number != merged_pr.get("number")
            or reviewed.base_sha != _pr_base_sha(merged_pr)
            or reviewed.head_sha != _pr_head_sha(merged_pr)
        ):
            raise LckStopError(
                "Closeout STOP: merged PR/head does not match the latest Review PASS"
            )

    def complete(self, task_number: int) -> CloseoutResult:
        snapshot = self.snapshots.acquire(task_number, operation="closeout")
        state = snapshot.state
        decision = self.eligibility.resolve(state, Phase.CLOSEOUT)
        if not decision.eligible:
            raise LckStopError(
                f"Closeout STOP for Task #{task_number}: " + "; ".join(decision.reasons)
            )
        expected_head, merge_sha = self._validate_merged_identity(state)
        if not isinstance(state.merged_pr, Mapping):
            raise LckStopError("Closeout STOP: merged PR identity is unavailable")
        self._validate_reviewed_identity(state, state.merged_pr)
        effects: list[EffectReceipt] = []
        main = self.main_effect.execute(state, merge_sha=merge_sha)
        effects.append(main)

        metadata = self.metadata_effect.execute(state)
        effects.append(metadata)

        if main.action in {"synchronized", "already-synced"}:
            cleanup = self.cleanup_effect.execute(
                state,
                expected_head_sha=expected_head,
                merge_sha=merge_sha,
            )
        else:
            cleanup = _pending_receipt(
                "cleanup_task_refs",
                "pending",
                reason="main synchronization is incomplete",
            )
        effects.append(cleanup)
        cleanup_complete = (
            main.action in {"synchronized", "already-synced"}
            and metadata.action in {"updated", "already-converged"}
            and cleanup.action in {"cleaned", "already-clean"}
        )
        return CloseoutResult(
            task_number=task_number,
            status="BUSINESS_DELIVERY_COMPLETE",
            business_delivery="COMPLETE",
            cleanup="COMPLETE" if cleanup_complete else "PENDING",
            effects=tuple(effects),
            operation_snapshot=snapshot,
        )


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
    operation_snapshot: OperationSnapshot

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
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "human_boundary": "Independent Review must be started separately",
        }


class DeliveryCompleter:
    """Complete Delivery from one immutable operation-start snapshot.

    Lifecycle authority is acquired once.  Local/remote/PR/metadata mutations
    then prove only their own exact postconditions.  If asynchronous PR checks
    are pending, the operation stops after the durable commit/push/PR effects;
    a later invocation acquires a fresh snapshot and continues idempotently.
    """

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        formal_validation: FormalValidationGate | None = None,
        commit_effect: CommitCurrentTreeEffect | None = None,
        remote_effect: EnsureRemoteBranchEffect | None = None,
        pr_effect: EnsureOpenPrEffect | ReuseExistingOpenPrEffect | None = None,
        status_effect: SetReviewStatusEffect | None = None,
        checks_gate: DeliveryChecksGate | None = None,
        require_existing_open_pr: bool = False,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.formal_validation = formal_validation or FormalValidationGate(resolver)
        self.commit_effect = commit_effect or CommitCurrentTreeEffect(resolver)
        self.remote_effect = remote_effect or EnsureRemoteBranchEffect(resolver)
        self.pr_effect = pr_effect or EnsureOpenPrEffect(resolver)
        self.status_effect = status_effect or SetReviewStatusEffect(resolver)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)
        self.require_existing_open_pr = require_existing_open_pr

    def _run_critical_outcome(self, state: LiveState) -> dict[str, Any]:
        issue = state.issue
        contract_snapshot = (
            issue.get("critical_outcome") if isinstance(issue, Mapping) else None
        )
        try:
            contract = contract_from_snapshot(contract_snapshot)
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
        raise LckStopError("unable to verify Task diff against operation base")

    def _verify_local_completion(self, branch: str, head_sha: str) -> None:
        branch_result = self.resolver.runner.run(
            ["git", "branch", "--show-current"],
            command_id="lck-delivery-final-branch",
        )
        head_result = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-delivery-final-head",
        )
        status = self.resolver.runner.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            command_id="lck-delivery-final-status",
        )
        if (
            branch_result.returncode != 0
            or branch_result.stdout.strip() != branch
            or head_result.returncode != 0
            or head_result.stdout.strip() != head_sha
            or status.returncode != 0
            or status.stdout.strip()
        ):
            raise LckStopError(
                "Delivery local postcondition failed: branch/head/worktree is not "
                "the validated operation result"
            )

    @staticmethod
    def _receipt_pr_identity(receipt: EffectReceipt) -> dict[str, Any]:
        return {
            "number": receipt.details.get("number"),
            "head_sha": receipt.details.get("head_sha"),
            "base_sha": receipt.details.get("base_sha"),
        }

    def _complete_from_snapshot(
        self,
        snapshot: OperationSnapshot,
        *,
        phase: Phase,
        commit_message: str,
        summary: str,
        risks: str,
    ) -> DeliveryCompletionResult:
        state = snapshot.state
        task_number = state.task_number
        required_checks = snapshot.required_checks
        _required_check_contract_for_snapshot(snapshot)
        required_checks_mapping = cast(Mapping[str, Any], required_checks)
        decision = self.eligibility.resolve(state, phase)
        if not decision.eligible:
            raise LckStopError(
                f"{phase.value} STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )
        base_sha = _authoritative_remote_main_sha(state.git)
        if not is_sha(base_sha):
            raise LckStopError("current remote main identity is unavailable")
        issue = state.issue
        body_sha256 = issue.get("body_sha256") if isinstance(issue, Mapping) else None
        if not isinstance(body_sha256, str) or not body_sha256:
            raise LckStopError("current Task body identity is unavailable")
        branch = state.target_branch
        if self.require_existing_open_pr:
            pr = state.open_pr
            if (
                not isinstance(pr, Mapping)
                or pr.get("isDraft") is not False
                or pr.get("baseRefOid") != base_sha
                or pr.get("headRefName") != branch
            ):
                raise LckStopError(
                    "Remediation requires the snapshot's existing OPEN PR on the "
                    "resolved Task branch/base"
                )

        effects: list[EffectReceipt] = []
        if state.git.get("clean") is True:
            if not self._has_task_diff(base_sha):
                raise LckStopError(
                    "Delivery Complete found no Task diff against operation base"
                )
            validated_tree = self.commit_effect.current_head_tree()
            critical = self._run_critical_outcome(state)
            validation = self.formal_validation.run(base_sha)
            validated_head = state.local_task_head
            if not is_sha(validated_head):
                raise LckStopError("current Task head is unavailable")
            self.commit_effect.verify_tree_unchanged(
                validated_tree,
                expected_head_sha=validated_head,
            )
            head = validated_head
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
            self.commit_effect.verify_tree_unchanged(
                validated_tree,
                expected_head_sha=state.local_task_head,
            )
            commit = self.commit_effect.execute(
                validated_tree,
                commit_message,
                expected_parent_sha=state.local_task_head,
            )
            effects.append(commit)
            head = commit.details.get("head_sha")
            if not is_sha(head):
                raise LckStopError("commit receipt did not contain a valid head SHA")

        remote = self.remote_effect.execute(branch, expected_head_sha=str(head))
        effects.append(remote)
        if remote.details.get("remote_oid") != head:
            raise LckStopError("remote branch effect did not prove the validated head")

        pr_receipt = self.pr_effect.execute(
            state,
            head_sha=str(head),
            summary=summary,
            risks=risks,
            critical_outcome=critical,
            validation=validation,
            expected_base_sha=base_sha,
            expected_body_sha256=body_sha256,
        )
        effects.append(pr_receipt)
        pr_identity = self._receipt_pr_identity(pr_receipt)
        pr_number = pr_identity.get("number")
        if (
            not isinstance(pr_number, int)
            or pr_identity.get("head_sha") != head
            or pr_identity.get("base_sha") != base_sha
        ):
            raise LckStopError("PR effect did not prove the validated head/base")

        snapshot_pr = state.open_pr
        if (
            isinstance(snapshot_pr, Mapping)
            and snapshot_pr.get("number") == pr_number
            and snapshot_pr.get("headRefOid") == head
            and snapshot_pr.get("baseRefOid") == base_sha
        ):
            checks = self.checks_gate.evaluate(snapshot)
        else:
            if not isinstance(state.repository, str):
                raise LckStopError("repository identity is unavailable for PR checks")
            checks = self.checks_gate.query_exact_pr(
                state.repository,
                pr_number,
                expected_head_sha=str(head),
                expected_base_sha=base_sha,
                required_checks=required_checks_mapping,
            )

        checks_pr = checks.get("pr")
        if not isinstance(checks_pr, Mapping):
            raise LckStopError("checks result did not contain PR identity")
        status_receipt = self.status_effect.execute(
            state,
            expected_pr=checks_pr,
            checks_result=checks,
        )
        effects.append(status_receipt)
        if status_receipt.action not in {"updated", "already-review"}:
            raise LckStopError("Project Status effect did not reach Review")

        self._verify_local_completion(branch, str(head))
        return DeliveryCompletionResult(
            task_number=task_number,
            status="READY_FOR_REVIEW",
            branch=branch,
            head_sha=str(head),
            critical_outcome=critical,
            validation=validation,
            checks=checks,
            effects=tuple(effects),
            operation_snapshot=snapshot,
        )

    def complete(
        self,
        task_number: int,
        *,
        commit_message: str,
        summary: str,
        risks: str = "",
        operation_snapshot: OperationSnapshot | None = None,
        phase: Phase = Phase.DELIVERY_COMPLETE,
    ) -> DeliveryCompletionResult:
        if not summary.strip():
            raise LckStopError("Delivery summary must be non-empty")
        snapshot = operation_snapshot or self.snapshots.acquire(
            task_number,
            operation=phase.value,
            include_required_checks=True,
        )
        if snapshot.state.task_number != task_number:
            raise LckStopError("operation snapshot belongs to another Task")
        return self._complete_from_snapshot(
            snapshot,
            phase=phase,
            commit_message=commit_message,
            summary=summary,
            risks=risks,
        )


@dataclass(frozen=True)
class RemediationContext:
    task_number: int
    review_id: str
    findings: str
    findings_source: str
    operation_snapshot: OperationSnapshot
    action: str

    @property
    def state(self) -> LiveState:
        return self.operation_snapshot.state

    def to_dict(self) -> dict[str, Any]:
        pr = self.state.open_pr or {}
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "remediation-prepare",
            "status": "READY_FOR_REMEDIATION",
            "task_number": self.task_number,
            "review_id": self.review_id,
            "action": self.action,
            "findings": self.findings,
            "findings_source": self.findings_source,
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "live_target": {
                "pr_number": pr.get("number"),
                "base_sha": pr.get("baseRefOid"),
                "head_sha": pr.get("headRefOid"),
                "branch": self.state.target_branch,
                "task_body_sha256": (
                    self.state.issue.get("body_sha256")
                    if isinstance(self.state.issue, Mapping)
                    else None
                ),
            },
            "mechanical_authority": (
                "operation snapshot acquired at Remediation entry; Review findings are semantic "
                "input only, whether loaded from the local audit record or an explicitly supplied "
                "portable findings file"
            ),
            "acceptance_boundary": (
                "Requirements whose evidence can only be truthfully produced after the repaired "
                "candidate head exists, or by a separate provider/fresh Review invocation, do not "
                "block Remediation Complete. They remain unsatisfied Review-acceptance requirements "
                "and must not be fabricated or treated as satisfied by remediation."
            ),
        }


def _failed_review_record(
    store: ReviewInvocationStore, task_number: int, review_id: str
) -> dict[str, Any]:
    record = store.read_record(task_number, review_id)
    if record.get("task_number") != task_number or record.get("verdict") != "FAIL":
        raise LckStopError("Remediation requires a failed Independent Review record")
    latest = store.read_latest_review(task_number)
    if (
        not isinstance(latest, Mapping)
        or latest.get("review_id") != review_id
        or latest.get("verdict") != "FAIL"
    ):
        raise LckStopError(
            "Remediation requires the latest completed Independent Review to be this FAIL"
        )
    findings = record.get("findings")
    if not isinstance(findings, str) or not findings.strip():
        raise LckStopError(
            "failed Independent Review record has no remediation findings"
        )
    identity = record.get("identity")
    if not isinstance(identity, Mapping):
        raise LckStopError("failed Independent Review record has no reviewed identity")
    _identity_from_mapping(identity)
    return record


def _read_portable_review_findings(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LckStopError(f"cannot read portable Review findings file: {exc}") from exc
    if not text.strip():
        raise LckStopError("portable Review findings file is empty")
    return text


def _remediation_findings(
    store: ReviewInvocationStore,
    task_number: int,
    review_id: str,
    *,
    findings_file: Path | None = None,
) -> tuple[str, str]:
    # Constructing the path validates the caller-provided review id even when the
    # originating workspace-local audit record is unavailable.
    record_path = store.record_path(task_number, review_id)
    if record_path.exists():
        record = _failed_review_record(store, task_number, review_id)
        return cast(str, record["findings"]), "local-review-record"
    if findings_file is None:
        raise LckStopError(
            "failed Review audit record is unavailable in this workspace; "
            "for an explicit cross-workspace/provider Remediation handoff, provide "
            "--findings-file with the completed Review findings. The file is semantic "
            "input only; LCK still reacquires all mechanical authority from live state."
        )
    return _read_portable_review_findings(findings_file), "portable-findings-file"


class RemediationPreparer:
    """Explicitly enter repair using live mechanics plus semantic Review findings."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.store = store or ReviewInvocationStore(resolver.repo_root)

    def _run_git(self, args: Sequence[str], command_id: str) -> None:
        result = self.resolver.runner.run(["git", *args], command_id=command_id)
        if result.returncode != 0:
            raise LckStopError(
                f"{command_id} failed with exit code {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def _verify_local_review_head(self, branch: str, expected_head: str) -> None:
        current = self.resolver.runner.run(
            ["git", "branch", "--show-current"],
            command_id="lck-remediation-post-branch",
        )
        head = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-remediation-post-head",
        )
        if (
            current.returncode != 0
            or current.stdout.strip() != branch
            or head.returncode != 0
            or head.stdout.strip() != expected_head
        ):
            raise LckStopError(
                "Remediation Prepare postcondition failed: workspace is not on "
                "the operation snapshot PR head"
            )

    def prepare(
        self,
        task_number: int,
        review_id: str,
        *,
        findings_file: Path | None = None,
    ) -> RemediationContext:
        required = self.store.read_review_required(task_number)
        if required is not None:
            raise LckStopError(
                "Remediation STOP: a fresh Independent Review is required after the previous remediation"
            )
        findings, findings_source = _remediation_findings(
            self.store,
            task_number,
            review_id,
            findings_file=findings_file,
        )
        snapshot = self.snapshots.acquire(
            task_number,
            operation=Phase.REMEDIATION_PREPARE.value,
        )
        state = snapshot.state
        decision = self.eligibility.resolve(state, Phase.REMEDIATION_PREPARE)
        if not decision.eligible:
            raise LckStopError(
                f"Remediation Prepare STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )

        branch = state.target_branch
        current = state.git.get("branch")
        clean = state.git.get("clean") is True
        action = "already-prepared"
        if state.local_task_branch is None:
            if not clean:
                raise LckStopError(
                    "restoring Remediation workspace requires a clean current worktree"
                )
            if state.remote_task_branch != branch:
                raise LckStopError(
                    "current OPEN PR has no restorable remote Task branch"
                )
            self._run_git(
                ["switch", "-c", branch, "--track", f"origin/{branch}"],
                "lck-remediation-restore-branch",
            )
            action = "restored-remote-branch"
        elif current != branch:
            if not clean:
                raise LckStopError(
                    "cannot switch to Remediation branch with a dirty unrelated worktree"
                )
            self._run_git(["switch", branch], "lck-remediation-switch-branch")
            action = "selected-existing-branch"
        elif not clean:
            action = "resumed-dirty-remediation"

        pr_head = state.open_pr.get("headRefOid") if state.open_pr else None
        if not is_sha(pr_head):
            raise LckStopError("Remediation Prepare PR head is unavailable")
        self._verify_local_review_head(branch, pr_head)
        pr_number = state.open_pr.get("number") if state.open_pr else None
        pr_base = state.open_pr.get("baseRefOid") if state.open_pr else None
        if not isinstance(pr_number, int) or not is_sha(pr_base):
            raise LckStopError("Remediation Prepare PR identity is unavailable")
        self.store.write_remediation_session(
            task_number,
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "remediation-session",
                "task_number": task_number,
                "review_id": review_id,
                "start_head_sha": pr_head,
                "pr_number": pr_number,
                "base_sha": pr_base,
                "findings_sha256": hashlib.sha256(findings.encode("utf-8")).hexdigest(),
                "findings_source": findings_source,
                "authority": (
                    "operation-local continuity only; current mechanical target must be "
                    "reacquired by the Remediation terminal operation"
                ),
            },
        )
        return RemediationContext(
            task_number=task_number,
            review_id=review_id,
            findings=findings,
            findings_source=findings_source,
            operation_snapshot=snapshot,
            action=action,
        )


@dataclass(frozen=True)
class RemediationNoChangeResult:
    task_number: int
    review_id: str
    head_sha: str
    pr_number: int
    base_sha: str
    summary: str
    operation_snapshot: OperationSnapshot
    receipt_path: Path
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "remediation-no-change",
            "task_number": self.task_number,
            "review_id": self.review_id,
            "status": "NO_IMPLEMENTATION_CHANGE",
            "head_sha": self.head_sha,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "summary": self.summary,
            "candidate_changed": False,
            "fresh_review_required": False,
            "session_released": True,
            "replayed": self.replayed,
            "receipt_path": str(self.receipt_path),
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "acceptance_boundary": (
                "This receipt closes one prepared Remediation session without changing the "
                "candidate. It records a real no-change implementation-path terminal state, "
                "but it does not satisfy deferred provider/cross-runtime Review acceptance by itself."
            ),
            "human_boundary": (
                "STOP — continue external acceptance work on the unchanged head; no fresh Review "
                "is required solely because this no-change session was closed"
            ),
        }


class RemediationNoChangeCompleter:
    """Close one prepared Remediation session when semantic repair needs no tree change."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.store = store or ReviewInvocationStore(resolver.repo_root)

    @staticmethod
    def _session_identity(
        session: Mapping[str, Any],
    ) -> tuple[str, int, str]:
        head_sha = session.get("start_head_sha")
        pr_number = session.get("pr_number")
        base_sha = session.get("base_sha")
        if (
            not is_sha(head_sha)
            or not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or not is_sha(base_sha)
        ):
            raise LckStopError("Remediation session identity is incomplete")
        return cast(str, head_sha), pr_number, cast(str, base_sha)

    @staticmethod
    def _verify_current_target(
        snapshot: OperationSnapshot,
        *,
        head_sha: str,
        pr_number: int,
        base_sha: str,
    ) -> None:
        state = snapshot.state
        pr = state.open_pr or {}
        if (
            pr.get("number") != pr_number
            or pr.get("headRefOid") != head_sha
            or pr.get("baseRefOid") != base_sha
            or state.local_task_head != head_sha
            or state.remote_task_oid != head_sha
        ):
            raise LckStopError(
                "Remediation No Change target no longer matches the prepared session"
            )
        if state.git.get("clean") is not True:
            raise LckStopError(
                "Remediation No Change requires a clean tracked and staged worktree"
            )

    def complete(
        self,
        task_number: int,
        review_id: str,
        *,
        summary: str,
    ) -> RemediationNoChangeResult:
        if self.store.read_review_required(task_number) is not None:
            raise LckStopError(
                "Remediation STOP: a fresh Independent Review is required after the previous remediation"
            )

        session = self.store.read_remediation_session(task_number)
        if session is None:
            prior = self.store.read_remediation_no_change_receipt(
                task_number, review_id
            )
            if prior is None:
                raise LckStopError(
                    "Remediation No Change requires a prepared Remediation session"
                )
            head_sha, pr_number, base_sha = self._session_identity(prior)
            snapshot = self.snapshots.acquire(
                task_number, operation=Phase.REMEDIATION_NO_CHANGE.value
            )
            decision = self.eligibility.resolve(
                snapshot.state, Phase.REMEDIATION_NO_CHANGE
            )
            if not decision.eligible:
                raise LckStopError(
                    f"Remediation No Change STOP for Task #{task_number}: "
                    + "; ".join(decision.reasons)
                )
            self._verify_current_target(
                snapshot,
                head_sha=head_sha,
                pr_number=pr_number,
                base_sha=base_sha,
            )
            return RemediationNoChangeResult(
                task_number=task_number,
                review_id=review_id,
                head_sha=head_sha,
                pr_number=pr_number,
                base_sha=base_sha,
                summary=str(prior.get("summary") or ""),
                operation_snapshot=snapshot,
                receipt_path=self.store.remediation_no_change_receipt_path(
                    task_number, review_id
                ),
                replayed=True,
            )

        if session.get("review_id") != review_id:
            raise LckStopError(
                "Remediation No Change review id does not match the prepared Remediation session"
            )
        head_sha, pr_number, base_sha = self._session_identity(session)
        snapshot = self.snapshots.acquire(
            task_number, operation=Phase.REMEDIATION_NO_CHANGE.value
        )
        decision = self.eligibility.resolve(snapshot.state, Phase.REMEDIATION_NO_CHANGE)
        if not decision.eligible:
            raise LckStopError(
                f"Remediation No Change STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )
        self._verify_current_target(
            snapshot,
            head_sha=head_sha,
            pr_number=pr_number,
            base_sha=base_sha,
        )

        receipt = {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "remediation-no-change-receipt",
            "task_number": task_number,
            "review_id": review_id,
            "start_head_sha": head_sha,
            "pr_number": pr_number,
            "base_sha": base_sha,
            "findings_sha256": session.get("findings_sha256"),
            "findings_source": session.get("findings_source"),
            "summary": summary,
            "candidate_changed": False,
            "fresh_review_required": False,
            "authority": (
                "formal no-change Remediation terminal receipt only; current mechanical "
                "target was reacquired at completion"
            ),
        }
        receipt_path = self.store.write_remediation_no_change_receipt(
            task_number, review_id, receipt
        )
        self.store.clear_remediation_session(task_number)
        return RemediationNoChangeResult(
            task_number=task_number,
            review_id=review_id,
            head_sha=head_sha,
            pr_number=pr_number,
            base_sha=base_sha,
            summary=summary,
            operation_snapshot=snapshot,
            receipt_path=receipt_path,
        )


@dataclass(frozen=True)
class RemediationCompletionResult:
    task_number: int
    review_id: str
    delivery: DeliveryCompletionResult

    def to_dict(self) -> dict[str, Any]:
        payload = self.delivery.to_dict()
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "remediation-complete",
            "task_number": self.task_number,
            "review_id": self.review_id,
            "status": "READY_FOR_NEW_REVIEW",
            "head_sha": self.delivery.head_sha,
            "critical_outcome": payload["critical_outcome"],
            "validation": payload["validation"],
            "checks": payload["checks"],
            "effects": payload["effects"],
            "operation_snapshot": payload["operation_snapshot"],
            "human_boundary": (
                "STOP — a new Independent Review must be started explicitly in a fresh invocation"
            ),
            "deferred_review_acceptance": (
                "Any requirement whose evidence depends on this new head or on a separate provider/"
                "fresh Review remains pending for the next Independent Review. READY_FOR_NEW_REVIEW "
                "does not claim that such evidence is satisfied."
            ),
            "automatic_review": False,
        }


class RemediationCompleter:
    """Reuse Task-2 Delivery effects for one explicitly started repair."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        store: ReviewInvocationStore | None = None,
        checks_gate: DeliveryChecksGate | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)

    def complete(
        self,
        task_number: int,
        review_id: str,
        *,
        commit_message: str,
        summary: str,
        risks: str = "",
    ) -> RemediationCompletionResult:
        if self.store.read_review_required(task_number) is not None:
            raise LckStopError(
                "Remediation STOP: a fresh Independent Review is required after the previous remediation"
            )
        session = self.store.read_remediation_session(task_number)
        start_head: str | None = None
        if session is not None:
            if session.get("review_id") != review_id:
                raise LckStopError(
                    "Remediation Complete review id does not match the prepared Remediation session"
                )
            candidate = session.get("start_head_sha")
            if not is_sha(candidate):
                raise LckStopError("Remediation session start head is unavailable")
            start_head = cast(str, candidate)
        else:
            # Backward-compatible path for an already-prepared Codex workspace from
            # before remediation sessions were introduced. Existing local Review
            # records retain their previous behavior.
            record = _failed_review_record(self.store, task_number, review_id)
            reviewed_identity = _identity_from_mapping(
                cast(Mapping[str, Any], record["identity"])
            )
            start_head = reviewed_identity.head_sha
        snapshot = self.snapshots.acquire(
            task_number,
            operation=Phase.REMEDIATION_COMPLETE.value,
            include_required_checks=True,
        )
        state = snapshot.state
        decision = self.eligibility.resolve(state, Phase.REMEDIATION_COMPLETE)
        if not decision.eligible:
            raise LckStopError(
                f"Remediation Complete STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )
        if state.git.get("clean") is True and state.local_task_head == start_head:
            raise LckStopError(
                "Remediation Complete requires a repaired head or uncommitted repair changes"
            )

        # Initial Delivery already owns the bounded validate/commit/push/check
        # mechanics.  Remediation deliberately reuses those effects while
        # replacing PR create/resolve with a strict existing-PR postcondition.
        delivery = DeliveryCompleter(
            self.resolver,
            pr_effect=cast(Any, ReuseExistingOpenPrEffect(self.resolver)),
            checks_gate=self.checks_gate,
            require_existing_open_pr=True,
        ).complete(
            task_number,
            commit_message=commit_message,
            summary=summary,
            risks=risks,
            operation_snapshot=snapshot,
            phase=Phase.REMEDIATION_COMPLETE,
        )
        if delivery.head_sha == start_head:
            raise LckStopError(
                "Remediation Complete did not produce a new head; fresh Review boundary cannot advance"
            )
        self.store.write_review_required(task_number, review_id, delivery.head_sha)
        self.store.clear_remediation_session(task_number)
        return RemediationCompletionResult(
            task_number=task_number,
            review_id=review_id,
            delivery=delivery,
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

    review = commands.add_parser("review")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_prepare = review_commands.add_parser("prepare")
    review_prepare.add_argument("task", type=int)
    review_complete = review_commands.add_parser("complete")
    review_complete.add_argument("task", type=int)
    review_complete.add_argument("--review-id", required=True)
    review_complete.add_argument("--verdict", required=True, choices=("PASS", "FAIL"))
    review_complete.add_argument("--findings-file", type=Path)

    remediation = commands.add_parser("remediation")
    remediation_commands = remediation.add_subparsers(
        dest="remediation_command", required=True
    )
    remediation_prepare = remediation_commands.add_parser("prepare")
    remediation_prepare.add_argument("task", type=int)
    remediation_prepare.add_argument("--review-id", required=True)
    remediation_prepare.add_argument("--findings-file", type=Path)
    remediation_no_change = remediation_commands.add_parser("no-change")
    remediation_no_change.add_argument("task", type=int)
    remediation_no_change.add_argument("--review-id", required=True)
    remediation_no_change.add_argument("--summary", required=True)
    remediation_complete = remediation_commands.add_parser("complete")
    remediation_complete.add_argument("task", type=int)
    remediation_complete.add_argument("--review-id", required=True)
    remediation_complete.add_argument("--commit-message", required=True)
    remediation_complete.add_argument("--summary", required=True)
    remediation_complete.add_argument("--risks", default="")

    merge = commands.add_parser("merge")
    merge_commands = merge.add_subparsers(dest="merge_command", required=True)
    merge_preflight = merge_commands.add_parser("preflight")
    merge_preflight.add_argument("task", type=int)

    merge_preflight_alias = commands.add_parser("merge-preflight")
    merge_preflight_alias.add_argument("task", type=int)

    closeout = commands.add_parser("closeout")
    closeout.add_argument("task", type=int)
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
        if args.command == "delivery" and args.delivery_command == "prepare":
            context = DeliveryPreparer(resolver).prepare(args.task)
            print_json(context.to_dict(), pretty=True)
            return 0
        if args.command == "delivery" and args.delivery_command == "complete":
            result = DeliveryCompleter(resolver).complete(
                args.task,
                commit_message=args.commit_message,
                summary=args.summary,
                risks=args.risks,
            )
            print_json(result.to_dict(), pretty=True)
            return 0
        if args.command == "review" and args.review_command == "prepare":
            context = ReviewPreparer(resolver).prepare(args.task)
            print_json(context.to_dict(), pretty=True)
            return 0
        if args.command == "review" and args.review_command == "complete":
            result = ReviewCompleter(resolver).complete(
                args.task,
                args.review_id,
                verdict=args.verdict,
                findings_file=args.findings_file,
            )
            print_json(result.to_dict(), pretty=True)
            return 0
        if args.command == "remediation" and args.remediation_command == "prepare":
            context = RemediationPreparer(resolver).prepare(
                args.task,
                args.review_id,
                findings_file=args.findings_file,
            )
            print_json(context.to_dict(), pretty=True)
            return 0
        if args.command == "remediation" and args.remediation_command == "no-change":
            result = RemediationNoChangeCompleter(resolver).complete(
                args.task,
                args.review_id,
                summary=args.summary,
            )
            print_json(result.to_dict(), pretty=True)
            return 0
        if args.command == "remediation" and args.remediation_command == "complete":
            result = RemediationCompleter(resolver).complete(
                args.task,
                args.review_id,
                commit_message=args.commit_message,
                summary=args.summary,
                risks=args.risks,
            )
            print_json(result.to_dict(), pretty=True)
            return 0
        if (
            args.command == "merge" and args.merge_command == "preflight"
        ) or args.command == "merge-preflight":
            result = MergePreflight(resolver).run(args.task)
            print_json(result.to_dict(), pretty=True)
            return 0
        if args.command == "closeout":
            result = CloseoutCompleter(resolver).complete(args.task)
            print_json(result.to_dict(), pretty=True)
            return 0
        raise LckStopError("unsupported LCK command")
    except ReviewStaleError as exc:
        print(
            json.dumps(
                {"status": "stale", "code": exc.code, "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 3
    except WorkflowToolError as exc:
        print(json.dumps({"status": "stop", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
