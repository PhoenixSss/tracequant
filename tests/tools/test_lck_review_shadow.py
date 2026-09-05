# ruff: noqa: E402, I001

"""Critical Outcome tests for the finding-centric Review shadow pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from tracequant.contracts import (
    CandidateFinding,
    FindingBlockingStatus,
    FindingSeverity,
    FindingVerificationStatus,
    ReviewAuthorityIdentity,
    ReviewAuthorityKind,
    ReviewContractError,
    ReviewSurface,
    RunProvenance,
    TokenUsage,
)
from lck_core.review_shadow import (  # type: ignore[import-not-found]
    DEFAULT_DISCOVERY_PASSES,
    DiscoveryPass,
    DiscoveryPassResult,
    IndependentVerification,
    IndependentVerificationRequest,
    ProductionReviewState,
    ShadowReviewPipeline,
    StructuredDiscoveryPlan,
    VerificationMethod,
)
from lck_core.review_benchmark import (  # type: ignore[import-not-found]
    ReviewBenchmarkRunner,
    load_task_194_benchmark,
)


def _authority() -> ReviewAuthorityIdentity:
    return ReviewAuthorityIdentity(
        authority_kind=ReviewAuthorityKind.FIXTURE,
        repository="PhoenixSss/tracequant",
        task_number=255,
        pull_request_number=None,
        base_sha="a" * 40,
        head_sha="b" * 40,
        diff_sha256="c" * 64,
    )


def _candidate(run_id: str, finding_id: str, claim: str) -> CandidateFinding:
    provenance = RunProvenance(
        run_id=run_id,
        authority=_authority(),
        harness_id="shadow-harness.v1",
        protocol_id="shadow-discovery.v1",
    )
    return CandidateFinding(
        finding_id=finding_id,
        surface=ReviewSurface.FUNCTIONAL_CORRECTNESS,
        claim=claim,
        affected_locations=("src/example.py:10",),
        contract_invariant="The operation must preserve its contract invariant.",
        failure_scenario="A concrete state counterexample violates the invariant.",
        evidence_refs=(f"evidence-{run_id}",),
        originating_runs=(provenance,),
    )


def _result(pass_id: str, *candidates: CandidateFinding) -> DiscoveryPassResult:
    return DiscoveryPassResult(
        pass_id=pass_id,
        covered_surfaces=(
            ReviewSurface.CONTRACT_CONFORMANCE,
            ReviewSurface.FUNCTIONAL_CORRECTNESS,
        )
        if pass_id == "contract-functional-invariants"
        else (
            ReviewSurface.STATE_TRANSITIONS,
            ReviewSurface.ERROR_FAILURE_PATHS,
            ReviewSurface.COMPATIBILITY_MIGRATION,
        )
        if pass_id == "state-failure-compatibility"
        else (
            ReviewSurface.TESTS_VS_CLAIMS,
            ReviewSurface.ARCHITECTURE,
        )
        if pass_id == "tests-claims-architecture"
        else (ReviewSurface.FUNCTIONAL_CORRECTNESS,),
        candidate_findings=candidates,
        coverage_evidence=(f"coverage://{pass_id}",),
        token_usage=TokenUsage(input_tokens=10, output_tokens=2, total_tokens=12),
        wall_clock_ms=1,
    )


def test_shadow_review_vnext_cannot_change_production_verdict() -> None:
    production = ProductionReviewState(
        verdict="PASS",
        merge_eligible=True,
        authoritative_review_state="review-approved",
    )
    duplicate_a = _candidate("discovery-contract", "reviewer-a", "A claim")
    duplicate_b = _candidate("discovery-risk", "reviewer-b", "A claim")
    rejected = _candidate("discovery-contract", "reviewer-c", "Reject this claim")
    plan = StructuredDiscoveryPlan(
        passes=(
            *DEFAULT_DISCOVERY_PASSES,
            DiscoveryPass("risk-follow-up", (ReviewSurface.FUNCTIONAL_CORRECTNESS,)),
        ),
    )
    requests: list[IndependentVerificationRequest] = []

    def verifier(request: IndependentVerificationRequest) -> IndependentVerification:
        requests.append(request)
        assert not hasattr(request.candidate, "originating_runs")
        if "Reject" in request.candidate.claim:
            return IndependentVerification(
                status=FindingVerificationStatus.REJECTED,
                verification_evidence_refs=("targeted-reproduction-rejected",),
                method=VerificationMethod.TARGETED_DETERMINISTIC_REPRODUCTION,
                falsification_attempted=True,
            )
        return IndependentVerification(
            status=FindingVerificationStatus.CONFIRMED,
            verification_evidence_refs=("targeted-counterexample",),
            method=VerificationMethod.CODE_STATE_COUNTEREXAMPLE,
            falsification_attempted=True,
            severity=FindingSeverity.HIGH,
            blocking_status=FindingBlockingStatus.BLOCKING,
        )

    result = ShadowReviewPipeline(verifier, plan=plan).run(
        production,
        (
            _result("contract-functional-invariants", duplicate_a, rejected),
            _result("state-failure-compatibility"),
            _result("tests-claims-architecture"),
            _result("risk-follow-up", duplicate_b),
        ),
        authority=_authority(),
        run_id="shadow-test-run",
    )

    assert result.production_state is production
    assert result.production_verdict == "PASS"
    assert result.merge_eligible is True
    assert result.authoritative_review_state == "review-approved"
    assert result.receipt.production_state_unchanged is True
    assert result.receipt.production_verdict == "PASS"
    assert result.receipt.production_merge_eligible is True
    assert result.receipt.authoritative_review_state == "review-approved"
    assert result.finding_union.source_count == 3
    assert result.finding_union.duplicate_count == 1
    assert len(result.finding_union.candidate_findings) == 2
    assert len(result.finding_union.candidate_findings[0].originating_runs) == 2
    assert len(result.verified_findings) == 1
    assert len(result.rejected_findings) == 1
    assert result.receipt.verified_blocker_count == 1
    assert result.receipt.wall_clock_ms >= 5
    assert len(requests) == 2
    assert len({item.context_id for item in requests}) == 2
    assert all(
        "originating_runs" not in item.to_dict()["candidate"] for item in requests
    )


def test_shadow_unresolved_finding_is_not_a_blocker_or_false_positive() -> None:
    def verifier(_request: IndependentVerificationRequest) -> IndependentVerification:
        return IndependentVerification(
            status=FindingVerificationStatus.NEEDS_MORE_EVIDENCE,
            verification_evidence_refs=("adjudication://fresh-evidence",),
            method=VerificationMethod.INDEPENDENT_ADJUDICATION,
            falsification_attempted=True,
        )

    result = ShadowReviewPipeline(verifier).run(
        ProductionReviewState("PASS", True, "review-approved"),
        (
            _result(
                "contract-functional-invariants",
                _candidate("discovery-1", "candidate-1", "Potential new claim"),
            ),
            _result("state-failure-compatibility"),
            _result("tests-claims-architecture"),
        ),
    )

    assert len(result.unresolved_findings) == 1
    assert result.verified_blockers == ()
    assert result.receipt.unresolved_count == 1
    assert result.recommendation.adjudication_required_ids
    assert result.production_verdict == "PASS"


def test_shadow_requires_every_planned_pass_and_surface() -> None:
    production = ProductionReviewState("PASS", True, "review-approved")

    with pytest.raises(ReviewContractError, match="missing planned passes"):
        ShadowReviewPipeline(lambda _request: pytest.fail("must not verify")).run(
            production,
            (
                _result("contract-functional-invariants"),
                _result("state-failure-compatibility"),
            ),
        )

    partial = DiscoveryPassResult(
        pass_id="contract-functional-invariants",
        planned_surfaces=(
            ReviewSurface.CONTRACT_CONFORMANCE,
            ReviewSurface.FUNCTIONAL_CORRECTNESS,
        ),
        covered_surfaces=(ReviewSurface.CONTRACT_CONFORMANCE,),
        coverage_evidence=("coverage://partial",),
    )
    with pytest.raises(ReviewContractError, match="cover every surface"):
        ShadowReviewPipeline(lambda _request: pytest.fail("must not verify")).run(
            production,
            (
                partial,
                _result("state-failure-compatibility"),
                _result("tests-claims-architecture"),
            ),
        )


def test_task_194_benchmark_compares_shadow_metrics_without_using_the_oracle_in_review(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).parents[2]
    corpus = load_task_194_benchmark(repo_root)
    benchmark = ReviewBenchmarkRunner(
        corpus,
        repo_root,
        workspace_root=tmp_path / "shadow-runs",
    )

    defect = benchmark.compare_baseline_and_shadow("task-194:defect-rich-v1")
    assert defect.baseline.observation.verdict == "FAIL"
    assert defect.baseline_score.matched_count == defect.baseline_score.known_count
    assert defect.shadow.shadow.production_verdict == "FAIL"
    assert defect.shadow.score.matched_count == defect.shadow.score.known_count
    assert defect.shadow.shadow.finding_union.duplicate_count == 1
    assert defect.shadow.shadow.receipt.verified_count == 3
    assert defect.shadow.shadow.receipt.unresolved_count == 0

    stable = benchmark.compare_baseline_and_shadow("task-194:stable-v1")
    assert stable.baseline.observation.verdict == "PASS"
    assert stable.shadow.shadow.production_verdict == "PASS"
    assert stable.shadow.score.control_status == "needs-adjudication"
    assert stable.shadow.shadow.receipt.unresolved_count == 1
    assert stable.shadow.shadow.receipt.verified_blocker_count == 0
    assert stable.shadow.shadow.receipt.incremental_known_findings == ()
    assert stable.shadow.shadow.receipt.production_state_unchanged is True
    assert stable.to_dict()["production_state_unchanged"] is True
    comparison = stable.to_dict()
    assert comparison["wall_clock_scope"].startswith("ReviewEvalRunner.run:")
    assert comparison["baseline"]["wall_clock_scope"] == comparison["wall_clock_scope"]
    assert comparison["shadow"]["wall_clock_scope"] == comparison["wall_clock_scope"]
