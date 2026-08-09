"""Claude transcript adapter for task-65-round-2-v2 observability.

Normalizes Claude session transcripts (``~/.claude/projects/**`` JSONL,
or whichever source the arm's session resolution produces) into mechanical
access events.

Parser pins two record content-item types:

- ``tool_use`` (assistant messages): tool invocation (name + input -> target);
- ``tool_result`` (user messages): tool call result (content -> target).

The adapter only extracts / normalizes; it never interprets workflow
semantics.  Unknown record formats fail closed (``fail_closed: true``) so the
arm audit reports ``NOT VERIFIED`` instead of a false pass.  Recognized
non-tool records (plain text, summaries, metadata) are skipped and counted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from benchmark_common import BenchmarkError, bounded_text

CONTENT_ITEM_TYPES: frozenset[str] = frozenset({"tool_use", "tool_result"})

# Known non-tool content item types (skipped and counted, never events).
NON_TOOL_CONTENT_TYPES: frozenset[str] = frozenset({"text", "thinking"})

# Message record types whose content items are inspected for tool events.
_TOOL_MESSAGE_TYPES: frozenset[str] = frozenset({"assistant", "user"})
# Record types that are known and never carry tool events.
_SKIPPED_RECORD_TYPES: frozenset[str] = frozenset(
    {"system", "summary", "isMeta", "meta"}
)


def _normalize_tool(tool: str) -> str:
    name = tool.lower()
    for prefix, category in (
        ("bash", "shell"),
        ("read", "read"),
        ("grep", "grep"),
        ("glob", "glob"),
        ("write", "other"),
        ("edit", "other"),
        ("notebookedit", "other"),
        ("web", "other"),
        ("todo", "other"),
        ("task", "other"),
    ):
        if name.startswith(prefix):
            return category
    return "other"


def parse_record(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize one transcript record into access events (0..n).

    Returns a list of events.  Raises ``BenchmarkError`` (fail closed) when a
    record's structure or a content-item type is unknown.
    """
    record_type = record.get("type")
    message = record.get("message")
    if record_type in _SKIPPED_RECORD_TYPES:
        return []
    if record_type not in _TOOL_MESSAGE_TYPES:
        raise BenchmarkError(
            f"unknown Claude transcript record type {record_type!r} (fail closed)"
        )
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        raise BenchmarkError(
            f"{record_type} record without a message.content list (fail closed)"
        )
    session_id = record.get("session_id", "")
    timestamp = record.get("timestamp", "")
    events: list[dict[str, Any]] = []
    for item in message["content"]:
        if not isinstance(item, dict):
            raise BenchmarkError(
                f"content item is not an object (fail closed): {item!r}"
            )
        item_type = item.get("type")
        if item_type in NON_TOOL_CONTENT_TYPES:
            continue
        if item_type not in CONTENT_ITEM_TYPES:
            raise BenchmarkError(
                f"unknown content item type {item_type!r} (fail closed)"
            )
        if item_type == "tool_use":
            name = item.get("name")
            if not isinstance(name, str):
                raise BenchmarkError("tool_use without a name (fail closed)")
            events.append(
                {
                    "arm_id": record.get("arm_id", ""),
                    "session_id": str(session_id),
                    "timestamp": str(timestamp),
                    "tool": _normalize_tool(name),
                    "operation": "tool_use",
                    "target": bounded_text(
                        json.dumps(item.get("input", {}), sort_keys=True)
                    ),
                    "raw_event_reference": str(item.get("id", "")),
                }
            )
        else:  # tool_result
            content = item.get("content", "")
            events.append(
                {
                    "arm_id": record.get("arm_id", ""),
                    "session_id": str(session_id),
                    "timestamp": str(timestamp),
                    "tool": "other",
                    "operation": "tool_result",
                    "target": bounded_text(
                        content if isinstance(content, str) else json.dumps(content)
                    ),
                    "raw_event_reference": str(item.get("tool_use_id", "")),
                }
            )
    return events


def parse_transcript(
    lines: Iterable[str], arm_id: str = ""
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse transcript JSONL lines into normalized access events.

    Returns (events, diagnostics) with counts: ``records``, ``events``,
    ``non_tool_records``, ``malformed``.  A malformed record fails the whole
    parse (fail closed).
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
                f"unparseable transcript line (fail closed): {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            diagnostics["malformed"] += 1
            raise BenchmarkError(
                f"transcript record is not an object (fail closed): {parsed!r}"
            )
        if parsed.get("type") in _SKIPPED_RECORD_TYPES:
            diagnostics["non_tool_records"] += 1
            continue
        record_events = parse_record(parsed)
        for event in record_events:
            event["arm_id"] = event["arm_id"] or arm_id
            events.append(event)
        if not record_events:
            diagnostics["non_tool_records"] += 1
        else:
            diagnostics["events"] += len(record_events)
    return events, diagnostics
