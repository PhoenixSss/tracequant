# ruff: noqa: E402, I001

from __future__ import annotations

import json
from typing import Any

from _benchmark_helpers import REPO_ROOT  # noqa: F401  (wires tooling onto sys.path)

from observability_preflight import run_preflight  # type: ignore[import-not-found]

from claude_transcript_fixtures import (
    FIXTURE_SESSION_ID,
    ai_title_record,
    assistant_record,
    command_permissions_attachment_record,
    file_attachment_record,
    queue_operation_record,
    summary_record,
    tool_use_item,
)


def _write_transcript(tmp_path, records: list[dict[str, object]]) -> str:  # type: ignore[no-untyped-def]
    transcript = tmp_path / f"{FIXTURE_SESSION_ID}.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    return str(transcript)


def _claude_config(transcript: str, formats: list[str]) -> dict[str, Any]:
    return {
        "arm_id": "D",
        "session_identity": {"session_id": FIXTURE_SESSION_ID, "arm_id": "D"},
        "transcript_rollout_source": {
            "kind": "claude_transcript",
            "location": transcript,
        },
        "capture_active": True,
        # M2: the archive destination must explicitly carry the current arm
        # (D) and the current session as path components.
        "archive_destination": (
            f".agents/evidence.local/task-65-round-2-v2/D/{FIXTURE_SESSION_ID}"
        ),
        "parser_record_formats": formats,
        # M3: the controlled probe is the EXPECTED normalized event; the
        # check finds it mechanically in the real parser's events.
        "controlled_test_tool_call": {
            "tool": "read",
            "operation": "tool_use",
            "target_predicate": "CLAUDE.md",
        },
    }


