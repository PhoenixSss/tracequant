# ruff: noqa: E402, I001

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _benchmark_helpers import (
    REPO_ROOT,
    cat_blob,
    commit_all,
    init_repo,
    ls_tree_blob,
    run_git_quiet,
    sha256_bytes,
)

from benchmark_common import BenchmarkError  # type: ignore[import-not-found]
from benchmark_common import generation_identity_digest
from generation_materializer import (  # type: ignore[import-not-found]
    materialize,
    parse_run_locked_manifest,
    parse_pinned_manifest,
)


def _build_sample_manifest(repo: Path) -> dict[str, object]:
    """Pinned manifest over real objects committed in ``repo``.

    Mirrors the A/B manifest shape: one INSTALL file, one ENSURE_ABSENT
    directory, one INHERIT file.
    """
    (repo / "AGENTS.md").write_text("base agents\n", encoding="utf-8")
    (repo / "tools").mkdir()
    (repo / "tools" / "runner.py").write_text(
        "#!/usr/bin/env python3\nprint('run')\n", encoding="utf-8"
    )
    (repo / "tools" / "runner.py").chmod(0o755)
    (repo / "business").mkdir()
    (repo / "business" / "source.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    base_sha = commit_all(repo, "base")

    installed = ("tools/runner.py", ls_tree_blob(repo, base_sha, "tools/runner.py"))
    agents = ("AGENTS.md", ls_tree_blob(repo, base_sha, "AGENTS.md"))
    inherit = ("business/source.py", ls_tree_blob(repo, base_sha, "business/source.py"))

    def entry(
        path: str, mode: str, blob_id: str, role: str, action: str
    ) -> dict[str, object]:
        return {
            "path": path,
            "role": role,
            "projection_action": action,
            "projection_reason": None,
            "exists_at_source": True,
            "blob_id": blob_id,
            "sha256": sha256_bytes(cat_blob(repo, blob_id)),
            "file_mode": mode,
        }

    manifest: dict[str, object] = {
        "schema_version": 1,
        "protocol_identity": "task-65-round-2-v2",
        "kind": "pinned",
        "generation_id": "X",
        "agent_identity": "test",
        "workflow_source": {"sha": base_sha, "label": "TEST_SHA"},
        "closure": {
            "definition": "test closure",
            "paths": [
                entry(
                    agents[0],
                    *agents[1],
                    "IDENTITY_REQUIRED",
                    "INSTALL_GENERATION_VERSION",
                ),
                entry(
                    installed[0],
                    *installed[1],
                    "EXECUTION_REQUIRED",
                    "INSTALL_GENERATION_VERSION",
                ),
                entry(
                    inherit[0],
                    *inherit[1],
                    "VALIDATION_PRESENCE_REQUIRED",
                    "INHERIT_BUSINESS_BASE",
                ),
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
    return manifest


def _write_manifest(repo: Path, manifest_path: Path) -> None:
    manifest = _build_sample_manifest(repo)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_materialize_verbatim_modes_idempotent_and_deterministic(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "repo")
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(repo, manifest_path)
    store = tmp_path / "fixtures"
    manifest = parse_pinned_manifest(manifest_path)

    bundle1 = materialize(manifest, repo, store)
    assert bundle1["file_count"] == 3

    # Verbatim bytes and git file modes.
    assert (store / "AGENTS.md").read_bytes() == b"base agents\n"
    assert (
        store / "tools" / "runner.py"
    ).read_bytes() == b"#!/usr/bin/env python3\nprint('run')\n"
    assert (store / "tools" / "runner.py").stat().st_mode & 0o777 == 0o755
    assert (store / "business" / "source.py").read_bytes() == b"x = 1\n"
    assert not (store / ".claude").exists()

    # Deterministic + idempotent: same input -> identical tree identity, and
    # a second run changes nothing and yields the same bundle.
    bundle2 = materialize(parse_pinned_manifest(manifest_path), repo, store)
    assert bundle2["tree_identity"] == bundle1["tree_identity"]
    assert bundle2["files"] == bundle1["files"]
    assert (store / "AGENTS.md").read_bytes() == b"base agents\n"

    # Two independent materializations from the same source+manifest produce
    # byte-identical bundles (identical tree identity).
    store2 = tmp_path / "fixtures2"
    bundle3 = materialize(parse_pinned_manifest(manifest_path), repo, store2)
    assert bundle3["tree_identity"] == bundle1["tree_identity"]


def test_materialize_fail_closed_on_sha_mismatch(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(repo, manifest_path)

    # Tamper with the declared sha256 of AGENTS.md -> fail closed.
    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in tampered["closure"]["paths"]:
        if entry["path"] == "AGENTS.md":
            entry["sha256"] = "0" * 64
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="manifest sha256"):
        materialize(parse_pinned_manifest(tampered_path), repo, tmp_path / "store")


def test_materialize_fail_closed_on_source_missing(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(repo, manifest_path)
    manifest = parse_pinned_manifest(manifest_path)

    # Materialize against a different (empty) repo: the pinned source commit
    # does not exist there -> source path missing -> fail closed.
    other = init_repo(tmp_path / "other")
    (other / "file.txt").write_text("x\n", encoding="utf-8")
    commit_all(other, "unrelated")
    with pytest.raises(BenchmarkError, match="source_path_missing|ls-tree failed"):
        materialize(manifest, other, tmp_path / "store")


def test_materialize_against_current_repo_objects(tmp_path: Path) -> None:
    """Materialize a manifest over real current-main objects (CI-safe).

    Uses files that exist on current main; historical objects are never
    required, so this always runs.
    """
    head = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    entries: list[dict[str, object]] = []
    for path in ("AGENTS.md", ".claude/settings.json"):
        mode, blob_id = ls_tree_blob(REPO_ROOT, head, path)
        entries.append(
            {
                "path": path,
                "role": "IDENTITY_REQUIRED"
                if path == "AGENTS.md"
                else "VALIDATION_PRESENCE_REQUIRED",
                "projection_action": "INSTALL_GENERATION_VERSION",
                "projection_reason": None,
                "exists_at_source": True,
                "blob_id": blob_id,
                "sha256": sha256_bytes(cat_blob(REPO_ROOT, blob_id)),
                "file_mode": mode,
            }
        )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "protocol_identity": "task-65-round-2-v2",
        "kind": "pinned",
        "generation_id": "CURRENT",
        "agent_identity": "test",
        "workflow_source": {"sha": head, "label": "CURRENT_MAIN"},
        "closure": {"definition": "current-main smoke", "paths": entries},
        "invocation": {},
        "permission_profile": {},
        "known_limitations": [],
    }
    manifest_path = tmp_path / "current.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    store = tmp_path / "store"
    bundle = materialize(parse_pinned_manifest(manifest_path), REPO_ROOT, store)
    assert bundle["file_count"] == 2
    assert (store / "AGENTS.md").read_bytes() == cat_blob(
        REPO_ROOT, ls_tree_blob(REPO_ROOT, head, "AGENTS.md")[1]
    )
    assert (store / ".claude" / "settings.json").read_bytes() == cat_blob(
        REPO_ROOT, ls_tree_blob(REPO_ROOT, head, ".claude/settings.json")[1]
    )


def test_run_locked_materializer_never_falls_back_to_mutable_current_tree(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "repo")
    locked_file = repo / "locked.txt"
    locked_file.write_text("locked source\n", encoding="utf-8")
    base = commit_all(repo, "locked source")
    mode, blob_id = ls_tree_blob(repo, base, "locked.txt")
    locked_file.write_text("mutable current\n", encoding="utf-8")
    commit_all(repo, "mutable current")

    entry = {
        "path": "locked.txt",
        "role": "EXECUTION_REQUIRED",
        "projection_action": "INSTALL_GENERATION_VERSION",
        "exists_at_source": True,
        "blob_id": blob_id,
        "sha256": sha256_bytes(cat_blob(repo, blob_id)),
        "file_mode": mode,
    }
    raw: dict[str, object] = {
        "schema_version": 1,
        "protocol_identity": "task-65-round-2-v2",
        "kind": "run_locked",
        "generation_id": "C",
        "agent_identity": {"agent": "codex"},
        "source_selector": {"kind": "fixed-commit", "ref": "BENCHMARK_BASE_SHA"},
        "benchmark_base_sha": base,
        "generated_by": "test",
        "generated_at_utc": "2026-08-10T00:00:00Z",
        "closure": {"definition": {}, "paths": [entry]},
        "validation_contract": {},
        "invocation_contract": {},
    }
    raw["generation_identity_digest"] = generation_identity_digest(base, [entry])
    manifest_path = tmp_path / "locked.json"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    bundle = materialize(
        parse_run_locked_manifest(manifest_path),
        repo,
        tmp_path / "store",
    )
    assert bundle["source_sha"] == base
    assert (tmp_path / "store" / "locked.txt").read_bytes() == b"locked source\n"
