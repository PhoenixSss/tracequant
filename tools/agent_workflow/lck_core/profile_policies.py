"""Typed leaf policy dispatch and profile-owned evidence contracts.

The LCK kernel owns lifecycle mechanics. This module owns the small seam at
which a resolved leaf profile becomes executable policy. The seam is explicit:
production uses :data:`DEFAULT_PROFILE_POLICY_REGISTRY` and tests may construct
an independent registry without mutating production state.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Protocol, cast, runtime_checkable

from bug_policy import bug_contract_snapshot, is_valid_bug_contract
from critical_outcome import (
    CriticalOutcomeError,
    contract_from_snapshot,
    critical_outcome_snapshot,
    verify_critical_outcome,
)
from documentation_policy import (
    DocumentationChangeResult,
    documentation_contract_snapshot,
    evaluate_documentation_changes,
    is_valid_documentation_contract,
)
from research_policy import (
    RESEARCH_OUTCOME_FIELD,
    ResearchPolicyError,
    architecture_decision_is_consistent,
    bind_research_outcome,
    decision_contract_snapshot,
    evaluate_research_changes,
    is_implementation_outcome,
    is_valid_research_contract,
    parse_research_outcome,
    require_typed_research_outcome,
    research_artifact_binding,
    research_artifact_outcome,
    research_contract_snapshot,
)
from workflow_common import read_json_text, safe_text, sha256_json

from .effective_diff import calculate_effective_diff
from .issue_profiles import (
    IssueProfileResolution,
    LeafIssueWorkflowProfile,
    resolve_leaf_issue_profile,
)
from .models import LckStopError
from .shared_facts import canonical_project_field

PROFILE_EVIDENCE_SCHEMA_VERSION: Final = 1
PROFILE_EVIDENCE_STAGES: Final = ("contract", "candidate", "review", "completion")
_KIND_PATTERN: Final = r"^[a-z][a-z0-9_.-]*$"
_CODE_PATTERN: Final = r"^[A-Z][A-Z0-9_.-]*$"
_TYPE_LABEL_PATTERN: Final = r"^type:[a-z][a-z0-9_.-]*$"
_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
_EFFECT_KIND_PATTERN: Final = r"^[a-z][a-z0-9_.-]*$"


class ProfilePolicyError(ValueError):
    """A profile policy or its typed evidence cannot be accepted safely."""


class ProfileCandidateRejected(ProfilePolicyError):
    """A candidate failed with a structured, policy-owned result."""

    def __init__(self, detail: str, result: Mapping[str, Any]) -> None:
        super().__init__(detail)
        self.result = dict(result)


@dataclass(frozen=True)
class ProfileEffectDescriptor:
    """A constrained, data-only intent emitted by a profile policy.

    A descriptor is deliberately not executable.  The LCK kernel validates its
    shape and dispatches the allow-listed ``effect_kind`` through its own
    executor registry.  Keeping parameters, postconditions, and receipt
    metadata as JSON data also prevents a policy from smuggling a callable or
    an unbounded GitHub operation through the profile boundary.
    """

    effect_kind: str
    schema_version: int
    parameters: Mapping[str, Any]
    postcondition: Mapping[str, Any]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.effect_kind, str)
            or re.fullmatch(_EFFECT_KIND_PATTERN, self.effect_kind) is None
        ):
            raise ProfilePolicyError("effect descriptor kind is malformed")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != 1
        ):
            raise ProfilePolicyError("unsupported effect descriptor schema version")
        for name, value in (
            ("parameters", self.parameters),
            ("postcondition", self.postcondition),
            ("receipt", self.receipt),
        ):
            if not isinstance(value, Mapping):
                raise ProfilePolicyError(f"effect descriptor {name} must be a mapping")
            if any(not isinstance(key, str) for key in value):
                raise ProfilePolicyError(
                    f"effect descriptor {name} keys must be strings"
                )
            try:
                _validate_json_data(value)
                _canonical_json(value)
            except (TypeError, ValueError) as exc:
                raise ProfilePolicyError(
                    f"effect descriptor {name} must be JSON data"
                ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_kind": self.effect_kind,
            "schema_version": self.schema_version,
            "parameters": _jsonable(self.parameters),
            "postcondition": _jsonable(self.postcondition),
            "receipt": _jsonable(self.receipt),
        }

    def serialize(self) -> str:
        return _canonical_json(self.to_dict())


# Short aliases make the generic contract convenient for policy adapters and
# for external test-only policies without exposing a second schema.
EffectDescriptor = ProfileEffectDescriptor


class DocumentationReclassificationRequired(LckStopError):
    """The Documentation policy rejected the candidate file scope."""

    code = "DOCUMENTATION_RECLASSIFICATION_REQUIRED"

    def __init__(
        self, message: str, *, result: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.result = dict(result) if isinstance(result, Mapping) else None


class ResearchReclassificationRequired(LckStopError):
    """The Research policy rejected the candidate artifact scope."""

    code = "RESEARCH_RECLASSIFICATION_REQUIRED"

    def __init__(
        self, message: str, *, result: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.result = dict(result) if isinstance(result, Mapping) else None


class ResearchOutcomeRequired(LckStopError):
    """A Research completion boundary has no typed outcome."""

    code = "RESEARCH_OUTCOME_REQUIRED"


ProfileResolver = Callable[[Mapping[str, Any] | None], IssueProfileResolution]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _validate_json_data(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise TypeError("non-finite numbers are not JSON data")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            _validate_json_data(nested)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _validate_json_data(nested)
        return
    raise TypeError(f"unsupported JSON data type: {type(value).__name__}")


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
        *,
        leaf_contract: Mapping[str, Any] | None = None,
    ) -> ProfileEvidenceEnvelope:
        selected = registry or DEFAULT_PROFILE_POLICY_REGISTRY
        if not isinstance(self.profile_id, str) or not self.profile_id:
            raise ProfilePolicyError("evidence envelope profile_id is required")
        if self.schema_version != PROFILE_EVIDENCE_SCHEMA_VERSION:
            raise ProfilePolicyError(
                "unsupported ProfileEvidenceEnvelope schema version"
            )
        policy = selected.resolve_profile_id(self.profile_id)
        records = (
            ("contract", self.contract),
            ("candidate", self.candidate),
            ("review", self.review),
            ("completion", self.completion),
        )
        if leaf_contract is None and any(record is not None for _, record in records):
            raise ProfilePolicyError(
                "canonical leaf contract is required to validate profile evidence"
            )
        for stage, record in records:
            if record is not None:
                _validate_policy_evidence(
                    policy,
                    record,
                    stage=stage,
                    leaf_contract=leaf_contract,
                )
        return self

    def validate_evidence(
        self,
        registry: ProfilePolicyRegistry | None = None,
        *,
        leaf_contract: Mapping[str, Any] | None = None,
    ) -> ProfileEvidenceEnvelope:
        """Compatibility spelling for the lifecycle validation boundary."""
        return self.validated(registry, leaf_contract=leaf_contract)


def serialize_profile_evidence(
    value: ProfileEvidenceEnvelope,
    *,
    registry: ProfilePolicyRegistry | None = None,
    leaf_contract: Mapping[str, Any] | None = None,
) -> str:
    """Serialize a validated envelope deterministically."""
    return value.validated(registry, leaf_contract=leaf_contract).serialize()


def validate_profile_evidence(
    value: ProfileEvidenceEnvelope,
    *,
    registry: ProfilePolicyRegistry | None = None,
    leaf_contract: Mapping[str, Any] | None = None,
) -> ProfileEvidenceEnvelope:
    """Validate and return an envelope at a lifecycle boundary."""
    return value.validated(registry, leaf_contract=leaf_contract)


@dataclass(frozen=True)
class PolicyContext:
    """Bounded inputs and callbacks exposed to a leaf policy."""

    profile: LeafIssueWorkflowProfile | None = None
    profile_id: str | None = None
    phase: str | None = None
    issue: Mapping[str, Any] | None = None
    relationships: Mapping[str, Any] | None = None
    repository: str | None = None
    downstream_contract: Mapping[str, Any] | None = None
    repo_root: Path | None = None
    runner: Any = None
    base_sha: str | None = None
    head_sha: str | None = None
    include_index: bool = False
    changed_files: tuple[str, ...] = ()
    progress: Any = None
    # ``services`` is a compatibility seam for deterministic test doubles.
    # Production policies use the bounded inputs above and do not receive
    # controller-owned concrete validators.
    services: tuple[Any, ...] = ()
    review_identity: Mapping[str, Any] | None = None
    review_record: Mapping[str, Any] | None = None
    merged_pr: Mapping[str, Any] | None = None
    review_verdict: str | None = None
    research_outcome: str | None = None
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


class ProfileReviewPolicy(Protocol):
    """Optional generic review-stage capability supplied by a leaf policy."""

    def validate_review(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        review_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord: ...


class ProfileCompletionPolicy(Protocol):
    """Optional generic completion-stage capability supplied by a leaf policy."""

    def validate_completion(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        completion_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord | None: ...


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
        by_type_label: dict[str, LeafIssuePolicy] = {}
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
            type_label = getattr(policy, "canonical_type_label", None)
            if (
                not isinstance(type_label, str)
                or re.fullmatch(_TYPE_LABEL_PATTERN, type_label) is None
            ):
                raise ProfilePolicyError(
                    f"registered policy {profile_id!r} has a malformed canonical type label"
                )
            if type_label in by_type_label:
                raise ProfilePolicyError(
                    f"duplicate canonical type label: {type_label}"
                )
            selected[profile_id] = policy
            by_type_label[type_label] = policy
        self._policies = MappingProxyType(selected)
        self._policies_by_type_label = MappingProxyType(by_type_label)

    @classmethod
    def from_policies(cls, *policies: LeafIssuePolicy) -> ProfilePolicyRegistry:
        return cls(policies)

    @property
    def policies(self) -> Mapping[str, LeafIssuePolicy]:
        return self._policies

    @property
    def policies_by_type_label(self) -> Mapping[str, LeafIssuePolicy]:
        return self._policies_by_type_label

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
                try:
                    return self._policies_by_type_label[profile]
                except KeyError as exc:
                    raise ProfilePolicyError(
                        f"profile policy is not registered: {profile}"
                    ) from exc
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
        self,
        issue: Mapping[str, Any],
        *,
        profile_resolver: ProfileResolver = resolve_leaf_issue_profile,
    ) -> tuple[LeafIssueWorkflowProfile, LeafIssuePolicy]:
        resolution = profile_resolver(issue)
        if not resolution.resolved or resolution.profile is None:
            raise ProfilePolicyError(
                resolution.error_message or "profile resolution failed"
            )
        return resolution.profile, self.resolve(resolution.profile)


LeafIssuePolicyRegistry = ProfilePolicyRegistry


def _contract_reference(leaf_contract: Mapping[str, Any]) -> dict[str, Any]:
    body_sha256 = leaf_contract.get("body_sha256")
    if isinstance(body_sha256, str) and body_sha256:
        return {
            "number": leaf_contract.get("number"),
            "body_sha256": body_sha256,
        }
    return {
        "number": leaf_contract.get("number"),
        "contract_sha256": sha256_json(
            {
                "number": leaf_contract.get("number"),
                "title": leaf_contract.get("title"),
                "url": leaf_contract.get("url"),
            }
        ),
    }


def _valid_contract_reference(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    number = value.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        return False
    body_sha256 = value.get("body_sha256")
    contract_sha256 = value.get("contract_sha256")
    if body_sha256 is not None and (
        not isinstance(body_sha256, str)
        or re.fullmatch(_SHA256_PATTERN, body_sha256) is None
    ):
        return False
    if contract_sha256 is not None and (
        not isinstance(contract_sha256, str)
        or re.fullmatch(_SHA256_PATTERN, contract_sha256) is None
    ):
        return False
    if body_sha256 is None and contract_sha256 is None:
        return False
    if body_sha256 is not None and contract_sha256 is not None:
        return False
    return set(value) == (
        {"number", "body_sha256"}
        if body_sha256 is not None
        else {"number", "contract_sha256"}
    )


def _contract_reference_matches(
    value: Any,
    leaf_contract: Mapping[str, Any],
) -> bool:
    return _valid_contract_reference(value) and dict(value) == _contract_reference(
        leaf_contract
    )


def _expected_evidence_kind(policy: LeafIssuePolicy, stage: str) -> str:
    value = getattr(policy, f"{stage}_kind", None)
    if value is None:
        value = f"{policy.profile_id}.{stage}.v1"
    if not isinstance(value, str) or re.fullmatch(_KIND_PATTERN, value) is None:
        raise ProfilePolicyError(f"policy evidence kind for {stage} is malformed")
    return value


def _validate_policy_evidence(
    policy: LeafIssuePolicy,
    record: ProfileEvidenceRecord,
    *,
    stage: str | None = None,
    leaf_contract: Mapping[str, Any] | None = None,
) -> None:
    if record.schema_version != PROFILE_EVIDENCE_SCHEMA_VERSION:
        raise ProfilePolicyError(
            f"unsupported evidence schema version for {record.kind}"
        )
    if stage is not None and record.kind != _expected_evidence_kind(policy, stage):
        raise ProfilePolicyError(
            f"policy evidence kind does not match {stage} stage: {record.kind}"
        )
    if leaf_contract is not None and not _contract_reference_matches(
        record.payload.get("contract_ref"), leaf_contract
    ):
        raise ProfilePolicyError(
            "profile evidence contract reference does not match the canonical leaf contract"
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
    review_kind: str | None = None
    completion_kind: str | None = None
    legacy_result_field: str | None = None
    candidate_requires_result: bool = False

    def _kind(self, stage: str) -> str:
        value = getattr(self, f"{stage}_kind", None)
        return value if isinstance(value, str) else f"{self.profile_id}.{stage}.v1"

    def _validate_contract_payload(self, value: object) -> bool:
        del value
        return False

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
        if record.kind not in {
            self.contract_kind,
            self.candidate_kind,
            self._kind("review"),
            self._kind("completion"),
        }:
            return False
        payload = record.payload
        if payload.get("policy_id") != self.profile_id or not isinstance(
            payload.get("contract_ref"), Mapping
        ):
            return False
        if record.kind == self.contract_kind:
            return self._validate_contract_payload(payload.get("contract"))
        status = payload.get("status")
        if status not in {"pass", "fail"}:
            return False
        result = payload.get("result")
        if result is not None and not isinstance(result, Mapping):
            return False
        if status == "fail":
            error = payload.get("error")
            return isinstance(error, str) and bool(error.strip())
        if record.kind == self.candidate_kind and self.candidate_requires_result:
            return isinstance(result, Mapping) and result.get("status") == "pass"
        return True

    def validate_review(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        review_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord:
        return self._record(
            self._kind("review"),
            context,
            leaf_contract,
            result={"status": "pass"},
            status="pass",
            review=review_input,
        )

    def review_artifact(
        self,
        context: PolicyContext,
        review_input: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        del context, review_input
        return None

    def validate_completion(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        completion_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord | None:
        del context, leaf_contract, completion_input
        return None

    def evaluate_blockers(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> Iterable[PolicyBlocker]:
        del context
        _validate_policy_evidence(
            cast(LeafIssuePolicy, self),
            contract_evidence,
            stage="contract",
            leaf_contract=leaf_contract,
        )
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

    def _service_result(self, context: PolicyContext) -> Mapping[str, Any] | None:
        """Use an injected test service without making it a controller contract."""
        if not context.services:
            return None
        service = context.services[0]
        run = getattr(service, "run", None)
        if not callable(run):
            raise ProfilePolicyError("injected profile service is not executable")
        try:
            result = run(
                context.base_sha,
                head_sha=context.head_sha,
                include_index=context.include_index,
            )
        except TypeError:
            # Keep old test doubles that only accept a positional base SHA.
            result = run(context.base_sha)
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("profile candidate result is malformed")
        return result


def _candidate_effective_diff(context: PolicyContext, *, command_prefix: str) -> Any:
    if context.runner is None or context.repo_root is None:
        raise ProfilePolicyError("profile candidate diff context is unavailable")
    if not isinstance(context.base_sha, str) or not context.base_sha:
        raise ProfilePolicyError("profile candidate base identity is unavailable")
    head_ref = "HEAD" if context.include_index else (context.head_sha or "HEAD")
    return calculate_effective_diff(
        context.runner,
        base_sha=context.base_sha,
        head_ref=head_ref,
        command_id_prefix=command_prefix,
        cwd=context.repo_root,
        include_index=context.include_index,
    )


def _documentation_candidate_result(context: PolicyContext) -> dict[str, Any]:
    effective_diff = _candidate_effective_diff(
        context, command_prefix="lck-documentation"
    )
    policy = evaluate_documentation_changes(effective_diff.changed_files)
    payload = policy.to_dict()
    payload["effective_diff"] = {
        "merge_base_sha": effective_diff.merge_base_sha,
        "effective_diff_sha256": effective_diff.effective_diff_sha256,
        "source": "index" if context.include_index else "head",
    }
    return payload


def _research_candidate_result(context: PolicyContext) -> dict[str, Any]:
    effective_diff = _candidate_effective_diff(context, command_prefix="lck-research")
    policy = evaluate_research_changes(
        effective_diff.changed_files, repo_root=context.repo_root
    )
    payload = policy.to_dict()
    payload["effective_diff"] = {
        "merge_base_sha": effective_diff.merge_base_sha,
        "effective_diff_sha256": effective_diff.effective_diff_sha256,
        "source": "index" if context.include_index else "head",
    }
    try:
        artifact_outcome = research_artifact_outcome(
            context.repo_root, policy.artifact_files
        )
    except ResearchPolicyError as exc:
        raise ResearchReclassificationRequired(str(exc)) from exc
    payload["artifact_outcome"] = (
        artifact_outcome.value if artifact_outcome is not None else None
    )
    return payload


class DocumentationValidationGate:
    """Compatibility adapter; production Delivery uses policy dispatch."""

    def __init__(self, resolver: Any) -> None:
        self.resolver = resolver
        self.last_result: dict[str, Any] | None = None

    def run(self, base_sha: str) -> dict[str, Any]:
        context = PolicyContext(
            base_sha=base_sha,
            repo_root=self.resolver.repo_root,
            runner=self.resolver.runner,
            include_index=True,
        )
        result = _documentation_candidate_result(context)
        self.last_result = result
        if result.get("status") != "pass":
            raise DocumentationReclassificationRequired(
                "DOCUMENTATION_RECLASSIFICATION_REQUIRED: "
                + str(
                    result.get("detail")
                    or "Documentation policy rejected the candidate"
                ),
                result=result,
            )
        return result


class ResearchValidationGate:
    """Compatibility adapter; production Delivery uses policy dispatch."""

    def __init__(self, resolver: Any) -> None:
        self.resolver = resolver
        self.last_result: dict[str, Any] | None = None

    def run(
        self,
        base_sha: str,
        *,
        head_sha: str | None = None,
        include_index: bool = False,
    ) -> dict[str, Any]:
        context = PolicyContext(
            base_sha=base_sha,
            head_sha=head_sha,
            repo_root=self.resolver.repo_root,
            runner=self.resolver.runner,
            include_index=include_index,
        )
        result = _research_candidate_result(context)
        self.last_result = result
        if result.get("status") != "pass":
            raise ResearchReclassificationRequired(
                "RESEARCH_RECLASSIFICATION_REQUIRED: "
                + str(result.get("detail") or "Research policy rejected the candidate"),
                result=result,
            )
        return result


RESEARCH_OUTCOME_QUERY: Final = r"""
query($owner:String!, $projectNumber:Int!, $userAfter:String, $organizationAfter:String) {
  user(login:$owner) {
    projectV2(number:$projectNumber) {
      items(first:100, after:$userAfter) {
        nodes {
          content { ... on Issue { number repository { nameWithOwner } } }
          fieldValueByName(name:"Research Outcome") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
  organization(login:$owner) {
    projectV2(number:$projectNumber) {
      items(first:100, after:$organizationAfter) {
        nodes {
          content { ... on Issue { number repository { nameWithOwner } } }
          fieldValueByName(name:"Research Outcome") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


class ResearchOutcomeEffect:
    """Legacy read-only adapter retained for callers of the old helper.

    Closeout no longer owns this profile-specific implementation; the generic
    effect executor in ``effects.py`` owns completion side effects.
    """

    def __init__(self, resolver: Any) -> None:
        self.resolver = resolver

    def _query_outcome(self, repository: str, task_number: int) -> str | None:
        owner, separator, _name = repository.partition("/")
        if not separator or not owner:
            return None
        cursors: dict[str, str | None] = {"user": None, "organization": None}
        seen: dict[str, set[str]] = {"user": set(), "organization": set()}
        complete: set[str] = set()
        while len(complete) < 2:
            argv = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={' '.join(RESEARCH_OUTCOME_QUERY.split())}",
                "-F",
                f"owner={owner}",
                "-F",
                "projectNumber=1",
            ]
            if cursors["user"] is not None:
                argv.extend(("-F", f"userAfter={cursors['user']}"))
            if cursors["organization"] is not None:
                argv.extend(("-F", f"organizationAfter={cursors['organization']}"))
            result = self.resolver.runner.run(
                argv, command_id="lck-research-outcome-postcondition", retries=1
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            value = read_json_text(
                result.stdout, field="lck-research-outcome-postcondition"
            )
            if not isinstance(value, Mapping) or value.get("errors"):
                return None
            data = value.get("data")
            if not isinstance(data, Mapping):
                return None
            for scope in ("user", "organization"):
                if scope in complete:
                    continue
                owner_data = data.get(scope)
                if owner_data is None:
                    complete.add(scope)
                    continue
                if not isinstance(owner_data, Mapping):
                    return None
                project = owner_data.get("projectV2")
                if project is None:
                    complete.add(scope)
                    continue
                if not isinstance(project, Mapping):
                    return None
                items = project.get("items")
                if not isinstance(items, Mapping):
                    return None
                nodes = items.get("nodes")
                page_info = items.get("pageInfo")
                if not isinstance(nodes, list) or not isinstance(page_info, Mapping):
                    return None
                for item in nodes:
                    if not isinstance(item, Mapping):
                        continue
                    content = item.get("content")
                    if not isinstance(content, Mapping):
                        continue
                    content_repository = content.get("repository")
                    if (
                        content.get("number") == task_number
                        and isinstance(content_repository, Mapping)
                        and content_repository.get("nameWithOwner") == repository
                    ):
                        field_value = item.get("fieldValueByName")
                        if not isinstance(field_value, Mapping):
                            return None
                        return safe_text(field_value.get("name"))
                if page_info.get("hasNextPage") is False:
                    complete.add(scope)
                elif page_info.get("hasNextPage") is True:
                    end_cursor = page_info.get("endCursor")
                    if not isinstance(end_cursor, str) or not end_cursor:
                        return None
                    if end_cursor in seen[scope]:
                        return None
                    seen[scope].add(end_cursor)
                    cursors[scope] = end_cursor
                else:
                    return None
        return None


class _TaskPolicy(_BuiltinPolicy):
    profile_id = "task"
    canonical_type_label = "type:task"
    contract_kind = "task.contract.v1"
    candidate_kind = "task.candidate.v1"
    policy_label = "Critical Outcome"
    legacy_result_field = "critical_outcome"
    candidate_requires_result = True

    def _validate_contract_payload(self, value: object) -> bool:
        try:
            contract_from_snapshot(value)
        except (CriticalOutcomeError, ValueError):
            return False
        return True

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
        _validate_policy_evidence(
            self,
            contract_evidence,
            stage="contract",
            leaf_contract=leaf_contract,
        )
        if context.critical_outcome is not None:
            # Compatibility for callers that inject a deterministic candidate
            # callback.  Production Delivery leaves this unset and uses the
            # policy-owned verifier below.
            result = context.critical_outcome()
        else:
            if context.repo_root is None or context.runner is None:
                raise ProfilePolicyError(
                    "Task Critical Outcome verifier is unavailable"
                )
            try:
                contract = contract_from_snapshot(
                    contract_evidence.payload.get("contract")
                )
                verified = verify_critical_outcome(
                    context.repo_root,
                    context.runner,
                    contract,
                    progress=context.progress,
                )
            except (CriticalOutcomeError, ValueError) as exc:
                raise ProfilePolicyError(str(exc)) from exc
            result = verified.to_dict()
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Task Critical Outcome result is malformed")
        if result.get("status") != "pass":
            detail = "Critical Outcome FAIL"
            verification_test = contract_evidence.payload.get("verification_test")
            exit_code = result.get("exit_code")
            if isinstance(verification_test, str) and verification_test:
                detail += f": {verification_test}"
            if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                detail += f" exited {exit_code}"
            raise ProfileCandidateRejected(detail, result)
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

    def _validate_contract_payload(self, value: object) -> bool:
        return is_valid_bug_contract(value)

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
        _validate_policy_evidence(
            self,
            contract_evidence,
            stage="contract",
            leaf_contract=leaf_contract,
        )
        return self._record(self.candidate_kind, context, leaf_contract, status="pass")


class _DocumentationPolicy(_BuiltinPolicy):
    profile_id = "documentation"
    canonical_type_label = "type:documentation"
    contract_kind = "documentation.contract.v1"
    candidate_kind = "documentation.candidate.v1"
    policy_label = "Documentation"
    legacy_result_field = "documentation_validation"
    candidate_requires_result = True

    def _validate_contract_payload(self, value: object) -> bool:
        return is_valid_documentation_contract(value)

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
        _validate_policy_evidence(
            self,
            contract_evidence,
            stage="contract",
            leaf_contract=leaf_contract,
        )
        result = self._service_result(context)
        if result is None:
            if context.runner is None or context.repo_root is None:
                result = evaluate_documentation_changes(context.changed_files).to_dict()
            else:
                result = _documentation_candidate_result(context)
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Documentation candidate result is malformed")
        if result.get("status") != "pass":
            raise DocumentationReclassificationRequired(
                "DOCUMENTATION_RECLASSIFICATION_REQUIRED: "
                + str(
                    result.get("detail")
                    or "Documentation policy rejected the candidate"
                ),
                result=result,
            )
        return self._record(
            self.candidate_kind,
            context,
            leaf_contract,
            result=dict(result),
            status=result.get("status"),
        )

    def validate_review(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        review_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord:
        identity = review_input.get("identity", review_input)
        changed_files = (
            identity.get("changed_files") if isinstance(identity, Mapping) else None
        )
        if not isinstance(changed_files, (list, tuple)):
            raise DocumentationReclassificationRequired(
                "DOCUMENTATION_RECLASSIFICATION_REQUIRED: "
                "Review changed-file inventory is unavailable"
            )
        result = evaluate_documentation_changes(changed_files).to_dict()
        if result.get("status") != "pass":
            raise DocumentationReclassificationRequired(
                "DOCUMENTATION_RECLASSIFICATION_REQUIRED: "
                + str(
                    result.get("detail")
                    or "Documentation policy rejected the Review target"
                )
            )
        return self._record(
            self._kind("review"),
            context,
            leaf_contract,
            result=result,
            status="pass",
        )


class _ResearchPolicy(_BuiltinPolicy):
    profile_id = "research"
    canonical_type_label = "type:research"
    contract_kind = "research.contract.v1"
    candidate_kind = "research.candidate.v1"
    policy_label = "Research"
    legacy_result_field = "research_validation"
    candidate_requires_result = True

    def _validate_contract_payload(self, value: object) -> bool:
        return is_valid_research_contract(value)

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

    def evaluate_blockers(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> Iterable[PolicyBlocker]:
        """Evaluate Research dependency semantics through the policy seam."""
        _validate_policy_evidence(
            self,
            contract_evidence,
            stage="contract",
            leaf_contract=leaf_contract,
        )
        if str(leaf_contract.get("state", "")).upper() != "CLOSED":
            return ()

        outcome = leaf_contract.get("research_outcome")
        if outcome is None:
            outcome = canonical_project_field(
                leaf_contract.get("project_items"),
                repository=context.repository,
                field_name=RESEARCH_OUTCOME_FIELD,
            )
        try:
            parsed = parse_research_outcome(outcome)
        except ResearchPolicyError as exc:
            return (
                PolicyBlocker(
                    code="RESEARCH_OUTCOME_UNKNOWN",
                    kind="research-outcome",
                    detail=str(exc),
                ),
            )

        if parsed.value == "ARCHITECTURE DECISION":
            decision_contract = leaf_contract.get("decision_contract")
            if not isinstance(decision_contract, Mapping):
                body = leaf_contract.get("body")
                decision_contract = decision_contract_snapshot(
                    body if isinstance(body, str) else None,
                    research=True,
                )
            if not architecture_decision_is_consistent(
                decision_contract,
                context.downstream_contract,
            ):
                return (
                    PolicyBlocker(
                        code="ARCHITECTURE_DECISION_UNMATCHED",
                        kind="research-outcome",
                        detail="Architecture Decision does not match the downstream contract",
                    ),
                )
            return ()

        if is_implementation_outcome(parsed):
            return ()
        return (
            PolicyBlocker(
                code="RESEARCH_OUTCOME_NOT_IMPLEMENTATION",
                kind="research-outcome",
                detail=f"Research Outcome {parsed.value!r} does not authorize implementation",
            ),
        )

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord:
        _validate_policy_evidence(
            self,
            contract_evidence,
            stage="contract",
            leaf_contract=leaf_contract,
        )
        result = self._service_result(context)
        if result is None:
            if context.runner is None or context.repo_root is None:
                raise ProfilePolicyError("Research candidate validator is unavailable")
            result = _research_candidate_result(context)
        if not isinstance(result, Mapping):
            raise ProfilePolicyError("Research candidate result is malformed")
        if result.get("status") != "pass":
            raise ResearchReclassificationRequired(
                "RESEARCH_RECLASSIFICATION_REQUIRED: "
                + str(result.get("detail") or "Research policy rejected the candidate"),
                result=result,
            )
        return self._record(
            self.candidate_kind,
            context,
            leaf_contract,
            result=dict(result),
            status=result.get("status"),
        )

    def review_artifact(
        self,
        context: PolicyContext,
        review_input: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        if context.repo_root is None:
            raise ResearchPolicyError("Research Review workspace is unavailable")
        identity = review_input.get("identity", review_input)
        if not isinstance(identity, Mapping):
            raise ResearchPolicyError("Research Review identity is unavailable")
        required = {
            key: identity.get(key)
            for key in (
                "task_number",
                "pr_number",
                "base_sha",
                "head_sha",
                "task_body_sha256",
                "merge_base_sha",
                "effective_diff_sha256",
                "changed_files",
            )
        }
        if not isinstance(required["changed_files"], (list, tuple)):
            raise ResearchPolicyError(
                "Research Review changed-file inventory is unavailable"
            )
        try:
            return research_artifact_binding(
                context.repo_root,
                task_number=required["task_number"],
                pr_number=required["pr_number"],
                base_sha=required["base_sha"],
                head_sha=required["head_sha"],
                task_body_sha256=required["task_body_sha256"],
                merge_base_sha=required["merge_base_sha"],
                effective_diff_sha256=required["effective_diff_sha256"],
                changed_files=required["changed_files"],
            )
        except (ResearchPolicyError, TypeError, ValueError) as exc:
            raise ResearchPolicyError(str(exc)) from exc

    def validate_review(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        review_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord:
        artifact = review_input.get("artifact")
        if not isinstance(artifact, Mapping):
            artifact = self.review_artifact(context, review_input)
        verdict = str(review_input.get("verdict") or "PASS").upper()
        outcome = review_input.get("research_outcome")
        if outcome is not None:
            if not isinstance(artifact, Mapping):
                raise ResearchOutcomeRequired(
                    "Research Review Complete requires a reviewed artifact binding"
                )
            try:
                artifact = bind_research_outcome(artifact, outcome)
            except ResearchPolicyError as exc:
                raise ResearchOutcomeRequired(str(exc)) from exc
        if verdict == "PASS":
            if not isinstance(artifact, Mapping):
                raise ResearchOutcomeRequired(
                    "Research Review Complete requires a reviewed artifact binding"
                )
            try:
                require_typed_research_outcome(artifact)
            except ResearchPolicyError as exc:
                raise ResearchOutcomeRequired(
                    f"Research Review Complete requires a typed outcome: {exc}"
                ) from exc
        payload: dict[str, Any] = {
            "result": {"status": "pass"},
            "status": "pass",
            "verdict": verdict,
        }
        if isinstance(artifact, Mapping):
            payload["artifact"] = dict(artifact)
        return self._record(self._kind("review"), context, leaf_contract, **payload)

    def validate_completion(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        completion_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord:
        review_record = completion_input.get("review_record")
        artifact: Mapping[str, Any] | None = None
        if isinstance(review_record, Mapping):
            raw_artifact = review_record.get("research_artifact")
            if not isinstance(raw_artifact, Mapping):
                raw_identity = review_record.get("identity")
                if isinstance(raw_identity, Mapping):
                    raw_artifact = raw_identity.get("research_artifact")
            if isinstance(raw_artifact, Mapping):
                artifact = raw_artifact
        if artifact is None:
            raw_artifact = completion_input.get("artifact")
            if isinstance(raw_artifact, Mapping):
                artifact = raw_artifact
        if artifact is None:
            raise ResearchOutcomeRequired(
                "Research completion requires a reviewed artifact binding"
            )
        self._validate_completion_artifact_binding(
            artifact,
            context=context,
            leaf_contract=leaf_contract,
            completion_input=completion_input,
        )
        try:
            outcome = require_typed_research_outcome(artifact)
        except ResearchPolicyError as exc:
            raise ResearchOutcomeRequired(
                f"Research completion requires a typed outcome: {exc}"
            ) from exc
        repository = completion_input.get("repository") or (
            context.issue.get("repository")
            if isinstance(context.issue, Mapping)
            else None
        )
        task_number = completion_input.get("task_number")
        if not isinstance(repository, str) or not repository:
            raise ProfilePolicyError("Research completion repository is unavailable")
        if (
            not isinstance(task_number, int)
            or isinstance(task_number, bool)
            or task_number <= 0
        ):
            raise ProfilePolicyError("Research completion Task number is unavailable")
        descriptor = ProfileEffectDescriptor(
            effect_kind="project.single_select.set.v1",
            schema_version=1,
            parameters={
                "repository": repository,
                "task_number": task_number,
                "project_number": 1,
                "field": RESEARCH_OUTCOME_FIELD,
                "value": outcome.value,
            },
            postcondition={
                "kind": "project.single_select.equals",
                "repository": repository,
                "task_number": task_number,
                "project_number": 1,
                "field": RESEARCH_OUTCOME_FIELD,
                "value": outcome.value,
            },
            receipt={"outcome": outcome.value},
        )
        return self._record(
            self._kind("completion"),
            context,
            leaf_contract,
            result={"status": "pass", "outcome": outcome.value},
            status="pass",
            effect=descriptor.to_dict(),
        )

    @staticmethod
    def _validate_completion_artifact_binding(
        artifact: Mapping[str, Any],
        *,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        completion_input: Mapping[str, Any],
    ) -> None:
        """Keep a Research completion effect bound to the accepted Review.

        Closeout has already validated the outer Review identity against the
        merged PR.  The Research policy owns the separate artifact binding and
        therefore verifies that the artifact selected for the effect matches
        both that identity and the current merged PR before producing intent.
        """
        review_record = completion_input.get("review_record")
        if not isinstance(review_record, Mapping):
            raise ResearchOutcomeRequired(
                "Research completion requires a reviewed identity"
            )
        reviewed_identity = review_record.get("identity")
        if not isinstance(reviewed_identity, Mapping):
            raise ResearchOutcomeRequired(
                "Research completion requires a reviewed identity"
            )
        identity_artifact = reviewed_identity.get("research_artifact")
        if not isinstance(identity_artifact, Mapping):
            raise ResearchOutcomeRequired(
                "Research completion requires an identity-bound artifact"
            )

        merged_pr = completion_input.get("merged_pr")
        if not isinstance(merged_pr, Mapping):
            merged_pr = context.merged_pr
        if not isinstance(merged_pr, Mapping):
            raise ResearchOutcomeRequired(
                "Research completion requires the current merged PR identity"
            )

        def first_value(source: Mapping[str, Any], *names: str) -> Any:
            for name in names:
                value = source.get(name)
                if value is not None:
                    return value
            return None

        current_identity = {
            "task_number": completion_input.get("task_number"),
            "pr_number": merged_pr.get("number"),
            "base_sha": first_value(merged_pr, "baseRefOid", "base_sha"),
            "head_sha": first_value(merged_pr, "headRefOid", "head_sha"),
            "task_body_sha256": leaf_contract.get("body_sha256"),
        }
        for field, current_value in current_identity.items():
            reviewed_value = reviewed_identity.get(field)
            artifact_value = artifact.get(field)
            if (
                current_value is None
                or reviewed_value != current_value
                or artifact_value != current_value
            ):
                raise ResearchOutcomeRequired(
                    f"Research completion artifact is not bound to the current {field}"
                )

        for field in ("merge_base_sha", "effective_diff_sha256"):
            reviewed_value = reviewed_identity.get(field)
            if reviewed_value is None or artifact.get(field) != reviewed_value:
                raise ResearchOutcomeRequired(
                    f"Research completion artifact is not bound to the reviewed {field}"
                )

        if dict(identity_artifact) != dict(artifact):
            raise ResearchOutcomeRequired(
                "Research completion artifact diverges from the reviewed identity"
            )

    def validate_evidence(self, record: ProfileEvidenceRecord) -> bool:
        if not super().validate_evidence(record):
            return False
        if record.kind == self._kind("review"):
            artifact = record.payload.get("artifact")
            if artifact is not None and not isinstance(artifact, Mapping):
                return False
        if record.kind == self._kind("completion"):
            effect = record.payload.get("effect")
            if not isinstance(effect, Mapping):
                return False
            try:
                ProfileEffectDescriptor(
                    effect_kind=effect.get("effect_kind"),
                    schema_version=effect.get("schema_version"),
                    parameters=effect.get("parameters"),
                    postcondition=effect.get("postcondition"),
                    receipt=effect.get("receipt"),
                )
            except (ProfilePolicyError, TypeError, ValueError):
                return False
        return True


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
    legacy_results: Mapping[str, Any] = MappingProxyType({})

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
    profile_resolver: ProfileResolver = resolve_leaf_issue_profile,
) -> tuple[LeafIssueWorkflowProfile, LeafIssuePolicy]:
    return registry.resolve_issue(issue, profile_resolver=profile_resolver)


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
        _validate_policy_evidence(policy, evidence, leaf_contract=issue)
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
    evidence = contract_evidence if contract_evidence is not None else check.evidence
    _validate_policy_evidence(policy, evidence, leaf_contract=issue)
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
    evidence = contract_evidence if contract_evidence is not None else check.evidence
    _validate_policy_evidence(policy, evidence, leaf_contract=issue)
    candidate = _coerce_evidence(
        policy.validate_candidate(execution_context, issue, evidence)
    )
    _validate_policy_evidence(
        policy,
        candidate,
        stage="candidate",
        leaf_contract=issue,
    )
    expected_kind = getattr(policy, "candidate_kind", candidate.kind)
    if candidate.kind != expected_kind:
        raise ProfilePolicyError("policy returned evidence for the wrong stage")
    return candidate


@dataclass(frozen=True)
class ProfileReviewResult:
    """Generic Review-stage output, including the optional workspace artifact."""

    evidence: ProfileEvidenceRecord | None
    artifact: Mapping[str, Any] | None = None
    profile_evidence: ProfileEvidenceEnvelope | None = None


@dataclass(frozen=True)
class ProfileCompletionResult:
    """Generic completion-stage output and its data-only effect intent."""

    evidence: ProfileEvidenceRecord | None
    effect: ProfileEffectDescriptor | None = None
    profile_evidence: ProfileEvidenceEnvelope | None = None


def _policy_context(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
    *,
    context: PolicyContext | None = None,
    **updates: Any,
) -> PolicyContext:
    if context is not None:
        values = {**context.__dict__, **updates}
        return PolicyContext(**values)
    return PolicyContext(profile=profile, issue=issue, **updates)


def build_profile_review_artifact(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
    review_input: Mapping[str, Any],
    *,
    repo_root: Path | None,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
) -> Mapping[str, Any] | None:
    """Ask the selected policy for artifact semantics inside a review clone."""
    policy = resolve_profile_policy(profile, registry=registry)
    method = getattr(policy, "review_artifact", None)
    if not callable(method):
        return None
    context = PolicyContext(
        profile=profile,
        phase="review",
        issue=issue,
        repo_root=repo_root,
        review_identity=(
            review_input.get("identity")
            if isinstance(review_input.get("identity"), Mapping)
            else review_input
        ),
    )
    value = method(context, review_input)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ProfilePolicyError("policy returned malformed Review artifact")
    return dict(value)


def validate_profile_review(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
    review_input: Mapping[str, Any],
    *,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
    context: PolicyContext | None = None,
) -> ProfileReviewResult:
    """Dispatch the generic Review capability without profile switches."""
    if review_input.get(
        "research_outcome"
    ) is not None and not profile_research_outcome_supported(profile):
        raise ProfilePolicyError(
            "--research-outcome is supported only for Research Issues"
        )
    policy = resolve_profile_policy(profile, registry=registry)
    execution_context = _policy_context(
        profile,
        issue,
        context=context,
        phase="review",
        review_identity=(
            review_input.get("identity")
            if isinstance(review_input.get("identity"), Mapping)
            else review_input
        ),
        review_verdict=review_input.get("verdict"),
        research_outcome=review_input.get("research_outcome"),
    )
    normalized_input = dict(review_input)
    artifact = normalized_input.get("artifact")
    identity = normalized_input.get("identity")
    if not isinstance(artifact, Mapping) and isinstance(identity, Mapping):
        identity_artifact = identity.get("research_artifact")
        if isinstance(identity_artifact, Mapping):
            artifact = identity_artifact
            normalized_input["artifact"] = dict(identity_artifact)
    if not isinstance(artifact, Mapping):
        artifact = build_profile_review_artifact(
            profile,
            issue,
            normalized_input,
            repo_root=execution_context.repo_root,
            registry=registry,
        )
        if artifact is not None:
            normalized_input["artifact"] = dict(artifact)
    method = getattr(policy, "validate_review", None)
    evidence: ProfileEvidenceRecord | None = None
    if callable(method):
        evidence = _coerce_evidence(method(execution_context, issue, normalized_input))
        _validate_policy_evidence(policy, evidence, stage="review", leaf_contract=issue)
    artifact = normalized_input.get("artifact")
    check = validate_profile_contract(
        profile, issue, registry=registry, context=execution_context
    )
    if not check.valid or check.evidence is None:
        raise ProfilePolicyError(check.failure_reason)
    envelope = ProfileEvidenceEnvelope(
        profile_id=profile.profile_id,
        contract=check.evidence,
        review=evidence,
    ).validated(registry, leaf_contract=issue)
    return ProfileReviewResult(
        evidence=evidence,
        artifact=dict(artifact) if isinstance(artifact, Mapping) else None,
        profile_evidence=envelope,
    )


def validate_profile_completion(
    profile: LeafIssueWorkflowProfile,
    issue: Mapping[str, Any],
    completion_input: Mapping[str, Any],
    *,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
    context: PolicyContext | None = None,
) -> ProfileCompletionResult:
    """Dispatch and validate one profile's generic completion capability."""
    policy = resolve_profile_policy(profile, registry=registry)
    execution_context = _policy_context(
        profile,
        issue,
        context=context,
        phase="completion",
        # Completion policies may declare effect intent only.  Do not allow a
        # caller-provided command runner to cross the Kernel effect boundary.
        runner=None,
        review_record=completion_input.get("review_record")
        if isinstance(completion_input.get("review_record"), Mapping)
        else None,
        merged_pr=completion_input.get("merged_pr")
        if isinstance(completion_input.get("merged_pr"), Mapping)
        else None,
    )
    method = getattr(policy, "validate_completion", None)
    evidence: ProfileEvidenceRecord | None = None
    if callable(method):
        result = method(execution_context, issue, completion_input)
        if result is not None:
            evidence = _coerce_evidence(result)
            _validate_policy_evidence(
                policy, evidence, stage="completion", leaf_contract=issue
            )
    effect: ProfileEffectDescriptor | None = None
    if evidence is not None:
        raw_effect = evidence.payload.get("effect")
        if raw_effect is not None:
            if not isinstance(raw_effect, Mapping):
                raise ProfilePolicyError("completion effect descriptor is malformed")
            try:
                effect = ProfileEffectDescriptor(
                    effect_kind=raw_effect.get("effect_kind"),
                    schema_version=raw_effect.get("schema_version"),
                    parameters=raw_effect.get("parameters"),
                    postcondition=raw_effect.get("postcondition"),
                    receipt=raw_effect.get("receipt"),
                )
            except (ProfilePolicyError, TypeError, ValueError) as exc:
                raise ProfilePolicyError(str(exc)) from exc
    if evidence is None:
        # Completion is an optional capability.  Profiles without a completion
        # policy must not acquire or re-validate a profile-specific contract at
        # this generic lifecycle boundary.
        return ProfileCompletionResult(
            evidence=None, effect=None, profile_evidence=None
        )
    check = validate_profile_contract(
        profile, issue, registry=registry, context=execution_context
    )
    if not check.valid or check.evidence is None:
        raise ProfilePolicyError(check.failure_reason)
    review_record = completion_input.get("review_evidence")
    review_evidence = (
        _coerce_evidence(review_record) if review_record is not None else None
    )
    if review_evidence is not None:
        _validate_policy_evidence(
            policy, review_evidence, stage="review", leaf_contract=issue
        )
    envelope = ProfileEvidenceEnvelope(
        profile_id=profile.profile_id,
        contract=check.evidence,
        review=review_evidence,
        completion=evidence,
    ).validated(registry, leaf_contract=issue)
    return ProfileCompletionResult(
        evidence=evidence, effect=effect, profile_evidence=envelope
    )


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
    documentation_validation: Any = None,
    research_validation: Any = None,
    critical_outcome: Callable[[], dict[str, Any]] | None = None,
    issue: Mapping[str, Any] | None = None,
    registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
    repo_root: Path | None = None,
    runner: Any = None,
    services: Sequence[Any] = (),
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
        repo_root=repo_root,
        runner=runner,
        base_sha=base_sha,
        head_sha=head_sha,
        include_index=include_index,
        progress=progress,
        critical_outcome=run_critical if critical_outcome is not None else None,
        documentation_validation=(
            run_documentation if documentation_validation is not None else None
        ),
        research_validation=run_research if research_validation is not None else None,
        services=tuple(
            item
            for item in (*services, documentation_validation, research_validation)
            if item is not None
        ),
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
        structured_result = getattr(exc, "result", None)
        if isinstance(structured_result, Mapping):
            observed_result = dict(structured_result)
            if callable(failure_builder):
                failure = failure_builder(
                    context,
                    issue,
                    check.evidence,
                    observed_result,
                    str(exc),
                )
        envelope = ProfileEvidenceEnvelope(
            profile_id=profile.profile_id,
            contract=check.evidence,
            candidate=failure,
        ).validated(registry, leaf_contract=issue)
        legacy = getattr(policy, "legacy_result", lambda _result: {})(observed_result)
        raise ProfileGateFailure(str(exc), envelope, legacy_results=legacy) from exc

    envelope = ProfileEvidenceEnvelope(
        profile_id=profile.profile_id,
        contract=check.evidence,
        candidate=candidate,
    ).validated(registry, leaf_contract=issue)
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
