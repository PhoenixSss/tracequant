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
_SOURCE_LOCATOR = re.compile(r"^snapshot:ev-[0-9a-f]{16}$")

RAW_CHECK_FIELDS: Final = frozenset({"observed", "required"})
CHECK_ITEM_FIELDS: Final = frozenset(
    {"name", "state", "category", "run_id", "started_at", "completed_at", "source_url"}
)
BOUNDED_LIST_FIELDS: Final = frozenset({"items", "count", "truncated"})
REQUIRED_CHECK_FIELDS: Final = frozenset({"configuration", "contexts", "failure"})
REQUIRED_FAILURE_FIELDS: Final = frozenset(
    {"category", "reason", "http_status", "command_id"}
)
VALIDATION_FIELDS: Final = frozenset(
    {
        "base_sha",
        "profile",
        "schema_version",
        "runner_identity",
        "exit_code",
        "result_locator",
        "result_sha256",
    }
)
VALIDATION_RUNNER_FIELDS: Final = frozenset(
    {
        "path",
        "sha256",
        "profile_spec_path",
        "profile_spec_sha256",
        "rules_path",
        "rules_sha256",
        "workflow_validation_path",
        "workflow_validation_sha256",
        "skill",
    }
)
WORKFLOW_FIELDS: Final = frozenset({"profile", "schema_version", "runner", "skill"})
WORKFLOW_RUNNER_FIELDS: Final = frozenset(
    {"path", "source_sha", "content_sha256", "handoff_schema"}
)
WORKFLOW_SCHEMA_FIELDS: Final = frozenset({"path", "content_sha256"})
WORKFLOW_SKILL_FIELDS: Final = frozenset({"path", "sha256"})
SOURCE_FIELDS: Final = frozenset({"repository", "source_locator", "source_digest"})
SOURCE_STABLE_FIELDS: Final = (
    "schema_version",
    "repository",
    "subject",
    "execution_context",
    "observed",
    "gates",
    "limitations",
)
FRESHNESS_FIELDS: Final = frozenset(
    {
        "invalidate_on",
        "revalidate_current_facts",
        "requires_new_semantic_context_on_object_drift",
    }
)
VALIDATION_PROFILES: Final = frozenset(
    {"workflow-delivery", "workflow-review", "workflow-closeout"}
)
VALIDATION_IDENTITY_PATHS: Final = {
    "path": "tools/agent_workflow/wsl2_validation_runner.py",
    "profile_spec_path": "tools/agent_workflow/wsl2_validation_profiles.json",
    "rules_path": ".codex/rules/tracequant-wsl-validation.rules",
    "workflow_validation_path": "tools/agent_workflow/workflow_validation.py",
}
VALIDATION_SKILL_NAMES: Final = {
    "workflow-delivery": "task-delivery-runner",
    "workflow-review": "task-pr-review-runner",
    "workflow-closeout": "task-closeout",
}


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
            "validation_facts",
            "workflow_identity",
            "source_identity",
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


def _exact_fields(
    value: Any,
    field: str,
    allowed: frozenset[str],
    required: frozenset[str],
    violations: list[str],
) -> Mapping[str, Any] | None:
    """Validate one bounded object and reject every unspecified field."""
    if not isinstance(value, Mapping):
        violations.append(f"{field} must be an object")
        return None
    extra = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if extra:
        violations.append(f"{field} has unknown fields: {extra}")
    if missing:
        violations.extend(f"{field} missing field: {item}" for item in missing)
    return value


def _require_string(
    value: Any, field: str, violations: list[str], *, non_empty: bool = True
) -> None:
    if not isinstance(value, str) or (non_empty and not value):
        violations.append(f"{field} must be a string")


def _require_sha_length(
    value: Any, field: str, length: int, violations: list[str]
) -> None:
    if not isinstance(value, str) or not is_sha(value) or len(value) != length:
        violations.append(f"{field} must be a {length}-character SHA")


