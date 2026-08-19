from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "tools/agent_workflow/mainline_experiment.py"
DOC_PATH = ROOT / "docs/workflows/mainline-before-after-revert-protocol.md"
EXAMPLE_PATH = ROOT / "docs/workflows/templates/mainline-experiment-record.example.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mainline_experiment", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPERIMENT = _load_module()


def _example() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")))


def _frozen_record() -> dict[str, Any]:
    record = _example()
    record["candidate_main_sha"] = "b" * 40
    for name in ("before", "after"):
        run = record["runs"][name]
        root_id = f"{name}-root"
        guardian_id = f"{name}-guardian"
        root_hash_character = "1" if name == "before" else "3"
        guardian_hash_character = "2" if name == "before" else "4"
        run["root_session_ids"] = [root_id]
        run["guardian_session_ids"] = [guardian_id]
        run["rollout_inventory"] = [
            {
                "actor": "root",
                "session_id": root_id,
                "parent_session_id": None,
                "rollout_filename": f"{root_id}.jsonl",
                "byte_size": 100,
                "sha256": root_hash_character * 64,
            },
            {
                "actor": "guardian",
                "session_id": guardian_id,
                "parent_session_id": root_id,
                "rollout_filename": f"{guardian_id}.jsonl",
                "byte_size": 20,
                "sha256": guardian_hash_character * 64,
            },
        ]
        run["measured_metrics"] = {field: 0 for field in EXPERIMENT.COUNT_METRIC_FIELDS}
        run["measured_metrics"]["command_grouping"] = []
        run["measured_metrics"]["manual_intervention"] = []
        expected_identity = run["experiment_identity"][
            "expected_head_or_patch_identity"
        ]
        run["workflow_evidence"] = {
            "git_pr_issue_identity": {
                "git_sha": run["experiment_identity"]["base_sha"],
                "issue": 90,
                "pr": 146,
            },
            "implementation_or_fixed_patch_identity": expected_identity,
            "validation": {"status": "pass", "evidence_identity": "validation-1"},
            "review_result": {
                "status": "excluded_by_boundary",
                "evidence_identity": "boundary-review-false",
            },
            "integrity": {"status": "pass", "evidence_identity": "integrity-1"},
            "cleanup_preconditions": ["exact branch and linkage identities"],
        }
    record["evidence_freeze"] = {"frozen_at": "2026-08-19T00:00:00Z"}
    record["decision"] = {
        "outcome": "keep",
        "criteria": {
            "correctness": "pass",
            "quality_gate": "pass",
            "hypothesis_supported": True,
            "operational_regression": "acceptable",
        },
    }
    record["revert_plan"] = {
        "candidate_commit_sha": "candidate-commit-sha",
        "mechanical_method": "approved git revert PR",
        "expected_restored_tree": "intended-current-baseline-tree",
        "cleanup_integrity_checks": [
            "target tree",
            "Issue/PR/branch cleanup",
            "no stale Development linkage",
            "no historical evidence mutation",
        ],
    }
    record["conductor"]["session_inventory"] = [
        {
            "role": "evidence_collector",
            "session_id": "collector-session",
            "parent_session_id": None,
            "rollout_filename": "collector.jsonl",
            "byte_size": 10,
            "sha256": "5" * 64,
        }
    ]
    return record


def test_example_is_valid_at_pre_run_checkpoint() -> None:
    assert EXPERIMENT.validate_record(_example(), checkpoint="pre_run") == []


def test_evidence_freeze_requires_rollout_hashes_metrics_and_revert_plan() -> None:
    violations = EXPERIMENT.validate_record(_example(), checkpoint="evidence_frozen")
    assert "candidate_main_sha must be frozen with evidence" in violations
    assert any(
        "rollout_inventory must be a non-empty list" in item for item in violations
    )
    assert "revert_plan must be an object" in violations

    assert (
        EXPERIMENT.validate_record(_frozen_record(), checkpoint="evidence_frozen") == []
    )


def test_boundary_and_conductor_exclusion_are_hard_requirements() -> None:
    record = _example()
    record["measured_boundary"]["evidence_collection_excluded"] = False
    record["conductor"]["excluded_from_measured_metrics"] = False
    violations = EXPERIMENT.validate_record(record, checkpoint="pre_run")
    assert "measured_boundary.evidence_collection_excluded must be true" in violations
    assert "conductor.excluded_from_measured_metrics must be true" in violations


def test_comparability_is_conditional_for_any_non_candidate_change() -> None:
    dimensions = _example()["comparability"]["dimensions"]
    assert EXPERIMENT.derive_comparability(dimensions) == "STRICT"
    dimensions["agent_invocation_granularity_change"] = True
    assert EXPERIMENT.derive_comparability(dimensions) == "CONDITIONAL"
    dimensions["workflow_change"] = False
    assert EXPERIMENT.derive_comparability(dimensions) == "NOT_COMPARABLE"


def test_declared_comparability_cannot_hide_guardian_admission_confound() -> None:
    record = _example()
    record["runs"]["after"]["experiment_identity"]["guardian_admission"] = (
        "DIFFERENT_ADMISSION"
    )
    violations = EXPERIMENT.validate_record(record, checkpoint="pre_run")
    assert (
        "comparability.classification must be CONDITIONAL for recorded dimensions"
        in violations
    )
    assert "comparability.conditional_reason is required for CONDITIONAL" in violations


def test_recorded_dimensions_must_match_before_after_identities() -> None:
    record = _example()
    record["runs"]["after"]["experiment_identity"]["model"] = "different-model"
    violations = EXPERIMENT.validate_record(record, checkpoint="pre_run")
    assert (
        "comparability.dimensions.model_change must be True for BEFORE/AFTER identities"
        in violations
    )


