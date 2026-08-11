"""Access audit tooling for task-65-round-2-v2.

Matches observed access-event targets AND context-input targets against:

- the prior-benchmark contamination inventory (Class 2);
- other-arm current-run dynamic identity sets (Class 3);
- gh / GraphQL query results exposing Issue timeline connected / disconnected
  metadata of previous-Arm dynamic identity (Class 3).

Context inputs are the normalized INPUT_CONTEXT_BEARING records from the
Claude transcript adapter (attachments, summaries, prompts): content that was
injected into the session without a tool call.  They are matched with the same
forbidden identifiers so that "no Read happened" can never mask contamination
that arrived through attachment / summary / snapshot input.

Any non-exempt match -> ``BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE``.
Negative-evidence PASS definition: capture complete + parser supported +
audit executed + zero forbidden matches (access or context).

Contamination identity semantics (Issue #125):

(A) CURRENT_ARM_OWN_EVIDENCE -> ALLOWED.  A forbidden-identifier occurrence
    whose span overlaps the current run's own evidence/validation/fixture
    paths (``current_run_identity.own_evidence_paths``, both spans
    boundary-checked) is the current run's own freshly written artifact and is
    exempted, recorded in ``own_evidence_exemptions``.  The exemption applies
    ONLY to Class 2 (inventory) identifiers: other-arm current-run identities
    (Class 3) are never a current arm's own artifact.
(B) PRIOR_BENCHMARK / HISTORICAL_ANSWER_BEARING -> Class 2 forbidden.
(C) OTHER_ARM_CURRENT_RUN -> Class 3 forbidden.

The generic evidence/validation ROOTS (``.agents/evidence.local``,
``.agents/validation.local``) are NOT forbidden identifiers: the inventory
carries only specific prior-run artifact identities (provenance / arm /
session / path identities), and the audit never ignores an evidence root
wholesale.  Fail closed: an inventory entry without any concrete forbidden
identifier, or a malformed ``current_run_identity``, aborts the audit.

The audit only matches; it never interprets workflow semantics.  Matching is
deterministic substring/identifier matching on normalized lowercased values;
every match carries its ``identity_classes`` (PRIOR_BENCHMARK_CLASS_2 /
OTHER_ARM_CURRENT_RUN_CLASS_3).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark_common import BenchmarkError, load_json, validate_basic

_INVENTORY_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "contamination-inventory.schema.json"
)

_CLASS_2 = "PRIOR_BENCHMARK_CLASS_2"
_CLASS_3 = "OTHER_ARM_CURRENT_RUN_CLASS_3"
_ARM_IDS = ("A", "B", "C", "D")
_CONDUCTOR_LOCAL_ROOTS = (
    ".agents/evidence.local",
    ".agents/validation.local",
    ".agents/benchmark-fixtures.local",
)
# Path-like continuation characters: an occurrence bounded on both sides by
# non-continuation characters (or string ends) is a standalone occurrence.
# "/" is deliberately NOT a continuation character: a directory-path
# occurrence is a standalone occurrence when followed by "/" (a path that
# extends deeper is not "inside a longer token").
_CONTINUATION_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._@-")

_PR_NUMBER = re.compile(r"(?:^|[^0-9])#(\d{1,6})(?:[^0-9]|$)")
_PULL_URL = re.compile(r"/pull/(\d{1,6})(?:[^0-9]|$)")
# "gh pr view 108", "gh issue view 99", "PR #108", "/pull/108" forms.
_PR_CONTEXT = re.compile(
    r"(?:^|[^0-9])(?:pr|pull|issue)\b[^0-9]{0,12}?(\d{1,6})(?:[^0-9]|$)"
)
_PR_IDENTIFIER = re.compile(r"#?\d{1,6}")


@dataclass(frozen=True)
class ForbiddenIdentifier:
    """A forbidden identifier with its contamination class and source.

    ``source`` names the provenance (inventory artifact id, cross-arm set,
    timeline metadata) for human investigation; ``identity_class`` is the
    machine-readable Class 2 / Class 3 label carried into every match.
    """

    identifier: str
    identity_class: str
    source: str


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


def _forbidden_identifiers(
    inventory: list[dict[str, Any]],
) -> list[ForbiddenIdentifier]:
    """Collect machine-readable forbidden location identifiers (Class 2).

    Only concrete identifier values are collected: ``identifier`` and the
    ``path`` / ``ref`` / ``pr`` / ``branch`` / ``commit`` reference fields.
    Category labels (``kind`` values such as ``commit`` / ``path`` /
    ``branch`` / ``pr`` / ``external``) are NOT identifiers and never match:
    a generic label would false-positive against every target that merely
    contains the word, making the audit unable to ever PASS a clean run.

    The generic evidence/validation ROOTS (``.agents/evidence.local``,
    ``.agents/validation.local``) are deliberately NOT identifiers: only
    specific prior-run artifact identities are forbidden (provenance / arm /
    session / path identity).  An entry whose locations carry NO concrete
    identifier at all is unusable for matching and fails closed -- an entry
    that forbids nothing would silently weaken the audit.
    """
    identifiers: list[ForbiddenIdentifier] = []
    for entry in inventory:
        collected: list[str] = []
        for location in entry.get("locations", []):
            value = location.get("identifier", "")
            if value:
                collected.append(_normalize(value))
            for key in ("path", "ref", "pr", "branch", "commit"):
                extra = location.get(key)
                if isinstance(extra, str) and extra:
                    collected.append(_normalize(extra))
        if not collected:
            raise BenchmarkError(
                "inventory entry has no concrete forbidden identifiers "
                f"(fail closed): {entry.get('artifact_id', '<unnamed>')}"
            )
        identifiers.extend(
            ForbiddenIdentifier(item, _CLASS_2, entry.get("artifact_id", ""))
            for item in sorted(set(collected))
        )
    return identifiers


def _occurrences(text: str, needle: str) -> list[tuple[int, int]]:
    """All ``[start, end)`` spans of ``needle`` in ``text``."""
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            break
        spans.append((index, index + len(needle)))
        start = index + 1
    return spans


def _bounded(span: tuple[int, int], text: str) -> bool:
    """True if ``span`` is a standalone occurrence (not inside a longer token)."""
    before = text[span[0] - 1] if span[0] > 0 else ""
    after = text[span[1]] if span[1] < len(text) else ""
    return before not in _CONTINUATION_CHARS and after not in _CONTINUATION_CHARS


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _match_target_detail(
    normalized: str,
    forbidden: list[ForbiddenIdentifier],
    own_paths: list[str],
) -> tuple[list[ForbiddenIdentifier], list[ForbiddenIdentifier]]:
    """Match ``normalized`` target; return ``(matches, own-evidence exemptions)``.

    An identifier occurrence is exempted as CURRENT_ARM_OWN_EVIDENCE only
    when ALL of: it is a Class 2 (inventory) identifier, it has a
    boundary-checked occurrence, and that occurrence overlaps a
    boundary-checked occurrence of one of the current run's own paths.  This
    is deliberately strict: the exemption must never mask a prior or
    other-arm artifact (those never overlap the current run's own paths).
    """
    pr_numbers = _target_pr_numbers(normalized)
    own_spans: list[tuple[int, int]] = []
    for own in own_paths:
        own_spans.extend(_occurrences(normalized, own))

    matched: list[ForbiddenIdentifier] = []
    exempted: list[ForbiddenIdentifier] = []
    for item in forbidden:
        identifier = item.identifier
        if _PR_IDENTIFIER.fullmatch(identifier):
            if identifier.lstrip("#") in pr_numbers:
                matched.append(item)
            continue
        if len(identifier) < 4:
            continue
        if identifier not in normalized:
            continue
        if own_spans and item.identity_class == _CLASS_2:
            occurrences = _occurrences(normalized, identifier)
            exempted_any = any(
                _bounded(span, normalized)
                and any(
                    _bounded(own, normalized) and _overlaps(span, own)
                    for own in own_spans
                )
                for span in occurrences
            )
            if exempted_any:
                exempted.append(item)
                continue
        matched.append(item)
    return _dedupe(matched), _dedupe(exempted)


def _dedupe(items: list[ForbiddenIdentifier]) -> list[ForbiddenIdentifier]:
    """Drop duplicate identifiers (same value may recur across inventory
    entries / fields, e.g. ``identifier`` plus ``branch``/``commit``)."""
    seen: set[str] = set()
    unique: list[ForbiddenIdentifier] = []
    for item in items:
        if item.identifier not in seen:
            seen.add(item.identifier)
            unique.append(item)
    return unique


def match_target(target: str, forbidden: list[str]) -> list[str]:
    """Return the forbidden identifiers occurring in ``target`` (if any).

    PR-number identifiers (bare digits or ``#NN``) match only through the
    boundary-checked PR patterns, so a bare number can never substring-match
    unrelated digits.  Longer identifiers match as normalized substrings.

    This is the raw string matcher (no class / exemption semantics); the
    audit itself uses the class-annotated matcher with the current-run
    own-evidence exemption.
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


def _match_record(
    kind: str, record: dict[str, Any], hits: list[ForbiddenIdentifier]
) -> dict[str, Any]:
    """Build a match/exemption record for ``record`` with class labels."""
    return {
        "kind": kind,
        "session_id": record.get("session_id", ""),
        "tool": record.get("tool", ""),
        "operation": record.get("operation", ""),
        "source_type": record.get("source_type", ""),
        "target": record.get("target", ""),
        "forbidden_identifiers": [item.identifier for item in hits],
        "identity_classes": sorted({item.identity_class for item in hits}),
        "raw_event_reference": record.get("raw_event_reference", ""),
    }


def _match_access_event(
    event: dict[str, Any],
    forbidden: list[ForbiddenIdentifier],
    own_paths: list[str],
    matches: list[dict[str, Any]],
    exemptions: list[dict[str, Any]],
) -> None:
    normalized = _normalize(str(event.get("target", "")))
    matched, exempted = _match_target_detail(normalized, forbidden, own_paths)
    if matched:
        matches.append(_match_record("access", event, matched))
    if exempted:
        exemptions.append(_match_record("access", event, exempted))


def _match_context_input(
    context_input: dict[str, Any],
    forbidden: list[ForbiddenIdentifier],
    own_paths: list[str],
    matches: list[dict[str, Any]],
    exemptions: list[dict[str, Any]],
) -> None:
    normalized = _normalize(str(context_input.get("target", "")))
    matched, exempted = _match_target_detail(normalized, forbidden, own_paths)
    if matched:
        matches.append(_match_record("context_input", context_input, matched))
    if exempted:
        exemptions.append(_match_record("context_input", context_input, exempted))


def _validate_current_run_identity(
    value: Mapping[str, Any] | None,
) -> tuple[str, str, list[str]] | None:
    """Validate ``current_run_identity``; returns ``(arm_id, session_id, paths)``.

    ``None`` (not provided) is legal and means the exemption is inactive.
    Anything else must be exactly ``{arm_id, session_id, own_evidence_paths}``
    with an arm id in {A, B, C, D}, a non-empty session id, and non-empty
    own-evidence paths under a conductor-local evidence/validation/fixture
    root -- otherwise the audit fails closed (a malformed identity must never
    silently disable or broaden the exemption).
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BenchmarkError("current_run_identity must be an object (fail closed)")
    if set(value) != {"arm_id", "session_id", "own_evidence_paths"}:
        raise BenchmarkError(
            "current_run_identity keys must be exactly arm_id, session_id, "
            "own_evidence_paths (fail closed)"
        )
    arm_id = value["arm_id"]
    session_id = value["session_id"]
    own_evidence_paths = value["own_evidence_paths"]
    if not isinstance(arm_id, str) or arm_id not in _ARM_IDS:
        raise BenchmarkError(
            "current_run_identity.arm_id must be one of "
            f"{', '.join(_ARM_IDS)} (fail closed)"
        )
    if not isinstance(session_id, str) or not session_id.strip():
        raise BenchmarkError(
            "current_run_identity.session_id must be a non-empty string (fail closed)"
        )
    if not isinstance(own_evidence_paths, list):
        raise BenchmarkError(
            "current_run_identity.own_evidence_paths must be a list (fail closed)"
        )
    paths: list[str] = []
    for own in own_evidence_paths:
        if not isinstance(own, str) or not own.strip():
            raise BenchmarkError(
                "current_run_identity.own_evidence_paths entries must be "
                "non-empty strings (fail closed)"
            )
        normalized = _normalize(own)
        if not any(normalized.startswith(root) for root in _CONDUCTOR_LOCAL_ROOTS):
            raise BenchmarkError(
                "current_run_identity.own_evidence_paths entries must be "
                "conductor-local evidence/validation/fixture paths (fail "
                f"closed): {own}"
            )
        paths.append(normalized)
    return (arm_id, session_id, paths)


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
    current_run_identity: Mapping[str, Any] | None = None,
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
    ``current_run_identity`` carries the current run's ``arm_id`` /
    ``session_id`` / ``own_evidence_paths``; forbidden-identifier occurrences
    overlapping those own paths are exempted as the current run's own
    evidence (see module docstring), never silently ignored.
    """
    entries = load_inventory_entries(inventory)
    forbidden = _forbidden_identifiers(entries)
    forbidden += [
        ForbiddenIdentifier(
            _normalize(item), _CLASS_3, "cross-arm-dynamic-identity-set"
        )
        for item in cross_arm_dynamic_identity_sets
    ]
    forbidden += [
        ForbiddenIdentifier(_normalize(item), _CLASS_3, "timeline-metadata")
        for item in timeline_metadata
    ]
    current_identity = _validate_current_run_identity(current_run_identity)
    own_paths = current_identity[2] if current_identity else []

    context_inputs = list(context_inputs or [])
    for context_input in context_inputs:
        if not isinstance(context_input, Mapping):
            raise BenchmarkError(
                f"context input is not an object (fail closed): {context_input!r}"
            )

    matches: list[dict[str, Any]] = []
    exemptions: list[dict[str, Any]] = []
    for event in events:
        _match_access_event(event, forbidden, own_paths, matches, exemptions)
    for context_input in context_inputs:
        _match_context_input(context_input, forbidden, own_paths, matches, exemptions)

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
        "current_run_identity": (
            None
            if current_identity is None
            else {
                "arm_id": current_identity[0],
                "session_id": current_identity[1],
                "own_evidence_paths": current_identity[2],
            }
        ),
        "matches": matches,
        "match_count": len(matches),
        "own_evidence_exemptions": exemptions,
        "exemption_count": len(exemptions),
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
    parser.add_argument(
        "--current-run-identity",
        help="current run identity JSON: {arm_id, session_id, own_evidence_paths}",
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
        current_run_identity = (
            load_json(Path(args.current_run_identity))
            if args.current_run_identity
            else None
        )
        if not isinstance(events, list):
            raise BenchmarkError("events must be a JSON array")
        if not isinstance(context_inputs, list):
            raise BenchmarkError("context inputs must be a JSON array")
        if not isinstance(cross_arm, list) or not isinstance(timeline, list):
            raise BenchmarkError(
                "cross-arm sets and timeline metadata must be JSON arrays"
            )
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
            current_run_identity=current_run_identity,
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
