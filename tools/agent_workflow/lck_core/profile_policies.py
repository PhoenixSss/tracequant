"""Typed leaf policy dispatch and profile-owned evidence contracts.

The LCK kernel owns lifecycle mechanics. This module owns the small seam at
which a resolved leaf profile becomes executable policy. The seam is explicit:
production uses :data:`DEFAULT_PROFILE_POLICY_REGISTRY` and tests may construct
an independent registry without mutating production state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Protocol, cast, runtime_checkable

from bug_policy import bug_contract_snapshot, is_valid_bug_contract
from critical_outcome import contract_from_snapshot, critical_outcome_snapshot
from documentation_policy import (
    DocumentationChangeResult,
    documentation_contract_snapshot,
    evaluate_documentation_changes,
    is_valid_documentation_contract,
)
from research_policy import is_valid_research_contract, research_contract_snapshot
from workflow_common import sha256_json

from .issue_profiles import LeafIssueWorkflowProfile, resolve_leaf_issue_profile
from .models import LckStopError

PROFILE_EVIDENCE_SCHEMA_VERSION: Final = 1
PROFILE_EVIDENCE_STAGES: Final = ("contract", "candidate", "review", "completion")
_KIND_PATTERN: Final = r"^[a-z][a-z0-9_.-]*$"
_CODE_PATTERN: Final = r"^[A-Z][A-Z0-9_.-]*$"


class ProfilePolicyError(ValueError):
    """A profile policy or its typed evidence cannot be accepted safely."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class ProfileEvidenceRecord:
    """One policy-owned, strongly shaped stage-evidence record."""

    kind: str
    schema_version: int
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ProfilePolicyError("evidence kind is required")
        if not isinstance(self.schema_version, int) or isinstance(
            self.schema_version, bool
        ):
            raise ProfilePolicyError("evidence schema_version must be an integer")
        if not isinstance(self.payload, Mapping):
            raise ProfilePolicyError("evidence payload must be a mapping")
        if any(not isinstance(key, str) for key in self.payload):
            raise ProfilePolicyError("evidence payload keys must be strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "schema_version": self.schema_version,
            "payload": _jsonable(self.payload),
        }

    def serialize(self) -> str:
        """Return deterministic JSON for this record."""
        return _canonical_json(self.to_dict())


EvidenceRecord = ProfileEvidenceRecord
StageEvidence = ProfileEvidenceRecord


@dataclass(frozen=True)
class PolicyBlocker:
    """A normalized blocker emitted by one profile policy."""

    code: str
    kind: str
    detail: str
    evidence_ref: str | Mapping[str, Any] | None = None

    @property
    def stable_code(self) -> str:
        return self.code

    @property
    def evidence_reference(self) -> str | Mapping[str, Any] | None:
        return self.evidence_ref

    def __post_init__(self) -> None:
        if re.fullmatch(_CODE_PATTERN, self.code or "") is None:
            raise ProfilePolicyError("policy blocker code is malformed")
        if re.fullmatch(_KIND_PATTERN, self.kind or "") is None:
            raise ProfilePolicyError("policy blocker kind is malformed")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ProfilePolicyError("policy blocker detail is required")
        if self.evidence_ref is not None and not isinstance(
            self.evidence_ref, (str, Mapping)
        ):
            raise ProfilePolicyError("policy blocker evidence reference is malformed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "kind": self.kind,
            "detail": self.detail,
            "evidence_ref": _jsonable(self.evidence_ref),
        }


ProfileBlocker = PolicyBlocker


