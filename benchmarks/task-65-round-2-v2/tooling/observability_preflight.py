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

Check 5 is decided by the REAL parser machinery, not an allowlist: the
declared ``parser_record_formats`` must be adapter-supported AND the formal
adapter must actually consume every record of the observed source (top-level
record types, sub-types, content-item types, structural invariants, session
identity, metadata discriminators).  Any record the full parser rejects ->
check 5 FAIL -> ``BENCHMARK OBSERVABILITY NOT VERIFIED``.

Check 4 (archive isolation, M2) uses mechanical PATH-COMPONENT semantics:
the archive destination must be under an approved evidence/archive root, must
never point at the fixture store (``.agents/benchmark-fixtures.local/**``),
and must explicitly contain the current arm and session components
(component equality, never loose substring).

Check 6 (controlled probe, M3) is also decided by the REAL parser: the
declared ``controlled_test_tool_call`` spec {tool, operation,
target_predicate} must be found as a normalized event in the actual source
(never trusted from ``captured``/``normalized_event`` config assertions), with
the event's session identity equal to the resolved current session.

The checks are mechanical probes over a preflight configuration document;
they never interpret workflow semantics.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmark_common import BenchmarkError, gate, load_json, validate_basic
from claude_transcript_adapter import (
    CLAUDE_RECORD_FORMATS,
    parse_transcript,
    verify_session_path_match,
)
from codex_rollout_adapter import parse_rollout

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

# Parser record formats supported by the adapters: the Codex rollout adapter's
# two record types plus the full Claude transcript taxonomy observed on the
# current tested runtime (Claude Code VSCode 2.1.226).  This derives from the
# adapters' own taxonomies; it only sanity-checks the DECLARED formats.
# Whether the parser actually supports the OBSERVED records is decided by the
# real parser compatibility probe below.
KNOWN_RECORD_FORMATS: frozenset[str] = (
    frozenset(
        {
            "codex:custom_tool_call",
            "codex:custom_tool_call_output",
        }
    )
    | CLAUDE_RECORD_FORMATS
)


def _check(name: str, ok: bool, detail: str | None = None) -> dict[str, Any]:
    return gate(name, "pass" if ok else "fail", detail)


def _claude_source_compatible(
    source: Mapping[str, Any], session_id: str, arm_id: str
) -> tuple[bool, str | None]:
    """Run the formal Claude transcript adapter over the actual transcript.

    The full parse machinery validates top-level record types, attachment /
    system / queue-operation sub-types, content-item types, structural
    invariants, metadata discriminators, and record-level session identity.
    Any record the full parser cannot consume -> (False, reason).
    """
    if not session_id:
        return False, "session identity unresolved (real parser probe skipped)"
    try:
        _source_events(source, arm_id, session_id)
    except OSError as exc:
        return False, f"cannot read transcript {source.get('location')}: {exc}"
    except BenchmarkError as exc:
        return False, str(exc)
    return True, None


def _codex_source_compatible(
    source: Mapping[str, Any], arm_id: str
) -> tuple[bool, str | None]:
    """Run the formal Codex rollout adapter over the actual rollout source.

    The source is a rollout JSONL file or a directory of rollout JSONL files;
    every ``*.jsonl`` file is fully consumed by the adapter.  Any record the
    full parser cannot consume -> (False, reason).
    """
    try:
        _source_events(source, arm_id, "")
    except OSError as exc:
        return False, f"cannot read rollout {source.get('location')}: {exc}"
    except BenchmarkError as exc:
        return False, str(exc)
    return True, None


def _source_events(
    source: Mapping[str, Any], arm_id: str, session_id: str
) -> list[dict[str, Any]]:
    """Fully parse the observed source with the REAL parser machinery.

    Returns the normalized access events.  Raises ``BenchmarkError`` for any
    record the full parser cannot consume (fail closed) and ``OSError`` for
    unreadable sources.  Shared by check 5 (parse validation) and check 6
    (controlled-probe search), so both checks always see the same normalized
    events.
    """
    if source["kind"] == "claude_transcript":
        location = str(source["location"])
        lines = Path(location).read_text(encoding="utf-8").splitlines()
        events, _context, _diag = parse_transcript(
            lines, arm_id=arm_id, session_id=session_id
        )
        return events
    location = Path(str(source["location"]))
    if location.is_dir():
        files = sorted(location.glob("*.jsonl"))
        if not files:
            raise BenchmarkError(f"no rollout JSONL files under {location}")
    elif location.is_file():
        files = [location]
    else:
        raise BenchmarkError(f"rollout source not found: {location}")
    events: list[dict[str, Any]] = []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        parsed, _diag = parse_rollout(lines, arm_id=arm_id)
        events.extend(parsed)
    return events


def _archive_isolation_failure(
    archive: str, arm_id: str, session_id: str
) -> str | None:
    """Check 4 (M2, Issue #125 remediation): archive destination isolation.

    Mechanical PATH-COMPONENT semantics (never loose substring): the archive
    must be under an approved evidence/archive root, must never point at the
    fixture store (``.agents/benchmark-fixtures.local/**``), and its path
    identity must explicitly contain the current arm component and the
    current session component.  Returns a failure reason, or ``None`` when
    the destination is isolated for this Arm/session.
    """
    normalized = archive.strip().lower().rstrip("/")
    if not normalized:
        return "archive destination is empty"
    if normalized == ".agents/benchmark-fixtures.local" or normalized.startswith(
        ".agents/benchmark-fixtures.local/"
    ):
        return (
            "archive destination must not be the fixture store "
            f"(.agents/benchmark-fixtures.local/**): {archive}"
        )
    if not normalized.startswith(
        (".agents/evidence.local/", ".agents/validation.local/")
    ):
        return (
            "archive destination must be under an approved evidence/archive "
            f"root (.agents/evidence.local or .agents/validation.local): {archive}"
        )
    if not session_id.strip():
        return "session identity unresolved (cannot verify archive isolation)"
    components = [part for part in normalized.split("/") if part]
    arm_expected = {arm_id.strip().lower(), f"arm-{arm_id.strip().lower()}"}
    if not arm_expected.intersection(components):
        return (
            "archive destination does not explicitly contain the current arm "
            f"component {sorted(arm_expected)!r} (component equality, not "
            f"substring): {archive}"
        )
    if session_id.strip().lower() not in components:
        return (
            "archive destination does not explicitly contain the current "
            f"session component: {archive}"
        )
    return None


def _controlled_probe_found(
    spec: Mapping[str, Any],
    events: list[dict[str, Any]],
    session_id: str,
) -> tuple[bool, str | None]:
    """Check 6 (M3, Issue #125 remediation): mechanical probe verification.

    The declared ``controlled_test_tool_call`` spec is the EXPECTED probe
    {tool, operation, target_predicate}; this function mechanically finds it
    in the REAL normalized events from the full parser (never trusts a
    ``captured`` / ``normalized_event`` config assertion).  A normalized
    event matches only when ALL of: normalized tool equals ``spec.tool``,
    operation equals ``spec.operation``, the FULL target text
    (``target_full``, never truncated) contains ``spec.target_predicate``,
    and the event's session identity equals the resolved current session.
    Returns (found, failure_reason).
    """
    tool = spec.get("tool")
    operation = spec.get("operation")
    predicate = spec.get("target_predicate")
    if not isinstance(tool, str) or not tool:
        return False, "controlled_test_tool_call.tool missing"
    if not isinstance(operation, str) or not operation:
        return False, "controlled_test_tool_call.operation missing"
    if not isinstance(predicate, str) or not predicate:
        return False, "controlled_test_tool_call.target_predicate missing"
    if not session_id.strip():
        return False, "session identity unresolved (cannot verify the probe)"
    needle = predicate.strip().lower()
    for event in events:
        if str(event.get("tool", "")) != tool:
            continue
        if str(event.get("operation", "")) != operation:
            continue
        if str(event.get("session_id", "")) != session_id:
            continue
        text = str(event.get("target_full") or event.get("target", ""))
        if needle in text.strip().lower():
            return True, None
    return (
        False,
        "no normalized event from the real parser matches the declared "
        f"controlled probe (tool={tool!r} operation={operation!r} "
        f"target_predicate={predicate!r} session={session_id!r})",
    )


def run_preflight(config: dict[str, Any]) -> dict[str, Any]:
    """Run the six observability checks against ``config``."""
    checks: list[dict[str, Any]] = []

    session = config.get("session_identity")
    session_ok = bool(
        isinstance(session, dict)
        and isinstance(session.get("session_id"), str)
        and session.get("session_id")
        and bool(session.get("arm_id"))
    )
    session_detail = None
    if session_ok and isinstance(session, dict):
        source = config.get("transcript_rollout_source")
        session_id = str(session["session_id"])
        if (
            isinstance(source, dict)
            and source.get("kind") == "claude_transcript"
            and source.get("location")
        ):
            # Mechanical match: the Claude transcript basename stem must equal
            # the resolved session identity; mismatch fails closed.
            try:
                verify_session_path_match(str(source["location"]), session_id)
            except BenchmarkError as exc:
                session_ok = False
                session_detail = str(exc)
    checks.append(
        _check(
            "session_identity_resolvable",
            session_ok,
            session_detail
            or (
                None
                if session_ok
                else "session_id/arm_id missing, unresolved, or transcript "
                "path does not match the session identity"
            ),
        )
    )

    source = config.get("transcript_rollout_source")
    source_ok = bool(
        isinstance(source, dict)
        and bool(source.get("kind") in {"codex_rollout", "claude_transcript"})
        and bool(source.get("location"))
    )
    checks.append(
        _check(
            "transcript_rollout_source_locatable",
            source_ok,
            None if source_ok else "source kind/location missing or unknown",
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
    session_id = ""
    if isinstance(config.get("session_identity"), dict):
        session_id = str(config["session_identity"].get("session_id", ""))
    archive_failure = _archive_isolation_failure(archive, arm_id, session_id)
    archive_isolated = archive_failure is None
    checks.append(
        _check(
            "archive_destination_isolated",
            archive_isolated,
            None if archive_isolated else archive_failure,
        )
    )

    # 5. parser supports the observed record format.  The DECLARED formats
    # must be adapter-supported (declaration sanity), and the REAL parser
    # compatibility probe must consume every record of the actual source:
    # any record the full parser rejects fails the check.
    formats = [str(item) for item in config.get("parser_record_formats", [])]
    declared_ok = bool(formats) and all(
        item in KNOWN_RECORD_FORMATS for item in formats
    )
    parser_ok = declared_ok
    parser_detail: str | None = None
    if declared_ok and source_ok and isinstance(source, Mapping):
        session_id = ""
        if isinstance(config.get("session_identity"), dict):
            session_id = str(config["session_identity"].get("session_id", ""))
        if source["kind"] == "claude_transcript":
            parser_ok, parser_detail = _claude_source_compatible(
                source, session_id, arm_id
            )
        else:  # codex_rollout
            parser_ok, parser_detail = _codex_source_compatible(source, arm_id)
    if not declared_ok:
        parser_detail = f"unsupported declared record formats: {formats}"
    checks.append(
        _check(
            "parser_supports_observed_record_format",
            parser_ok,
            None if parser_ok else parser_detail,
        )
    )

    # 6. controlled test tool call: mechanical probe verification.  The
    # declared ``controlled_test_tool_call`` spec is the EXPECTED probe
    # {tool, operation, target_predicate}; it is found in the REAL normalized
    # events from the full parser (check 5's machinery).  A config assertion
    # like ``captured: true`` / ``normalized_event: {...}`` is never trusted:
    # without a matching normalized probe event in the actual source the
    # check FAILs (fake config + no probe -> FAIL).
    controlled = config.get("controlled_test_tool_call")
    probe_ok = False
    probe_detail: str | None = None
    if isinstance(controlled, Mapping):
        probe_events: list[dict[str, Any]] = []
        if source_ok and isinstance(source, Mapping):
            try:
                probe_events = _source_events(source, arm_id, session_id)
            except (BenchmarkError, OSError) as exc:
                probe_detail = (
                    f"controlled-probe search over the real parser failed: {exc}"
                )
        if probe_detail is None:
            probe_ok, probe_detail = _controlled_probe_found(
                controlled, probe_events, session_id
            )
    else:
        probe_detail = "controlled_test_tool_call spec missing"
    checks.append(
        _check(
            "controlled_test_tool_call_captured_and_normalizable",
            probe_ok,
            None if probe_ok else probe_detail,
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
