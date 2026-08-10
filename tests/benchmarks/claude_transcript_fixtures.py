"""De-identified structural fixtures for the Claude transcript adapter.

Derived from the real current-tested-runtime transcript (Claude Code VSCode
2.1.226, project transcript for session ``65864426-...``, 2026-08-10): the
record shapes, key sets, and type discriminator values mirror the observed
format exactly, while all content values are scrubbed placeholders.  No user
transcript content is committed.

Fixture provenance (observed shape -> fixture):

- ``queue-operation``: {type, operation, timestamp, sessionId}
- ``ai-title``: {type, aiTitle, sessionId}
- ``attachment`` ``file``: {attachment: {type, content: {type, file: {filePath,
  content, numLines, startLine, totalLines}}, displayPath, filename}}
- ``attachment`` ``agent_listing_delta``: {attachment: {type, addedLines,
  addedTypes, isInitial, removedTypes, showConcurrencyNote}}
- ``attachment`` ``skill_listing``: {attachment: {type, content, isInitial,
  names, skillCount}}
- ``attachment`` ``todo_reminder``: {attachment: {type, content: [...], itemCount}}
- ``file-history-snapshot``: {type, isSnapshotUpdate, messageId, snapshot:
  {messageId, timestamp, trackedFileBackups: {path: {backupFileName, version,
  backupTime, realParentDir}}}}
- ``file-history-delta``: {type, backup, messageId, snapshotMessageId,
  trackingPath, timestamp}
- ``last-prompt``: {type, lastPrompt, leafUuid, sessionId}
- ``system``: {type, subtype, ...} with subtypes api_error / compact_boundary
- ``assistant``: {type, message: {content: [thinking, tool_use, ...]}, ...}
- ``user``: {type, message: {content: [tool_result, ...]}, ...}
- user ``isCompactSummary``: message.content is a plain summary string
- top-level ``summary``: {type, summary, ...}
"""

from __future__ import annotations

FIXTURE_SESSION_ID = "sess-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
FIXTURE_ARM_ID = "D"


def user_record(
    content_items: list[dict[str, object]],
    *,
    session_id: str = FIXTURE_SESSION_ID,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "type": "user",
        "sessionId": session_id,
        "timestamp": "2026-08-10T14:02:52.337Z",
        "message": {"role": "user", "content": content_items},
    }
    if extra:
        record.update(extra)
    return record


def assistant_record(
    content_items: list[dict[str, object]],
    *,
    session_id: str = FIXTURE_SESSION_ID,
) -> dict[str, object]:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": "2026-08-10T14:02:57.843Z",
        "message": {"role": "assistant", "content": content_items},
    }


def tool_use_item(
    name: str, input_: dict[str, object], *, item_id: str
) -> dict[str, object]:
    return {"type": "tool_use", "id": item_id, "name": name, "input": input_}


def tool_result_item(content: object, *, tool_use_id: str) -> dict[str, object]:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


