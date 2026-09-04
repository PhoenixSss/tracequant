"""Critical Outcome tests for the canonical Review vNext contracts."""

from __future__ import annotations

import json

import pytest

from tracequant.contracts import (
    ALWAYS_ON_SURFACES,
    AssuranceObligation,
    AssuranceResult,
    AssuranceStatus,
    CandidateFinding,
    ChangeMapEntry,
    EvidenceReference,
    FindingBlockingStatus,
    FindingSeverity,
    FindingVerificationStatus,
    ReviewAuthorityIdentity,
    ReviewAuthorityKind,
    ReviewContractError,
    ReviewEvidencePackage,
    ReviewRiskProfile,
    ReviewRunReceipt,
    ReviewSurface,
    ReviewSurfacePlan,
    RunProvenance,
    TokenUsage,
    VerifiedFinding,
)


def _authority(*, head: str = "b" * 40) -> ReviewAuthorityIdentity:
    return ReviewAuthorityIdentity(
        authority_kind=ReviewAuthorityKind.LIVE,
        repository="PhoenixSss/tracequant",
        task_number=254,
        pull_request_number=300,
        base_sha="a" * 40,
        head_sha=head,
        diff_sha256="c" * 64,
    )


def _surface_plan() -> ReviewSurfacePlan:
    risk_surface = ReviewSurface.SECURITY
    required = (*ALWAYS_ON_SURFACES, risk_surface)
    return ReviewSurfacePlan(
        required=required,
        covered=required,
        risk_triggered=(risk_surface,),
        semantic_escalation_requests=(),
    )


def _provenance(run_id: str) -> RunProvenance:
    return RunProvenance(
        run_id=run_id,
        authority=_authority(),
        harness_id="review-harness.v1",
        protocol_id="review-protocol.v1",
    )


def _candidate() -> CandidateFinding:
    return CandidateFinding(
        finding_id="finding-1",
        surface=ReviewSurface.ERROR_FAILURE_PATHS,
        claim="The failure path can leave an operation unresolved.",
        affected_locations=("src/tracequant/contracts/review.py:1",),
        contract_invariant="Unknown state must not be retried without reconciliation.",
        failure_scenario="A transport error occurs after the side effect but before acknowledgement.",
        evidence_refs=("deterministic-1",),
        originating_runs=(_provenance("run-1"), _provenance("run-2")),
    )


def test_review_vnext_canonical_contracts_round_trip_without_profile_specific_fields() -> (
    None
):
    """Exercise every canonical representation through JSON-compatible data."""
    risk = ReviewRiskProfile(
        deterministic_facts={
            "changed_runtime_boundary": True,
            "credential_path": False,
        },
        triggered_surfaces=(ReviewSurface.SECURITY,),
    )
    evidence = EvidenceReference(
        reference_id="deterministic-1",
        kind="validation-result",
        locator=".workflow.local/lck/review-validation/result.json",
        summary="Bounded deterministic validation result.",
        digest_sha256="d" * 64,
    )
    package = ReviewEvidencePackage(
        summary="Compact Review facts with retrieval pointers.",
        authority=_authority(),
        task_contract={
            "number": 254,
            "body_sha256": "e" * 64,
            "acceptance_count": 7,
            "labels": ("type:task",),
        },
        change_map=(
            ChangeMapEntry(
                path="src/tracequant/contracts/review.py",
                change_kind="added",
                locations=("ReviewEvidencePackage", "ReviewRunReceipt"),
                summary="Adds canonical Review value contracts.",
            ),
        ),
        deterministic_evidence=(evidence,),
        risk_profile=risk,
        surface_plan=_surface_plan(),
        targeted_retrieval_references=(
            EvidenceReference(
                reference_id="source-1",
                kind="source-location",
                locator="docs/architecture/repository-structure.md",
                summary="Repository boundary reference.",
            ),
        ),
    )
    candidate = _candidate()
    verified = VerifiedFinding.from_candidate(
        candidate,
        verification_status=FindingVerificationStatus.CONFIRMED,
        verification_evidence_refs=("deterministic-1",),
        severity=FindingSeverity.HIGH,
        blocking_status=FindingBlockingStatus.NON_BLOCKING,
    )
    receipt = ReviewRunReceipt(
        run_id="run-1",
        authority=_authority(),
        harness_config={"subject_only": True},
        protocol_config={"sequence": ("Inspect", "Reason", "Judge", "Report")},
        model_config={"temperature": 0},
        coverage=_surface_plan(),
        candidate_findings=(candidate,),
        verified_findings=(verified,),
        token_usage=TokenUsage(input_tokens=120, output_tokens=30, total_tokens=150),
        wall_clock_ms=250,
        assurance_obligations=(
            AssuranceObligation(
                obligation_id="coverage",
                description="All required surfaces are covered.",
                required_surfaces=(*ALWAYS_ON_SURFACES, ReviewSurface.SECURITY),
            ),
        ),
        assurance_results=(
            AssuranceResult(
                obligation_id="coverage",
                status=AssuranceStatus.PASS,
                evidence_refs=("deterministic-1",),
                summary="Required surfaces are covered.",
            ),
        ),
    )

    for model in (package, candidate, verified, receipt):
        payload = model.to_dict()
        assert payload["kind"]
        assert payload["schema_version"]
        assert json.loads(json.dumps(payload, allow_nan=False)) == payload

    assert ReviewEvidencePackage.from_json(package.to_json()) == package
    assert CandidateFinding.from_dict(candidate.to_dict()) == candidate
    assert VerifiedFinding.from_dict(verified.to_dict()) == verified
    assert ReviewRunReceipt.from_json(receipt.to_json()) == receipt
    assert receipt.review_complete is True
    assert "severity" not in candidate.to_dict()
    assert "blocking_status" not in candidate.to_dict()
    assert len(candidate.originating_runs) == 2
    serialized = package.to_json()
    assert "provider" not in serialized
    assert "openai" not in serialized


