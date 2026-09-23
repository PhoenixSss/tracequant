from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from tracequant.integrations.nautilus.stage4_demo import (
    DEMO_ENVIRONMENT,
    DEMO_INSTRUMENT_ID,
    NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
    NAUTILUS_DISTRIBUTION,
    NAUTILUS_UPSTREAM_COMMIT,
    NAUTILUS_VERSION,
)
from tracequant.integrations.nautilus.stage4_demo_evidence import (
    ACCEPTANCE_SCHEMA,
    EVIDENCE_SCHEMA,
    PRODUCT_STATUS,
    SCENARIOS,
    DemoEvidenceScenario,
    EvidenceClassification,
    Stage4DemoEvidenceError,
    canonical_stage4_demo_acceptance_json,
    canonical_stage4_demo_evidence_json,
    classify_stage4_cleanup,
    classify_stage4_observation,
    finalize_stage4_demo_acceptance,
    finalize_stage4_demo_evidence,
    new_stage4_demo_batch_id,
    stage4_demo_evidence_digest,
    stage4_demo_partition_path,
    validate_stage4_demo_acceptance,
    validate_stage4_demo_evidence,
    validate_stage4_demo_evidence_batch,
)

SOURCE_COMMIT = "1" * 40
LOCK_DIGEST = "2" * 64
CONFIG_DIGEST = "3" * 64
BATCH_ID = "batch_abcdefghijklmnopqrstuv"


def _runtime() -> dict[str, object]:
    return {
        "distribution": NAUTILUS_DISTRIBUTION,
        "version": NAUTILUS_VERSION,
        "upstream_commit": NAUTILUS_UPSTREAM_COMMIT,
        "wheel_sha256": NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
    }


def _instrument() -> dict[str, object]:
    return {
        "id": DEMO_INSTRUMENT_ID,
        "price_precision": 2,
        "price_increment": "0.01",
        "size_precision": 3,
        "size_increment": "0.001",
        "minimum_quantity": "0.001",
        "maximum_quantity": "10.000",
        "minimum_notional": "5.00",
    }


def _observations(scenario: str) -> dict[str, object]:
    if scenario == DemoEvidenceScenario.DATA_TESTER.value:
        return {
            "market_data": {
                "classification": "consistent",
                "quote_count": 3,
                "trade_count": 2,
                "timestamp_valid": True,
            },
            "order": {
                "classification": "not_applicable",
                "submitted": False,
                "accepted": False,
                "terminal": False,
                "active": False,
                "pending": False,
                "ambiguous": False,
            },
            "fill": {
                "classification": "not_applicable",
                "complete": False,
                "partial": False,
                "late": False,
            },
            "position": {
                "classification": "not_applicable",
                "open_count": 0,
                "final_net_quantity": "0",
            },
            "balance": {
                "classification": "not_applicable",
                "before_observed": False,
                "after_observed": False,
                "explained_change": False,
            },
            "account_mode": {
                "classification": "not_applicable",
                "operator_gate_confirmed": False,
                "config_requested": False,
                "canary_complete": False,
                "one_way_confirmed": False,
                "isolated_confirmed": False,
                "leverage_one_confirmed": False,
                "observed_initial_margin": None,
                "allowed_initial_margin_min": None,
                "allowed_initial_margin_max": None,
            },
        }
    fill_complete = scenario != DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL.value
    return {
        "market_data": {
            "classification": "consistent",
            "quote_count": 3,
            "trade_count": 2,
            "timestamp_valid": True,
        },
        "order": {
            "classification": "consistent",
            "submitted": True,
            "accepted": True,
            "terminal": True,
            "active": False,
            "pending": False,
            "ambiguous": False,
        },
        "fill": {
            "classification": "consistent",
            "complete": fill_complete,
            "partial": False,
            "late": False,
        },
        "position": {
            "classification": "consistent",
            "open_count": 0,
            "final_net_quantity": "0",
        },
        "balance": {
            "classification": "consistent",
            "before_observed": True,
            "after_observed": True,
            "explained_change": True,
        },
        "account_mode": {
            "classification": "consistent",
            "operator_gate_confirmed": True,
            "config_requested": True,
            "canary_complete": True,
            "one_way_confirmed": False,
            "isolated_confirmed": False,
            "leverage_one_confirmed": False,
            "observed_initial_margin": None,
            "allowed_initial_margin_min": None,
            "allowed_initial_margin_max": None,
        },
    }