def _validate_bounded_list(
    value: Any, field: str, violations: list[str], *, item_type: type = str
) -> None:
    bounded = _exact_fields(
        value,
        field,
        BOUNDED_LIST_FIELDS,
        frozenset(BOUNDED_LIST_FIELDS),
        violations,
    )
    if bounded is None:
        return
    items = bounded.get("items")
    if not isinstance(items, list) or any(
        not isinstance(item, item_type) for item in items
    ):
        violations.append(f"{field}.items must be a list of {item_type.__name__}")
    if not isinstance(bounded.get("truncated"), bool):
        violations.append(f"{field}.truncated must be a boolean")
    if type(bounded.get("count")) is not int or bounded["count"] < 0:
        violations.append(f"{field}.count must be a non-negative integer")


def _validate_workflow_identity(
    value: Any, field: str, violations: list[str]
) -> Mapping[str, Any] | None:
    workflow = _exact_fields(
        value, field, WORKFLOW_FIELDS, frozenset(WORKFLOW_FIELDS), violations
    )
    if workflow is None:
        return None
    if not isinstance(workflow.get("profile"), str) or not workflow["profile"]:
        violations.append(f"{field}.profile must be a non-empty string")
    if workflow.get("schema_version") != SCHEMA_VERSION:
        violations.append(f"{field}.schema_version must be {SCHEMA_VERSION}")

    runner = _exact_fields(
        workflow.get("runner"),
        f"{field}.runner",
        WORKFLOW_RUNNER_FIELDS,
        frozenset(WORKFLOW_RUNNER_FIELDS),
        violations,
    )
    if runner is not None:
        _require_string(runner.get("path"), f"{field}.runner.path", violations)
        _require_sha_length(
            runner.get("source_sha"), f"{field}.runner.source_sha", 40, violations
        )
        _require_sha_length(
            runner.get("content_sha256"),
            f"{field}.runner.content_sha256",
            64,
            violations,
        )
        schema = _exact_fields(
            runner.get("handoff_schema"),
            f"{field}.runner.handoff_schema",
            WORKFLOW_SCHEMA_FIELDS,
            frozenset(WORKFLOW_SCHEMA_FIELDS),
            violations,
        )
        if schema is not None:
            _require_string(
                schema.get("path"),
                f"{field}.runner.handoff_schema.path",
                violations,
            )
            _require_sha_length(
                schema.get("content_sha256"),
                f"{field}.runner.handoff_schema.content_sha256",
                64,
                violations,
            )

    skill = _exact_fields(
        workflow.get("skill"),
        f"{field}.skill",
        WORKFLOW_SKILL_FIELDS,
        frozenset(WORKFLOW_SKILL_FIELDS),
        violations,
    )
    if skill is not None:
        _require_string(skill.get("path"), f"{field}.skill.path", violations)
        _require_sha_length(
            skill.get("sha256"), f"{field}.skill.sha256", 64, violations
        )
    return workflow


def _validate_source_identity(
    value: Any,
    field: str,
    violations: list[str],
    *,
    expected_repository: str | None = None,
) -> Mapping[str, Any] | None:
    source = _exact_fields(
        value, field, SOURCE_FIELDS, frozenset(SOURCE_FIELDS), violations
    )
    if source is None:
        return None
    _require_string(source.get("repository"), f"{field}.repository", violations)
    locator = source.get("source_locator")
    if not isinstance(locator, str) or not _SOURCE_LOCATOR.fullmatch(locator):
        violations.append(
            f"{field}.source_locator must identify a canonical evidence snapshot"
        )
    _require_sha_length(
        source.get("source_digest"), f"{field}.source_digest", 64, violations
    )
    if expected_repository and source.get("repository") != expected_repository:
        violations.append(f"{field}.repository does not match repository")
    return source


