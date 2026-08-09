"""Codex rollout adapter for task-65-round-2-v2 observability.

Normalizes Codex rollout records into mechanical access events:

- ``custom_tool_call``: tool invocation (tool + args -> target);
- ``custom_tool_call_output``: tool call output record.

The adapter only extracts / normalizes; it never interprets workflow
semantics.  Unknown record formats fail closed (the parser is pinned to the
two record types above), producing ``fail_closed: true`` so the arm audit
reports ``NOT VERIFIED`` instead of a false pass.

Normalized event schema (access-event.schema.json):

    arm_id / session_id / timestamp / tool / operation / target /
    raw_event_reference
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from benchmark_common import BenchmarkError, bounded_text

RECORD_TYPES: frozenset[str] = frozenset(
    {"custom_tool_call", "custom_tool_call_output"}
)

# Tool name -> normalized tool category (access-event schema enum).
_TOOL_MAP: dict[str, str] = {
    "git": "git",
    "gh": "gh",
    "read": "read",
    "grep": "grep",
    "glob": "glob",
    "bash": "shell",
    "shell": "shell",
    "subprocess": "shell",
    "write": "other",
    "edit": "other",
    "apply_patch": "other",
}


def _normalize_tool(tool: str) -> str:
    return _TOOL_MAP.get(tool.lower(), "other")


def _target_from_args(args: Any) -> str:
    """Derive a bounded audit target from tool call arguments.

    Targets cover git args, gh args, Read paths, Grep path/pattern scope,
    Glob scope, shell commands, and evidence/rollout paths.
    """
    if isinstance(args, Mapping):
        for key in (
            "path",
            "pattern",
            "paths",
            "file_path",
            "command",
            "url",
            "target",
        ):
            value = args.get(key)
            if isinstance(value, str) and value:
                return bounded_text(value)
        return bounded_text(json.dumps(args, sort_keys=True, ensure_ascii=False))
    return bounded_text(json.dumps(args, sort_keys=True, ensure_ascii=False))


def parse_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize one rollout record into an access event.

    Returns None for recognized non-access metadata records; raises
    ``BenchmarkError`` (fail closed) for unknown record formats.
    """
    record_type = record.get("type")
    if record_type not in RECORD_TYPES:
        raise BenchmarkError(f"unknown Codex rollout record type {record_type!r}")
    timestamp = record.get("timestamp")
    session_id = record.get("session_id")
    tool = record.get("tool")
    if not isinstance(timestamp, str) or not isinstance(session_id, str):
        raise BenchmarkError(
            f"{record_type}: missing/invalid timestamp or session_id (fail closed)"
        )
    if not isinstance(tool, str):
        raise BenchmarkError(f"{record_type}: missing tool (fail closed)")
    if record_type == "custom_tool_call":
        return {
            "arm_id": record.get("arm_id", ""),
            "session_id": session_id,
            "timestamp": timestamp,
            "tool": _normalize_tool(tool),
            "operation": "tool_call",
            "target": _target_from_args(record.get("args")),
            "raw_event_reference": str(record.get("raw_reference", "")),
        }
    # custom_tool_call_output
    return {
        "arm_id": record.get("arm_id", ""),
        "session_id": session_id,
        "timestamp": timestamp,
        "tool": _normalize_tool(tool),
        "operation": "tool_call_output",
        "target": bounded_text(str(record.get("output_reference", ""))),
        "raw_event_reference": str(record.get("raw_reference", "")),
    }


def parse_rollout(
    lines: Iterable[str], arm_id: str = ""
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse rollout lines into normalized access events.

    Returns (events, diagnostics).  Diagnostics counts:
    ``records``, ``events``, ``non_tool_records``, ``malformed``.
    A malformed record fails the whole parse (fail closed).
    """
    events: list[dict[str, Any]] = []
    diagnostics = {"records": 0, "events": 0, "non_tool_records": 0, "malformed": 0}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        diagnostics["records"] += 1
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            diagnostics["malformed"] += 1
            raise BenchmarkError(
                f"unparseable rollout line (fail closed): {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            diagnostics["malformed"] += 1
            raise BenchmarkError(
                f"rollout record is not an object (fail closed): {parsed!r}"
            )
        if parsed.get("record_type") == "meta" or parsed.get("type") == "meta":
            diagnostics["non_tool_records"] += 1
            continue
        event = parse_record(parsed)
        if event is None:
            diagnostics["non_tool_records"] += 1
            continue
        event["arm_id"] = event["arm_id"] or arm_id
        events.append(event)
        diagnostics["events"] += 1
    return events, diagnostics
