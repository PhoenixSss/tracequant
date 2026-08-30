"""Canonical leaf Issue workflow profiles and type-label resolution.

Issue labels are the only authoritative carrier for a leaf Issue's workflow
kind.  This module deliberately contains policy facts only; lifecycle state
acquisition and effects remain owned by the existing LCK controllers.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final


class LeafIssueKind(StrEnum):
    TASK = "task"
    BUG = "bug"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"


class IssueProfileResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    MISSING_TYPE = "missing_type"
    MULTIPLE_TYPES = "multiple_types"
    UNKNOWN_TYPE = "unknown_type"
    NON_LEAF_TYPE = "non_leaf_type"


@dataclass(frozen=True)
class WorkflowPolicyEntrypoints:
    """Named policy entrypoints reserved by one profile.

    The values are stable identifiers rather than callables so selecting a
    profile cannot create a second lifecycle engine or introduce import
    coupling between policy and phase controllers.
    """

    eligibility: str
    validation: str
    completion: str

    def to_dict(self) -> dict[str, str]:
        return {
            "eligibility": self.eligibility,
            "validation": self.validation,
            "completion": self.completion,
        }


@dataclass(frozen=True)
class LeafIssueWorkflowProfile:
    """The complete type-specific contract shared by all LCK phases."""

    profile_id: str
    issue_kind: LeafIssueKind
    canonical_type_label: str
    requires_critical_outcome: bool
    branch_namespace: str
    lifecycle_enabled: bool
    policy_entrypoints: WorkflowPolicyEntrypoints
    candidate_capability: str
    contract_policy: str | None = None
    change_policy: str | None = None
    artifact_policy: str | None = None
    supports_research_outcome: bool = False
    allow_legacy_branch_aliases: bool = False

    @property
    def eligibility_policy(self) -> str:
        return self.policy_entrypoints.eligibility

    @property
    def validation_policy(self) -> str:
        return self.policy_entrypoints.validation

    @property
    def completion_policy(self) -> str:
        return self.policy_entrypoints.completion

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "issue_kind": self.issue_kind.value,
            "canonical_type_label": self.canonical_type_label,
            "requires_critical_outcome": self.requires_critical_outcome,
            "branch_namespace": self.branch_namespace,
            "lifecycle_enabled": self.lifecycle_enabled,
            "policy_entrypoints": self.policy_entrypoints.to_dict(),
            "candidate_capability": self.candidate_capability,
            "contract_policy": self.contract_policy,
            "change_policy": self.change_policy,
            "artifact_policy": self.artifact_policy,
            "supports_research_outcome": self.supports_research_outcome,
            "allow_legacy_branch_aliases": self.allow_legacy_branch_aliases,
        }


def _profile(
    kind: LeafIssueKind,
    *,
    requires_critical_outcome: bool,
    lifecycle_enabled: bool,
    branch_namespace: str,
    candidate_capability: str,
    contract_policy: str | None = None,
    change_policy: str | None = None,
    artifact_policy: str | None = None,
    supports_research_outcome: bool = False,
    allow_legacy_branch_aliases: bool = False,
) -> LeafIssueWorkflowProfile:
    return LeafIssueWorkflowProfile(
        profile_id=kind.value,
        issue_kind=kind,
        canonical_type_label=f"type:{kind.value}",
        requires_critical_outcome=requires_critical_outcome,
        branch_namespace=branch_namespace,
        lifecycle_enabled=lifecycle_enabled,
        policy_entrypoints=WorkflowPolicyEntrypoints(
            eligibility=kind.value,
            validation=kind.value,
            completion=kind.value,
        ),
        candidate_capability=candidate_capability,
        contract_policy=contract_policy,
        change_policy=change_policy,
        artifact_policy=artifact_policy,
        supports_research_outcome=supports_research_outcome,
        allow_legacy_branch_aliases=allow_legacy_branch_aliases,
    )


TASK_PROFILE: Final = _profile(
    LeafIssueKind.TASK,
    requires_critical_outcome=True,
    lifecycle_enabled=True,
    branch_namespace="task/",
    candidate_capability="verify_critical_outcome",
    allow_legacy_branch_aliases=True,
)
BUG_PROFILE: Final = _profile(
    LeafIssueKind.BUG,
    requires_critical_outcome=False,
    lifecycle_enabled=True,
    branch_namespace="bug/",
    candidate_capability="validate_bug_contract",
    contract_policy="bug",
)
DOCUMENTATION_PROFILE: Final = _profile(
    LeafIssueKind.DOCUMENTATION,
    requires_critical_outcome=False,
    lifecycle_enabled=True,
    branch_namespace="documentation/",
    candidate_capability="validate_documentation_candidate",
    contract_policy="documentation",
    change_policy="documentation",
)
RESEARCH_PROFILE: Final = _profile(
    LeafIssueKind.RESEARCH,
    requires_critical_outcome=False,
    lifecycle_enabled=True,
    branch_namespace="research/",
    candidate_capability="validate_research_artifact",
    contract_policy="research",
    artifact_policy="research",
    supports_research_outcome=True,
)

PROFILES_BY_TYPE_LABEL: Final = {
    profile.canonical_type_label: profile
    for profile in (
        TASK_PROFILE,
        BUG_PROFILE,
        DOCUMENTATION_PROFILE,
        RESEARCH_PROFILE,
    )
}
_NON_LEAF_TYPE_LABELS: Final = frozenset({"type:feature", "type:epic"})


def _label_names(issue: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(issue, Mapping):
        return ()
    raw_labels: Any = issue.get("labels")
    if isinstance(raw_labels, Mapping):
        raw_labels = raw_labels.get("items")
    if not isinstance(raw_labels, list):
        return ()
    labels: list[str] = []
    for item in raw_labels:
        if isinstance(item, str):
            labels.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
            labels.append(item["name"])
    return tuple(labels)


@dataclass(frozen=True)
class IssueProfileResolution:
    status: IssueProfileResolutionStatus
    profile: LeafIssueWorkflowProfile | None = None
    type_labels: tuple[str, ...] = ()
    detail: str = ""

    @property
    def resolved(self) -> bool:
        return self.status is IssueProfileResolutionStatus.RESOLVED

    @property
    def terminal_status(self) -> str:
        if self.resolved and self.profile is not None:
            return (
                "PROFILE_ENABLED"
                if self.profile.lifecycle_enabled
                else "PROFILE_NOT_ENABLED"
            )
        return self.status.value.upper()

    @property
    def error_message(self) -> str:
        if self.resolved:
            if self.profile is None:
                return "PROFILE_RESOLUTION_FAILED: resolved profile is unavailable"
            if not self.profile.lifecycle_enabled:
                return (
                    "PROFILE_NOT_ENABLED: workflow profile "
                    f"{self.profile.canonical_type_label} is not enabled"
                )
            return ""
        return f"{self.terminal_status}: {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status.value,
            "terminal_status": self.terminal_status,
            "type_labels": list(self.type_labels),
        }
        if self.detail:
            result["detail"] = self.detail
        if self.profile is not None:
            result["profile"] = self.profile.to_dict()
        return result


def resolve_leaf_issue_profile(
    issue: Mapping[str, Any] | None,
) -> IssueProfileResolution:
    """Resolve exactly one profile from canonical ``type:*`` labels.

    Title, body, GitHub's Issue Type field, and all non-type labels are
    intentionally ignored.  Duplicate labels are treated as ambiguous input
    even though GitHub normally prevents duplicate labels on one Issue.
    """

    labels = _label_names(issue)
    type_labels = tuple(label for label in labels if label.startswith("type:"))
    if not type_labels:
        return IssueProfileResolution(
            status=IssueProfileResolutionStatus.MISSING_TYPE,
            detail="exactly one canonical type:* label is required",
        )
    if len(type_labels) != 1:
        return IssueProfileResolution(
            status=IssueProfileResolutionStatus.MULTIPLE_TYPES,
            type_labels=type_labels,
            detail=(
                "exactly one canonical type:* label is required; found "
                + ", ".join(type_labels)
            ),
        )

    type_label = type_labels[0]
    if type_label in _NON_LEAF_TYPE_LABELS:
        return IssueProfileResolution(
            status=IssueProfileResolutionStatus.NON_LEAF_TYPE,
            type_labels=type_labels,
            detail=(
                "target Issue is not a type:task; non-leaf type labels are not "
                f"eligible: {type_label}"
            ),
        )
    profile = PROFILES_BY_TYPE_LABEL.get(type_label)
    if profile is None:
        return IssueProfileResolution(
            status=IssueProfileResolutionStatus.UNKNOWN_TYPE,
            type_labels=type_labels,
            detail=f"unsupported canonical type label: {type_label}",
        )
    return IssueProfileResolution(
        status=IssueProfileResolutionStatus.RESOLVED,
        profile=profile,
        type_labels=type_labels,
    )


def resolve_issue_profile(
    issue: Mapping[str, Any] | None,
) -> IssueProfileResolution:
    """Compatibility-oriented name for the canonical leaf resolver."""

    return resolve_leaf_issue_profile(issue)


def canonical_branch_for_profile(
    profile: LeafIssueWorkflowProfile,
    issue_number: int,
    title: str,
) -> str:
    """Derive a profile-owned branch name without reading Issue semantics."""

    title_without_prefix = re.sub(r"^\s*\[[^]]+\]\s*", "", title)
    normalized = unicodedata.normalize("NFKD", title_without_prefix)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-zA-Z0-9]+", ascii_title.casefold())
    slug = "-".join(words[:12]).strip("-") or profile.issue_kind.value
    return f"{profile.branch_namespace}{issue_number}-{slug}"