@dataclass(frozen=True)
class ProfileEvidenceEnvelope:
    """Frozen four-stage envelope shared by every leaf profile.

    ``leaf_contract`` never appears here. The contract stage contains
    validation evidence and may contain a bounded identity/digest reference to
    the canonical acquisition input.
    """

    profile_id: str
    schema_version: int = PROFILE_EVIDENCE_SCHEMA_VERSION
    contract: ProfileEvidenceRecord | None = None
    candidate: ProfileEvidenceRecord | None = None
    review: ProfileEvidenceRecord | None = None
    completion: ProfileEvidenceRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "schema_version": self.schema_version,
            "contract": self.contract.to_dict() if self.contract else None,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "review": self.review.to_dict() if self.review else None,
            "completion": self.completion.to_dict() if self.completion else None,
        }

    def serialize(self) -> str:
        return _canonical_json(self.to_dict())

    def validated(
        self,
        registry: ProfilePolicyRegistry | None = None,
    ) -> ProfileEvidenceEnvelope:
        selected = registry or DEFAULT_PROFILE_POLICY_REGISTRY
        if not isinstance(self.profile_id, str) or not self.profile_id:
            raise ProfilePolicyError("evidence envelope profile_id is required")
        if self.schema_version != PROFILE_EVIDENCE_SCHEMA_VERSION:
            raise ProfilePolicyError(
                "unsupported ProfileEvidenceEnvelope schema version"
            )
        policy = selected.resolve_profile_id(self.profile_id)
        for record in (self.contract, self.candidate, self.review, self.completion):
            if record is not None:
                _validate_policy_evidence(policy, record)
        return self

    def validate_evidence(
        self,
        registry: ProfilePolicyRegistry | None = None,
    ) -> ProfileEvidenceEnvelope:
        """Compatibility spelling for the lifecycle validation boundary."""
        return self.validated(registry)


def serialize_profile_evidence(
    value: ProfileEvidenceEnvelope,
    *,
    registry: ProfilePolicyRegistry | None = None,
) -> str:
    """Serialize a validated envelope deterministically."""
    return value.validated(registry).serialize()


def validate_profile_evidence(
    value: ProfileEvidenceEnvelope,
    *,
    registry: ProfilePolicyRegistry | None = None,
) -> ProfileEvidenceEnvelope:
    """Validate and return an envelope at a lifecycle boundary."""
    return value.validated(registry)


@dataclass(frozen=True)
class PolicyContext:
    """Bounded inputs and callbacks exposed to a leaf policy."""

    profile: LeafIssueWorkflowProfile | None = None
    profile_id: str | None = None
    phase: str | None = None
    issue: Mapping[str, Any] | None = None
    relationships: Mapping[str, Any] | None = None
    repo_root: Path | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    include_index: bool = False
    changed_files: tuple[str, ...] = ()
    progress: Any = None
    critical_outcome: Callable[[], Mapping[str, Any]] | None = None
    documentation_validation: Callable[[], Mapping[str, Any]] | None = None
    research_validation: Callable[[], Mapping[str, Any]] | None = None

    @property
    def resolved_profile_id(self) -> str | None:
        return self.profile.profile_id if self.profile is not None else self.profile_id