def queue_operation_record(
    operation: str, *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    return {
        "type": "queue-operation",
        "operation": operation,
        "timestamp": "2026-08-10T14:02:52.337Z",
        "sessionId": session_id,
    }


def ai_title_record(*, session_id: str = FIXTURE_SESSION_ID) -> dict[str, object]:
    return {
        "type": "ai-title",
        "aiTitle": "<scrubbed session title>",
        "sessionId": session_id,
    }


def file_attachment_record(
    file_content: str,
    *,
    file_path: str = "<scrubbed>/synthetic-fixture.txt",
    session_id: str = FIXTURE_SESSION_ID,
) -> dict[str, object]:
    return {
        "type": "attachment",
        "sessionId": session_id,
        "timestamp": "2026-08-10T14:02:52.353Z",
        "attachment": {
            "type": "file",
            "filename": file_path,
            "displayPath": file_path,
            "content": {
                "type": "text",
                "file": {
                    "filePath": file_path,
                    "content": file_content,
                    "numLines": file_content.count("\n") + 1,
                    "startLine": 1,
                    "totalLines": file_content.count("\n") + 1,
                },
            },
        },
    }


def agent_listing_attachment_record(
    *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    return {
        "type": "attachment",
        "sessionId": session_id,
        "timestamp": "2026-08-10T14:02:52.353Z",
        "attachment": {
            "type": "agent_listing_delta",
            "addedLines": ["<scrubbed agent line 1>", "<scrubbed agent line 2>"],
            "addedTypes": ["claude", "claude-code-guide"],
            "removedTypes": [],
            "isInitial": True,
            "showConcurrencyNote": True,
        },
    }


def skill_listing_attachment_record(
    *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    return {
        "type": "attachment",
        "sessionId": session_id,
        "timestamp": "2026-08-10T14:02:52.353Z",
        "attachment": {
            "type": "skill_listing",
            "content": "<scrubbed skill listing content>",
            "skillCount": 14,
            "isInitial": True,
            "names": ["<scrubbed-skill-a>", "<scrubbed-skill-b>"],
        },
    }


def todo_reminder_attachment_record(
    *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    return {
        "type": "attachment",
        "sessionId": session_id,
        "timestamp": "2026-08-10T14:02:52.353Z",
        "attachment": {
            "type": "todo_reminder",
            "content": [
                {
                    "content": "<scrubbed todo 1>",
                    "status": "in_progress",
                    "activeForm": "<scrubbed>",
                },
                {
                    "content": "<scrubbed todo 2>",
                    "status": "pending",
                    "activeForm": "<scrubbed>",
                },
            ],
            "itemCount": 2,
        },
    }


def file_history_snapshot_record(
    *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    return {
        "type": "file-history-snapshot",
        "isSnapshotUpdate": False,
        "messageId": "m-11111111-2222-4333-8444-555555555555",
        "snapshot": {
            "messageId": "m-11111111-2222-4333-8444-555555555555",
            "timestamp": "2026-08-10T14:02:52.353Z",
            "trackedFileBackups": {
                "<scrubbed>/synthetic-tracked.md": {
                    "backupFileName": "<scrubbed>@v1",
                    "version": 1,
                    "backupTime": "2026-08-10T14:02:52.353Z",
                    "realParentDir": "<scrubbed>/synthetic",
                }
            },
        },
    }


def file_history_delta_record(
    *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    return {
        "type": "file-history-delta",
        "messageId": "m-11111111-2222-4333-8444-555555555555",
        "snapshotMessageId": "m-11111111-2222-4333-8444-555555555555",
        "trackingPath": "<scrubbed>/synthetic-tracked.md",
        "backup": {
            "backupFileName": None,
            "version": 1,
            "backupTime": "2026-08-10T14:20:32.389Z",
            "realParentDir": "<scrubbed>/synthetic",
        },
        "timestamp": "2026-08-10T14:20:32.389Z",
    }


def last_prompt_record(*, session_id: str = FIXTURE_SESSION_ID) -> dict[str, object]:
    return {
        "type": "last-prompt",
        "lastPrompt": "<scrubbed user prompt echo>",
        "leafUuid": "u-11111111-2222-4333-8444-555555555555",
        "sessionId": session_id,
    }


def system_record(
    subtype: str, *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    if subtype == "compact_boundary":
        return {
            "type": "system",
            "subtype": "compact_boundary",
            "content": "Conversation compacted",
            "isMeta": False,
            "level": "info",
            "timestamp": "2026-08-10T14:24:42.072Z",
            "sessionId": session_id,
            "compactMetadata": {
                "trigger": "auto",
                "preservedSegment": {
                    "headUuid": "u-11111111-2222-4333-8444-555555555555"
                },
            },
        }
    return {
        "type": "system",
        "subtype": "api_error",
        "level": "error",
        "source": "request_retry",
        "retryAttempt": 1,
        "maxRetries": 10,
        "retryInMs": 506,
        "error": {"message": "<scrubbed error>", "isNetworkDown": False},
        "timestamp": "2026-08-10T14:20:33.000Z",
        "sessionId": session_id,
    }


def summary_record(*, session_id: str = FIXTURE_SESSION_ID) -> dict[str, object]:
    return {
        "type": "summary",
        "summary": "<scrubbed compaction summary text>",
        "timestamp": "2026-08-10T14:24:42.072Z",
        "sessionId": session_id,
    }


def compact_summary_user_record(
    *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    return {
        "type": "user",
        "sessionId": session_id,
        "timestamp": "2026-08-10T14:24:42.072Z",
        "isCompactSummary": True,
        "message": {
            "role": "user",
            "content": (
                "This session is being continued from a previous conversation. "
                "<scrubbed summary text>"
            ),
        },
    }


def prompt_user_record(
    prompt_text: str, *, session_id: str = FIXTURE_SESSION_ID
) -> dict[str, object]:
    return user_record([{"type": "text", "text": prompt_text}], session_id=session_id)


def controlled_probe_records(
    *, session_id: str = FIXTURE_SESSION_ID
) -> list[dict[str, object]]:
    """The controlled test tool calls, in real transcript shape."""
    return [
        assistant_record(
            [
                {"type": "thinking", "thinking": "<scrubbed>"},
                tool_use_item(
                    "Read", {"file_path": "CLAUDE.md"}, item_id="call_00_read_claude"
                ),
            ],
            session_id=session_id,
        ),
        user_record(
            [
                tool_result_item(
                    "(scrubbed CLAUDE.md content)", tool_use_id="call_00_read_claude"
                )
            ],
            session_id=session_id,
        ),
        assistant_record(
            [
                tool_use_item(
                    "Read", {"file_path": "AGENTS.md"}, item_id="call_01_read_agents"
                )
            ],
            session_id=session_id,
        ),
        user_record(
            [
                tool_result_item(
                    "(scrubbed AGENTS.md content)", tool_use_id="call_01_read_agents"
                )
            ],
            session_id=session_id,
        ),
        assistant_record(
            [
                tool_use_item(
                    "Bash",
                    {"command": "git status --porcelain"},
                    item_id="call_02_git_status",
                )
            ],
            session_id=session_id,
        ),
        user_record(
            [tool_result_item("(scrubbed status)", tool_use_id="call_02_git_status")],
            session_id=session_id,
        ),
        assistant_record(
            [
                tool_use_item(
                    "Bash",
                    {"command": "git rev-parse HEAD"},
                    item_id="call_03_git_revparse",
                )
            ],
            session_id=session_id,
        ),
        user_record(
            [tool_result_item("(scrubbed sha)", tool_use_id="call_03_git_revparse")],
            session_id=session_id,
        ),
    ]
