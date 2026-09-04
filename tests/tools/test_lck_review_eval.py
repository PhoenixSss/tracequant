# ruff: noqa: E402, I001

"""Acceptance tests for the isolated Review Eval Harness boundary."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core.review_authority import (  # type: ignore[import-not-found]
    FrozenReviewAuthority,
    LiveReviewAuthority,
    require_frozen_review_authority,
    require_live_review_authority,
)
from lck_core.models import LckStopError  # type: ignore[import-not-found]
from lck_core.review_eval import (  # type: ignore[import-not-found]
    GitFrozenSubjectMaterializer,
    ReviewEvalRunner,
    ReviewEvalWorkspaceManager,
)
from lck_core.review_fixture import (  # type: ignore[import-not-found]
    ReviewFixtureBuilder,
)
from lck_core.review_workspace import _review_target_refs  # type: ignore[import-not-found]
from lck_test_support import _review_state


SHA = "a" * 40


def test_review_eval_keeps_harness_and_frozen_subject_authorities_separate(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "harness"
    fixture_source = tmp_path / "fixture-source"
    harness.mkdir()
    fixture_source.mkdir()
    (harness / "review_harness.py").write_text(
        "CURRENT CANDIDATE HARNESS\n", encoding="utf-8"
    )
    old_workflow = fixture_source / "tools" / "agent_workflow"
    old_workflow.mkdir(parents=True)
    (old_workflow / "review.py").write_text(
        "HISTORICAL SUBJECT WORKFLOW\n", encoding="utf-8"
    )

    authority = FrozenReviewAuthority(
        fixture_id="historical-subject",
        base_sha=SHA,
        head_sha="b" * 40,
    )
    workspace = ReviewEvalWorkspaceManager(tmp_path / "runs")
    runner = ReviewEvalRunner(harness, workspace=workspace)

    def materialize(supplied: FrozenReviewAuthority, destination: Path) -> None:
        assert supplied is authority
        shutil.copytree(fixture_source, destination, dirs_exist_ok=True)

    def evaluate(run: Any) -> dict[str, Any]:
        assert run.authority is authority
        assert run.harness_root == harness.resolve()
        assert run.subject_root != run.harness_root
        assert run.execution_cwd == run.harness_root
        assert (run.harness_root / "review_harness.py").read_text(
            encoding="utf-8"
        ) == "CURRENT CANDIDATE HARNESS\n"
        assert (run.subject_root / "tools/agent_workflow/review.py").read_text(
            encoding="utf-8"
        ) == "HISTORICAL SUBJECT WORKFLOW\n"
        return {
            "harness_file": (run.harness_root / "review_harness.py").read_text(
                encoding="utf-8"
            ),
            "subject_file": (
                run.subject_root / "tools/agent_workflow/review.py"
            ).read_text(encoding="utf-8"),
        }

    execution = runner.run(authority, materialize, evaluate)
    assert execution.run_id
    assert execution.value["harness_file"] == "CURRENT CANDIDATE HARNESS\n"
    assert execution.value["subject_file"] == "HISTORICAL SUBJECT WORKFLOW\n"
    assert execution.run.to_dict()["planes"] == {
        "harness": "current-checkout",
        "subject": "frozen-fixture-materialization",
        "run": "operation-owned-isolated-state",
    }
    assert execution.run.result_path("receipt.json").parent == execution.run.result_root
    assert execution.run.run_root.is_dir()
    execution.close()
    assert not execution.run.run_root.exists()
    assert (harness / "review_harness.py").read_text(encoding="utf-8") == (
        "CURRENT CANDIDATE HARNESS\n"
    )


def test_review_eval_rejects_live_authority_and_production_uses_live_pr() -> None:
    frozen = FrozenReviewAuthority(
        fixture_id="fixture",
        base_sha=SHA,
        head_sha=SHA,
    )
    state = _review_state()
    live = LiveReviewAuthority.from_state(state, state.task_contract or {})

    assert require_live_review_authority(live) is live
    assert require_frozen_review_authority(frozen) is frozen
    with pytest.raises(TypeError, match="FrozenReviewAuthority"):
        require_frozen_review_authority(live)
    with pytest.raises(TypeError, match="LiveReviewAuthority"):
        require_live_review_authority(frozen)

    production_target = _review_target_refs(state, state.task_contract or {})
    assert production_target.pr_number == live.pr_number
    assert production_target.base_sha == live.base_sha
    assert production_target.head_sha == live.head_sha


def test_review_eval_command_runs_from_harness_checkout_without_subject_import_path(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "harness"
    fixture_source = tmp_path / "fixture-source"
    harness.mkdir()
    fixture_source.mkdir()
    (fixture_source / "old_runner.py").write_text(
        "raise RuntimeError('subject runner must not execute')\n", encoding="utf-8"
    )
    authority = FrozenReviewAuthority("fixture", SHA, SHA)
    runner = ReviewEvalRunner(
        harness,
        workspace=ReviewEvalWorkspaceManager(tmp_path / "runs"),
    )

    def materialize(_authority: FrozenReviewAuthority, destination: Path) -> None:
        shutil.copytree(fixture_source, destination, dirs_exist_ok=True)

    execution = runner.execute_command(
        authority,
        materialize,
        [
            sys.executable,
            "-c",
            "import os, pathlib; print(pathlib.Path.cwd()); print(os.getenv('PYTHONPATH', '')); print(os.getenv('TRACEQUANT_REVIEW_EVAL_SUBJECT_ROOT', ''))",
        ],
    )
    try:
        assert execution.value.returncode == 0
        lines = execution.value.stdout.splitlines()
        assert lines[0] == str(harness.resolve())
        assert str(execution.run.subject_root) not in lines[1]
        assert lines[2] == str(execution.run.subject_root)
    finally:
        execution.close()


def test_review_eval_detection_rejects_explicit_writable_subject(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    authority = FrozenReviewAuthority("fixture", SHA, SHA)
    runner = ReviewEvalRunner(
        harness,
        workspace=ReviewEvalWorkspaceManager(tmp_path / "runs"),
    )

    with pytest.raises(LckStopError, match="Detection Run Subject must be read-only"):
        runner.start(
            authority,
            lambda _authority, destination: destination.mkdir(exist_ok=True),
            writable=True,
        )
    assert not list((tmp_path / "runs").iterdir())


def test_review_eval_closes_run_when_harness_entrypoint_fails(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    fixture_source = tmp_path / "fixture-source"
    harness.mkdir()
    fixture_source.mkdir()
    authority = FrozenReviewAuthority("fixture", SHA, SHA)
    runner = ReviewEvalRunner(
        harness,
        workspace=ReviewEvalWorkspaceManager(tmp_path / "runs"),
    )

    with pytest.raises(RuntimeError, match="entrypoint failed"):
        runner.run(
            authority,
            lambda _authority, destination: destination.mkdir(exist_ok=True),
            lambda _run: (_ for _ in ()).throw(RuntimeError("entrypoint failed")),
        )
    assert not list((tmp_path / "runs").iterdir())


def test_git_subject_materializer_uses_explicit_frozen_head(tmp_path: Path) -> None:
    source = tmp_path / "historical-source"
    harness = tmp_path / "harness"
    source.mkdir()
    harness.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "Review Eval Test")
    git("config", "user.email", "review-eval@example.invalid")
    (source / "subject.txt").write_text("base\n", encoding="utf-8")
    git("add", "subject.txt")
    git("commit", "--quiet", "-m", "base")
    base_sha = git("rev-parse", "HEAD")
    (source / "subject.txt").write_text("historical head\n", encoding="utf-8")
    git("commit", "--quiet", "-am", "head")
    head_sha = git("rev-parse", "HEAD")

    authority = FrozenReviewAuthority("fixture", base_sha, head_sha)
    runner = ReviewEvalRunner(
        harness,
        workspace_root=tmp_path / "runs",
    )
    execution = runner.run(
        authority,
        GitFrozenSubjectMaterializer(source),
        lambda run: (
            (run.subject_root / "subject.txt").read_text(encoding="utf-8"),
            subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=run.subject_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
        ),
    )
    try:
        assert execution.value == ("historical head\n", "")
    finally:
        execution.close()


def test_fixture_digest_and_fresh_run_isolation_are_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    harness = tmp_path / "harness"
    source.mkdir()
    harness.mkdir()

    def git(root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    for root in (source, harness):
        git(root, "init", "--quiet")
        git(root, "config", "user.name", "Review Eval Test")
        git(root, "config", "user.email", "review-eval@example.invalid")
    (source / "subject.txt").write_text("base\n", encoding="utf-8")
    git(source, "add", "subject.txt")
    git(source, "commit", "--quiet", "-m", "base")
    base_sha = git(source, "rev-parse", "HEAD")
    (source / "subject.txt").write_text("frozen head\n", encoding="utf-8")
    git(source, "commit", "--quiet", "-am", "head")
    head_sha = git(source, "rev-parse", "HEAD")
    (harness / "harness.py").write_text("candidate\n", encoding="utf-8")
    git(harness, "add", "harness.py")
    git(harness, "commit", "--quiet", "-m", "harness")
    harness_sha = git(harness, "rev-parse", "HEAD")

    fixture = ReviewFixtureBuilder(source).create(
        tmp_path / "fixture",
        fixture_id="fixture-252",
        base_sha=base_sha,
        head_sha=head_sha,
        task_contract={"task": 252},
        deterministic_evidence={"checks": ["deterministic"]},
    )
    fixture_digest = fixture.fixture_digest
    shutil.rmtree(source)
    runner = ReviewEvalRunner(harness, workspace_root=tmp_path / "runs")
    materializer = GitFrozenSubjectMaterializer(fixture=fixture)
    first = runner.run(fixture, materializer, lambda _run: {"result": "ok"})
    try:
        assert first.run.run_id
        assert first.run.harness_sha == harness_sha
        receipt = json.loads(first.run.receipt_path.read_text(encoding="utf-8"))
        assert receipt["fixture_id"] == "fixture-252"
        assert receipt["fixture_digest"] == fixture_digest
        assert receipt["run_identity"]["run_id"] == first.run.run_id
        assert receipt["harness_sha"] == harness_sha
        with pytest.raises(LckStopError, match="reserved"):
            first.run.write_result("run-receipt.json", {"tampered": True})
        with pytest.raises(LckStopError, match="reserved"):
            first.run.write_result("nested/../run-receipt.json", {"tampered": True})
        assert json.loads(first.run.receipt_path.read_text(encoding="utf-8")) == receipt
        first.run.receipt_path.write_text('{"tampered": true}\n', encoding="utf-8")
        with pytest.raises(LckStopError, match="receipt identity does not match"):
            first.run.write_receipt()
        assert json.loads(
            first.run.result_path("eval-result.json").read_text(encoding="utf-8")
        ) == {"result": "ok"}
        assert (first.run.subject_root / "subject.txt").read_text(
            encoding="utf-8"
        ) == "frozen head\n"
        with pytest.raises(PermissionError):
            (first.run.subject_root / "subject.txt").write_text(
                "must not mutate\n", encoding="utf-8"
            )

        remediation = first.run.materialize_remediation(materializer)
        (remediation / "subject.txt").write_text("remediation copy\n", encoding="utf-8")
        assert (first.run.subject_root / "subject.txt").read_text(
            encoding="utf-8"
        ) == "frozen head\n"
        assert fixture.verify().fixture_digest == fixture_digest
    finally:
        first.close()

    second = runner.run(fixture, materializer, lambda run: run)
    try:
        assert second.run.run_id != first.run.run_id
        assert second.run.run_root != first.run.run_root
        assert (second.run.subject_root / "subject.txt").read_text(
            encoding="utf-8"
        ) == "frozen head\n"
    finally:
        second.close()

    mismatched_authority = FrozenReviewAuthority(
        "different-fixture", base_sha, head_sha
    )
    with pytest.raises(LckStopError, match="fixture authorities do not match"):
        materializer.materialize(mismatched_authority, tmp_path / "mismatched-subject")

    fixture.repository_bundle_path.write_bytes(
        fixture.repository_bundle_path.read_bytes() + b"tampered"
    )
    with pytest.raises(LckStopError, match="digest mismatch"):
        runner.start(fixture, materializer)