@runtime_checkable
class LeafIssuePolicy(Protocol):
    """Minimal executable policy API for one canonical leaf profile."""

    @property
    def profile_id(self) -> str: ...

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord: ...

    def evaluate_blockers(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> Iterable[PolicyBlocker]: ...

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord: ...

    def validate_evidence(self, record: ProfileEvidenceRecord) -> bool: ...


class ProfilePolicyRegistry:
    """Immutable explicit registry used to resolve executable policies."""

    def __init__(
        self,
        policies: Mapping[str, LeafIssuePolicy] | Iterable[LeafIssuePolicy],
    ) -> None:
        values = (
            tuple(policies.values())
            if isinstance(policies, Mapping)
            else tuple(policies)
        )
        selected: dict[str, LeafIssuePolicy] = {}
        for policy in values:
            profile_id = getattr(policy, "profile_id", None)
            if not isinstance(profile_id, str) or not profile_id:
                raise ProfilePolicyError("registered policy has no profile_id")
            if profile_id in selected:
                raise ProfilePolicyError(f"duplicate registered profile: {profile_id}")
            if not isinstance(policy, LeafIssuePolicy):
                raise ProfilePolicyError(
                    f"registered policy {profile_id!r} does not implement LeafIssuePolicy"
                )
            selected[profile_id] = policy
        self._policies = MappingProxyType(selected)

    @classmethod
    def from_policies(cls, *policies: LeafIssuePolicy) -> ProfilePolicyRegistry:
        return cls(policies)

    @property
    def policies(self) -> Mapping[str, LeafIssuePolicy]:
        return self._policies

    def resolve_profile_id(self, profile_id: str) -> LeafIssuePolicy:
        if not isinstance(profile_id, str) or not profile_id:
            raise ProfilePolicyError("profile policy identity is unavailable")
        try:
            return self._policies[profile_id]
        except KeyError as exc:
            raise ProfilePolicyError(
                f"profile policy is not registered: {profile_id}"
            ) from exc

    def resolve(self, profile: LeafIssueWorkflowProfile | str) -> LeafIssuePolicy:
        if isinstance(profile, str):
            if profile.startswith("type:"):
                for candidate in self._policies.values():
                    if getattr(candidate, "canonical_type_label", None) == profile:
                        return candidate
                raise ProfilePolicyError(f"profile policy is not registered: {profile}")
            return self.resolve_profile_id(profile)
        if not isinstance(profile, LeafIssueWorkflowProfile):
            raise ProfilePolicyError("profile metadata is malformed")
        policy = self.resolve_profile_id(profile.profile_id)
        policy_label = getattr(policy, "canonical_type_label", None)
        if policy_label is not None and policy_label != profile.canonical_type_label:
            raise ProfilePolicyError(
                f"profile/policy canonical type label mismatch: {profile.profile_id}"
            )
        return policy

    def resolve_issue(
        self, issue: Mapping[str, Any]
    ) -> tuple[LeafIssueWorkflowProfile, LeafIssuePolicy]:
        resolution = resolve_leaf_issue_profile(issue)
        if not resolution.resolved or resolution.profile is None:
            raise ProfilePolicyError(
                resolution.error_message or "profile resolution failed"
            )
        return resolution.profile, self.resolve(resolution.profile)


LeafIssuePolicyRegistry = ProfilePolicyRegistry


def _contract_reference(leaf_contract: Mapping[str, Any]) -> dict[str, Any]:
    reference = {
        "number": leaf_contract.get("number"),
        "body_sha256": leaf_contract.get("body_sha256"),
    }
    if not isinstance(reference["body_sha256"], str) or not reference["body_sha256"]:
        reference["contract_sha256"] = sha256_json(
            {
                "number": leaf_contract.get("number"),
                "title": leaf_contract.get("title"),
                "url": leaf_contract.get("url"),
            }
        )
    return reference


def _validate_policy_evidence(
    policy: LeafIssuePolicy, record: ProfileEvidenceRecord
) -> None:
    if record.schema_version != PROFILE_EVIDENCE_SCHEMA_VERSION:
        raise ProfilePolicyError(
            f"unsupported evidence schema version for {record.kind}"
        )
    try:
        valid = policy.validate_evidence(record)
    except (ProfilePolicyError, ValueError) as exc:
        raise ProfilePolicyError(str(exc)) from exc
    if valid is not True:
        raise ProfilePolicyError(f"policy rejected evidence kind: {record.kind}")


def _coerce_evidence(value: Any) -> ProfileEvidenceRecord:
    if isinstance(value, ProfileEvidenceRecord):
        return value
    if isinstance(value, Mapping):
        kind = value.get("kind")
        schema_version = value.get("schema_version")
        payload = value.get("payload")
        if (
            not isinstance(kind, str)
            or not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or not isinstance(payload, Mapping)
        ):
            raise ProfilePolicyError("policy returned malformed evidence")
        return ProfileEvidenceRecord(
            kind=kind,
            schema_version=schema_version,
            payload=payload,
        )
    raise ProfilePolicyError("policy returned malformed evidence")


class _BuiltinPolicy:
    """Small common codec used only by repository-owned policies."""

    profile_id: str
    canonical_type_label: str
    contract_kind: str
    candidate_kind: str
    policy_label: str
    legacy_result_field: str | None = None
    candidate_requires_result: bool = False

    def _record(
        self,
        kind: str,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        **payload: Any,
    ) -> ProfileEvidenceRecord:
        del context
        return ProfileEvidenceRecord(
            kind,
            PROFILE_EVIDENCE_SCHEMA_VERSION,
            {
                "policy_id": self.profile_id,
                "contract_ref": _contract_reference(leaf_contract),
                **payload,
            },
        )

    def _contract_snapshot(
        self,
        leaf_contract: Mapping[str, Any],
        field_name: str,
        snapshot: Callable[[str | None], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        value = leaf_contract.get(field_name)
        if isinstance(value, Mapping):
            return value
        body = leaf_contract.get("body")
        return snapshot(body if isinstance(body, str) else None)

    def validate_evidence(self, record: ProfileEvidenceRecord) -> bool:
        if record.schema_version != PROFILE_EVIDENCE_SCHEMA_VERSION:
            return False
        if record.kind not in {self.contract_kind, self.candidate_kind}:
            return False
        payload = record.payload
        if payload.get("policy_id") != self.profile_id or not isinstance(
            payload.get("contract_ref"), Mapping
        ):
            return False
        if record.kind == self.contract_kind:
            return isinstance(payload.get("contract"), Mapping)
        if not isinstance(payload.get("status"), str):
            return False
        if payload.get("status") == "fail":
            return True
        return not self.candidate_requires_result or isinstance(
            payload.get("result"), Mapping
        )

    def evaluate_blockers(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> Iterable[PolicyBlocker]:
        del context, leaf_contract
        _validate_policy_evidence(cast(LeafIssuePolicy, self), contract_evidence)
        return ()

    def candidate_failure(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
        result: Mapping[str, Any] | None,
        error: str,
    ) -> ProfileEvidenceRecord:
        return self._record(
            self.candidate_kind,
            context,
            leaf_contract,
            result=_jsonable(result) if result is not None else None,
            status="fail",
            error=error,
        )

    def legacy_result(self, result: Mapping[str, Any] | None) -> dict[str, Any]:
        if self.legacy_result_field is None or result is None:
            return {}
        return {self.legacy_result_field: dict(result)}


class _TaskPolicy(_BuiltinPolicy):
    profile_id = "task"
    canonical_type_label = "type:task"
    contract_kind = "task.contract.v1"
    candidate_kind = "task.candidate.v1"
    policy_label = "Critical Outcome"
    legacy_result_field = "critical_outcome"
    candidate_requires_result = True

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord:
        snapshot = self._contract_snapshot(
            leaf_contract, "critical_outcome", critical_outcome_snapshot
        )
        try:
            contract = contract_from_snapshot(snapshot)
        except ValueError as exc:
            raise ProfilePolicyError(str(exc)) from exc
        return self._record(
            self.contract_kind,
            context,
            leaf_contract,
            contract=snapshot,
            verification_test=contract.verification_test,
        )

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord:
        if context.critical_outcome is None:
            raise ProfilePolicyError("Task Critical Outcome verifier is unavailable")
        _validate_policy_evidence(self, contract_evidence)
        result = context.critical_outcome()
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Task Critical Outcome result is malformed")
        if result.get("status") != "pass":
            raise ProfilePolicyError("Task Critical Outcome candidate did not pass")
        return self._record(
            self.candidate_kind,
            context,
            leaf_contract,
            result=dict(result),
            status=result.get("status"),
        )


class _BugPolicy(_BuiltinPolicy):
    profile_id = "bug"
    canonical_type_label = "type:bug"
    contract_kind = "bug.contract.v1"
    candidate_kind = "bug.candidate.v1"
    policy_label = "Bug defect"

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord:
        snapshot = self._contract_snapshot(
            leaf_contract, "bug_contract", bug_contract_snapshot
        )
        if not is_valid_bug_contract(snapshot):
            raise ProfilePolicyError(
                str(snapshot.get("detail") or "Bug defect contract is invalid")
            )
        return self._record(
            self.contract_kind, context, leaf_contract, contract=snapshot
        )

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord:
        _validate_policy_evidence(self, contract_evidence)
        return self._record(self.candidate_kind, context, leaf_contract, status="pass")


class _DocumentationPolicy(_BuiltinPolicy):
    profile_id = "documentation"
    canonical_type_label = "type:documentation"
    contract_kind = "documentation.contract.v1"
    candidate_kind = "documentation.candidate.v1"
    policy_label = "Documentation"
    legacy_result_field = "documentation_validation"
    candidate_requires_result = True

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord:
        snapshot = self._contract_snapshot(
            leaf_contract, "documentation_contract", documentation_contract_snapshot
        )
        if not is_valid_documentation_contract(snapshot):
            raise ProfilePolicyError(
                str(snapshot.get("detail") or "Documentation contract is invalid")
            )
        return self._record(
            self.contract_kind, context, leaf_contract, contract=snapshot
        )

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord:
        _validate_policy_evidence(self, contract_evidence)
        if context.documentation_validation is not None:
            result = context.documentation_validation()
        else:
            result = evaluate_documentation_changes(context.changed_files).to_dict()
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Documentation candidate result is malformed")
        return self._record(
            self.candidate_kind,
            context,
            leaf_contract,
            result=dict(result),
            status=result.get("status"),
        )


class _ResearchPolicy(_BuiltinPolicy):
    profile_id = "research"
    canonical_type_label = "type:research"
    contract_kind = "research.contract.v1"
    candidate_kind = "research.candidate.v1"
    policy_label = "Research"
    legacy_result_field = "research_validation"
    candidate_requires_result = True

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord:
        snapshot = self._contract_snapshot(
            leaf_contract, "research_contract", research_contract_snapshot
        )
        if not is_valid_research_contract(snapshot):
            raise ProfilePolicyError(
                str(snapshot.get("detail") or "Research contract is invalid")
            )
        return self._record(
            self.contract_kind, context, leaf_contract, contract=snapshot
        )

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord:
        _validate_policy_evidence(self, contract_evidence)
        if context.research_validation is None:
            raise ProfilePolicyError("Research candidate validator is unavailable")
        result = context.research_validation()
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Research candidate result is malformed")
        return self._record(
            self.candidate_kind,
            context,
            leaf_contract,
            result=dict(result),
            status=result.get("status"),
        )


DEFAULT_PROFILE_POLICY_REGISTRY: Final = ProfilePolicyRegistry(
    (_TaskPolicy(), _BugPolicy(), _DocumentationPolicy(), _ResearchPolicy())
)
PRODUCTION_PROFILE_POLICY_REGISTRY: Final = DEFAULT_PROFILE_POLICY_REGISTRY
PROFILE_POLICY_REGISTRY: Final = DEFAULT_PROFILE_POLICY_REGISTRY
POLICIES_BY_PROFILE_ID: Final = DEFAULT_PROFILE_POLICY_REGISTRY.policies


@dataclass(frozen=True)
class ProfileContractCheck:
    """Bounded result for one profile's Issue contract."""

    policy: str
    label: str
    valid: bool
    contract: Mapping[str, Any] | None
    detail: str = ""
    evidence: ProfileEvidenceRecord | None = None

    @property
    def failure_reason(self) -> str:
        return f"{self.label} contract invalid: {self.detail or 'contract is invalid'}"


@dataclass(frozen=True)
class ProfileGateResults:
    """Results of generic profile candidate dispatch."""

    critical_outcome: dict[str, Any] | None = None
    documentation_validation: dict[str, Any] | None = None
    research_validation: dict[str, Any] | None = None
    profile_evidence: ProfileEvidenceEnvelope | None = None


@dataclass(frozen=True)
class ProfileGateFailure(LckStopError):
    """Candidate policy failure carrying evidence produced before failure."""

    detail: str
    profile_evidence: ProfileEvidenceEnvelope | None = None

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class _ContractPolicy:
    label: str
    snapshot: Callable[[str | None], Mapping[str, Any]]
    is_valid: Callable[[object], bool]


# Compatibility/readability map for typed Issue contract snapshots. Executable
# behavior is owned by the registry above.
_CONTRACT_POLICIES: Final = {
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


def resolve_profile_policy(
    profile: LeafIssueWorkflowProfile,
    *,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
) -> LeafIssuePolicy:
    """Resolve profile metadata to one executable policy through the registry."""
    return registry.resolve(profile)


def resolve_issue_policy(
    issue: Mapping[str, Any],
    *,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
) -> tuple[LeafIssueWorkflowProfile, LeafIssuePolicy]:
    return registry.resolve_issue(issue)


def _context_for(
    profile: LeafIssueWorkflowProfile,
    *,
    issue: Mapping[str, Any] | None = None,
    context: PolicyContext | None = None,
    **updates: Any,
) -> PolicyContext:
    if context is not None:
        return context
    return PolicyContext(profile=profile, issue=issue, **updates)


def _raw_contract_snapshot(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if profile.profile_id == "task":
        field_name = "critical_outcome"
    elif profile.contract_policy is not None:
        field_name = f"{profile.contract_policy}_contract"
    else:
        return None
    value = issue.get(field_name)
    return value if isinstance(value, Mapping) else None


def validate_profile_contract(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
    *,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
    context: PolicyContext | None = None,
) -> ProfileContractCheck:
    """Validate one canonical contract through its executable policy."""
    policy: LeafIssuePolicy | None = None
    try:
        policy = resolve_profile_policy(profile, registry=registry)
        execution_context = _context_for(profile, issue=issue, context=context)
        evidence = _coerce_evidence(policy.validate_contract(execution_context, issue))
        _validate_policy_evidence(policy, evidence)
    except (ProfilePolicyError, ValueError) as exc:
        label = getattr(policy, "policy_label", profile.profile_id.title())
        return ProfileContractCheck(
            policy=profile.profile_id,
            label=label,
            valid=False,
            contract=_raw_contract_snapshot(profile, issue),
            detail=str(exc),
        )
    return ProfileContractCheck(
        policy=profile.profile_id,
        label=getattr(policy, "policy_label", profile.profile_id.title()),
        valid=True,
        contract=_raw_contract_snapshot(profile, issue),
        evidence=evidence,
    )


def evaluate_profile_blockers(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
    *,
    contract_evidence: ProfileEvidenceRecord | None = None,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
    context: PolicyContext | None = None,
) -> tuple[PolicyBlocker, ...]:
    """Dispatch and validate generic profile blockers deterministically."""
    policy = resolve_profile_policy(profile, registry=registry)
    execution_context = _context_for(profile, issue=issue, context=context)
    check = validate_profile_contract(
        profile,
        issue,
        registry=registry,
        context=execution_context,
    )
    if not check.valid or check.evidence is None:
        return (
            PolicyBlocker(
                code="CONTRACT_INVALID",
                kind="contract",
                detail=check.failure_reason,
                evidence_ref={"profile_id": profile.profile_id},
            ),
        )
    evidence = contract_evidence or check.evidence
    _validate_policy_evidence(policy, evidence)
    raw = policy.evaluate_blockers(execution_context, issue, evidence)
    if raw is None:
        raise ProfilePolicyError("policy returned no blocker collection")
    blockers = tuple(raw)
    if any(not isinstance(blocker, PolicyBlocker) for blocker in blockers):
        raise ProfilePolicyError("policy returned malformed blocker")
    return tuple(sorted(blockers, key=lambda item: (item.code, item.kind, item.detail)))


def validate_profile_candidate(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
    *,
    contract_evidence: ProfileEvidenceRecord | None = None,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
    context: PolicyContext | None = None,
) -> ProfileEvidenceRecord:
    """Dispatch one candidate validator without profile branching in callers."""
    policy = resolve_profile_policy(profile, registry=registry)
    execution_context = _context_for(profile, issue=issue, context=context)
    check = validate_profile_contract(
        profile,
        issue,
        registry=registry,
        context=execution_context,
    )
    if not check.valid or check.evidence is None:
        raise ProfilePolicyError(check.failure_reason)
    evidence = contract_evidence or check.evidence
    _validate_policy_evidence(policy, evidence)
    candidate = _coerce_evidence(
        policy.validate_candidate(execution_context, issue, evidence)
    )
    _validate_policy_evidence(policy, candidate)
    expected_kind = getattr(policy, "candidate_kind", candidate.kind)
    if candidate.kind != expected_kind:
        raise ProfilePolicyError("policy returned evidence for the wrong stage")
    return candidate


def _result_from_candidate(
    record: ProfileEvidenceRecord | None,
) -> dict[str, Any] | None:
    if record is None:
        return None
    result = record.payload.get("result")
    return dict(result) if isinstance(result, Mapping) else None


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
    issue: Mapping[str, Any] | None = None,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
) -> ProfileGateResults:
    """Run contract and candidate stages through the selected policy."""
    policy = resolve_profile_policy(profile, registry=registry)
    observed_result: dict[str, Any] | None = None

    def run_documentation() -> Mapping[str, Any]:
        nonlocal observed_result
        try:
            result = documentation_validation.run(base_sha)
        except BaseException:
            result = getattr(documentation_validation, "last_result", None)
            if isinstance(result, Mapping):
                observed_result = dict(result)
            raise
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Documentation candidate result is malformed")
        observed_result = dict(result)
        return result

    def run_research() -> Mapping[str, Any]:
        nonlocal observed_result
        try:
            result = research_validation.run(
                base_sha,
                head_sha=head_sha,
                include_index=include_index,
            )
        except BaseException:
            result = getattr(research_validation, "last_result", None)
            if isinstance(result, Mapping):
                observed_result = dict(result)
            raise
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Research candidate result is malformed")
        observed_result = dict(result)
        return result

    def run_critical() -> Mapping[str, Any]:
        nonlocal observed_result
        result = critical_outcome()
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Critical Outcome result is malformed")
        observed_result = dict(result)
        return result

    context = PolicyContext(
        profile=profile,
        phase="delivery",
        issue=issue,
        base_sha=base_sha,
        head_sha=head_sha,
        include_index=include_index,
        progress=progress,
        critical_outcome=run_critical,
        documentation_validation=run_documentation,
        research_validation=run_research,
    )
    if not isinstance(issue, Mapping):
        raise LckStopError("current leaf Issue contract is unavailable")
    check = validate_profile_contract(
        profile, issue, registry=registry, context=context
    )
    if not check.valid or check.evidence is None:
        raise LckStopError(check.failure_reason)

    try:
        candidate = validate_profile_candidate(
            profile,
            issue,
            contract_evidence=check.evidence,
            registry=registry,
            context=context,
        )
    except BaseException as exc:
        failure_builder = getattr(policy, "candidate_failure", None)
        failure = (
            failure_builder(
                context,
                issue,
                check.evidence,
                observed_result,
                str(exc),
            )
            if callable(failure_builder)
            else None
        )
        envelope = ProfileEvidenceEnvelope(
            profile_id=profile.profile_id,
            contract=check.evidence,
            candidate=failure,
        ).validated(registry)
        raise ProfileGateFailure(str(exc), envelope) from exc

    envelope = ProfileEvidenceEnvelope(
        profile_id=profile.profile_id,
        contract=check.evidence,
        candidate=candidate,
    ).validated(registry)
    result = _result_from_candidate(candidate)
    legacy: dict[str, Any] = getattr(policy, "legacy_result", lambda _result: {})(
        result
    )
    return ProfileGateResults(
        **legacy,
        profile_evidence=envelope,
    )


def evaluate_profile_changes(
    profile: LeafIssueWorkflowProfile,
    changed_files: Sequence[str],
) -> DocumentationChangeResult | None:
    """Evaluate a profile's legacy Review change-policy projection."""
    if profile.change_policy == "documentation":
        return evaluate_documentation_changes(changed_files)
    return None


def profile_cleanup_label(profile: LeafIssueWorkflowProfile) -> str:
    return profile.issue_kind.value.title()


def profile_has_research_artifacts(profile: LeafIssueWorkflowProfile) -> bool:
    return profile.artifact_policy == "research"


def profile_research_outcome_supported(profile: LeafIssueWorkflowProfile) -> bool:
    return profile.supports_research_outcome
