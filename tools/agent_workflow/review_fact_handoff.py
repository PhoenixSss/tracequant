#!/usr/bin/env python3
"""Bounded mechanical facts that a fresh Independent Review may revalidate.

This module deliberately contains no semantic review result.  A handoff is a
candidate input for a fresh Review root, never a verdict or an acceptance
summary.  The current Task/PR/effective diff must still be collected and
compared before the facts can be trusted.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

if __name__ != "__main__" or not any(p.endswith("agent_workflow") for p in sys.path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from workflow_common import (  # noqa: E402
    WorkflowToolError,
    atomic_write_json,
    is_sha,
    sha256_bytes,
    sha256_json,
)

SCHEMA_VERSION: Final = 1
HANDOFF_ROOT: Final = ".agents/evidence.local/review-handoffs"
HANDOFF_FIELDS: Final = (
    "schema_version",
    "task_id",
    "pr_id",
    "task_spec_hash",
    "base_sha",
    "head_sha",
    "effective_diff_sha256",
    "changed_files_manifest",
    "acceptance_criteria_ids",
    "raw_check_facts",
    "validation_facts",
    "workflow_identity",
    "created_at",
    "source_identity",
    "freshness_contract",
)
PROHIBITED_FIELDS: Final = frozenset(
    {
        "delivery_correctness_verdict",
        "delivery_risk_verdict",
        "delivery_ac_satisfaction_verdict",
        "review_conclusion",
        "recommended_review_outcome",
    }
)
DRIFT_TYPES: Final = (
    "TASK_SPEC_DRIFT",
    "BASE_DRIFT",
    "HEAD_DRIFT",
    "EFFECTIVE_DIFF_DRIFT",
    "CHECKS_DRIFT",
    "VALIDATION_DRIFT",
    "WORKFLOW_RULE_DRIFT",
    "HANDOFF_SCHEMA_DRIFT",
)
_AC_ID = re.compile(r"^AC-[0-9]+$")
_RELATIVE_PATH = re.compile(r"^[^/].*$")


class ReviewFactHandoffError(WorkflowToolError):
    """Expected fail-closed error for a malformed or stale handoff."""


def default_freshness_contract() -> dict[str, Any]:
    """Return the explicit invalidation contract required by the schema."""
    return {
        "invalidate_on": list(DRIFT_TYPES),
        "revalidate_current_facts": [
            "task_pr_identity",
            "base_head_merge_base",
            "effective_diff_and_changed_files",
            "task_spec_and_acceptance_criteria",
            "checks_and_required_configuration",
            "validation_result_freshness",
            "workflow_identity",
        ],
        "requires_new_semantic_context_on_object_drift": True,
    }


def _walk_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                path = f"{prefix}.{key}" if prefix else key
                if key in PROHIBITED_FIELDS:
                    found.append(path)
                found.extend(_walk_keys(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_walk_keys(nested, f"{prefix}[{index}]"))
    return found


def _is_relative_repo_path(value: Any) -> bool:
    if not isinstance(value, str) or not _RELATIVE_PATH.fullmatch(value):
        return False
    path = Path(value)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and "" not in path.parts
        and "\\" not in value
    )


def _require_sha(value: Any, field: str, violations: list[str]) -> None:
    if not is_sha(value) or not isinstance(value, str) or len(value) not in {40, 64}:
        violations.append(f"{field} must be a 40- or 64-character SHA")


def _require_mapping(value: Any, field: str, violations: list[str]) -> None:
    if not isinstance(value, Mapping):
        violations.append(f"{field} must be an object")


def validate_handoff_structure(
    handoff: Mapping[str, Any], *, expected_repository: str | None = None
) -> list[str]:
    """Return structural violations; an empty list means trusted shape only.

    Identity, freshness, and source checks are intentionally separate so the
    Review entry can report malformed input distinctly from current-object
    drift.  Unknown top-level fields are rejected to prevent semantic claims
    from being smuggled into an otherwise valid mechanical package.
    """
    violations: list[str] = []
    if not isinstance(handoff, Mapping):
        return ["handoff must be an object"]

    missing = [field for field in HANDOFF_FIELDS if field not in handoff]
    extra = sorted(set(handoff) - set(HANDOFF_FIELDS))
    if missing:
        violations.extend(f"missing field: {field}" for field in missing)
    if extra:
        violations.append(f"unknown top-level fields: {extra}")
    prohibited = _walk_keys(handoff)
    if prohibited:
        violations.append(f"prohibited semantic fields: {sorted(prohibited)}")

    if handoff.get("schema_version") != SCHEMA_VERSION:
        violations.append(f"schema_version must be {SCHEMA_VERSION}")
    for field in ("task_id", "pr_id"):
        value = handoff.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            violations.append(f"{field} must be a positive integer")
    task_spec_hash = handoff.get("task_spec_hash")
    if not isinstance(task_spec_hash, str) or not is_sha(task_spec_hash) or len(task_spec_hash) != 64:
        violations.append("task_spec_hash must be a 64-character SHA-256")
    for field in ("base_sha", "head_sha"):
        value = handoff.get(field)
        if not isinstance(value, str) or not is_sha(value) or len(value) != 40:
            violations.append(f"{field} must be a 40-character Git SHA")
    _require_sha(handoff.get("effective_diff_sha256"), "effective_diff_sha256", violations)

    files = handoff.get("changed_files_manifest")
    if not isinstance(files, list) or not files:
        violations.append("changed_files_manifest must be a non-empty list")
    elif any(not _is_relative_repo_path(item) for item in files):
        violations.append("changed_files_manifest contains an invalid repository path")
    elif files != sorted(set(files)):
        violations.append("changed_files_manifest must be sorted and unique")

    ac_ids = handoff.get("acceptance_criteria_ids")
    if not isinstance(ac_ids, list) or not ac_ids:
        violations.append("acceptance_criteria_ids must be a non-empty list")
    elif any(not isinstance(item, str) or not _AC_ID.fullmatch(item) for item in ac_ids):
        violations.append("acceptance_criteria_ids must contain only AC-N identifiers")
    elif ac_ids != sorted(set(ac_ids), key=lambda item: int(item[3:])):
        violations.append("acceptance_criteria_ids must be ordered and unique")

    raw_checks = handoff.get("raw_check_facts")
    _require_mapping(raw_checks, "raw_check_facts", violations)
    if isinstance(raw_checks, Mapping):
        extra_check_fields = sorted(set(raw_checks) - {"observed", "required"})
        if extra_check_fields:
            violations.append(
                f"raw_check_facts has non-mechanical fields: {extra_check_fields}"
            )
        if not isinstance(raw_checks.get("observed"), list):
            violations.append("raw_check_facts.observed must be a list")
        else:
            allowed_check_fields = {
                "name",
                "state",
                "run_id",
                "started_at",
                "completed_at",
                "source_url",
            }
            for index, item in enumerate(raw_checks["observed"]):
                if not isinstance(item, Mapping):
                    violations.append(f"raw_check_facts.observed[{index}] must be an object")
                else:
                    extra = sorted(set(item) - allowed_check_fields)
                    if extra:
                        violations.append(
                            f"raw_check_facts.observed[{index}] has non-mechanical fields: {extra}"
                        )
        _require_mapping(raw_checks.get("required"), "raw_check_facts.required", violations)
        required = raw_checks.get("required")
        if isinstance(required, Mapping):
            extra = sorted(set(required) - {"configuration", "contexts", "failure"})
            if extra:
                violations.append(
                    f"raw_check_facts.required has non-mechanical fields: {extra}"
                )

    validation = handoff.get("validation_facts")
    _require_mapping(validation, "validation_facts", violations)
    if isinstance(validation, Mapping):
        extra = sorted(
            set(validation)
            - {
                "profile",
                "schema_version",
                "runner_identity",
                "exit_code",
                "result_locator",
                "result_sha256",
            }
        )
        if extra:
            violations.append(
                f"validation_facts has non-mechanical fields: {extra}"
            )
        for field in (
            "profile",
            "schema_version",
            "runner_identity",
            "exit_code",
            "result_locator",
            "result_sha256",
        ):
            if field not in validation:
                violations.append(f"validation_facts missing field: {field}")
        if not isinstance(validation.get("profile"), str):
            violations.append("validation_facts.profile must be a string")
        if not isinstance(validation.get("runner_identity"), Mapping):
            violations.append("validation_facts.runner_identity must be an object")
        if not isinstance(validation.get("exit_code"), int):
            violations.append("validation_facts.exit_code must be an integer")
        if not _is_relative_repo_path(validation.get("result_locator")):
            violations.append("validation_facts.result_locator must be repository-relative")
        result_sha = validation.get("result_sha256")
        if not isinstance(result_sha, str) or not is_sha(result_sha) or len(result_sha) != 64:
            violations.append("validation_facts.result_sha256 must be a 64-character SHA-256")

    workflow = handoff.get("workflow_identity")
    _require_mapping(workflow, "workflow_identity", violations)
    if isinstance(workflow, Mapping):
        for field in ("profile", "schema_version", "runner", "skill"):
            if field not in workflow:
                violations.append(f"workflow_identity missing field: {field}")
        if not isinstance(workflow.get("profile"), str):
            violations.append("workflow_identity.profile must be a string")
        if not isinstance(workflow.get("runner"), Mapping):
            violations.append("workflow_identity.runner must be an object")
        if not isinstance(workflow.get("skill"), Mapping):
            violations.append("workflow_identity.skill must be an object")

    source = handoff.get("source_identity")
    _require_mapping(source, "source_identity", violations)
    if isinstance(source, Mapping):
        if not isinstance(source.get("repository"), str):
            violations.append("source_identity.repository must be a string")
        if not isinstance(source.get("source_locator"), str):
            violations.append("source_identity.source_locator must be a string")
        source_digest = source.get("source_digest")
        if not isinstance(source_digest, str) or not is_sha(source_digest) or len(source_digest) != 64:
            violations.append("source_identity.source_digest must be a 64-character SHA-256")
        if expected_repository and source.get("repository") != expected_repository:
            violations.append("source_identity.repository does not match repository")

    freshness = handoff.get("freshness_contract")
    _require_mapping(freshness, "freshness_contract", violations)
    if isinstance(freshness, Mapping):
        invalidations = freshness.get("invalidate_on")
        if not isinstance(invalidations, list) or set(invalidations) != set(DRIFT_TYPES):
            violations.append("freshness_contract.invalidate_on must list all supported drift types")
        if not isinstance(freshness.get("revalidate_current_facts"), list):
            violations.append("freshness_contract.revalidate_current_facts must be a list")
        if freshness.get("requires_new_semantic_context_on_object_drift") is not True:
            violations.append(
                "freshness_contract must require a new semantic context on object drift"
            )

    created_at = handoff.get("created_at")
    if not isinstance(created_at, str):
        violations.append("created_at must be an ISO-8601 string")
    else:
        try:
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            violations.append("created_at must be an ISO-8601 string")
    return violations


def load_handoff(path: Path, *, expected_repository: str | None = None) -> tuple[dict[str, Any], str]:
    """Load and structurally validate one handoff, returning value and digest."""
    if path.is_symlink():
        raise ReviewFactHandoffError("handoff path must not be a symlink")
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ReviewFactHandoffError(f"handoff file is missing: {path}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewFactHandoffError(f"handoff is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReviewFactHandoffError("handoff must be a JSON object")
    violations = validate_handoff_structure(value, expected_repository=expected_repository)
    if violations:
        raise ReviewFactHandoffError("; ".join(violations))
    return value, sha256_bytes(raw)


def resolve_handoff_path(repo_root: Path, value: str) -> Path:
    """Resolve a read-only, repository-relative handoff under ignored evidence."""
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise ReviewFactHandoffError("handoff path must be repository-relative")
    root = (repo_root / HANDOFF_ROOT).resolve()
    path = (repo_root / candidate).resolve()
    if path == root or root not in path.parents:
        raise ReviewFactHandoffError(
            f"handoff path must be under {HANDOFF_ROOT}/"
        )
    return path


def _current_changed_files(snapshot: Mapping[str, Any]) -> tuple[list[str] | None, str | None]:
    observed = snapshot.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    diff = observed.get("effective_diff")
    diff = diff if isinstance(diff, Mapping) else {}
    files = diff.get("changed_files")
    if not isinstance(files, Mapping) or files.get("truncated") is True:
        return None, "current effective diff manifest is unavailable or truncated"
    items = files.get("items")
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        return None, "current effective diff manifest is unavailable"
    return sorted(set(items)), None


def _current_raw_check_facts(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    observed = snapshot.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    pr = observed.get("pr")
    pr = pr if isinstance(pr, Mapping) else {}
    checks = pr.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    required = observed.get("required_checks")
    required = required if isinstance(required, Mapping) else {}
    bounded_items = checks.get("items")
    if isinstance(bounded_items, Mapping):
        if bounded_items.get("truncated") is True:
            return None
        items = bounded_items.get("items")
    else:
        items = bounded_items
    if not isinstance(items, list):
        return None
    raw_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            return None
        raw_items.append(
            {
                "name": item.get("name"),
                "state": item.get("state"),
                "run_id": item.get("run_id"),
                "started_at": item.get("started_at"),
                "completed_at": item.get("completed_at"),
                "source_url": item.get("source_url"),
            }
        )
    return {"observed": raw_items, "required": dict(required)}


def validate_against_snapshot(
    handoff: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    handoff_digest: str | None = None,
    current_acceptance_criteria_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Compare trusted handoff candidates with freshly collected current facts."""
    errors = validate_handoff_structure(handoff)
    observed = snapshot.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    issue = observed.get("issue")
    issue = issue if isinstance(issue, Mapping) else {}
    pr = observed.get("pr")
    pr = pr if isinstance(pr, Mapping) else {}
    diff = observed.get("effective_diff")
    diff = diff if isinstance(diff, Mapping) else {}
    repository = snapshot.get("repository")
    source = handoff.get("source_identity")
    source_repository = source.get("repository") if isinstance(source, Mapping) else None
    if isinstance(repository, str) and source_repository != repository:
        errors.append("TASK_PR_IDENTITY_DRIFT: repository")
    comparisons = (
        ("task_id", issue.get("number"), "TASK_PR_IDENTITY_DRIFT"),
        ("pr_id", pr.get("number"), "TASK_PR_IDENTITY_DRIFT"),
        ("task_spec_hash", issue.get("spec_sha256"), "TASK_SPEC_DRIFT"),
        ("base_sha", pr.get("base_sha"), "BASE_DRIFT"),
        ("head_sha", pr.get("head_sha"), "HEAD_DRIFT"),
        ("effective_diff_sha256", diff.get("sha256"), "EFFECTIVE_DIFF_DRIFT"),
    )
    for field, current, drift in comparisons:
        if field in handoff and handoff.get(field) != current:
            errors.append(f"{drift}: {field}")

    files, file_error = _current_changed_files(snapshot)
    if file_error:
        errors.append(f"EFFECTIVE_DIFF_DRIFT: {file_error}")
    elif files != handoff.get("changed_files_manifest"):
        errors.append("EFFECTIVE_DIFF_DRIFT: changed_files_manifest")

    if current_acceptance_criteria_ids is None:
        errors.append("TASK_SPEC_DRIFT: current acceptance_criteria_ids unavailable")
    elif list(current_acceptance_criteria_ids) != handoff.get("acceptance_criteria_ids"):
        errors.append("TASK_SPEC_DRIFT: acceptance_criteria_ids")

    current_checks = _current_raw_check_facts(snapshot)
    if current_checks is None or current_checks != handoff.get("raw_check_facts"):
        errors.append("CHECKS_DRIFT: raw_check_facts")

    unique_errors = list(dict.fromkeys(errors))
    status = "pass" if not unique_errors else "fail"
    return {
        "available": True,
        "status": status,
        "trusted": status == "pass",
        "strategy": "FRESH_ROOT_BOUNDED_HANDOFF" if status == "pass" else "FAIL_CLOSED",
        "handoff_sha256": handoff_digest,
        "invalidated": unique_errors,
        "revalidation_required": [
            "task_pr_identity",
            "base_head_merge_base",
            "effective_diff_and_changed_files",
            "task_spec_and_acceptance_criteria",
            "checks_and_required_configuration",
            "validation_result_freshness",
            "workflow_identity",
        ],
    }


