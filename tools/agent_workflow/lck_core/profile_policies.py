"""The single profile-policy dispatch layer used by the LCK kernel.

The four leaf policy modules own their business contracts.  This module owns
only the mapping from a resolved profile to the shared lifecycle hooks.  Phase
controllers must call these helpers instead of maintaining their own type
allowlists or importing the profile type enum.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bug_policy import bug_contract_snapshot, is_valid_bug_contract
from documentation_policy import (
    DocumentationChangeResult,
    documentation_contract_snapshot,
    evaluate_documentation_changes,
    is_valid_documentation_contract,
)
from research_policy import is_valid_research_contract, research_contract_snapshot

from .issue_profiles import LeafIssueWorkflowProfile


@dataclass(frozen=True)
class ProfileContractCheck:
    """Bounded result for a profile-specific Issue contract."""

    policy: str
    label: str
    valid: bool
    contract: Mapping[str, Any] | None
    detail: str = ""

    @property
    def failure_reason(self) -> str:
        return f"{self.label} contract invalid: {self.detail or 'contract is invalid'}"


@dataclass(frozen=True)
class ProfileGateResults:
    """Results of the profile-specific Delivery gates."""

    critical_outcome: dict[str, Any] | None = None
    documentation_validation: dict[str, Any] | None = None
    research_validation: dict[str, Any] | None = None


@dataclass(frozen=True)
class _ContractPolicy:
    label: str
    snapshot: Callable[[str | None], Mapping[str, Any]]
    is_valid: Callable[[object], bool]


_CONTRACT_POLICIES: dict[str, _ContractPolicy] = {
    "bug": _ContractPolicy("Bug defect", bug_contract_snapshot, is_valid_bug_contract),
    "documentation": _ContractPolicy(
        "Documentation",
        documentation_contract_snapshot,
        is_valid_documentation_contract,
    ),
    "research": _ContractPolicy(
        "Research", research_contract_snapshot, is_valid_research_contract
    ),
}


def validate_profile_contract(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
) -> ProfileContractCheck | None:
    """Validate the contract selected by one canonical profile.

    A Task's Critical Outcome is validated by its dedicated gate, so Task has
    no form contract here.  The remaining typed leaf contracts are selected by
    profile metadata and dispatched through the one registry above.
    """

    policy_name = profile.contract_policy
    if policy_name is None:
        return None
    policy = _CONTRACT_POLICIES.get(policy_name)
    if policy is None:
        return ProfileContractCheck(
            policy=policy_name,
            label=policy_name.title(),
            valid=False,
            contract=None,
            detail="profile contract policy is not registered",
        )

    field_name = f"{policy_name}_contract"
    contract = issue.get(field_name)
    if not isinstance(contract, Mapping):
        body = issue.get("body")
        contract = policy.snapshot(body if isinstance(body, str) else None)
    bounded = dict(contract) if isinstance(contract, Mapping) else None
    valid = policy.is_valid(bounded)
    detail = (
        bounded.get("detail")
        if isinstance(bounded, Mapping) and isinstance(bounded.get("detail"), str)
        else "contract is invalid"
    )
    return ProfileContractCheck(
        policy=policy_name,
        label=policy.label,
        valid=valid,
        contract=bounded,
        detail="" if valid else detail,
    )


def run_profile_delivery_gates(
    profile: LeafIssueWorkflowProfile,
    *,
    base_sha: str,
    head_sha: str | None,
    include_index: bool,
    progress: Any,
    documentation_validation: Any,
    research_validation: Any,
    critical_outcome: Callable[[], dict[str, Any]],
) -> ProfileGateResults:
    """Run one profile's candidate gates in the shared Delivery sequence."""

    documentation_result: dict[str, Any] | None = None
    research_result: dict[str, Any] | None = None
    critical_result: dict[str, Any] | None = None

    if profile.change_policy == "documentation":
        progress.running("documentation-policy")
        documentation_result = documentation_validation.run(base_sha)
    elif profile.artifact_policy == "research":
        progress.running("research-artifact-policy")
        research_result = research_validation.run(
            base_sha,
            head_sha=head_sha,
            include_index=include_index,
        )

    if profile.requires_critical_outcome:
        progress.running("critical-outcome")
        critical_result = critical_outcome()

    return ProfileGateResults(
        critical_outcome=critical_result,
        documentation_validation=documentation_result,
        research_validation=research_result,
    )


def evaluate_profile_changes(
    profile: LeafIssueWorkflowProfile,
    changed_files: Sequence[str],
) -> DocumentationChangeResult | None:
    """Evaluate a profile's candidate file policy when one exists."""

    if profile.change_policy == "documentation":
        return evaluate_documentation_changes(changed_files)
    return None


def profile_cleanup_label(profile: LeafIssueWorkflowProfile) -> str:
    """Return the bounded human-facing name for cleanup diagnostics."""

    return profile.issue_kind.value.title()


def profile_has_research_artifacts(profile: LeafIssueWorkflowProfile) -> bool:
    return profile.artifact_policy == "research"


def profile_research_outcome_supported(profile: LeafIssueWorkflowProfile) -> bool:
    return profile.supports_research_outcome
