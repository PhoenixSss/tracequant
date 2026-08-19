#!/usr/bin/env python3
"""Schema adapter and validator for mainline workflow experiments.

The module is deliberately side-effect free.  It does not inspect rollouts, run
commands, change Git state, or collect evidence.  A conductor may use the
adapter after a measured session has ended to construct the canonical record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

SCHEMA_VERSION: Final = 1
CHECKPOINTS: Final = frozenset({"pre_run", "evidence_frozen"})
COMPARABILITY_VALUES: Final = frozenset({"STRICT", "CONDITIONAL", "NOT_COMPARABLE"})
ROLLOUT_ACTORS: Final = frozenset({"root", "guardian"})
CONTAMINATION_CLASSES: Final = frozenset(
    {"metadata_only_exposure", "answer_bearing_implementation_access"}
)

EXPERIMENT_IDENTITY_FIELDS: Final = (
    "task_or_fixed_patch_identity",
    "task_spec_hash",
    "base_sha",
    "expected_head_or_patch_identity",
    "model",
    "reasoning_effort",
    "guardian_model",
    "guardian_effort",
    "cli_version",
    "approval_policy",
    "sandbox_policy",
    "network_mode",
    "agent_invocation_granularity",
    "guardian_admission",
)

COMPARABILITY_DIMENSIONS: Final = (
    "workflow_change",
    "model_change",
    "cli_runtime_change",
    "sandbox_change",
    "approval_policy_change",
    "network_change",
    "agent_invocation_granularity_change",
    "guardian_admission_change",
)

MEASURED_METRIC_FIELDS: Final = (
    "tokens",
    "duration_ms",
    "root_tool_calls",
    "guardian_turns",
    "guardian_tokens",
    "validation_command_segments",
    "shell_tool_invocation_count",
    "repeated_git_github_acquisition",
    "compound_invocation_count",
    "command_grouping",
    "manual_intervention",
)

_COMPARABILITY_IDENTITY_FIELDS: Final = {
    "model_change": ("model", "reasoning_effort", "guardian_model", "guardian_effort"),
    "cli_runtime_change": ("cli_version",),
    "sandbox_change": ("sandbox_policy",),
    "approval_policy_change": ("approval_policy",),
    "network_change": ("network_mode",),
    "agent_invocation_granularity_change": ("agent_invocation_granularity",),
    "guardian_admission_change": ("guardian_admission",),
}


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _git_or_patch_oid(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and all(character in "0123456789abcdef" for character in value)
    )


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return None


def derive_comparability(dimensions: Mapping[str, object]) -> str:
    """Derive the strongest supported causal-comparison classification."""
    if any(field not in dimensions for field in COMPARABILITY_DIMENSIONS):
        return "NOT_COMPARABLE"
    if not all(
        isinstance(dimensions[field], bool) for field in COMPARABILITY_DIMENSIONS
    ):
        return "NOT_COMPARABLE"
    if dimensions["workflow_change"] is not True:
        return "NOT_COMPARABLE"
    confounds = COMPARABILITY_DIMENSIONS[1:]
    if any(dimensions[field] is True for field in confounds):
        return "CONDITIONAL"
    return "STRICT"


def derive_change_dimensions(
    before_identity: Mapping[str, object],
    after_identity: Mapping[str, object],
    *,
    baseline_main_sha: object,
    candidate_main_sha: object,
) -> dict[str, bool]:
    """Mechanically derive candidate and non-candidate change dimensions."""
    dimensions = {
        "workflow_change": (
            _git_or_patch_oid(baseline_main_sha)
            and _git_or_patch_oid(candidate_main_sha)
            and baseline_main_sha != candidate_main_sha
        )
    }
    for dimension, fields in _COMPARABILITY_IDENTITY_FIELDS.items():
        dimensions[dimension] = any(
            before_identity.get(field) != after_identity.get(field) for field in fields
        )
    return dimensions


def adapt_collected_run(
    *,
    experiment_identity: Mapping[str, object],
    rollout_inventory: Sequence[Mapping[str, object]],
    measured_metrics: Mapping[str, object],
) -> dict[str, object]:
    """Adapt already-collected external evidence to one canonical run.

    Conductor sessions are intentionally not accepted here.  They belong in
    the record-level ``conductor`` inventory and therefore cannot enter the
    measured run metrics through this adapter.
    """
    root_session_ids = [
        item.get("session_id")
        for item in rollout_inventory
        if item.get("actor") == "root"
    ]
    guardian_session_ids = [
        item.get("session_id")
        for item in rollout_inventory
        if item.get("actor") == "guardian"
    ]
    return {
        "experiment_identity": dict(experiment_identity),
        "root_session_ids": root_session_ids,
        "guardian_session_ids": guardian_session_ids,
        "rollout_inventory": [dict(item) for item in rollout_inventory],
        "measured_metrics": dict(measured_metrics),
    }


def _validate_identity(identity: object, prefix: str, violations: list[str]) -> None:
    value = _mapping(identity)
    if value is None:
        violations.append(f"{prefix} must be an object")
        return
    for field in EXPERIMENT_IDENTITY_FIELDS:
        if not _non_empty_string(value.get(field)):
            violations.append(f"{prefix}.{field} must be a non-empty string")
    if not _sha256(value.get("task_spec_hash")):
        violations.append(f"{prefix}.task_spec_hash must be a lowercase SHA-256")
    for field in ("base_sha", "expected_head_or_patch_identity"):
        if not _git_or_patch_oid(value.get(field)):
            violations.append(
                f"{prefix}.{field} must be a 40- or 64-character lowercase object identity"
            )


def _validate_rollouts(
    run: Mapping[str, Any], prefix: str, violations: list[str]
) -> dict[str, set[str]]:
    identities: dict[str, set[str]] = {
        "session_id": set(),
        "rollout_filename": set(),
        "sha256": set(),
    }
    inventory = _sequence(run.get("rollout_inventory"))
    if inventory is None or len(inventory) == 0:
        violations.append(f"{prefix}.rollout_inventory must be a non-empty list")
        return identities

    root_ids: set[str] = set()
    guardian_ids: set[str] = set()
    parent_pairs: list[tuple[str, str | None]] = []
    for index, raw_item in enumerate(inventory):
        item = _mapping(raw_item)
        item_prefix = f"{prefix}.rollout_inventory[{index}]"
        if item is None:
            violations.append(f"{item_prefix} must be an object")
            continue
        actor = item.get("actor")
        session_id = item.get("session_id")
        parent_id = item.get("parent_session_id")
        if actor not in ROLLOUT_ACTORS:
            violations.append(f"{item_prefix}.actor must be root or guardian")
        if not _non_empty_string(session_id):
            violations.append(f"{item_prefix}.session_id must be a non-empty string")
            continue
        if session_id in identities["session_id"]:
            violations.append(f"{item_prefix}.session_id must be unique")
        identities["session_id"].add(session_id)
        if actor == "root":
            root_ids.add(session_id)
            if parent_id is not None:
                violations.append(
                    f"{item_prefix}.parent_session_id must be null for root"
                )
        elif actor == "guardian":
            guardian_ids.add(session_id)
            if not _non_empty_string(parent_id):
                violations.append(
                    f"{item_prefix}.parent_session_id must identify its root session"
                )
        parent_pairs.append(
            (session_id, parent_id if isinstance(parent_id, str) else None)
        )
        for field in ("rollout_filename", "sha256"):
            value = item.get(field)
            if not _non_empty_string(value):
                violations.append(f"{item_prefix}.{field} must be a non-empty string")
            else:
                identities[field].add(value)
        if not _sha256(item.get("sha256")):
            violations.append(f"{item_prefix}.sha256 must be a lowercase SHA-256")
        byte_size = item.get("byte_size")
        if (
            not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size < 0
        ):
            violations.append(f"{item_prefix}.byte_size must be a non-negative integer")

    for session_id, parent_id in parent_pairs:
        if parent_id is not None and parent_id not in root_ids:
            violations.append(
                f"{prefix}: session {session_id!r} has unknown root parent {parent_id!r}"
            )

    declared_root = set(_sequence(run.get("root_session_ids")) or ())
    declared_guardian = set(_sequence(run.get("guardian_session_ids")) or ())
    if declared_root != root_ids:
        violations.append(f"{prefix}.root_session_ids must match root rollout sessions")
    if declared_guardian != guardian_ids:
        violations.append(
            f"{prefix}.guardian_session_ids must match guardian rollout sessions"
        )
    return identities


def _validate_metrics(
    run: Mapping[str, Any], prefix: str, violations: list[str]
) -> None:
    metrics = _mapping(run.get("measured_metrics"))
    if metrics is None:
        violations.append(f"{prefix}.measured_metrics must be an object")
        return
    for field in MEASURED_METRIC_FIELDS:
        if field not in metrics:
            violations.append(f"{prefix}.measured_metrics.{field} is required")


def _validate_workflow_evidence(
    run: Mapping[str, Any], prefix: str, violations: list[str]
) -> None:
    evidence = _mapping(run.get("workflow_evidence"))
    if evidence is None:
        violations.append(f"{prefix}.workflow_evidence must be an object")
        return
    if not _non_empty_string(evidence.get("implementation_or_fixed_patch_identity")):
        violations.append(
            f"{prefix}.workflow_evidence.implementation_or_fixed_patch_identity "
            "must be a non-empty string"
        )
    for field in ("git_pr_issue_identity", "validation", "review_result", "integrity"):
        value = _mapping(evidence.get(field))
        if value is None or len(value) == 0:
            violations.append(f"{prefix}.workflow_evidence.{field} must be non-empty")
    cleanup = _sequence(evidence.get("cleanup_preconditions"))
    if cleanup is None or len(cleanup) == 0:
        violations.append(
            f"{prefix}.workflow_evidence.cleanup_preconditions must be non-empty"
        )


def _validate_conductor(
    conductor: object,
    frozen: bool,
    measured_rollout_identities: Mapping[str, set[str]],
    violations: list[str],
) -> None:
    value = _mapping(conductor)
    if value is None:
        violations.append("conductor must be an object")
        return
    if value.get("excluded_from_measured_metrics") is not True:
        violations.append("conductor.excluded_from_measured_metrics must be true")
    if not frozen:
        return
    inventory = _sequence(value.get("session_inventory"))
    if inventory is None or len(inventory) == 0:
        violations.append("conductor.session_inventory must be non-empty with evidence")
        return
    for index, raw_item in enumerate(inventory):
        item = _mapping(raw_item)
        prefix = f"conductor.session_inventory[{index}]"
        if item is None:
            violations.append(f"{prefix} must be an object")
            continue
        if item.get("role") not in {"conductor", "evidence_collector"}:
            violations.append(f"{prefix}.role is invalid")
        for field in ("session_id", "rollout_filename", "sha256"):
            item_value = item.get(field)
            if not _non_empty_string(item_value):
                violations.append(f"{prefix}.{field} must be a non-empty string")
            elif item_value in measured_rollout_identities[field]:
                violations.append(
                    f"{prefix}.{field} must not overlap measured rollout identity"
                )
        if not _sha256(item.get("sha256")):
            violations.append(f"{prefix}.sha256 must be a lowercase SHA-256")
        byte_size = item.get("byte_size")
        if (
            not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or byte_size < 0
        ):
            violations.append(f"{prefix}.byte_size must be a non-negative integer")
        if "parent_session_id" not in item:
            violations.append(f"{prefix}.parent_session_id is required")


def validate_record(record: Mapping[str, object], *, checkpoint: str) -> list[str]:
    """Validate a protocol record at its pre-run or evidence-freeze gate."""
    violations: list[str] = []
    if checkpoint not in CHECKPOINTS:
        return [f"checkpoint must be one of {sorted(CHECKPOINTS)!r}"]
    if record.get("schema_version") != SCHEMA_VERSION:
        violations.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in ("protocol_id", "candidate_id", "baseline_main_sha"):
        if not _non_empty_string(record.get(field)):
            violations.append(f"{field} must be a non-empty string")
    if not _git_or_patch_oid(record.get("baseline_main_sha")):
        violations.append(
            "baseline_main_sha must be a 40- or 64-character lowercase Git object identity"
        )

    boundary = _mapping(record.get("measured_boundary"))
    if boundary is None:
        violations.append("measured_boundary must be an object")
    else:
        for field in ("start_condition", "end_condition", "frozen_at"):
            if not _non_empty_string(boundary.get(field)):
                violations.append(
                    f"measured_boundary.{field} must be a non-empty string"
                )
        for field in (
            "includes_remediation",
            "includes_independent_review",
            "includes_merge",
            "includes_closeout",
        ):
            if not isinstance(boundary.get(field), bool):
                violations.append(f"measured_boundary.{field} must be boolean")
        if boundary.get("evidence_collection_excluded") is not True:
            violations.append(
                "measured_boundary.evidence_collection_excluded must be true"
            )

    runs = _mapping(record.get("runs"))
    run_identities: dict[str, Mapping[str, Any]] = {}
    measured_rollout_identities: dict[str, set[str]] = {
        "session_id": set(),
        "rollout_filename": set(),
        "sha256": set(),
    }
    measured_arm_rollout_identities: dict[str, dict[str, set[str]]] = {}
    if runs is None:
        violations.append("runs must be an object")
    else:
        for name in ("before", "after"):
            run = _mapping(runs.get(name))
            prefix = f"runs.{name}"
            if run is None:
                violations.append(f"{prefix} must be an object")
                continue
            identity = _mapping(run.get("experiment_identity"))
            _validate_identity(identity, f"{prefix}.experiment_identity", violations)
            if identity is not None:
                run_identities[name] = identity
            if checkpoint == "evidence_frozen":
                rollout_identities = _validate_rollouts(run, prefix, violations)
                measured_arm_rollout_identities[name] = rollout_identities
                for field, values in rollout_identities.items():
                    measured_rollout_identities[field].update(values)
                _validate_metrics(run, prefix, violations)
                _validate_workflow_evidence(run, prefix, violations)

        if set(measured_arm_rollout_identities) == {"before", "after"}:
            before = measured_arm_rollout_identities["before"]
            after = measured_arm_rollout_identities["after"]
            for field in ("session_id", "rollout_filename", "sha256"):
                if before[field] & after[field]:
                    violations.append(
                        f"runs.before and runs.after rollout {field} values must be disjoint"
                    )

    comparability = _mapping(record.get("comparability"))
    if comparability is None:
        violations.append("comparability must be an object")
    else:
        dimensions = _mapping(comparability.get("dimensions"))
        declared = comparability.get("classification")
        if dimensions is None:
            violations.append("comparability.dimensions must be an object")
        else:
            effective_dimensions = dimensions
            if set(run_identities) == {"before", "after"}:
                for field in (
                    "task_or_fixed_patch_identity",
                    "task_spec_hash",
                    "expected_head_or_patch_identity",
                ):
                    if run_identities["before"].get(field) != run_identities[
                        "after"
                    ].get(field):
                        violations.append(
                            f"runs.before and runs.after experiment_identity.{field} "
                            "must match for comparability"
                        )
                mechanically_derived = derive_change_dimensions(
                    run_identities["before"],
                    run_identities["after"],
                    baseline_main_sha=record.get("baseline_main_sha"),
                    candidate_main_sha=record.get("candidate_main_sha"),
                )
                if checkpoint == "pre_run":
                    mechanically_derived["workflow_change"] = bool(
                        dimensions.get("workflow_change")
                    )
                effective_dimensions = mechanically_derived
                for field, actual in mechanically_derived.items():
                    if checkpoint == "pre_run" and field == "workflow_change":
                        continue
                    if dimensions.get(field) != actual:
                        violations.append(
                            f"comparability.dimensions.{field} must be {actual} "
                            "for BEFORE/AFTER identities"
                        )
            derived = derive_comparability(effective_dimensions)
            if declared not in COMPARABILITY_VALUES:
                violations.append("comparability.classification is invalid")
            elif declared != derived:
                violations.append(
                    f"comparability.classification must be {derived} for recorded dimensions"
                )
            if derived == "CONDITIONAL" and not _non_empty_string(
                comparability.get("conditional_reason")
            ):
                violations.append(
                    "comparability.conditional_reason is required for CONDITIONAL"
                )

    contamination = _mapping(record.get("contamination_audit"))
    if contamination is None:
        violations.append("contamination_audit must be an object")
    else:
        accesses = _sequence(contamination.get("accesses"))
        if accesses is None:
            violations.append("contamination_audit.accesses must be a list")
        else:
            for index, raw_access in enumerate(accesses):
                access = _mapping(raw_access)
                if (
                    access is None
                    or access.get("classification") not in CONTAMINATION_CLASSES
                ):
                    violations.append(
                        "contamination_audit.accesses"
                        f"[{index}].classification must distinguish metadata-only "
                        "from answer-bearing access"
                    )
        if contamination.get("proactive_answer_access_prohibited") is not True:
            violations.append(
                "contamination_audit.proactive_answer_access_prohibited must be true"
            )

    _validate_conductor(
        record.get("conductor"),
        checkpoint == "evidence_frozen",
        measured_rollout_identities,
        violations,
    )

    if checkpoint == "evidence_frozen":
        candidate_sha = record.get("candidate_main_sha")
        if not _non_empty_string(candidate_sha):
            violations.append("candidate_main_sha must be frozen with evidence")
        elif not _git_or_patch_oid(candidate_sha):
            violations.append(
                "candidate_main_sha must be a 40- or 64-character lowercase Git object identity"
            )
        freeze = _mapping(record.get("evidence_freeze"))
        if freeze is None or not _non_empty_string(freeze.get("frozen_at")):
            violations.append("evidence_freeze.frozen_at must be a non-empty string")
        decision = _mapping(record.get("decision"))
        if decision is None or decision.get("outcome") not in {"keep", "revert"}:
            violations.append("decision.outcome must be keep or revert")
        elif decision is not None:
            criteria = _mapping(decision.get("criteria"))
            if criteria is None:
                violations.append("decision.criteria must be an object")
            else:
                positive = (
                    criteria.get("correctness") == "pass"
                    and criteria.get("quality_gate") == "pass"
                    and criteria.get("hypothesis_supported") is True
                    and criteria.get("operational_regression") == "acceptable"
                )
                negative = (
                    criteria.get("correctness") == "fail"
                    or criteria.get("quality_gate") == "degraded"
                    or criteria.get("hypothesis_supported") is False
                    or criteria.get("operational_regression") == "unacceptable"
                )
                if decision.get("outcome") == "keep" and not positive:
                    violations.append(
                        "decision.outcome keep requires all keep criteria"
                    )
                if decision.get("outcome") == "revert" and not negative:
                    violations.append(
                        "decision.outcome revert requires a revert condition"
                    )
        revert = _mapping(record.get("revert_plan"))
        if revert is None:
            violations.append("revert_plan must be an object")
        else:
            for field in (
                "candidate_commit_sha",
                "mechanical_method",
                "expected_restored_tree",
            ):
                if not _non_empty_string(revert.get(field)):
                    violations.append(f"revert_plan.{field} must be a non-empty string")
            checks = _sequence(revert.get("cleanup_integrity_checks"))
            if checks is None or len(checks) == 0:
                violations.append(
                    "revert_plan.cleanup_integrity_checks must be non-empty"
                )

    return violations