def _stable_source_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Select canonical source facts, excluding operation-phase metadata."""
    return {field: snapshot.get(field) for field in SOURCE_STABLE_FIELDS}


def source_identity_for_snapshot(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Return the source identity that remains stable across review phases."""
    repository = snapshot.get("repository")
    if not isinstance(repository, str) or not repository:
        raise ReviewFactHandoffError("current source repository is unavailable")
    stable_digest = sha256_json(_stable_source_projection(snapshot))
    return {
        "repository": repository,
        "source_locator": f"snapshot:ev-{stable_digest[:16]}",
        "source_digest": stable_digest,
    }


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
    if (
        not isinstance(task_spec_hash, str)
        or not is_sha(task_spec_hash)
        or len(task_spec_hash) != 64
    ):
        violations.append("task_spec_hash must be a 64-character SHA-256")
    for field in ("base_sha", "head_sha"):
        value = handoff.get(field)
        if not isinstance(value, str) or not is_sha(value) or len(value) != 40:
            violations.append(f"{field} must be a 40-character Git SHA")
    _require_sha(
        handoff.get("effective_diff_sha256"), "effective_diff_sha256", violations
    )

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
    elif any(
        not isinstance(item, str) or not _AC_ID.fullmatch(item) for item in ac_ids
    ):
        violations.append("acceptance_criteria_ids must contain only AC-N identifiers")
    elif ac_ids != sorted(set(ac_ids), key=lambda item: int(item[3:])):
        violations.append("acceptance_criteria_ids must be ordered and unique")

    raw_checks = handoff.get("raw_check_facts")
    raw_checks_mapping = _exact_fields(
        raw_checks,
        "raw_check_facts",
        RAW_CHECK_FIELDS,
        frozenset(RAW_CHECK_FIELDS),
        violations,
    )
    if raw_checks_mapping is not None:
        observed_checks = raw_checks_mapping.get("observed")
        if not isinstance(observed_checks, list):
            violations.append("raw_check_facts.observed must be a list")
        else:
            for index, item in enumerate(observed_checks):
                check = _exact_fields(
                    item,
                    f"raw_check_facts.observed[{index}]",
                    CHECK_ITEM_FIELDS,
                    frozenset(CHECK_ITEM_FIELDS),
                    violations,
                )
                if check is not None:
                    for field in ("name", "state", "category"):
                        _require_string(
                            check.get(field),
                            f"raw_check_facts.observed[{index}].{field}",
                            violations,
                        )
                    for field in ("run_id", "started_at", "completed_at", "source_url"):
                        value = check.get(field)
                        if value is not None and not isinstance(value, str):
                            violations.append(
                                f"raw_check_facts.observed[{index}].{field} must be a string or null"
                            )

        required = _exact_fields(
            raw_checks_mapping.get("required"),
            "raw_check_facts.required",
            REQUIRED_CHECK_FIELDS,
            frozenset({"configuration", "contexts"}),
            violations,
        )
        if required is not None:
            _require_string(
                required.get("configuration"),
                "raw_check_facts.required.configuration",
                violations,
            )
            _validate_bounded_list(
                required.get("contexts"),
                "raw_check_facts.required.contexts",
                violations,
            )
            if "failure" in required:
                failure = _exact_fields(
                    required.get("failure"),
                    "raw_check_facts.required.failure",
                    REQUIRED_FAILURE_FIELDS,
                    frozenset(REQUIRED_FAILURE_FIELDS),
                    violations,
                )
                if failure is not None:
                    for field in ("category", "reason", "command_id"):
                        _require_string(
                            failure.get(field),
                            f"raw_check_facts.required.failure.{field}",
                            violations,
                        )
                    if failure.get("http_status") is not None and not isinstance(
                        failure.get("http_status"), int
                    ):
                        violations.append(
                            "raw_check_facts.required.failure.http_status must be an integer or null"
                        )

    validation = handoff.get("validation_facts")
    validation_mapping = _exact_fields(
        validation,
        "validation_facts",
        VALIDATION_FIELDS,
        frozenset(VALIDATION_FIELDS),
        violations,
    )
    if validation_mapping is not None:
        _require_sha_length(
            validation_mapping.get("base_sha"),
            "validation_facts.base_sha",
            40,
            violations,
        )
        profile = validation_mapping.get("profile")
        _require_string(
            profile,
            "validation_facts.profile",
            violations,
        )
        if profile not in VALIDATION_PROFILES:
            violations.append(
                "validation_facts.profile is not a canonical workflow profile"
            )
        if validation_mapping.get("schema_version") != SCHEMA_VERSION:
            violations.append(
                f"validation_facts.schema_version must be {SCHEMA_VERSION}"
            )
        identity = _exact_fields(
            validation_mapping.get("runner_identity"),
            "validation_facts.runner_identity",
            VALIDATION_RUNNER_FIELDS,
            frozenset(VALIDATION_RUNNER_FIELDS),
            violations,
        )
        if identity is not None:
            _require_string(
                identity.get("path"),
                "validation_facts.runner_identity.path",
                violations,
            )
            for field in (
                "sha256",
                "profile_spec_sha256",
                "rules_sha256",
                "workflow_validation_sha256",
            ):
                _require_sha_length(
                    identity.get(field),
                    f"validation_facts.runner_identity.{field}",
                    64,
                    violations,
                )
            for field in (
                "profile_spec_path",
                "rules_path",
                "workflow_validation_path",
            ):
                if not _is_relative_repo_path(identity.get(field)):
                    violations.append(
                        f"validation_facts.runner_identity.{field} must be repository-relative"
                    )
            skill = _exact_fields(
                identity.get("skill"),
                "validation_facts.runner_identity.skill",
                WORKFLOW_SKILL_FIELDS,
                frozenset(WORKFLOW_SKILL_FIELDS),
                violations,
            )
            if skill is not None:
                _require_string(
                    skill.get("path"),
                    "validation_facts.runner_identity.skill.path",
                    violations,
                )
                _require_sha_length(
                    skill.get("sha256"),
                    "validation_facts.runner_identity.skill.sha256",
                    64,
                    violations,
                )
            if profile in VALIDATION_PROFILES:
                for field, expected in VALIDATION_IDENTITY_PATHS.items():
                    if identity.get(field) != expected:
                        violations.append(
                            f"validation_facts.runner_identity.{field} is not canonical"
                        )
                skill_path = skill.get("path") if isinstance(skill, Mapping) else None
                expected_skill_suffix = f"/{VALIDATION_SKILL_NAMES[profile]}/SKILL.md"
                if (
                    not isinstance(skill_path, str)
                    or not (
                        skill_path.startswith(".agents/skills/")
                        or skill_path.startswith(".claude/skills/")
                    )
                    or not skill_path.endswith(expected_skill_suffix)
                ):
                    violations.append(
                        "validation_facts.runner_identity.skill.path is not canonical"
                    )
        exit_code = validation_mapping.get("exit_code")
        if type(exit_code) is not int or exit_code != 0:
            violations.append("validation_facts.exit_code must be 0")
        locator = validation_mapping.get("result_locator")
        if not _is_relative_repo_path(locator) or not (
            isinstance(locator, str) and locator.startswith(".agents/validation.local/")
        ):
            violations.append(
                "validation_facts.result_locator must be under .agents/validation.local/"
            )
        _require_sha_length(
            validation_mapping.get("result_sha256"),
            "validation_facts.result_sha256",
            64,
            violations,
        )

    workflow = handoff.get("workflow_identity")
    _validate_workflow_identity(workflow, "workflow_identity", violations)

    source = handoff.get("source_identity")
    _validate_source_identity(
        source,
        "source_identity",
        violations,
        expected_repository=expected_repository,
    )

    freshness = handoff.get("freshness_contract")
    freshness_mapping = _exact_fields(
        freshness,
        "freshness_contract",
        FRESHNESS_FIELDS,
        frozenset(FRESHNESS_FIELDS),
        violations,
    )
    if freshness_mapping is not None:
        invalidations = freshness_mapping.get("invalidate_on")
        if not isinstance(invalidations, list) or set(invalidations) != set(
            DRIFT_TYPES
        ):
            violations.append(
                "freshness_contract.invalidate_on must list all supported drift types"
            )
        current_facts = freshness_mapping.get("revalidate_current_facts")
        if not isinstance(current_facts, list) or any(
            not isinstance(item, str) for item in current_facts
        ):
            violations.append(
                "freshness_contract.revalidate_current_facts must be a list"
            )
        if (
            freshness_mapping.get("requires_new_semantic_context_on_object_drift")
            is not True
        ):
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


