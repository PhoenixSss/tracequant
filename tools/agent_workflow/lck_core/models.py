from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, cast

from workflow_common import WorkflowToolError, is_sha

from .issue_profiles import (
    TASK_PROFILE,
    LeafIssueWorkflowProfile,
    canonical_branch_for_profile,
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


@dataclass(frozen=True)
class FactProfile:
    """Stable bounded acquisition contract for one lifecycle operation."""

    name: str
    include_comments: bool = False
    include_issue_closure: bool = False
    include_task_contract: bool = True
    include_git: bool = True
    include_workspace_inventory: bool = False
    include_local_task_branches: bool = True
    include_remote_task_branches: bool = True
    include_open_pr: bool = True
    include_pr_history: bool = False
    include_pr_history_details: bool = False
    include_checks: bool = False
    include_mergeability: bool = False

    def facts(self) -> tuple[str, ...]:
        enabled = []
        for name in (
            "comments",
            "issue_closure",
            "task_contract",
            "git",
            "workspace_inventory",
            "local_task_branches",
            "remote_task_branches",
            "open_pr",
            "pr_history",
            "checks",
            "mergeability",
        ):
            if getattr(self, f"include_{name}"):
                enabled.append(name)
        return tuple(enabled)


_DIAGNOSTIC_FACT_PROFILE: Final = FactProfile(
    name="diagnostic",
    include_comments=True,
    include_issue_closure=True,
    include_task_contract=True,
    include_git=True,
    include_workspace_inventory=True,
    include_local_task_branches=True,
    include_remote_task_branches=True,
    include_open_pr=True,
    include_pr_history=True,
    include_pr_history_details=True,
    include_checks=True,
    include_mergeability=True,
)

_OPERATION_FACT_PROFILES: Final = {
    "delivery-prepare": FactProfile(
        name="delivery-prepare",
        include_pr_history=True,
    ),
    "delivery-complete": FactProfile(
        name="delivery-complete",
        include_pr_history=True,
        include_checks=True,
    ),
    "review-prepare": FactProfile(
        name="review-prepare",
        include_git=False,
        include_local_task_branches=False,
        include_checks=True,
    ),
    "review-complete": FactProfile(
        name="review-complete",
        include_git=False,
        include_local_task_branches=False,
        include_checks=True,
    ),
    # All phases that run eligibility need the current Issue contract so
    # Documentation remains valid through its terminal lifecycle paths.
    "remediation-prepare": FactProfile(
        name="remediation-prepare",
        include_task_contract=True,
    ),
    "remediation-no-change": FactProfile(
        name="remediation-no-change",
        include_task_contract=True,
    ),
    "remediation-complete": FactProfile(
        name="remediation-complete",
        include_checks=True,
    ),
    "merge-preflight": FactProfile(
        name="merge-preflight",
        include_git=False,
        include_local_task_branches=False,
        include_checks=True,
        include_mergeability=True,
    ),
    "closeout": FactProfile(
        name="closeout",
        include_issue_closure=True,
        include_task_contract=True,
        include_pr_history=True,
        include_pr_history_details=True,
    ),
}


def _operation_key(operation: str) -> str:
    return operation.strip().casefold().replace(" ", "-").replace("_", "-")


def fact_profile_for_operation(operation: str) -> FactProfile:
    key = _operation_key(operation)
    try:
        return _OPERATION_FACT_PROFILES[key]
    except KeyError as exc:
        raise LckStopError(
            f"unsupported authoritative operation profile: {operation}"
        ) from exc


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
    return canonical_branch_for_profile(TASK_PROFILE, task_number, title)


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


def _pr_agent_view(value: Any) -> dict[str, Any] | None:
    """Keep only the identity needed to reason about a current PR."""
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for output, candidates in (
        ("number", ("number",)),
        ("url", ("url",)),
        ("state", ("state",)),
        ("is_draft", ("isDraft", "is_draft")),
        ("base_branch", ("baseRefName", "base_branch")),
        ("base_sha", ("baseRefOid", "base_sha")),
        ("head_branch", ("headRefName", "head_branch")),
        ("head_sha", ("headRefOid", "head_sha")),
    ):
        for candidate in candidates:
            if candidate in value and value[candidate] is not None:
                result[output] = value[candidate]
                break
    return result or None


def _checks_agent_view(value: Any) -> dict[str, Any]:
    """Summarize checks without copying observed runs or command diagnostics."""
    if not isinstance(value, Mapping):
        return {"status": "unknown"}
    result: dict[str, Any] = {}
    for key in ("status", "check_state", "configuration", "limitation"):
        item = value.get(key)
        if item is not None:
            result[key] = item
    pr = _pr_agent_view(value.get("pr"))
    if pr is not None:
        result["pr"] = pr
    required = value.get("required")
    if isinstance(required, list):
        result["required_count"] = len(required)
    observed = value.get("observed")
    if isinstance(observed, Mapping):
        result["observed_count"] = len(observed)
    for key in ("failed", "pending", "success", "skipped_or_unknown", "all_success"):
        item = value.get(key)
        if item is not None:
            result[key] = item
    return result


def _validation_agent_view(value: Any) -> dict[str, Any]:
    """Summarize validation identity and outcome, leaving diagnostics in Receipt."""
    if not isinstance(value, Mapping):
        return {"status": "unknown"}
    result: dict[str, Any] = {}
    for key in (
        "status",
        "validated_base_sha",
        "validated_head_sha",
        "profile",
        "profile_version",
    ):
        item = value.get(key)
        if item is not None:
            result[key] = item
    commands = value.get("commands")
    if isinstance(commands, list):
        result["command_count"] = len(commands)
        failed = next(
            (
                item
                for item in commands
                if isinstance(item, Mapping) and item.get("status") == "fail"
            ),
            None,
        )
        if isinstance(failed, Mapping):
            result["failed_command"] = {
                key: failed[key] for key in ("command_id", "exit_code") if key in failed
            }
    return result


def _critical_outcome_agent_view(value: Any) -> dict[str, Any] | None:
    """Summarize the formal Critical Outcome gate."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return {"status": "unknown"}
    result: dict[str, Any] = {}
    for key in ("status", "verification_test", "exit_code"):
        item = value.get(key)
        if item is not None:
            result[key] = item
    contract = value.get("contract")
    if isinstance(contract, Mapping):
        verification_test = contract.get("verification_test")
        if verification_test is not None:
            result.setdefault("verification_test", verification_test)
    return result


def _issue_agent_view(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in (
        "number",
        "title",
        "url",
        "state",
        "project_status",
        "research_outcome",
    ):
        item = value.get(key)
        if item is not None:
            result[key] = item
    return result or None


def _branch_matches_task(branch: str, task_number: int) -> bool:
    match = TASK_BRANCH_PATTERN.fullmatch(branch)
    if match is None:
        return False
    number = next(
        (value for value in match.groups() if value is not None),
        None,
    )
    return number == str(task_number)


def branch_matches_profile(
    branch: str,
    issue_number: int,
    profile: LeafIssueWorkflowProfile,
) -> bool:
    """Return whether a branch belongs to the profile-owned Issue namespace.

    Task keeps its historical aliases for compatibility.  Other enabled leaf
    profiles use only their canonical namespace, so a Documentation branch can
    never be mistaken for a Task branch (or vice versa).
    """

    if profile is TASK_PROFILE:
        return _branch_matches_task(branch, issue_number)
    prefix = f"{profile.branch_namespace}{issue_number}-"
    return bool(re.fullmatch(re.escape(prefix) + r"[a-z0-9][a-z0-9-]*", branch))


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
    """Current mechanical facts acquired from Git and GitHub for one Issue."""

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
    issue_profile: Mapping[str, Any] | None = None

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
                            "documentation_contract": self.task_contract.get(
                                "documentation_contract"
                            ),
                            "research_contract": self.task_contract.get(
                                "research_contract"
                            ),
                            "research_outcome": self.task_contract.get(
                                "research_outcome"
                            ),
                        }
                        if isinstance(self.task_contract, Mapping)
                        else None
                    ),
                    "issue_profile": self.issue_profile,
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

    def agent_view(self) -> dict[str, Any]:
        """Return the compact result intended for the invoking Agent."""
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "status",
            "task_number": self.task_number,
            "repository": self.repository,
            "status": self.status,
            "issue": _issue_agent_view(self.issue),
            "issue_profile": self.issue_profile,
            "target_branch": self.target_branch,
            "task_branch": {
                "local": self.local_task_branch,
                "local_head_sha": self.local_task_head,
                "remote": self.remote_task_branch,
                "remote_head_sha": self.remote_task_oid,
            },
            "pr": _pr_agent_view(self.open_pr or self.merged_pr),
            "checks": _checks_agent_view(self.checks),
            "cleanup": _jsonable(self.cleanup),
            "stop_reasons": list(self.stop_reasons),
            "next_action": (
                "inspect receipt and resolve STOP reasons"
                if self.status is ResolutionStatus.STOP
                else "use the phase-specific LCK operation for the next lifecycle action"
            ),
        }


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
    fact_profile: str = "diagnostic"
    acquired_facts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": self.operation,
            "state": self.state.to_dict(),
            "required_checks": _jsonable(self.required_checks),
            "acquisition_warnings": _jsonable(self.acquisition_warnings),
            "fact_profile": self.fact_profile,
            "acquired_facts": list(self.acquired_facts),
        }


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
