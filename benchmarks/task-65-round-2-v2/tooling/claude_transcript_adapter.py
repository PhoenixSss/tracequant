"""Claude transcript adapter for task-65-round-2-v2 observability.

Normalizes Claude session transcripts (``~/.claude/projects/**`` JSONL, or
whichever source the arm's session resolution produces) into two mechanical
streams:

- access events (canonical ``arm_id / session_id / timestamp / tool /
  operation / target / raw_event_reference`` shape, from ``tool_use`` and
  ``tool_result`` content items);
- context inputs (records that injected content into the session:
  attachments, summaries, prompts) so the access audit can also check those
  channels for Class 2 / Class 3 forbidden identities.

Record taxonomy (schema-backed, see
``contracts/observability-and-access-audit-contract.md``):

A. ACCESS_BEARING
   ``assistant`` / ``user`` records whose ``message.content`` items carry
   ``tool_use`` / ``tool_result`` -> normalized access events.

B. INPUT_CONTEXT_BEARING
   ``attachment`` (file contents, agent/skill listings, todo state),
   ``summary`` (compaction summaries), ``last-prompt`` (prompt echo), and
   user message text (prompt / continuation summary) -> context inputs,
   audited for forbidden identities.  Never silently skipped.

C. KNOWN_NON_ACCESS_METADATA
   ``queue-operation``, ``ai-title``, ``file-history-delta``,
   ``file-history-snapshot``, ``system``, ``meta``, ``isMeta``, ``pr-link``,
   ``mode`` -- structurally validated UI/queue/lifecycle metadata.  Explicit
   allowlist; a structure that could carry content or access fails closed.

D. UNKNOWN
   Any other record type, content item type, or invalid structure ->
   ``BenchmarkError`` (fail closed) so the arm audit reports ``NOT VERIFIED``
   instead of a false pass.

Session identity model: the transcript records carry ``sessionId`` but the
resolved session identity is an explicit adapter input (from the observability
preflight).  Every normalized event is stamped with that injected
``session_id``; a record whose ``sessionId`` conflicts with it fails closed.
No default value, no silent fallback, no empty ``session_id``.

The adapter only extracts / normalizes; it never interprets workflow
semantics.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from benchmark_common import BenchmarkError, bounded_text

CONTENT_ITEM_TYPES: frozenset[str] = frozenset({"tool_use", "tool_result"})

# --------------------------------------------------------------------------
# Record taxonomy (contract-backed).
# --------------------------------------------------------------------------

# A. Records whose message.content items carry tool access.
ACCESS_BEARING_RECORD_TYPES: frozenset[str] = frozenset({"assistant", "user"})

# B. Records that inject content into the session context.
INPUT_CONTEXT_BEARING_RECORD_TYPES: frozenset[str] = frozenset(
    {"attachment", "summary", "last-prompt"}
)

# C. Validated UI / queue / lifecycle metadata (explicit allowlist).
KNOWN_NON_ACCESS_METADATA_RECORD_TYPES: frozenset[str] = frozenset(
    {
        "queue-operation",
        "ai-title",
        "file-history-delta",
        "file-history-snapshot",
        "system",
        "meta",
        "isMeta",
        "pr-link",
        "mode",
    }
)

ALL_SUPPORTED_RECORD_TYPES: frozenset[str] = (
    ACCESS_BEARING_RECORD_TYPES
    | INPUT_CONTEXT_BEARING_RECORD_TYPES
    | KNOWN_NON_ACCESS_METADATA_RECORD_TYPES
)

# Observed record formats for the observability preflight (claude:<type>).
CLAUDE_RECORD_FORMATS: frozenset[str] = frozenset(
    {"claude:tool_use", "claude:tool_result", "claude:assistant", "claude:user"}
    | {"claude:" + record_type for record_type in ALL_SUPPORTED_RECORD_TYPES}
)

# Known non-tool, non-context content item types (assistant output; skipped).
ASSISTANT_NON_ACCESS_CONTENT_TYPES: frozenset[str] = frozenset({"text", "thinking"})

# Attachment sub-types observed on the current tested runtime
# (Claude Code VSCode 2.1.226).  ``file`` carries full file content injected
# into the session (context input); the others carry listing/todo text.
ATTACHMENT_TYPES: frozenset[str] = frozenset(
    {"file", "agent_listing_delta", "skill_listing", "todo_reminder"}
)
# Attachment types whose payload is content input (audited as context inputs).
CONTEXT_BEARING_ATTACHMENT_TYPES: frozenset[str] = frozenset(
    {"file", "agent_listing_delta", "skill_listing", "todo_reminder"}
)

# ``system`` record subtypes observed on the current tested runtime.
SYSTEM_SUBTYPES: frozenset[str] = frozenset({"api_error", "compact_boundary"})

# ``queue-operation`` operations observed on the current tested runtime.
QUEUE_OPERATIONS: frozenset[str] = frozenset({"enqueue", "dequeue"})

# Allowed keys inside file-history backup metadata.  Backup records are the
# file-history feature's backup registry (paths + backup metadata only); any
# content-bearing payload here is unsupported -> fail closed.
FILE_HISTORY_BACKUP_KEYS: frozenset[str] = frozenset(
    {"backupFileName", "version", "backupTime", "realParentDir"}
)
FILE_HISTORY_SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {"messageId", "timestamp", "trackedFileBackups"}
)

# Content-bearing keys that must never appear inside metadata records.
_CONTENT_KEYS: frozenset[str] = frozenset(
    {"content", "text", "lines", "data", "payload", "input"}
)


def _record_session_id(record: Mapping[str, Any]) -> str | None:
    """Return the record's own session identity, if it carries one."""
    value = record.get("sessionId", record.get("session_id"))
    if value is None:
        return None
    if not isinstance(value, str):
        raise BenchmarkError(
            f"record session identity is not a string (fail closed): {value!r}"
        )
    return value


