"""Access audit tooling for task-65-round-2-v2.

Matches observed access-event targets AND context-input targets against:

- the prior-benchmark contamination inventory (Class 2);
- other-arm current-run dynamic identity sets (Class 3);
- gh / GraphQL query results exposing Issue timeline connected / disconnected
  metadata of previous-Arm dynamic identity.

Context inputs are the normalized INPUT_CONTEXT_BEARING records from the
Claude transcript adapter (attachments, summaries, prompts): content that was
injected into the session without a tool call.  They are matched with the same
forbidden identifiers so that "no Read happened" can never mask contamination
that arrived through attachment / summary / snapshot input.

Any match -> ``BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE``.
Negative-evidence PASS definition: capture complete + parser supported +
audit executed + zero forbidden matches (access or context).

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

from benchmark_common import BenchmarkError, load_json, validate_basic

_INVENTORY_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "contamination-inventory.schema.json"
)

_PR_NUMBER = re.compile(r"(?:^|[^0-9])#(\d{1,6})(?:[^0-9]|$)")
_PULL_URL = re.compile(r"/pull/(\d{1,6})(?:[^0-9]|$)")
# "gh pr view 108", "gh issue view 99", "PR #108", "/pull/108" forms.
_PR_CONTEXT = re.compile(
    r"(?:^|[^0-9])(?:pr|pull|issue)\b[^0-9]{0,12}?(\d{1,6})(?:[^0-9]|$)"
)
_PR_IDENTIFIER = re.compile(r"#?\d{1,6}")


def load_inventory_entries(value: Any) -> list[dict[str, Any]]:
    """Load and schema-validate the canonical contamination inventory document.

    The canonical contract is the schema-backed document
    ``{"protocol_identity": ..., "schema_version": ..., "classification":
    ..., "entries": [...]}`` (``schemas/contamination-inventory.schema.json``).
    The document is validated against that schema and its ``entries`` are
    extracted mechanically.  Anything else -- including a bare JSON array --
    is an unsupported implicit format and fails closed.  API and CLI share
    this single loader so the CLI contract cannot drift from the API.
    """
    if not isinstance(value, Mapping):
        raise BenchmarkError(
            "inventory must be the schema-conformant canonical document (fail closed)"
        )
    schema = load_json(_INVENTORY_SCHEMA_PATH)
    validate_basic(value, schema, "inventory")
    return list(value["entries"])


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
    """Collect machine-readable forbidden location identifiers.

    Only concrete identifier values are matched: ``identifier`` and the
    ``path`` / ``ref`` / ``pr`` / ``branch`` / ``commit`` reference fields.
    Category labels (``kind`` values such as ``commit`` / ``path`` /
    ``branch`` / ``pr`` / ``external``) are NOT identifiers and never match:
    a generic label would false-positive against every target that merely
    contains the word, making the audit unable to ever PASS a clean run.
    """
    identifiers: list[str] = []
    for entry in inventory:
        for location in entry.get("locations", []):
            value = location.get("identifier", "")
            if value:
                identifiers.append(_normalize(value))
            for key in ("path", "ref", "pr", "branch", "commit"):
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


def _match_access_event(
    event: dict[str, Any], forbidden: list[str], matches: list[dict[str, Any]]
) -> None:
    hits = match_target(str(event.get("target", "")), forbidden)
    if hits:
        matches.append(
            {
                "kind": "access",
                "session_id": event.get("session_id", ""),
                "tool": event.get("tool", ""),
                "operation": event.get("operation", ""),
                "target": event.get("target", ""),
                "forbidden_identifiers": hits,
                "raw_event_reference": event.get("raw_event_reference", ""),
            }
        )


def _match_context_input(
    context_input: dict[str, Any], forbidden: list[str], matches: list[dict[str, Any]]
) -> None:
    hits = match_target(str(context_input.get("target", "")), forbidden)
    if hits:
        matches.append(
            {
                "kind": "context_input",
                "session_id": context_input.get("session_id", ""),
                "source_type": context_input.get("source_type", ""),
                "target": context_input.get("target", ""),
                "forbidden_identifiers": hits,
                "raw_event_reference": context_input.get("raw_event_reference", ""),
            }
        )


def audit(
    events: list[dict[str, Any]],
    inventory: Mapping[str, Any],
    cross_arm_dynamic_identity_sets: list[str],
    timeline_metadata: list[str],
    *,
    context_inputs: list[dict[str, Any]] | None = None,
    capture_complete: bool = True,
    parser_supported: bool = True,
    audit_executed: bool = True,
) -> dict[str, Any]:
    """Run the access audit over normalized events; returns the audit report.

    ``inventory`` is the schema-conformant canonical contamination inventory
    document (``{"...", "entries": [...]}``), loaded through the shared
    :func:`load_inventory_entries` loader; a bare array is an unsupported
    implicit format and fails closed.  ``context_inputs`` are the normalized
    INPUT_CONTEXT_BEARING records from the Claude transcript adapter
    (attachments, summaries, prompts) and are matched against the same
    forbidden identifiers, so Class 2 / Class 3 identities are detected even
    when they entered the session without a tool call.  All preconditions
    (capture complete, parser supported, audit executed) are explicit inputs
    so a missing log is never silently interpreted as "no access".
    """
    entries = load_inventory_entries(inventory)
    forbidden = _forbidden_identifiers(entries)
    forbidden += [_normalize(item) for item in cross_arm_dynamic_identity_sets]
    forbidden += [_normalize(item) for item in timeline_metadata]

    context_inputs = list(context_inputs or [])
    for context_input in context_inputs:
        if not isinstance(context_input, Mapping):
            raise BenchmarkError(
                f"context input is not an object (fail closed): {context_input!r}"
            )

    matches: list[dict[str, Any]] = []
    for event in events:
        _match_access_event(event, forbidden, matches)
    for context_input in context_inputs:
        _match_context_input(context_input, forbidden, matches)

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
        "context_inputs_count": len(context_inputs),
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
    parser.add_argument(
        "--context-inputs",
        help="normalized context inputs JSON (Claude transcript adapter)",
    )
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
        context_inputs = (
            load_json(Path(args.context_inputs)) if args.context_inputs else []
        )
        if not isinstance(events, list):
            raise BenchmarkError("events must be a JSON array")
        if not isinstance(context_inputs, list):
            raise BenchmarkError("context inputs must be a JSON array")
        # CLI contract: the canonical schema inventory document.  The shared
        # ``load_inventory_entries`` loader schema-validates it and
        # mechanically extracts entries inside ``audit``; a bare array or any
        # other shape fails closed (no implicit second format).
        report = audit(
            events,
            inventory,
            [str(item) for item in cross_arm],
            [str(item) for item in timeline],
            context_inputs=context_inputs,
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
