from __future__ import annotations

import inspect
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
    include_leaf_contract: bool = True
    include_git: bool = True
    include_workspace_inventory: bool = False
    include_local_issue_branches: bool = True
    include_remote_issue_branches: bool = True
    include_open_pr: bool = True
    include_pr_history: bool = False
    include_pr_history_details: bool = False
    include_checks: bool = False
    include_mergeability: bool = False

    # Stable compatibility spellings for persisted fact-profile consumers.
    @property
    def include_task_contract(self) -> bool:
        return self.include_leaf_contract

    @property
    def include_local_task_branches(self) -> bool:
        return self.include_local_issue_branches

    @property
    def include_remote_task_branches(self) -> bool:
        return self.include_remote_issue_branches

    def facts(self) -> tuple[str, ...]:
        """Return the stable legacy fact names used by persisted snapshots."""
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

    def neutral_facts(self) -> tuple[str, ...]:
        """Return the canonical neutral names for new in-memory consumers."""
        enabled = []
        for name in (
            "comments",
            "issue_closure",
            "leaf_contract",
            "git",
            "workspace_inventory",
            "local_issue_branches",
            "remote_issue_branches",
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
    include_leaf_contract=True,
    include_git=True,
    include_workspace_inventory=True,
    include_local_issue_branches=True,
    include_remote_issue_branches=True,
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
        include_local_issue_branches=False,
        include_checks=True,
    ),
    "review-complete": FactProfile(
        name="review-complete",
        include_git=False,
        include_local_issue_branches=False,
        include_checks=True,
    ),
    # All phases that run eligibility need the current Issue contract so
    # Documentation remains valid through its terminal lifecycle paths.
    "remediation-prepare": FactProfile(
        name="remediation-prepare",
        include_leaf_contract=True,
    ),
    "remediation-no-change": FactProfile(
        name="remediation-no-change",
        include_leaf_contract=True,
    ),
    "remediation-complete": FactProfile(
        name="remediation-complete",
        include_checks=True,
    ),
    "merge-preflight": FactProfile(
        name="merge-preflight",
        include_git=False,
        include_local_issue_branches=False,
        include_checks=True,
        include_mergeability=True,
    ),
    "closeout": FactProfile(
        name="closeout",
        include_issue_closure=True,
        include_leaf_contract=True,
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
        "bug_contract",
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

    if profile.allow_legacy_branch_aliases:
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


def _called_from_dataclasses_replace() -> bool:
    """Recognize Python's reconstruction path for legacy alias updates."""
    frame = inspect.currentframe()
    try:
        while frame is not None:
            if (
                frame.f_globals.get("__name__") == "dataclasses"
                and frame.f_code.co_name == "_replace"
            ):
                return True
            frame = frame.f_back
    finally:
        del frame
    return False


def _coalesce_compatibility_value(
    canonical_name: str,
    canonical: Any,
    legacy_name: str,
    legacy: Any,
) -> Any:
    """Normalize one canonical value and its optional legacy projection.

    Legacy serialized contracts historically contained a bounded projection of
    the full contract.  Mapping values therefore use an overlap comparison so
    that a complete canonical value and an older bounded projection can be
    read together without creating a second source of truth.
    """
    if canonical is None:
        return legacy
    if legacy is None:
        return canonical
    if isinstance(canonical, Mapping) and isinstance(legacy, Mapping):
        conflicts = [
            key
            for key, value in legacy.items()
            if key in canonical and canonical[key] != value
        ]
        if conflicts:
            if _called_from_dataclasses_replace():
                return legacy
            raise ValueError(
                f"conflicting {canonical_name}/{legacy_name} values: "
                + ", ".join(sorted(str(key) for key in conflicts))
            )
        return canonical
    if canonical != legacy:
        # ``dataclasses.replace`` reconstructs an object by passing every
        # stored field plus the requested change.  A legacy property is not a
        # stored field, but callers may still use the historical alias in a
        # replace call.  Let that explicit replacement win while keeping all
        # ordinary constructors and deserializers fail-closed below.
        if _called_from_dataclasses_replace():
            return legacy
        raise ValueError(f"conflicting {canonical_name}/{legacy_name} values")
    return canonical


def _legacy_contract_projection(
    leaf_contract: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the bounded pre-neutralization ``task_contract`` projection."""
    if not isinstance(leaf_contract, Mapping):
        return None
    return {
        key: leaf_contract.get(key)
        for key in (
            "number",
            "title",
            "url",
            "body_sha256",
            "critical_outcome",
            "bug_contract",
            "documentation_contract",
            "research_contract",
            "research_outcome",
        )
    }


def _contract_agent_view(
    leaf_contract: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a bounded contract projection without exposing the Issue body."""
    if not isinstance(leaf_contract, Mapping):
        return None
    return _legacy_contract_projection(leaf_contract)


def _issue_number_from_state(value: Any) -> int | None:
    """Read an Issue number from canonical state with a legacy adapter fallback."""
    issue_number = getattr(value, "issue_number", None)
    if isinstance(issue_number, int) and not isinstance(issue_number, bool):
        return issue_number
    task_number = getattr(value, "task_number", None)
    return (
        task_number
        if isinstance(task_number, int) and not isinstance(task_number, bool)
        else None
    )


@dataclass(frozen=True, init=False)
class LiveState:
    """Current mechanical facts acquired from Git and GitHub for one Issue.

    The neutral fields are the only stored identity, branch, and contract
    values.  ``task_*`` names remain constructor and property compatibility
    projections for existing Task callers and persisted consumers.
    """

    issue_number: int
    repository: str | None
    issue: Mapping[str, Any] | None
    relationships: Mapping[str, Any]
    git: Mapping[str, Any]
    target_branch: str
    local_issue_branch: str | None
    local_issue_head: str | None
    remote_issue_branch: str | None
    remote_issue_oid: str | None
    open_pr: Mapping[str, Any] | None
    merged_pr_numbers: tuple[int, ...]
    merged: bool | None
    checks: Mapping[str, Any]
    cleanup: Mapping[str, Any]
    status: ResolutionStatus = ResolutionStatus.RESOLVED
    stop_reasons: tuple[str, ...] = ()
    warnings: tuple[Mapping[str, Any], ...] = ()
    merged_pr: Mapping[str, Any] | None = None
    leaf_contract: Mapping[str, Any] | None = None
    issue_profile: Mapping[str, Any] | None = None

    def __init__(
        self,
        issue_number: int | None = None,
        repository: str | None = None,
        issue: Mapping[str, Any] | None = None,
        relationships: Mapping[str, Any] | None = None,
        git: Mapping[str, Any] | None = None,
        target_branch: str = "",
        local_issue_branch: str | None = None,
        local_issue_head: str | None = None,
        remote_issue_branch: str | None = None,
        remote_issue_oid: str | None = None,
        open_pr: Mapping[str, Any] | None = None,
        merged_pr_numbers: tuple[int, ...] | list[int] = (),
        merged: bool | None = None,
        checks: Mapping[str, Any] | None = None,
        cleanup: Mapping[str, Any] | None = None,
        status: ResolutionStatus = ResolutionStatus.RESOLVED,
        stop_reasons: tuple[str, ...] | list[str] = (),
        warnings: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
        merged_pr: Mapping[str, Any] | None = None,
        leaf_contract: Mapping[str, Any] | None = None,
        issue_profile: Mapping[str, Any] | None = None,
        *,
        task_number: int | None = None,
        local_task_branch: str | None = None,
        local_task_head: str | None = None,
        remote_task_branch: str | None = None,
        remote_task_oid: str | None = None,
        task_contract: Mapping[str, Any] | None = None,
    ) -> None:
        resolved_number = _coalesce_compatibility_value(
            "issue_number", issue_number, "task_number", task_number
        )
        if not isinstance(resolved_number, int) or isinstance(resolved_number, bool):
            raise TypeError("LiveState issue_number must be an integer")

        values = (
            (
                "local_issue_branch",
                local_issue_branch,
                "local_task_branch",
                local_task_branch,
            ),
            (
                "local_issue_head",
                local_issue_head,
                "local_task_head",
                local_task_head,
            ),
            (
                "remote_issue_branch",
                remote_issue_branch,
                "remote_task_branch",
                remote_task_branch,
            ),
            (
                "remote_issue_oid",
                remote_issue_oid,
                "remote_task_oid",
                remote_task_oid,
            ),
        )
        resolved_values = {
            name: _coalesce_compatibility_value(name, canonical, legacy_name, legacy)
            for name, canonical, legacy_name, legacy in values
        }
        resolved_contract = _coalesce_compatibility_value(
            "leaf_contract", leaf_contract, "task_contract", task_contract
        )
        if resolved_contract is not None and not isinstance(resolved_contract, Mapping):
            raise TypeError("LiveState leaf_contract must be a mapping or None")

        if isinstance(status, str):
            status = ResolutionStatus(status)
        if not isinstance(status, ResolutionStatus):
            raise TypeError("LiveState status must be a ResolutionStatus")
        object.__setattr__(self, "issue_number", resolved_number)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "issue", issue)
        object.__setattr__(self, "relationships", relationships or {})
        object.__setattr__(self, "git", git or {})
        object.__setattr__(self, "target_branch", target_branch)
        for name, value in resolved_values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "open_pr", open_pr)
        object.__setattr__(self, "merged_pr_numbers", tuple(merged_pr_numbers))
        object.__setattr__(self, "merged", merged)
        object.__setattr__(self, "checks", checks or {})
        object.__setattr__(self, "cleanup", cleanup or {})
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "stop_reasons", tuple(stop_reasons))
        object.__setattr__(self, "warnings", tuple(warnings))
        object.__setattr__(self, "merged_pr", merged_pr)
        object.__setattr__(self, "leaf_contract", resolved_contract)
        object.__setattr__(self, "issue_profile", issue_profile)

    # Read-only legacy projections.  They intentionally do not appear in the
    # dataclass storage or in ``__dict__`` and can never diverge from neutral
    # state.
    @property
    def task_number(self) -> int:
        return self.issue_number

    @property
    def local_task_branch(self) -> str | None:
        return self.local_issue_branch

    @property
    def local_task_head(self) -> str | None:
        return self.local_issue_head

    @property
    def remote_task_branch(self) -> str | None:
        return self.remote_issue_branch

    @property
    def remote_task_oid(self) -> str | None:
        return self.remote_issue_oid

    @property
    def task_contract(self) -> Mapping[str, Any] | None:
        return self.leaf_contract

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
                    # Neutral fields are canonical.  The task_* entries below
                    # are retained as additive compatibility projections.
                    "issue_number": self.issue_number,
                    "local_issue_branch": self.local_issue_branch,
                    "local_issue_head": self.local_issue_head,
                    "remote_issue_branch": self.remote_issue_branch,
                    "remote_issue_oid": self.remote_issue_oid,
                    "leaf_contract": self.leaf_contract,
                    "task_number": self.issue_number,
                    "repository": self.repository,
                    "issue": self.issue,
                    "task_contract": _legacy_contract_projection(self.leaf_contract),
                    "issue_profile": self.issue_profile,
                    "relationships": self.relationships,
                    "git": self.git,
                    "target_branch": self.target_branch,
                    "local_task_branch": self.local_issue_branch,
                    "local_task_head": self.local_issue_head,
                    "remote_task_branch": self.remote_issue_branch,
                    "remote_task_oid": self.remote_issue_oid,
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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LiveState:
        """Deserialize canonical or legacy state into one neutral model.

        Both schemas may be present in an additive receipt.  The constructor
        performs overlap conflict detection and stores only the neutral form.
        """
        if not isinstance(value, Mapping):
            raise TypeError("LiveState payload must be a mapping")
        merged_numbers = value.get("merged_pr_numbers", ())
        if not isinstance(merged_numbers, (list, tuple)):
            raise TypeError("LiveState merged_pr_numbers must be a sequence")
        stop_reasons = value.get("stop_reasons", ())
        if not isinstance(stop_reasons, (list, tuple)):
            raise TypeError("LiveState stop_reasons must be a sequence")
        warnings = value.get("warnings", ())
        if not isinstance(warnings, (list, tuple)):
            raise TypeError("LiveState warnings must be a sequence")
        return cls(
            issue_number=value.get("issue_number"),
            task_number=value.get("task_number"),
            repository=value.get("repository"),
            issue=value.get("issue"),
            relationships=value.get("relationships"),
            git=value.get("git"),
            target_branch=value.get("target_branch", ""),
            local_issue_branch=value.get("local_issue_branch"),
            local_task_branch=value.get("local_task_branch"),
            local_issue_head=value.get("local_issue_head"),
            local_task_head=value.get("local_task_head"),
            remote_issue_branch=value.get("remote_issue_branch"),
            remote_task_branch=value.get("remote_task_branch"),
            remote_issue_oid=value.get("remote_issue_oid"),
            remote_task_oid=value.get("remote_task_oid"),
            open_pr=value.get("open_pr"),
            merged_pr_numbers=merged_numbers,
            merged=value.get("merged"),
            checks=value.get("checks"),
            cleanup=value.get("cleanup"),
            status=value.get("status", ResolutionStatus.RESOLVED),
            stop_reasons=stop_reasons,
            warnings=warnings,
            merged_pr=value.get("merged_pr"),
            leaf_contract=value.get("leaf_contract"),
            task_contract=value.get("task_contract"),
            issue_profile=value.get("issue_profile"),
        )

    def agent_view(self) -> dict[str, Any]:
        """Return the compact result intended for the invoking Agent."""
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "status",
            "issue_number": self.issue_number,
            "task_number": self.issue_number,
            "repository": self.repository,
            "status": self.status,
            "issue": _issue_agent_view(self.issue),
            "issue_profile": self.issue_profile,
            "target_branch": self.target_branch,
            "local_issue_branch": self.local_issue_branch,
            "local_issue_head": self.local_issue_head,
            "remote_issue_branch": self.remote_issue_branch,
            "remote_issue_oid": self.remote_issue_oid,
            "leaf_contract": _contract_agent_view(self.leaf_contract),
            "task_branch": {
                "local": self.local_issue_branch,
                "local_head_sha": self.local_issue_head,
                "remote": self.remote_issue_branch,
                "remote_head_sha": self.remote_issue_oid,
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

    # The snapshot owns one LiveState object; these accessors expose its
    # canonical neutral model without copying identity/branch/contract data.
    @property
    def issue_number(self) -> int:
        return self.state.issue_number

    @property
    def local_issue_branch(self) -> str | None:
        return self.state.local_issue_branch

    @property
    def local_issue_head(self) -> str | None:
        return self.state.local_issue_head

    @property
    def remote_issue_branch(self) -> str | None:
        return self.state.remote_issue_branch

    @property
    def remote_issue_oid(self) -> str | None:
        return self.state.remote_issue_oid

    @property
    def leaf_contract(self) -> Mapping[str, Any] | None:
        return self.state.leaf_contract

    # Legacy names are read-only projections of the same snapshot state.
    @property
    def task_number(self) -> int:
        return self.issue_number

    @property
    def local_task_branch(self) -> str | None:
        return self.local_issue_branch

    @property
    def local_task_head(self) -> str | None:
        return self.local_issue_head

    @property
    def remote_task_branch(self) -> str | None:
        return self.remote_issue_branch

    @property
    def remote_task_oid(self) -> str | None:
        return self.remote_issue_oid

    @property
    def task_contract(self) -> Mapping[str, Any] | None:
        return self.leaf_contract

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": self.operation,
            # These are projections of ``state`` for consumers that read the
            # operation envelope without unpacking its nested LiveState.
            "issue_number": self.issue_number,
            "local_issue_branch": self.local_issue_branch,
            "local_issue_head": self.local_issue_head,
            "remote_issue_branch": self.remote_issue_branch,
            "remote_issue_oid": self.remote_issue_oid,
            "leaf_contract": _jsonable(self.leaf_contract),
            "task_number": self.issue_number,
            "local_task_branch": self.local_issue_branch,
            "local_task_head": self.local_issue_head,
            "remote_task_branch": self.remote_issue_branch,
            "remote_task_oid": self.remote_issue_oid,
            "task_contract": _jsonable(_legacy_contract_projection(self.leaf_contract)),
            "state": self.state.to_dict(),
            "required_checks": _jsonable(self.required_checks),
            "acquisition_warnings": _jsonable(self.acquisition_warnings),
            "fact_profile": self.fact_profile,
            "acquired_facts": list(self.acquired_facts),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OperationSnapshot:
        """Deserialize a snapshot through the canonical LiveState adapter."""
        if not isinstance(value, Mapping):
            raise TypeError("OperationSnapshot payload must be a mapping")
        state = value.get("state")
        if not isinstance(state, Mapping):
            raise TypeError("OperationSnapshot state payload must be a mapping")
        restored_state = LiveState.from_dict(state)
        for canonical_name, legacy_name, expected in (
            ("issue_number", "task_number", restored_state.issue_number),
            (
                "local_issue_branch",
                "local_task_branch",
                restored_state.local_issue_branch,
            ),
            (
                "local_issue_head",
                "local_task_head",
                restored_state.local_issue_head,
            ),
            (
                "remote_issue_branch",
                "remote_task_branch",
                restored_state.remote_issue_branch,
            ),
            ("remote_issue_oid", "remote_task_oid", restored_state.remote_issue_oid),
            ("leaf_contract", "task_contract", restored_state.leaf_contract),
        ):
            if canonical_name not in value and legacy_name not in value:
                continue
            observed = _coalesce_compatibility_value(
                canonical_name,
                value.get(canonical_name),
                legacy_name,
                value.get(legacy_name),
            )
            _coalesce_compatibility_value(
                canonical_name,
                expected,
                "serialized_" + canonical_name,
                observed,
            )
        warnings = value.get("acquisition_warnings", ())
        facts = value.get("acquired_facts", ())
        if not isinstance(warnings, (list, tuple)):
            raise TypeError("OperationSnapshot acquisition_warnings must be a sequence")
        if not isinstance(facts, (list, tuple)):
            raise TypeError("OperationSnapshot acquired_facts must be a sequence")
        operation = value.get("operation")
        if not isinstance(operation, str) or not operation:
            raise TypeError("OperationSnapshot operation must be a non-empty string")
        return cls(
            operation=operation,
            state=restored_state,
            required_checks=value.get("required_checks"),
            acquisition_warnings=tuple(warnings),
            fact_profile=str(value.get("fact_profile", "diagnostic")),
            acquired_facts=tuple(str(fact) for fact in facts),
        )


@dataclass(frozen=True)
class EffectReceipt:
    effect: str
    action: str
    details: Mapping[str, Any]

    @property
    def is_complete(self) -> bool:
        """Whether the effect produced a proven completion receipt.

        Effect actions are executor-specific.  The kernel reserves only
        ``pending`` for an effect whose postcondition is not proven, so
        completion controllers must not maintain an action-name allowlist.
        """
        return self.action != "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect,
            "action": self.action,
            "details": _jsonable(self.details),
        }