def _validate_record_session(record: Mapping[str, Any], session_id: str) -> None:
    """Validate a record's session identity against the injected identity.

    A record carrying a ``sessionId`` that conflicts with the injected
    ``session_id`` fails closed (transcript/session mismatch).  Records
    without a session key (e.g. ``file-history-*``) pass.
    """
    if not session_id:
        raise BenchmarkError(
            "session identity is required for transcript normalization "
            "(no empty session_id)"
        )
    own = _record_session_id(record)
    if own is not None and own != session_id:
        raise BenchmarkError(
            f"record sessionId {own!r} does not match injected session "
            f"identity {session_id!r} (fail closed)"
        )


# --------------------------------------------------------------------------
# Metadata validators (structure + semantics; fail closed on any violation).
# --------------------------------------------------------------------------


def _validate_no_content_payload(record_type: str, record: Mapping[str, Any]) -> None:
    for key in _CONTENT_KEYS:
        if key in record:
            raise BenchmarkError(
                f"{record_type} metadata record carries a content payload "
                f"key {key!r} (fail closed)"
            )


def _validate_metadata_record(record_type: str, record: Mapping[str, Any]) -> None:
    if record_type == "queue-operation":
        operation = record.get("operation")
        if not isinstance(operation, str) or operation not in QUEUE_OPERATIONS:
            raise BenchmarkError(
                f"queue-operation with unsupported operation {operation!r} "
                "(fail closed)"
            )
        _validate_no_content_payload(record_type, record)
    elif record_type == "ai-title":
        if not isinstance(record.get("aiTitle"), str):
            raise BenchmarkError("ai-title without an aiTitle string (fail closed)")
    elif record_type == "file-history-delta":
        _validate_file_history_backup(record_type, record.get("backup"))
        if not isinstance(record.get("trackingPath"), str):
            raise BenchmarkError(
                "file-history-delta without a trackingPath (fail closed)"
            )
    elif record_type == "file-history-snapshot":
        snapshot = record.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise BenchmarkError(
                "file-history-snapshot without a snapshot object (fail closed)"
            )
        unexpected = sorted(set(snapshot) - FILE_HISTORY_SNAPSHOT_KEYS)
        if unexpected:
            raise BenchmarkError(
                f"file-history-snapshot with unsupported snapshot keys "
                f"{unexpected} (fail closed)"
            )
        tracked = snapshot.get("trackedFileBackups")
        if tracked is not None:
            if not isinstance(tracked, Mapping):
                raise BenchmarkError(
                    "file-history-snapshot trackedFileBackups is not an object "
                    "(fail closed)"
                )
            for path, backup in tracked.items():
                _validate_file_history_backup(record_type, backup)
                if not isinstance(path, str):
                    raise BenchmarkError(
                        "file-history-snapshot backup path is not a string "
                        "(fail closed)"
                    )
    elif record_type == "system":
        subtype = record.get("subtype")
        if not isinstance(subtype, str) or subtype not in SYSTEM_SUBTYPES:
            raise BenchmarkError(
                f"system record with unsupported subtype {subtype!r} (fail closed)"
            )
        if "message" in record:
            raise BenchmarkError(
                "system record carries a message payload (fail closed)"
            )
    elif record_type in ("meta", "isMeta"):
        _validate_no_content_payload(record_type, record)
    elif record_type == "pr-link":
        if not isinstance(record.get("prNumber"), int):
            raise BenchmarkError("pr-link without a prNumber integer (fail closed)")
        if not isinstance(record.get("prUrl"), str):
            raise BenchmarkError("pr-link without a prUrl string (fail closed)")
    elif record_type == "mode":
        if not isinstance(record.get("mode"), str):
            raise BenchmarkError("mode record without a mode string (fail closed)")