def _good_codex_config(tmp_path) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    rollout_dir = tmp_path / "rollout"
    rollout_dir.mkdir()
    (rollout_dir / "rollout.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "custom_tool_call",
                        "timestamp": "2026-08-10T14:02:52.337Z",
                        "session_id": "sess-c-1",
                        "tool": "Bash",
                        "args": {"command": "git status"},
                    }
                ),
                json.dumps(
                    {
                        "type": "custom_tool_call_output",
                        "timestamp": "2026-08-10T14:02:53.000Z",
                        "session_id": "sess-c-1",
                        "tool": "Bash",
                        "output_reference": "(scrubbed)",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "arm_id": "C",
        "session_identity": {"session_id": "sess-c-1", "arm_id": "C"},
        "transcript_rollout_source": {
            "kind": "codex_rollout",
            "location": str(rollout_dir),
        },
        "capture_active": True,
        "archive_destination": ".agents/evidence.local/task-65-round-2-v2/C/sess-c-1",
        "parser_record_formats": [
            "codex:custom_tool_call",
            "codex:custom_tool_call_output",
        ],
        "controlled_test_tool_call": {
            "tool": "shell",
            "operation": "tool_call",
            "target_predicate": "git status",
        },
    }


def test_preflight_all_checks_pass(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = run_preflight(_good_codex_config(tmp_path))
    assert report["verified"] is True
    assert report["verdict"] == "OBSERVABILITY VERIFIED"
    assert len(report["checks"]) == 6
    assert all(c["status"] == "pass" for c in report["checks"])


def test_preflight_missing_session_identity_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _good_codex_config(tmp_path)
    del config["session_identity"]
    report = run_preflight(config)
    assert report["verified"] is False
    assert report["verdict"] == "BENCHMARK OBSERVABILITY NOT VERIFIED"
    assert any(
        c["name"] == "session_identity_resolvable" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_capture_not_active_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _good_codex_config(tmp_path)
    config["capture_active"] = False
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "capture_active_before_formal_work" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_archive_destination_must_be_isolated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _good_codex_config(tmp_path)
    config["archive_destination"] = ".agents/evidence.local/shared"
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "archive_destination_isolated" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_unsupported_parser_format_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _good_codex_config(tmp_path)
    config["parser_record_formats"] = ["codex:something_new"]
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "parser_supports_observed_record_format" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_controlled_test_call_required(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _good_codex_config(tmp_path)
    config["controlled_test_tool_call"] = {}
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "controlled_test_tool_call_captured_and_normalizable"
        and c["status"] == "fail"
        for c in report["checks"]
    )


# --------------------------------------------------------------------------
# M2 remediation: archive destination isolation (path-component semantics).
# --------------------------------------------------------------------------


def test_preflight_archive_wrong_arm_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _good_codex_config(tmp_path)  # arm C, session sess-c-1
    config["archive_destination"] = (
        ".agents/evidence.local/task-65-round-2-v2/D/sess-c-1"
    )
    report = run_preflight(config)
    assert report["verified"] is False
    check = next(
        c for c in report["checks"] if c["name"] == "archive_destination_isolated"
    )
    assert check["status"] == "fail"
    assert "arm" in str(check.get("detail", ""))


def test_preflight_archive_wrong_session_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _good_codex_config(tmp_path)  # session sess-c-1
    config["archive_destination"] = (
        ".agents/evidence.local/task-65-round-2-v2/C/some-other-session"
    )
    report = run_preflight(config)
    assert report["verified"] is False
    check = next(
        c for c in report["checks"] if c["name"] == "archive_destination_isolated"
    )
    assert check["status"] == "fail"
    assert "session" in str(check.get("detail", ""))


def test_preflight_archive_fixture_store_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    config = _good_codex_config(tmp_path)
    config["archive_destination"] = (
        ".agents/benchmark-fixtures.local/arm-c-smoke/sess-c-1"
    )
    report = run_preflight(config)
    assert report["verified"] is False
    check = next(
        c for c in report["checks"] if c["name"] == "archive_destination_isolated"
    )
    assert check["status"] == "fail"
    assert "fixture store" in str(check.get("detail", ""))


def test_preflight_archive_generic_arm_substring_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Component equality, never loose substring: a component like
    # "arm-d-something" must NOT satisfy arm D.
    config = _good_codex_config(tmp_path)
    config["arm_id"] = "D"
    config["archive_destination"] = (
        ".agents/evidence.local/task-65-round-2-v2/arm-d-something/sess-c-1"
    )
    report = run_preflight(config)
    assert report["verified"] is False
    check = next(
        c for c in report["checks"] if c["name"] == "archive_destination_isolated"
    )
    assert check["status"] == "fail"
    assert "arm" in str(check.get("detail", ""))


# --------------------------------------------------------------------------
# M3 remediation: controlled probe found in the REAL parser's events.
# --------------------------------------------------------------------------


def test_preflight_controlled_probe_not_in_transcript_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Valid probe spec, but the actual transcript carries no such normalized
    # event -> check 6 FAIL (the probe must be mechanically found, never
    # assumed).
    transcript = _write_transcript(
        tmp_path, [queue_operation_record("enqueue"), ai_title_record()]
    )
    config = _claude_config(transcript, ["claude:queue-operation", "claude:ai-title"])
    report = run_preflight(config)
    assert report["verified"] is False
    check = next(
        c
        for c in report["checks"]
        if c["name"] == "controlled_test_tool_call_captured_and_normalizable"
    )
    assert check["status"] == "fail"
    assert "no normalized event" in str(check.get("detail", ""))


def test_preflight_fake_capture_assertion_is_not_trusted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The old-style config asserted "captured": true / "normalized_event"
    # without any probe in the real transcript.  The config assertion is
    # never trusted: the spec fields are missing and the check FAILs.
    transcript = _write_transcript(tmp_path, [queue_operation_record("enqueue")])
    config = _claude_config(transcript, ["claude:queue-operation"])
    config["controlled_test_tool_call"] = {
        "captured": True,
        "normalized_event": {"target": "read CLAUDE.md"},
    }
    report = run_preflight(config)
    assert report["verified"] is False
    check = next(
        c
        for c in report["checks"]
        if c["name"] == "controlled_test_tool_call_captured_and_normalizable"
    )
    assert check["status"] == "fail"
    assert "tool" in str(check.get("detail", ""))


def test_preflight_controlled_probe_wrong_target_predicate_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The transcript's controlled probe is Read CLAUDE.md; declaring a
    # different target predicate must FAIL (wrong predicate -> no match).
    transcript = _write_transcript(
        tmp_path,
        [
            assistant_record(
                [tool_use_item("Read", {"file_path": "CLAUDE.md"}, item_id="t1")]
            )
        ],
    )
    config = _claude_config(transcript, ["claude:tool_use", "claude:tool_result"])
    config["controlled_test_tool_call"] = {
        "tool": "read",
        "operation": "tool_use",
        "target_predicate": "AGENTS.md",
    }
    report = run_preflight(config)
    assert report["verified"] is False
    check = next(
        c
        for c in report["checks"]
        if c["name"] == "controlled_test_tool_call_captured_and_normalizable"
    )
    assert check["status"] == "fail"
    assert "no normalized event" in str(check.get("detail", ""))


def test_preflight_controlled_probe_session_mismatch_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A normalized probe event exists but carries a DIFFERENT session
    # identity: the current session identity must be part of the match.
    transcript = _write_transcript(
        tmp_path,
        [
            assistant_record(
                [tool_use_item("Read", {"file_path": "CLAUDE.md"}, item_id="t1")],
                session_id="another-session-uuid",
            )
        ],
    )
    config = _claude_config(transcript, ["claude:tool_use"])
    report = run_preflight(config)
    # The record's own sessionId conflicts with the resolved identity, so the
    # full parser rejects it (check 5 FAIL); check 6 additionally cannot find
    # the probe under the current session identity.
    assert report["verified"] is False
    probe_check = next(
        c
        for c in report["checks"]
        if c["name"] == "controlled_test_tool_call_captured_and_normalizable"
    )
    assert probe_check["status"] == "fail"


def test_preflight_supports_current_claude_record_formats(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A real supported transcript (all observed record types incl. the
    # command_permissions attachment subtype) passes the full parser probe.
    transcript = _write_transcript(
        tmp_path,
        [
            queue_operation_record("enqueue"),
            ai_title_record(),
            file_attachment_record("benign synthetic fixture text"),
            command_permissions_attachment_record(),
            summary_record(),
            assistant_record(
                [tool_use_item("Read", {"file_path": "CLAUDE.md"}, item_id="t1")]
            ),
        ],
    )
    config = _claude_config(
        transcript,
        [
            "claude:tool_use",
            "claude:tool_result",
            "claude:queue-operation",
            "claude:attachment",
            "claude:file-history-snapshot",
            "claude:ai-title",
            "claude:last-prompt",
            "claude:system",
            "claude:summary",
        ],
    )
    report = run_preflight(config)
    assert report["verified"] is True
    assert all(c["status"] == "pass" for c in report["checks"])


def test_preflight_full_parser_rejects_unknown_top_level_record(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Declared formats are all adapter-supported, but the transcript carries a
    # future/unknown top-level record: the REAL parser cannot consume it, so
    # item 5 FAILs (allowlist-only checking would have passed this).
    transcript = _write_transcript(
        tmp_path,
        [
            queue_operation_record("enqueue"),
            {"type": "future-record-type", "sessionId": FIXTURE_SESSION_ID},
        ],
    )
    config = _claude_config(transcript, ["claude:queue-operation", "claude:user"])
    report = run_preflight(config)
    assert report["verified"] is False
    assert report["verdict"] == "BENCHMARK OBSERVABILITY NOT VERIFIED"
    parser_check = next(
        c
        for c in report["checks"]
        if c["name"] == "parser_supports_observed_record_format"
    )
    assert parser_check["status"] == "fail"
    assert "future-record-type" in str(parser_check.get("detail", ""))


def test_preflight_full_parser_rejects_unsupported_attachment_subtype(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The transcript carries an attachment subtype the adapter has never
    # observed: item 5 FAILs via the real parser (the exact false-positive
    # class fixed by the remediation: preflight PASS while full parse FAILs).
    transcript = _write_transcript(
        tmp_path,
        [
            queue_operation_record("enqueue"),
            command_permissions_attachment_record(),
            {
                "type": "attachment",
                "sessionId": FIXTURE_SESSION_ID,
                "timestamp": "t",
                "attachment": {"type": "future_attachment_subtype", "content": "x"},
            },
        ],
    )
    config = _claude_config(transcript, ["claude:attachment", "claude:queue-operation"])
    report = run_preflight(config)
    assert report["verified"] is False
    parser_check = next(
        c
        for c in report["checks"]
        if c["name"] == "parser_supports_observed_record_format"
    )
    assert parser_check["status"] == "fail"
    assert "future_attachment_subtype" in str(parser_check.get("detail", ""))


def test_preflight_full_parser_rejects_malformed_command_permissions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # allowedTools must be a string list; a structurally invalid record fails
    # the real parser -> item 5 FAIL (never a silent skip).
    record = command_permissions_attachment_record()
    record["attachment"] = {"type": "command_permissions", "allowedTools": [123]}
    transcript = _write_transcript(tmp_path, [record])
    config = _claude_config(transcript, ["claude:attachment"])
    report = run_preflight(config)
    assert report["verified"] is False
    parser_check = next(
        c
        for c in report["checks"]
        if c["name"] == "parser_supports_observed_record_format"
    )
    assert parser_check["status"] == "fail"
    assert "allowedTools" in str(parser_check.get("detail", ""))


def test_preflight_parser_probe_matches_full_parse_outcome(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Producer/parser/preflight integration regression: the full transcript
    # parse and preflight item 5 always agree on the same source.  A
    # parseable transcript -> item 5 PASS; adding one unsupported record ->
    # both the full parse and item 5 FAIL.
    from claude_transcript_adapter import parse_transcript  # type: ignore[import-not-found]

    supported = [
        queue_operation_record("enqueue"),
        command_permissions_attachment_record(),
        assistant_record(
            [tool_use_item("Read", {"file_path": "CLAUDE.md"}, item_id="t1")]
        ),
    ]
    transcript = _write_transcript(tmp_path, supported)
    config = _claude_config(transcript, ["claude:attachment", "claude:user"])
    report = run_preflight(config)
    assert report["verified"] is True
    payload = [json.dumps(record) for record in supported]
    parse_transcript(payload, arm_id="D", session_id=FIXTURE_SESSION_ID)  # no error

    unsupported = supported + [
        {"type": "future-record-type", "sessionId": FIXTURE_SESSION_ID}
    ]
    bad_transcript = _write_transcript(tmp_path, unsupported)
    report = run_preflight(_claude_config(bad_transcript, ["claude:attachment"]))
    assert report["verified"] is False
    parser_check = next(
        c
        for c in report["checks"]
        if c["name"] == "parser_supports_observed_record_format"
    )
    assert parser_check["status"] == "fail"


def test_preflight_claude_session_path_mismatch_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The transcript basename stem does not match the resolved session
    # identity: check 1 FAILs even though the records themselves are parseable
    # (check 5 stays the parser-probe's verdict, not a duplicate path check).
    transcript = tmp_path / "other-session.jsonl"
    transcript.write_text(
        json.dumps(
            assistant_record([tool_use_item("Read", {"file_path": "x"}, item_id="t1")])
        ),
        encoding="utf-8",
    )
    config = _claude_config(transcript, ["claude:tool_use", "claude:tool_result"])
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "session_identity_resolvable" and c["status"] == "fail"
        for c in report["checks"]
    )