def test_strict_comparability_requires_valid_main_and_same_workload_identity() -> None:
    record = _frozen_record()
    record["candidate_main_sha"] = "not-a-git-object"
    record["runs"]["after"]["experiment_identity"]["task_or_fixed_patch_identity"] = (
        "different-workload"
    )
    violations = EXPERIMENT.validate_record(record, checkpoint="evidence_frozen")
    assert "candidate_main_sha must be a 40-character lowercase Git SHA" in violations
    assert (
        "runs.before and runs.after experiment_identity.task_or_fixed_patch_identity must match for comparability"
        in violations
    )
    assert (
        "comparability.classification must be NOT_COMPARABLE for recorded dimensions"
        in violations
    )


def test_frozen_identity_closes_main_arm_and_implementation_chain() -> None:
    record = _frozen_record()
    record["runs"]["before"]["experiment_identity"]["base_sha"] = "d" * 40
    record["runs"]["after"]["workflow_evidence"][
        "implementation_or_fixed_patch_identity"
    ] = "e" * 64
    record["runs"]["after"]["workflow_evidence"]["git_pr_issue_identity"]["git_sha"] = (
        "f" * 40
    )
    violations = EXPERIMENT.validate_record(record, checkpoint="evidence_frozen")
    assert (
        "runs.before.experiment_identity.base_sha must match baseline_main_sha"
        in violations
    )
    assert (
        "runs.after.workflow_evidence.implementation_or_fixed_patch_identity must match experiment_identity.expected_head_or_patch_identity"
        in violations
    )
    assert (
        "runs.after.workflow_evidence.git_pr_issue_identity.git_sha must match experiment_identity.base_sha"
        in violations
    )


def test_frozen_metrics_and_workflow_evidence_reject_placeholders() -> None:
    record = _frozen_record()
    before = record["runs"]["before"]
    before["measured_metrics"]["tokens"] = None
    before["measured_metrics"]["command_grouping"] = 0
    before["workflow_evidence"]["validation"] = {}
    violations = EXPERIMENT.validate_record(record, checkpoint="evidence_frozen")
    assert (
        "runs.before.measured_metrics.tokens must be a non-negative integer"
        in violations
    )
    assert "runs.before.measured_metrics.command_grouping must be a list" in violations
    assert any(
        "runs.before.workflow_evidence.validation.status" in item for item in violations
    )
    assert any(
        "runs.before.workflow_evidence.validation.evidence_identity" in item
        for item in violations
    )


def test_frozen_record_without_candidate_main_change_cannot_be_strict() -> None:
    record = _frozen_record()
    record["candidate_main_sha"] = record["baseline_main_sha"]
    violations = EXPERIMENT.validate_record(record, checkpoint="evidence_frozen")
    assert (
        "comparability.dimensions.workflow_change must be False for BEFORE/AFTER identities"
        in violations
    )
    assert (
        "comparability.classification must be NOT_COMPARABLE for recorded dimensions"
        in violations
    )


def test_adapter_cannot_mix_conductor_sessions_into_measured_run() -> None:
    identity = _example()["runs"]["before"]["experiment_identity"]
    run = EXPERIMENT.adapt_collected_run(
        experiment_identity=identity,
        rollout_inventory=[
            {
                "actor": "root",
                "session_id": "measured-root",
                "parent_session_id": None,
                "rollout_filename": "root.jsonl",
                "byte_size": 1,
                "sha256": "4" * 64,
            }
        ],
        measured_metrics={"tokens": 10},
    )
    assert run["root_session_ids"] == ["measured-root"]
    assert "conductor" not in run


def test_conductor_session_cannot_overlap_a_measured_session() -> None:
    record = _frozen_record()
    before_root = record["runs"]["before"]["rollout_inventory"][0]
    conductor = record["conductor"]["session_inventory"][0]
    for field in ("session_id", "rollout_filename", "sha256"):
        conductor[field] = before_root[field]
    violations = EXPERIMENT.validate_record(record, checkpoint="evidence_frozen")
    for field in ("session_id", "rollout_filename", "sha256"):
        assert (
            f"conductor.session_inventory[0].{field} must not overlap measured rollout identity"
            in violations
        )


def test_before_after_runs_cannot_reuse_measured_rollout_identity() -> None:
    record = _frozen_record()
    before_root = record["runs"]["before"]["rollout_inventory"][0]
    after_root = record["runs"]["after"]["rollout_inventory"][0]
    after_root.update(
        {
            "session_id": before_root["session_id"],
            "rollout_filename": before_root["rollout_filename"],
            "sha256": before_root["sha256"],
        }
    )
    record["runs"]["after"]["root_session_ids"] = [before_root["session_id"]]
    violations = EXPERIMENT.validate_record(record, checkpoint="evidence_frozen")
    for field in ("session_id", "rollout_filename", "sha256"):
        assert (
            f"runs.before and runs.after rollout {field} values must be disjoint"
            in violations
        )


def test_contamination_classification_is_explicit() -> None:
    record = _example()
    record["contamination_audit"]["accesses"] = [
        {"classification": "implementation", "artifact": "old answer"}
    ]
    violations = EXPERIMENT.validate_record(record, checkpoint="pre_run")
    assert any(
        "metadata-only from answer-bearing access" in item for item in violations
    )


def test_protocol_documents_scope_and_cleanup_boundaries() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    for phrase in (
        "baseline main",
        "evidence_collection_excluded: true",
        "COMPARABILITY",
        "metadata_only_exposure",
        "answer_bearing_implementation_access",
        "no stale Development linkage",
        "does not require a live Before/After/Revert run",
        "historical A/B/C/D benchmark architecture",
        "Issue #91",
    ):
        assert phrase in text
