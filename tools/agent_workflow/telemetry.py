#!/usr/bin/env python3
"""Local, append-only telemetry for repository Task workflows."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import statistics
import sys
import tempfile
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1
MODES: Final = frozenset({"baseline-only", "spot-check"})
PHASES: Final = frozenset(
    {
        "task-specification",
        "task-delivery",
        "task-pr-review",
        "manual-merge",
        "task-closeout",
        "feature-completion-audit",
    }
)
EVENT_TYPES: Final = frozenset(
    {
        "phase-summary",
        "interruption",
        "rework",
        "review-run",
        "manual-merge",
        "usage-patch",
    }
)
USAGE_SOURCES: Final = frozenset(
    {"runtime-exact", "client-export", "estimated-external", "unavailable"}
)
EXACT_USAGE_SOURCES: Final = frozenset({"runtime-exact", "client-export"})
CONTEXT_CATEGORIES: Final = frozenset(
    {
        "task_and_comments",
        "governance",
        "skills_and_policies",
        "templates_and_workflows",
        "source",
        "tests",
        "documentation",
        "pr_diff_and_commits",
        "github_facts",
        "validation_output",
        "previous_handoff",
        "other",
    }
)
CONTEXT_METRICS: Final = frozenset(
    {"files_read", "bytes_read", "lines_read", "repeated_bytes_estimate"}
)
RETRY_CATEGORIES: Final = frozenset(
    {
        "sandbox-permission",
        "credential-session",
        "filesystem-isolation",
        "real-validation-failure",
        "remote-failure",
        "other",
    }
)
COMMAND_CATEGORIES: Final = frozenset(
    {
        "git-read",
        "git-write-authorized",
        "github-read",
        "github-write-authorized",
        "validator",
        "test",
        "lint",
        "format",
        "type-check",
        "telemetry",
        "other",
    }
)
SEVERITIES: Final = frozenset({"blocking", "high", "medium", "low", "nit"})
ALLOWED_RECORD_KEYS: Final = frozenset(
    {
        "schema_version",
        "event_type",
        "recorded_at",
        "identity",
        "usage",
        "context",
        "operations",
        "report",
        "rework",
        "outcome",
        "limitations",
    }
)
SENSITIVE_KEYS: Final = frozenset(
    {
        "prompt",
        "raw_prompt",
        "assistant_response",
        "raw_response",
        "transcript",
        "raw_transcript",
        "stdout",
        "stderr",
        "command_output",
        "raw_command_output",
        "file_content",
        "file_contents",
        "source_code",
        "private_reasoning",
        "chain_of_thought",
        "environment",
        "environment_variables",
        "env",
        "headers",
        "authorization",
        "cookie",
        "password",
        "secret",
        "api_key",
        "api_token",
        "auth_token",
        "bearer_token",
        "token",
        "github_token",
        "access_token",
        "refresh_token",
        "private_key",
    }
)
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SAFE_RUN_ID = re.compile(r"^tw-[1-9][0-9]*-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")
SAFE_EVENT_ID = re.compile(r"^ev-[0-9]{6}-[0-9a-f]{8}$")
REPOSITORY_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
SENSITIVE_VALUE_PATTERNS: Final = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class TelemetryError(RuntimeError):
    """Expected user-facing telemetry error."""


@dataclass(frozen=True)
class Config:
    path: Path
    output_dir: Path
    default_mode: str
    allow_usage_patch: bool


@dataclass(frozen=True)
class RunPaths:
    output_dir: Path
    active_file: Path
    run_dir: Path
    manifest_file: Path
    events_file: Path
    summary_file: Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TelemetryError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TelemetryError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _print_json(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TelemetryError(f"required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TelemetryError(f"invalid JSON in {path}: {exc}") from exc


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TelemetryError(
                f"invalid JSONL event at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise TelemetryError(f"event at {path}:{line_number} is not an object")
        events.append(value)
    return events


def _normalize_relative(path: Path) -> str:
    return path.as_posix().lstrip("./")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _gitignore_patterns(repo_root: Path) -> set[str]:
    ignore_file = repo_root / ".gitignore"
    if not ignore_file.exists():
        return set()
    patterns: set[str] = set()
    for raw_line in ignore_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.add(line.lstrip("./"))
    return patterns


def _require_exact_ignore(repo_root: Path, path: Path, *, directory: bool) -> None:
    resolved = path.resolve()
    root = repo_root.resolve()
    if not _is_within(resolved, root):
        return
    relative = _normalize_relative(resolved.relative_to(root))
    candidates = {relative}
    if directory:
        candidates.add(f"{relative.rstrip('/')}/")
    patterns = _gitignore_patterns(root)
    if not candidates.intersection(patterns):
        kind = "directory" if directory else "file"
        raise TelemetryError(
            f"local telemetry {kind} is not covered by an exact .gitignore rule: "
            f"{relative}"
        )


def _load_config(repo_root: Path, config_arg: str) -> Config:
    config_path = Path(config_arg)
    if not config_path.is_absolute():
        config_path = repo_root / config_path
    config_path = config_path.resolve()
    if not config_path.exists():
        raise TelemetryError("local telemetry config is missing")
    _require_exact_ignore(repo_root, config_path, directory=False)
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TelemetryError(f"invalid telemetry config: {exc}") from exc
    allowed = {
        "schema_version",
        "output_dir",
        "default_mode",
        "store_raw_transcript",
        "store_command_output",
        "store_file_contents",
        "allow_usage_patch",
    }
    unknown = set(data) - allowed
    if unknown:
        raise TelemetryError(f"unknown telemetry config keys: {sorted(unknown)}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise TelemetryError("unsupported telemetry config schema_version")
    default_mode = data.get("default_mode")
    if default_mode not in MODES:
        raise TelemetryError("default_mode must be baseline-only or spot-check")
    for key in ("store_raw_transcript", "store_command_output", "store_file_contents"):
        if data.get(key) is not False:
            raise TelemetryError(f"{key} must remain false in schema version 1")
    allow_usage_patch = data.get("allow_usage_patch")
    if not isinstance(allow_usage_patch, bool):
        raise TelemetryError("allow_usage_patch must be a boolean")
    output_value = data.get("output_dir")
    if not isinstance(output_value, str) or not output_value.strip():
        raise TelemetryError("output_dir must be a non-empty path string")
    if "://" in output_value:
        raise TelemetryError("network output endpoints are not allowed")
    output_dir = Path(output_value)
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    output_dir = output_dir.resolve()
    if output_dir == repo_root.resolve():
        raise TelemetryError("output_dir cannot be the repository root")
    _require_exact_ignore(repo_root, output_dir, directory=True)
    return Config(
        path=config_path,
        output_dir=output_dir,
        default_mode=default_mode,
        allow_usage_patch=allow_usage_patch,
    )


def _run_paths(config: Config, task: int, run_id: str) -> RunPaths:
    return RunPaths(
        output_dir=config.output_dir,
        active_file=config.output_dir / "active" / f"task-{task}.json",
        run_dir=config.output_dir / "runs" / run_id,
        manifest_file=config.output_dir / "runs" / run_id / "manifest.json",
        events_file=config.output_dir / "runs" / run_id / "events.jsonl",
        summary_file=config.output_dir / "runs" / run_id / "summary.json",
    )


def _active_run(config: Config, task: int) -> tuple[str, RunPaths]:
    active_file = config.output_dir / "active" / f"task-{task}.json"
    active = _read_json(active_file)
    if not isinstance(active, dict) or not isinstance(active.get("run_id"), str):
        raise TelemetryError(f"invalid active pointer: {active_file}")
    run_id = active["run_id"]
    return run_id, _run_paths(config, task, run_id)


def _validate_slug(value: str, field: str) -> str:
    if not SAFE_SLUG.fullmatch(value):
        raise TelemetryError(f"{field} must be a lowercase slug")
    return value


def _validate_sha(value: Any, field: str) -> None:
    if value is not None and (
        not isinstance(value, str) or not SHA_PATTERN.fullmatch(value)
    ):
        raise TelemetryError(f"{field} must be a Git SHA or null")


def _reject_sensitive(value: Any, path: str = "data") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in SENSITIVE_KEYS:
                raise TelemetryError(
                    f"sensitive or raw-content field is forbidden: {path}.{key}"
                )
            _reject_sensitive(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_sensitive(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        if WINDOWS_ABSOLUTE_PATH.match(value):
            raise TelemetryError(
                f"machine-sensitive absolute path is forbidden: {path}"
            )
        if value.startswith("/") and not value.startswith("//"):
            raise TelemetryError(f"absolute path is forbidden: {path}")
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
            raise TelemetryError(f"sensitive value is forbidden: {path}")


def _nonnegative_int_or_null(value: Any, field: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise TelemetryError(f"{field} must be a non-negative integer or null")


def _validate_usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("usage must be an object")
    allowed = {
        "source",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "model",
    }
    unknown = set(value) - allowed
    if unknown:
        raise TelemetryError(f"unknown usage fields: {sorted(unknown)}")
    source = value.get("source")
    if source not in USAGE_SOURCES:
        raise TelemetryError(f"usage.source must be one of {sorted(USAGE_SOURCES)}")
    normalized = dict(value)
    for field in (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    ):
        _nonnegative_int_or_null(normalized.get(field), f"usage.{field}")
        normalized.setdefault(field, None)
    model = normalized.get("model")
    if model is not None and (not isinstance(model, str) or len(model) > 128):
        raise TelemetryError("usage.model must be a short string or null")
    normalized.setdefault("model", None)
    if source == "unavailable":
        token_fields = (field for field in allowed if field.endswith("_tokens"))
        if any(normalized[field] is not None for field in token_fields):
            raise TelemetryError("unavailable usage must use null token fields")
    input_tokens = normalized["input_tokens"]
    output_tokens = normalized["output_tokens"]
    total_tokens = normalized["total_tokens"]
    if input_tokens is not None and output_tokens is not None:
        computed = input_tokens + output_tokens
        if total_tokens is None:
            normalized["total_tokens"] = computed
        elif total_tokens != computed:
            raise TelemetryError(
                "usage.total_tokens must equal input_tokens + output_tokens"
            )
    return normalized


def _validate_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("context must be an object")
    unknown_categories = set(value) - CONTEXT_CATEGORIES
    if unknown_categories:
        raise TelemetryError(
            f"unknown context categories: {sorted(unknown_categories)}"
        )
    normalized: dict[str, Any] = {}
    for category, metrics in value.items():
        if not isinstance(metrics, dict):
            raise TelemetryError(f"context.{category} must be an object")
        unknown_metrics = set(metrics) - CONTEXT_METRICS
        if unknown_metrics:
            raise TelemetryError(
                f"unknown context metrics for {category}: {sorted(unknown_metrics)}"
            )
        item: dict[str, Any] = {}
        for metric in CONTEXT_METRICS:
            metric_value = metrics.get(metric)
            _nonnegative_int_or_null(metric_value, f"context.{category}.{metric}")
            item[metric] = metric_value
        normalized[category] = item
    return normalized


def _validate_operations(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("operations must be an object")
    counters = {
        "tool_calls",
        "github_queries",
        "git_commands",
        "validation_commands",
        "sandbox_attempts",
        "elevated_attempts",
        "retries",
    }
    allowed = counters | {"retry_categories", "command_categories"}
    unknown = set(value) - allowed
    if unknown:
        raise TelemetryError(f"unknown operations fields: {sorted(unknown)}")
    normalized: dict[str, Any] = {}
    for field in counters:
        field_value = value.get(field)
        _nonnegative_int_or_null(field_value, f"operations.{field}")
        normalized[field] = field_value
    retry_categories = value.get("retry_categories", {})
    if not isinstance(retry_categories, dict):
        raise TelemetryError("operations.retry_categories must be an object")
    if set(retry_categories) - RETRY_CATEGORIES:
        raise TelemetryError("operations.retry_categories contains unsupported values")
    normalized_retries: dict[str, int] = {}
    for category in sorted(RETRY_CATEGORIES):
        count = retry_categories.get(category, 0)
        _nonnegative_int_or_null(count, f"operations.retry_categories.{category}")
        if count is None:
            raise TelemetryError("retry category counts cannot be null")
        normalized_retries[category] = count
    normalized["retry_categories"] = normalized_retries
    command_categories = value.get("command_categories", {})
    if not isinstance(command_categories, dict):
        raise TelemetryError("operations.command_categories must be an object")
    if set(command_categories) - COMMAND_CATEGORIES:
        raise TelemetryError(
            "operations.command_categories contains unsupported values"
        )
    normalized_commands: dict[str, int] = {}
    for category in sorted(COMMAND_CATEGORIES):
        count = command_categories.get(category, 0)
        _nonnegative_int_or_null(count, f"operations.command_categories.{category}")
        if count is None:
            raise TelemetryError("command category counts cannot be null")
        normalized_commands[category] = count
    normalized["command_categories"] = normalized_commands
    return normalized


def _validate_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("report must be an object")
    allowed = {
        "report_characters",
        "report_lines",
        "report_estimated_tokens",
        "report_estimation_method",
        "copied_to_next_phase",
    }
    unknown = set(value) - allowed
    if unknown:
        raise TelemetryError(f"unknown report fields: {sorted(unknown)}")
    normalized = dict(value)
    for field in ("report_characters", "report_lines", "report_estimated_tokens"):
        field_value = normalized.get(field)
        _nonnegative_int_or_null(field_value, f"report.{field}")
        normalized.setdefault(field, None)
    method = normalized.get("report_estimation_method")
    if method is not None and (not isinstance(method, str) or len(method) > 128):
        raise TelemetryError("report_estimation_method must be a short string or null")
    normalized.setdefault("report_estimation_method", None)
    copied = normalized.get("copied_to_next_phase")
    if copied is not None and not isinstance(copied, bool):
        raise TelemetryError("copied_to_next_phase must be boolean or null")
    normalized.setdefault("copied_to_next_phase", None)
    return normalized


def _validate_rework(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("rework must be an object")
    counters = {
        "commits_added_after_first_handoff",
        "head_sha_changes",
        "independent_review_runs",
        "review_invalidations",
        "maintainer_decisions",
        "interruptions",
    }
    allowed = counters | {"findings_by_severity"}
    unknown = set(value) - allowed
    if unknown:
        raise TelemetryError(f"unknown rework fields: {sorted(unknown)}")
    normalized: dict[str, Any] = {}
    for field in counters:
        field_value = value.get(field)
        _nonnegative_int_or_null(field_value, f"rework.{field}")
        normalized[field] = field_value
    findings = value.get("findings_by_severity", {})
    if not isinstance(findings, dict) or set(findings) - SEVERITIES:
        raise TelemetryError("findings_by_severity contains unsupported values")
    normalized_findings: dict[str, int] = {}
    for severity in sorted(SEVERITIES):
        count = findings.get(severity, 0)
        _nonnegative_int_or_null(count, f"rework.findings_by_severity.{severity}")
        if count is None:
            raise TelemetryError("finding counts cannot be null")
        normalized_findings[severity] = count
    normalized["findings_by_severity"] = normalized_findings
    return normalized


def _validate_outcome(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("outcome must be an object")
    allowed = {
        "phase_result",
        "workflow_result",
        "review_verdict",
        "feature_audit_verdict",
        "validation_passed",
        "telemetry_complete",
    }
    unknown = set(value) - allowed
    if unknown:
        raise TelemetryError(f"unknown outcome fields: {sorted(unknown)}")
    normalized = dict(value)
    for field in (
        "phase_result",
        "workflow_result",
        "review_verdict",
        "feature_audit_verdict",
    ):
        field_value = normalized.get(field)
        if field_value is not None and (
            not isinstance(field_value, str) or len(field_value) > 256
        ):
            raise TelemetryError(f"outcome.{field} must be a short string or null")
        normalized.setdefault(field, None)
    for field in ("validation_passed", "telemetry_complete"):
        field_value = normalized.get(field)
        if field_value is not None and not isinstance(field_value, bool):
            raise TelemetryError(f"outcome.{field} must be boolean or null")
        normalized.setdefault(field, None)
    return normalized


def _validate_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("identity must be an object")
    allowed = {
        "task_canonical_title",
        "pr_number",
        "feature_number",
        "base_sha",
        "head_sha",
        "workflow_main_sha",
        "model",
        "changed_files_count",
        "changed_lines",
        "acceptance_criteria_count",
    }
    unknown = set(value) - allowed
    if unknown:
        raise TelemetryError(f"unknown identity fields: {sorted(unknown)}")
    normalized = dict(value)
    for field in (
        "pr_number",
        "feature_number",
        "changed_files_count",
        "changed_lines",
        "acceptance_criteria_count",
    ):
        field_value = normalized.get(field)
        _nonnegative_int_or_null(field_value, f"identity.{field}")
        normalized.setdefault(field, None)
    for field in ("base_sha", "head_sha", "workflow_main_sha"):
        _validate_sha(normalized.get(field), f"identity.{field}")
        normalized.setdefault(field, None)
    for field in ("task_canonical_title", "model"):
        field_value = normalized.get(field)
        if field_value is not None and (
            not isinstance(field_value, str) or len(field_value) > 512
        ):
            raise TelemetryError(f"identity.{field} must be a short string or null")
        normalized.setdefault(field, None)
    return normalized


def _validate_record_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("phase data must be a JSON object")
    _reject_sensitive(value)
    unknown = set(value) - ALLOWED_RECORD_KEYS
    if unknown:
        raise TelemetryError(f"unknown phase data fields: {sorted(unknown)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise TelemetryError("phase data schema_version must be 1")
    event_type = value.get("event_type", "phase-summary")
    if event_type not in EVENT_TYPES - {"usage-patch"}:
        raise TelemetryError("unsupported record event_type")
    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
    }
    recorded_at = value.get("recorded_at")
    if recorded_at is not None:
        if not isinstance(recorded_at, str):
            raise TelemetryError("recorded_at must be an ISO timestamp string or null")
        _parse_timestamp(recorded_at, "recorded_at")
    normalized["recorded_at"] = recorded_at
    if "identity" in value:
        normalized["identity"] = _validate_identity(value["identity"])
    if "usage" in value:
        normalized["usage"] = _validate_usage(value["usage"])
    if "context" in value:
        normalized["context"] = _validate_context(value["context"])
    if "operations" in value:
        normalized["operations"] = _validate_operations(value["operations"])
    if "report" in value:
        normalized["report"] = _validate_report(value["report"])
    if "rework" in value:
        normalized["rework"] = _validate_rework(value["rework"])
    if "outcome" in value:
        normalized["outcome"] = _validate_outcome(value["outcome"])
    limitations = value.get("limitations", [])
    if not isinstance(limitations, list) or not all(
        isinstance(item, str) and len(item) <= 512 for item in limitations
    ):
        raise TelemetryError("limitations must be a list of short strings")
    normalized["limitations"] = limitations
    return normalized


def _usage_priority(source: str) -> int:
    return {
        "unavailable": 0,
        "estimated-external": 1,
        "client-export": 2,
        "runtime-exact": 3,
    }[source]


def _events_with_patches(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {
        event.get("event_id"): dict(event)
        for event in events
        if event.get("event_type") != "usage-patch"
    }
    order = [
        event.get("event_id")
        for event in events
        if event.get("event_type") != "usage-patch"
    ]
    for patch in events:
        if patch.get("event_type") != "usage-patch":
            continue
        target = patch.get("target_event_id")
        if target in by_id:
            current = by_id[target].get("usage")
            incoming = patch.get("usage")
            if isinstance(incoming, dict) and (
                not isinstance(current, dict)
                or _usage_priority(incoming["source"])
                >= _usage_priority(current["source"])
            ):
                by_id[target]["usage"] = incoming
    return [by_id[event_id] for event_id in order if event_id in by_id]


def _sum_optional(values: Iterable[Any]) -> int | None:
    present = [
        value
        for value in values
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    return sum(present) if present else None


def _aggregate_events(
    manifest: Mapping[str, Any],
    events: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    effective = _events_with_patches(events)
    phases: dict[str, dict[str, Any]] = {}
    for phase in sorted(PHASES):
        phase_events = [event for event in effective if event.get("phase") == phase]
        if not phase_events:
            continue
        usages = [
            event.get("usage")
            for event in phase_events
            if isinstance(event.get("usage"), dict)
        ]
        phase_usage: dict[str, Any] = {
            "sources": sorted({usage["source"] for usage in usages}),
            "input_tokens": _sum_optional(
                usage.get("input_tokens") for usage in usages
            ),
            "cached_input_tokens": _sum_optional(
                usage.get("cached_input_tokens") for usage in usages
            ),
            "output_tokens": _sum_optional(
                usage.get("output_tokens") for usage in usages
            ),
            "reasoning_tokens": _sum_optional(
                usage.get("reasoning_tokens") for usage in usages
            ),
            "total_tokens": _sum_optional(
                usage.get("total_tokens") for usage in usages
            ),
        }
        context: dict[str, dict[str, int | None]] = {}
        for category in sorted(CONTEXT_CATEGORIES):
            category_values = [
                event["context"][category]
                for event in phase_events
                if isinstance(event.get("context"), dict)
                and isinstance(event["context"].get(category), dict)
            ]
            if category_values:
                context[category] = {
                    metric: _sum_optional(item.get(metric) for item in category_values)
                    for metric in sorted(CONTEXT_METRICS)
                }
        operations_fields = (
            "tool_calls",
            "github_queries",
            "git_commands",
            "validation_commands",
            "sandbox_attempts",
            "elevated_attempts",
            "retries",
        )
        operations = {
            field: _sum_optional(
                event.get("operations", {}).get(field)
                for event in phase_events
                if isinstance(event.get("operations"), dict)
            )
            for field in operations_fields
        }
        for aggregate_field in ("retry_categories", "command_categories"):
            category_names = (
                RETRY_CATEGORIES
                if aggregate_field == "retry_categories"
                else COMMAND_CATEGORIES
            )
            operations[aggregate_field] = {
                category: _sum_optional(
                    event.get("operations", {}).get(aggregate_field, {}).get(category)
                    for event in phase_events
                    if isinstance(event.get("operations"), dict)
                )
                for category in sorted(category_names)
            }
        report_fields = (
            "report_characters",
            "report_lines",
            "report_estimated_tokens",
        )
        report = {
            field: _sum_optional(
                event.get("report", {}).get(field)
                for event in phase_events
                if isinstance(event.get("report"), dict)
            )
            for field in report_fields
        }
        report["estimation_methods"] = sorted(
            {
                event.get("report", {}).get("report_estimation_method")
                for event in phase_events
                if isinstance(event.get("report"), dict)
                and isinstance(
                    event.get("report", {}).get("report_estimation_method"),
                    str,
                )
            }
        )
        report["copied_to_next_phase_count"] = sum(
            1
            for event in phase_events
            if isinstance(event.get("report"), dict)
            and event.get("report", {}).get("copied_to_next_phase") is True
        )
        rework_fields = (
            "commits_added_after_first_handoff",
            "head_sha_changes",
            "independent_review_runs",
            "review_invalidations",
            "maintainer_decisions",
            "interruptions",
        )
        rework = {
            field: _sum_optional(
                event.get("rework", {}).get(field)
                for event in phase_events
                if isinstance(event.get("rework"), dict)
            )
            for field in rework_fields
        }
        findings: dict[str, int | None] = {}
        for severity in sorted(SEVERITIES):
            findings[severity] = _sum_optional(
                event.get("rework", {}).get("findings_by_severity", {}).get(severity)
                for event in phase_events
                if isinstance(event.get("rework"), dict)
            )
        rework["findings_by_severity"] = findings
        outcome_fields = (
            "phase_result",
            "workflow_result",
            "review_verdict",
            "feature_audit_verdict",
        )
        outcomes = {
            field: sorted(
                {
                    event.get("outcome", {}).get(field)
                    for event in phase_events
                    if isinstance(event.get("outcome"), dict)
                    and isinstance(event.get("outcome", {}).get(field), str)
                }
            )
            for field in outcome_fields
        }
        for field in ("validation_passed", "telemetry_complete"):
            outcomes[field] = [
                event.get("outcome", {}).get(field)
                for event in phase_events
                if isinstance(event.get("outcome"), dict)
                and isinstance(event.get("outcome", {}).get(field), bool)
            ]
        phases[phase] = {
            "event_count": len(phase_events),
            "usage": phase_usage,
            "context": context,
            "operations": operations,
            "report": report,
            "rework": rework,
            "outcomes": outcomes,
        }
    total_usage = {
        field: _sum_optional(
            phase["usage"].get(field)
            for phase in phases.values()
            if isinstance(phase.get("usage"), dict)
        )
        for field in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        )
    }
    usage_sources = sorted(
        {
            source
            for phase in phases.values()
            for source in phase["usage"].get("sources", [])
        }
    )
    usage_coverage = {
        "events_with_usage": 0,
        "by_source": {source: 0 for source in sorted(USAGE_SOURCES)},
        "events_without_usage": 0,
    }
    for event in effective:
        usage = event.get("usage")
        if not isinstance(usage, dict):
            usage_coverage["events_without_usage"] += 1
            continue
        usage_coverage["events_with_usage"] += 1
        usage_coverage["by_source"][usage["source"]] += 1
    total_findings = {
        severity: _sum_optional(
            phase.get("rework", {}).get("findings_by_severity", {}).get(severity)
            for phase in phases.values()
        )
        for severity in sorted(SEVERITIES)
    }
    validation_values = [
        value
        for phase in phases.values()
        for value in phase.get("outcomes", {}).get("validation_passed", [])
    ]
    quality = {
        "findings_by_severity": total_findings,
        "review_invalidations": _sum_optional(
            phase.get("rework", {}).get("review_invalidations")
            for phase in phases.values()
        ),
        "maintainer_decisions": _sum_optional(
            phase.get("rework", {}).get("maintainer_decisions")
            for phase in phases.values()
        ),
        "validation_observations": validation_values,
        "validation_all_passed": (
            all(validation_values) if validation_values else None
        ),
        "review_verdicts": sorted(
            {
                verdict
                for phase in phases.values()
                for verdict in phase.get("outcomes", {}).get("review_verdict", [])
            }
        ),
        "feature_audit_verdicts": sorted(
            {
                verdict
                for phase in phases.values()
                for verdict in phase.get("outcomes", {}).get(
                    "feature_audit_verdict", []
                )
            }
        ),
        "workflow_results": sorted(
            {
                result
                for phase in phases.values()
                for result in phase.get("outcomes", {}).get("workflow_result", [])
            }
        ),
    }
    required_phases = {
        "task-delivery",
        "task-pr-review",
        "task-closeout",
    }
    if manifest.get("workflow_shape") == "task-plus-feature-audit":
        required_phases.add("feature-completion-audit")
    present_phases = set(phases)
    missing_phases = sorted(required_phases - present_phases)
    limitations = sorted(
        {
            item
            for event in effective
            for item in event.get("limitations", [])
            if isinstance(item, str)
        }
    )
    outcome_completeness = [
        event.get("outcome", {}).get("telemetry_complete")
        for event in effective
        if isinstance(event.get("outcome"), dict)
    ]
    telemetry_complete = not missing_phases and False not in outcome_completeness
    summarized_run = dict(manifest)
    for event in effective:
        identity = event.get("identity")
        if not isinstance(identity, dict):
            continue
        for field in ("pr_number", "feature_number", "base_sha", "head_sha"):
            field_value = identity.get(field)
            if field_value is not None:
                summarized_run[field] = field_value
    return {
        "schema_version": SCHEMA_VERSION,
        "run": summarized_run,
        "phases": phases,
        "total_usage": {"sources": usage_sources, **total_usage},
        "usage_coverage": usage_coverage,
        "quality": quality,
        "missing_phases": missing_phases,
        "telemetry_complete": telemetry_complete,
        "limitations": limitations,
    }


def _load_manifests(config: Config) -> list[dict[str, Any]]:
    runs_dir = config.output_dir / "runs"
    if not runs_dir.exists():
        return []
    manifests: list[dict[str, Any]] = []
    for manifest_file in sorted(runs_dir.glob("*/manifest.json")):
        value = _read_json(manifest_file)
        manifests.append(_validate_manifest(value))
    return manifests


def _active_run_by_feature(config: Config, feature: int) -> tuple[str, RunPaths] | None:
    matches: list[tuple[str, RunPaths]] = []
    active_dir = config.output_dir / "active"
    if not active_dir.exists():
        return None
    for active_file in sorted(active_dir.glob("task-*.json")):
        active = _read_json(active_file)
        if not isinstance(active, dict):
            raise TelemetryError(f"invalid active pointer: {active_file}")
        run_id = active.get("run_id")
        task = active.get("task_number")
        if not isinstance(run_id, str) or not isinstance(task, int):
            raise TelemetryError(f"invalid active pointer: {active_file}")
        paths = _run_paths(config, task, run_id)
        manifest = _validate_manifest(_read_json(paths.manifest_file))
        if manifest.get("feature_number") == feature:
            matches.append((run_id, paths))
    if len(matches) > 1:
        raise TelemetryError(
            f"Feature #{feature} is associated with multiple active telemetry runs"
        )
    return matches[0] if matches else None


def _select_active_run(
    config: Config,
    *,
    task: int | None,
    feature: int | None,
) -> tuple[str, RunPaths] | None:
    if task is not None:
        active_file = config.output_dir / "active" / f"task-{task}.json"
        if not active_file.exists():
            return None
        return _active_run(config, task)
    if feature is not None:
        return _active_run_by_feature(config, feature)
    raise TelemetryError("exactly one of --task or --feature is required")


def _locate_run(
    config: Config,
    *,
    task: int | None,
    feature: int | None,
    run_id: str | None,
) -> tuple[str, RunPaths]:
    if task is None and feature is None:
        raise TelemetryError("exactly one of --task or --feature is required")
    if task is not None and feature is not None:
        raise TelemetryError("--task and --feature are mutually exclusive")
    manifests = _load_manifests(config)
    if run_id is not None:
        candidates = [
            manifest for manifest in manifests if manifest.get("run_id") == run_id
        ]
        if len(candidates) != 1:
            raise TelemetryError(f"run does not exist: {run_id}")
        manifest = candidates[0]
        if task is not None and manifest.get("task_number") != task:
            raise TelemetryError("run does not belong to the requested Task")
        if feature is not None and manifest.get("feature_number") != feature:
            raise TelemetryError("run does not belong to the requested Feature")
        manifest_task = manifest.get("task_number")
        if not isinstance(manifest_task, int):
            raise TelemetryError("run has an invalid task_number")
        return run_id, _run_paths(config, manifest_task, run_id)
    active = _select_active_run(config, task=task, feature=feature)
    if active is not None:
        return active
    candidates = [
        manifest
        for manifest in manifests
        if (task is not None and manifest.get("task_number") == task)
        or (feature is not None and manifest.get("feature_number") == feature)
    ]
    if not candidates:
        identity = f"Task #{task}" if task is not None else f"Feature #{feature}"
        raise TelemetryError(f"no telemetry run found for {identity}")
    candidates.sort(key=lambda item: str(item.get("started_at", "")), reverse=True)
    selected = candidates[0]
    selected_id = selected.get("run_id")
    selected_task = selected.get("task_number")
    if not isinstance(selected_id, str) or not isinstance(selected_task, int):
        raise TelemetryError("latest run has invalid identity")
    return selected_id, _run_paths(config, selected_task, selected_id)


def _selector_identity(args: argparse.Namespace) -> dict[str, int | None]:
    return {"task_number": args.task, "feature_number": args.feature}


def _validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("manifest must be an object")
    required = {
        "schema_version",
        "run_id",
        "mode",
        "task_number",
        "task_canonical_title",
        "task_kind",
        "size",
        "risk_class",
        "workflow_shape",
        "repository",
        "workflow_main_sha",
        "model",
        "pr_number",
        "feature_number",
        "base_sha",
        "head_sha",
        "started_at",
        "finished_at",
        "status",
    }
    missing = required - set(value)
    unknown = set(value) - required
    if missing:
        raise TelemetryError(f"manifest is missing fields: {sorted(missing)}")
    if unknown:
        raise TelemetryError(f"manifest has unknown fields: {sorted(unknown)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise TelemetryError("unsupported manifest schema_version")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or not SAFE_RUN_ID.fullmatch(run_id):
        raise TelemetryError("manifest run_id is invalid")
    if value.get("mode") not in MODES:
        raise TelemetryError("manifest mode is invalid")
    task_number = value.get("task_number")
    if (
        isinstance(task_number, bool)
        or not isinstance(task_number, int)
        or task_number <= 0
    ):
        raise TelemetryError("manifest task_number is invalid")
    _validate_slug(str(value.get("task_kind")), "task_kind")
    if value.get("size") not in {"S", "M", "L"}:
        raise TelemetryError("manifest size is invalid")
    _validate_slug(str(value.get("risk_class")), "risk_class")
    _validate_slug(str(value.get("workflow_shape")), "workflow_shape")
    for field in ("pr_number", "feature_number"):
        field_value = value.get(field)
        if field_value is not None and (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or field_value <= 0
        ):
            raise TelemetryError(f"manifest {field} must be a positive integer or null")
    for field in ("workflow_main_sha", "base_sha", "head_sha"):
        _validate_sha(value.get(field), field)
    task_title = value.get("task_canonical_title")
    if not isinstance(task_title, str) or not task_title or len(task_title) > 512:
        raise TelemetryError("manifest task_canonical_title is invalid")
    model = value.get("model")
    if model is not None and (
        not isinstance(model, str) or not model or len(model) > 128
    ):
        raise TelemetryError("manifest model must be a short string or null")
    repository = value.get("repository")
    if (
        not isinstance(repository, str)
        or len(repository) > 256
        or not REPOSITORY_SLUG.fullmatch(repository)
    ):
        raise TelemetryError("manifest repository must be owner/repository")
    if value.get("workflow_main_sha") is None:
        raise TelemetryError("manifest workflow_main_sha is required")
    status = value.get("status")
    if status not in {"active", "completed", "cancelled", "failed"}:
        raise TelemetryError("manifest status is invalid")
    started_at = value.get("started_at")
    if not isinstance(started_at, str):
        raise TelemetryError("manifest started_at is invalid")
    started = _parse_timestamp(started_at, "manifest.started_at")
    finished_at = value.get("finished_at")
    if status == "active" and finished_at is not None:
        raise TelemetryError("active manifest cannot have finished_at")
    if status != "active" and finished_at is None:
        raise TelemetryError("finished manifest must have finished_at")
    if finished_at is not None:
        if not isinstance(finished_at, str):
            raise TelemetryError("manifest finished_at is invalid")
        if _parse_timestamp(finished_at, "manifest.finished_at") < started:
            raise TelemetryError("manifest finished_at precedes started_at")
    _reject_sensitive(value)
    return dict(value)


def _validate_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise TelemetryError("event must be an object")
    base_allowed = set(ALLOWED_RECORD_KEYS) | {"event_id", "phase", "target_event_id"}
    unknown = set(event) - base_allowed
    if unknown:
        raise TelemetryError(f"event has unknown fields: {sorted(unknown)}")
    required = {"schema_version", "event_id", "event_type", "phase", "recorded_at"}
    missing = required - set(event)
    if missing:
        raise TelemetryError(f"event is missing fields: {sorted(missing)}")
    if event.get("schema_version") != SCHEMA_VERSION:
        raise TelemetryError("unsupported event schema_version")
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not SAFE_EVENT_ID.fullmatch(event_id):
        raise TelemetryError("event_id is invalid")
    event_type = event.get("event_type")
    if event_type not in EVENT_TYPES:
        raise TelemetryError("event_type is invalid")
    if event.get("phase") not in PHASES:
        raise TelemetryError("event phase is invalid")
    recorded_at = event.get("recorded_at")
    if not isinstance(recorded_at, str):
        raise TelemetryError("event recorded_at is invalid")
    _parse_timestamp(recorded_at, "event.recorded_at")
    _reject_sensitive(event)
    if event_type == "usage-patch":
        patch_allowed = {
            "schema_version",
            "event_id",
            "event_type",
            "phase",
            "target_event_id",
            "recorded_at",
            "usage",
        }
        if set(event) - patch_allowed:
            raise TelemetryError("usage patch contains unsupported fields")
        target = event.get("target_event_id")
        if not isinstance(target, str) or not SAFE_EVENT_ID.fullmatch(target):
            raise TelemetryError("usage patch target_event_id is invalid")
        if "usage" not in event:
            raise TelemetryError("usage patch must contain usage")
        _validate_usage(event["usage"])
        return dict(event)
    if "target_event_id" in event:
        raise TelemetryError("target_event_id is allowed only for usage patches")
    if "identity" in event:
        _validate_identity(event["identity"])
    if "usage" in event:
        _validate_usage(event["usage"])
    if "context" in event:
        _validate_context(event["context"])
    if "operations" in event:
        _validate_operations(event["operations"])
    if "report" in event:
        _validate_report(event["report"])
    if "rework" in event:
        _validate_rework(event["rework"])
    if "outcome" in event:
        _validate_outcome(event["outcome"])
    limitations = event.get("limitations", [])
    if not isinstance(limitations, list) or not all(
        isinstance(item, str) and len(item) <= 512 for item in limitations
    ):
        raise TelemetryError("event limitations must be a list of short strings")
    return dict(event)


def _validate_event_identity_against_manifest(
    event: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    identity = event.get("identity")
    if not isinstance(identity, dict):
        return

    event_title = identity.get("task_canonical_title")
    if event_title is not None and event_title != manifest["task_canonical_title"]:
        raise TelemetryError("event Task title conflicts with manifest")

    event_workflow_sha = identity.get("workflow_main_sha")
    if (
        event_workflow_sha is not None
        and event_workflow_sha != manifest["workflow_main_sha"]
    ):
        raise TelemetryError("event workflow_main_sha conflicts with manifest")

    event_feature = identity.get("feature_number")
    manifest_feature = manifest.get("feature_number")
    if (
        isinstance(event_feature, int)
        and manifest_feature is not None
        and event_feature != manifest_feature
    ):
        raise TelemetryError("event Feature number conflicts with manifest")


def _validate_run(
    paths: RunPaths,
    *,
    check_stored_summary: bool = True,
) -> dict[str, Any]:
    manifest = _validate_manifest(_read_json(paths.manifest_file))
    events = _read_events(paths.events_file)
    seen_ids: set[str] = set()
    previous_time: datetime | None = None
    primary_phases: set[str] = set()
    event_ids: set[str] = set()
    started = _parse_timestamp(manifest["started_at"], "manifest.started_at")
    finished = (
        _parse_timestamp(manifest["finished_at"], "manifest.finished_at")
        if manifest["finished_at"] is not None
        else None
    )
    pr_numbers: set[int] = set()
    feature_numbers: set[int] = set()
    for event in events:
        validated = _validate_event(event)
        _validate_event_identity_against_manifest(validated, manifest)
        identity = validated.get("identity")
        if isinstance(identity, dict):
            event_pr = identity.get("pr_number")
            if isinstance(event_pr, int):
                pr_numbers.add(event_pr)
            event_feature = identity.get("feature_number")
            if isinstance(event_feature, int):
                feature_numbers.add(event_feature)
        event_id = validated["event_id"]
        if event_id in seen_ids:
            raise TelemetryError("event IDs must be unique strings")
        seen_ids.add(event_id)
        parsed_time = _parse_timestamp(validated["recorded_at"], "event.recorded_at")
        if parsed_time < started:
            raise TelemetryError("event timestamp precedes run start")
        if finished is not None and parsed_time > finished:
            raise TelemetryError("event timestamp follows run finish")
        if previous_time is not None and parsed_time < previous_time:
            raise TelemetryError("event timestamps must be non-decreasing")
        previous_time = parsed_time
        if validated["event_type"] == "phase-summary":
            phase = validated["phase"]
            if phase in primary_phases:
                raise TelemetryError(f"duplicate primary phase summary: {phase}")
            primary_phases.add(phase)
        if validated["event_type"] != "usage-patch":
            event_ids.add(event_id)
    if len(pr_numbers) > 1:
        raise TelemetryError("events contain conflicting PR numbers")
    if len(feature_numbers) > 1:
        raise TelemetryError("events contain conflicting Feature numbers")
    if feature_numbers and manifest["feature_number"] not in {None, *feature_numbers}:
        raise TelemetryError("event Feature number conflicts with manifest")
    for event in events:
        if (
            event.get("event_type") == "usage-patch"
            and event.get("target_event_id") not in event_ids
        ):
            raise TelemetryError("usage patch targets an unknown event")
    computed = _aggregate_events(manifest, events)
    if check_stored_summary and paths.summary_file.exists():
        stored = _read_json(paths.summary_file)
        if stored != computed:
            raise TelemetryError(
                "stored summary does not match deterministic aggregation"
            )
    return {
        "valid": True,
        "run_id": manifest["run_id"],
        "task_number": manifest["task_number"],
        "event_count": len(events),
        "telemetry_complete": computed["telemetry_complete"],
        "missing_phases": computed["missing_phases"],
    }


def _require_active_manifest(paths: RunPaths) -> dict[str, Any]:
    manifest = _validate_manifest(_read_json(paths.manifest_file))
    if manifest["status"] != "active":
        raise TelemetryError("telemetry run is not active")
    return manifest


def _ensure_append_timestamp(
    events: Sequence[dict[str, Any]],
    recorded_at: str,
) -> None:
    if not events:
        return
    last = events[-1].get("recorded_at")
    if not isinstance(last, str):
        raise TelemetryError("last event recorded_at is invalid")
    if _parse_timestamp(recorded_at, "event.recorded_at") < _parse_timestamp(
        last, "last event.recorded_at"
    ):
        raise TelemetryError("new event timestamp precedes the latest event")


def _comparable_summaries(
    config: Config,
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    current_run = current["run"]
    keys = (
        "task_kind",
        "size",
        "risk_class",
        "workflow_shape",
        "model",
        "workflow_main_sha",
    )
    comparable: list[dict[str, Any]] = []
    for manifest in _load_manifests(config):
        if manifest.get("run_id") == current_run.get("run_id"):
            continue
        if manifest.get("status") != "completed":
            continue
        if any(manifest.get(key) != current_run.get(key) for key in keys):
            continue
        run_id = manifest.get("run_id")
        task = manifest.get("task_number")
        if not isinstance(run_id, str) or not isinstance(task, int):
            continue
        summary_path = _run_paths(config, task, run_id).summary_file
        if not summary_path.exists():
            continue
        summary = _read_json(summary_path)
        if isinstance(summary, dict):
            comparable.append(summary)
    return comparable


def _precision_group(summary: Mapping[str, Any]) -> str:
    sources = set(summary.get("total_usage", {}).get("sources", []))
    measured = sources - {"unavailable"}
    if measured and measured <= EXACT_USAGE_SOURCES:
        return "exact"
    if measured == {"estimated-external"}:
        return "estimated"
    if not measured:
        return "unavailable"
    return "mixed"


def _spot_check(config: Config, summary: Mapping[str, Any]) -> dict[str, Any]:
    comparable = _comparable_summaries(config, summary)
    current_group = _precision_group(summary)
    same_precision = [
        item for item in comparable if _precision_group(item) == current_group
    ]
    result: dict[str, Any] = {
        "comparable_sample_count": len(comparable),
        "same_precision_sample_count": len(same_precision),
        "precision_group": current_group,
        "sample_sufficient": len(same_precision) >= 3,
        "historical_total_median": None,
        "historical_total_range": None,
        "current_total_delta": None,
        "anomaly_flags": [],
        "limitations": [],
    }
    if len(same_precision) < 3:
        result["limitations"].append(
            "fewer than three comparable completed runs with the same usage precision"
        )
    current_total = summary.get("total_usage", {}).get("total_tokens")
    historical_totals = [
        item.get("total_usage", {}).get("total_tokens")
        for item in same_precision
        if isinstance(item.get("total_usage", {}).get("total_tokens"), int)
    ]
    flags: set[str] = set()
    if len(historical_totals) >= 3 and isinstance(current_total, int):
        median = statistics.median(historical_totals)
        result["historical_total_median"] = median
        result["historical_total_range"] = [
            min(historical_totals),
            max(historical_totals),
        ]
        result["current_total_delta"] = current_total - median
        if median > 0 and current_total > median * 1.5:
            flags.add("total-token-high")
    repeated = 0
    context_bytes = 0
    validation_output_bytes = 0
    retries = 0
    report_chars = 0
    review_invalidations = 0
    for phase in summary.get("phases", {}).values():
        for category, metrics in phase.get("context", {}).items():
            bytes_read = metrics.get("bytes_read")
            repeated_bytes = metrics.get("repeated_bytes_estimate")
            if isinstance(bytes_read, int):
                context_bytes += bytes_read
                if category == "validation_output":
                    validation_output_bytes += bytes_read
            if isinstance(repeated_bytes, int):
                repeated += repeated_bytes
        phase_retries = phase.get("operations", {}).get("retries")
        if isinstance(phase_retries, int):
            retries += phase_retries
        chars = phase.get("report", {}).get("report_characters")
        if isinstance(chars, int):
            report_chars += chars
        invalidations = phase.get("rework", {}).get("review_invalidations")
        if isinstance(invalidations, int):
            review_invalidations += invalidations
    if context_bytes > 0 and repeated / context_bytes > 0.4:
        flags.add("repeated-context-high")
    if context_bytes > 0 and validation_output_bytes / context_bytes > 0.4:
        flags.add("tool-output-high")
    if review_invalidations > 0:
        flags.add("review-restart-high")
    if retries >= 3:
        flags.add("retry-high")
    if report_chars >= 20000:
        flags.add("report-size-high")
    if len(same_precision) >= 3:
        for phase_name, phase in summary.get("phases", {}).items():
            current_phase_total = phase.get("usage", {}).get("total_tokens")
            historical_phase = [
                item.get("phases", {})
                .get(phase_name, {})
                .get("usage", {})
                .get("total_tokens")
                for item in same_precision
            ]
            values = [value for value in historical_phase if isinstance(value, int)]
            if len(values) >= 3 and isinstance(current_phase_total, int):
                median = statistics.median(values)
                if median > 0 and current_phase_total > median * 1.5:
                    flags.add("phase-token-high")
                    break
    result["anomaly_flags"] = sorted(flags)
    return result


def _markdown_summary(
    summary: Mapping[str, Any],
    comparison: Mapping[str, Any] | None,
) -> str:
    run = summary["run"]
    lines = [
        "# Task Workflow Telemetry Summary",
        "",
        f"- Run: `{run['run_id']}`",
        f"- Task: `#{run['task_number']}` {run.get('task_canonical_title') or ''}",
        f"- Mode: `{run['mode']}`",
        (
            f"- Classification: `{run['task_kind']} / {run['size']} / "
            f"{run['risk_class']} / {run['workflow_shape']}`"
        ),
        f"- Workflow main SHA: `{run.get('workflow_main_sha') or 'unavailable'}`",
        f"- Status: `{run['status']}`",
        f"- Telemetry complete: `{str(summary['telemetry_complete']).lower()}`",
        "",
        "## Usage",
        "",
        f"- Sources: `{', '.join(summary['total_usage']['sources']) or 'unavailable'}`",
        f"- Total tokens: `{summary['total_usage']['total_tokens']}`",
        f"- Input tokens: `{summary['total_usage']['input_tokens']}`",
        f"- Output tokens: `{summary['total_usage']['output_tokens']}`",
        (
            "- Coverage by source: `"
            + ", ".join(
                f"{source}={count}"
                for source, count in summary["usage_coverage"]["by_source"].items()
            )
            + "`"
        ),
        "",
        "## Phases",
        "",
        "| Phase | Events | Total tokens | Tool calls | Retries | Report chars |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, phase in summary["phases"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(phase["event_count"]),
                    str(phase["usage"]["total_tokens"]),
                    str(phase["operations"]["tool_calls"]),
                    str(phase["operations"]["retries"]),
                    str(phase["report"]["report_characters"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Quality and limitations",
            "",
            f"- Missing phases: `{', '.join(summary['missing_phases']) or 'none'}`",
            (
                "- Findings: `"
                + ", ".join(
                    f"{severity}={count}"
                    for severity, count in summary["quality"][
                        "findings_by_severity"
                    ].items()
                )
                + "`"
            ),
            (
                "- Validation all passed: `"
                f"{summary['quality']['validation_all_passed']}`"
            ),
            (f"- Review invalidations: `{summary['quality']['review_invalidations']}`"),
            (f"- Maintainer decisions: `{summary['quality']['maintainer_decisions']}`"),
            f"- Limitations: `{'; '.join(summary['limitations']) or 'none'}`",
        ]
    )
    if comparison is not None:
        lines.extend(
            [
                "",
                "## Spot-check",
                "",
                f"- Comparable samples: `{comparison['comparable_sample_count']}`",
                (
                    "- Same-precision samples: "
                    f"`{comparison['same_precision_sample_count']}`"
                ),
                (
                    "- Sample sufficient: "
                    f"`{str(comparison['sample_sufficient']).lower()}`"
                ),
                f"- Historical median: `{comparison['historical_total_median']}`",
                f"- Current delta: `{comparison['current_total_delta']}`",
                (
                    "- Anomaly flags: "
                    f"`{', '.join(comparison['anomaly_flags']) or 'none'}`"
                ),
                f"- Limitations: `{'; '.join(comparison['limitations']) or 'none'}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _common_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=".", help="repository root")
    parser.add_argument(
        "--config",
        default=".agents/task-workflow-telemetry.local.toml",
        help="local telemetry TOML path",
    )


def _load_for_command(args: argparse.Namespace) -> tuple[Path, Config]:
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        raise TelemetryError(f"repository root does not exist: {repo_root}")
    return repo_root, _load_config(repo_root, args.config)


def _command_start(args: argparse.Namespace) -> int:
    repo_root, config = _load_for_command(args)
    mode = args.mode or config.default_mode
    if mode not in MODES:
        raise TelemetryError("mode must be baseline-only or spot-check")
    task_kind = _validate_slug(args.task_kind, "task_kind")
    risk_class = _validate_slug(args.risk_class, "risk_class")
    workflow_shape = _validate_slug(args.workflow_shape, "workflow_shape")
    if args.size not in {"S", "M", "L"}:
        raise TelemetryError("size must be S, M, or L")
    if args.task <= 0:
        raise TelemetryError("task number must be positive")
    _validate_sha(args.workflow_main_sha, "workflow_main_sha")
    active_file = config.output_dir / "active" / f"task-{args.task}.json"
    if active_file.exists():
        active = _read_json(active_file)
        raise TelemetryError(
            f"Task #{args.task} already has active run "
            f"{active.get('run_id', 'unknown')}"
        )
    now = _utc_now()
    timestamp = now[:19].replace(":", "").replace("-", "")
    run_id = f"tw-{args.task}-{timestamp}-{secrets.token_hex(4)}"
    paths = _run_paths(config, args.task, run_id)
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": mode,
        "task_number": args.task,
        "task_canonical_title": args.task_title,
        "task_kind": task_kind,
        "size": args.size,
        "risk_class": risk_class,
        "workflow_shape": workflow_shape,
        "repository": args.repository,
        "workflow_main_sha": args.workflow_main_sha,
        "model": args.model,
        "pr_number": None,
        "feature_number": args.feature,
        "base_sha": None,
        "head_sha": None,
        "started_at": now,
        "finished_at": None,
        "status": "active",
    }
    _validate_manifest(manifest)
    _atomic_write_json(paths.manifest_file, manifest)
    paths.events_file.touch(exist_ok=False)
    active = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "task_number": args.task,
        "mode": mode,
        "current_phase": None,
        "started_at": now,
    }
    _atomic_write_json(paths.active_file, active)
    _print_json(
        {
            "active": True,
            "run_id": run_id,
            "task_number": args.task,
            "mode": mode,
            "run_path": str(paths.run_dir.relative_to(repo_root))
            if _is_within(paths.run_dir, repo_root)
            else "external-local-output",
        }
    )
    return 0


def _command_status(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    identity = _selector_identity(args)
    try:
        config = _load_config(repo_root, args.config)
    except TelemetryError as exc:
        _print_json(
            {
                "active": False,
                "telemetry_available": False,
                "reason": str(exc),
                **identity,
            }
        )
        return 0
    active = _select_active_run(config, task=args.task, feature=args.feature)
    if active is None:
        _print_json(
            {
                "active": False,
                "telemetry_available": True,
                **identity,
            }
        )
        return 0
    _, paths = active
    pointer = _read_json(paths.active_file)
    if not isinstance(pointer, dict):
        raise TelemetryError("active pointer is invalid")
    events = _read_events(paths.events_file)
    manifest = _validate_manifest(_read_json(paths.manifest_file))
    _print_json(
        {
            "active": True,
            "telemetry_available": True,
            "task_number": manifest["task_number"],
            "feature_number": manifest["feature_number"],
            "run_id": manifest["run_id"],
            "mode": manifest["mode"],
            "current_phase": pointer.get("current_phase"),
            "event_count": len(events),
            "telemetry_complete": False,
        }
    )
    return 0


def _command_record(args: argparse.Namespace) -> int:
    _, config = _load_for_command(args)
    active = _select_active_run(config, task=args.task, feature=args.feature)
    if active is None:
        raise TelemetryError("no active telemetry run matches the requested identity")
    _, paths = active
    data = _validate_record_data(_read_json(Path(args.data_file)))
    phase = args.phase
    if phase not in PHASES:
        raise TelemetryError("unsupported phase")
    _validate_run(paths)
    manifest = _require_active_manifest(paths)
    events = _read_events(paths.events_file)
    if data["event_type"] == "phase-summary" and any(
        event.get("event_type") == "phase-summary" and event.get("phase") == phase
        for event in events
    ):
        raise TelemetryError(f"primary phase summary already exists for {phase}")
    event_id = f"ev-{len(events) + 1:06d}-{secrets.token_hex(4)}"
    event = {
        **data,
        "event_id": event_id,
        "phase": phase,
        "recorded_at": data.get("recorded_at") or _utc_now(),
    }
    _validate_event(event)
    _validate_event_identity_against_manifest(event, manifest)
    _ensure_append_timestamp(events, event["recorded_at"])
    _append_jsonl(paths.events_file, event)
    active = _read_json(paths.active_file)
    if not isinstance(active, dict):
        raise TelemetryError("active pointer is invalid")
    active["current_phase"] = phase
    active["last_event_id"] = event_id
    _atomic_write_json(paths.active_file, active)
    _print_json(
        {
            "recorded": True,
            "run_id": active["run_id"],
            "event_id": event_id,
            "event_type": event["event_type"],
            "phase": phase,
        }
    )
    return 0


def _command_patch_usage(args: argparse.Namespace) -> int:
    _, config = _load_for_command(args)
    if not config.allow_usage_patch:
        raise TelemetryError("usage patching is disabled by local config")
    active = _select_active_run(config, task=args.task, feature=args.feature)
    if active is None:
        raise TelemetryError("no active telemetry run matches the requested identity")
    _, paths = active
    _validate_run(paths)
    _require_active_manifest(paths)
    raw = _read_json(Path(args.data_file))
    _reject_sensitive(raw)
    if isinstance(raw, dict) and "usage" in raw:
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise TelemetryError("usage patch schema_version must be 1")
        usage = _validate_usage(raw["usage"])
    else:
        usage = _validate_usage(raw)
    events = _read_events(paths.events_file)
    candidates = [
        event
        for event in events
        if event.get("phase") == args.phase and event.get("event_type") != "usage-patch"
    ]
    if args.event_id is not None:
        candidates = [
            event for event in candidates if event.get("event_id") == args.event_id
        ]
    if not candidates:
        raise TelemetryError("no target event found for usage patch")
    target = candidates[-1]
    effective = _events_with_patches(events)
    effective_target = next(
        (
            event
            for event in effective
            if event.get("event_id") == target.get("event_id")
        ),
        target,
    )
    existing = effective_target.get("usage")
    if isinstance(existing, dict) and existing.get("source") in EXACT_USAGE_SOURCES:
        raise TelemetryError("existing exact usage cannot be overwritten")
    patch_id = f"ev-{len(events) + 1:06d}-{secrets.token_hex(4)}"
    patch = {
        "schema_version": SCHEMA_VERSION,
        "event_id": patch_id,
        "event_type": "usage-patch",
        "phase": args.phase,
        "target_event_id": target["event_id"],
        "recorded_at": _utc_now(),
        "usage": usage,
    }
    _validate_event(patch)
    _ensure_append_timestamp(events, patch["recorded_at"])
    _append_jsonl(paths.events_file, patch)
    _print_json(
        {
            "patched": True,
            "event_id": patch_id,
            "target_event_id": target["event_id"],
            "phase": args.phase,
            "source": usage["source"],
        }
    )
    return 0


def _command_finish(args: argparse.Namespace) -> int:
    _, config = _load_for_command(args)
    active = _select_active_run(config, task=args.task, feature=args.feature)
    if active is None:
        raise TelemetryError("no active telemetry run matches the requested identity")
    _, paths = active
    _validate_run(paths)
    manifest = _require_active_manifest(paths)
    manifest["finished_at"] = _utc_now()
    manifest["status"] = args.status
    _validate_manifest(manifest)
    events = _read_events(paths.events_file)
    summary = _aggregate_events(manifest, events)
    _atomic_write_json(paths.manifest_file, manifest)
    _atomic_write_json(paths.summary_file, summary)
    paths.active_file.unlink()
    _print_json(
        {
            "finished": True,
            "run_id": manifest["run_id"],
            "status": manifest["status"],
            "telemetry_complete": summary["telemetry_complete"],
            "missing_phases": summary["missing_phases"],
        }
    )
    return 0


def _command_validate(args: argparse.Namespace) -> int:
    _, config = _load_for_command(args)
    _, paths = _locate_run(
        config,
        task=args.task,
        feature=args.feature,
        run_id=args.run_id,
    )
    _print_json(_validate_run(paths))
    return 0


def _command_summarize(args: argparse.Namespace) -> int:
    _, config = _load_for_command(args)
    _, paths = _locate_run(
        config,
        task=args.task,
        feature=args.feature,
        run_id=args.run_id,
    )
    _validate_run(paths, check_stored_summary=False)
    manifest = _validate_manifest(_read_json(paths.manifest_file))
    events = _read_events(paths.events_file)
    summary = _aggregate_events(manifest, events)
    if manifest["status"] != "active":
        _atomic_write_json(paths.summary_file, summary)
    comparison = (
        _spot_check(config, summary) if manifest["mode"] == "spot-check" else None
    )
    if args.format == "json":
        result = dict(summary)
        if comparison is not None:
            result["spot_check"] = comparison
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_markdown_summary(summary, comparison), end="")
    return 0


def _add_identity_selector(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", type=int)
    group.add_argument("--feature", type=int)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local, append-only Task workflow telemetry",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="start an explicit Task telemetry run")
    _common_parser(start)
    start.add_argument("--task", type=int, required=True)
    start.add_argument("--task-title", required=True)
    start.add_argument("--mode", choices=sorted(MODES))
    start.add_argument("--task-kind", required=True)
    start.add_argument("--size", choices=["S", "M", "L"], required=True)
    start.add_argument("--risk-class", required=True)
    start.add_argument("--workflow-shape", required=True)
    start.add_argument("--repository", required=True)
    start.add_argument("--workflow-main-sha", required=True)
    start.add_argument("--model")
    start.add_argument("--feature", type=int)
    start.set_defaults(func=_command_start)

    status = subparsers.add_parser("status", help="read lightweight active-run state")
    _common_parser(status)
    _add_identity_selector(status)
    status.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    status.set_defaults(func=_command_status)

    record = subparsers.add_parser("record", help="append a validated phase event")
    _common_parser(record)
    _add_identity_selector(record)
    record.add_argument("--phase", choices=sorted(PHASES), required=True)
    record.add_argument("--data-file", required=True)
    record.set_defaults(func=_command_record)

    patch = subparsers.add_parser("patch-usage", help="append usage for a phase event")
    _common_parser(patch)
    _add_identity_selector(patch)
    patch.add_argument("--phase", choices=sorted(PHASES), required=True)
    patch.add_argument("--event-id")
    patch.add_argument("--data-file", required=True)
    patch.set_defaults(func=_command_patch_usage)

    finish = subparsers.add_parser("finish", help="finish a run and write summary.json")
    _common_parser(finish)
    _add_identity_selector(finish)
    finish.add_argument(
        "--status",
        choices=["completed", "cancelled", "failed"],
        default="completed",
    )
    finish.set_defaults(func=_command_finish)

    validate = subparsers.add_parser(
        "validate",
        help="validate a run without modifying it",
    )
    _common_parser(validate)
    _add_identity_selector(validate)
    validate.add_argument("--run-id")
    validate.set_defaults(func=_command_validate)

    summarize = subparsers.add_parser(
        "summarize",
        help="render a sanitized local summary",
    )
    _common_parser(summarize)
    _add_identity_selector(summarize)
    summarize.add_argument("--run-id")
    summarize.add_argument("--format", choices=["json", "markdown"], default="json")
    summarize.set_defaults(func=_command_summarize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except TelemetryError as exc:
        print(f"telemetry error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
