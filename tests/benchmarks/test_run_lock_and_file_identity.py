# ruff: noqa: E402, I001

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _benchmark_helpers import REPO_ROOT, run_git_quiet

from file_identity_report import (  # type: ignore[import-not-found]
    ALLOWED_IDENTITY_FIELDS,
    file_identity_report,
)
from benchmark_common import BenchmarkError  # type: ignore[import-not-found]
from run_lock import generate_run_locked  # type: ignore[import-not-found]

MANIFESTS = REPO_ROOT / "benchmarks" / "task-65-round-2-v2" / "manifests"


def _templates() -> tuple[Path, Path]:
    return (
        MANIFESTS / "generation-c-current-template-manifest.json",
        MANIFESTS / "generation-d-current-template-manifest.json",
    )


def test_run_lock_generates_cd_identical_closure() -> None:
    """Run-lock C and D from their templates at the current main HEAD.

    Uses a real, locally available commit (current HEAD) so the test is
    CI-safe; the template's source selector is resolved through the
    ``--benchmark-base-sha`` argument at freeze time.
    """
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, d_path = _templates()
    c = generate_run_locked(c_path, REPO_ROOT, base, "test")
    d = generate_run_locked(d_path, REPO_ROOT, base, "test")

    assert c["generation_id"] == "C"
    assert d["generation_id"] == "D"
    assert c["benchmark_base_sha"] == d["benchmark_base_sha"] == base
    assert c["kind"] == d["kind"] == "run_locked"

    report = file_identity_report(c, d)
    assert report["path_set_identical"] is True
    assert report["per_path_blob_identical"] is True
    assert report["per_path_sha256_identical"] is True
    assert report["no_per_agent_pruned_closure"] is True
    assert report["human_gate"] is False
    assert report["disposition"] == "pass"
    assert report["unexpected_field_differences"] == []
    # Only identity fields differ.
    assert set(report["allowed_identity_differences"]) <= ALLOWED_IDENTITY_FIELDS


def test_run_lock_role_classification_and_identity_explicit() -> None:
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, _ = _templates()
    c = generate_run_locked(c_path, REPO_ROOT, base, "test")

    roles: dict[str, int] = {}
    actions: set[str] = set()
    for entry in c["closure"]["paths"]:
        roles[entry["role"]] = roles.get(entry["role"], 0) + 1
        actions.add(entry["projection_action"])
    # Every generation-control-plane path is installed or explicitly absent;
    # no run-locked control-plane path may inherit the business base.
    assert actions == {"INSTALL_GENERATION_VERSION"}
    assert "IDENTITY_REQUIRED" in roles
    assert "OPTIONAL_HISTORICAL_LIMITATION" in roles

    # Every installed path carries real blob/sha256/mode facts.
    for entry in c["closure"]["paths"]:
        assert entry["exists_at_source"] is True
        assert len(entry["blob_id"]) == 40
        assert len(entry["sha256"]) == 64
        assert entry["file_mode"] in {"100644", "100755"}


def test_run_lock_rejects_control_plane_inherit(tmp_path: Path) -> None:
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, _ = _templates()
    raw = json.loads(c_path.read_text(encoding="utf-8"))
    raw["role_classification_rules"]["projection_defaults"][
        "OPTIONAL_HISTORICAL_LIMITATION"
    ] = "INHERIT_BUSINESS_BASE"
    template = tmp_path / "invalid-control-plane-inherit.json"
    template.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="GENERATION_CONTROL_PLANE"):
        generate_run_locked(template, REPO_ROOT, base, "test")


def test_file_identity_report_flags_blob_difference() -> None:
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, d_path = _templates()
    c = generate_run_locked(c_path, REPO_ROOT, base, "test")
    d = generate_run_locked(d_path, REPO_ROOT, base, "test")

    # Tamper: change one path's sha256 in D -> human gate.
    d["closure"]["paths"][0]["sha256"] = "f" * 64
    d["closure"]["paths"][0]["blob_id"] = "0" * 40
    report = file_identity_report(c, d)
    assert report["human_gate"] is True
    assert report["per_path_sha256_identical"] is False
    assert report["per_path_blob_identical"] is False


def test_run_lock_rejects_unclassified_control_plane_path(tmp_path: Path) -> None:
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, _ = _templates()
    raw = json.loads(c_path.read_text(encoding="utf-8"))
    raw["closure_derivation_rules"]["path_classes"] = [
        item
        for item in raw["closure_derivation_rules"]["path_classes"]
        if item["glob"] != "tests/tools/**"
    ]
    template = tmp_path / "incomplete-template.json"
    template.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    with pytest.raises(BenchmarkError, match="test_agent_neutral_workflow"):
        generate_run_locked(template, REPO_ROOT, base, "test")
