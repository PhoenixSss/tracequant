# ruff: noqa: E402, I001

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from _benchmark_helpers import REPO_ROOT, run_git_quiet

from file_identity_report import (  # type: ignore[import-not-found]
    ALLOWED_IDENTITY_FIELDS,
    file_identity_report,
)
from benchmark_common import BenchmarkError  # type: ignore[import-not-found]
from benchmark_common import generation_identity_digest
from generation_materializer import (  # type: ignore[import-not-found]
    materialize,
    parse_manifest,
    parse_run_locked_manifest,
)
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
    timestamp = "2026-08-10T00:00:00Z"
    c = generate_run_locked(c_path, REPO_ROOT, base, "test", timestamp)
    d = generate_run_locked(d_path, REPO_ROOT, base, "test", timestamp)

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
    c = generate_run_locked(c_path, REPO_ROOT, base, "test", "2026-08-10T00:00:00Z")

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
        generate_run_locked(template, REPO_ROOT, base, "test", "2026-08-10T00:00:00Z")


def test_file_identity_report_flags_blob_difference() -> None:
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, d_path = _templates()
    timestamp = "2026-08-10T00:00:00Z"
    c = generate_run_locked(c_path, REPO_ROOT, base, "test", timestamp)
    d = generate_run_locked(d_path, REPO_ROOT, base, "test", timestamp)

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
        generate_run_locked(template, REPO_ROOT, base, "test", "2026-08-10T00:00:00Z")


def test_run_lock_materializer_producer_consumer_and_independent_bundles(
    tmp_path: Path,
) -> None:
    """The actual C/D producer output is directly consumable by materializer."""
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, d_path = _templates()
    timestamp = "2026-08-10T00:00:00Z"
    c_raw = generate_run_locked(c_path, REPO_ROOT, base, "test", timestamp)
    d_raw = generate_run_locked(d_path, REPO_ROOT, base, "test", timestamp)
    c_manifest = tmp_path / "c-run-locked.json"
    d_manifest = tmp_path / "d-run-locked.json"
    c_manifest.write_text(json.dumps(c_raw, sort_keys=True), encoding="utf-8")
    d_manifest.write_text(json.dumps(d_raw, sort_keys=True), encoding="utf-8")

    c = parse_run_locked_manifest(c_manifest)
    d = parse_run_locked_manifest(d_manifest)
    c_store = tmp_path / "c-bundle"
    d_store = tmp_path / "d-bundle"
    c_bundle = materialize(c, REPO_ROOT, c_store, expected_source_sha=base)
    d_bundle = materialize(d, REPO_ROOT, d_store, expected_source_sha=base)

    assert c_store != d_store
    assert c_bundle != d_bundle
    assert c_bundle["tree_identity"] == d_bundle["tree_identity"]
    assert (
        c_bundle["generation_identity_digest"] == d_bundle["generation_identity_digest"]
    )
    assert c_bundle["files"] == d_bundle["files"]
    c_files = {item["path"]: item for item in c_bundle["files"]}
    d_files = {item["path"]: item for item in d_bundle["files"]}
    assert set(c_files) == set(d_files)
    for path in c_files:
        c_file = c_store / path
        d_file = d_store / path
        assert not c_file.is_symlink()
        assert not d_file.is_symlink()
        assert c_file.read_bytes() == d_file.read_bytes()
        assert os.stat(c_file).st_mode & 0o777 == os.stat(d_file).st_mode & 0o777
        assert os.stat(c_file).st_ino != os.stat(d_file).st_ino

    with pytest.raises(BenchmarkError, match="cross-generation reuse"):
        materialize(d, REPO_ROOT, c_store, expected_source_sha=base)

    report = file_identity_report(c_raw, d_raw)
    assert report["per_path_file_mode_identical"] is True
    assert report["generation_identity_digest_identical"] is True
    assert report["disposition"] == "pass"


