"""Canonical, provider-neutral Review vNext value contracts.

The contracts carry compact facts and references.  They deliberately do not
embed repository trees, complete command logs, reviewer prose, or provider
specific fields.  Callers can retrieve referenced evidence on demand.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import ClassVar, Final, Self, cast

from tracequant.domain import DomainValidationError

__all__ = [
    "ALWAYS_ON_SURFACES",
    "RISK_TRIGGERED_SURFACES",
    "AssuranceObligation",
    "AssuranceResult",
    "AssuranceStatus",
    "CandidateFinding",
    "ChangeMapEntry",
    "EvidenceReference",
    "FindingBlockingStatus",
    "FindingSeverity",
    "FindingVerificationStatus",
    "ReviewAuthorityIdentity",
    "ReviewAuthorityKind",
    "ReviewContractError",
    "ReviewEvidencePackage",
    "ReviewRiskProfile",
    "ReviewRunReceipt",
    "ReviewSurface",
    "ReviewSurfacePlan",
    "RunProvenance",
    "SkippedSurface",
    "TokenUsage",
    "VerifiedFinding",
]


type JsonValue = (
    None | bool | int | float | str | tuple[JsonValue, ...] | Mapping[str, JsonValue]
)

_SHA1_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


class ReviewContractError(DomainValidationError):
    """Raised when a Review contract violates its canonical invariants."""


def _require_exact_fields(
    value: object, *, expected: frozenset[str], model: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{model} serialized value must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{model} serialized field names must be strings")
    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"extra fields: {', '.join(sorted(extra))}")
        raise ReviewContractError(f"invalid {model} fields ({'; '.join(details)})")
    return cast(Mapping[str, object], value)


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value.strip():
        raise ReviewContractError(f"{field} must not be empty")
    return value


def _require_nonnegative_int(value: object, *, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    if value < 0:
        raise ReviewContractError(f"{field} must be non-negative")
    return value


def _require_positive_int(value: object, *, field: str) -> int:
    number = _require_nonnegative_int(value, field=field)
    if number == 0:
        raise ReviewContractError(f"{field} must be positive")
    return number


def _require_sha(value: object, *, field: str, length: int) -> str:
    text = _require_text(value, field=field)
    pattern = _SHA1_RE if length == 40 else _SHA256_RE
    if pattern.fullmatch(text) is None:
        raise ReviewContractError(f"{field} must be a lowercase {length}-character SHA")
    return text


def _coerce_enum[EnumT: StrEnum](
    value: object, enum_type: type[EnumT], *, field: str
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a supported string value")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ReviewContractError(f"{field} has unsupported value {value!r}") from error


def _deduplicate[T](values: Sequence[T], *, field: str) -> tuple[T, ...]:
    result: list[T] = []
    for value in values:
        if value in result:
            raise ReviewContractError(f"{field} must not contain duplicates")
        result.append(value)
    return tuple(result)


def _normalize_text_tuple(
    value: object, *, field: str, allow_empty: bool = False
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of strings")
    values = tuple(_require_text(item, field=field) for item in value)
    if not allow_empty and not values:
        raise ReviewContractError(f"{field} must not be empty")
    return _deduplicate(values, field=field)


def _normalize_enum_tuple[EnumT: StrEnum](
    value: object, enum_type: type[EnumT], *, field: str
) -> tuple[EnumT, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence")
    return _deduplicate(
        tuple(_coerce_enum(item, enum_type, field=field) for item in value),
        field=field,
    )


def _freeze_json(value: object, *, field: str = "value") -> JsonValue:
    if value is None or type(value) in (bool, int, str):
        return cast(JsonValue, value)
    if type(value) is float:
        if not isfinite(value):
            raise ReviewContractError(f"{field} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field} object keys must be strings")
            frozen[key] = _freeze_json(item, field=f"{field}.{key}")
        return cast(JsonValue, MappingProxyType(frozen))
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, field=f"{field}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(f"{field} must contain only JSON-compatible values")


def _freeze_object(value: object, *, field: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a JSON object")
    frozen = _freeze_json(value, field=field)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{field} must be a JSON object")
    return frozen


def _thaw_json(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _json_dict(value: Mapping[str, JsonValue]) -> dict[str, object]:
    thawed = _thaw_json(value)
    if not isinstance(thawed, dict):
        raise TypeError("expected a JSON object")
    return thawed


def _model_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


class ReviewSurface(StrEnum):
    """Canonical assurance surfaces shared by all Review profiles."""

    CONTRACT_CONFORMANCE = "contract_conformance"
    FUNCTIONAL_CORRECTNESS = "functional_correctness"
    STATE_TRANSITIONS = "state_transitions"
    ERROR_FAILURE_PATHS = "error_failure_paths"
    TESTS_VS_CLAIMS = "tests_vs_claims"
    ARCHITECTURE = "architecture"
    PERSISTENCE_ATOMICITY = "persistence_atomicity"
    COMPATIBILITY_MIGRATION = "compatibility_migration"
    CONCURRENCY = "concurrency"
    SECURITY = "security"
    RECOVERY_IDEMPOTENCY = "recovery_idempotency"
    ACCOUNTING_DOMAIN_INVARIANTS = "accounting_domain_invariants"


ALWAYS_ON_SURFACES: Final[tuple[ReviewSurface, ...]] = (
    ReviewSurface.CONTRACT_CONFORMANCE,
    ReviewSurface.FUNCTIONAL_CORRECTNESS,
    ReviewSurface.STATE_TRANSITIONS,
    ReviewSurface.ERROR_FAILURE_PATHS,
    ReviewSurface.TESTS_VS_CLAIMS,
)

RISK_TRIGGERED_SURFACES: Final[tuple[ReviewSurface, ...]] = (
    ReviewSurface.ARCHITECTURE,
    ReviewSurface.PERSISTENCE_ATOMICITY,
    ReviewSurface.COMPATIBILITY_MIGRATION,
    ReviewSurface.CONCURRENCY,
    ReviewSurface.SECURITY,
    ReviewSurface.RECOVERY_IDEMPOTENCY,
    ReviewSurface.ACCOUNTING_DOMAIN_INVARIANTS,
)


class ReviewAuthorityKind(StrEnum):
    """Whether a Review run is tied to a frozen fixture or live repository state."""

    FIXTURE = "fixture"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class ReviewAuthorityIdentity:
    """Immutable identity of the exact Review subject and its authority."""

    authority_kind: ReviewAuthorityKind
    repository: str
    task_number: int
    pull_request_number: int | None
    base_sha: str
    head_sha: str
    diff_sha256: str

    KIND: ClassVar[str] = "review-authority-identity"
    SCHEMA_VERSION: ClassVar[str] = "review-authority-identity.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authority_kind",
            _coerce_enum(
                self.authority_kind, ReviewAuthorityKind, field="authority_kind"
            ),
        )
        object.__setattr__(
            self, "repository", _require_text(self.repository, field="repository")
        )
        object.__setattr__(
            self,
            "task_number",
            _require_positive_int(self.task_number, field="task_number"),
        )
        if self.pull_request_number is not None:
            object.__setattr__(
                self,
                "pull_request_number",
                _require_positive_int(
                    self.pull_request_number, field="pull_request_number"
                ),
            )
        object.__setattr__(
            self, "base_sha", _require_sha(self.base_sha, field="base_sha", length=40)
        )
        object.__setattr__(
            self, "head_sha", _require_sha(self.head_sha, field="head_sha", length=40)
        )
        object.__setattr__(
            self,
            "diff_sha256",
            _require_sha(self.diff_sha256, field="diff_sha256", length=64),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "authority_kind": self.authority_kind.value,
            "repository": self.repository,
            "task_number": self.task_number,
            "pull_request_number": self.pull_request_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "diff_sha256": self.diff_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "authority_kind",
                    "repository",
                    "task_number",
                    "pull_request_number",
                    "base_sha",
                    "head_sha",
                    "diff_sha256",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "ReviewAuthorityIdentity kind or schema_version is invalid"
            )
        pull_request_number = fields["pull_request_number"]
        if pull_request_number is not None and type(pull_request_number) is not int:
            raise TypeError("pull_request_number must be an integer or null")
        return cls(
            authority_kind=_coerce_enum(
                fields["authority_kind"], ReviewAuthorityKind, field="authority_kind"
            ),
            repository=_require_text(fields["repository"], field="repository"),
            task_number=_require_positive_int(
                fields["task_number"], field="task_number"
            ),
            pull_request_number=pull_request_number,
            base_sha=_require_sha(fields["base_sha"], field="base_sha", length=40),
            head_sha=_require_sha(fields["head_sha"], field="head_sha", length=40),
            diff_sha256=_require_sha(
                fields["diff_sha256"], field="diff_sha256", length=64
            ),
        )


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """Compact pointer to retrievable evidence, never the evidence payload."""

    reference_id: str
    kind: str
    locator: str
    summary: str
    digest_sha256: str | None = None

    KIND: ClassVar[str] = "evidence-reference"
    SCHEMA_VERSION: ClassVar[str] = "evidence-reference.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("reference_id", "kind", "locator", "summary"):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field=field_name),
            )
        if self.digest_sha256 is not None:
            object.__setattr__(
                self,
                "digest_sha256",
                _require_sha(self.digest_sha256, field="digest_sha256", length=64),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "reference_id": self.reference_id,
            "evidence_kind": self.kind,
            "locator": self.locator,
            "summary": self.summary,
            "digest_sha256": self.digest_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "reference_id",
                    "evidence_kind",
                    "locator",
                    "summary",
                    "digest_sha256",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "EvidenceReference kind or schema_version is invalid"
            )
        digest = fields["digest_sha256"]
        if digest is not None and not isinstance(digest, str):
            raise TypeError("digest_sha256 must be a string or null")
        return cls(
            reference_id=_require_text(fields["reference_id"], field="reference_id"),
            kind=_require_text(fields["evidence_kind"], field="kind"),
            locator=_require_text(fields["locator"], field="locator"),
            summary=_require_text(fields["summary"], field="summary"),
            digest_sha256=digest,
        )


@dataclass(frozen=True, slots=True)
class ChangeMapEntry:
    """One high-signal changed-location summary."""

    path: str
    change_kind: str
    locations: tuple[str, ...]
    summary: str

    KIND: ClassVar[str] = "change-map-entry"
    SCHEMA_VERSION: ClassVar[str] = "change-map-entry.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_text(self.path, field="path"))
        object.__setattr__(
            self, "change_kind", _require_text(self.change_kind, field="change_kind")
        )
        object.__setattr__(
            self,
            "locations",
            _normalize_text_tuple(self.locations, field="locations", allow_empty=True),
        )
        object.__setattr__(
            self, "summary", _require_text(self.summary, field="summary")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "path": self.path,
            "change_kind": self.change_kind,
            "locations": list(self.locations),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "path",
                    "change_kind",
                    "locations",
                    "summary",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "ChangeMapEntry kind or schema_version is invalid"
            )
        return cls(
            path=_require_text(fields["path"], field="path"),
            change_kind=_require_text(fields["change_kind"], field="change_kind"),
            locations=_normalize_text_tuple(
                fields["locations"], field="locations", allow_empty=True
            ),
            summary=_require_text(fields["summary"], field="summary"),
        )


@dataclass(frozen=True, slots=True)
class ReviewRiskProfile:
    """Deterministic risk facts and conservative surface escalation."""

    deterministic_facts: Mapping[str, JsonValue]
    triggered_surfaces: tuple[ReviewSurface, ...]
    semantic_escalation_requests: tuple[ReviewSurface, ...] = ()

    KIND: ClassVar[str] = "review-risk-profile"
    SCHEMA_VERSION: ClassVar[str] = "review-risk-profile.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "deterministic_facts",
            _freeze_object(self.deterministic_facts, field="deterministic_facts"),
        )
        object.__setattr__(
            self,
            "triggered_surfaces",
            _normalize_enum_tuple(
                self.triggered_surfaces, ReviewSurface, field="triggered_surfaces"
            ),
        )
        object.__setattr__(
            self,
            "semantic_escalation_requests",
            _normalize_enum_tuple(
                self.semantic_escalation_requests,
                ReviewSurface,
                field="semantic_escalation_requests",
            ),
        )

    @property
    def deterministic_required_surfaces(self) -> tuple[ReviewSurface, ...]:
        """Alias emphasizing that triggered surfaces cannot be removed by a Reviewer."""
        return self.triggered_surfaces

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "deterministic_facts": _json_dict(self.deterministic_facts),
            "triggered_surfaces": [
                surface.value for surface in self.triggered_surfaces
            ],
            "semantic_escalation_requests": [
                surface.value for surface in self.semantic_escalation_requests
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "deterministic_facts",
                    "triggered_surfaces",
                    "semantic_escalation_requests",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "ReviewRiskProfile kind or schema_version is invalid"
            )
        return cls(
            deterministic_facts=_freeze_object(
                fields["deterministic_facts"], field="deterministic_facts"
            ),
            triggered_surfaces=_normalize_enum_tuple(
                fields["triggered_surfaces"], ReviewSurface, field="triggered_surfaces"
            ),
            semantic_escalation_requests=_normalize_enum_tuple(
                fields["semantic_escalation_requests"],
                ReviewSurface,
                field="semantic_escalation_requests",
            ),
        )


@dataclass(frozen=True, slots=True)
class SkippedSurface:
    """A surface explicitly skipped with an auditable reason."""

    surface: ReviewSurface
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "surface", _coerce_enum(self.surface, ReviewSurface, field="surface")
        )
        object.__setattr__(self, "reason", _require_text(self.reason, field="reason"))


@dataclass(frozen=True, slots=True)
class ReviewSurfacePlan:
    """Mechanical required/covered/skipped surface accounting."""

    required: tuple[ReviewSurface, ...] = ALWAYS_ON_SURFACES
    covered: tuple[ReviewSurface, ...] = ()
    skipped_with_reason: Mapping[str, str] | Sequence[SkippedSurface] = ()
    risk_triggered: tuple[ReviewSurface, ...] = ()
    semantic_escalation_requests: tuple[ReviewSurface, ...] = ()

    KIND: ClassVar[str] = "review-surface-plan"
    SCHEMA_VERSION: ClassVar[str] = "review-surface-plan.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        required = _normalize_enum_tuple(self.required, ReviewSurface, field="required")
        covered = _normalize_enum_tuple(self.covered, ReviewSurface, field="covered")
        risk_triggered = _normalize_enum_tuple(
            self.risk_triggered, ReviewSurface, field="risk_triggered"
        )
        semantic_escalations = _normalize_enum_tuple(
            self.semantic_escalation_requests,
            ReviewSurface,
            field="semantic_escalation_requests",
        )
        missing_always_on = [
            surface.value for surface in ALWAYS_ON_SURFACES if surface not in required
        ]
        if missing_always_on:
            raise ReviewContractError(
                "required surfaces must include always-on surfaces: "
                + ", ".join(missing_always_on)
            )
        if any(surface not in required for surface in risk_triggered):
            raise ReviewContractError("every risk-triggered surface must be required")
        skipped = self._normalize_skipped(self.skipped_with_reason)
        if set(covered) & set(skipped):
            raise ReviewContractError("a surface cannot be both covered and skipped")
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "covered", covered)
        object.__setattr__(self, "risk_triggered", risk_triggered)
        object.__setattr__(self, "semantic_escalation_requests", semantic_escalations)
        object.__setattr__(self, "skipped_with_reason", MappingProxyType(skipped))

    @staticmethod
    def _normalize_skipped(
        value: Mapping[str, str] | Sequence[SkippedSurface],
    ) -> dict[str, str]:
        if isinstance(value, Mapping):
            result: dict[str, str] = {}
            for surface, reason in value.items():
                normalized_surface = _coerce_enum(
                    surface, ReviewSurface, field="skipped surface"
                )
                if normalized_surface.value in result:
                    raise ReviewContractError(
                        "skipped surfaces must not contain duplicates"
                    )
                result[normalized_surface.value] = _require_text(
                    reason, field="skip reason"
                )
            return result
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise TypeError("skipped_with_reason must be a mapping or sequence")
        result = {}
        for item in value:
            if not isinstance(item, SkippedSurface):
                raise TypeError(
                    "skipped_with_reason sequence must contain SkippedSurface values"
                )
            if item.surface.value in result:
                raise ReviewContractError(
                    "skipped surfaces must not contain duplicates"
                )
            result[item.surface.value] = item.reason
        return result

    @property
    def missing_required(self) -> tuple[ReviewSurface, ...]:
        return tuple(
            surface for surface in self.required if surface not in self.covered
        )

    @property
    def is_complete(self) -> bool:
        return not self.missing_required

    @property
    def coverage_status(self) -> str:
        return "complete" if self.is_complete else "incomplete"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "required": [surface.value for surface in self.required],
            "covered": [surface.value for surface in self.covered],
            "skipped_with_reason": dict(
                cast(Mapping[str, str], self.skipped_with_reason)
            ),
            "risk_triggered": [surface.value for surface in self.risk_triggered],
            "semantic_escalation_requests": [
                surface.value for surface in self.semantic_escalation_requests
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "required",
                    "covered",
                    "skipped_with_reason",
                    "risk_triggered",
                    "semantic_escalation_requests",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "ReviewSurfacePlan kind or schema_version is invalid"
            )
        skipped = fields["skipped_with_reason"]
        if not isinstance(skipped, Mapping):
            raise TypeError("skipped_with_reason must be a JSON object")
        return cls(
            required=_normalize_enum_tuple(
                fields["required"], ReviewSurface, field="required"
            ),
            covered=_normalize_enum_tuple(
                fields["covered"], ReviewSurface, field="covered"
            ),
            skipped_with_reason=cast(Mapping[str, str], skipped),
            risk_triggered=_normalize_enum_tuple(
                fields["risk_triggered"], ReviewSurface, field="risk_triggered"
            ),
            semantic_escalation_requests=_normalize_enum_tuple(
                fields["semantic_escalation_requests"],
                ReviewSurface,
                field="semantic_escalation_requests",
            ),
        )


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """Origin identity retained on every candidate finding."""

    run_id: str
    authority: ReviewAuthorityIdentity
    harness_id: str
    protocol_id: str

    KIND: ClassVar[str] = "review-run-provenance"
    SCHEMA_VERSION: ClassVar[str] = "review-run-provenance.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_text(self.run_id, field="run_id"))
        if not isinstance(self.authority, ReviewAuthorityIdentity):
            raise TypeError("authority must be a ReviewAuthorityIdentity")
        object.__setattr__(
            self, "harness_id", _require_text(self.harness_id, field="harness_id")
        )
        object.__setattr__(
            self, "protocol_id", _require_text(self.protocol_id, field="protocol_id")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "run_id": self.run_id,
            "authority": self.authority.to_dict(),
            "harness_id": self.harness_id,
            "protocol_id": self.protocol_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "run_id",
                    "authority",
                    "harness_id",
                    "protocol_id",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError("RunProvenance kind or schema_version is invalid")
        return cls(
            run_id=_require_text(fields["run_id"], field="run_id"),
            authority=ReviewAuthorityIdentity.from_dict(fields["authority"]),
            harness_id=_require_text(fields["harness_id"], field="harness_id"),
            protocol_id=_require_text(fields["protocol_id"], field="protocol_id"),
        )


def _normalize_provenance(value: object, *, field: str) -> tuple[RunProvenance, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of RunProvenance values")
    result = tuple(
        item if isinstance(item, RunProvenance) else RunProvenance.from_dict(item)
        for item in value
    )
    if not result:
        raise ReviewContractError(f"{field} must not be empty")
    _deduplicate(tuple(item.run_id for item in result), field=field)
    return result


def _normalize_references(
    value: object, *, field: str
) -> tuple[EvidenceReference, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a sequence of EvidenceReference values")
    result = tuple(
        item
        if isinstance(item, EvidenceReference)
        else EvidenceReference.from_dict(item)
        for item in value
    )
    if not result:
        raise ReviewContractError(f"{field} must not be empty")
    _deduplicate(tuple(item.reference_id for item in result), field=field)
    return result


def _normalize_finding_refs(
    value: object, *, field: str, allow_empty: bool = False
) -> tuple[str, ...]:
    refs = _normalize_text_tuple(value, field=field, allow_empty=allow_empty)
    return refs


@dataclass(frozen=True, slots=True)
class CandidateFinding:
    """A reported claim awaiting independent verification and classification."""

    finding_id: str
    surface: ReviewSurface
    claim: str
    affected_locations: tuple[str, ...]
    contract_invariant: str
    failure_scenario: str
    evidence_refs: tuple[str, ...]
    originating_runs: tuple[RunProvenance, ...]

    KIND: ClassVar[str] = "candidate-finding"
    SCHEMA_VERSION: ClassVar[str] = "candidate-finding.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "finding_id", _require_text(self.finding_id, field="finding_id")
        )
        object.__setattr__(
            self, "surface", _coerce_enum(self.surface, ReviewSurface, field="surface")
        )
        object.__setattr__(self, "claim", _require_text(self.claim, field="claim"))
        object.__setattr__(
            self,
            "affected_locations",
            _normalize_text_tuple(self.affected_locations, field="affected_locations"),
        )
        object.__setattr__(
            self,
            "contract_invariant",
            _require_text(self.contract_invariant, field="contract_invariant"),
        )
        object.__setattr__(
            self,
            "failure_scenario",
            _require_text(self.failure_scenario, field="failure_scenario"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_finding_refs(self.evidence_refs, field="evidence_refs"),
        )
        object.__setattr__(
            self,
            "originating_runs",
            _normalize_provenance(self.originating_runs, field="originating_runs"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "finding_id": self.finding_id,
            "surface": self.surface.value,
            "claim": self.claim,
            "affected_locations": list(self.affected_locations),
            "contract_invariant": self.contract_invariant,
            "failure_scenario": self.failure_scenario,
            "evidence_refs": list(self.evidence_refs),
            "originating_runs": [run.to_dict() for run in self.originating_runs],
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "finding_id",
                    "surface",
                    "claim",
                    "affected_locations",
                    "contract_invariant",
                    "failure_scenario",
                    "evidence_refs",
                    "originating_runs",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "CandidateFinding kind or schema_version is invalid"
            )
        return cls(
            finding_id=_require_text(fields["finding_id"], field="finding_id"),
            surface=_coerce_enum(fields["surface"], ReviewSurface, field="surface"),
            claim=_require_text(fields["claim"], field="claim"),
            affected_locations=_normalize_text_tuple(
                fields["affected_locations"], field="affected_locations"
            ),
            contract_invariant=_require_text(
                fields["contract_invariant"], field="contract_invariant"
            ),
            failure_scenario=_require_text(
                fields["failure_scenario"], field="failure_scenario"
            ),
            evidence_refs=_normalize_finding_refs(
                fields["evidence_refs"], field="evidence_refs"
            ),
            originating_runs=_normalize_provenance(
                fields["originating_runs"], field="originating_runs"
            ),
        )


class FindingVerificationStatus(StrEnum):
    """Independent disposition of a candidate finding."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class FindingSeverity(StrEnum):
    """Severity assigned only after verification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingBlockingStatus(StrEnum):
    """Explicit blocker classification, separate from candidate status."""

    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"
    NOT_ASSESSED = "not_assessed"


@dataclass(frozen=True, slots=True)
class VerifiedFinding:
    """A candidate finding plus explicit independent verification facts."""

    finding_id: str
    surface: ReviewSurface
    claim: str
    affected_locations: tuple[str, ...]
    contract_invariant: str
    failure_scenario: str
    evidence_refs: tuple[str, ...]
    originating_runs: tuple[RunProvenance, ...]
    verification_status: FindingVerificationStatus
    verification_evidence_refs: tuple[str, ...]
    severity: FindingSeverity
    blocking_status: FindingBlockingStatus

    KIND: ClassVar[str] = "verified-finding"
    SCHEMA_VERSION: ClassVar[str] = "verified-finding.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        candidate = CandidateFinding(
            finding_id=self.finding_id,
            surface=self.surface,
            claim=self.claim,
            affected_locations=self.affected_locations,
            contract_invariant=self.contract_invariant,
            failure_scenario=self.failure_scenario,
            evidence_refs=self.evidence_refs,
            originating_runs=self.originating_runs,
        )
        for field_name in (
            "finding_id",
            "surface",
            "claim",
            "affected_locations",
            "contract_invariant",
            "failure_scenario",
            "evidence_refs",
            "originating_runs",
        ):
            object.__setattr__(self, field_name, getattr(candidate, field_name))
        object.__setattr__(
            self,
            "verification_status",
            _coerce_enum(
                self.verification_status,
                FindingVerificationStatus,
                field="verification_status",
            ),
        )
        object.__setattr__(
            self,
            "verification_evidence_refs",
            _normalize_finding_refs(
                self.verification_evidence_refs,
                field="verification_evidence_refs",
            ),
        )
        object.__setattr__(
            self,
            "severity",
            _coerce_enum(self.severity, FindingSeverity, field="severity"),
        )
        object.__setattr__(
            self,
            "blocking_status",
            _coerce_enum(
                self.blocking_status,
                FindingBlockingStatus,
                field="blocking_status",
            ),
        )
        if self.verification_status is FindingVerificationStatus.CONFIRMED:
            if self.blocking_status is FindingBlockingStatus.NOT_ASSESSED:
                raise ReviewContractError(
                    "confirmed finding requires an explicit blocking status"
                )
        elif self.blocking_status is not FindingBlockingStatus.NOT_ASSESSED:
            raise ReviewContractError(
                "only a confirmed finding may have a blocking or non-blocking status"
            )

    @classmethod
    def from_candidate(
        cls,
        candidate: CandidateFinding,
        *,
        verification_status: FindingVerificationStatus,
        verification_evidence_refs: tuple[str, ...],
        severity: FindingSeverity,
        blocking_status: FindingBlockingStatus,
    ) -> Self:
        if not isinstance(candidate, CandidateFinding):
            raise TypeError("candidate must be a CandidateFinding")
        return cls(
            finding_id=candidate.finding_id,
            surface=candidate.surface,
            claim=candidate.claim,
            affected_locations=candidate.affected_locations,
            contract_invariant=candidate.contract_invariant,
            failure_scenario=candidate.failure_scenario,
            evidence_refs=candidate.evidence_refs,
            originating_runs=candidate.originating_runs,
            verification_status=verification_status,
            verification_evidence_refs=verification_evidence_refs,
            severity=severity,
            blocking_status=blocking_status,
        )

    @property
    def candidate(self) -> CandidateFinding:
        return CandidateFinding(
            finding_id=self.finding_id,
            surface=self.surface,
            claim=self.claim,
            affected_locations=self.affected_locations,
            contract_invariant=self.contract_invariant,
            failure_scenario=self.failure_scenario,
            evidence_refs=self.evidence_refs,
            originating_runs=self.originating_runs,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "finding_id": self.finding_id,
            "surface": self.surface.value,
            "claim": self.claim,
            "affected_locations": list(self.affected_locations),
            "contract_invariant": self.contract_invariant,
            "failure_scenario": self.failure_scenario,
            "evidence_refs": list(self.evidence_refs),
            "originating_runs": [run.to_dict() for run in self.originating_runs],
            "verification_status": self.verification_status.value,
            "verification_evidence_refs": list(self.verification_evidence_refs),
            "severity": self.severity.value,
            "blocking_status": self.blocking_status.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "finding_id",
                    "surface",
                    "claim",
                    "affected_locations",
                    "contract_invariant",
                    "failure_scenario",
                    "evidence_refs",
                    "originating_runs",
                    "verification_status",
                    "verification_evidence_refs",
                    "severity",
                    "blocking_status",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "VerifiedFinding kind or schema_version is invalid"
            )
        return cls(
            finding_id=_require_text(fields["finding_id"], field="finding_id"),
            surface=_coerce_enum(fields["surface"], ReviewSurface, field="surface"),
            claim=_require_text(fields["claim"], field="claim"),
            affected_locations=_normalize_text_tuple(
                fields["affected_locations"], field="affected_locations"
            ),
            contract_invariant=_require_text(
                fields["contract_invariant"], field="contract_invariant"
            ),
            failure_scenario=_require_text(
                fields["failure_scenario"], field="failure_scenario"
            ),
            evidence_refs=_normalize_finding_refs(
                fields["evidence_refs"], field="evidence_refs"
            ),
            originating_runs=_normalize_provenance(
                fields["originating_runs"], field="originating_runs"
            ),
            verification_status=_coerce_enum(
                fields["verification_status"],
                FindingVerificationStatus,
                field="verification_status",
            ),
            verification_evidence_refs=_normalize_finding_refs(
                fields["verification_evidence_refs"],
                field="verification_evidence_refs",
            ),
            severity=_coerce_enum(
                fields["severity"], FindingSeverity, field="severity"
            ),
            blocking_status=_coerce_enum(
                fields["blocking_status"],
                FindingBlockingStatus,
                field="blocking_status",
            ),
        )


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Bounded token accounting attached to a Review run."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    KIND: ClassVar[str] = "review-token-usage"
    SCHEMA_VERSION: ClassVar[str] = "review-token-usage.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("input_tokens", "output_tokens", "total_tokens"):
            object.__setattr__(
                self,
                field_name,
                _require_nonnegative_int(getattr(self, field_name), field=field_name),
            )
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ReviewContractError(
                "total_tokens must equal input_tokens + output_tokens"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError("TokenUsage kind or schema_version is invalid")
        return cls(
            input_tokens=_require_nonnegative_int(
                fields["input_tokens"], field="input_tokens"
            ),
            output_tokens=_require_nonnegative_int(
                fields["output_tokens"], field="output_tokens"
            ),
            total_tokens=_require_nonnegative_int(
                fields["total_tokens"], field="total_tokens"
            ),
        )


class AssuranceStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class AssuranceObligation:
    """A declared assurance obligation and its relevant required surfaces."""

    obligation_id: str
    description: str
    required_surfaces: tuple[ReviewSurface, ...]

    KIND: ClassVar[str] = "assurance-obligation"
    SCHEMA_VERSION: ClassVar[str] = "assurance-obligation.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_id",
            _require_text(self.obligation_id, field="obligation_id"),
        )
        object.__setattr__(
            self, "description", _require_text(self.description, field="description")
        )
        object.__setattr__(
            self,
            "required_surfaces",
            _normalize_enum_tuple(
                self.required_surfaces, ReviewSurface, field="required_surfaces"
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "obligation_id": self.obligation_id,
            "description": self.description,
            "required_surfaces": [surface.value for surface in self.required_surfaces],
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "obligation_id",
                    "description",
                    "required_surfaces",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "AssuranceObligation kind or schema_version is invalid"
            )
        return cls(
            obligation_id=_require_text(fields["obligation_id"], field="obligation_id"),
            description=_require_text(fields["description"], field="description"),
            required_surfaces=_normalize_enum_tuple(
                fields["required_surfaces"], ReviewSurface, field="required_surfaces"
            ),
        )


@dataclass(frozen=True, slots=True)
class AssuranceResult:
    """Result and retrievable evidence for one assurance obligation."""

    obligation_id: str
    status: AssuranceStatus
    evidence_refs: tuple[str, ...]
    summary: str

    KIND: ClassVar[str] = "assurance-result"
    SCHEMA_VERSION: ClassVar[str] = "assurance-result.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "obligation_id",
            _require_text(self.obligation_id, field="obligation_id"),
        )
        object.__setattr__(
            self, "status", _coerce_enum(self.status, AssuranceStatus, field="status")
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _normalize_finding_refs(
                self.evidence_refs, field="evidence_refs", allow_empty=True
            ),
        )
        object.__setattr__(
            self, "summary", _require_text(self.summary, field="summary")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "obligation_id": self.obligation_id,
            "status": self.status.value,
            "evidence_refs": list(self.evidence_refs),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "obligation_id",
                    "status",
                    "evidence_refs",
                    "summary",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "AssuranceResult kind or schema_version is invalid"
            )
        return cls(
            obligation_id=_require_text(fields["obligation_id"], field="obligation_id"),
            status=_coerce_enum(fields["status"], AssuranceStatus, field="status"),
            evidence_refs=_normalize_finding_refs(
                fields["evidence_refs"], field="evidence_refs", allow_empty=True
            ),
            summary=_require_text(fields["summary"], field="summary"),
        )


@dataclass(frozen=True, slots=True)
class ReviewEvidencePackage:
    """Canonical high-signal package exchanged by Review vNext components."""

    summary: str
    authority: ReviewAuthorityIdentity
    task_contract: Mapping[str, JsonValue]
    change_map: tuple[ChangeMapEntry, ...]
    deterministic_evidence: tuple[EvidenceReference, ...]
    risk_profile: ReviewRiskProfile
    surface_plan: ReviewSurfacePlan
    targeted_retrieval_references: tuple[EvidenceReference, ...]

    KIND: ClassVar[str] = "review-evidence-package"
    SCHEMA_VERSION: ClassVar[str] = "review-evidence-package.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "summary", _require_text(self.summary, field="summary")
        )
        if not isinstance(self.authority, ReviewAuthorityIdentity):
            raise TypeError("authority must be a ReviewAuthorityIdentity")
        object.__setattr__(
            self,
            "task_contract",
            _freeze_object(self.task_contract, field="task_contract"),
        )
        change_map = tuple(
            item if isinstance(item, ChangeMapEntry) else ChangeMapEntry.from_dict(item)
            for item in self.change_map
        )
        if not change_map:
            raise ReviewContractError("change_map must not be empty")
        object.__setattr__(self, "change_map", change_map)
        deterministic = _normalize_references(
            self.deterministic_evidence, field="deterministic_evidence"
        )
        retrieval = _normalize_references(
            self.targeted_retrieval_references,
            field="targeted_retrieval_references",
        )
        all_reference_ids = tuple(
            item.reference_id for item in (*deterministic, *retrieval)
        )
        _deduplicate(all_reference_ids, field="evidence reference IDs")
        object.__setattr__(self, "deterministic_evidence", deterministic)
        object.__setattr__(self, "targeted_retrieval_references", retrieval)
        if not isinstance(self.risk_profile, ReviewRiskProfile):
            raise TypeError("risk_profile must be a ReviewRiskProfile")
        if not isinstance(self.surface_plan, ReviewSurfacePlan):
            raise TypeError("surface_plan must be a ReviewSurfacePlan")
        if set(self.risk_profile.triggered_surfaces) != set(
            self.surface_plan.risk_triggered
        ):
            raise ReviewContractError(
                "surface plan risk_triggered must equal risk profile deterministic triggers"
            )
        if set(self.risk_profile.semantic_escalation_requests) != set(
            self.surface_plan.semantic_escalation_requests
        ):
            raise ReviewContractError(
                "surface plan semantic escalation requests must match risk profile"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "summary": self.summary,
            "authority": self.authority.to_dict(),
            "task_contract": _json_dict(self.task_contract),
            "change_map": [item.to_dict() for item in self.change_map],
            "deterministic_evidence": [
                item.to_dict() for item in self.deterministic_evidence
            ],
            "risk_profile": self.risk_profile.to_dict(),
            "surface_plan": self.surface_plan.to_dict(),
            "targeted_retrieval_references": [
                item.to_dict() for item in self.targeted_retrieval_references
            ],
        }

    def to_json(self) -> str:
        return _model_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "summary",
                    "authority",
                    "task_contract",
                    "change_map",
                    "deterministic_evidence",
                    "risk_profile",
                    "surface_plan",
                    "targeted_retrieval_references",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "ReviewEvidencePackage kind or schema_version is invalid"
            )
        return cls(
            summary=_require_text(fields["summary"], field="summary"),
            authority=ReviewAuthorityIdentity.from_dict(fields["authority"]),
            task_contract=_freeze_object(
                fields["task_contract"], field="task_contract"
            ),
            change_map=tuple(
                ChangeMapEntry.from_dict(item)
                for item in _require_sequence(fields["change_map"], field="change_map")
            ),
            deterministic_evidence=tuple(
                EvidenceReference.from_dict(item)
                for item in _require_sequence(
                    fields["deterministic_evidence"], field="deterministic_evidence"
                )
            ),
            risk_profile=ReviewRiskProfile.from_dict(fields["risk_profile"]),
            surface_plan=ReviewSurfacePlan.from_dict(fields["surface_plan"]),
            targeted_retrieval_references=tuple(
                EvidenceReference.from_dict(item)
                for item in _require_sequence(
                    fields["targeted_retrieval_references"],
                    field="targeted_retrieval_references",
                )
            ),
        )

    @classmethod
    def from_json(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError("ReviewEvidencePackage JSON value must be a string")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as error:
            raise ReviewContractError(
                "ReviewEvidencePackage JSON is invalid"
            ) from error
        return cls.from_dict(payload)


def _require_sequence(value: object, *, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} must be a JSON array")
    return value


@dataclass(frozen=True, slots=True)
class ReviewRunReceipt:
    """Auditable receipt for one fixture or live Review run."""

    run_id: str
    authority: ReviewAuthorityIdentity
    harness_config: Mapping[str, JsonValue]
    protocol_config: Mapping[str, JsonValue]
    model_config: Mapping[str, JsonValue]
    coverage: ReviewSurfacePlan
    candidate_findings: tuple[CandidateFinding, ...]
    verified_findings: tuple[VerifiedFinding, ...]
    token_usage: TokenUsage
    wall_clock_ms: int
    assurance_obligations: tuple[AssuranceObligation, ...]
    assurance_results: tuple[AssuranceResult, ...]

    KIND: ClassVar[str] = "review-run-receipt"
    SCHEMA_VERSION: ClassVar[str] = "review-run-receipt.v1"
    VERSION: ClassVar[str] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_text(self.run_id, field="run_id"))
        if not isinstance(self.authority, ReviewAuthorityIdentity):
            raise TypeError("authority must be a ReviewAuthorityIdentity")
        for field_name in ("harness_config", "protocol_config", "model_config"):
            object.__setattr__(
                self,
                field_name,
                _freeze_object(getattr(self, field_name), field=field_name),
            )
        if not isinstance(self.coverage, ReviewSurfacePlan):
            raise TypeError("coverage must be a ReviewSurfacePlan")
        object.__setattr__(
            self,
            "candidate_findings",
            self._normalize_candidates(self.candidate_findings),
        )
        object.__setattr__(
            self, "verified_findings", self._normalize_verified(self.verified_findings)
        )
        if not isinstance(self.token_usage, TokenUsage):
            raise TypeError("token_usage must be a TokenUsage")
        object.__setattr__(
            self,
            "wall_clock_ms",
            _require_nonnegative_int(self.wall_clock_ms, field="wall_clock_ms"),
        )
        obligations = tuple(
            item
            if isinstance(item, AssuranceObligation)
            else AssuranceObligation.from_dict(item)
            for item in self.assurance_obligations
        )
        results = tuple(
            item
            if isinstance(item, AssuranceResult)
            else AssuranceResult.from_dict(item)
            for item in self.assurance_results
        )
        _deduplicate(
            tuple(item.obligation_id for item in obligations),
            field="assurance_obligations",
        )
        _deduplicate(
            tuple(item.obligation_id for item in results), field="assurance_results"
        )
        if not set(item.obligation_id for item in results).issubset(
            {item.obligation_id for item in obligations}
        ):
            raise ReviewContractError(
                "assurance results must refer to declared obligations"
            )
        object.__setattr__(self, "assurance_obligations", obligations)
        object.__setattr__(self, "assurance_results", results)

    @staticmethod
    def _normalize_candidates(value: object) -> tuple[CandidateFinding, ...]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise TypeError("candidate_findings must be a sequence")
        result = tuple(
            item
            if isinstance(item, CandidateFinding)
            else CandidateFinding.from_dict(item)
            for item in value
        )
        _deduplicate(
            tuple(item.finding_id for item in result), field="candidate_findings"
        )
        return result

    @staticmethod
    def _normalize_verified(value: object) -> tuple[VerifiedFinding, ...]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise TypeError("verified_findings must be a sequence")
        result = tuple(
            item
            if isinstance(item, VerifiedFinding)
            else VerifiedFinding.from_dict(item)
            for item in value
        )
        _deduplicate(
            tuple(item.finding_id for item in result), field="verified_findings"
        )
        return result

    @property
    def coverage_complete(self) -> bool:
        return self.coverage.is_complete

    @property
    def review_complete(self) -> bool:
        results_by_id = {item.obligation_id: item for item in self.assurance_results}
        if not self.coverage_complete or len(results_by_id) != len(
            self.assurance_obligations
        ):
            return False
        return all(
            results_by_id[item.obligation_id].status
            in (AssuranceStatus.PASS, AssuranceStatus.NOT_APPLICABLE)
            for item in self.assurance_obligations
        )

    @property
    def review_status(self) -> str:
        return "complete" if self.review_complete else "incomplete"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "run_id": self.run_id,
            "authority": self.authority.to_dict(),
            "harness_config": _json_dict(self.harness_config),
            "protocol_config": _json_dict(self.protocol_config),
            "model_config": _json_dict(self.model_config),
            "coverage": self.coverage.to_dict(),
            "candidate_findings": [item.to_dict() for item in self.candidate_findings],
            "verified_findings": [item.to_dict() for item in self.verified_findings],
            "token_usage": self.token_usage.to_dict(),
            "wall_clock_ms": self.wall_clock_ms,
            "assurance_obligations": [
                item.to_dict() for item in self.assurance_obligations
            ],
            "assurance_results": [item.to_dict() for item in self.assurance_results],
            "review_status": self.review_status,
        }

    def to_json(self) -> str:
        return _model_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "kind",
                    "schema_version",
                    "run_id",
                    "authority",
                    "harness_config",
                    "protocol_config",
                    "model_config",
                    "coverage",
                    "candidate_findings",
                    "verified_findings",
                    "token_usage",
                    "wall_clock_ms",
                    "assurance_obligations",
                    "assurance_results",
                    "review_status",
                }
            ),
            model=cls.KIND,
        )
        if fields["kind"] != cls.KIND or fields["schema_version"] != cls.SCHEMA_VERSION:
            raise ReviewContractError(
                "ReviewRunReceipt kind or schema_version is invalid"
            )
        receipt = cls(
            run_id=_require_text(fields["run_id"], field="run_id"),
            authority=ReviewAuthorityIdentity.from_dict(fields["authority"]),
            harness_config=_freeze_object(
                fields["harness_config"], field="harness_config"
            ),
            protocol_config=_freeze_object(
                fields["protocol_config"], field="protocol_config"
            ),
            model_config=_freeze_object(fields["model_config"], field="model_config"),
            coverage=ReviewSurfacePlan.from_dict(fields["coverage"]),
            candidate_findings=tuple(
                CandidateFinding.from_dict(item)
                for item in _require_sequence(
                    fields["candidate_findings"], field="candidate_findings"
                )
            ),
            verified_findings=tuple(
                VerifiedFinding.from_dict(item)
                for item in _require_sequence(
                    fields["verified_findings"], field="verified_findings"
                )
            ),
            token_usage=TokenUsage.from_dict(fields["token_usage"]),
            wall_clock_ms=_require_nonnegative_int(
                fields["wall_clock_ms"], field="wall_clock_ms"
            ),
            assurance_obligations=tuple(
                AssuranceObligation.from_dict(item)
                for item in _require_sequence(
                    fields["assurance_obligations"], field="assurance_obligations"
                )
            ),
            assurance_results=tuple(
                AssuranceResult.from_dict(item)
                for item in _require_sequence(
                    fields["assurance_results"], field="assurance_results"
                )
            ),
        )
        if fields["review_status"] != receipt.review_status:
            raise ReviewContractError(
                "review_status does not match coverage and assurance results"
            )
        return receipt

    @classmethod
    def from_json(cls, value: str) -> Self:
        if not isinstance(value, str):
            raise TypeError("ReviewRunReceipt JSON value must be a string")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as error:
            raise ReviewContractError("ReviewRunReceipt JSON is invalid") from error
        return cls.from_dict(payload)