def build_handoff_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    acceptance_criteria_ids: Sequence[str],
    validation_facts: Mapping[str, Any],
    workflow_identity: Mapping[str, Any],
    source_identity: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build the exact bounded field set from an evidence snapshot.

    The caller supplies validation facts and AC identifiers because those facts
    come from the separate validation run and semantic Review context.  This
    function only copies mechanical values from the snapshot.
    """
    observed = snapshot.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    issue = observed.get("issue")
    issue = issue if isinstance(issue, Mapping) else {}
    pr = observed.get("pr")
    pr = pr if isinstance(pr, Mapping) else {}
    diff = observed.get("effective_diff")
    diff = diff if isinstance(diff, Mapping) else {}
    files, file_error = _current_changed_files(snapshot)
    if file_error or files is None:
        raise ReviewFactHandoffError(file_error or "changed file manifest unavailable")
    raw_checks = _current_raw_check_facts(snapshot)
    if raw_checks is None:
        raise ReviewFactHandoffError("raw check facts unavailable")
    repository = snapshot.get("repository")
    if not isinstance(repository, str):
        raise ReviewFactHandoffError("snapshot repository is unavailable")
    source = dict(source_identity or {})
    source.setdefault("repository", repository)
    source.setdefault(
        "source_locator", f"snapshot:{snapshot.get('snapshot_id', 'unknown')}"
    )
    source.setdefault("source_digest", sha256_json(snapshot))
    handoff = {
        "schema_version": SCHEMA_VERSION,
        "task_id": issue.get("number"),
        "pr_id": pr.get("number"),
        "task_spec_hash": issue.get("spec_sha256"),
        "base_sha": pr.get("base_sha"),
        "head_sha": pr.get("head_sha"),
        "effective_diff_sha256": diff.get("sha256"),
        "changed_files_manifest": files,
        "acceptance_criteria_ids": list(acceptance_criteria_ids),
        "raw_check_facts": raw_checks,
        "validation_facts": dict(validation_facts),
        "workflow_identity": dict(workflow_identity),
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "source_identity": source,
        "freshness_contract": default_freshness_contract(),
    }
    violations = validate_handoff_structure(handoff, expected_repository=repository)
    if violations:
        raise ReviewFactHandoffError("; ".join(violations))
    return handoff


def write_handoff(repo_root: Path, handoff: Mapping[str, Any], *, filename: str) -> Path:
    """Write a validated handoff to the exact ignored evidence root."""
    violations = validate_handoff_structure(handoff)
    if violations:
        raise ReviewFactHandoffError("; ".join(violations))
    if not filename or Path(filename).name != filename or not filename.endswith(".json"):
        raise ReviewFactHandoffError("handoff filename must be a simple .json filename")
    root = repo_root / HANDOFF_ROOT
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    atomic_write_json(path, dict(handoff))
    return path
