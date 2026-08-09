"""Access audit tooling for task-65-round-2-v2.

Matches observed access-event targets against:

- the prior-benchmark contamination inventory (Class 2);
- other-arm current-run dynamic identity sets (Class 3);
- gh / GraphQL query results exposing Issue timeline connected / disconnected
  metadata of previous-Arm dynamic identity.

Any match -> ``BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE``.
Negative-evidence PASS definition: capture complete + parser supported +
audit executed + zero forbidden matches.

The audit only matches; it never interprets workflow semantics.  Matching is
deterministic substring/identifier matching on normalized lowercased values.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmark_common import BenchmarkError, load_json

_PR_NUMBER = re.compile(r"(?:^|[^0-9])#(\d{1,6})(?:[^0-9]|$)")
_PULL_URL = re.compile(r"/pull/(\d{1,6})(?:[^0-9]|$)")
# "gh pr view 108", "gh issue view 99", "PR #108", "/pull/108" forms.
_PR_CONTEXT = re.compile(
    r"(?:^|[^0-9])(?:pr|pull|issue)\b[^0-9]{0,12}?(\d{1,6})(?:[^0-9]|$)"
)
_PR_IDENTIFIER = re.compile(r"#?\d{1,6}")


def _normalize(value: str) -> str:
    return value.strip().lower()


def _target_pr_numbers(normalized: str) -> list[str]:
    """PR numbers referenced by ``normalized`` (#NN, /pull/NN, 'pr view NN')."""
    numbers: list[str] = []
    for pattern in (_PR_NUMBER, _PULL_URL, _PR_CONTEXT):
        for match in pattern.finditer(normalized):
            number = match.group(1) or match.group(2)
            if number:
                numbers.append(number)
    return numbers


def _forbidden_identifiers(inventory: list[dict[str, Any]]) -> list[str]:
    """Collect machine-readable forbidden location identifiers."""
    identifiers: list[str] = []
    for entry in inventory:
        for location in entry.get("locations", []):
            value = location.get("identifier", "")
            if value:
                identifiers.append(_normalize(value))
            for key in ("kind", "path", "ref", "pr", "branch", "commit"):
                extra = location.get(key)
                if isinstance(extra, str) and extra:
                    identifiers.append(_normalize(extra))
    return identifiers


def match_target(target: str, forbidden: list[str]) -> list[str]:
    """Return the forbidden identifiers occurring in ``target`` (if any).

    PR-number identifiers (bare digits or ``#NN``) match only through the
    boundary-checked PR patterns, so a bare number can never substring-match
    unrelated digits.  Longer identifiers match as normalized substrings.
    """
    normalized = _normalize(target)
    pr_numbers = _target_pr_numbers(normalized)
    hits: list[str] = []
    for identifier in forbidden:
        if not identifier:
            continue
        if _PR_IDENTIFIER.fullmatch(identifier):
            if identifier.lstrip("#") in pr_numbers:
                hits.append(identifier)
            continue
        if len(identifier) < 4:
            continue
        if identifier in normalized:
            hits.append(identifier)
    return sorted(set(hits))


def audit(
    events: list[dict[str, Any]],
    inventory: list[dict[str, Any]] | Mapping[str, Any],
    cross_arm_dynamic_identity_sets: list[str],
    timeline_metadata: list[str],
    *,
    capture_complete: bool = True,
    parser_supported: bool = True,
    audit_executed: bool = True,
) -> dict[str, Any]:
    """Run the access audit over normalized events; returns the audit report.

    All preconditions (capture complete, parser supported, audit executed)
    are explicit inputs so a missing log is never silently interpreted as
    "no access".
    """
    if isinstance(inventory, Mapping) and "entries" in inventory:
        # Accept the schema-conformant inventory document ({..., "entries": [...]}).
        inventory = list(inventory["entries"])
    elif not isinstance(inventory, list):
        raise BenchmarkError(
            "inventory must be a JSON array or the schema-conformant document (fail closed)"
        )
    forbidden = _forbidden_identifiers(inventory)
    forbidden += [_normalize(item) for item in cross_arm_dynamic_identity_sets]
    forbidden += [_normalize(item) for item in timeline_metadata]

    matches: list[dict[str, Any]] = []
    for event in events:
        hits = match_target(str(event.get("target", "")), forbidden)
        if hits:
            matches.append(
                {
                    "session_id": event.get("session_id", ""),
                    "tool": event.get("tool", ""),
                    "operation": event.get("operation", ""),
                    "target": event.get("target", ""),
                    "forbidden_identifiers": hits,
                    "raw_event_reference": event.get("raw_event_reference", ""),
                }
            )

    if not capture_complete:
        verdict = "NOT VERIFIED"
        reason = (
            "capture incomplete (missing/incomplete logs are never no-access evidence)"
        )
    elif not parser_supported:
        verdict = "NOT VERIFIED"
        reason = "parser does not support the observed record format"
    elif not audit_executed:
        verdict = "NOT VERIFIED"
        reason = "audit not executed"
    elif matches:
        verdict = "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
        reason = f"{len(matches)} forbidden access match(es)"
    else:
        verdict = "PASS"
        reason = "zero forbidden matches"

    report: dict[str, Any] = {
        "protocol_identity": "task-65-round-2-v2",
        "verdict": verdict,
        "reason": reason,
        "capture_complete": capture_complete,
        "parser_supported": parser_supported,
        "audit_executed": audit_executed,
        "events_count": len(events),
        "forbidden_identifier_count": len(forbidden),
        "matches": matches,
        "match_count": len(matches),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the benchmark access audit.")
    parser.add_argument("--events", required=True, help="normalized access events JSON")
    parser.add_argument(
        "--inventory", required=True, help="contamination inventory JSON"
    )
    parser.add_argument("--cross-arm-sets", help="cross-arm dynamic identity sets JSON")
    parser.add_argument("--timeline-metadata", help="timeline metadata strings JSON")
    parser.add_argument("--capture-complete", action="store_true")
    parser.add_argument("--parser-supported", action="store_true")
    parser.add_argument("--audit-executed", action="store_true")
    parser.add_argument("--out", help="optional output path")
    args = parser.parse_args(argv)

    try:
        events = load_json(Path(args.events))
        inventory = load_json(Path(args.inventory))
        cross_arm = load_json(Path(args.cross_arm_sets)) if args.cross_arm_sets else []
        timeline = (
            load_json(Path(args.timeline_metadata)) if args.timeline_metadata else []
        )
        if not isinstance(events, list) or not isinstance(inventory, list):
            raise BenchmarkError("events and inventory must be JSON arrays")
        report = audit(
            events,
            inventory,
            [str(item) for item in cross_arm],
            [str(item) for item in timeline],
            capture_complete=args.capture_complete,
            parser_supported=args.parser_supported,
            audit_executed=args.audit_executed,
        )
    except BenchmarkError as exc:
        print(f"ACCESS AUDIT FAIL CLOSED: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
