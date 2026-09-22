"""Pure evidence contracts for the bounded Stage 4 Binance Demo."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final, cast

from tracequant.integrations.nautilus.stage4_demo import (
    DEMO_ENVIRONMENT,
    DEMO_INSTRUMENT_ID,
    NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
    NAUTILUS_DISTRIBUTION,
    NAUTILUS_UPSTREAM_COMMIT,
    NAUTILUS_VERSION,
)

EVIDENCE_SCHEMA: Final = "tracequant-stage4-demo-evidence-v1"
ACCEPTANCE_SCHEMA: Final = "tracequant-stage4-demo-acceptance-v1"
PRODUCT_STATUS: Final = "LIVE_NOT_APPROVED"

_SHA256_PATTERN: Final = r"[0-9a-f]{64}"
_GIT_SHA_PATTERN: Final = r"[0-9a-f]{40}"
_BATCH_ID_PATTERN: Final = r"[A-Za-z0-9_-]{22,128}"
_DECIMAL_PATTERN: Final = r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
_TIMESTAMP_PATTERN: Final = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z"
)
_FAILURE_CODES: Final = (
    "CANCEL_FILL_RACE",
    "CONNECT_SUBSCRIPTION_FAILED",
    "CLEANUP_INCOMPLETE",
    "CLEANUP_FAILED",
    "DATA_TIMEOUT",
    "DATA_INVALID",
    "EVENT_SEQUENCE_INVALID",
    "HANDLER_EXCEPTION",
    "IDENTITY_CONFIG_GATE_FAILED",
    "ORDER_AMBIGUOUS",
    "ORDER_REJECTED",
    "ORDER_TIMEOUT",
    "OBSERVATION_CONFLICT",
    "TERMINAL_FACT_CONFLICTING",
    "TERMINAL_FACT_MISSING",
    "TERMINAL_FACT_UNKNOWN",
)
_FAILURE_PHASES: Final = (
    "account_mode",
    "cleanup",
    "data",
    "execution",
    "reconciliation",
)
_DIAGNOSTIC_CODES: Final = (
    "ACCOUNT_MODE_UNKNOWN",
    "ACTIVE_ORDERS_REMAIN",
    "BALANCE_UNKNOWN",
    "FAILED",
    "HANDLER_EXCEPTION",
    "NO_QUOTES",
    "NO_TRADES",
    "NONZERO_NET_QUANTITY",
    "OPEN_POSITION_REMAINS",
    "ORDER_REJECTED",
    "ORDER_UNKNOWN",
    "PENDING_ORDERS_REMAIN",
    "POSITION_UNKNOWN",
    "UNRESOLVED_UNKNOWN",
)


class Stage4DemoEvidenceError(ValueError):
    """Raised when a Stage 4 evidence value fails its frozen contract."""


class EvidenceClassification(StrEnum):
    CONSISTENT = "consistent"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    CLEANUP_INCOMPLETE = "cleanup_incomplete"


class DemoEvidenceScenario(StrEnum):
    DATA_TESTER = "data_tester"
    EXEC_TESTER_MARKET_CLOSE = "exec_tester_market_close"
    EXEC_TESTER_PASSIVE_CANCEL = "exec_tester_passive_cancel"
    DEMO_STRATEGY = "demo_strategy"


SCENARIOS: Final = (
    "data_tester",
    "exec_tester_market_close",
    "exec_tester_passive_cancel",
    "demo_strategy",
)
_ORDER_ENABLED_SCENARIOS: Final = SCENARIOS[1:]
_PASSIVE_ORDER_SCENARIOS: Final = (
    DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL.value,
    DemoEvidenceScenario.DEMO_STRATEGY.value,
)
_CLASSIFICATIONS: Final = (
    "consistent",
    "missing",
    "conflicting",
    "unknown",
    "not_applicable",
    "cleanup_incomplete",
)

_EVIDENCE_KEYS: Final = (
    "schema",
    "source_commit",
    "dependency_lock_sha256",
    "runtime",
    "environment",
    "config_digest",
    "batch_id",
    "instrument",
    "scenario",
    "started_at",
    "ended_at",
    "result",
    "terminal_state",
    "observations",
    "cleanup",
    "failure",
    "evidence_digest",
)
_ACCEPTANCE_KEYS: Final = (
    "schema",
    "source_commit",
    "dependency_lock_sha256",
    "runtime",
    "environment",
    "config_digest",
    "batch_id",
    "instrument_id",
    "evidence_digests",
    "required_runs",
    "final_state",
    "product_status",
    "generated_at",
    "acceptance_digest",
)
_RUNTIME_KEYS: Final = (
    "distribution",
    "version",
    "upstream_commit",
    "wheel_sha256",
)
_INSTRUMENT_KEYS: Final = (
    "id",
    "price_precision",
    "price_increment",
    "size_precision",
    "size_increment",
    "minimum_quantity",
    "maximum_quantity",
    "minimum_notional",
)
_OBSERVATION_KEYS: Final = (
    "market_data",
    "order",
    "fill",
    "position",
    "balance",
    "account_mode",
)
_MARKET_DATA_KEYS: Final = (
    "classification",
    "quote_count",
    "trade_count",
    "timestamp_valid",
)
_ORDER_KEYS: Final = (
    "classification",
    "submitted",
    "accepted",
    "terminal",
    "active",
    "pending",
    "ambiguous",
)
_FILL_KEYS: Final = ("classification", "complete", "partial", "late")
_POSITION_KEYS: Final = (
    "classification",
    "open_count",
    "final_net_quantity",
)
_BALANCE_KEYS: Final = (
    "classification",
    "before_observed",
    "after_observed",
    "explained_change",
)
_ACCOUNT_MODE_KEYS: Final = (
    "classification",
    "operator_gate_confirmed",
    "canary_complete",
    "one_way_confirmed",
    "isolated_confirmed",
    "leverage_one_confirmed",
    "observed_initial_margin",
    "allowed_initial_margin_min",
    "allowed_initial_margin_max",
)
_CLEANUP_KEYS: Final = (
    "classification",
    "active_order_count",
    "pending_order_count",
    "open_position_count",
    "unresolved_unknown_count",
    "final_net_quantity",
)
_FAILURE_KEYS: Final = ("code", "phase", "diagnostic_codes")
_FINAL_STATE_KEYS: Final = (
    "active_order_count",
    "pending_order_count",
    "open_position_count",
    "unresolved_unknown_count",
    "final_net_quantity",
)


def new_stage4_demo_batch_id() -> str:
    """Return a credential-independent opaque identifier for one Demo batch."""
    return secrets.token_urlsafe(24)


def classify_stage4_observation(
    *,
    applicable: bool,
    observed: bool,
    conflicting: bool = False,
    unresolved_unknown: bool = False,
) -> EvidenceClassification:
    """Classify one required category from current-attempt public facts."""
    _require_bool(applicable, "applicable")
    _require_bool(observed, "observed")
    _require_bool(conflicting, "conflicting")
    _require_bool(unresolved_unknown, "unresolved_unknown")
    if not applicable:
        if observed or conflicting or unresolved_unknown:
            raise Stage4DemoEvidenceError(
                "not_applicable observations cannot contain current-attempt facts"
            )
        return EvidenceClassification.NOT_APPLICABLE
    if conflicting:
        return EvidenceClassification.CONFLICTING
    if unresolved_unknown:
        return EvidenceClassification.UNKNOWN
    if not observed:
        return EvidenceClassification.MISSING
    return EvidenceClassification.CONSISTENT


def classify_stage4_cleanup(
    *,
    applicable: bool,
    terminal_facts_observed: bool,
    conflicting: bool,
    active_order_count: int,
    pending_order_count: int,
    open_position_count: int,
    unresolved_unknown_count: int,
    final_net_quantity: str,
) -> EvidenceClassification:
    """Classify cleanup without maintaining an order or accounting shadow."""
    _require_bool(applicable, "applicable")
    _require_bool(terminal_facts_observed, "terminal_facts_observed")
    _require_bool(conflicting, "conflicting")
    counts = (
        _require_nonnegative_int(active_order_count, "active_order_count"),
        _require_nonnegative_int(pending_order_count, "pending_order_count"),
        _require_nonnegative_int(open_position_count, "open_position_count"),
        _require_nonnegative_int(unresolved_unknown_count, "unresolved_unknown_count"),
    )
    net_quantity = _require_decimal(final_net_quantity, "final_net_quantity")
    if not applicable:
        if terminal_facts_observed or conflicting or any(counts) or net_quantity != 0:
            raise Stage4DemoEvidenceError(
                "not_applicable cleanup cannot contain execution facts"
            )
        return EvidenceClassification.NOT_APPLICABLE
    if conflicting:
        return EvidenceClassification.CONFLICTING
    if not terminal_facts_observed:
        return EvidenceClassification.MISSING
    if any(counts) or net_quantity != 0:
        return EvidenceClassification.CLEANUP_INCOMPLETE
    return EvidenceClassification.CONSISTENT


def stage4_demo_partition_path(
    *,
    repository_root: Path,
    evidence_root: Path,
    batch_id: str,
    scenario: DemoEvidenceScenario | str,
) -> Path:
    """Return one scenario's external logical partition without creating it."""
    repository = repository_root.resolve()
    external_root = evidence_root.resolve()
    _validate_batch_id(batch_id)
    scenario_value = _scenario_value(scenario)
    if external_root == repository or repository in external_root.parents:
        raise Stage4DemoEvidenceError(
            "Stage 4 Demo evidence root must be outside the source repository"
        )
    return external_root / batch_id / scenario_value


