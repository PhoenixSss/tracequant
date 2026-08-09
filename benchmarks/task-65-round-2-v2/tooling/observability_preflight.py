"""Observability preflight (six checks) for task-65-round-2-v2.

Executed before a Formal Arm starts.  Any failed check yields
``BENCHMARK OBSERVABILITY NOT VERIFIED`` and the Formal Delivery must not
start.  The six checks:

1. session_identity_resolvable
2. transcript_rollout_source_locatable
3. capture_active_before_formal_work
4. archive_destination_isolated
5. parser_supports_observed_record_format
6. controlled_test_tool_call_captured_and_normalizable

The checks are mechanical probes over a preflight configuration document;
they never interpret workflow semantics.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchmark_common import BenchmarkError, gate, load_json, validate_basic

PREFLIGHT_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "arm_id",
        "session_identity",
        "transcript_rollout_source",
        "capture_active",
        "archive_destination",
        "parser_record_formats",
        "controlled_test_tool_call",
    ],
    "properties": {
        "arm_id": {"type": "string"},
        "session_identity": {"type": "object"},
        "transcript_rollout_source": {"type": "object"},
        "capture_active": {"type": "boolean"},
        "archive_destination": {"type": "string"},
        "parser_record_formats": {"type": "array", "items": {"type": "string"}},
        "controlled_test_tool_call": {"type": "object"},
    },
    "additionalProperties": True,
}

KNOWN_RECORD_FORMATS: frozenset[str] = frozenset(
    {
        "codex:custom_tool_call",
        "codex:custom_tool_call_output",
        "claude:tool_use",
        "claude:tool_result",
    }
)


def _check(name: str, ok: bool, detail: str | None = None) -> dict[str, Any]:
    return gate(name, "pass" if ok else "fail", detail)


def run_preflight(config: dict[str, Any]) -> dict[str, Any]:
    """Run the six observability checks against ``config``."""
    checks: list[dict[str, Any]] = []

    session = config.get("session_identity")
    checks.append(
        _check(
            "session_identity_resolvable",
            isinstance(session, dict)
            and bool(session.get("session_id"))
            and bool(session.get("arm_id")),
            None
            if isinstance(session, dict)
            and bool(session.get("session_id"))
            and bool(session.get("arm_id"))
            else "session_id/arm_id missing or unresolved",
        )
    )

    source = config.get("transcript_rollout_source")
    checks.append(
        _check(
            "transcript_rollout_source_locatable",
            isinstance(source, dict)
            and bool(source.get("kind") in {"codex_rollout", "claude_transcript"})
            and bool(source.get("location")),
            None
            if isinstance(source, dict)
            and bool(source.get("kind") in {"codex_rollout", "claude_transcript"})
            and bool(source.get("location"))
            else "source kind/location missing or unknown",
        )
    )

    checks.append(
        _check(
            "capture_active_before_formal_work",
            bool(config.get("capture_active")),
            None
            if config.get("capture_active")
            else "capture must be active before formal Agent work starts",
        )
    )

    archive = str(config.get("archive_destination", ""))
    arm_id = str(config.get("arm_id", ""))
    archive_isolated = (
        bool(archive)
        and arm_id in archive
        and "fixture-store" not in archive
        and "comparison" not in archive
    )
    checks.append(
        _check(
            "archive_destination_isolated",
            archive_isolated,
            None
            if archive_isolated
            else "archive destination must be isolated per Arm/session",
        )
    )

    formats = [str(item) for item in config.get("parser_record_formats", [])]
    checks.append(
        _check(
            "parser_supports_observed_record_format",
            bool(formats) and all(item in KNOWN_RECORD_FORMATS for item in formats),
            None
            if bool(formats) and all(item in KNOWN_RECORD_FORMATS for item in formats)
            else f"unsupported record formats: {formats}",
        )
    )

    controlled = config.get("controlled_test_tool_call")
    captured = controlled and bool(controlled.get("captured"))
    normalized = controlled and bool(controlled.get("normalized_event"))
    checks.append(
        _check(
            "controlled_test_tool_call_captured_and_normalizable",
            bool(captured) and bool(normalized),
            None
            if bool(captured) and bool(normalized)
            else "controlled test tool call not captured or not normalizable",
        )
    )

    failed = [check for check in checks if check["status"] == "fail"]
    verified = not failed
    return {
        "protocol_identity": "task-65-round-2-v2",
        "arm_id": config.get("arm_id", ""),
        "verified": verified,
        "verdict": "BENCHMARK OBSERVABILITY NOT VERIFIED"
        if not verified
        else "OBSERVABILITY VERIFIED",
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the six-item benchmark observability preflight."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", help="optional output path")
    args = parser.parse_args(argv)

    try:
        config = load_json(Path(args.config))
        validate_basic(config, PREFLIGHT_CONFIG_SCHEMA, "config")
        report = run_preflight(config)
    except BenchmarkError as exc:
        print(f"OBSERVABILITY PREFLIGHT FAIL CLOSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    sys.exit(main())