def _cleanup(scenario: str) -> dict[str, object]:
    return {
        "classification": (
            "not_applicable"
            if scenario == DemoEvidenceScenario.DATA_TESTER.value
            else "consistent"
        ),
        "active_order_count": 0,
        "pending_order_count": 0,
        "open_position_count": 0,
        "unresolved_unknown_count": 0,
        "final_net_quantity": "0",
    }


def _evidence_payload(scenario: str) -> dict[str, object]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "source_commit": SOURCE_COMMIT,
        "dependency_lock_sha256": LOCK_DIGEST,
        "runtime": _runtime(),
        "environment": DEMO_ENVIRONMENT,
        "config_digest": CONFIG_DIGEST,
        "batch_id": BATCH_ID,
        "instrument": _instrument(),
        "scenario": scenario,
        "started_at": "2026-09-22T01:02:03Z",
        "ended_at": "2026-09-22T01:03:04.500000Z",
        "result": "PASS",
        "terminal_state": "COMPLETE",
        "observations": _observations(scenario),
        "cleanup": _cleanup(scenario),
        "failure": None,
    }


def _evidence(scenario: str) -> dict[str, object]:
    return finalize_stage4_demo_evidence(_evidence_payload(scenario))


def _nested(record: dict[str, object], *keys: str) -> dict[str, object]:
    value: object = record
    for key in keys:
        value = cast(Mapping[str, object], value)[key]
    return cast(dict[str, object], value)


def _acceptance_payload(evidence_digests: Mapping[str, str]) -> dict[str, object]:
    return {
        "schema": ACCEPTANCE_SCHEMA,
        "source_commit": SOURCE_COMMIT,
        "dependency_lock_sha256": LOCK_DIGEST,
        "runtime": _runtime(),
        "environment": DEMO_ENVIRONMENT,
        "config_digest": CONFIG_DIGEST,
        "batch_id": BATCH_ID,
        "instrument_id": DEMO_INSTRUMENT_ID,
        "evidence_digests": dict(evidence_digests),
        "required_runs": {scenario: "PASS" for scenario in SCENARIOS},
        "final_state": {
            "active_order_count": 0,
            "pending_order_count": 0,
            "open_position_count": 0,
            "unresolved_unknown_count": 0,
            "final_net_quantity": "0",
        },
        "product_status": PRODUCT_STATUS,
        "generated_at": "2026-09-22T01:04:05Z",
    }


def test_stage4_demo_evidence_fails_closed_on_missing_or_conflicting_terminal_facts() -> (
    None
):
    changes: list[tuple[tuple[str, ...], object]] = [
        (("observations", "order", "classification"), "missing"),
        (("observations", "position", "classification"), "conflicting"),
        (("observations", "account_mode", "classification"), "unknown"),
        (("cleanup", "classification"), "cleanup_incomplete"),
        (("observations", "order", "active"), True),
        (("observations", "fill", "partial"), True),
        (("observations", "balance", "before_observed"), False),
        (("cleanup", "unresolved_unknown_count"), 1),
        (("cleanup", "final_net_quantity"), "0.001"),
    ]
    for path, value in changes:
        payload = _evidence_payload(DemoEvidenceScenario.DEMO_STRATEGY.value)
        target = _nested(payload, *path[:-1])
        target[path[-1]] = value
        with pytest.raises(Stage4DemoEvidenceError):
            finalize_stage4_demo_evidence(payload)

    failed = _evidence_payload(DemoEvidenceScenario.DEMO_STRATEGY.value)
    failed["result"] = "FAIL"
    failed["terminal_state"] = "HALTED"
    failed["failure"] = {
        "code": "TERMINAL_FACT_MISSING",
        "phase": "reconciliation",
        "diagnostic_codes": ["POSITION_UNKNOWN"],
    }
    _nested(failed, "observations", "position")["classification"] = "unknown"
    validate_stage4_demo_evidence(finalize_stage4_demo_evidence(failed))


