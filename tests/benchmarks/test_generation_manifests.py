# ruff: noqa: E402, I001

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, cast

import pytest

from _benchmark_helpers import REPO_ROOT, cat_blob, ls_tree_blob, sha256_bytes

from benchmark_common import load_json, validate_basic  # type: ignore[import-not-found]
from generation_materializer import (  # type: ignore[import-not-found]
    PINNED_SCHEMA,
    parse_pinned_manifest,
)
from runtime_control_plane import (  # type: ignore[import-not-found]
    CONDUCTOR_BENCHMARK_PATH_CLASSES,
    CONDUCTOR_BENCHMARK_TOOLING,
    GENERATION_CONTROL_PLANE,
    covering_entry_paths,
    managed_runtime_control_plane_paths,
    ownership_class,
    runtime_control_plane_paths,
)
from run_lock import TEMPLATE_SCHEMA  # type: ignore[import-not-found]

MANIFESTS = REPO_ROOT / "benchmarks" / "task-65-round-2-v2" / "manifests"

A_SOURCE_SHA = "a492f0b334f950f2613b4b2204e96bef413355be"
B_SOURCE_SHA = "e4a38d8404b6ad935fc24430a70e715b4504aa57"


def _manifest(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], load_json(MANIFESTS / name))


def _object_available(object_id: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", object_id],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def test_a_manifest_structure_and_schema() -> None:
    manifest = _manifest("generation-a-pinned-manifest.json")
    validate_basic(manifest, PINNED_SCHEMA, "manifest-a")
    parsed = parse_pinned_manifest(MANIFESTS / "generation-a-pinned-manifest.json")
    assert parsed.generation_id == "A"
    assert parsed.workflow_source_sha == A_SOURCE_SHA
    assert parsed.source_label == "A_WORKFLOW_SOURCE_SHA"
    assert len(parsed.paths) == 123

    absent = [
        entry.path
        for entry in parsed.paths
        if entry.projection_action == "ENSURE_ABSENT"
    ]
    assert {".claude", ".codex", "CLAUDE.md"} <= set(absent)
    installed = [
        entry.path
        for entry in parsed.paths
        if entry.projection_action == "INSTALL_GENERATION_VERSION"
    ]
    assert len(installed) == 29
    assert "AGENTS.md" in installed
    assert "tools/agent_workflow/trusted_runner.py" in installed
    # A has no unified runner and no pr_resolve.py.
    assert "tools/agent_workflow/pr_resolve.py" not in installed
    assert any(
        entry.path == "tools/agent_workflow/pr_resolve.py"
        and entry.projection_action == "ENSURE_ABSENT"
        for entry in parsed.paths
    )
    assert any(
        entry.path == "tests/tools/test_agent_neutral_workflow.py"
        and entry.projection_action == "ENSURE_ABSENT"
        for entry in parsed.paths
    )

    # Class 2 exclusion is a declared known limitation.
    assert any("Class 2" in item for item in parsed.known_limitations)

    for prefix in CONDUCTOR_BENCHMARK_PATH_CLASSES:
        entry = next(item for item in parsed.paths if item.path == prefix.rstrip("/"))
        assert entry.projection_action == "ENSURE_ABSENT"
        assert entry.role == CONDUCTOR_BENCHMARK_TOOLING


def test_b_manifest_structure_and_schema() -> None:
    manifest = _manifest("generation-b-pinned-manifest.json")
    validate_basic(manifest, PINNED_SCHEMA, "manifest-b")
    parsed = parse_pinned_manifest(MANIFESTS / "generation-b-pinned-manifest.json")
    assert parsed.generation_id == "B"
    assert parsed.workflow_source_sha == B_SOURCE_SHA
    assert len(parsed.paths) == 126

    inherit = [
        entry.path
        for entry in parsed.paths
        if entry.projection_action == "INHERIT_BUSINESS_BASE"
    ]
    assert len(inherit) == 5
    assert ".agents/skills/task-delivery/SKILL.md" in inherit
    assert ".agents/skills/task-pr-review/SKILL.md" in inherit
    assert "docs/workflows/task-skill-runner-migration/README.md" in inherit
    assert "tools/agent_workflow/pr_resolve.py" in [
        entry.path for entry in parsed.paths if entry.role == "EXECUTION_REQUIRED"
    ]

    # The four stale references are recorded verbatim as known limitations.
    stale_mentions = [
        "task-workflow-telemetry.md",
        "trusted_runner.py",
        "rollback-and-compatibility",
        "task-material-register.md",
    ]
    joined = "\n".join(parsed.known_limitations)
    for mention in stale_mentions:
        assert mention in joined


def test_path_ownership_separates_business_generation_and_conductor_namespaces() -> (
    None
):
    assert (
        ownership_class(
            "benchmarks/task-65-round-2-v2/manifests/generation-a-pinned-manifest.json"
        )
        == CONDUCTOR_BENCHMARK_TOOLING
    )
    assert ownership_class("tests/benchmarks/test_materializer.py") == (
        CONDUCTOR_BENCHMARK_TOOLING
    )
    assert ownership_class("docs/workflows/wsl2-validation-runner/README.md") == (
        GENERATION_CONTROL_PLANE
    )
    assert ownership_class("src/tracequant/config.py") == "BUSINESS_SNAPSHOT"


def test_a_projection_covers_every_current_only_workflow_path() -> None:
    if not _object_available(A_SOURCE_SHA):
        pytest.skip("historical A source object unavailable in this checkout")
    parsed = parse_pinned_manifest(MANIFESTS / "generation-a-pinned-manifest.json")
    base = run_git_head()
    current_paths = runtime_control_plane_paths(REPO_ROOT, base, include_conductor=True)
    managed_paths = managed_runtime_control_plane_paths(REPO_ROOT, base, A_SOURCE_SHA)
    entries = {entry.path: entry for entry in parsed.paths}

    assert managed_paths >= current_paths
    for path in sorted(
        current_paths
        - runtime_control_plane_paths(REPO_ROOT, A_SOURCE_SHA, include_conductor=True)
    ):
        covered = covering_entry_paths(entries, path)
        assert len(covered) == 1, path
        assert entries[covered[0]].projection_action == "ENSURE_ABSENT", path

    for path in (
        "tools/agent_workflow/pr_resolve.py",
        ".claude/settings.json",
        ".codex/rules/tracequant-wsl-evidence.rules",
        "CLAUDE.md",
    ):
        covered = covering_entry_paths(entries, path)
        assert len(covered) == 1
        assert entries[covered[0]].projection_action == "ENSURE_ABSENT"


def test_b_projection_audits_post_b_workflow_delta() -> None:
    if not _object_available(B_SOURCE_SHA):
        pytest.skip("historical B source object unavailable in this checkout")
    parsed = parse_pinned_manifest(MANIFESTS / "generation-b-pinned-manifest.json")
    base = run_git_head()
    current_paths = runtime_control_plane_paths(REPO_ROOT, base)
    historical_paths = runtime_control_plane_paths(REPO_ROOT, B_SOURCE_SHA)
    current_only = current_paths - historical_paths
    entries = {entry.path: entry for entry in parsed.paths}

    assert current_only == {
        "docs/development/issue-workflow.md",
        "docs/development/pr-review.md",
        "tests/tools/test_agent_neutral_workflow.py",
    }
    for path in sorted(current_only):
        covered = covering_entry_paths(entries, path)
        assert len(covered) == 1
        assert entries[covered[0]].projection_action == "ENSURE_ABSENT"


def run_git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_cd_templates_schema_and_path_classes() -> None:
    for gen, name in (("C", "current-codex"), ("D", "current-claude")):
        path = MANIFESTS / f"generation-{gen.lower()}-current-template-manifest.json"
        raw = load_json(path)
        validate_basic(raw, TEMPLATE_SCHEMA, f"template-{gen}")
        assert raw["kind"] == "template"
        assert raw["generation_id"] == gen
        assert raw["agent_identity"]["generation_name"] == name
        assert raw["source_selector"] == {
            "kind": "fixed-commit",
            "ref": "BENCHMARK_BASE_SHA",
        }
        # No concrete SHA / blob / hash is allowed in a template manifest.
        dumped = json.dumps(raw)
        assert A_SOURCE_SHA not in dumped
        assert B_SOURCE_SHA not in dumped
        assert re.search(r"[0-9a-f]{40}", dumped) is None
        assert re.search(r"[0-9a-f]{64}", dumped) is None

    c = _manifest("generation-c-current-template-manifest.json")
    d = _manifest("generation-d-current-template-manifest.json")
    c_classes = [
        (item["glob"], item["role"])
        for item in c["closure_derivation_rules"]["path_classes"]
    ]
    d_classes = [
        (item["glob"], item["role"])
        for item in d["closure_derivation_rules"]["path_classes"]
    ]
    assert c_classes == d_classes
    # First-match-wins ordering: legacy-specific classes come first.
    assert c_classes[0] == (
        ".agents/skills/task-delivery/SKILL.md",
        "OPTIONAL_HISTORICAL_LIMITATION",
    )
    assert c_classes[1] == (
        ".agents/skills/task-pr-review/SKILL.md",
        "OPTIONAL_HISTORICAL_LIMITATION",
    )
    # Identity-required must be explicit; runtime_install=false is not a decision.
    rules = c["role_classification_rules"]
    assert rules["identity_required_must_be_explicit"] is True
    assert rules["runtime_install_false_is_not_a_decision"] is True
    assert rules["projection_defaults"]["OPTIONAL_HISTORICAL_LIMITATION"] == (
        "INHERIT_BUSINESS_BASE"
    )


def test_ab_manifest_git_integrity() -> None:
    """Verify every pinned blob/sha256/mode against git objects.

    Historical trees (a492f0b / e4a38d8) may be absent from a shallow CI
    checkout; the test skips when the objects are unavailable.
    """
    if not (_object_available(A_SOURCE_SHA) and _object_available(B_SOURCE_SHA)):
        pytest.skip("historical A/B source objects unavailable in this checkout")
    for name, source_sha in (
        ("generation-a-pinned-manifest.json", A_SOURCE_SHA),
        ("generation-b-pinned-manifest.json", B_SOURCE_SHA),
    ):
        parsed = parse_pinned_manifest(MANIFESTS / name)
        for entry in parsed.paths:
            if entry.projection_action != "INSTALL_GENERATION_VERSION":
                continue
            mode, blob_id = ls_tree_blob(REPO_ROOT, source_sha, entry.path)
            assert blob_id == entry.blob_id
            assert mode == entry.file_mode
            assert sha256_bytes(cat_blob(REPO_ROOT, blob_id)) == entry.sha256
