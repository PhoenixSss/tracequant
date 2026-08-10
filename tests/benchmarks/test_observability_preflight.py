# ruff: noqa: E402, I001

from __future__ import annotations

from typing import Any

from observability_preflight import run_preflight  # type: ignore[import-not-found]


def _good_config() -> dict[str, Any]:
    return {
        "arm_id": "C",
        "session_identity": {"session_id": "sess-c-1", "arm_id": "C"},
        "transcript_rollout_source": {
            "kind": "codex_rollout",
            "location": ".agents/rollout.local/task-65-round-2-v2/C",
        },
        "capture_active": True,
        "archive_destination": ".agents/evidence.local/task-65-round-2-v2/C",
        "parser_record_formats": [
            "codex:custom_tool_call",
            "codex:custom_tool_call_output",
        ],
        "controlled_test_tool_call": {
            "captured": True,
            "normalized_event": {"target": "git status"},
        },
    }


def test_preflight_all_checks_pass() -> None:
    report = run_preflight(_good_config())
    assert report["verified"] is True
    assert report["verdict"] == "OBSERVABILITY VERIFIED"
    assert len(report["checks"]) == 6
    assert all(c["status"] == "pass" for c in report["checks"])


def test_preflight_missing_session_identity_fails() -> None:
    config = _good_config()
    del config["session_identity"]
    report = run_preflight(config)
    assert report["verified"] is False
    assert report["verdict"] == "BENCHMARK OBSERVABILITY NOT VERIFIED"
    assert any(
        c["name"] == "session_identity_resolvable" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_capture_not_active_fails() -> None:
    config = _good_config()
    config["capture_active"] = False
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "capture_active_before_formal_work" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_archive_destination_must_be_isolated() -> None:
    config = _good_config()
    config["archive_destination"] = ".agents/evidence.local/shared"
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "archive_destination_isolated" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_unsupported_parser_format_fails() -> None:
    config = _good_config()
    config["parser_record_formats"] = ["codex:something_new"]
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "parser_supports_observed_record_format" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_controlled_test_call_required() -> None:
    config = _good_config()
    config["controlled_test_tool_call"] = {"captured": False, "normalized_event": {}}
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "controlled_test_tool_call_captured_and_normalizable"
        and c["status"] == "fail"
        for c in report["checks"]
    )


def test_preflight_supports_current_claude_record_formats(tmp_path) -> None:  # type: ignore[no-untyped-def]
    session_id = "sess-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    transcript = tmp_path / f"{session_id}.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    config = {
        "arm_id": "D",
        "session_identity": {"session_id": session_id, "arm_id": "D"},
        "transcript_rollout_source": {
            "kind": "claude_transcript",
            "location": str(transcript),
        },
        "capture_active": True,
        "archive_destination": ".agents/evidence.local/task-65-round-2-v2/D",
        "parser_record_formats": [
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
        "controlled_test_tool_call": {
            "captured": True,
            "normalized_event": {"target": "read CLAUDE.md"},
        },
    }
    report = run_preflight(config)
    assert report["verified"] is True
    assert all(c["status"] == "pass" for c in report["checks"])


def test_preflight_claude_session_path_mismatch_fails(tmp_path) -> None:  # type: ignore[no-untyped-def]
    transcript = tmp_path / "other-session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    config = {
        "arm_id": "D",
        "session_identity": {
            "session_id": "sess-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "arm_id": "D",
        },
        "transcript_rollout_source": {
            "kind": "claude_transcript",
            "location": str(transcript),
        },
        "capture_active": True,
        "archive_destination": ".agents/evidence.local/task-65-round-2-v2/D",
        "parser_record_formats": ["claude:tool_use", "claude:tool_result"],
        "controlled_test_tool_call": {
            "captured": True,
            "normalized_event": {"target": "read CLAUDE.md"},
        },
    }
    report = run_preflight(config)
    assert report["verified"] is False
    assert any(
        c["name"] == "session_identity_resolvable" and c["status"] == "fail"
        for c in report["checks"]
    )