def stage4_demo_evidence_digest(record: Mapping[str, object]) -> str:
    """Compute the sole EvidenceV1 digest after removing its own field."""
    payload = dict(record)
    payload.pop("evidence_digest", None)
    return _digest(payload)


def finalize_stage4_demo_evidence(
    payload_without_digest: Mapping[str, object],
) -> dict[str, object]:
    """Copy, digest, and validate one complete EvidenceV1 payload."""
    if "evidence_digest" in payload_without_digest:
        raise Stage4DemoEvidenceError("EvidenceV1 payload already has its digest")
    record = _json_object_copy(payload_without_digest, "EvidenceV1 payload")
    record["evidence_digest"] = _digest(record)
    validate_stage4_demo_evidence(record)
    return record


def validate_stage4_demo_evidence(record: Mapping[str, object]) -> None:
    """Validate one EvidenceV1 record and its fail-closed terminal state."""
    _require_keys(record, _EVIDENCE_KEYS, "EvidenceV1")
    _require_literal(record["schema"], EVIDENCE_SCHEMA, "schema")
    _require_pattern(record["source_commit"], _GIT_SHA_PATTERN, "source_commit")
    _require_sha256(record["dependency_lock_sha256"], "dependency_lock_sha256")
    _validate_runtime(_require_mapping(record["runtime"], "runtime"))
    _require_literal(record["environment"], DEMO_ENVIRONMENT, "environment")
    _require_sha256(record["config_digest"], "config_digest")
    _validate_batch_id(record["batch_id"])
    _validate_instrument(_require_mapping(record["instrument"], "instrument"))
    scenario = _require_one_of(record["scenario"], frozenset(SCENARIOS), "scenario")
    started_at = _require_timestamp(record["started_at"], "started_at")
    ended_at = _require_timestamp(record["ended_at"], "ended_at")
    if ended_at < started_at:
        raise Stage4DemoEvidenceError("ended_at precedes started_at")

    result = _require_one_of(record["result"], frozenset({"PASS", "FAIL"}), "result")
    terminal = _require_one_of(
        record["terminal_state"], frozenset({"COMPLETE", "HALTED"}), "terminal_state"
    )
    observations = _require_mapping(record["observations"], "observations")
    cleanup = _require_mapping(record["cleanup"], "cleanup")
    _validate_observations(observations)
    _validate_cleanup(cleanup)
    _validate_failure(record["failure"])
    _validate_terminal_contract(
        scenario=scenario,
        result=result,
        terminal=terminal,
        observations=observations,
        cleanup=cleanup,
        failure=record["failure"],
    )
    _require_sha256(record["evidence_digest"], "evidence_digest")
    if record["evidence_digest"] != stage4_demo_evidence_digest(record):
        raise Stage4DemoEvidenceError("EvidenceV1 digest does not match its payload")