def test_surface_plan_does_not_hide_uncovered_required_surfaces() -> None:
    plan = ReviewSurfacePlan(
        covered=ALWAYS_ON_SURFACES[:-1],
        skipped_with_reason={
            ReviewSurface.TESTS_VS_CLAIMS.value: "No test claim was available in this fixture."
        },
    )

    assert plan.is_complete is False
    assert plan.coverage_status == "incomplete"
    assert plan.missing_required == (ReviewSurface.TESTS_VS_CLAIMS,)
    with pytest.raises(ReviewContractError, match="risk-triggered"):
        ReviewSurfacePlan(
            required=ALWAYS_ON_SURFACES,
            risk_triggered=(ReviewSurface.SECURITY,),
        )


def test_review_completion_requires_obligation_surfaces_in_coverage() -> None:
    receipt = ReviewRunReceipt(
        run_id="run-1",
        authority=_authority(),
        harness_config={"subject_only": True},
        protocol_config={"sequence": ("Inspect", "Reason", "Judge", "Report")},
        model_config={"temperature": 0},
        coverage=ReviewSurfacePlan(
            required=ALWAYS_ON_SURFACES,
            covered=ALWAYS_ON_SURFACES,
        ),
        candidate_findings=(),
        verified_findings=(),
        token_usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        wall_clock_ms=1,
        assurance_obligations=(
            AssuranceObligation(
                obligation_id="security",
                description="Security must be reviewed.",
                required_surfaces=(ReviewSurface.SECURITY,),
            ),
        ),
        assurance_results=(
            AssuranceResult(
                obligation_id="security",
                status=AssuranceStatus.PASS,
                evidence_refs=(),
                summary="Security review passed.",
            ),
        ),
    )

    assert receipt.review_complete is False
    assert receipt.review_status == "incomplete"
    assert ReviewRunReceipt.from_json(receipt.to_json()) == receipt


def test_candidate_severity_cannot_become_a_verified_blocker_implicitly() -> None:
    candidate = _candidate()

    assert not hasattr(candidate, "severity")
    assert not hasattr(candidate, "blocking_status")
    with pytest.raises(ReviewContractError, match="confirmed"):
        VerifiedFinding.from_candidate(
            candidate,
            verification_status=FindingVerificationStatus.INCONCLUSIVE,
            verification_evidence_refs=("deterministic-1",),
            severity=FindingSeverity.CRITICAL,
            blocking_status=FindingBlockingStatus.BLOCKING,
        )
