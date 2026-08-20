from __future__ import annotations

import sys
from collections.abc import Callable
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


def _workflow_identity() -> dict[str, Any]:
    return {
        "profile": "delivery-readiness",
        "schema_version": 1,
        "runner": {
            "path": "tools/agent_workflow/workflow_evidence.py",
            "source_sha": SHA40,
            "content_sha256": SHA256,
            "handoff_schema": {
                "path": "tools/agent_workflow/review_fact_handoff.py",
                "content_sha256": SHA256,
            },
        },
        "skill": {
            "path": ".agents/skills/task-pr-review-runner/SKILL.md",
            "sha256": SHA256,
        },
    }


def _validation_facts() -> dict[str, Any]:
    return {
        "profile": "workflow-delivery",
        "schema_version": 1,
        "runner_identity": {
            "path": "tools/agent_workflow/wsl2_validation_runner.py",
            "sha256": SHA256,
            "profile_spec_path": "tools/agent_workflow/wsl2_validation_profiles.json",
            "profile_spec_sha256": SHA256,
            "rules_path": ".codex/rules/tracequant-wsl-validation.rules",
            "rules_sha256": SHA256,
            "workflow_validation_path": "tools/agent_workflow/workflow_validation.py",
            "workflow_validation_sha256": SHA256,
            "skill": {
                "path": ".agents/skills/task-delivery-runner/SKILL.md",
                "sha256": SHA256,
            },
        },
        "exit_code": 0,
        "result_locator": ".agents/validation.local/wsl2-runs/run/result.json",
        "result_sha256": SHA256,
    }


def _snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "snapshot_id": "ev-1234567890abcdef",
        "repository": "owner/repo",
        "operation": "delivery-readiness",
        "execution_context": {"workflow_identity": _workflow_identity()},
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
                "checks": {
                    "items": [
                        {
                            "name": "quality",
                            "state": "SUCCESS",
                            "category": "success",
                            "run_id": "run-1",
                            "started_at": "2026-08-20T00:00:00Z",
                            "completed_at": "2026-08-20T00:01:00Z",
                            "source_url": "https://example.invalid/run-1",
                        }
                    ]
                },
            },
            "effective_diff": {
                "sha256": DIFF_SHA256,
                "changed_files": {"items": ["src/app.py"], "truncated": False},
            },
            "required_checks": {
                "configuration": "available",
                "contexts": {"items": ["quality"], "count": 1, "truncated": False},
            },
        },
    }


def _handoff() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        build_handoff_from_snapshot(
            _snapshot(),
            acceptance_criteria_ids=["AC-1", "AC-2"],
            validation_facts=_validation_facts(),
            workflow_identity=_workflow_identity(),
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
        current_validation_facts=_validation_facts(),
    )
    assert result["status"] == "fail"
    assert result["trusted"] is False
    assert any(item.startswith("HEAD_DRIFT") for item in result["invalidated"])


def test_current_validation_facts_match_handoff() -> None:
    result = validate_against_snapshot(
        _handoff(),
        _snapshot(),
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
        current_validation_facts=_validation_facts(),
    )
    assert result["status"] == "pass"
    assert result["trusted"] is True


def test_stale_or_mismatched_validation_facts_fail_closed() -> None:
    current = _validation_facts()
    current["result_sha256"] = "e" * 64
    result = validate_against_snapshot(
        _handoff(),
        _snapshot(),
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
        current_validation_facts=current,
    )
    assert result["status"] == "fail"
    assert any(item.startswith("VALIDATION_DRIFT") for item in result["invalidated"])


def test_unverifiable_validation_facts_fail_closed() -> None:
    result = validate_against_snapshot(
        _handoff(),
        _snapshot(),
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
        current_validation_facts=None,
    )
    assert result["status"] == "fail"
    assert any(item.startswith("VALIDATION_DRIFT") for item in result["invalidated"])


def test_workflow_runner_skill_and_schema_drift_fail_closed() -> None:
    for drift in ("runner", "skill", "schema"):
        current = _workflow_identity()
        if drift == "runner":
            current["runner"]["content_sha256"] = "e" * 64
        elif drift == "skill":
            current["skill"]["sha256"] = "e" * 64
        else:
            current["schema_version"] = 2
        result = validate_against_snapshot(
            _handoff(),
            _snapshot(),
            current_acceptance_criteria_ids=["AC-1", "AC-2"],
            current_validation_facts=_validation_facts(),
            current_workflow_identity=current,
        )
        assert result["status"] == "fail", drift
        assert any(
            item.startswith("WORKFLOW_RULE_DRIFT") for item in result["invalidated"]
        ), drift


def test_unverifiable_workflow_identity_fails_closed() -> None:
    result = validate_against_snapshot(
        _handoff(),
        _snapshot(),
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
        current_validation_facts=_validation_facts(),
        current_workflow_identity={},
    )
    assert result["status"] == "fail"
    assert any(item.startswith("WORKFLOW_RULE_DRIFT") for item in result["invalidated"])


def test_source_digest_mismatch_fails_closed() -> None:
    current = {
        "repository": "owner/repo",
        "source_locator": _handoff()["source_identity"]["source_locator"],
        "source_digest": "e" * 64,
    }
    result = validate_against_snapshot(
        _handoff(),
        _snapshot(),
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
        current_validation_facts=_validation_facts(),
        current_source_identity=current,
    )
    assert result["status"] == "fail"
    assert any(
        item.startswith("HANDOFF_SCHEMA_DRIFT") for item in result["invalidated"]
    )


def test_unverifiable_source_identity_fails_closed() -> None:
    result = validate_against_snapshot(
        _handoff(),
        _snapshot(),
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
        current_validation_facts=_validation_facts(),
        current_source_identity={},
    )
    assert result["status"] == "fail"
    assert any(
        item.startswith("HANDOFF_SCHEMA_DRIFT") for item in result["invalidated"]
    )


def test_nested_allowlists_reject_unknown_fields() -> None:
    cases: tuple[tuple[Callable[[dict[str, Any]], None], str], ...] = (
        (lambda value: value.update({"unknown": True}), "top-level"),
        (
            lambda value: value["workflow_identity"]["runner"].update(
                {"unknown": True}
            ),
            "workflow",
        ),
        (lambda value: value["source_identity"].update({"unknown": True}), "source"),
        (
            lambda value: value["validation_facts"]["runner_identity"].update(
                {"unknown": True}
            ),
            "validation",
        ),
        (
            lambda value: value["workflow_identity"]["skill"].update(
                {"semantic_claim": "all acceptance criteria pass"}
            ),
            "semantic-looking",
        ),
    )
    for mutate, label in cases:
        handoff = _handoff()
        mutate(handoff)
        violations = validate_handoff_structure(
            handoff, expected_repository="owner/repo"
        )
        assert violations, label


def test_defined_nested_fields_remain_compatible() -> None:
    handoff = _handoff()
    handoff["raw_check_facts"]["required"]["failure"] = {
        "category": "unknown",
        "reason": "required-checks-query-failed",
        "http_status": None,
        "command_id": "gh-required-checks-main",
    }
    handoff["raw_check_facts"]["observed"][0]["source_url"] = None
    assert validate_handoff_structure(handoff, expected_repository="owner/repo") == []


def test_default_freshness_contract_covers_all_required_drift() -> None:
    contract = default_freshness_contract()
    assert set(contract["invalidate_on"]) == set(DRIFT_TYPES)
    assert contract["requires_new_semantic_context_on_object_drift"] is True
