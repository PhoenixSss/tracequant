# ruff: noqa: E402, I001

from __future__ import annotations

import json

import pytest

from _benchmark_helpers import REPO_ROOT  # noqa: F401  (wires tooling onto sys.path)

from benchmark_common import BenchmarkError, load_json  # type: ignore[import-not-found]
from claude_transcript_adapter import (  # type: ignore[import-not-found]
    parse_record as parse_claude_record,
    parse_transcript,
)
from claude_transcript_fixtures import (
    FIXTURE_SESSION_ID,
    agent_listing_attachment_record,
    ai_title_record,
    command_permissions_attachment_record,
    compact_summary_user_record,
    file_attachment_record,
    last_prompt_record,
    prompt_user_record,
    skill_listing_attachment_record,
    summary_record,
    system_record,
    todo_reminder_attachment_record,
)
from codex_rollout_adapter import (  # type: ignore[import-not-found]
    parse_record as parse_codex_record,
    parse_rollout,
)


def test_codex_adapter_normalizes_tool_calls() -> None:
    record = {
        "type": "custom_tool_call",
        "session_id": "sess-1",
        "timestamp": "2026-08-09T12:00:00Z",
        "tool": "Bash",
        "args": {"command": "git status"},
        "raw_reference": "rollout.jsonl#42",
    }
    event = parse_codex_record(record)
    assert event is not None
    assert event["tool"] == "shell"
    assert event["operation"] == "tool_call"
    assert event["target"] == "git status"
    assert event["raw_event_reference"] == "rollout.jsonl#42"


def test_codex_adapter_output_record() -> None:
    event = parse_codex_record(
        {
            "type": "custom_tool_call_output",
            "session_id": "s",
            "timestamp": "t",
            "tool": "gh",
            "output_reference": "gh pr view 108",
            "raw_reference": "r1",
        }
    )
    assert event is not None
    assert event["tool"] == "gh"
    assert event["operation"] == "tool_call_output"
    assert event["target"] == "gh pr view 108"


def test_codex_adapter_fail_closed_unknown_record_type() -> None:
    with pytest.raises(BenchmarkError):
        parse_codex_record(
            {"type": "something_else", "session_id": "s", "timestamp": "t"}
        )


def test_codex_rollout_parse_and_meta_skip() -> None:
    lines = [
        json.dumps({"record_type": "meta", "session_id": "s"}),
        json.dumps(
            {
                "type": "custom_tool_call",
                "session_id": "s",
                "timestamp": "t",
                "tool": "Read",
                "args": {"file_path": "AGENTS.md"},
            }
        ),
    ]
    events, diagnostics = parse_rollout(lines, arm_id="A")
    assert diagnostics["records"] == 2
    assert diagnostics["non_tool_records"] == 1
    assert diagnostics["events"] == 1
    assert events[0]["arm_id"] == "A"
    assert events[0]["tool"] == "read"
    assert events[0]["target"] == "AGENTS.md"


def test_codex_rollout_fail_closed_malformed_line() -> None:
    with pytest.raises(BenchmarkError):
        parse_rollout(["not json at all"])


def test_claude_adapter_normalizes_tool_use() -> None:
    record = {
        "type": "assistant",
        "sessionId": "claude-sess",
        "timestamp": "2026-08-09T12:00:00Z",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "private"},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "Bash",
                    "input": {"command": "git log --oneline"},
                },
            ]
        },
    }
    events, context_inputs = parse_claude_record(record)
    assert len(events) == 1
    assert context_inputs == []
    event = events[0]
    assert event["tool"] == "shell"
    assert event["operation"] == "tool_use"
    assert '"command": "git log --oneline"' in event["target"]
    assert event["raw_event_reference"] == "toolu_01"
    assert event["session_id"] == "claude-sess"  # record's own sessionId


def test_claude_adapter_normalizes_tool_result() -> None:
    record = {
        "type": "user",
        "sessionId": "s",
        "timestamp": "t",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_02",
                    "content": "read docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json",
                }
            ]
        },
    }
    events, context_inputs = parse_claude_record(record)
    assert len(events) == 1 and context_inputs == []
    assert events[0]["operation"] == "tool_result"
    assert "benchmark-manifest.json" in events[0]["target"]