def test_stage4_classification_uses_only_bounded_current_attempt_facts() -> None:
    assert (
        classify_stage4_observation(applicable=True, observed=True)
        is EvidenceClassification.CONSISTENT
    )
    assert (
        classify_stage4_observation(applicable=True, observed=False)
        is EvidenceClassification.MISSING
    )
    assert (
        classify_stage4_observation(applicable=True, observed=True, conflicting=True)
        is EvidenceClassification.CONFLICTING
    )
    assert (
        classify_stage4_observation(
            applicable=True, observed=True, unresolved_unknown=True
        )
        is EvidenceClassification.UNKNOWN
    )
    assert (
        classify_stage4_observation(applicable=False, observed=False)
        is EvidenceClassification.NOT_APPLICABLE
    )
    with pytest.raises(Stage4DemoEvidenceError):
        classify_stage4_observation(applicable=False, observed=True)

    assert (
        classify_stage4_cleanup(
            applicable=True,
            terminal_facts_observed=True,
            conflicting=False,
            active_order_count=0,
            pending_order_count=0,
            open_position_count=0,
            unresolved_unknown_count=0,
            final_net_quantity="0",
        )
        is EvidenceClassification.CONSISTENT
    )
    assert (
        classify_stage4_cleanup(
            applicable=True,
            terminal_facts_observed=True,
            conflicting=False,
            active_order_count=0,
            pending_order_count=0,
            open_position_count=0,
            unresolved_unknown_count=1,
            final_net_quantity="0",
        )
        is EvidenceClassification.CLEANUP_INCOMPLETE
    )


def test_data_tester_not_applicable_is_valid_but_order_scenarios_require_facts() -> (
    None
):
    data_record = _evidence(DemoEvidenceScenario.DATA_TESTER.value)
    validate_stage4_demo_evidence(data_record)

    order_payload = _evidence_payload(
        DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value
    )
    _nested(order_payload, "observations", "fill")["classification"] = "not_applicable"
    with pytest.raises(Stage4DemoEvidenceError):
        finalize_stage4_demo_evidence(order_payload)

    empty_data = _evidence_payload(DemoEvidenceScenario.DATA_TESTER.value)
    _nested(empty_data, "observations", "market_data")["quote_count"] = 0
    with pytest.raises(Stage4DemoEvidenceError):
        finalize_stage4_demo_evidence(empty_data)


def test_order_scenarios_require_their_frozen_fill_outcomes() -> None:
    passive = _evidence_payload(DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL.value)
    _nested(passive, "observations", "fill")["complete"] = True
    with pytest.raises(Stage4DemoEvidenceError, match="zero fill"):
        finalize_stage4_demo_evidence(passive)

    for scenario in (
        DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value,
        DemoEvidenceScenario.DEMO_STRATEGY.value,
    ):
        required_fill = _evidence_payload(scenario)
        _nested(required_fill, "observations", "fill")["complete"] = False
        with pytest.raises(Stage4DemoEvidenceError, match="must be complete"):
            finalize_stage4_demo_evidence(required_fill)


def test_order_scenarios_keep_unproved_rc4_mode_fields_false() -> None:
    payload = _evidence_payload(DemoEvidenceScenario.DEMO_STRATEGY.value)
    account_mode = _nested(payload, "observations", "account_mode")
    validate_stage4_demo_evidence(finalize_stage4_demo_evidence(payload))

    for key in ("one_way_confirmed", "isolated_confirmed", "leverage_one_confirmed"):
        forged = _evidence_payload(DemoEvidenceScenario.DEMO_STRATEGY.value)
        _nested(forged, "observations", "account_mode")[key] = True
        with pytest.raises(Stage4DemoEvidenceError, match="unproved"):
            finalize_stage4_demo_evidence(forged)

    account_mode["observed_initial_margin"] = "0.01"
    validate_stage4_demo_evidence(finalize_stage4_demo_evidence(payload))


@pytest.mark.parametrize(
    "scenario",
    [
        DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL.value,
        DemoEvidenceScenario.DEMO_STRATEGY.value,
    ],
)
def test_passive_order_scenarios_require_a_qualified_quote(scenario: str) -> None:
    payload = _evidence_payload(scenario)
    _nested(payload, "observations", "market_data")["quote_count"] = 0

    with pytest.raises(Stage4DemoEvidenceError, match="at least one quote"):
        finalize_stage4_demo_evidence(payload)

    market_close = _evidence_payload(
        DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value
    )
    _nested(market_close, "observations", "market_data")["quote_count"] = 0
    validate_stage4_demo_evidence(finalize_stage4_demo_evidence(market_close))


