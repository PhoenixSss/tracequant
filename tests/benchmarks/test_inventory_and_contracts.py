# ruff: noqa: E402, I001

from __future__ import annotations

import subprocess
from typing import Any, cast

import pytest

from _benchmark_helpers import REPO_ROOT

from benchmark_common import (  # type: ignore[import-not-found]
    BenchmarkError,
    load_json,
    validate_basic,
)

NAMESPACE = REPO_ROOT / "benchmarks" / "task-65-round-2-v2"


def _schema(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], load_json(NAMESPACE / "schemas" / name))


def test_contamination_inventory_schema_and_scope() -> None:
    inventory = load_json(
        NAMESPACE / "inventory" / "prior-benchmark-contamination-inventory.json"
    )
    validate_basic(
        inventory, _schema("contamination-inventory.schema.json"), "inventory"
    )
    assert inventory["classification"] == "PRIOR_BENCHMARK_CONTAMINATION_SECRET"
    entries = inventory["entries"]
    assert isinstance(entries, list) and len(entries) == 12
    for entry in entries:
        # Every entry covers all four arms (Class 2 is forbidden for all).
        assert set(entry["scope"]) == {"A", "B", "C", "D"}
        assert entry["artifact_id"] and entry["type"] and entry["reason_forbidden"]
        assert entry["locations"]
    # The v1/v1.1 answer-bearing artifacts must be present.
    identifiers = str(inventory)
    for expected in (
        "benchmark-manifest.json",
        "experiment/task65-candidate-wsl2",
        "experiment/task65-current-windows",
    ):
        assert expected in identifiers
    # Remediation B1/H1 (Issue #125): only SPECIFIC answer-bearing artifact
    # identities may be forbidden.  The A/B workflow source SHA is a
    # provenance selector for normal Class 1 workflow materialization and
    # must NOT be a location identifier (its literal occurrence must never
    # auto-flag leakage); generic category/root labels (the v1 protocol
    # bundle directory, "actions/runs", "experiment-record") are likewise
    # not forbidden identifiers.
    location_identifiers = {
        location["identifier"]
        for entry in entries
        for location in entry["locations"]
        if location.get("identifier")
    }
    assert "a492f0b334f950f2613b4b2204e96bef413355be" not in location_identifiers
    assert not any("actions/runs" == ident for ident in location_identifiers)
    assert not any("experiment-record" == ident for ident in location_identifiers)
    assert not any(
        ident.rstrip("/").endswith("docs/workflows/benchmarks/task-65-round-2")
        for ident in location_identifiers
    )


def test_arm_identity_registry_schema() -> None:
    registry = load_json(NAMESPACE / "registry" / "arm-identity-registry.json")
    arms = registry["arms"]
    assert len(arms) == 4
    for arm in arms:
        validate_basic(arm, _schema("arm-identity.schema.json"), f"arm-{arm['arm_id']}")
        assert arm["pr_state"] == "OPEN"
        assert arm["pr_draft"] is False
        assert not arm["merge_allowed"]
        assert not arm["auto_merge_allowed"]
        assert not arm["closeout_allowed"]
        assert arm["issue_link"] == "Closes #65"
        assert arm["base_sha"] == "BENCHMARK_BASE_SHA"

    # Deterministic naming per the Arm Identity / PR Contract.
    by_arm = {arm["arm_id"]: arm for arm in arms}
    assert by_arm["A"]["agent"] == "codex"
    assert (
        by_arm["A"]["branch_name_template"]
        == "experiment/task65-v2-a-legacy-no-runner-codex"
    )
    assert by_arm["A"]["control_base_branch"] == "experiment/task65-v2-a-control-base"
    assert (
        by_arm["B"]["branch_name_template"]
        == "experiment/task65-v2-b-legacy-runner-codex"
    )
    assert by_arm["C"]["branch_name_template"] == "experiment/task65-v2-c-current-codex"
    assert by_arm["D"]["agent"] == "claude"
    assert (
        by_arm["D"]["branch_name_template"] == "experiment/task65-v2-d-current-claude"
    )
    assert by_arm["D"]["control_base_branch"] == "experiment/task65-v2-d-control-base"

    # Generation ids are distinct per arm and match the generation model.
    gen_ids = {arm["generation_id"] for arm in arms}
    assert gen_ids == {
        "legacy-no-unified-runner-codex",
        "legacy-unified-runner-codex",
        "current-codex",
        "current-claude",
    }


