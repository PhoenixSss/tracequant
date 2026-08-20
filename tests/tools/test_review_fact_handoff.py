from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).parents[2] / "tools/agent_workflow"))

from review_fact_handoff import (  # type: ignore[import-not-found]  # noqa: E402, I001
    DRIFT_TYPES,
    acquire_current_validation_facts,
    build_handoff_from_snapshot,
    default_freshness_contract,
    source_identity_for_snapshot,
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
        "control_plane": {
            "evidence_runner": {
                "path": "tools/agent_workflow/wsl2_github_evidence_runner.py",
                "content_sha256": SHA256,
            },
            "profile_spec": {
                "path": "tools/agent_workflow/wsl2_github_evidence_profiles.json",
                "content_sha256": SHA256,
            },
            "evidence_rules": {
                "path": ".codex/rules/tracequant-wsl-evidence.rules",
                "content_sha256": SHA256,
            },
            "workflow_common": {
                "path": "tools/agent_workflow/workflow_common.py",
                "content_sha256": SHA256,
            },
            "command_execution_policy": {
                "path": ".agents/policies/command-execution.md",
                "content_sha256": SHA256,
            },
            "workflow_evidence_policy": {
                "path": ".agents/policies/workflow-evidence.md",
                "content_sha256": SHA256,
            },
            "review_semantics": {
                "path": "docs/development/pr-review.md",
                "content_sha256": SHA256,
            },
        },
    }


def _validation_facts() -> dict[str, Any]:
    return {
        "base_sha": SHA40,
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
                    "items": {
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
                        ],
                        "count": 1,
                        "truncated": False,
                    }
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


def _handoff(validation_facts: dict[str, Any] | None = None) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        build_handoff_from_snapshot(
            _snapshot(),
            acceptance_criteria_ids=["AC-1", "AC-2"],
            validation_facts=validation_facts or _validation_facts(),
            workflow_identity=_workflow_identity(),
            created_at="2026-08-20T00:00:00+00:00",
        ),
    )