@pytest.mark.parametrize(
    "classification_path",
    [
        ("observations", "order", "classification"),
        ("observations", "fill", "classification"),
        ("observations", "position", "classification"),
        ("observations", "balance", "classification"),
        ("observations", "account_mode", "classification"),
        ("cleanup", "classification"),
    ],
)
def test_failed_data_tester_requires_not_applicable_scenario_facts(
    classification_path: tuple[str, ...],
) -> None:
    data_failure = _evidence_payload(DemoEvidenceScenario.DATA_TESTER.value)
    data_failure["result"] = "FAIL"
    data_failure["terminal_state"] = "HALTED"
    data_failure["failure"] = {
        "code": "DATA_TIMEOUT",
        "phase": "data",
        "diagnostic_codes": ["NO_QUOTES"],
    }
    target = _nested(data_failure, *classification_path[:-1])
    target[classification_path[-1]] = "consistent"
    with pytest.raises(Stage4DemoEvidenceError, match="must be not_applicable"):
        finalize_stage4_demo_evidence(data_failure)


def test_failed_evidence_allows_only_wholly_missing_instrument_constraints() -> None:
    data_failure = _evidence_payload(DemoEvidenceScenario.DATA_TESTER.value)
    data_failure["result"] = "FAIL"
    data_failure["terminal_state"] = "HALTED"
    instrument = _nested(data_failure, "instrument")
    for key in instrument:
        if key != "id":
            instrument[key] = None
    market_data = _nested(data_failure, "observations", "market_data")
    market_data.update(
        {
            "classification": "missing",
            "quote_count": 0,
            "trade_count": 0,
            "timestamp_valid": False,
        }
    )
    data_failure["failure"] = {
        "code": "CONNECT_SUBSCRIPTION_FAILED",
        "phase": "data",
        "diagnostic_codes": ["FAILED"],
    }

    validate_stage4_demo_evidence(finalize_stage4_demo_evidence(data_failure))

    incomplete = copy.deepcopy(data_failure)
    _nested(incomplete, "instrument")["price_precision"] = 2
    with pytest.raises(Stage4DemoEvidenceError, match="all null"):
        finalize_stage4_demo_evidence(incomplete)

    passing = _evidence_payload(DemoEvidenceScenario.DATA_TESTER.value)
    passing_instrument = _nested(passing, "instrument")
    for key in passing_instrument:
        if key != "id":
            passing_instrument[key] = None
    with pytest.raises(Stage4DemoEvidenceError, match="all null"):
        finalize_stage4_demo_evidence(passing)


@pytest.mark.parametrize(
    "classification_path",
    [
        ("observations", "order", "classification"),
        ("observations", "fill", "classification"),
        ("observations", "position", "classification"),
        ("observations", "balance", "classification"),
        ("observations", "account_mode", "classification"),
        ("cleanup", "classification"),
    ],
)
def test_failed_order_scenario_rejects_not_applicable_facts(
    classification_path: tuple[str, ...],
) -> None:
    order_failure = _evidence_payload(
        DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value
    )
    order_failure["result"] = "FAIL"
    order_failure["terminal_state"] = "HALTED"
    order_failure["failure"] = {
        "code": "ORDER_TIMEOUT",
        "phase": "execution",
        "diagnostic_codes": ["ORDER_UNKNOWN"],
    }
    target = _nested(order_failure, *classification_path[:-1])
    target[classification_path[-1]] = "not_applicable"
    with pytest.raises(Stage4DemoEvidenceError, match="cannot mark"):
        finalize_stage4_demo_evidence(order_failure)


