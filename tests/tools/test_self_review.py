# ruff: noqa: E402

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from self_review import (  # type: ignore[import-not-found]  # noqa: E402
    SelfReviewError,
    compute_changed_files,
    compute_diff_sha256,
    generate_artifact_path,
    is_artifact_fresh,
    read_artifact,
    validate_artifact_structure,
    validate_file_coverage,
    write_artifact,
)

SCHEMA_VERSION = 1
HEAD_SHA = "a" * 40
HEAD_SHA_2 = "b" * 40
BASE_SHA = "0" * 40
DIFF_SHA256 = "d" * 64


def _make_artifact(
    *,
    task: int = 110,
    base_sha: str = BASE_SHA,
    head_sha: str = HEAD_SHA,
    diff_sha256: str = DIFF_SHA256,
    pr: int = 111,
    areas: list[dict[str, Any]] | None = None,
    acs: list[dict[str, Any]] | None = None,
    overall: str = "verified",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "task": task,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "effective_diff_sha256": diff_sha256,
        "pr": pr,
        "generated_at": "2026-08-06T00:00:00Z",
        "areas": areas
        if areas is not None
        else [
            {
                "name": "runner-implementation",
                "files": ["tools/agent_workflow/pr_resolve.py"],
                "status": "verified",
                "implementation_evidence": ["PR resolve/create helper implemented"],
                "validation_evidence": ["test_pr_resolve: 14/14 pass"],
                "findings": [],
                "remaining_risk": "",
            }
        ],
        "acceptance_criteria": acs
        if acs is not None
        else [
            {
                "id": "AC-1",
                "text": "PR resolve/create path is deterministic",
                "status": "verified",
                "implementation_evidence": ["pr_resolve.py enforces single path"],
                "validation_evidence": ["test_pr_resolve.py covers all cases"],
                "remaining_risk": "",
            }
        ],
        "overall": overall,
    }


# --- Identity binding ---


def test_artifact_binds_task_base_head_diff(tmp_path: Path) -> None:
    artifact = _make_artifact()
    assert artifact["task"] == 110
    assert artifact["base_sha"] == BASE_SHA
    assert artifact["head_sha"] == HEAD_SHA
    assert artifact["effective_diff_sha256"] == DIFF_SHA256
    assert artifact["pr"] == 111


# --- Artifact freshness ---


def test_artifact_fresh_when_head_matches(tmp_path: Path) -> None:
    artifact = _make_artifact()
    fresh, reason = is_artifact_fresh(
        artifact, current_head_sha=HEAD_SHA, current_diff_sha256=DIFF_SHA256
    )
    assert fresh is True
    assert reason == "artifact is fresh"


def test_artifact_stale_after_head_change(tmp_path: Path) -> None:
    artifact = _make_artifact()
    fresh, reason = is_artifact_fresh(
        artifact, current_head_sha=HEAD_SHA_2, current_diff_sha256=DIFF_SHA256
    )
    assert fresh is False
    assert "head SHA changed" in reason


def test_artifact_stale_after_diff_change(tmp_path: Path) -> None:
    artifact = _make_artifact()
    fresh, reason = is_artifact_fresh(
        artifact, current_head_sha=HEAD_SHA, current_diff_sha256="e" * 64
    )
    assert fresh is False
    assert "effective diff changed" in reason


# --- Structure validation (valid cases) ---


def test_valid_verified_artifact_passes_validation(tmp_path: Path) -> None:
    artifact = _make_artifact()
    violations = validate_artifact_structure(artifact)
    assert violations == []