def test_schemas_are_valid_json_documents() -> None:
    for name in (
        "generation-manifest.schema.json",
        "arm-identity.schema.json",
        "contamination-inventory.schema.json",
        "access-event.schema.json",
        "context-input.schema.json",
    ):
        raw = load_json(NAMESPACE / "schemas" / name)
        assert raw["protocol_identity"] == "task-65-round-2-v2"
        assert raw["$schema"].startswith("https://json-schema.org/")


def test_context_input_schema_validates_normalized_inputs() -> None:
    schema = _schema("context-input.schema.json")
    validate_basic(
        {
            "session_id": "sess-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "timestamp": "2026-08-10T14:02:52.353Z",
            "source_type": "attachment:file",
            "target": "synthetic attached content",
            "raw_event_reference": "toolu_01",
        },
        schema,
        "context-input",
    )
    for source_type in (
        "attachment:agent_listing_delta",
        "attachment:skill_listing",
        "attachment:command_permissions",
        "attachment:todo_reminder",
        "summary",
        "user-prompt",
        "last-prompt",
        "ai-title",
        "system:compact_boundary",
        "system:api_error",
    ):
        validate_basic(
            {
                "session_id": "s",
                "timestamp": "t",
                "source_type": source_type,
                "target": "x",
                "raw_event_reference": "r",
            },
            schema,
            f"context-input/{source_type}",
        )


def test_context_input_schema_rejects_unknown_source_type() -> None:
    # Unknown source types must fail closed: the schema never widens to
    # accept arbitrary source_type values.
    schema = _schema("context-input.schema.json")
    for source_type in (
        "attachment:something_else",
        "attachment:command_permissions_extra",
        "system:unknown",
        "",
    ):
        with pytest.raises(BenchmarkError):
            validate_basic(
                {
                    "session_id": "s",
                    "timestamp": "t",
                    "source_type": source_type,
                    "target": "x",
                    "raw_event_reference": "r",
                },
                schema,
                "context-input/unknown-source-type",
            )


def test_contracts_present_and_covers_required_topics() -> None:
    contracts = NAMESPACE / "contracts"
    required = {
        "arm-identity-and-pr-contract.md": [
            "ENSURE-LINKED",
            "control-base",
            "DO NOT MERGE",
        ],
        "temporary-development-link-contract.md": [
            "ENSURE-LINKED-BUSINESS-BRANCH",
            "gh issue develop",
            "closingIssuesReferences",
            "OTHER_ARM_CURRENT_RUN_SECRET",
        ],
        "runtime-projection-and-control-base-contract.md": [
            "INSTALL_GENERATION_VERSION",
            "INHERIT_BUSINESS_BASE",
            "ENSURE_ABSENT",
            "HUMAN GATE",
        ],
        "benchmark-information-boundary.md": [
            "PRIOR_BENCHMARK_CONTAMINATION_SECRET",
            "STATIC_REPOSITORY_CONTEXT",
            "CURRENT_RUN_CROSS_ARM_SECRET",
        ],
        "observability-and-access-audit-contract.md": [
            "OBSERVABILITY VERIFIED",
            "BENCHMARK INVALID",
            "zero forbidden matches",
        ],
        "non-formal-labeling-convention.md": [
            "NON-FORMAL",
            "PREP_VALIDATION_SOURCE_SHA",
        ],
    }
    for filename, markers in required.items():
        text = (contracts / filename).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in text, f"{filename} missing marker {marker!r}"


def test_namespace_readme_present() -> None:
    readme = (NAMESPACE / "README.md").read_text(encoding="utf-8")
    assert "task-65-round-2-v2" in readme
    assert "Deliverables map" in readme


def test_fixture_store_is_gitignored() -> None:
    probe = ".agents/benchmark-fixtures.local/generation-a/bundle.json"
    result = subprocess.run(
        ["git", "check-ignore", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "fixture store must be gitignored"
    assert probe in result.stdout