def _write_validation_result(
    repo: Path,
    *,
    base_sha: Any = SHA40,
    head_sha: Any = HEAD40,
    clean: bool = True,
    include_base: bool = True,
) -> dict[str, Any]:
    identity_paths = {
        "path": "tools/agent_workflow/wsl2_validation_runner.py",
        "profile_spec_path": "tools/agent_workflow/wsl2_validation_profiles.json",
        "rules_path": ".codex/rules/tracequant-wsl-validation.rules",
        "workflow_validation_path": "tools/agent_workflow/workflow_validation.py",
        "skill_path": ".agents/skills/task-delivery-runner/SKILL.md",
    }
    digests: dict[str, str] = {}
    for key, relative in identity_paths.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"validation identity")
        digests[key] = hashlib.sha256(target.read_bytes()).hexdigest()
    identity = {
        "path": identity_paths["path"],
        "sha256": digests["path"],
        "profile_spec_path": identity_paths["profile_spec_path"],
        "profile_spec_sha256": digests["profile_spec_path"],
        "rules_path": identity_paths["rules_path"],
        "rules_sha256": digests["rules_path"],
        "workflow_validation_path": identity_paths["workflow_validation_path"],
        "workflow_validation_sha256": digests["workflow_validation_path"],
        "skill": {
            "path": identity_paths["skill_path"],
            "sha256": digests["skill_path"],
        },
    }
    locator = ".agents/validation.local/wsl2-runs/run/result.json"
    document: dict[str, Any] = {
        "schema_version": 1,
        "profile": "workflow-delivery",
        "status": "pass",
        "repository": {"state": {"head_sha": head_sha, "clean": clean}},
        "artifacts": {"result_json": locator},
        "integrity": {
            "verification": "current-worktree-content",
            "repository_head_sha": head_sha,
            "repository_clean": clean,
            "runner_path": identity["path"],
            "runner_sha256": identity["sha256"],
            "profile_spec_path": identity["profile_spec_path"],
            "profile_spec_sha256": identity["profile_spec_sha256"],
            "rules_path": identity["rules_path"],
            "rules_sha256": identity["rules_sha256"],
            "workflow_validation_path": identity["workflow_validation_path"],
            "workflow_validation_sha256": identity["workflow_validation_sha256"],
            "skill": identity["skill"],
        },
    }
    if include_base:
        document["base_sha"] = base_sha
    payload = (json.dumps(document, sort_keys=True) + "\n").encode()
    result_path = repo / locator
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(payload)
    return {
        "base_sha": base_sha,
        "profile": document["profile"],
        "schema_version": document["schema_version"],
        "runner_identity": identity,
        "exit_code": 0,
        "result_locator": locator,
        "result_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _acquire_validation(
    tmp_path: Path,
    *,
    base_sha: Any = SHA40,
    head_sha: Any = HEAD40,
    clean: bool = True,
    include_base: bool = True,
) -> tuple[dict[str, Any] | None, list[str]]:
    facts = _write_validation_result(
        tmp_path,
        base_sha=base_sha,
        head_sha=head_sha,
        clean=clean,
        include_base=include_base,
    )
    return cast(
        tuple[dict[str, Any] | None, list[str]],
        acquire_current_validation_facts(
            tmp_path,
            facts,
            expected_base_sha=SHA40,
            expected_head_sha=HEAD40,
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


def test_review_phase_operation_does_not_change_stable_source_identity() -> None:
    initial = _snapshot()
    recheck = _snapshot()
    recheck["operation"] = "pr-review-recheck"
    recheck["snapshot_id"] = "ev-fedcba9876543210"

    assert source_identity_for_snapshot(initial) == source_identity_for_snapshot(
        recheck
    )
    result = validate_against_snapshot(
        _handoff(),
        recheck,
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
        current_validation_facts=_validation_facts(),
    )
    assert result["status"] == "pass"
    assert result["trusted"] is True
    assert not any(
        item.startswith("HANDOFF_SCHEMA_DRIFT") for item in result["invalidated"]
    )


def test_validation_base_and_head_match_reviewed_object(tmp_path: Path) -> None:
    current, errors = _acquire_validation(tmp_path)
    assert errors == []
    assert current is not None
    result = validate_against_snapshot(
        _handoff(current),
        _snapshot(),
        current_acceptance_criteria_ids=["AC-1", "AC-2"],
        current_validation_facts=current,
    )
    assert result["status"] == "pass"
    assert result["trusted"] is True


def test_validation_base_mismatch_fails_even_when_head_matches(tmp_path: Path) -> None:
    current, errors = _acquire_validation(tmp_path, base_sha=HEAD40)
    assert current is None
    assert any("base identity mismatch" in error for error in errors)


def test_validation_base_missing_fails_closed(tmp_path: Path) -> None:
    current, errors = _acquire_validation(tmp_path, include_base=False)
    assert current is None
    assert any("base identity is unavailable" in error for error in errors)


def test_validation_base_unknown_fails_closed(tmp_path: Path) -> None:
    current, errors = _acquire_validation(tmp_path, base_sha="UNKNOWN")
    assert current is None
    assert any("base identity is unavailable" in error for error in errors)


def test_validation_base_and_head_match_but_clean_fails_closed(
    tmp_path: Path,
) -> None:
    current, errors = _acquire_validation(tmp_path, clean=False)
    assert current is None
    assert any("repository was not clean" in error for error in errors)


def test_validation_head_mismatch_remains_fail_closed(tmp_path: Path) -> None:
    current, errors = _acquire_validation(tmp_path, head_sha="e" * 40)
    assert current is None
    assert any("head identity mismatch" in error for error in errors)


def test_all_required_drift_classes_fail_closed() -> None:
    cases: list[tuple[str, Callable[[dict[str, Any], dict[str, Any]], None]]] = [
        (
            "TASK_SPEC_DRIFT",
            lambda snapshot, current: snapshot["observed"]["issue"].update(
                {"spec_sha256": "e" * 64}
            ),
        ),
        (
            "BASE_DRIFT",
            lambda snapshot, current: snapshot["observed"]["pr"].update(
                {"base_sha": "e" * 40}
            ),
        ),
        (
            "HEAD_DRIFT",
            lambda snapshot, current: snapshot["observed"]["pr"].update(
                {"head_sha": "e" * 40}
            ),
        ),
        (
            "EFFECTIVE_DIFF_DRIFT",
            lambda snapshot, current: snapshot["observed"]["effective_diff"].update(
                {"sha256": "e" * 64}
            ),
        ),
        (
            "CHECKS_DRIFT",
            lambda snapshot, current: snapshot["observed"]["pr"]["checks"]["items"][
                "items"
            ][0].update({"state": "FAILURE"}),
        ),
        (
            "VALIDATION_DRIFT",
            lambda snapshot, current: current["validation"].update(
                {"result_sha256": "e" * 64}
            ),
        ),
        (
            "WORKFLOW_RULE_DRIFT",
            lambda snapshot, current: current["workflow"]["control_plane"][
                "evidence_runner"
            ].update({"content_sha256": "e" * 64}),
        ),
        (
            "HANDOFF_SCHEMA_DRIFT",
            lambda snapshot, current: current["source"].update(
                {"source_digest": "e" * 64}
            ),
        ),
    ]

    for drift, mutate in cases:
        snapshot = _snapshot()
        current = {
            "validation": _validation_facts(),
            "workflow": _workflow_identity(),
            "source": source_identity_for_snapshot(snapshot),
        }
        mutate(snapshot, current)
        if drift not in {
            "HANDOFF_SCHEMA_DRIFT",
            "WORKFLOW_RULE_DRIFT",
            "VALIDATION_DRIFT",
        }:
            current["source"] = source_identity_for_snapshot(snapshot)
        result = validate_against_snapshot(
            _handoff(),
            snapshot,
            current_acceptance_criteria_ids=["AC-1", "AC-2"],
            current_validation_facts=current["validation"],
            current_workflow_identity=current["workflow"],
            current_source_identity=current["source"],
        )
        assert result["status"] == "fail", drift
        assert any(item.startswith(drift) for item in result["invalidated"]), (
            drift,
            result["invalidated"],
        )


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


def test_workflow_runner_skill_schema_and_control_plane_drift_fail_closed() -> None:
    for drift in (
        "runner",
        "skill",
        "schema",
        "evidence_runner",
        "profile_spec",
        "evidence_rules",
        "workflow_common",
        "command_execution_policy",
        "workflow_evidence_policy",
        "review_semantics",
    ):
        current = _workflow_identity()
        if drift == "runner":
            current["runner"]["content_sha256"] = "e" * 64
        elif drift == "skill":
            current["skill"]["sha256"] = "e" * 64
        elif drift == "schema":
            current["schema_version"] = 2
        else:
            current["control_plane"][drift]["content_sha256"] = "e" * 64
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
        (
            lambda value: value["workflow_identity"]["control_plane"][
                "evidence_runner"
            ].update({"unknown": True}),
            "workflow-control-plane",
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


def test_current_required_check_facts_fail_closed_when_incomplete() -> None:
    cases = []

    truncated_contexts = json.loads(json.dumps(_snapshot()))
    truncated_contexts["observed"]["required_checks"]["contexts"]["truncated"] = True
    cases.append(truncated_contexts)

    unknown_configuration = json.loads(json.dumps(_snapshot()))
    unknown_configuration["observed"]["required_checks"]["configuration"] = "unknown"
    cases.append(unknown_configuration)

    truncated_checks = json.loads(json.dumps(_snapshot()))
    truncated_checks["observed"]["pr"]["checks"]["items"]["truncated"] = True
    cases.append(truncated_checks)

    for snapshot in cases:
        result = validate_against_snapshot(
            _handoff(),
            snapshot,
            current_acceptance_criteria_ids=["AC-1", "AC-2"],
            current_validation_facts=_validation_facts(),
            current_workflow_identity=_workflow_identity(),
        )
        assert result["status"] == "fail"
        assert result["trusted"] is False
        assert result["strategy"] == "FAIL_CLOSED"
        assert any(item.startswith("CHECKS_DRIFT") for item in result["invalidated"])


def test_default_freshness_contract_covers_all_required_drift() -> None:
    contract = default_freshness_contract()
    assert contract["invalidate_on"] == list(DRIFT_TYPES)
    assert contract["requires_new_semantic_context_on_object_drift"] is True


def test_freshness_contract_is_closed_world() -> None:
    mutators: tuple[Callable[[dict[str, Any]], Any], ...] = (
        lambda value: value["freshness_contract"]["invalidate_on"].append(
            "UNKNOWN_DRIFT"
        ),
        lambda value: value["freshness_contract"]["revalidate_current_facts"].pop(),
    )
    for mutate in mutators:
        handoff = _handoff()
        mutate(handoff)
        violations = validate_handoff_structure(
            handoff, expected_repository="owner/repo"
        )
        assert violations