def test_manifest_discriminator_and_required_fields_fail_closed(tmp_path: Path) -> None:
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, _ = _templates()
    raw = generate_run_locked(c_path, REPO_ROOT, base, "test", "2026-08-10T00:00:00Z")

    wrong_kind = dict(raw)
    wrong_kind["kind"] = "pinned"
    wrong_kind_path = tmp_path / "wrong-kind.json"
    wrong_kind_path.write_text(json.dumps(wrong_kind), encoding="utf-8")
    with pytest.raises(
        BenchmarkError, match="missing required property 'workflow_source'"
    ):
        parse_manifest(wrong_kind_path)

    missing_run_locked = dict(raw)
    del missing_run_locked["generated_at_utc"]
    missing_run_locked_path = tmp_path / "missing-run-lock-field.json"
    missing_run_locked_path.write_text(json.dumps(missing_run_locked), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="generated_at_utc"):
        parse_run_locked_manifest(missing_run_locked_path)

    missing_pinned = {
        "schema_version": 1,
        "protocol_identity": "task-65-round-2-v2",
        "kind": "pinned",
        "generation_id": "A",
        "agent_identity": "test",
        "closure": {"definition": "test", "paths": []},
        "invocation": {},
        "permission_profile": {},
        "known_limitations": [],
    }
    missing_pinned_path = tmp_path / "missing-pinned-field.json"
    missing_pinned_path.write_text(json.dumps(missing_pinned), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="workflow_source"):
        parse_manifest(missing_pinned_path)

    template_kind = dict(raw)
    template_kind["kind"] = "template"
    template_kind_path = tmp_path / "template-kind.json"
    template_kind_path.write_text(json.dumps(template_kind), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="unsupported manifest kind"):
        parse_manifest(template_kind_path)


def test_cannot_materialize_d_values_as_c_or_reuse_c_store_as_d(
    tmp_path: Path,
) -> None:
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, d_path = _templates()
    timestamp = "2026-08-10T00:00:00Z"
    c_raw = generate_run_locked(c_path, REPO_ROOT, base, "test", timestamp)
    d_raw = generate_run_locked(d_path, REPO_ROOT, base, "test", timestamp)

    swapped = dict(c_raw)
    swapped["agent_identity"] = d_raw["agent_identity"]
    swapped_path = tmp_path / "c-with-d-values.json"
    swapped_path.write_text(json.dumps(swapped), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="incompatible agent identity"):
        parse_run_locked_manifest(swapped_path)

    c_manifest = tmp_path / "c.json"
    c_manifest.write_text(json.dumps(c_raw), encoding="utf-8")
    c_store = tmp_path / "c-store"
    materialize(parse_run_locked_manifest(c_manifest), REPO_ROOT, c_store)
    d_manifest = tmp_path / "d.json"
    d_manifest.write_text(json.dumps(d_raw), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="cross-generation reuse"):
        materialize(parse_run_locked_manifest(d_manifest), REPO_ROOT, c_store)


def test_run_locked_integrity_fields_fail_closed(tmp_path: Path) -> None:
    base = run_git_quiet("rev-parse", "HEAD").stdout.strip()
    c_path, _ = _templates()
    raw = generate_run_locked(c_path, REPO_ROOT, base, "test", "2026-08-10T00:00:00Z")
    entry = raw["closure"]["paths"][0]

    for field, value, message in (
        ("blob_id", "0" * 40, "manifest blob"),
        ("sha256", "0" * 64, "manifest sha256"),
        (
            "file_mode",
            "100755" if entry["file_mode"] == "100644" else "100644",
            "manifest mode",
        ),
    ):
        tampered = json.loads(json.dumps(raw))
        tampered["closure"]["paths"][0][field] = value
        tampered["generation_identity_digest"] = generation_identity_digest(
            base, tampered["closure"]["paths"]
        )
        path = tmp_path / f"tampered-{field}.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(BenchmarkError, match=message):
            materialize(parse_run_locked_manifest(path), REPO_ROOT, tmp_path / field)

    wrong_source = json.loads(json.dumps(raw))
    wrong_source["generation_identity_digest"] = generation_identity_digest(
        "0" * 40, wrong_source["closure"]["paths"]
    )
    wrong_source["benchmark_base_sha"] = "0" * 40
    wrong_source_path = tmp_path / "wrong-source.json"
    wrong_source_path.write_text(json.dumps(wrong_source), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="source SHA mismatch"):
        materialize(
            parse_run_locked_manifest(wrong_source_path),
            REPO_ROOT,
            tmp_path / "wrong-source-store",
            expected_source_sha=base,
        )
