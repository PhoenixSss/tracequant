from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).parents[2] / "tools/agent_workflow"))

from review_fact_handoff import (  # type: ignore[import-not-found]  # noqa: E402, I001
    DRIFT_TYPES,
    build_handoff_from_snapshot,
    default_freshness_contract,
    validate_against_snapshot,
    validate_handoff_structure,
)


SHA40 = "a" * 40
HEAD40 = "b" * 40
SHA256 = "c" * 64
DIFF_SHA256 = "d" * 64


def _snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "snapshot_id": "ev-1234567890abcdef",
        "repository": "owner/repo",
        "operation": "delivery-readiness",
        "execution_context": {
            "workflow_identity": {
                "profile": "delivery-readiness",
                "schema_version": 1,
                "runner": {"path": "runner", "content_sha256": SHA256},
                "skill": {"path": "skill", "sha256": SHA256},
            }
        },
        "observed": {
            "issue": {
                "number": 70,
                "spec_sha256": SHA256,
                "acceptance_criteria_ids": ["AC-1", "AC-2"],
            },
            "pr": {
                "number": 71,
                "base_sha": SHA40,
                "head_sha": HEAD40,
                "checks": {"items": [{"name": "quality", "state": "SUCCESS"}]},
            },
            "effective_diff": {
                "sha256": DIFF_SHA256,
                "changed_files": {"items": ["src/app.py"], "truncated": False},
            },
            "required_checks": {"configuration": "available", "contexts": ["quality"]},
        },
    }


def _handoff() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        build_handoff_from_snapshot(
            _snapshot(),
            acceptance_criteria_ids=["AC-1", "AC-2"],
            validation_facts={
                "profile": "workflow-delivery",
                "schema_version": 1,
                "runner_identity": {"path": "validation", "sha256": SHA256},
                "exit_code": 0,
                "result_locator": ".agents/validation.local/run.json",
                "result_sha256": SHA256,
            },
            workflow_identity={
                "profile": "delivery-readiness",
                "schema_version": 1,
                "runner": {"path": "runner", "content_sha256": SHA256},
                "skill": {"path": "skill", "sha256": SHA256},
            },
            source_identity={
                "repository": "owner/repo",
                "source_locator": ".agents/evidence.local/snapshot.json",
                "source_digest": SHA256,
            },
            created_at="2026-08-20T00:00:00+00:00",
        ),
    )


def test_valid_handoff_has_exact_bounded_contract() -> None:
    handoff = _handoff()
    assert validate_handoff_structure(handoff, expected_repository="owner/repo") == []
    assert handoff["freshness_contract"]["invalidate_on"] == list(DRIFT_TYPES)


def test_prohibited_semantic_field_fails_closed_even_when_nested() -> None:
    handoff = _handoff()
    handoff["raw_check_facts"]["review_conclusion"] = "pass"
    violations = validate_handoff_structure(handoff, expected_repository="owner/repo")
    assert any("prohibited semantic fields" in item for item in violations)


def test_current_object_drift_invalidates_handoff() -> None:
    handoff = _handoff()
    snapshot = _snapshot()
    snapshot["observed"]["pr"]["head_sha"] = "e" * 40
    result = validate_against_snapshot(
        handoff,
        snapshot,
        handoff_digest=SHA256,
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
    )
    assert result["status"] == "fail"
    assert result["trusted"] is False
    assert any(item.startswith("HEAD_DRIFT") for item in result["invalidated"])


def test_default_freshness_contract_covers_all_required_drift() -> None:
    contract = default_freshness_contract()
    assert set(contract["invalidate_on"]) == set(DRIFT_TYPES)
    assert contract["requires_new_semantic_context_on_object_drift"] is True