def _validate_file_history_backup(record_type: str, backup: Any) -> None:
    if backup is None:
        return
    if not isinstance(backup, Mapping):
        raise BenchmarkError(
            f"{record_type} backup entry is not an object (fail closed)"
        )
    unexpected = sorted(set(backup) - FILE_HISTORY_BACKUP_KEYS)
    if unexpected:
        raise BenchmarkError(
            f"{record_type} backup entry with unsupported keys {unexpected} "
            "(fail closed)"
        )


# --------------------------------------------------------------------------
# Extraction helpers.
# --------------------------------------------------------------------------


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


def _event(
    record: Mapping[str, Any],
    session_id: str,
    arm_id: str,
    *,
    tool: str,
    operation: str,
    target: str,
    raw_event_reference: str,
) -> dict[str, Any]:
    return {
        "arm_id": arm_id,
        "session_id": session_id,
        "timestamp": str(record.get("timestamp", "")),
        "tool": tool,
        "operation": operation,
        "target": target,
        "raw_event_reference": raw_event_reference,
    }


def _context_input(
    record: Mapping[str, Any],
    session_id: str,
    *,
    source_type: str,
    target: str,
    raw_event_reference: str = "",
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "timestamp": str(record.get("timestamp", "")),
        "source_type": source_type,
        # Context inputs are NOT truncated: a forbidden identifier in the
        # tail of an attachment / summary / prompt must not be lost to a
        # bounded-output cap (contamination detection must not silently miss).
        "target": target,
        "raw_event_reference": raw_event_reference,
    }


