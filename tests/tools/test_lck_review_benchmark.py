# ruff: noqa: E402, I001

"""Acceptance tests for the checked-in Task #194 Review benchmark corpus."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core.models import LckStopError  # type: ignore[import-not-found]
from lck_core.review_benchmark import (  # type: ignore[import-not-found]
    CandidateFinding,
    ReviewBenchmarkCorpus,
    ReviewBenchmarkRunner,
    Task194ProductionEquivalentReviewer,
    load_task_194_benchmark,
)


BASE_SHA = "fd9946e1c7773c33c2cabbd08e084fb18c236525"
DEFECT_RICH_HEAD = "929ce3dfc71846c4d40a3ce8a05215345be8bc33"
STABLE_HEAD = "3b1cbbd6db08ae81506c9b70ebfc7eabd7b8cf70"


def test_task_194_fixture_replay_is_oracle_isolated_and_reproducible(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).parents[2]
    corpus = load_task_194_benchmark(repo_root)
    reviewer = Task194ProductionEquivalentReviewer()
    seen_subjects: list[Path] = []

    def oracle_blind_reviewer(run: Any) -> Any:
        subject = run.subject_root
        seen_subjects.append(subject)
        assert subject != corpus.root
        assert corpus.root not in subject.parents
        assert not (subject / "oracle").exists()
        assert not (subject / "known-findings.json").exists()
        serialized_run = json.dumps(run.to_dict(), ensure_ascii=False)
        assert "expected_verdict" not in serialized_run
        assert "accepted_finding_list" not in serialized_run
        return reviewer(run)

    benchmark = ReviewBenchmarkRunner(
        corpus,
        repo_root,
        workspace_root=tmp_path / "review-eval-runs",
    )
    before = {
        item.fixture_id: (item.fixture.root / "fixture-manifest.json").read_bytes()
        for item in corpus.fixtures
    }

    defect_runs = benchmark.replay_repeated(
        "task-194:defect-rich-v1", oracle_blind_reviewer
    )
    stable_runs = benchmark.replay_repeated("task-194:stable-v1", oracle_blind_reviewer)

    defect = corpus.fixture("task-194:defect-rich-v1")
    stable = corpus.fixture("task-194:stable-v1")
    assert defect.fixture.base_sha == BASE_SHA
    assert defect.fixture.head_sha == DEFECT_RICH_HEAD
    assert stable.fixture.base_sha == BASE_SHA
    assert stable.fixture.head_sha == STABLE_HEAD
    assert len(seen_subjects) == 4

    for result in (*defect_runs, *stable_runs):
        receipt = result.receipt
        assert receipt.protocol_steps == ("Inspect", "Reason", "Judge", "Report")
        assert receipt.protocol_id == "tracequant-production-equivalent-review"
        assert receipt.protocol_version == "v1"
        assert receipt.model
        assert receipt.config["subject_only"] is True
        assert receipt.config["oracle_access"] is False
        assert receipt.token_usage["total"] == (
            receipt.token_usage["uncached_input"] + receipt.token_usage["output"]
        )
        assert receipt.wall_clock_ms > 0
        assert receipt.findings_sha256
        assert receipt.subject_clean_exact_head is True
        assert receipt.run_workspace_cleaned is True

    defect_score = corpus.score(
        defect.fixture_id,
        defect_runs[0].observation.findings,
        defect_runs[0].observation.findings,
    )
    assert defect_score.known_count >= 3
    assert defect_score.matched_count == defect_score.known_count
    assert defect_score.control_status == "known-findings-covered"
    assert defect_score.adjudication_required is False

    stable_score = corpus.score(
        stable.fixture_id,
        stable_runs[0].observation.findings,
        stable_runs[0].observation.findings,
    )
    assert stable_runs[0].observation.verdict == "PASS"
    assert stable_score.control_status == "repeated-pass-control"
    assert stable_score.adjudication_required is False

    receipt_dir = tmp_path / "baseline-receipts"
    for index, result in enumerate((*defect_runs, *stable_runs), start=1):
        path = receipt_dir / f"run-{index}.json"
        result.receipt.write(path)
        assert json.loads(path.read_text(encoding="utf-8"))["kind"] == (
            "review-benchmark-run-receipt"
        )
    assert list((tmp_path / "review-eval-runs").iterdir()) == []

    for item in corpus.fixtures:
        assert (item.fixture.root / "fixture-manifest.json").read_bytes() == before[
            item.fixture_id
        ]
        assert item.fixture.verify().fixture_digest == item.fixture.fixture_digest


def test_stable_fixture_unknown_finding_requires_adjudication() -> None:
    corpus = load_task_194_benchmark(Path(__file__).parents[2])
    finding = CandidateFinding(
        severity="Medium",
        path="src/tracequant/data/raw_store.py",
        symbol="future-check",
        category="new-unadjudicated-category",
        summary="Potential issue not present in the frozen known-finding list.",
        evidence=("independent reviewer evidence",),
    )

    score = corpus.score("task-194:stable-v1", (finding,), (finding,))

    assert score.matched_count == 0
    assert score.adjudication_required is True
    assert score.control_status == "needs-adjudication"
    assert score.matches[0].match_type == "unmatched-candidate"
    assert score.matches[0].known_finding_id is None


def test_benchmark_replay_rejects_invalid_repetition_count() -> None:
    corpus = load_task_194_benchmark(Path(__file__).parents[2])
    benchmark = ReviewBenchmarkRunner(corpus, Path(__file__).parents[2])

    with pytest.raises(ValueError, match="at least two"):
        benchmark.replay_repeated(
            "task-194:stable-v1",
            Task194ProductionEquivalentReviewer(),
            repetitions=1,
        )


def test_benchmark_corpus_fails_closed_when_manifest_changes(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "docs/workflows/benchmarks/task-194"
    copied = tmp_path / "task-194"
    copied.mkdir()
    for name in ("corpus-manifest.json",):
        (copied / name).write_bytes((source / name).read_bytes())

    payload = json.loads((copied / "corpus-manifest.json").read_text(encoding="utf-8"))
    payload["corpus_id"] = "forged"
    (copied / "corpus-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(LckStopError, match="manifest"):
        ReviewBenchmarkCorpus.from_manifest(copied / "corpus-manifest.json")