def canonical_stage4_demo_evidence_json(record: Mapping[str, object]) -> str:
    """Return validated compact sorted EvidenceV1 JSON."""
    validate_stage4_demo_evidence(record)
    return _canonical_json(record)


def validate_stage4_demo_evidence_batch(
    records: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Validate the four passing records as one immutable acceptance candidate."""
    if len(records) != len(SCENARIOS):
        raise Stage4DemoEvidenceError("a Demo batch requires exactly four records")
    by_scenario: dict[str, Mapping[str, object]] = {}
    for record in records:
        validate_stage4_demo_evidence(record)
        scenario = cast(str, record["scenario"])
        if scenario in by_scenario:
            raise Stage4DemoEvidenceError("a Demo batch contains duplicate scenarios")
        if record["result"] != "PASS" or record["terminal_state"] != "COMPLETE":
            raise Stage4DemoEvidenceError("a failed Demo run cannot enter acceptance")
        by_scenario[scenario] = record
    if frozenset(by_scenario) != frozenset(SCENARIOS):
        raise Stage4DemoEvidenceError("a Demo batch does not cover every scenario")

    first = by_scenario[SCENARIOS[0]]
    identity_keys = (
        "source_commit",
        "dependency_lock_sha256",
        "runtime",
        "environment",
        "config_digest",
        "batch_id",
        "instrument",
    )
    for record in by_scenario.values():
        if any(record[key] != first[key] for key in identity_keys):
            raise Stage4DemoEvidenceError("Demo batch evidence identity drifted")
    return {
        scenario: cast(str, by_scenario[scenario]["evidence_digest"])
        for scenario in SCENARIOS
    }


def stage4_demo_acceptance_digest(record: Mapping[str, object]) -> str:
    """Compute the sole AcceptanceV1 digest after removing its own field."""
    payload = dict(record)
    payload.pop("acceptance_digest", None)
    return _digest(payload)


def finalize_stage4_demo_acceptance(
    payload_without_digest: Mapping[str, object],
) -> dict[str, object]:
    """Copy, digest, and validate an already-aggregated AcceptanceV1 payload."""
    if "acceptance_digest" in payload_without_digest:
        raise Stage4DemoEvidenceError("AcceptanceV1 payload already has its digest")
    record = _json_object_copy(payload_without_digest, "AcceptanceV1 payload")
    record["acceptance_digest"] = _digest(record)
    validate_stage4_demo_acceptance(record)
    return record


def validate_stage4_demo_acceptance(record: Mapping[str, object]) -> None:
    """Validate the exact final acceptance schema without publishing it."""
    _require_keys(record, _ACCEPTANCE_KEYS, "AcceptanceV1")
    _require_literal(record["schema"], ACCEPTANCE_SCHEMA, "schema")
    _require_pattern(record["source_commit"], _GIT_SHA_PATTERN, "source_commit")
    _require_sha256(record["dependency_lock_sha256"], "dependency_lock_sha256")
    _validate_runtime(_require_mapping(record["runtime"], "runtime"))
    _require_literal(record["environment"], DEMO_ENVIRONMENT, "environment")
    _require_sha256(record["config_digest"], "config_digest")
    _validate_batch_id(record["batch_id"])
    _require_literal(record["instrument_id"], DEMO_INSTRUMENT_ID, "instrument_id")

    evidence_digests = _require_mapping(record["evidence_digests"], "evidence_digests")
    required_runs = _require_mapping(record["required_runs"], "required_runs")
    _require_keys(evidence_digests, frozenset(SCENARIOS), "evidence_digests")
    _require_keys(required_runs, frozenset(SCENARIOS), "required_runs")
    for scenario in SCENARIOS:
        _require_sha256(evidence_digests[scenario], f"evidence_digests.{scenario}")
        _require_literal(required_runs[scenario], "PASS", f"required_runs.{scenario}")

    final_state = _require_mapping(record["final_state"], "final_state")
    _require_keys(final_state, _FINAL_STATE_KEYS, "final_state")
    for key in _keys_except(_FINAL_STATE_KEYS, "final_net_quantity"):
        if _require_nonnegative_int(final_state[key], f"final_state.{key}") != 0:
            raise Stage4DemoEvidenceError("AcceptanceV1 final state is not cleared")
    _require_literal(
        final_state["final_net_quantity"], "0", "final_state.final_net_quantity"
    )
    _require_literal(record["product_status"], PRODUCT_STATUS, "product_status")
    _require_timestamp(record["generated_at"], "generated_at")
    _require_sha256(record["acceptance_digest"], "acceptance_digest")
    if record["acceptance_digest"] != stage4_demo_acceptance_digest(record):
        raise Stage4DemoEvidenceError("AcceptanceV1 digest does not match its payload")


def canonical_stage4_demo_acceptance_json(record: Mapping[str, object]) -> str:
    """Return validated compact sorted AcceptanceV1 JSON."""
    validate_stage4_demo_acceptance(record)
    return _canonical_json(record)


def _validate_runtime(runtime: Mapping[str, object]) -> None:
    _require_keys(runtime, _RUNTIME_KEYS, "runtime")
    expected = {
        "distribution": NAUTILUS_DISTRIBUTION,
        "version": NAUTILUS_VERSION,
        "upstream_commit": NAUTILUS_UPSTREAM_COMMIT,
        "wheel_sha256": NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
    }
    for key, value in expected.items():
        _require_literal(runtime[key], value, f"runtime.{key}")


def _validate_instrument(instrument: Mapping[str, object]) -> None:
    _require_keys(instrument, _INSTRUMENT_KEYS, "instrument")
    _require_literal(instrument["id"], DEMO_INSTRUMENT_ID, "instrument.id")
    _require_nonnegative_int(
        instrument["price_precision"], "instrument.price_precision"
    )
    _require_decimal(
        instrument["price_increment"], "instrument.price_increment", positive=True
    )
    _require_nonnegative_int(instrument["size_precision"], "instrument.size_precision")
    _require_decimal(
        instrument["size_increment"], "instrument.size_increment", positive=True
    )
    minimum_quantity = _require_decimal(
        instrument["minimum_quantity"], "instrument.minimum_quantity", positive=True
    )
    maximum_quantity = _require_optional_decimal(
        instrument["maximum_quantity"],
        "instrument.maximum_quantity",
        positive=True,
    )
    _require_optional_decimal(
        instrument["minimum_notional"],
        "instrument.minimum_notional",
        positive=True,
    )
    if maximum_quantity is not None and maximum_quantity < minimum_quantity:
        raise Stage4DemoEvidenceError(
            "instrument.maximum_quantity is below instrument.minimum_quantity"
        )


def _validate_observations(observations: Mapping[str, object]) -> None:
    _require_keys(observations, _OBSERVATION_KEYS, "observations")
    market_data = _require_mapping(
        observations["market_data"], "observations.market_data"
    )
    _require_keys(market_data, _MARKET_DATA_KEYS, "observations.market_data")
    _require_classification(market_data["classification"], "observations.market_data")
    _require_nonnegative_int(
        market_data["quote_count"], "observations.market_data.quote_count"
    )
    _require_nonnegative_int(
        market_data["trade_count"], "observations.market_data.trade_count"
    )
    _require_bool(
        market_data["timestamp_valid"], "observations.market_data.timestamp_valid"
    )

    order = _require_mapping(observations["order"], "observations.order")
    _require_keys(order, _ORDER_KEYS, "observations.order")
    _require_classification(order["classification"], "observations.order")
    for key in _keys_except(_ORDER_KEYS, "classification"):
        _require_bool(order[key], f"observations.order.{key}")

    fill = _require_mapping(observations["fill"], "observations.fill")
    _require_keys(fill, _FILL_KEYS, "observations.fill")
    _require_classification(fill["classification"], "observations.fill")
    for key in _keys_except(_FILL_KEYS, "classification"):
        _require_bool(fill[key], f"observations.fill.{key}")

    position = _require_mapping(observations["position"], "observations.position")
    _require_keys(position, _POSITION_KEYS, "observations.position")
    _require_classification(position["classification"], "observations.position")
    _require_nonnegative_int(position["open_count"], "observations.position.open_count")
    _require_decimal(
        position["final_net_quantity"], "observations.position.final_net_quantity"
    )

    balance = _require_mapping(observations["balance"], "observations.balance")
    _require_keys(balance, _BALANCE_KEYS, "observations.balance")
    _require_classification(balance["classification"], "observations.balance")
    for key in _keys_except(_BALANCE_KEYS, "classification"):
        _require_bool(balance[key], f"observations.balance.{key}")

    account_mode = _require_mapping(
        observations["account_mode"], "observations.account_mode"
    )
    _require_keys(account_mode, _ACCOUNT_MODE_KEYS, "observations.account_mode")
    _require_classification(account_mode["classification"], "observations.account_mode")
    for key in _keys_except(
        _ACCOUNT_MODE_KEYS,
        "classification",
        "observed_initial_margin",
        "allowed_initial_margin_min",
        "allowed_initial_margin_max",
    ):
        _require_bool(account_mode[key], f"observations.account_mode.{key}")
    _require_optional_decimal(
        account_mode["observed_initial_margin"],
        "observations.account_mode.observed_initial_margin",
    )
    _require_optional_decimal(
        account_mode["allowed_initial_margin_min"],
        "observations.account_mode.allowed_initial_margin_min",
    )
    _require_optional_decimal(
        account_mode["allowed_initial_margin_max"],
        "observations.account_mode.allowed_initial_margin_max",
    )


def _validate_cleanup(cleanup: Mapping[str, object]) -> None:
    _require_keys(cleanup, _CLEANUP_KEYS, "cleanup")
    _require_classification(cleanup["classification"], "cleanup")
    for key in _keys_except(_CLEANUP_KEYS, "classification", "final_net_quantity"):
        _require_nonnegative_int(cleanup[key], f"cleanup.{key}")
    _require_decimal(cleanup["final_net_quantity"], "cleanup.final_net_quantity")


def _validate_failure(failure: object) -> None:
    if failure is None:
        return
    value = _require_mapping(failure, "failure")
    _require_keys(value, _FAILURE_KEYS, "failure")
    _require_one_of(value["code"], _FAILURE_CODES, "failure.code")
    _require_one_of(value["phase"], _FAILURE_PHASES, "failure.phase")
    codes = value["diagnostic_codes"]
    if not isinstance(codes, list) or not codes:
        raise Stage4DemoEvidenceError(
            "failure.diagnostic_codes must be a non-empty list"
        )
    seen: set[str] = set()
    for index, code in enumerate(codes):
        validated = _require_one_of(
            code, _DIAGNOSTIC_CODES, f"failure.diagnostic_codes[{index}]"
        )
        if validated in seen:
            raise Stage4DemoEvidenceError("failure diagnostic codes must be unique")
        seen.add(validated)


def _validate_terminal_contract(
    *,
    scenario: str,
    result: str,
    terminal: str,
    observations: Mapping[str, object],
    cleanup: Mapping[str, object],
    failure: object,
) -> None:
    if (result, terminal) not in {("PASS", "COMPLETE"), ("FAIL", "HALTED")}:
        raise Stage4DemoEvidenceError("result and terminal_state are inconsistent")

    execution_names = ("order", "fill", "position", "balance", "account_mode")
    if scenario == DemoEvidenceScenario.DATA_TESTER.value:
        for name in execution_names:
            observation = cast(Mapping[str, object], observations[name])
            if (
                observation["classification"]
                != EvidenceClassification.NOT_APPLICABLE.value
            ):
                raise Stage4DemoEvidenceError(
                    "DataTester execution and account facts must be not_applicable"
                )
        if cleanup["classification"] != EvidenceClassification.NOT_APPLICABLE.value:
            raise Stage4DemoEvidenceError("DataTester cleanup must be not_applicable")
        _require_neutral_data_tester_state(observations, cleanup)
    else:
        if scenario not in _ORDER_ENABLED_SCENARIOS:
            raise Stage4DemoEvidenceError("scenario is outside the Stage 4 contract")
        for name in execution_names:
            observation = cast(Mapping[str, object], observations[name])
            if (
                observation["classification"]
                == EvidenceClassification.NOT_APPLICABLE.value
            ):
                raise Stage4DemoEvidenceError(
                    "order-enabled evidence cannot mark terminal facts not_applicable"
                )
        if cleanup["classification"] == EvidenceClassification.NOT_APPLICABLE.value:
            raise Stage4DemoEvidenceError(
                "order-enabled evidence cannot mark cleanup not_applicable"
            )

    if result == "FAIL":
        if failure is None:
            raise Stage4DemoEvidenceError("failed evidence requires a bounded failure")
        return
    if failure is not None:
        raise Stage4DemoEvidenceError("passing evidence cannot contain a failure")

    market_data = cast(Mapping[str, object], observations["market_data"])
    if market_data["classification"] != EvidenceClassification.CONSISTENT.value:
        raise Stage4DemoEvidenceError(
            "passing evidence requires consistent market data"
        )
    if market_data["timestamp_valid"] is not True:
        raise Stage4DemoEvidenceError(
            "passing evidence requires valid market timestamps"
        )

    if scenario == DemoEvidenceScenario.DATA_TESTER.value:
        if market_data["quote_count"] == 0 or market_data["trade_count"] == 0:
            raise Stage4DemoEvidenceError(
                "DataTester requires at least one quote and one trade"
            )
        return
    if scenario in _PASSIVE_ORDER_SCENARIOS and market_data["quote_count"] == 0:
        raise Stage4DemoEvidenceError(
            "passive-order evidence requires at least one quote"
        )

    for name in execution_names:
        observation = cast(Mapping[str, object], observations[name])
        if observation["classification"] != EvidenceClassification.CONSISTENT.value:
            raise Stage4DemoEvidenceError(
                "passing order-enabled evidence requires consistent terminal facts"
            )
    if cleanup["classification"] != EvidenceClassification.CONSISTENT.value:
        raise Stage4DemoEvidenceError("passing order-enabled evidence requires cleanup")
    _require_cleared_order_state(scenario, observations, cleanup)


def _require_neutral_data_tester_state(
    observations: Mapping[str, object], cleanup: Mapping[str, object]
) -> None:
    order = cast(Mapping[str, object], observations["order"])
    fill = cast(Mapping[str, object], observations["fill"])
    position = cast(Mapping[str, object], observations["position"])
    balance = cast(Mapping[str, object], observations["balance"])
    account = cast(Mapping[str, object], observations["account_mode"])
    if any(
        cast(bool, order[key]) for key in _keys_except(_ORDER_KEYS, "classification")
    ):
        raise Stage4DemoEvidenceError("DataTester order facts must be neutral")
    if any(cast(bool, fill[key]) for key in _keys_except(_FILL_KEYS, "classification")):
        raise Stage4DemoEvidenceError("DataTester fill facts must be neutral")
    if position["open_count"] != 0 or position["final_net_quantity"] != "0":
        raise Stage4DemoEvidenceError("DataTester position facts must be neutral")
    if any(
        cast(bool, balance[key])
        for key in _keys_except(_BALANCE_KEYS, "classification")
    ):
        raise Stage4DemoEvidenceError("DataTester balance facts must be neutral")
    account_boolean_keys = _keys_except(
        _ACCOUNT_MODE_KEYS,
        "classification",
        "observed_initial_margin",
        "allowed_initial_margin_min",
        "allowed_initial_margin_max",
    )
    if any(cast(bool, account[key]) for key in account_boolean_keys) or any(
        account[key] is not None
        for key in (
            "observed_initial_margin",
            "allowed_initial_margin_min",
            "allowed_initial_margin_max",
        )
    ):
        raise Stage4DemoEvidenceError("DataTester account-mode facts must be neutral")
    if (
        any(
            cleanup[key] != 0
            for key in _keys_except(
                _CLEANUP_KEYS, "classification", "final_net_quantity"
            )
        )
        or cleanup["final_net_quantity"] != "0"
    ):
        raise Stage4DemoEvidenceError("DataTester cleanup facts must be neutral")


def _require_cleared_order_state(
    scenario: str,
    observations: Mapping[str, object],
    cleanup: Mapping[str, object],
) -> None:
    order = cast(Mapping[str, object], observations["order"])
    fill = cast(Mapping[str, object], observations["fill"])
    position = cast(Mapping[str, object], observations["position"])
    balance = cast(Mapping[str, object], observations["balance"])
    account = cast(Mapping[str, object], observations["account_mode"])
    if not all(order[key] is True for key in ("submitted", "accepted", "terminal")):
        raise Stage4DemoEvidenceError("order lifecycle is incomplete")
    if any(order[key] is True for key in ("active", "pending", "ambiguous")):
        raise Stage4DemoEvidenceError(
            "order lifecycle did not reach a known terminal state"
        )
    if fill["partial"] is True or fill["late"] is True:
        raise Stage4DemoEvidenceError("partial or late fills cannot pass")
    if scenario == DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL.value:
        if fill["complete"] is True:
            raise Stage4DemoEvidenceError("passive-cancel evidence requires zero fill")
    elif fill["complete"] is not True:
        raise Stage4DemoEvidenceError("required market fills must be complete")
    if position["open_count"] != 0 or position["final_net_quantity"] != "0":
        raise Stage4DemoEvidenceError("position is not flat")
    if not all(
        balance[key] is True
        for key in ("before_observed", "after_observed", "explained_change")
    ):
        raise Stage4DemoEvidenceError("balance proof is incomplete")
    if (
        any(
            cleanup[key] != 0
            for key in _keys_except(
                _CLEANUP_KEYS, "classification", "final_net_quantity"
            )
        )
        or cleanup["final_net_quantity"] != "0"
    ):
        raise Stage4DemoEvidenceError(
            "cleanup did not prove zero orders, flat, and known"
        )
    for key in (
        "operator_gate_confirmed",
        "canary_complete",
        "one_way_confirmed",
        "isolated_confirmed",
        "leverage_one_confirmed",
    ):
        if account[key] is not True:
            raise Stage4DemoEvidenceError("account-mode proof is incomplete")
    observed = _optional_decimal_value(account["observed_initial_margin"])
    allowed_min = _optional_decimal_value(account["allowed_initial_margin_min"])
    allowed_max = _optional_decimal_value(account["allowed_initial_margin_max"])
    if observed is None or allowed_min is None or allowed_max is None:
        raise Stage4DemoEvidenceError("initial-margin proof is incomplete")
    if observed <= 0:
        raise Stage4DemoEvidenceError("observed initial margin must be positive")
    if allowed_min > allowed_max or not allowed_min <= observed <= allowed_max:
        raise Stage4DemoEvidenceError(
            "observed initial margin is outside its allowed range"
        )


def _scenario_value(scenario: DemoEvidenceScenario | str) -> str:
    value = scenario.value if isinstance(scenario, DemoEvidenceScenario) else scenario
    return _require_one_of(value, frozenset(SCENARIOS), "scenario")


def _validate_batch_id(value: object) -> str:
    return _require_pattern(value, _BATCH_ID_PATTERN, "batch_id")


def _require_classification(value: object, context: str) -> str:
    return _require_one_of(value, _CLASSIFICATIONS, f"{context}.classification")


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise Stage4DemoEvidenceError(f"{context} must be a JSON object")
    return cast(Mapping[str, object], value)


def _require_keys(
    value: Mapping[str, object], expected: Collection[str], context: str
) -> None:
    actual = frozenset(value)
    expected_keys = frozenset(expected)
    if actual != expected_keys:
        missing = sorted(expected_keys - actual)
        extra = sorted(actual - expected_keys)
        raise Stage4DemoEvidenceError(
            f"{context} fields do not match the frozen schema; "
            f"missing={missing}, extra={extra}"
        )


def _require_literal(value: object, expected: str, context: str) -> str:
    if value != expected or not isinstance(value, str):
        raise Stage4DemoEvidenceError(f"{context} must be {expected!r}")
    return value


def _require_one_of(value: object, allowed: Collection[str], context: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise Stage4DemoEvidenceError(f"{context} is outside the frozen values")
    return value


def _require_pattern(value: object, pattern: str, context: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise Stage4DemoEvidenceError(f"{context} has an invalid representation")
    return value


def _require_sha256(value: object, context: str) -> str:
    return _require_pattern(value, _SHA256_PATTERN, context)


def _require_bool(value: object, context: str) -> bool:
    if type(value) is not bool:
        raise Stage4DemoEvidenceError(f"{context} must be a boolean")
    return value


def _require_nonnegative_int(value: object, context: str) -> int:
    if type(value) is not int or value < 0:
        raise Stage4DemoEvidenceError(f"{context} must be an unsigned integer")
    return value


def _require_decimal(value: object, context: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str) or re.fullmatch(_DECIMAL_PATTERN, value) is None:
        raise Stage4DemoEvidenceError(
            f"{context} must be a non-exponent decimal string"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise Stage4DemoEvidenceError(f"{context} is not a decimal") from error
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise Stage4DemoEvidenceError(f"{context} is outside the allowed decimal range")
    if parsed == 0 and value.startswith("-"):
        raise Stage4DemoEvidenceError(f"{context} must not use negative zero")
    return parsed


def _require_optional_decimal(
    value: object, context: str, *, positive: bool = False
) -> Decimal | None:
    if value is None:
        return None
    return _require_decimal(value, context, positive=positive)


def _optional_decimal_value(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(cast(str, value))


def _require_timestamp(value: object, context: str) -> datetime:
    text = _require_pattern(value, _TIMESTAMP_PATTERN, context)
    try:
        parsed = datetime.fromisoformat(text.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise Stage4DemoEvidenceError(f"{context} is not a valid timestamp") from error
    if parsed.tzinfo != UTC:
        raise Stage4DemoEvidenceError(f"{context} must use UTC Z")
    return parsed


def _json_object_copy(value: Mapping[str, object], context: str) -> dict[str, object]:
    try:
        encoded = _canonical_json(value)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise Stage4DemoEvidenceError(f"{context} is not JSON serializable") from error
    if not isinstance(decoded, dict):
        raise Stage4DemoEvidenceError(f"{context} must be a JSON object")
    return cast(dict[str, object], decoded)


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _keys_except(keys: Sequence[str], *excluded: str) -> tuple[str, ...]:
    return tuple(key for key in keys if key not in excluded)