def test_valid_partial_artifact_passes_validation(tmp_path: Path) -> None:
    artifact = _make_artifact(
        overall="partial",
        areas=[
            {
                "name": "runner-implementation",
                "files": ["tools/agent_workflow/pr_resolve.py"],
                "status": "partially_verified",
                "implementation_evidence": ["PR helper implemented"],
                "validation_evidence": [],
                "findings": ["not all edge cases tested"],
                "remaining_risk": "some edge cases untested",
            }
        ],
        acs=[
            {
                "id": "AC-1",
                "text": "PR resolve/create path",
                "status": "partially_verified",
                "implementation_evidence": ["pr_resolve.py exists"],
                "validation_evidence": [],
                "remaining_risk": "test coverage incomplete",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert violations == []


# --- Structure validation (invalid cases) ---


def test_overall_not_verified_with_partial_area(tmp_path: Path) -> None:
    artifact = _make_artifact(
        overall="verified",
        areas=[
            {
                "name": "runner-implementation",
                "files": ["tools/agent_workflow/pr_resolve.py"],
                "status": "partially_verified",
                "implementation_evidence": ["..."],
                "validation_evidence": [],
                "findings": [],
                "remaining_risk": "incomplete",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert any("not all areas are verified" in v for v in violations)


def test_overall_not_verified_with_partial_criterion(tmp_path: Path) -> None:
    artifact = _make_artifact(
        overall="verified",
        acs=[
            {
                "id": "AC-1",
                "text": "PR resolve/create path",
                "status": "partially_verified",
                "implementation_evidence": ["..."],
                "validation_evidence": [],
                "remaining_risk": "gaps",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert any("not all acceptance criteria are verified" in v for v in violations)


def test_verified_area_must_have_evidence(tmp_path: Path) -> None:
    artifact = _make_artifact(
        areas=[
            {
                "name": "runner-implementation",
                "files": ["tools/agent_workflow/pr_resolve.py"],
                "status": "verified",
                "implementation_evidence": [],
                "validation_evidence": [],
                "findings": [],
                "remaining_risk": "",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert any("has no evidence entries" in v for v in violations)


def test_verified_ac_must_have_evidence(tmp_path: Path) -> None:
    artifact = _make_artifact(
        acs=[
            {
                "id": "AC-1",
                "text": "PR resolve/create path",
                "status": "verified",
                "implementation_evidence": [],
                "validation_evidence": [],
                "remaining_risk": "",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert any("has no evidence entries" in v for v in violations)


def test_partial_area_must_have_remaining_risk(tmp_path: Path) -> None:
    artifact = _make_artifact(
        overall="partial",
        areas=[
            {
                "name": "runner-implementation",
                "files": ["tools/agent_workflow/pr_resolve.py"],
                "status": "partially_verified",
                "implementation_evidence": ["..."],
                "validation_evidence": [],
                "findings": [],
                "remaining_risk": "",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert any("remaining_risk is empty" in v for v in violations)


def test_partial_criterion_must_have_remaining_risk(tmp_path: Path) -> None:
    artifact = _make_artifact(
        overall="partial",
        acs=[
            {
                "id": "AC-1",
                "text": "PR resolve/create path",
                "status": "partially_verified",
                "implementation_evidence": ["..."],
                "validation_evidence": [],
                "remaining_risk": "",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert any("remaining_risk is empty" in v for v in violations)


def test_invalid_status_rejected(tmp_path: Path) -> None:
    artifact = _make_artifact(
        areas=[
            {
                "name": "runner-implementation",
                "files": ["tools/agent_workflow/pr_resolve.py"],
                "status": "complete",
                "implementation_evidence": ["..."],
                "validation_evidence": ["..."],
                "findings": [],
                "remaining_risk": "",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert any("invalid status" in v for v in violations)


def test_invalid_overall_rejected(tmp_path: Path) -> None:
    artifact = _make_artifact(overall="pass")
    violations = validate_artifact_structure(artifact)
    assert any("invalid overall" in v for v in violations)


def test_missing_top_level_field_rejected(tmp_path: Path) -> None:
    artifact = _make_artifact()
    del artifact["head_sha"]
    violations = validate_artifact_structure(artifact)
    assert any("missing top-level field: head_sha" in v for v in violations)


def test_empty_areas_rejected(tmp_path: Path) -> None:
    artifact = _make_artifact(areas=[])
    violations = validate_artifact_structure(artifact)
    assert any("areas must be a non-empty list" in v for v in violations)


def test_empty_acs_rejected(tmp_path: Path) -> None:
    artifact = _make_artifact(acs=[])
    violations = validate_artifact_structure(artifact)
    assert any("acceptance_criteria must be a non-empty list" in v for v in violations)


# --- File coverage ---


def test_every_changed_file_in_some_area(tmp_path: Path) -> None:
    artifact = _make_artifact(
        areas=[
            {
                "name": "runner",
                "files": [
                    "tools/agent_workflow/pr_resolve.py",
                    "tools/agent_workflow/self_review.py",
                ],
                "status": "verified",
                "implementation_evidence": ["..."],
                "validation_evidence": ["..."],
                "findings": [],
                "remaining_risk": "",
            }
        ],
    )
    changed = [
        "tools/agent_workflow/pr_resolve.py",
        "tools/agent_workflow/self_review.py",
    ]
    uncovered = validate_file_coverage(artifact, changed)
    assert uncovered == []


def test_uncovered_file_detected(tmp_path: Path) -> None:
    artifact = _make_artifact(
        areas=[
            {
                "name": "runner",
                "files": ["tools/agent_workflow/pr_resolve.py"],
                "status": "verified",
                "implementation_evidence": ["..."],
                "validation_evidence": ["..."],
                "findings": [],
                "remaining_risk": "",
            }
        ],
    )
    changed = [
        "tools/agent_workflow/pr_resolve.py",
        "tools/agent_workflow/self_review.py",
    ]
    uncovered = validate_file_coverage(artifact, changed)
    assert "tools/agent_workflow/self_review.py" in uncovered


# --- Write and read artifacts ---


def test_write_and_read_artifact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence_dir = repo / ".agents" / "evidence.local"
    evidence_dir.mkdir(parents=True)

    artifact = _make_artifact()
    path = write_artifact(repo, artifact, task=110, head_sha=HEAD_SHA)
    assert path.exists()
    assert "task-110" in path.name
    assert HEAD_SHA[:12] in path.name

    read_back = read_artifact(path)
    assert read_back["task"] == 110
    assert read_back["head_sha"] == HEAD_SHA


def test_write_artifact_fails_without_evidence_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    # No .agents/evidence.local directory
    with pytest.raises(SelfReviewError, match="must exist"):
        write_artifact(repo, _make_artifact(), task=110, head_sha=HEAD_SHA)


def test_read_missing_artifact(tmp_path: Path) -> None:
    with pytest.raises(SelfReviewError, match="not found"):
        read_artifact(tmp_path / "nonexistent.json")


def test_read_invalid_json_artifact(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(SelfReviewError, match="invalid JSON"):
        read_artifact(path)


# --- Artifact path stability ---


def test_artifact_path_is_stable_for_same_task_and_head(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    p1 = generate_artifact_path(root, task=110, head_sha=HEAD_SHA)
    p2 = generate_artifact_path(root, task=110, head_sha=HEAD_SHA)
    assert p1 == p2


# --- Acceptance criteria completeness ---


def test_every_criterion_has_status_and_evidence(tmp_path: Path) -> None:
    artifact = _make_artifact()
    for ac in artifact["acceptance_criteria"]:
        assert ac["status"] in {"verified", "partially_verified", "not_verified"}
        assert isinstance(ac["implementation_evidence"], list)
        assert isinstance(ac["validation_evidence"], list)
        assert isinstance(ac["remaining_risk"], str)


# --- Provenance claim without validator evidence ---


def test_provenance_claim_not_verified_without_validator(tmp_path: Path) -> None:
    """A 'verified' area with no evidence entries must fail validation."""
    artifact = _make_artifact(
        areas=[
            {
                "name": "skill-provenance",
                "files": [".agents/skills/task-delivery-runner/SKILL.md"],
                "status": "verified",
                "implementation_evidence": [],
                "validation_evidence": [],
                "findings": [],
                "remaining_risk": "",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    # A verified area with zero evidence entries should fail
    assert any("has no evidence entries" in v for v in violations)


# --- Partial skill check not reported as all canonical ---


def test_partial_skill_check_not_reported_as_all_canonical(tmp_path: Path) -> None:
    """When only 2 of 4 Skills are checked, cannot claim all are canonical."""
    artifact = _make_artifact(
        overall="partial",
        areas=[
            {
                "name": "skill-canonical-state",
                "files": [
                    ".agents/skills/task-delivery-runner/SKILL.md",
                    ".agents/skills/task-pr-review-runner/SKILL.md",
                    ".agents/skills/task-closeout/SKILL.md",
                    ".agents/skills/feature-completion-audit/SKILL.md",
                ],
                "status": "partially_verified",
                "implementation_evidence": ["checked 2 of 4 Skills"],
                "validation_evidence": ["skill_validator: 2/4 pass"],
                "findings": [
                    "task-closeout and feature-completion-audit not validated"
                ],
                "remaining_risk": "2 of 4 Skills not mechanically validated",
            }
        ],
    )
    violations = validate_artifact_structure(artifact)
    assert violations == []
    assert artifact["overall"] == "partial"


# --- Diff computation ---


def test_compute_changed_files_in_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=False)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=False)
    (repo / "file1.py").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=False)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=False)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()

    (repo / "file2.py").write_text("new", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=False)
    subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=repo, check=False)

    files = compute_changed_files(repo, base_sha)
    assert "file2.py" in files


def test_compute_diff_sha256_in_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=False)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=False)
    (repo / "file1.py").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=False)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=False)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()

    digest = compute_diff_sha256(repo, base_sha)
    assert isinstance(digest, str)
    assert len(digest) == 64
