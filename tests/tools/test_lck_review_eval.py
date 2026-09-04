# ruff: noqa: E402, I001

"""Acceptance tests for the isolated Review Eval Harness boundary."""

from __future__ import annotations

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
from lck_core.review_eval import (  # type: ignore[import-not-found]
    GitFrozenSubjectMaterializer,
    ReviewEvalRunner,
    ReviewEvalWorkspaceManager,
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