def load_handoff(
    path: Path, *, expected_repository: str | None = None
) -> tuple[dict[str, Any], str]:
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
    violations = validate_handoff_structure(
        value, expected_repository=expected_repository
    )
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
        raise ReviewFactHandoffError(f"handoff path must be under {HANDOFF_ROOT}/")
    return path


def _current_changed_files(
    snapshot: Mapping[str, Any],
) -> tuple[list[str] | None, str | None]:
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
                "category": item.get("category"),
                "run_id": item.get("run_id"),
                "started_at": item.get("started_at"),
                "completed_at": item.get("completed_at"),
                "source_url": item.get("source_url"),
            }
        )
    return {"observed": raw_items, "required": dict(required)}


def acquire_current_validation_facts(
    repo_root: Path,
    validation_facts: Mapping[str, Any],
    *,
    expected_base_sha: str | None,
    expected_head_sha: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read and normalize the canonical validation Runner result for a handoff.

    The handoff contains only a locator and digest.  The current result file is
    the canonical mechanical source; its repository/base/head, profile, schema, and
    Runner/Skill identities must all be present and internally verified before
    the facts can be compared with the handoff.
    """
    errors: list[str] = []
    if not isinstance(validation_facts, Mapping):
        return None, ["validation facts unavailable"]
    locator = validation_facts.get("result_locator")
    if not isinstance(locator, str) or not _is_relative_repo_path(locator):
        return None, ["validation result locator is unavailable"]
    root = (repo_root / ".agents/validation.local").resolve()
    path = (repo_root / locator).resolve()
    if not locator.startswith(".agents/validation.local/") or root not in path.parents:
        return None, [
            "validation result locator is outside canonical validation artifacts"
        ]
    if path.is_symlink():
        return None, ["validation result must not be a symlink"]
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, OSError):
        return None, ["validation result is unavailable"]
    result_digest = sha256_bytes(raw)
    if result_digest != validation_facts.get("result_sha256"):
        errors.append("validation result digest mismatch")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, ["validation result is not valid JSON"]
    if not isinstance(document, Mapping):
        return None, ["validation result is not an object"]

    schema_version = document.get("schema_version")
    profile = document.get("profile")
    base_sha = document.get("base_sha")
    status = document.get("status")
    artifacts = document.get("artifacts")
    integrity = document.get("integrity")
    repository = document.get("repository")
    repository_state = (
        repository.get("state") if isinstance(repository, Mapping) else None
    )
    if type(schema_version) is not int:
        errors.append("validation result schema identity is unavailable")
    if not isinstance(profile, str) or not profile:
        errors.append("validation result profile is unavailable")
    elif profile not in VALIDATION_PROFILES:
        errors.append("validation result profile is not canonical")
    if status != "pass":
        errors.append("validation result is not a passing current fact")
    if not is_sha(base_sha):
        errors.append("validation result base identity is unavailable")
    elif not is_sha(expected_base_sha):
        errors.append("expected validation base identity is unavailable")
    elif base_sha != expected_base_sha:
        errors.append("validation result base identity mismatch")
    if not isinstance(artifacts, Mapping) or artifacts.get("result_json") != locator:
        errors.append("validation result locator identity mismatch")
    if not isinstance(repository_state, Mapping):
        errors.append("validation result repository state is unavailable")
    if not isinstance(integrity, Mapping):
        errors.append("validation result integrity is unavailable")

    if isinstance(repository_state, Mapping):
        if repository_state.get("clean") is not True:
            errors.append("validation result repository was not clean")
        if (
            expected_head_sha is None
            or repository_state.get("head_sha") != expected_head_sha
        ):
            errors.append("validation result head identity mismatch")
    if isinstance(integrity, Mapping):
        if integrity.get("verification") != "current-worktree-content":
            errors.append("validation result integrity verification is unavailable")
        if integrity.get("repository_clean") is not True:
            errors.append(
                "validation result integrity does not prove a clean repository"
            )
        if (
            expected_head_sha is None
            or integrity.get("repository_head_sha") != expected_head_sha
        ):
            errors.append("validation result integrity head identity mismatch")

    identity: dict[str, Any] | None = None
    if isinstance(integrity, Mapping):
        skill = integrity.get("skill")
        identity = {
            "path": integrity.get("runner_path"),
            "sha256": integrity.get("runner_sha256"),
            "profile_spec_path": integrity.get("profile_spec_path"),
            "profile_spec_sha256": integrity.get("profile_spec_sha256"),
            "rules_path": integrity.get("rules_path"),
            "rules_sha256": integrity.get("rules_sha256"),
            "workflow_validation_path": integrity.get("workflow_validation_path"),
            "workflow_validation_sha256": integrity.get("workflow_validation_sha256"),
            "skill": dict(skill) if isinstance(skill, Mapping) else skill,
        }
        identity_violations: list[str] = []
        _validate_validation_runner_identity(identity, identity_violations)
        errors.extend(identity_violations)
        if (
            isinstance(profile, str)
            and profile in VALIDATION_PROFILES
            and identity is not None
        ):
            for field, expected in VALIDATION_IDENTITY_PATHS.items():
                if identity.get(field) != expected:
                    errors.append(
                        f"validation result identity is not canonical: {field}"
                    )
            skill = identity.get("skill")
            skill_path = skill.get("path") if isinstance(skill, Mapping) else None
            expected_skill_suffix = f"/{VALIDATION_SKILL_NAMES[profile]}/SKILL.md"
            if (
                not isinstance(skill_path, str)
                or not (
                    skill_path.startswith(".agents/skills/")
                    or skill_path.startswith(".claude/skills/")
                )
                or not skill_path.endswith(expected_skill_suffix)
            ):
                errors.append("validation result Skill identity is not canonical")
        if not identity_violations:
            identity_files = (
                ("path", "sha256"),
                ("profile_spec_path", "profile_spec_sha256"),
                ("rules_path", "rules_sha256"),
                ("workflow_validation_path", "workflow_validation_sha256"),
            )
            for path_field, digest_field in identity_files:
                relative = identity.get(path_field)
                target = repo_root / relative if isinstance(relative, str) else None
                if target is None or target.is_symlink() or not target.is_file():
                    errors.append(f"validation identity file unavailable: {path_field}")
                elif sha256_bytes(target.read_bytes()) != identity.get(digest_field):
                    errors.append(f"validation identity digest drift: {path_field}")
            skill = identity.get("skill")
            skill_path = skill.get("path") if isinstance(skill, Mapping) else None
            skill_digest = skill.get("sha256") if isinstance(skill, Mapping) else None
            skill_target = (
                repo_root / skill_path if isinstance(skill_path, str) else None
            )
            if (
                skill_target is None
                or skill_target.is_symlink()
                or not skill_target.is_file()
            ):
                errors.append("validation identity file unavailable: skill")
            elif sha256_bytes(skill_target.read_bytes()) != skill_digest:
                errors.append("validation identity digest drift: skill")

    canonical: dict[str, Any] | None = None
    if (
        type(schema_version) is int
        and isinstance(profile, str)
        and identity is not None
    ):
        canonical = {
            "base_sha": base_sha,
            "profile": profile,
            "schema_version": schema_version,
            "runner_identity": identity,
            "exit_code": 0 if status == "pass" else 1,
            "result_locator": locator,
            "result_sha256": result_digest,
        }
        if canonical != dict(validation_facts):
            errors.append("validation facts do not match current canonical result")
    if errors:
        return None, list(dict.fromkeys(errors))
    return canonical, []


def _validate_validation_runner_identity(
    value: Any, violations: list[str]
) -> Mapping[str, Any] | None:
    identity = _exact_fields(
        value,
        "validation_facts.runner_identity",
        VALIDATION_RUNNER_FIELDS,
        frozenset(VALIDATION_RUNNER_FIELDS),
        violations,
    )
    if identity is None:
        return None
    _require_string(
        identity.get("path"), "validation_facts.runner_identity.path", violations
    )
    for field in (
        "sha256",
        "profile_spec_sha256",
        "rules_sha256",
        "workflow_validation_sha256",
    ):
        _require_sha_length(
            identity.get(field),
            f"validation_facts.runner_identity.{field}",
            64,
            violations,
        )
    for field in ("profile_spec_path", "rules_path", "workflow_validation_path"):
        if not _is_relative_repo_path(identity.get(field)):
            violations.append(
                f"validation_facts.runner_identity.{field} must be repository-relative"
            )
    skill = _exact_fields(
        identity.get("skill"),
        "validation_facts.runner_identity.skill",
        WORKFLOW_SKILL_FIELDS,
        frozenset(WORKFLOW_SKILL_FIELDS),
        violations,
    )
    if skill is not None:
        _require_string(
            skill.get("path"), "validation_facts.runner_identity.skill.path", violations
        )
        _require_sha_length(
            skill.get("sha256"),
            "validation_facts.runner_identity.skill.sha256",
            64,
            violations,
        )
    return identity


def validate_against_snapshot(
    handoff: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    handoff_digest: str | None = None,
    current_acceptance_criteria_ids: Sequence[str] | None = None,
    current_validation_facts: Mapping[str, Any] | None = None,
    current_workflow_identity: Mapping[str, Any] | None = None,
    current_source_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare trusted handoff candidates with freshly collected current facts."""
    repository = snapshot.get("repository")
    errors = validate_handoff_structure(
        handoff,
        expected_repository=repository if isinstance(repository, str) else None,
    )
    observed = snapshot.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    issue = observed.get("issue")
    issue = issue if isinstance(issue, Mapping) else {}
    pr = observed.get("pr")
    pr = pr if isinstance(pr, Mapping) else {}
    diff = observed.get("effective_diff")
    diff = diff if isinstance(diff, Mapping) else {}
    source = handoff.get("source_identity")
    source_repository = (
        source.get("repository") if isinstance(source, Mapping) else None
    )
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
    elif list(current_acceptance_criteria_ids) != handoff.get(
        "acceptance_criteria_ids"
    ):
        errors.append("TASK_SPEC_DRIFT: acceptance_criteria_ids")

    current_checks = _current_raw_check_facts(snapshot)
    if current_checks is None or current_checks != handoff.get("raw_check_facts"):
        errors.append("CHECKS_DRIFT: raw_check_facts")

    validation = handoff.get("validation_facts")
    if current_validation_facts is None:
        errors.append("VALIDATION_DRIFT: current validation facts unavailable")
    else:
        if not isinstance(validation, Mapping) or dict(validation) != dict(
            current_validation_facts
        ):
            errors.append("VALIDATION_DRIFT: validation_facts")
        validation_base = current_validation_facts.get("base_sha")
        expected_base = pr.get("base_sha")
        if not is_sha(validation_base):
            errors.append("VALIDATION_DRIFT: validation base identity unavailable")
        elif not is_sha(expected_base):
            errors.append("VALIDATION_DRIFT: reviewed base identity unavailable")
        elif validation_base != expected_base:
            errors.append("VALIDATION_DRIFT: validation base identity mismatch")

    if current_workflow_identity is None:
        execution_context = snapshot.get("execution_context")
        execution_context = (
            execution_context if isinstance(execution_context, Mapping) else {}
        )
        candidate = execution_context.get("workflow_identity")
        current_workflow_identity = (
            candidate if isinstance(candidate, Mapping) else None
        )
    workflow = handoff.get("workflow_identity")
    if current_workflow_identity is None:
        errors.append("WORKFLOW_RULE_DRIFT: current workflow identity unavailable")
    elif not isinstance(workflow, Mapping) or dict(workflow) != dict(
        current_workflow_identity
    ):
        errors.append("WORKFLOW_RULE_DRIFT: workflow_identity")

    if current_source_identity is None:
        try:
            current_source_identity = source_identity_for_snapshot(snapshot)
        except ReviewFactHandoffError:
            current_source_identity = None
    if current_source_identity is None:
        errors.append("HANDOFF_SCHEMA_DRIFT: current source identity unavailable")
    elif not isinstance(source, Mapping) or dict(source) != dict(
        current_source_identity
    ):
        errors.append("HANDOFF_SCHEMA_DRIFT: source_identity")

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
            "source_identity",
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
    source = dict(source_identity or source_identity_for_snapshot(snapshot))
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


def write_handoff(
    repo_root: Path, handoff: Mapping[str, Any], *, filename: str
) -> Path:
    """Write a validated handoff to the exact ignored evidence root."""
    violations = validate_handoff_structure(handoff)
    if violations:
        raise ReviewFactHandoffError("; ".join(violations))
    if (
        not filename
        or Path(filename).name != filename
        or not filename.endswith(".json")
    ):
        raise ReviewFactHandoffError("handoff filename must be a simple .json filename")
    root = repo_root / HANDOFF_ROOT
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    atomic_write_json(path, dict(handoff))
    return path