def _extract_tool_events(
    record: Mapping[str, Any],
    message: Mapping[str, Any],
    session_id: str,
    arm_id: str,
    role: str,
) -> list[dict[str, Any]]:
    """Normalize ``message.content`` items into access events (fail closed)."""
    content = message.get("content")
    if not isinstance(content, list):
        raise BenchmarkError(
            f"{role} record without a message.content list (fail closed)"
        )
    events: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            raise BenchmarkError(
                f"content item is not an object (fail closed): {item!r}"
            )
        item_type = item.get("type")
        if item_type in ASSISTANT_NON_ACCESS_CONTENT_TYPES:
            continue
        if item_type not in CONTENT_ITEM_TYPES:
            raise BenchmarkError(
                f"unknown content item type {item_type!r} (fail closed)"
            )
        if item_type == "tool_use":
            name = item.get("name")
            item_id = item.get("id")
            if not isinstance(name, str):
                raise BenchmarkError("tool_use without a name (fail closed)")
            if not isinstance(item_id, str) or not item_id:
                raise BenchmarkError("tool_use without an id (fail closed)")
            events.append(
                _event(
                    record,
                    session_id,
                    arm_id,
                    tool=_normalize_tool(name),
                    operation="tool_use",
                    target=bounded_text(
                        json.dumps(item.get("input", {}), sort_keys=True)
                    ),
                    raw_event_reference=item_id,
                )
            )
        else:  # tool_result
            tool_use_id = item.get("tool_use_id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                raise BenchmarkError("tool_result without a tool_use_id (fail closed)")
            content_value = item.get("content", "")
            events.append(
                _event(
                    record,
                    session_id,
                    arm_id,
                    tool="other",
                    operation="tool_result",
                    target=bounded_text(
                        content_value
                        if isinstance(content_value, str)
                        else json.dumps(content_value)
                    ),
                    raw_event_reference=tool_use_id,
                )
            )
    return events


def _attachment_context_inputs(
    record: Mapping[str, Any], session_id: str
) -> list[dict[str, Any]]:
    """Extract context inputs from an ``attachment`` record (fail closed)."""
    attachment = record.get("attachment")
    if not isinstance(attachment, Mapping):
        raise BenchmarkError(
            "attachment record without an attachment object (fail closed)"
        )
    attachment_type = attachment.get("type")
    if attachment_type not in ATTACHMENT_TYPES:
        raise BenchmarkError(
            f"unsupported attachment type {attachment_type!r} (fail closed)"
        )
    if attachment_type == "file":
        content = attachment.get("content")
        if not isinstance(content, Mapping) or not isinstance(
            content.get("file"), Mapping
        ):
            raise BenchmarkError("file attachment without content.file (fail closed)")
        file = content["file"]
        file_path = file.get("filePath")
        file_content = file.get("content")
        if not isinstance(file_path, str) or not isinstance(file_content, str):
            raise BenchmarkError(
                "file attachment without filePath/content strings (fail closed)"
            )
        return [
            _context_input(
                record,
                session_id,
                source_type="attachment:file",
                target=f"{file_path}\n{file_content}",
                raw_event_reference=file_path,
            )
        ]
    if attachment_type == "agent_listing_delta":
        added_lines = attachment.get("addedLines")
        if not isinstance(added_lines, list) or not all(
            isinstance(line, str) for line in added_lines
        ):
            raise BenchmarkError(
                "agent_listing_delta attachment without addedLines strings "
                "(fail closed)"
            )
        return [
            _context_input(
                record,
                session_id,
                source_type="attachment:agent_listing_delta",
                target="\n".join(added_lines),
            )
        ]
    if attachment_type == "skill_listing":
        content = attachment.get("content")
        if not isinstance(content, str):
            raise BenchmarkError(
                "skill_listing attachment without a content string (fail closed)"
            )
        return [
            _context_input(
                record,
                session_id,
                source_type="attachment:skill_listing",
                target=content,
            )
        ]
    # todo_reminder
    items = attachment.get("content")
    if not isinstance(items, list) or not all(
        isinstance(item, Mapping) and isinstance(item.get("content"), str)
        for item in items
    ):
        raise BenchmarkError(
            "todo_reminder attachment without content item strings (fail closed)"
        )
    return [
        _context_input(
            record,
            session_id,
            source_type="attachment:todo_reminder",
            target="\n".join(str(item["content"]) for item in items),
        )
    ]


def _user_text_context_inputs(
    record: Mapping[str, Any], message: Mapping[str, Any], session_id: str
) -> list[dict[str, Any]]:
    """Extract context inputs from user message text (prompt / summary).

    ``message.content`` is a plain string for the isCompactSummary
    continuation record (and for legacy prompt strings); a content list may
    carry ``text`` items (the prompt and IDE context).  All of it is input
    context and is audited.
    """
    content = message.get("content")
    if isinstance(content, str):
        source_type = "summary" if record.get("isCompactSummary") else "user-prompt"
        return [
            _context_input(
                record,
                session_id,
                source_type=source_type,
                target=content,
            )
        ]
    if isinstance(content, list):
        inputs: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                raise BenchmarkError(
                    f"content item is not an object (fail closed): {item!r}"
                )
            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text")
                if not isinstance(text, str):
                    raise BenchmarkError(
                        "user text item without a text string (fail closed)"
                    )
                inputs.append(
                    _context_input(
                        record,
                        session_id,
                        source_type="user-prompt",
                        target=text,
                    )
                )
            elif item_type in CONTENT_ITEM_TYPES:
                continue  # handled by the access-event extraction
            elif item_type in ASSISTANT_NON_ACCESS_CONTENT_TYPES:
                continue
            else:
                raise BenchmarkError(
                    f"unknown content item type {item_type!r} (fail closed)"
                )
        return inputs
    raise BenchmarkError("user record without message.content (fail closed)")


# --------------------------------------------------------------------------
# Public API.
# --------------------------------------------------------------------------


def parse_record(
    record: Mapping[str, Any],
    *,
    session_id: str | None = None,
    arm_id: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize one transcript record into (access events, context inputs).

    ``session_id`` is the explicit resolved session identity; when omitted the
    record's own ``sessionId`` is used and must be present.  Raises
    ``BenchmarkError`` (fail closed) for unknown record types, unknown content
    item types, or malformed access-bearing structures.
    """
    record_type = record.get("type")
    if record_type in KNOWN_NON_ACCESS_METADATA_RECORD_TYPES:
        _validate_metadata_record(record_type, record)
        if session_id is not None:
            _validate_record_session(record, session_id)
        return [], []

    if record_type not in ACCESS_BEARING_RECORD_TYPES and (
        record_type not in INPUT_CONTEXT_BEARING_RECORD_TYPES
    ):
        raise BenchmarkError(
            f"unknown Claude transcript record type {record_type!r} (fail closed)"
        )

    if session_id is None:
        own = _record_session_id(record)
        if own is None:
            raise BenchmarkError(
                "record without a session identity and no injected session_id "
                "(fail closed)"
            )
        session_id = own
    else:
        _validate_record_session(record, session_id)

    # INPUT_CONTEXT_BEARING records (attachments carry no ``message``).
    if record_type == "attachment":
        return [], _attachment_context_inputs(record, session_id)
    if record_type == "last-prompt":
        prompt = record.get("lastPrompt")
        if not isinstance(prompt, str):
            raise BenchmarkError(
                "last-prompt record without a lastPrompt string (fail closed)"
            )
        return [], [
            _context_input(record, session_id, source_type="last-prompt", target=prompt)
        ]
    if record_type == "summary":
        summary = record.get("summary")
        if not isinstance(summary, str):
            raise BenchmarkError(
                "summary record without a summary string (fail closed)"
            )
        return [], [
            _context_input(record, session_id, source_type="summary", target=summary)
        ]

    # ACCESS_BEARING records: message object is required.
    message = record.get("message")
    if not isinstance(message, Mapping):
        raise BenchmarkError(
            f"{record_type} record without a message object (fail closed)"
        )

    if record_type == "assistant":
        events = _extract_tool_events(record, message, session_id, arm_id, "assistant")
        return events, []

    # user
    content = message.get("content")
    if isinstance(content, list):
        events = _extract_tool_events(record, message, session_id, arm_id, "user")
        context_inputs = _user_text_context_inputs(record, message, session_id)
    else:
        # String content: prompt / continuation summary.
        events = []
        context_inputs = _user_text_context_inputs(record, message, session_id)
    return events, context_inputs


def verify_session_path_match(path: str, session_id: str) -> None:
    """Mechanically verify ``path`` matches ``session_id``.

    The Claude transcript path pattern is
    ``~/.claude/projects/<project>/<session-id>.jsonl``; the basename stem
    must equal the resolved session identity.  Mismatch fails closed.
    """
    if not session_id:
        raise BenchmarkError(
            "session identity is required to verify a transcript path "
            "(no empty session_id)"
        )
    stem = Path(path).stem
    if stem != session_id:
        raise BenchmarkError(
            f"transcript path stem {stem!r} does not match session identity "
            f"{session_id!r} (fail closed)"
        )


def parse_transcript_file(
    path: str, arm_id: str = "", *, session_id: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parse a transcript file with explicit session identity binding.

    Verifies the mechanical transcript-path <-> session-identity match first,
    then normalizes every line.  See :func:`parse_transcript`.
    """
    verify_session_path_match(path, session_id)
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BenchmarkError(
            f"cannot read transcript {path}: {exc} (fail closed)"
        ) from exc
    return parse_transcript(lines, arm_id=arm_id, session_id=session_id)


def parse_transcript(
    lines: Iterable[str],
    arm_id: str = "",
    *,
    session_id: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parse transcript JSONL lines into (access events, context inputs,
    diagnostics).

    ``session_id`` is the explicit resolved session identity (from the
    observability preflight); it is required and injected into every
    normalized record.  Diagnostics counts: ``records``, ``events``,
    ``context_inputs``, ``non_tool_records``, ``malformed``, ``record_types``
    and the auditable ``session_identity``.  A malformed or unknown record
    fails the whole parse (fail closed).
    """
    if not session_id:
        raise BenchmarkError(
            "session identity is required for transcript normalization "
            "(no empty session_id)"
        )
    events: list[dict[str, Any]] = []
    context_inputs: list[dict[str, Any]] = []
    record_types: dict[str, int] = {}
    diagnostics: dict[str, Any] = {
        "records": 0,
        "events": 0,
        "context_inputs": 0,
        "non_tool_records": 0,
        "malformed": 0,
        "record_types": record_types,
        "session_identity": {
            "session_id": session_id,
            "source": "explicit-adapter-input",
            "verified": True,
        },
    }
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
        record_type = parsed.get("type")
        if not isinstance(record_type, str):
            raise BenchmarkError(
                f"transcript record without a type string (fail closed): {parsed!r}"
            )
        record_types[record_type] = record_types.get(record_type, 0) + 1
        if record_type in KNOWN_NON_ACCESS_METADATA_RECORD_TYPES:
            parse_record(parsed, session_id=session_id)  # validates structure
            diagnostics["non_tool_records"] += 1
            continue
        record_events, record_context = parse_record(
            parsed, session_id=session_id, arm_id=arm_id
        )
        for event in record_events:
            event["arm_id"] = event["arm_id"] or arm_id
            events.append(event)
        context_inputs.extend(record_context)
        if not record_events and not record_context:
            diagnostics["non_tool_records"] += 1
        else:
            diagnostics["events"] += len(record_events)
            diagnostics["context_inputs"] += len(record_context)
    return events, context_inputs, diagnostics