@pytest.mark.parametrize(
    ("scenario", "failure_code", "diagnostic_code", "order_changes"),
    [
        (
            DemoEvidenceScenario.DEMO_STRATEGY.value,
            "HANDLER_EXCEPTION",
            "HANDLER_EXCEPTION",
            {},
        ),
        (
            DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value,
            "ORDER_REJECTED",
            "ORDER_REJECTED",
            {"accepted": False, "complete": False},
        ),
    ],
)
def test_required_failures_remain_truthful_after_successful_cleanup(
    scenario: str,
    failure_code: str,
    diagnostic_code: str,
    order_changes: dict[str, bool],
) -> None:
    payload = _evidence_payload(scenario)
    payload["result"] = "FAIL"
    payload["terminal_state"] = "HALTED"
    payload["failure"] = {
        "code": failure_code,
        "phase": "execution",
        "diagnostic_codes": [diagnostic_code],
    }
    if "accepted" in order_changes:
        _nested(payload, "observations", "order")["accepted"] = order_changes[
            "accepted"
        ]
    if "complete" in order_changes:
        _nested(payload, "observations", "fill")["complete"] = order_changes["complete"]

    evidence = finalize_stage4_demo_evidence(payload)

    assert evidence["result"] == "FAIL"
    assert evidence["terminal_state"] == "HALTED"
    assert _nested(evidence, "cleanup")["classification"] == "consistent"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_quantity", "0"),
        ("maximum_quantity", "-1"),
        ("minimum_notional", "0"),
        ("minimum_notional", "-1"),
        ("maximum_quantity", "0.0001"),
    ],
)
def test_pass_record_rejects_invalid_optional_instrument_constraints(
    field: str, value: str
) -> None:
    payload = _evidence_payload(DemoEvidenceScenario.DATA_TESTER.value)
    _nested(payload, "instrument")[field] = value

    with pytest.raises(Stage4DemoEvidenceError, match=f"instrument\\.{field}"):
        finalize_stage4_demo_evidence(payload)


def test_batch_rejects_shared_impossible_instrument_constraints() -> None:
    records = [_evidence(scenario) for scenario in SCENARIOS]
    for record in records:
        _nested(record, "instrument")["maximum_quantity"] = "0.0001"
        record["evidence_digest"] = stage4_demo_evidence_digest(record)

    with pytest.raises(Stage4DemoEvidenceError, match="maximum_quantity"):
        validate_stage4_demo_evidence_batch(records)


