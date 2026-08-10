# ruff: noqa: E402, I001

"""Issue #125 remediation tests: current Claude runtime observability.

Covers the explicit transcript taxonomy, session identity injection,
context-input contamination detection, and fail-closed behavior on the
current tested runtime format (Claude Code VSCode 2.1.226).  All fixtures are
de-identified structural mirrors of the real transcript (see
``claude_transcript_fixtures.py``); no user transcript content is committed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from _benchmark_helpers import REPO_ROOT  # noqa: F401  (wires tooling onto sys.path)

from benchmark_common import (  # type: ignore[import-not-found]
    BenchmarkError,
    load_json,
)
from claude_transcript_adapter import (  # type: ignore[import-not-found]
    parse_transcript,
    parse_transcript_file,
    verify_session_path_match,
)
from access_audit import audit  # type: ignore[import-not-found]

from claude_transcript_fixtures import (
    FIXTURE_ARM_ID,
    FIXTURE_SESSION_ID,
    agent_listing_attachment_record,
    ai_title_record,
    assistant_record,
    compact_summary_user_record,
    controlled_probe_records,
    file_attachment_record,
    file_history_delta_record,
    file_history_snapshot_record,
    last_prompt_record,
    prompt_user_record,
    queue_operation_record,
    skill_listing_attachment_record,
    summary_record,
    system_record,
    todo_reminder_attachment_record,
    tool_result_item,
    tool_use_item,
    user_record,
)

INVENTORY = (
    REPO_ROOT
    / "benchmarks"
    / "task-65-round-2-v2"
    / "inventory"
    / "prior-benchmark-contamination-inventory.json"
)

FORBIDDEN_PATH = "docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json"


def _parse(
    lines: list[dict[str, object]], **kwargs: object
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    payload = [json.dumps(line) for line in lines]
    events, context_inputs, diagnostics = parse_transcript(
        payload,
        arm_id=FIXTURE_ARM_ID,
        session_id=kwargs.pop("session_id", FIXTURE_SESSION_ID),
    )
    return events, context_inputs, diagnostics


# --------------------------------------------------------------------------
# 1. user/assistant tool_use / tool_result normalize.
# --------------------------------------------------------------------------


def test_tool_use_and_tool_result_normalize() -> None:
    lines = [
        assistant_record(
            [
                {"type": "thinking", "thinking": "private"},
                tool_use_item("Read", {"file_path": "AGENTS.md"}, item_id="toolu_01"),
            ]
        ),
        user_record([tool_result_item("(scrubbed)", tool_use_id="toolu_01")]),
    ]
    events, context_inputs, diagnostics = _parse(lines)
    assert diagnostics["events"] == 2
    assert diagnostics["malformed"] == 0
    assert len(events) == 2
    tool_use = events[0]
    assert tool_use["operation"] == "tool_use"
    assert tool_use["tool"] == "read"
    assert tool_use["target"] == '{"file_path": "AGENTS.md"}'
    assert tool_use["raw_event_reference"] == "toolu_01"
    tool_result = events[1]
    assert tool_result["operation"] == "tool_result"
    assert tool_result["raw_event_reference"] == "toolu_01"
    assert all(event["session_id"] == FIXTURE_SESSION_ID for event in events)
    assert all(event["arm_id"] == FIXTURE_ARM_ID for event in events)


# --------------------------------------------------------------------------
# 2. queue-operation legal behavior (metadata, never events).
# --------------------------------------------------------------------------


def test_queue_operation_is_metadata() -> None:
    events, context_inputs, diagnostics = _parse(
        [queue_operation_record("enqueue"), queue_operation_record("dequeue")]
    )
    assert events == []
    assert context_inputs == []
    assert diagnostics["non_tool_records"] == 2
    assert diagnostics["events"] == 0
    assert diagnostics["record_types"]["queue-operation"] == 2


def test_queue_operation_unknown_operation_fails_closed() -> None:
    with pytest.raises(BenchmarkError):
        _parse([queue_operation_record("unknown-op")])


# --------------------------------------------------------------------------
# 3. ai-title / last-prompt behavior.
# --------------------------------------------------------------------------


def test_ai_title_is_metadata() -> None:
    events, context_inputs, diagnostics = _parse([ai_title_record()])
    assert events == []
    assert context_inputs == []
    assert diagnostics["non_tool_records"] == 1


def test_last_prompt_is_context_input() -> None:
    events, context_inputs, diagnostics = _parse([last_prompt_record()])
    assert events == []
    assert len(context_inputs) == 1
    assert context_inputs[0]["source_type"] == "last-prompt"
    assert context_inputs[0]["session_id"] == FIXTURE_SESSION_ID


# --------------------------------------------------------------------------
# 4. attachment classification.
# --------------------------------------------------------------------------


def test_file_attachment_is_context_input_with_full_text() -> None:
    content = "\n".join(f"synthetic line {i}" for i in range(200))
    events, context_inputs, _ = _parse([file_attachment_record(content)])
    assert events == []
    assert len(context_inputs) == 1
    context_input = context_inputs[0]
    assert context_input["source_type"] == "attachment:file"
    # Full content preserved: a forbidden identifier in the tail must not be
    # lost to a bounded-output cap.
    assert content in context_input["target"]


def test_agent_skill_todo_attachments_are_context_inputs() -> None:
    events, context_inputs, _ = _parse(
        [
            agent_listing_attachment_record(),
            skill_listing_attachment_record(),
            todo_reminder_attachment_record(),
        ]
    )
    assert events == []
    assert [c["source_type"] for c in context_inputs] == [
        "attachment:agent_listing_delta",
        "attachment:skill_listing",
        "attachment:todo_reminder",
    ]


def test_unknown_attachment_type_fails_closed() -> None:
    record = file_attachment_record("x")
    record["attachment"] = {"type": "unknown_attachment", "content": "x"}
    with pytest.raises(BenchmarkError):
        _parse([record])


# --------------------------------------------------------------------------
# 5. file-history-snapshot / delta classification.
# --------------------------------------------------------------------------


def test_file_history_snapshot_is_metadata() -> None:
    events, context_inputs, diagnostics = _parse(
        [file_history_snapshot_record(), file_history_delta_record()]
    )
    assert events == []
    assert context_inputs == []
    assert diagnostics["non_tool_records"] == 2


def test_file_history_snapshot_with_content_payload_fails_closed() -> None:
    record = file_history_snapshot_record()
    record["snapshot"] = {
        "messageId": "m-1",
        "timestamp": "t",
        "trackedFileBackups": {
            "<scrubbed>/synthetic.md": {"content": "file content must not hide here"}
        },
    }
    with pytest.raises(BenchmarkError):
        _parse([record])


# --------------------------------------------------------------------------
# 6. summary classification (top-level and isCompactSummary user records).
# --------------------------------------------------------------------------


def test_top_level_summary_is_context_input() -> None:
    events, context_inputs, _ = _parse([summary_record()])
    assert events == []
    assert len(context_inputs) == 1
    assert context_inputs[0]["source_type"] == "summary"


def test_compact_summary_user_record_is_context_input() -> None:
    events, context_inputs, _ = _parse([compact_summary_user_record()])
    assert events == []
    assert len(context_inputs) == 1
    assert context_inputs[0]["source_type"] == "summary"


# --------------------------------------------------------------------------
# 7. explicit session-id injection.
# --------------------------------------------------------------------------


def test_session_id_injected_into_all_normalized_records() -> None:
    lines = [
        queue_operation_record("enqueue"),
        assistant_record(
            [tool_use_item("Read", {"file_path": "CLAUDE.md"}, item_id="t1")]
        ),
        last_prompt_record(),
    ]
    events, context_inputs, diagnostics = _parse(lines)
    assert diagnostics["session_identity"] == {
        "session_id": FIXTURE_SESSION_ID,
        "source": "explicit-adapter-input",
        "verified": True,
    }
    assert all(event["session_id"] == FIXTURE_SESSION_ID for event in events)
    assert all(c["session_id"] == FIXTURE_SESSION_ID for c in context_inputs)


def test_session_id_is_required_no_empty_default() -> None:
    with pytest.raises(BenchmarkError):
        parse_transcript([json.dumps(assistant_record([]))], arm_id="D", session_id="")


# --------------------------------------------------------------------------
# 8. transcript/session mismatch rejected.
# --------------------------------------------------------------------------


def test_record_session_id_mismatch_fails_closed() -> None:
    record = assistant_record(
        [tool_use_item("Read", {"file_path": "CLAUDE.md"}, item_id="t1")],
        session_id="another-session-uuid",
    )
    with pytest.raises(BenchmarkError):
        _parse([record])


def test_transcript_path_stem_mismatch_fails_closed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    transcript = tmp_path / "some-other-session.jsonl"
    transcript.write_text(json.dumps(assistant_record([])), encoding="utf-8")
    with pytest.raises(BenchmarkError):
        parse_transcript_file(
            str(transcript), arm_id="D", session_id=FIXTURE_SESSION_ID
        )


def test_transcript_path_stem_match_parses(tmp_path) -> None:  # type: ignore[no-untyped-def]
    transcript = tmp_path / f"{FIXTURE_SESSION_ID}.jsonl"
    transcript.write_text(json.dumps(assistant_record([])), encoding="utf-8")
    events, context_inputs, diagnostics = parse_transcript_file(
        str(transcript), arm_id="D", session_id=FIXTURE_SESSION_ID
    )
    assert events == []
    assert context_inputs == []
    assert diagnostics["records"] == 1


def test_verify_session_path_match_mechanical_rule() -> None:
    verify_session_path_match(
        f"/home/u/.claude/projects/p/{FIXTURE_SESSION_ID}.jsonl", FIXTURE_SESSION_ID
    )
    with pytest.raises(BenchmarkError):
        verify_session_path_match("/tmp/other.jsonl", FIXTURE_SESSION_ID)


# --------------------------------------------------------------------------
# 9. unknown top-level record => fail closed.
# --------------------------------------------------------------------------


def test_unknown_top_level_record_fails_closed() -> None:
    with pytest.raises(BenchmarkError):
        _parse([{"type": "future-record-type", "sessionId": FIXTURE_SESSION_ID}])


# --------------------------------------------------------------------------
# 10. malformed tool record => fail closed.
# --------------------------------------------------------------------------


def test_malformed_tool_use_fails_closed() -> None:
    with pytest.raises(BenchmarkError):
        _parse([assistant_record([tool_use_item("Read", {}, item_id="")])])
    with pytest.raises(BenchmarkError):
        _parse([assistant_record([{"type": "tool_use", "id": "t1", "input": {}}])])


def test_malformed_tool_result_fails_closed() -> None:
    with pytest.raises(BenchmarkError):
        _parse([user_record([{"type": "tool_result", "content": "x"}])])


def test_unknown_content_item_type_fails_closed() -> None:
    with pytest.raises(BenchmarkError):
        _parse([assistant_record([{"type": "web_search_result", "content": "x"}])])


def test_malformed_line_fails_closed() -> None:
    with pytest.raises(BenchmarkError):
        parse_transcript(["not json at all"], arm_id="D", session_id=FIXTURE_SESSION_ID)


# --------------------------------------------------------------------------
# 11. controlled probe produces complete normalized events.
# --------------------------------------------------------------------------


def test_controlled_probe_full_normalization() -> None:
    events, context_inputs, diagnostics = _parse(controlled_probe_records())
    assert diagnostics["malformed"] == 0
    assert diagnostics["events"] == 8  # 4 tool_use + 4 tool_result
    targets = [event["target"] for event in events]
    assert any("CLAUDE.md" in target for target in targets)
    assert any("AGENTS.md" in target for target in targets)
    assert any("status --porcelain" in target for target in targets)
    assert any("rev-parse HEAD" in target for target in targets)
    assert all(event["session_id"] == FIXTURE_SESSION_ID for event in events)


# --------------------------------------------------------------------------
# 12. Class 2 / Class 3 contamination detectable via context-bearing records.
# --------------------------------------------------------------------------


def _audit(
    events: list[dict[str, Any]],
    context_inputs: list[dict[str, Any]],
    cross_arm_sets: list[str] | None = None,
) -> Any:
    return audit(
        events,
        load_json(INVENTORY),
        cross_arm_sets or [],
        [],
        context_inputs=context_inputs,
        capture_complete=True,
        parser_supported=True,
        audit_executed=True,
    )


def test_contamination_in_attachment_file_content_detected() -> None:
    contaminated = (
        f"synthetic fixture content\nreference: {FORBIDDEN_PATH}\nend of content"
    )
    events, context_inputs, _ = _parse([file_attachment_record(contaminated)])
    assert context_inputs, "file attachment must produce a context input"
    report = _audit(events, context_inputs)
    assert report["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
    assert report["match_count"] >= 1
    context_matches = [m for m in report["matches"] if m["kind"] == "context_input"]
    assert context_matches
    assert context_matches[0]["source_type"] == "attachment:file"


def test_contamination_in_summary_detected() -> None:
    contaminated = summary_record()
    contaminated["summary"] = f"prior-run notes mention {FORBIDDEN_PATH}"
    events, context_inputs, _ = _parse([contaminated])
    report = _audit(events, context_inputs)
    assert report["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
    assert any(
        m["kind"] == "context_input" and m["source_type"] == "summary"
        for m in report["matches"]
    )


def test_contamination_via_cross_arm_class3_in_context_input() -> None:
    class3 = "experiment/task65-v2-d-some-other-arm"
    contaminated = prompt_user_record(f"the answer lives at {class3}")
    events, context_inputs, _ = _parse([contaminated])
    assert context_inputs[0]["source_type"] == "user-prompt"
    report = _audit(events, context_inputs, cross_arm_sets=[class3])
    assert report["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
    assert any(
        m["kind"] == "context_input" and m["source_type"] == "user-prompt"
        for m in report["matches"]
    )


def test_contamination_in_last_prompt_detected() -> None:
    record = last_prompt_record()
    record["lastPrompt"] = f"continue with {FORBIDDEN_PATH}"
    events, context_inputs, _ = _parse([record])
    report = _audit(events, context_inputs)
    assert report["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"


# --------------------------------------------------------------------------
# 13. benign metadata produces no false access events.
# --------------------------------------------------------------------------


def test_benign_metadata_produces_no_false_access_events() -> None:
    lines = [
        queue_operation_record("enqueue"),
        ai_title_record(),
        file_history_snapshot_record(),
        file_history_delta_record(),
        system_record("compact_boundary"),
        system_record("api_error"),
    ]
    events, context_inputs, diagnostics = _parse(lines)
    assert events == []
    assert context_inputs == []
    assert diagnostics["events"] == 0
    assert diagnostics["context_inputs"] == 0
    assert diagnostics["non_tool_records"] == 6


def test_benign_context_inputs_no_false_matches() -> None:
    lines = [
        file_attachment_record("fully benign synthetic fixture text"),
        agent_listing_attachment_record(),
        last_prompt_record(),
        summary_record(),
    ]
    events, context_inputs, _ = _parse(lines)
    report = _audit(events, context_inputs)
    assert report["verdict"] == "PASS"
    assert report["reason"] == "zero forbidden matches"
