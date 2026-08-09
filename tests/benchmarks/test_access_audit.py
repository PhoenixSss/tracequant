# ruff: noqa: E402, I001

from __future__ import annotations

from typing import Any, cast

from _benchmark_helpers import REPO_ROOT

from access_audit import audit, match_target  # type: ignore[import-not-found]
from benchmark_common import load_json  # type: ignore[import-not-found]

INVENTORY = (
    REPO_ROOT
    / "benchmarks"
    / "task-65-round-2-v2"
    / "inventory"
    / "prior-benchmark-contamination-inventory.json"
)


def _event(target: str) -> dict[str, object]:
    return {
        "session_id": "s1",
        "tool": "other",
        "operation": "read",
        "target": target,
        "raw_event_reference": "e1",
    }


def _clean_audit(events: list[dict[str, object]]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        audit(
            events,
            load_json(INVENTORY),
            [],
            [],
            capture_complete=True,
            parser_supported=True,
            audit_executed=True,
        ),
    )


def test_match_target_pr_forms() -> None:
    forbidden = ["99", "#108", "109"]
    assert match_target("gh pr view 99", forbidden) == ["99"]
    assert match_target("see #108 in the thread", forbidden) == ["#108"]
    assert match_target("/pull/109", forbidden) == ["109"]
    assert match_target("git status", forbidden) == []


def test_match_target_no_false_positive_on_digits() -> None:
    assert match_target("timestamp 2025-07-19 09:33:00", ["99"]) == []
    assert match_target("1999 lines changed", ["99"]) == []
    assert match_target("issue count 5", ["99"]) == []


def test_match_target_long_identifiers_substring() -> None:
    forbidden = [
        "docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json",
        "experiment/task65-candidate-wsl2",
        "a492f0b334f950f2613b4b2204e96bef413355be",
    ]
    assert match_target(
        "Read docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json",
        forbidden,
    ) == ["docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json"]
    assert match_target("git checkout experiment/task65-candidate-wsl2", forbidden) == [
        "experiment/task65-candidate-wsl2"
    ]
    assert match_target(
        "git cat-file -p a492f0b334f950f2613b4b2204e96bef413355be", forbidden
    ) == ["a492f0b334f950f2613b4b2204e96bef413355be"]


def test_audit_negative_evidence_pass() -> None:
    result = _clean_audit([_event("git status"), _event("read AGENTS.md")])
    assert result["verdict"] == "PASS"
    assert result["reason"] == "zero forbidden matches"
    assert result["match_count"] == 0


def test_audit_leakage_on_inventory_match() -> None:
    result = _clean_audit([_event("gh pr view 108")])
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
    assert result["match_count"] >= 1


def test_audit_leakage_on_v1_bundle_path() -> None:
    result = _clean_audit(
        [
            _event(
                "Read docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json"
            )
        ]
    )
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"


def test_audit_cross_arm_dynamic_identity_set() -> None:
    events = [_event("git branch experiment/task65-v2-b-control-base")]
    result = audit(
        events,
        load_json(INVENTORY),
        ["experiment/task65-v2-b-control-base"],
        [],
        capture_complete=True,
        parser_supported=True,
        audit_executed=True,
    )
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"


def test_audit_timeline_metadata_previous_arm() -> None:
    # The timeline query RESULT exposes a previous-Arm dynamic identity
    # (branch name from a ConnectedEvent) -> forbidden match.
    events = [_event("timeline connected source: experiment/task65-current-windows")]
    result = audit(
        events,
        load_json(INVENTORY),
        [],
        ["experiment/task65-current-windows"],
        capture_complete=True,
        parser_supported=True,
        audit_executed=True,
    )
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"


def test_audit_incomplete_capture_is_not_verified() -> None:
    result = audit(
        [_event("git status")],
        load_json(INVENTORY),
        [],
        [],
        capture_complete=False,
        parser_supported=True,
        audit_executed=True,
    )
    assert result["verdict"] == "NOT VERIFIED"


def test_audit_missing_log_never_no_access() -> None:
    result = audit(
        [],
        load_json(INVENTORY),
        [],
        [],
        capture_complete=False,
        parser_supported=True,
        audit_executed=True,
    )
    assert result["verdict"] == "NOT VERIFIED"


def test_audit_accepts_schema_wrapper_document() -> None:
    wrapper = load_json(INVENTORY)
    result = audit(
        [_event("gh pr view 99")],
        wrapper,
        [],
        [],
        capture_complete=True,
        parser_supported=True,
        audit_executed=True,
    )
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
