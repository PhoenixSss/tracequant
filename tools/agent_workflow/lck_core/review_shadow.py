"""Finding-centric Review vNext shadow orchestration.

The shadow pipeline is deliberately downstream of the authoritative Review
result.  Discovery passes produce claims and coverage facts, the union stage
canonicalizes claims, and a fresh verifier produces independent dispositions.
Nothing in this module can mutate the production verdict, merge eligibility, or
authoritative Review state.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, Self

from tracequant.contracts import (
    ALWAYS_ON_SURFACES,
    CandidateFinding,
    EvidenceReference,
    FindingBlockingStatus,
    FindingSeverity,
    FindingVerificationStatus,
    ReviewAuthorityIdentity,
    ReviewContractError,
    ReviewEvidencePackage,
    ReviewSurface,
    ReviewSurfacePlan,
    RunProvenance,
    TokenUsage,
    VerifiedFinding,
)

SHADOW_PROTOCOL_ID: Final[str] = "tracequant-review-vnext-shadow"
SHADOW_PROTOCOL_VERSION: Final[str] = "v1"


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewContractError(f"{field} must not be empty")
    return value


def _unique_texts(value: Sequence[str], *, field: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in value:
        item = _require_text(item, field=field)
        if item in result:
            raise ReviewContractError(f"{field} must not contain duplicates")
        result.append(item)
    return tuple(result)


def _sum_tokens(values: Sequence[TokenUsage]) -> TokenUsage:
    return TokenUsage(
        input_tokens=sum(item.input_tokens for item in values),
        output_tokens=sum(item.output_tokens for item in values),
        total_tokens=sum(item.total_tokens for item in values),
    )


class VerificationMethod(StrEnum):
    """Evidence-first methods allowed for independent verification."""

    TARGETED_DETERMINISTIC_REPRODUCTION = "targeted_deterministic_reproduction"
    AUTHORITATIVE_CONTRACT_MISMATCH = "authoritative_contract_mismatch"
    CODE_STATE_COUNTEREXAMPLE = "code_state_counterexample"
    INDEPENDENT_ADJUDICATION = "independent_adjudication"


@dataclass(frozen=True, slots=True)
class DiscoveryPass:
    """One explicitly named structured discovery pass."""

    pass_id: str
    surfaces: tuple[ReviewSurface, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "pass_id", _require_text(self.pass_id, field="pass_id")
        )
        if not self.surfaces:
            raise ReviewContractError("discovery pass must declare surfaces")
        object.__setattr__(
            self,
            "surfaces",
            tuple(
                surface
                if isinstance(surface, ReviewSurface)
                else ReviewSurface(surface)
                for surface in self.surfaces
            ),
        )
        if len(set(self.surfaces)) != len(self.surfaces):
            raise ReviewContractError("discovery pass surfaces must be unique")


DEFAULT_DISCOVERY_PASSES: Final[tuple[DiscoveryPass, ...]] = (
    DiscoveryPass(
        pass_id="contract-functional-invariants",
        surfaces=(
            ReviewSurface.CONTRACT_CONFORMANCE,
            ReviewSurface.FUNCTIONAL_CORRECTNESS,
        ),
    ),
    DiscoveryPass(
        pass_id="state-failure-compatibility",
        surfaces=(
            ReviewSurface.STATE_TRANSITIONS,
            ReviewSurface.ERROR_FAILURE_PATHS,
            ReviewSurface.COMPATIBILITY_MIGRATION,
        ),
    ),
    DiscoveryPass(
        pass_id="tests-claims-architecture",
        surfaces=(ReviewSurface.TESTS_VS_CLAIMS, ReviewSurface.ARCHITECTURE),
    ),
)


@dataclass(frozen=True, slots=True)
class StructuredDiscoveryPlan:
    """The coverage contract for structured discovery."""

    passes: tuple[DiscoveryPass, ...] = DEFAULT_DISCOVERY_PASSES
    risk_triggered_surfaces: tuple[ReviewSurface, ...] = ()

    def __post_init__(self) -> None:
        if not self.passes:
            raise ReviewContractError("structured discovery plan must have passes")
        pass_ids = tuple(item.pass_id for item in self.passes)
        if len(set(pass_ids)) != len(pass_ids):
            raise ReviewContractError("structured discovery pass IDs must be unique")
        risk = tuple(
            item if isinstance(item, ReviewSurface) else ReviewSurface(item)
            for item in self.risk_triggered_surfaces
        )
        if len(set(risk)) != len(risk):
            raise ReviewContractError("risk-triggered surfaces must be unique")
        object.__setattr__(self, "risk_triggered_surfaces", risk)
        declared = set(self.declared_surfaces)
        if any(surface not in declared for surface in risk):
            raise ReviewContractError(
                "risk-triggered surfaces must be declared by a discovery pass"
            )
        missing = [
            surface.value for surface in ALWAYS_ON_SURFACES if surface not in declared
        ]
        for required in (
            ReviewSurface.COMPATIBILITY_MIGRATION,
            ReviewSurface.ARCHITECTURE,
        ):
            if required not in declared:
                missing.append(required.value)
        if missing:
            raise ReviewContractError(
                "structured discovery plan misses required surfaces: "
                + ", ".join(missing)
            )

    @property
    def declared_surfaces(self) -> tuple[ReviewSurface, ...]:
        result: list[ReviewSurface] = []
        for discovery_pass in self.passes:
            for surface in discovery_pass.surfaces:
                if surface not in result:
                    result.append(surface)
        return tuple(result)

    @property
    def required_surfaces(self) -> tuple[ReviewSurface, ...]:
        return self.declared_surfaces

    def pass_for(self, pass_id: str) -> DiscoveryPass:
        for discovery_pass in self.passes:
            if discovery_pass.pass_id == pass_id:
                return discovery_pass
        raise ReviewContractError(f"unknown discovery pass: {pass_id}")


def _coverage_reference(
    value: EvidenceReference | str, *, pass_id: str, index: int
) -> EvidenceReference:
    if isinstance(value, EvidenceReference):
        return value
    locator = _require_text(value, field="coverage evidence")
    return EvidenceReference(
        reference_id=f"{pass_id}:coverage:{index}",
        kind="surface-coverage",
        locator=locator,
        summary=f"Coverage evidence for discovery pass {pass_id}.",
    )


@dataclass(frozen=True, slots=True)
class DiscoveryPassResult:
    """A pass output: candidate claims plus explicit coverage evidence only."""

    pass_id: str
    covered_surfaces: tuple[ReviewSurface, ...]
    candidate_findings: tuple[CandidateFinding, ...] = ()
    coverage_evidence: tuple[EvidenceReference | str, ...] = ()
    planned_surfaces: tuple[ReviewSurface, ...] = ()
    token_usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(
            input_tokens=0, output_tokens=0, total_tokens=0
        )
    )
    wall_clock_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "pass_id", _require_text(self.pass_id, field="pass_id")
        )
        covered = tuple(
            item if isinstance(item, ReviewSurface) else ReviewSurface(item)
            for item in self.covered_surfaces
        )
        if not covered:
            raise ReviewContractError("discovery pass result must cover a surface")
        if len(set(covered)) != len(covered):
            raise ReviewContractError("covered surfaces must be unique")
        object.__setattr__(self, "covered_surfaces", covered)
        planned = tuple(
            item if isinstance(item, ReviewSurface) else ReviewSurface(item)
            for item in (self.planned_surfaces or covered)
        )
        if not set(covered).issubset(planned):
            raise ReviewContractError("covered surfaces must be planned surfaces")
        object.__setattr__(self, "planned_surfaces", planned)
        candidates = tuple(self.candidate_findings)
        if any(not isinstance(item, CandidateFinding) for item in candidates):
            raise TypeError("candidate_findings must contain CandidateFinding values")
        if any(item.surface not in covered for item in candidates):
            raise ReviewContractError(
                "candidate finding surface must be covered by its discovery pass"
            )
        object.__setattr__(self, "candidate_findings", candidates)
        evidence = tuple(
            _coverage_reference(item, pass_id=self.pass_id, index=index)
            for index, item in enumerate(self.coverage_evidence, start=1)
        )
        if not evidence:
            raise ReviewContractError("discovery pass result needs coverage evidence")
        ids = tuple(item.reference_id for item in evidence)
        if len(set(ids)) != len(ids):
            raise ReviewContractError("coverage evidence IDs must be unique")
        object.__setattr__(self, "coverage_evidence", evidence)
        if not isinstance(self.token_usage, TokenUsage):
            raise TypeError("token_usage must be TokenUsage")
        if type(self.wall_clock_ms) is not int or self.wall_clock_ms < 0:
            raise ReviewContractError("wall_clock_ms must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class FindingUnion:
    """Canonical union of all pass outputs with retained provenance."""

    candidate_findings: tuple[CandidateFinding, ...]
    source_count: int
    duplicate_count: int

    def __post_init__(self) -> None:
        if type(self.source_count) is not int or self.source_count < 0:
            raise ReviewContractError("source_count must be non-negative")
        if type(self.duplicate_count) is not int or self.duplicate_count < 0:
            raise ReviewContractError("duplicate_count must be non-negative")
        if self.source_count != len(self.candidate_findings) + self.duplicate_count:
            raise ReviewContractError("finding union counts do not reconcile")


def finding_fingerprint(candidate: CandidateFinding) -> str:
    """Return the stable semantic key used by canonical finding deduplication."""
    if not isinstance(candidate, CandidateFinding):
        raise TypeError("candidate must be a CandidateFinding")
    payload = {
        "surface": candidate.surface.value,
        "claim": " ".join(candidate.claim.split()),
        "affected_locations": sorted(candidate.affected_locations),
        "contract_invariant": " ".join(candidate.contract_invariant.split()),
        "failure_scenario": " ".join(candidate.failure_scenario.split()),
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _canonical_finding_id(fingerprint: str) -> str:
    return f"shadow-finding-{fingerprint[:24]}"


def union_candidate_findings(
    candidates: Sequence[CandidateFinding],
) -> FindingUnion:
    """Union and deduplicate candidates without dropping run provenance."""
    groups: dict[str, list[CandidateFinding]] = {}
    for candidate in candidates:
        if not isinstance(candidate, CandidateFinding):
            raise TypeError("candidates must contain CandidateFinding values")
        groups.setdefault(finding_fingerprint(candidate), []).append(candidate)

    canonical: list[CandidateFinding] = []
    duplicate_count = 0
    for fingerprint, group in groups.items():
        first = group[0]
        evidence_refs: list[str] = []
        provenance: dict[str, RunProvenance] = {}
        locations: list[str] = []
        for candidate in group:
            duplicate_count += int(candidate is not first)
            for reference in candidate.evidence_refs:
                if reference not in evidence_refs:
                    evidence_refs.append(reference)
            for location in candidate.affected_locations:
                if location not in locations:
                    locations.append(location)
            for run in candidate.originating_runs:
                prior = provenance.get(run.run_id)
                if prior is not None and prior != run:
                    raise ReviewContractError(
                        "duplicate finding has conflicting provenance for run "
                        + run.run_id
                    )
                provenance[run.run_id] = run
        canonical.append(
            CandidateFinding(
                finding_id=_canonical_finding_id(fingerprint),
                surface=first.surface,
                claim=first.claim,
                affected_locations=tuple(locations),
                contract_invariant=first.contract_invariant,
                failure_scenario=first.failure_scenario,
                evidence_refs=tuple(evidence_refs),
                originating_runs=tuple(provenance.values()),
            )
        )
    return FindingUnion(
        candidate_findings=tuple(canonical),
        source_count=len(candidates),
        duplicate_count=duplicate_count,
    )


@dataclass(frozen=True, slots=True)
class VerificationSubject:
    """The candidate claim exposed to a verifier, without social-proof data."""

    finding_id: str
    surface: ReviewSurface
    claim: str
    affected_locations: tuple[str, ...]
    contract_invariant: str
    failure_scenario: str
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_candidate(cls, candidate: CandidateFinding) -> Self:
        return cls(
            finding_id=candidate.finding_id,
            surface=candidate.surface,
            claim=candidate.claim,
            affected_locations=candidate.affected_locations,
            contract_invariant=candidate.contract_invariant,
            failure_scenario=candidate.failure_scenario,
            evidence_refs=candidate.evidence_refs,
        )

    def __post_init__(self) -> None:
        _require_text(self.finding_id, field="finding_id")
        if not isinstance(self.surface, ReviewSurface):
            object.__setattr__(self, "surface", ReviewSurface(self.surface))
        _require_text(self.claim, field="claim")
        _require_text(self.contract_invariant, field="contract_invariant")
        _require_text(self.failure_scenario, field="failure_scenario")
        object.__setattr__(
            self,
            "affected_locations",
            _unique_texts(self.affected_locations, field="affected_locations"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _unique_texts(self.evidence_refs, field="evidence_refs"),
        )


@dataclass(frozen=True, slots=True)
class IndependentVerificationRequest:
    """Fresh verifier context; it intentionally has no originating-run count."""

    context_id: str
    candidate: VerificationSubject
    evidence_package: ReviewEvidencePackage | None = None
    subject_root: Path | None = None

    def __post_init__(self) -> None:
        _require_text(self.context_id, field="verification context_id")
        if not isinstance(self.candidate, VerificationSubject):
            raise TypeError("candidate must be VerificationSubject")
        if self.subject_root is not None:
            object.__setattr__(self, "subject_root", self.subject_root.resolve())

    def to_dict(self) -> dict[str, object]:
        """Serialize only verifier inputs; provenance/social-proof is absent."""
        return {
            "context_id": self.context_id,
            "candidate": {
                "finding_id": self.candidate.finding_id,
                "surface": self.candidate.surface.value,
                "claim": self.candidate.claim,
                "affected_locations": list(self.candidate.affected_locations),
                "contract_invariant": self.candidate.contract_invariant,
                "failure_scenario": self.candidate.failure_scenario,
                "evidence_refs": list(self.candidate.evidence_refs),
            },
            "has_evidence_package": self.evidence_package is not None,
            "subject_root_supplied": self.subject_root is not None,
        }


@dataclass(frozen=True, slots=True)
class IndependentVerification:
    """Evidence-backed, independent disposition of one candidate claim."""

    status: FindingVerificationStatus
    verification_evidence_refs: tuple[str, ...]
    method: VerificationMethod
    falsification_attempted: bool
    severity: FindingSeverity | None = None
    blocking_status: FindingBlockingStatus | None = None
    token_usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(
            input_tokens=0, output_tokens=0, total_tokens=0
        )
    )
    wall_clock_ms: int = 0

    def __post_init__(self) -> None:
        status = (
            self.status
            if isinstance(self.status, FindingVerificationStatus)
            else FindingVerificationStatus(self.status)
        )
        method = (
            self.method
            if isinstance(self.method, VerificationMethod)
            else VerificationMethod(self.method)
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "method", method)
        object.__setattr__(
            self,
            "verification_evidence_refs",
            _unique_texts(
                self.verification_evidence_refs, field="verification_evidence_refs"
            ),
        )
        if self.falsification_attempted is not True:
            raise ReviewContractError(
                "independent verification must attempt falsification"
            )
        if self.severity is not None and not isinstance(self.severity, FindingSeverity):
            object.__setattr__(self, "severity", FindingSeverity(self.severity))
        if self.blocking_status is not None and not isinstance(
            self.blocking_status, FindingBlockingStatus
        ):
            object.__setattr__(
                self, "blocking_status", FindingBlockingStatus(self.blocking_status)
            )
        if status is FindingVerificationStatus.CONFIRMED:
            if self.severity is None or self.blocking_status in (
                None,
                FindingBlockingStatus.NOT_ASSESSED,
            ):
                raise ReviewContractError(
                    "confirmed verification requires severity and blocker status"
                )
        elif self.blocking_status not in (None, FindingBlockingStatus.NOT_ASSESSED):
            raise ReviewContractError("unconfirmed verification cannot be blocking")
        if not isinstance(self.token_usage, TokenUsage):
            raise TypeError("token_usage must be TokenUsage")
        if type(self.wall_clock_ms) is not int or self.wall_clock_ms < 0:
            raise ReviewContractError("wall_clock_ms must be a non-negative integer")


class IndependentVerifier(Protocol):
    """Callable verifier contract used by the shadow pipeline."""

    def __call__(
        self, request: IndependentVerificationRequest
    ) -> IndependentVerification: ...


@dataclass(frozen=True, slots=True)
class ProductionReviewState:
    """Immutable snapshot of the authoritative result observed by shadow mode."""

    verdict: str
    merge_eligible: bool
    authoritative_review_state: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "verdict", _require_text(self.verdict, field="verdict")
        )
        if type(self.merge_eligible) is not bool:
            raise TypeError("merge_eligible must be a boolean")
        object.__setattr__(
            self,
            "authoritative_review_state",
            _require_text(
                self.authoritative_review_state, field="authoritative_review_state"
            ),
        )


@dataclass(frozen=True, slots=True)
class ShadowRecommendation:
    """Non-authoritative shadow signal for later human/provider adjudication."""

    verified_blocker_ids: tuple[str, ...]
    unresolved_finding_ids: tuple[str, ...]
    adjudication_required_ids: tuple[str, ...]

    @property
    def status(self) -> str:
        return "shadow-only"


@dataclass(frozen=True, slots=True)
class ShadowReviewReceipt:
    """Bounded receipt for shadow metrics and production-state preservation."""

    run_id: str
    protocol_id: str
    protocol_version: str
    authority: ReviewAuthorityIdentity | None
    coverage: ReviewSurfacePlan
    candidate_count: int
    verified_count: int
    rejected_count: int
    unresolved_count: int
    verified_blocker_count: int
    candidate_finding_ids: tuple[str, ...]
    verified_finding_ids: tuple[str, ...]
    rejected_finding_ids: tuple[str, ...]
    unresolved_finding_ids: tuple[str, ...]
    token_usage: TokenUsage
    wall_clock_ms: int
    incremental_known_findings: tuple[str, ...]
    production_verdict: str
    production_merge_eligible: bool
    authoritative_review_state: str
    production_state_unchanged: bool

    KIND: Final[str] = "review-shadow-receipt"
    SCHEMA_VERSION: Final[str] = "review-shadow-receipt.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_text(self.run_id, field="run_id"))
        object.__setattr__(
            self, "protocol_id", _require_text(self.protocol_id, field="protocol_id")
        )
        object.__setattr__(
            self,
            "protocol_version",
            _require_text(self.protocol_version, field="protocol_version"),
        )
        if not isinstance(self.coverage, ReviewSurfacePlan):
            raise TypeError("coverage must be ReviewSurfacePlan")
        for field_name in (
            "candidate_finding_ids",
            "verified_finding_ids",
            "rejected_finding_ids",
            "unresolved_finding_ids",
            "incremental_known_findings",
        ):
            object.__setattr__(
                self,
                field_name,
                _unique_texts(getattr(self, field_name), field=field_name),
            )
        if self.candidate_count != len(self.candidate_finding_ids):
            raise ReviewContractError("candidate_count does not match receipt IDs")
        if self.verified_count != len(self.verified_finding_ids):
            raise ReviewContractError("verified_count does not match receipt IDs")
        if self.rejected_count != len(self.rejected_finding_ids):
            raise ReviewContractError("rejected_count does not match receipt IDs")
        if self.unresolved_count != len(self.unresolved_finding_ids):
            raise ReviewContractError("unresolved_count does not match receipt IDs")
        for field_name in (
            "candidate_count",
            "verified_count",
            "rejected_count",
            "unresolved_count",
            "verified_blocker_count",
            "wall_clock_ms",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ReviewContractError(f"{field_name} must be non-negative")
        if not isinstance(self.token_usage, TokenUsage):
            raise TypeError("token_usage must be TokenUsage")
        if type(self.production_merge_eligible) is not bool:
            raise TypeError("production_merge_eligible must be a boolean")
        if self.production_state_unchanged is not True:
            raise ReviewContractError(
                "shadow receipt must prove production state was unchanged"
            )
        object.__setattr__(
            self,
            "production_verdict",
            _require_text(self.production_verdict, field="production_verdict"),
        )
        object.__setattr__(
            self,
            "authoritative_review_state",
            _require_text(
                self.authoritative_review_state, field="authoritative_review_state"
            ),
        )

    @property
    def confirmed_count(self) -> int:
        return self.verified_count

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.KIND,
            "schema_version": self.SCHEMA_VERSION,
            "run_id": self.run_id,
            "protocol_id": self.protocol_id,
            "protocol_version": self.protocol_version,
            "authority": self.authority.to_dict()
            if self.authority is not None
            else None,
            "coverage": self.coverage.to_dict(),
            "candidate_count": self.candidate_count,
            "verified_count": self.verified_count,
            "rejected_count": self.rejected_count,
            "unresolved_count": self.unresolved_count,
            "verified_blocker_count": self.verified_blocker_count,
            "candidate_finding_ids": list(self.candidate_finding_ids),
            "verified_finding_ids": list(self.verified_finding_ids),
            "rejected_finding_ids": list(self.rejected_finding_ids),
            "unresolved_finding_ids": list(self.unresolved_finding_ids),
            "token_usage": self.token_usage.to_dict(),
            "wall_clock_ms": self.wall_clock_ms,
            "incremental_known_findings": list(self.incremental_known_findings),
            "production_verdict": self.production_verdict,
            "production_merge_eligible": self.production_merge_eligible,
            "authoritative_review_state": self.authoritative_review_state,
            "production_state_unchanged": self.production_state_unchanged,
        }


@dataclass(frozen=True, slots=True)
class ShadowReviewResult:
    """Complete shadow output; authoritative production input is returned intact."""

    production_state: ProductionReviewState
    coverage: ReviewSurfacePlan
    coverage_evidence: tuple[EvidenceReference, ...]
    finding_union: FindingUnion
    all_verifications: tuple[VerifiedFinding, ...]
    verified_findings: tuple[VerifiedFinding, ...]
    rejected_findings: tuple[VerifiedFinding, ...]
    unresolved_findings: tuple[VerifiedFinding, ...]
    recommendation: ShadowRecommendation
    receipt: ShadowReviewReceipt

    @property
    def production_verdict(self) -> str:
        return self.production_state.verdict

    @property
    def merge_eligible(self) -> bool:
        return self.production_state.merge_eligible

    @property
    def authoritative_review_state(self) -> str:
        return self.production_state.authoritative_review_state

    @property
    def verified_blockers(self) -> tuple[VerifiedFinding, ...]:
        return tuple(
            finding
            for finding in self.verified_findings
            if finding.blocking_status is FindingBlockingStatus.BLOCKING
        )

    def with_incremental_known_findings(self, finding_ids: Sequence[str]) -> Self:
        known = _unique_texts(finding_ids, field="incremental_known_findings")
        return replace(
            self,
            receipt=replace(self.receipt, incremental_known_findings=known),
        )


class ShadowReviewPipeline:
    """Run structured discovery and independent verification in shadow mode."""

    def __init__(
        self,
        verifier: IndependentVerifier,
        *,
        plan: StructuredDiscoveryPlan | None = None,
        protocol_id: str = SHADOW_PROTOCOL_ID,
        protocol_version: str = SHADOW_PROTOCOL_VERSION,
    ) -> None:
        if not callable(verifier):
            raise TypeError("verifier must be callable")
        self.verifier = verifier
        self.plan = plan or StructuredDiscoveryPlan()
        self.protocol_id = _require_text(protocol_id, field="protocol_id")
        self.protocol_version = _require_text(
            protocol_version, field="protocol_version"
        )

    def run(
        self,
        production_state: ProductionReviewState,
        discovery_results: Sequence[DiscoveryPassResult],
        *,
        authority: ReviewAuthorityIdentity | None = None,
        evidence_package: ReviewEvidencePackage | None = None,
        subject_root: Path | None = None,
        run_id: str | None = None,
        incremental_known_findings: Sequence[str] = (),
    ) -> ShadowReviewResult:
        if not isinstance(production_state, ProductionReviewState):
            raise TypeError("production_state must be ProductionReviewState")
        started = time.perf_counter_ns()
        results = tuple(discovery_results)
        seen_passes: set[str] = set()
        all_candidates: list[CandidateFinding] = []
        coverage: list[ReviewSurface] = []
        coverage_evidence: list[EvidenceReference] = []
        token_parts: list[TokenUsage] = []
        for result in results:
            if not isinstance(result, DiscoveryPassResult):
                raise TypeError(
                    "discovery_results must contain DiscoveryPassResult values"
                )
            if result.pass_id in seen_passes:
                raise ReviewContractError(
                    "discovery results must contain one result per pass"
                )
            seen_passes.add(result.pass_id)
            discovery_pass = self.plan.pass_for(result.pass_id)
            if not set(result.planned_surfaces).issubset(discovery_pass.surfaces):
                raise ReviewContractError(
                    "discovery result declares an unknown surface"
                )
            if not set(result.covered_surfaces).issubset(discovery_pass.surfaces):
                raise ReviewContractError(
                    "discovery result covers an undeclared surface"
                )
            for surface in result.covered_surfaces:
                if surface not in coverage:
                    coverage.append(surface)
            for evidence in result.coverage_evidence:
                if evidence.reference_id not in {
                    item.reference_id for item in coverage_evidence
                }:
                    coverage_evidence.append(evidence)
            all_candidates.extend(result.candidate_findings)
            token_parts.append(result.token_usage)

        union = union_candidate_findings(tuple(all_candidates))
        verification_results: list[VerifiedFinding] = []
        context_ids: set[str] = set()
        verification_tokens: list[TokenUsage] = []
        verification_started = time.perf_counter_ns()
        current_run_id = run_id or f"shadow-run-{uuid.uuid4().hex}"
        for candidate in union.candidate_findings:
            context_id = f"{current_run_id}:verification:{uuid.uuid4().hex}"
            if context_id in context_ids or context_id in {
                run.run_id for run in candidate.originating_runs
            }:
                raise ReviewContractError("independent verifier context is not fresh")
            context_ids.add(context_id)
            request = IndependentVerificationRequest(
                context_id=context_id,
                candidate=VerificationSubject.from_candidate(candidate),
                evidence_package=evidence_package,
                subject_root=subject_root,
            )
            decision = self.verifier(request)
            if not isinstance(decision, IndependentVerification):
                raise TypeError("independent verifier returned an invalid result")
            verification_tokens.append(decision.token_usage)
            severity = decision.severity or FindingSeverity.LOW
            blocking = decision.blocking_status or FindingBlockingStatus.NOT_ASSESSED
            verification_results.append(
                VerifiedFinding.from_candidate(
                    candidate,
                    verification_status=decision.status,
                    verification_evidence_refs=decision.verification_evidence_refs,
                    severity=severity,
                    blocking_status=blocking,
                )
            )
        all_verifications = tuple(verification_results)
        verified = tuple(
            item
            for item in all_verifications
            if item.verification_status is FindingVerificationStatus.CONFIRMED
        )
        rejected = tuple(
            item
            for item in all_verifications
            if item.verification_status is FindingVerificationStatus.REJECTED
        )
        unresolved = tuple(
            item
            for item in all_verifications
            if item.verification_status
            not in (
                FindingVerificationStatus.CONFIRMED,
                FindingVerificationStatus.REJECTED,
            )
        )
        known = _unique_texts(
            incremental_known_findings, field="incremental_known_findings"
        )
        run_id = current_run_id
        surface_plan = ReviewSurfacePlan(
            required=self.plan.required_surfaces,
            covered=tuple(coverage),
            risk_triggered=self.plan.risk_triggered_surfaces,
        )
        verified_blockers = tuple(
            item.finding_id
            for item in verified
            if item.blocking_status is FindingBlockingStatus.BLOCKING
        )
        unresolved_ids = tuple(item.finding_id for item in unresolved)
        recommendation = ShadowRecommendation(
            verified_blocker_ids=verified_blockers,
            unresolved_finding_ids=unresolved_ids,
            adjudication_required_ids=tuple(
                item.finding_id
                for item in (*rejected, *unresolved)
                if item.finding_id not in known
            ),
        )
        elapsed_ms = max(1, int((time.perf_counter_ns() - started) / 1_000_000))
        receipt = ShadowReviewReceipt(
            run_id=run_id,
            protocol_id=self.protocol_id,
            protocol_version=self.protocol_version,
            authority=authority,
            coverage=surface_plan,
            candidate_count=len(union.candidate_findings),
            verified_count=len(verified),
            rejected_count=len(rejected),
            unresolved_count=len(unresolved),
            verified_blocker_count=len(verified_blockers),
            candidate_finding_ids=tuple(
                item.finding_id for item in union.candidate_findings
            ),
            verified_finding_ids=tuple(item.finding_id for item in verified),
            rejected_finding_ids=tuple(item.finding_id for item in rejected),
            unresolved_finding_ids=tuple(item.finding_id for item in unresolved),
            token_usage=_sum_tokens((*token_parts, *verification_tokens)),
            wall_clock_ms=max(
                elapsed_ms,
                int((time.perf_counter_ns() - verification_started) / 1_000_000),
            ),
            incremental_known_findings=known,
            production_verdict=production_state.verdict,
            production_merge_eligible=production_state.merge_eligible,
            authoritative_review_state=production_state.authoritative_review_state,
            production_state_unchanged=True,
        )
        return ShadowReviewResult(
            production_state=production_state,
            coverage=surface_plan,
            coverage_evidence=tuple(coverage_evidence),
            finding_union=union,
            all_verifications=all_verifications,
            verified_findings=verified,
            rejected_findings=rejected,
            unresolved_findings=unresolved,
            recommendation=recommendation,
            receipt=receipt,
        )


class DiscoveryProtocol(Protocol):
    """Protocol boundary for a structured discovery implementation."""

    def __call__(self, subject_root: Path) -> Sequence[DiscoveryPassResult]: ...


__all__ = [
    "DEFAULT_DISCOVERY_PASSES",
    "DiscoveryPass",
    "DiscoveryPassResult",
    "DiscoveryProtocol",
    "FindingUnion",
    "IndependentVerification",
    "IndependentVerificationRequest",
    "IndependentVerifier",
    "ProductionReviewState",
    "SHADOW_PROTOCOL_ID",
    "SHADOW_PROTOCOL_VERSION",
    "ShadowRecommendation",
    "ShadowReviewPipeline",
    "ShadowReviewReceipt",
    "ShadowReviewResult",
    "StructuredDiscoveryPlan",
    "VerificationMethod",
    "VerificationSubject",
    "finding_fingerprint",
    "union_candidate_findings",
]
