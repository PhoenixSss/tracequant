# ruff: noqa: E402, I001

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _benchmark_helpers import (
    cat_blob,
    commit_all,
    init_repo,
    ls_tree_blob,
    run_git_quiet,
    sha256_bytes,
)

from benchmark_common import BenchmarkError  # type: ignore[import-not-found]
from control_base_validator import (  # type: ignore[import-not-found]
    validate_ab_from_file,
    validate_cd,
)
from generation_materializer import (  # type: ignore[import-not-found]
    parse_pinned_manifest,
)

# ---------- ephemeral repo + manifest builders (A/B control-base style) ----------


def _setup_repo(repo: Path) -> str:
    """Base tree: AGENTS.md, tools/agent_workflow/runner.py (100755), business/source.py,
    .claude/settings.json (to be ENSURE_ABSENT for the synthetic commit).

    An initial commit precedes the base commit so ``base_sha^`` always
    resolves (needed by the empty-synthetic-commit test).
    """
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    commit_all(repo, "init")
    (repo / "AGENTS.md").write_text("base agents\n", encoding="utf-8")
    (repo / "tools" / "agent_workflow").mkdir(parents=True)
    (repo / "tools" / "agent_workflow" / "runner.py").write_text(
        "old runner\n", encoding="utf-8"
    )
    (repo / "tools" / "agent_workflow" / "runner.py").chmod(0o755)
    (repo / "business").mkdir()
    (repo / "business" / "source.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    return commit_all(repo, "base")


def _build_ab_manifest(
    repo: Path,
    base_sha: str,
    manifest_path: Path,
    *,
    source_sha: str | None = None,
) -> None:
    """A-style manifest: INSTALL AGENTS.md + tools/agent_workflow/runner.py, ENSURE_ABSENT
    .claude, INHERIT business/source.py."""
    source_sha = source_sha or base_sha
    mode, blob = ls_tree_blob(repo, source_sha, "AGENTS.md")
    runner_mode, runner_blob = ls_tree_blob(
        repo, source_sha, "tools/agent_workflow/runner.py"
    )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "protocol_identity": "task-65-round-2-v2",
        "kind": "pinned",
        "generation_id": "TEST",
        "agent_identity": "test",
        "workflow_source": {"sha": source_sha, "label": "TEST_SHA"},
        "closure": {
            "definition": "test",
            "paths": [
                {
                    "path": "AGENTS.md",
                    "role": "IDENTITY_REQUIRED",
                    "projection_action": "INSTALL_GENERATION_VERSION",
                    "projection_reason": None,
                    "exists_at_source": True,
                    "blob_id": blob,
                    "sha256": sha256_bytes(cat_blob(repo, blob)),
                    "file_mode": mode,
                },
                {
                    "path": "tools/agent_workflow/runner.py",
                    "role": "EXECUTION_REQUIRED",
                    "projection_action": "INSTALL_GENERATION_VERSION",
                    "projection_reason": None,
                    "exists_at_source": True,
                    "blob_id": runner_blob,
                    "sha256": sha256_bytes(cat_blob(repo, runner_blob)),
                    "file_mode": runner_mode,
                },
                {
                    "path": ".claude",
                    "role": "IDENTITY_REQUIRED",
                    "projection_action": "ENSURE_ABSENT",
                    "projection_reason": "test",
                    "exists_at_source": False,
                },
            ],
        },
        "invocation": {},
        "permission_profile": {},
        "known_limitations": [],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_control_plane(
    repo: Path,
    manifest_path: Path,
    *,
    extra_file: str | None = None,
    wrong_blob: bool = False,
    benchmark_base_sha: str | None = None,
) -> str:
    """Apply the manifest-declared projection on top of the base commit and
    commit exactly one synthetic control-plane commit; return its SHA."""
    manifest = parse_pinned_manifest(manifest_path)
    base_sha = manifest.workflow_source_sha
    installed = {
        entry.path: entry.blob_id
        for entry in manifest.paths
        if entry.projection_action == "INSTALL_GENERATION_VERSION"
    }
    run_git_quiet(
        "checkout",
        "-q",
        "--detach",
        benchmark_base_sha or base_sha,
        cwd=repo,
    )
    for path, blob_id in installed.items():
        data = cat_blob(repo, blob_id)
        if wrong_blob and path == "AGENTS.md":
            data = b"tampered\n"
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for entry in manifest.paths:
        if entry.projection_action == "ENSURE_ABSENT":
            target = repo / entry.path
            if target.exists():
                run_git_quiet("rm", "-q", "-r", entry.path, cwd=repo)
    if extra_file is not None:
        target = repo / extra_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("unexpected\n", encoding="utf-8")
    run_git_quiet("add", "-A", cwd=repo)
    run_git_quiet("commit", "-q", "-m", "synthetic control-plane commit", cwd=repo)
    return run_git_quiet("rev-parse", "HEAD", cwd=repo).stdout.strip()


# ---------- A/B validator tests ----------


def test_ab_validate_pass(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, base_sha, manifest_path)
    control_sha = _write_control_plane(repo, manifest_path)

    result = validate_ab_from_file(
        manifest_path, repo, base_sha, control_sha, branch=None
    )
    assert result.disposition == "pass", result.gates
    assert all(g["status"] == "pass" for g in result.gates)


def test_ab_validate_fail_on_wrong_parent(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, base_sha, manifest_path)
    _write_control_plane(repo, manifest_path)

    # A second unrelated commit on top -> control-base parent != base.
    (repo / "other.txt").write_text("x\n", encoding="utf-8")
    other_sha = commit_all(repo, "unrelated")

    result = validate_ab_from_file(
        manifest_path, repo, base_sha, other_sha, branch=None
    )
    assert result.disposition == "HUMAN GATE"
    names = {g["name"] for g in result.gates if g["status"] == "fail"}
    assert "parent_equals_benchmark_base" in names


def test_ab_validate_fails_on_control_plane_inherit(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, base_sha, manifest_path)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in raw["closure"]["paths"]:
        if entry["path"] == "tools/agent_workflow/runner.py":
            entry["projection_action"] = "INHERIT_BUSINESS_BASE"
            break
    manifest_path.write_text(
        json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    control_sha = _write_control_plane(repo, manifest_path)

    result = validate_ab_from_file(
        manifest_path, repo, base_sha, control_sha, branch=None
    )
    assert result.disposition == "HUMAN GATE"
    assert any(
        gate["name"] == "generation_control_plane_must_not_inherit"
        and gate["status"] == "fail"
        for gate in result.gates
    )


def test_ab_validate_fail_on_unexpected_path(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, base_sha, manifest_path)
    control_sha = _write_control_plane(
        repo, manifest_path, extra_file="business/source.py"
    )

    # business/source.py is inherited (not managed) -> changing it is an
    # unexpected changed path -> HUMAN GATE.
    result = validate_ab_from_file(
        manifest_path, repo, base_sha, control_sha, branch=None
    )
    assert result.disposition == "HUMAN GATE"
    names = {g["name"] for g in result.gates if g["status"] == "fail"}
    assert "diff_within_runtime_control_plane_paths" in names


def test_ab_validate_fail_on_blob_mismatch(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, base_sha, manifest_path)
    control_sha = _write_control_plane(repo, manifest_path, wrong_blob=True)

    result = validate_ab_from_file(
        manifest_path, repo, base_sha, control_sha, branch=None
    )
    assert result.disposition == "HUMAN GATE"
    names = {g["name"] for g in result.gates if g["status"] == "fail"}
    assert "projection_actions_mechanically_verified" in names


def test_ab_validate_fail_on_empty_synthetic_commit(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, base_sha, manifest_path)

    # Control base == base (no synthetic commit) -> empty diff + .claude
    # still present -> HUMAN GATE.
    result = validate_ab_from_file(manifest_path, repo, base_sha, base_sha, branch=None)
    assert result.disposition == "HUMAN GATE"
    names = {g["name"] for g in result.gates if g["status"] == "fail"}
    assert "synthetic_commit_present" in names
    assert "expected_absent_paths_absent" in names


def test_ab_validate_worktree_clean_gate(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, base_sha, manifest_path)
    control_sha = _write_control_plane(repo, manifest_path)

    # A dirty worktree fails the worktree-clean gate when a branch is given.
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    result = validate_ab_from_file(
        manifest_path, repo, base_sha, control_sha, branch="experiment/test"
    )
    assert result.disposition == "HUMAN GATE"
    assert any(
        g["name"] == "control_base_worktree_clean" and g["status"] == "fail"
        for g in result.gates
    )


def test_ab_validate_rejects_undeclared_current_only_workflow_path(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "repo")
    source_sha = _setup_repo(repo)
    (repo / "tools" / "agent_workflow" / "current_only.py").write_text(
        "current = True\n", encoding="utf-8"
    )
    business_base_sha = commit_all(repo, "current workflow path")
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, business_base_sha, manifest_path, source_sha=source_sha)
    control_sha = _write_control_plane(
        repo,
        manifest_path,
        benchmark_base_sha=business_base_sha,
    )

    result = validate_ab_from_file(
        manifest_path, repo, business_base_sha, control_sha, branch=None
    )
    assert result.disposition == "HUMAN GATE"
    assert any(
        g["name"] == "runtime_control_plane_projection_complete"
        and g["status"] == "fail"
        and "tools/agent_workflow/current_only.py" in g.get("detail", "")
        for g in result.gates
    )


def test_ab_validate_rejects_source_absent_control_plane_inherit(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "repo")
    source_sha = _setup_repo(repo)
    (repo / "tools" / "agent_workflow" / "allowed_current.py").write_text(
        "allowed = True\n", encoding="utf-8"
    )
    business_base_sha = commit_all(repo, "business path")
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, business_base_sha, manifest_path, source_sha=source_sha)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["closure"]["source_absent_inherit_allowlist"] = [
        "tools/agent_workflow/allowed_current.py"
    ]
    raw["closure"]["paths"].append(
        {
            "path": "tools/agent_workflow/allowed_current.py",
            "role": "VALIDATION_PRESENCE_REQUIRED",
            "projection_action": "INHERIT_BUSINESS_BASE",
            "projection_reason": "canonical test allowlist",
            "exists_at_source": False,
        }
    )
    manifest_path.write_text(
        json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    control_sha = _write_control_plane(
        repo,
        manifest_path,
        benchmark_base_sha=business_base_sha,
    )

    result = validate_ab_from_file(
        manifest_path, repo, business_base_sha, control_sha, branch=None
    )
    assert result.disposition == "HUMAN GATE"
    assert any(
        gate["name"] == "generation_control_plane_must_not_inherit"
        and gate["status"] == "fail"
        for gate in result.gates
    )


def test_ab_validate_rejects_incomplete_projection_manifest(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    manifest_path = tmp_path / "manifest.json"
    _build_ab_manifest(repo, base_sha, manifest_path)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["closure"]["paths"] = [
        entry
        for entry in raw["closure"]["paths"]
        if entry["path"] != "tools/agent_workflow/runner.py"
    ]
    manifest_path.write_text(
        json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    result = validate_ab_from_file(manifest_path, repo, base_sha, base_sha, branch=None)
    assert result.disposition == "HUMAN GATE"
    assert any(
        g["name"] == "runtime_control_plane_projection_complete"
        and "tools/agent_workflow/runner.py" in g.get("detail", "")
        for g in result.gates
    )


# ---------- C/D validator tests ----------


def test_cd_validate_pass_when_tip_equals_base(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    result = validate_cd(repo, base_sha, base_sha, branch=None)
    assert result.disposition == "pass"


def test_cd_validate_fail_when_tip_differs(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    (repo / "extra.txt").write_text("x\n", encoding="utf-8")
    other_sha = commit_all(repo, "extra")
    result = validate_cd(repo, base_sha, other_sha, branch=None)
    assert result.disposition == "HUMAN GATE"
    assert any(
        g["name"] == "cd_tip_equals_benchmark_base" and g["status"] == "fail"
        for g in result.gates
    )


def test_validate_ab_missing_manifest_fails_closed(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    base_sha = _setup_repo(repo)
    with pytest.raises(BenchmarkError):
        validate_ab_from_file(
            tmp_path / "missing.json", repo, base_sha, base_sha, branch=None
        )
