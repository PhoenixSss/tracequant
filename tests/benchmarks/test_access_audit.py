# ruff: noqa: E402, I001

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from _benchmark_helpers import REPO_ROOT

from access_audit import audit, load_inventory_entries, main, match_target  # type: ignore[import-not-found]
from benchmark_common import BenchmarkError, load_json  # type: ignore[import-not-found]

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


def test_audit_rejects_bare_array_inventory() -> None:
    # The canonical contract is the schema document; a bare JSON array is an
    # unsupported implicit format and fails closed (no shape guessing).
    with pytest.raises(BenchmarkError):
        audit(
            [_event("git status")],
            load_json(INVENTORY)["entries"],
            [],
            [],
            capture_complete=True,
            parser_supported=True,
            audit_executed=True,
        )


def test_audit_rejects_inventory_missing_entries() -> None:
    doc = load_json(INVENTORY)
    del doc["entries"]
    with pytest.raises(BenchmarkError):
        audit(
            [_event("git status")],
            doc,
            [],
            [],
            capture_complete=True,
            parser_supported=True,
            audit_executed=True,
        )


def test_audit_rejects_schema_invalid_entry() -> None:
    doc = load_json(INVENTORY)
    doc["entries"][0]["type"] = "not-a-valid-type"
    with pytest.raises(BenchmarkError):
        audit(
            [_event("git status")],
            doc,
            [],
            [],
            capture_complete=True,
            parser_supported=True,
            audit_executed=True,
        )


def test_load_inventory_entries_shared_loader() -> None:
    entries = load_inventory_entries(load_json(INVENTORY))
    assert isinstance(entries, list)
    assert entries == load_json(INVENTORY)["entries"]


def _write_cli(tmp_path, name: str, value: object) -> str:  # type: ignore[no-untyped-def]
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def _cli_args(events_path: str, inventory_path: str) -> list[str]:
    return [
        "--events",
        events_path,
        "--inventory",
        inventory_path,
        "--capture-complete",
        "--parser-supported",
        "--audit-executed",
    ]