def test_evidence_schema_digest_json_and_redaction_are_exact_and_deterministic() -> (
    None
):
    first = _evidence(DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value)
    second = _evidence(DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value)
    assert first == second
    rendered = canonical_stage4_demo_evidence_json(first)
    assert rendered == json.dumps(
        first, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    assert "evidence_digest" in first
    assert "acceptance_digest" not in first
    for forbidden in (
        "raw_payload",
        "account_id",
        "/home/operator/private",
        "demo-api-secret",
        "authorization",
    ):
        assert forbidden not in rendered

    extra = copy.deepcopy(_evidence_payload(DemoEvidenceScenario.DATA_TESTER.value))
    extra["raw_payload"] = {"credential": "secret"}
    with pytest.raises(Stage4DemoEvidenceError):
        finalize_stage4_demo_evidence(extra)

    invalid_decimal = _evidence_payload(DemoEvidenceScenario.DATA_TESTER.value)
    _nested(invalid_decimal, "instrument")["size_increment"] = "1e-3"
    with pytest.raises(Stage4DemoEvidenceError):
        finalize_stage4_demo_evidence(invalid_decimal)

    invalid_timestamp = _evidence_payload(DemoEvidenceScenario.DATA_TESTER.value)
    invalid_timestamp["started_at"] = "2026-09-22T09:02:03+08:00"
    with pytest.raises(Stage4DemoEvidenceError):
        finalize_stage4_demo_evidence(invalid_timestamp)

    bad_diagnostic = _evidence_payload(DemoEvidenceScenario.DEMO_STRATEGY.value)
    bad_diagnostic["result"] = "FAIL"
    bad_diagnostic["terminal_state"] = "HALTED"
    bad_diagnostic["failure"] = {
        "code": "/home/operator/raw-payload.json",
        "phase": "cleanup",
        "diagnostic_codes": ["FAILED"],
    }
    with pytest.raises(Stage4DemoEvidenceError):
        finalize_stage4_demo_evidence(bad_diagnostic)


@pytest.mark.parametrize(
    ("field", "credential_or_account_identity"),
    [
        ("code", "DemoApiSecretABC123"),
        ("phase", "BinanceAccount987654321"),
        ("diagnostic_codes", "CredentialDerivedUser42"),
    ],
)
def test_failure_diagnostics_reject_credential_and_account_shaped_values(
    field: str,
    credential_or_account_identity: str,
) -> None:
    payload = _evidence_payload(DemoEvidenceScenario.DEMO_STRATEGY.value)
    payload["result"] = "FAIL"
    payload["terminal_state"] = "HALTED"
    failure: dict[str, object] = {
        "code": "TERMINAL_FACT_MISSING",
        "phase": "reconciliation",
        "diagnostic_codes": ["POSITION_UNKNOWN"],
    }
    if field == "diagnostic_codes":
        failure[field] = [credential_or_account_identity]
    else:
        failure[field] = credential_or_account_identity
    payload["failure"] = failure

    with pytest.raises(Stage4DemoEvidenceError, match=f"failure\\.{field}"):
        finalize_stage4_demo_evidence(payload)


def test_evidence_batch_rejects_identity_drift_and_bad_record_digest() -> None:
    records = [_evidence(scenario) for scenario in SCENARIOS]
    digests = validate_stage4_demo_evidence_batch(records)
    assert tuple(digests) == SCENARIOS

    for field, changed in (
        ("source_commit", "4" * 40),
        ("dependency_lock_sha256", "5" * 64),
        ("config_digest", "6" * 64),
        ("batch_id", "other_abcdefghijklmnopqrstuv"),
    ):
        drifted = copy.deepcopy(records)
        payload = {
            key: value for key, value in drifted[-1].items() if key != "evidence_digest"
        }
        payload[field] = changed
        drifted[-1] = finalize_stage4_demo_evidence(payload)
        with pytest.raises(Stage4DemoEvidenceError, match="identity drifted"):
            validate_stage4_demo_evidence_batch(drifted)

    bad_digest = copy.deepcopy(records)
    bad_digest[-1]["evidence_digest"] = "f" * 64
    with pytest.raises(Stage4DemoEvidenceError, match="digest"):
        validate_stage4_demo_evidence_batch(bad_digest)


def test_acceptance_v1_has_only_one_top_level_digest_and_exact_final_state() -> None:
    records = [_evidence(scenario) for scenario in SCENARIOS]
    evidence_digests = validate_stage4_demo_evidence_batch(records)
    acceptance = finalize_stage4_demo_acceptance(_acceptance_payload(evidence_digests))
    validate_stage4_demo_acceptance(acceptance)
    rendered = canonical_stage4_demo_acceptance_json(acceptance)
    assert rendered.count("acceptance_digest") == 1
    assert "evidence_digest" not in acceptance
    assert "reference_digest" not in rendered
    assert "category_digest" not in rendered

    uncleared = _acceptance_payload(evidence_digests)
    _nested(uncleared, "final_state")["active_order_count"] = 1
    with pytest.raises(Stage4DemoEvidenceError):
        finalize_stage4_demo_acceptance(uncleared)

    extra = _acceptance_payload(evidence_digests)
    extra["runtime_attestation_digest"] = "f" * 64
    with pytest.raises(Stage4DemoEvidenceError):
        finalize_stage4_demo_acceptance(extra)


def test_batch_partition_is_random_external_and_scenario_local(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    external = tmp_path / "external-evidence"
    repository.mkdir()
    external.mkdir()
    first_batch = new_stage4_demo_batch_id()
    second_batch = new_stage4_demo_batch_id()
    assert first_batch != second_batch

    partitions = {
        stage4_demo_partition_path(
            repository_root=repository,
            evidence_root=external,
            batch_id=first_batch,
            scenario=scenario,
        )
        for scenario in SCENARIOS
    }
    assert len(partitions) == 4
    assert all(path.parent == external.resolve() / first_batch for path in partitions)

    with pytest.raises(Stage4DemoEvidenceError, match="outside"):
        stage4_demo_partition_path(
            repository_root=repository,
            evidence_root=repository / "evidence",
            batch_id=first_batch,
            scenario=DemoEvidenceScenario.DATA_TESTER,
        )
    with pytest.raises(Stage4DemoEvidenceError):
        stage4_demo_partition_path(
            repository_root=repository,
            evidence_root=external,
            batch_id="../credential-derived",
            scenario=DemoEvidenceScenario.DATA_TESTER,
        )