def test_claude_adapter_skips_known_non_tool_records() -> None:
    for record_type in (
        "queue-operation",
        "ai-title",
        "file-history-snapshot",
        "system",
        "meta",
        "isMeta",
    ):
        record: dict[str, object] = {
            "type": record_type,
            "sessionId": "s",
            "timestamp": "t",
        }
        if record_type == "queue-operation":
            record["operation"] = "enqueue"
        if record_type == "system":
            record["subtype"] = "compact_boundary"
        if record_type == "ai-title":
            record["aiTitle"] = "title"
        if record_type == "file-history-snapshot":
            record["snapshot"] = {
                "messageId": "m-1",
                "timestamp": "t",
                "trackedFileBackups": {},
            }
        events, context_inputs = parse_claude_record(record, session_id="s")
        assert events == []
        # M4: only the ai-title payload is context-capable and transfers to
        # the context-input audit; the other metadata types carry no payload.
        if record_type == "ai-title":
            assert [c["source_type"] for c in context_inputs] == ["ai-title"]
            assert context_inputs[0]["target"] == "title"
        else:
            assert context_inputs == []


def test_claude_adapter_fail_closed_unknown_record_type() -> None:
    with pytest.raises(BenchmarkError):
        parse_claude_record(
            {"type": "unknown_type", "sessionId": "s", "timestamp": "t"}
        )


def test_claude_adapter_fail_closed_unknown_content_item() -> None:
    with pytest.raises(BenchmarkError):
        parse_claude_record(
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "t",
                "message": {"content": [{"type": "web_search_result", "content": "x"}]},
            }
        )


def test_claude_transcript_parse() -> None:
    lines = [
        json.dumps(
            {
                "type": "system",
                "sessionId": "s",
                "timestamp": "t",
                "subtype": "compact_boundary",
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "t",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "AGENTS.md"},
                        }
                    ]
                },
            }
        ),
    ]
    events, context_inputs, diagnostics = parse_transcript(
        lines, arm_id="D", session_id="s"
    )
    assert context_inputs == []
    assert diagnostics["records"] == 2
    assert diagnostics["non_tool_records"] == 1
    assert diagnostics["events"] == 1
    assert events[0]["arm_id"] == "D"
    assert events[0]["tool"] == "read"
    assert events[0]["session_id"] == "s"


def test_context_input_source_types_have_no_known_drift_with_schema() -> None:
    # Every adapter context-input emission path, exercised through the REAL
    # adapter over the observed-transcript fixtures.  The emitted source_type
    # set must equal the context-input schema enum exactly: no adapter type
    # may be missing from the schema, and no schema enum value may be stale
    # (a type the adapter never emits).
    fixture_records: list[tuple[str, dict[str, object]]] = [
        ("attachment:file", file_attachment_record("<scrubbed content>")),
        (
            "attachment:agent_listing_delta",
            agent_listing_attachment_record(),
        ),
        ("attachment:skill_listing", skill_listing_attachment_record()),
        (
            "attachment:command_permissions",
            command_permissions_attachment_record(),
        ),
        ("attachment:todo_reminder", todo_reminder_attachment_record()),
        ("summary", summary_record()),
        ("summary", compact_summary_user_record()),
        ("user-prompt", prompt_user_record("<scrubbed prompt>")),
        ("last-prompt", last_prompt_record()),
        ("ai-title", ai_title_record()),
        ("system:compact_boundary", system_record("compact_boundary")),
        ("system:api_error", system_record("api_error")),
    ]
    emitted: set[str] = set()
    for expected, record in fixture_records:
        _events, context_inputs = parse_claude_record(
            record, session_id=FIXTURE_SESSION_ID
        )
        types = {c["source_type"] for c in context_inputs}
        assert types == {expected}, f"fixture emitted {types}, expected {expected!r}"
        emitted.update(types)
    schema = load_json(
        REPO_ROOT / "benchmarks/task-65-round-2-v2/schemas/context-input.schema.json"
    )
    schema_types = set(schema["properties"]["source_type"]["enum"])
    assert emitted == schema_types, (
        "adapter/schema source-type drift: "
        f"adapter-only={sorted(emitted - schema_types)} "
        f"schema-only={sorted(schema_types - emitted)}"
    )