def test_cli_accepts_canonical_inventory_document(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    # The canonical inventory artifact is the schema document; the CLI must
    # accept it and mechanically extract entries (shared loader contract).
    events_path = _write_cli(tmp_path, "events.json", [_event("git status")])
    result = main(_cli_args(events_path, str(INVENTORY)))
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "PASS"
    assert report["reason"] == "zero forbidden matches"
    assert report["forbidden_identifier_count"] >= 1  # real entries were loaded


def test_cli_rejects_bare_array_inventory_fail_closed(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    events_path = _write_cli(tmp_path, "events.json", [_event("git status")])
    inventory_path = _write_cli(
        tmp_path, "inventory.json", load_json(INVENTORY)["entries"]
    )
    result = main(_cli_args(events_path, inventory_path))
    assert result == 1
    assert "FAIL CLOSED" in capsys.readouterr().err


def test_cli_rejects_inventory_missing_entries_fail_closed(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    events_path = _write_cli(tmp_path, "events.json", [_event("git status")])
    doc = load_json(INVENTORY)
    del doc["entries"]
    inventory_path = _write_cli(tmp_path, "inventory.json", doc)
    result = main(_cli_args(events_path, inventory_path))
    assert result == 1
    assert "FAIL CLOSED" in capsys.readouterr().err


def test_cli_rejects_schema_invalid_entry_fail_closed(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    events_path = _write_cli(tmp_path, "events.json", [_event("git status")])
    doc = load_json(INVENTORY)
    doc["entries"][0]["type"] = "not-a-valid-type"
    inventory_path = _write_cli(tmp_path, "inventory.json", doc)
    result = main(_cli_args(events_path, inventory_path))
    assert result == 1
    assert "FAIL CLOSED" in capsys.readouterr().err


def _context_input(
    target: str, source_type: str = "attachment:file"
) -> dict[str, object]:
    return {
        "session_id": "s1",
        "timestamp": "t",
        "source_type": source_type,
        "target": target,
        "raw_event_reference": "c1",
    }


def test_audit_matches_context_inputs() -> None:
    result = audit(
        [_event("git status")],
        load_json(INVENTORY),
        [],
        [],
        context_inputs=[
            _context_input(
                "attached notes: docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json"
            )
        ],
        capture_complete=True,
        parser_supported=True,
        audit_executed=True,
    )
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
    assert result["context_inputs_count"] == 1
    assert any(
        m["kind"] == "context_input" and m["source_type"] == "attachment:file"
        for m in result["matches"]
    )


def test_audit_clean_context_inputs_keep_pass() -> None:
    result = audit(
        [_event("git status")],
        load_json(INVENTORY),
        [],
        [],
        context_inputs=[_context_input("fully benign attached text")],
        capture_complete=True,
        parser_supported=True,
        audit_executed=True,
    )
    assert result["verdict"] == "PASS"
    assert result["reason"] == "zero forbidden matches"


def test_audit_kind_labels_are_not_identifiers() -> None:
    # Category labels (kind: commit/path/branch/...) must never be matched as
    # identifiers: a generic label would flag every target containing the
    # word and make a clean run unable to PASS.  Targets that merely contain
    # the words "branch"/"path" must produce zero matches against the real
    # inventory.
    result = audit(
        [_event("git branch --show-current"), _event("Read file_path AGENTS.md")],
        load_json(INVENTORY),
        [],
        [],
        capture_complete=True,
        parser_supported=True,
        audit_executed=True,
    )
    assert result["verdict"] == "PASS"
    assert result["reason"] == "zero forbidden matches"


def test_audit_rejects_malformed_context_input() -> None:
    with pytest.raises(BenchmarkError):
        audit(
            [_event("git status")],
            load_json(INVENTORY),
            [],
            [],
            context_inputs=["not an object"],
            capture_complete=True,
            parser_supported=True,
            audit_executed=True,
        )


# --- Issue #125: contamination identity semantics (current-run own evidence) ---

OWN_RUN_IDENTITY: dict[str, object] = {
    "arm_id": "D",
    "session_id": "19a59af7-02f6-4e51-ba23-f7f2c2e53df1",
    "own_evidence_paths": [
        # validated identity is echoed lowercased (matching normalizes)
        ".agents/evidence.local/task-65-round-2-v2/d",
        ".agents/benchmark-fixtures.local/arm-d-smoke-20260811",
    ],
}


def _audit_with_identity(
    events: list[dict[str, object]],
    *,
    context_inputs: list[dict[str, object]] | None = None,
    cross_arm: list[str] | None = None,
    timeline: list[str] | None = None,
    identity: dict[str, object] | None = OWN_RUN_IDENTITY,
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        audit(
            events,
            load_json(INVENTORY),
            cross_arm or [],
            timeline or [],
            context_inputs=context_inputs,
            capture_complete=True,
            parser_supported=True,
            audit_executed=True,
            current_run_identity=identity,
        ),
    )


def test_audit_current_run_own_evidence_is_allowed() -> None:
    # (A) CURRENT_ARM_OWN_EVIDENCE: the forbidden prior-evidence file name
    # under the current run's OWN evidence namespace is its own freshly
    # written artifact -> exempted (recorded), never a match.
    result = _audit_with_identity(
        [
            _event(
                ".agents/evidence.local/task-65-round-2-v2/D/"
                "arm-d-claude-final-head-smoke-evidence-NON-FORMAL.json"
            )
        ]
    )
    assert result["verdict"] == "PASS"
    assert result["reason"] == "zero forbidden matches"
    assert result["match_count"] == 0
    assert result["exemption_count"] == 1
    assert result["own_evidence_exemptions"][0]["forbidden_identifiers"] == [
        ".agents/evidence.local/task-65-round-2-v2/d/"
        "arm-d-claude-final-head-smoke-evidence-non-formal.json"
    ]
    assert result["current_run_identity"] == OWN_RUN_IDENTITY


def test_audit_prior_evidence_same_root_is_forbidden() -> None:
    # (B) PRIOR_BENCHMARK: prior Task #65 round-2-v2 evidence under the same
    # evidence/validation ROOT but NOT under the current run's own paths is
    # Class 2 forbidden (the generic root is not the problem; the specific
    # prior artifact identity is).
    result = _audit_with_identity(
        [
            _event(
                ".agents/validation.local/task-65-round-2-v2/"
                "arm-d-claude-final-head-NON-FORMAL/result.json"
            )
        ]
    )
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
    assert result["match_count"] == 1
    assert result["exemption_count"] == 0
    match = result["matches"][0]
    assert match["identity_classes"] == ["PRIOR_BENCHMARK_CLASS_2"]
    assert match["forbidden_identifiers"] == [
        ".agents/validation.local/task-65-round-2-v2/arm-d-claude-final-head-non-formal"
    ]


def test_audit_other_arm_current_evidence_is_forbidden() -> None:
    # (C) OTHER_ARM_CURRENT_RUN: another arm's CURRENT-run evidence namespace
    # is Class 3 forbidden even when the current run's own paths are declared
    # (the Class 3 exemption never applies).
    result = _audit_with_identity(
        [
            _event(
                ".agents/evidence.local/task-65-round-2-v2/B/"
                "arm-b-current-run-evidence.json"
            )
        ],
        cross_arm=[".agents/evidence.local/task-65-round-2-v2/B"],
    )
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
    assert result["match_count"] == 1
    match = result["matches"][0]
    assert match["identity_classes"] == ["OTHER_ARM_CURRENT_RUN_CLASS_3"]
    assert match["forbidden_identifiers"] == [
        ".agents/evidence.local/task-65-round-2-v2/b"
    ]


def test_audit_historical_forbidden_sha_in_current_input() -> None:
    # (B) a historical forbidden SHA embedded in CURRENT input (context
    # input, no tool call) -> Class 2 forbidden.
    result = _audit_with_identity(
        [_event("git status")],
        context_inputs=[
            _context_input(
                "attached notes reference a492f0b334f950f2613b4b2204e96bef413355be"
            )
        ],
    )
    assert result["verdict"] == "BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE"
    assert result["match_count"] == 1
    assert result["matches"][0]["kind"] == "context_input"
    assert result["matches"][0]["identity_classes"] == ["PRIOR_BENCHMARK_CLASS_2"]
    assert result["matches"][0]["forbidden_identifiers"] == [
        "a492f0b334f950f2613b4b2204e96bef413355be"
    ]


def test_audit_evidence_root_name_alone_is_not_forbidden() -> None:
    # The generic evidence/validation ROOTS are NOT forbidden identifiers:
    # a bare root mention must produce zero matches against the real
    # inventory (no wholesale root ignoring either way).
    result = _audit_with_identity(
        [_event("ls .agents/evidence.local"), _event("ls .agents/validation.local")]
    )
    assert result["verdict"] == "PASS"
    assert result["reason"] == "zero forbidden matches"
    assert result["match_count"] == 0
    assert result["exemption_count"] == 0


def test_audit_inventory_entry_without_identifiers_fails_closed() -> None:
    # An entry whose locations carry no concrete forbidden identifier
    # forbids nothing and would silently weaken the audit -> fail closed.
    doc = load_json(INVENTORY)
    doc["entries"][13]["locations"] = [{"kind": "external"}]
    with pytest.raises(BenchmarkError):
        audit(
            [_event("git status")],
            doc,
            [],
            [],
            capture_complete=True,
            parser_supported=True,
            audit_executed=True,
        )


def test_audit_malformed_current_run_identity_fails_closed() -> None:
    # A malformed current_run_identity must never silently disable or
    # broaden the exemption -> fail closed.
    with pytest.raises(BenchmarkError):
        _audit_with_identity(
            [_event("git status")],
            identity={"arm_id": "D"},  # missing session_id / own_evidence_paths
        )
    with pytest.raises(BenchmarkError):
        _audit_with_identity(
            [_event("git status")],
            identity={
                "arm_id": "D",
                "session_id": "s1",
                "own_evidence_paths": ["/tmp/not-conductor-local"],
            },
        )
    with pytest.raises(BenchmarkError):
        _audit_with_identity(
            [_event("git status")],
            identity={
                "arm_id": "Z",  # not a registered arm
                "session_id": "s1",
                "own_evidence_paths": [".agents/evidence.local/D"],
            },
        )
