"""Bounded official ExecTester entry for Stage 4 Binance Demo execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, cast

from nautilus_trader.adapters.binance import (
    BinanceDataClientConfig,
    BinanceDataClientFactory,
    BinanceEnvironment,
    BinanceExecutionClientFactory,
    BinanceInstrumentProviderConfig,
    BinanceProductType,
)
from nautilus_trader.common import Cache, Environment
from nautilus_trader.live import LiveNode, LiveNodeHandle, LiveRiskEngineConfig
from nautilus_trader.model import (
    AccountId,
    ClientId,
    CryptoPerpetual,
    InstrumentId,
    OrderSide,
    Position,
    Price,
    Quantity,
    StrategyId,
    TraderId,
)
from nautilus_trader.testkit import DataTesterConfig, ExecTesterConfig
from nautilus_trader.trading import Strategy, StrategyConfig

from tracequant.integrations.nautilus import stage4_demo
from tracequant.integrations.nautilus.stage4_demo import (
    DEADLINE_SECONDS,
    DEMO_ENVIRONMENT,
    DEMO_INSTRUMENT_ID,
    NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
    NAUTILUS_DISTRIBUTION,
    NAUTILUS_UPSTREAM_COMMIT,
    NAUTILUS_VERSION,
    AdmittedDemoAttempt,
    DeadlinePhase,
    DemoAdmissionBatch,
    FrozenDemoConfig,
    RuntimeIdentity,
    Stage4DemoAdmissionError,
    admit_current_demo_attempt,
    build_execution_client_config,
    exact_reduce_only_quantity,
    minimum_order_quantity,
)
from tracequant.integrations.nautilus.stage4_demo_evidence import (
    EVIDENCE_SCHEMA,
    DemoEvidenceScenario,
    EvidenceClassification,
    canonical_stage4_demo_evidence_json,
    finalize_stage4_demo_evidence,
    stage4_demo_partition_path,
)

EXEC_TESTER_BUILTIN: Final = "ExecTester"
_DATA_TESTER_BUILTIN: Final = "DataTester"
EXEC_CLIENT_NAME: Final = "BINANCE"
EXEC_ACCOUNT_ID: Final = "BINANCE-001"
EXEC_EVIDENCE_FILENAME: Final = "evidence.json"
_EXEC_OBSERVATIONS_FILENAME: Final = "observations.json"
PASSIVE_OFFSET_TICKS: Final = 1
_EXEC_NODE_NAME: Final = "TRACEQUANT-STAGE4-EXEC-TESTER"
_EXEC_TRADER_ID: Final = "TRACEQUANT-001"
_FUTURE_TOLERANCE_NS: Final = 1_000_000_000
_MAX_PRICE_AGE_NS: Final = 5_000_000_000
_POLL_INTERVAL_SECONDS: Final = 0.05
_EXEC_DEADLINE_PHASES: Final = (
    DeadlinePhase.CONNECT,
    DeadlinePhase.READY,
    DeadlinePhase.PRICE_READINESS,
    DeadlinePhase.ORDER_ACCEPTANCE,
    DeadlinePhase.MARKET_OR_REDUCE_ONLY_FILL,
    DeadlinePhase.CANCELLATION,
    DeadlinePhase.RECONCILIATION,
    DeadlinePhase.CLEANUP,
)


class Stage4DemoExecutionError(RuntimeError):
    """Raised when the bounded ExecTester entry cannot proceed safely."""


class _Stage4DemoExecutionRuntimeError(Stage4DemoExecutionError):
    def __init__(self, failure: Stage4DemoExecFailure) -> None:
        super().__init__(failure.code)
        self.failure = failure


class _RuntimeMarketConflict(Stage4DemoExecutionError):
    """The observed runtime market no longer matches the admitted plan."""


@dataclass(frozen=True)
class Stage4DemoExecMarketInput:
    """Fresh public rc4 values used to derive the closed execution plan."""

    instrument: CryptoPerpetual
    best_bid: Price
    best_ask: Price
    mark_price: Price
    quote_ts_event_ns: int
    mark_ts_event_ns: int
    observed_at_ns: int


@dataclass(frozen=True)
class Stage4DemoExecAttemptInput:
    """Attempt-local values acquired only when that attempt is ready to start."""

    market: Stage4DemoExecMarketInput
    operator_token: str = field(repr=False)


@dataclass(frozen=True)
class Stage4DemoExecPhasePlan:
    """One sequential official-tester phase; phases never overlap."""

    name: str
    tester_config: ExecTesterConfig
    run_condition: Literal["always", "cleanup_with_proof", "failure_with_cleanup_proof"]


@dataclass(frozen=True)
class Stage4DemoExecAttemptPlan:
    """One independently admitted logical attempt and its fixed phases."""

    scenario: DemoEvidenceScenario
    runtime: RuntimeIdentity
    frozen_config: FrozenDemoConfig
    evidence_partition: Path
    instrument: CryptoPerpetual
    market_input: Stage4DemoExecMarketInput
    canary_quantity: Quantity
    passive_quantity: Quantity | None
    passive_price: Price | None
    data_client_config: BinanceDataClientConfig
    observer_config: DataTesterConfig
    phases: tuple[Stage4DemoExecPhasePlan, ...]
    builtin_strategy: str
    deadline_seconds: tuple[tuple[DeadlinePhase, int], ...]
    _admission: AdmittedDemoAttempt = field(repr=False, compare=False)


@dataclass(frozen=True)
class Stage4DemoExecPlan:
    """Deferred two-attempt plan; no operator token or admission is cached."""

    evidence_root: Path
    batch_id: str
    acquire_market_close: Callable[[], Stage4DemoExecAttemptInput] = field(
        repr=False,
        compare=False,
    )
    acquire_passive_cancel: Callable[[], Stage4DemoExecAttemptInput] = field(
        repr=False,
        compare=False,
    )
    _batch: DemoAdmissionBatch = field(repr=False, compare=False)


@dataclass(frozen=True)
class Stage4DemoAccountModeObservation:
    """Attempt-local public cache/account facts captured after its canary."""

    operator_gate_confirmed: bool
    canary_complete: bool
    net_position_shape_consistent: bool
    account_scope_consistent: bool
    observed_initial_margin: Decimal | None
    mark_price_min: Decimal | None
    mark_price_max: Decimal | None


@dataclass(frozen=True)
class Stage4DemoExecFailure:
    """Bounded failure values already owned by EvidenceV1."""

    code: str
    phase: str
    diagnostic_codes: tuple[str, ...]


@dataclass(frozen=True)
class Stage4DemoExecObservation:
    """Normalized Nautilus-owned facts for exactly one logical attempt."""

    scenario: DemoEvidenceScenario
    instrument: CryptoPerpetual
    started_at_ns: int
    ended_at_ns: int
    quote_count: int
    trade_count: int
    market_timestamps_valid: bool
    order_submitted: bool
    order_accepted: bool
    order_terminal: bool
    active_order_count: int
    pending_order_count: int
    order_ambiguous: bool
    fill_complete: bool
    fill_partial: bool
    fill_late: bool
    open_position_count: int
    final_net_quantity: Decimal
    balance_before_observed: bool
    balance_after_observed: bool
    balance_change_explained: bool
    order_action_consistent: bool
    account_mode: Stage4DemoAccountModeObservation
    unresolved_unknown_count: int
    failure: Stage4DemoExecFailure | None = None
    market_input_conflicting: bool = False
    phase_observations: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class Stage4DemoExecOutcome:
    """One finalized EvidenceV1 record in its fresh external partition."""

    evidence_partition: Path
    evidence_path: Path
    evidence: dict[str, object]


@dataclass(frozen=True)
class Stage4DemoExecBatchOutcome:
    """The two separately persisted logical-attempt records."""

    market_close: Stage4DemoExecOutcome
    passive_cancel: Stage4DemoExecOutcome


@dataclass(frozen=True)
class Stage4DemoCleanupAuthorization:
    """Exact signed position and reduce-only close quantity proven for cleanup."""

    signed_position: Decimal
    quantity: Quantity


@dataclass(frozen=True)
class _ExecPhaseSnapshot:
    started_at_ns: int
    ended_at_ns: int
    quote_count: int
    trade_count: int
    market_timestamps_valid: bool
    order_submitted: bool
    order_accepted: bool
    order_terminal: bool
    active_order_count: int
    inflight_order_count: int
    pending_order_count: int
    order_ambiguous: bool
    fill_complete: bool
    fill_partial: bool
    fill_late: bool
    open_position_count: int
    final_net_quantity: Decimal
    balance_before_observed: bool
    balance_after_observed: bool
    balance_change_explained: bool
    order_action_consistent: bool
    account_mode: Stage4DemoAccountModeObservation | None
    unresolved_unknown_count: int
    failure: Stage4DemoExecFailure | None
    balance_before_total: Decimal | None = None
    market_input_conflicting: bool = False
    phase_observations: tuple[dict[str, object], ...] = ()


@dataclass
class _MarketTimestampSequence:
    quote_ns: int
    mark_ns: int


class _ExactCleanupStrategy(Strategy):
    """Private one-shot close for an already proven ExecTester position."""

    def __new__(
        cls,
        plan: Stage4DemoExecAttemptPlan,
        strategy_id: StrategyId,
        authorization: Stage4DemoCleanupAuthorization,
    ) -> _ExactCleanupStrategy:
        del authorization
        return super().__new__(  # type: ignore[call-arg]
            cls,
            StrategyConfig(
                strategy_id=strategy_id,
                external_order_claims=[plan.instrument.id],
                manage_stop=False,
                log_events=False,
                log_commands=False,
            ),
        )

    def __init__(
        self,
        plan: Stage4DemoExecAttemptPlan,
        strategy_id: StrategyId,
        authorization: Stage4DemoCleanupAuthorization,
    ) -> None:
        del strategy_id
        self._plan = plan
        self._authorization = authorization
        self._close_requested = False

    def close_once(self, cache: Cache) -> None:
        if self._close_requested:
            raise Stage4DemoExecutionError("cleanup close was already requested")
        self._close_requested = True
        position = _authorized_cleanup_position(
            cache,
            self._plan,
            self.strategy_id,
            self._authorization,
        )
        if position is None:
            raise _Stage4DemoExecutionRuntimeError(
                Stage4DemoExecFailure(
                    "TERMINAL_FACT_UNKNOWN",
                    "reconciliation",
                    ("POSITION_UNKNOWN",),
                )
            )
        try:
            order = self.order_factory.market(
                instrument_id=self._plan.instrument.id,
                order_side=(
                    OrderSide.SELL
                    if self._authorization.signed_position > 0
                    else OrderSide.BUY
                ),
                quantity=self._authorization.quantity,
                reduce_only=True,
            )
            self.submit_order(
                order,
                position_id=position.id,
                client_id=ClientId.from_str(EXEC_CLIENT_NAME),
            )
        except Exception as exc:
            raise _Stage4DemoExecutionRuntimeError(
                Stage4DemoExecFailure("CLEANUP_FAILED", "cleanup", ("ORDER_UNKNOWN",))
            ) from exc


@dataclass(frozen=True)
class _ExecTesterRuntime:
    node: LiveNode
    cleanup_strategy: _ExactCleanupStrategy | None = None


def build_stage4_demo_exec_plan(
    *,
    batch: DemoAdmissionBatch,
    evidence_root: Path,
    batch_id: str,
    acquire_market_close: Callable[[], Stage4DemoExecAttemptInput],
    acquire_passive_cancel: Callable[[], Stage4DemoExecAttemptInput],
) -> Stage4DemoExecPlan:
    """Build a deferred plan without acquiring or caching attempt-local gates."""
    return Stage4DemoExecPlan(
        evidence_root=evidence_root,
        batch_id=batch_id,
        acquire_market_close=acquire_market_close,
        acquire_passive_cancel=acquire_passive_cancel,
        _batch=batch,
    )


def run_stage4_demo_exec(
    *,
    batch: DemoAdmissionBatch,
    evidence_root: Path,
    batch_id: str,
    acquire_market_close: Callable[[], Stage4DemoExecAttemptInput],
    acquire_passive_cancel: Callable[[], Stage4DemoExecAttemptInput],
) -> Stage4DemoExecBatchOutcome:
    """Acquire, run, and persist each attempt sequentially and fail closed."""
    plan = build_stage4_demo_exec_plan(
        batch=batch,
        evidence_root=evidence_root,
        batch_id=batch_id,
        acquire_market_close=acquire_market_close,
        acquire_passive_cancel=acquire_passive_cancel,
    )
    market_plan = _admit_deferred_attempt(
        plan,
        scenario=DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE,
        acquire=plan.acquire_market_close,
    )
    market_outcome = _run_and_persist_attempt(market_plan)
    if market_outcome.evidence.get("result") != "PASS":
        raise Stage4DemoExecutionError(
            "market attempt halted; passive attempt was not admitted; "
            f"record={market_outcome.evidence_path}"
        )

    passive_plan = _admit_deferred_attempt(
        plan,
        scenario=DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL,
        acquire=plan.acquire_passive_cancel,
        previous_market_input=market_plan.market_input,
    )
    if passive_plan.evidence_partition == market_plan.evidence_partition:
        raise Stage4DemoExecutionError(
            "ExecTester attempts require separate partitions"
        )
    passive_outcome = _run_and_persist_attempt(passive_plan)
    return Stage4DemoExecBatchOutcome(
        market_close=market_outcome,
        passive_cancel=passive_outcome,
    )


def _admit_deferred_attempt(
    plan: Stage4DemoExecPlan,
    *,
    scenario: DemoEvidenceScenario,
    acquire: Callable[[], Stage4DemoExecAttemptInput],
    previous_market_input: Stage4DemoExecMarketInput | None = None,
) -> Stage4DemoExecAttemptPlan:
    started_at_ns = time.time_ns()
    price_deadline = stage4_demo.start_queue_deadline(DeadlinePhase.PRICE_READINESS)
    attempt_input = acquire()
    admission = admit_current_demo_attempt(
        batch=plan._batch,
        operator_token=attempt_input.operator_token,
    )
    try:
        public_market = asyncio.run(_acquire_public_market_input(price_deadline))
        now_ns = time.time_ns()
        if price_deadline.expired():
            raise Stage4DemoExecutionError("attempt price readiness deadline expired")
        _validate_market_input(
            public_market,
            checked_at_ns=now_ns,
            previous=previous_market_input,
        )
        if not _instrument_matches(
            attempt_input.market.instrument, public_market.instrument
        ):
            raise Stage4DemoExecutionError(
                "caller market constraints differ from public data"
            )
        return _build_attempt_plan(
            scenario=scenario,
            admission=admission,
            repository_root=plan._batch.repository_root,
            evidence_root=plan.evidence_root,
            batch_id=plan.batch_id,
            market_input=public_market,
        )
    except Exception as exc:
        outcome = _persist_preparation_failure(
            plan,
            scenario=scenario,
            admission=admission,
            started_at_ns=started_at_ns,
            timed_out=price_deadline.expired(),
        )
        raise Stage4DemoExecutionError(
            f"{scenario.value} halted before instrument readiness; "
            f"record={outcome.evidence_path}"
        ) from exc


async def _acquire_public_market_input(
    deadline: stage4_demo.DemoDeadline,
) -> Stage4DemoExecMarketInput:
    """Read public rc4 data before constructing any order-enabled node."""
    instrument_id = InstrumentId.from_str(DEMO_INSTRUMENT_ID)
    node = (
        LiveNode.builder(
            _EXEC_NODE_NAME,
            TraderId.from_str(_EXEC_TRADER_ID),
            Environment.LIVE,
        )
        .with_timeout_connection(_deadline_seconds(DeadlinePhase.CONNECT))
        .with_timeout_disconnection_secs(_deadline_seconds(DeadlinePhase.CLEANUP))
        .with_delay_post_stop_secs(0)
        .with_delay_shutdown_secs(_deadline_seconds(DeadlinePhase.CLEANUP))
        .add_data_client(
            EXEC_CLIENT_NAME,
            BinanceDataClientFactory(),
            _public_data_client_config(),
        )
        .build()
    )
    node.add_builtin_actor(
        _DATA_TESTER_BUILTIN,
        DataTesterConfig(
            client_id=ClientId.from_str(EXEC_CLIENT_NAME),
            instrument_ids=[instrument_id],
            subscribe_quotes=True,
            subscribe_trades=False,
            subscribe_mark_prices=True,
            subscribe_instrument=True,
            log_data=False,
            log_events=False,
            log_commands=False,
        ),
    )
    cache = node.cache
    handle = node.handle()
    run_task = asyncio.create_task(node.run_async())
    try:
        unavailable = Stage4DemoExecFailure(
            "CONNECT_SUBSCRIPTION_FAILED", "data", ("FAILED",)
        )
        await _wait_until(
            lambda: handle.is_running,
            deadline=deadline,
            run_task=run_task,
            failure=unavailable,
        )
        selected: Stage4DemoExecMarketInput | None = None
        sequence = _MarketTimestampSequence(quote_ns=0, mark_ns=0)

        def market_ready() -> bool:
            nonlocal selected
            selected = _qualified_public_market_input(cache, instrument_id, sequence)
            return selected is not None

        await _wait_until(
            market_ready,
            deadline=deadline,
            run_task=run_task,
            failure=unavailable,
        )
        if selected is None:
            raise Stage4DemoExecutionError("public market input disappeared")
        result = selected
    except _Stage4DemoExecutionRuntimeError as exc:
        raise Stage4DemoExecutionError("public market input unavailable") from exc
    finally:
        cleanup_failure = await _stop_exec_tester(handle, run_task, request_stop=True)
        if run_task.done():
            node.dispose()
        if cleanup_failure is not None:
            raise Stage4DemoExecutionError("public market data node did not stop")
    return result


def _qualified_public_market_input(
    cache: Cache,
    instrument_id: InstrumentId,
    sequence: _MarketTimestampSequence,
) -> Stage4DemoExecMarketInput | None:
    """Wait through stale public values while rejecting stream identity or rollback."""
    instrument = cache.instrument(instrument_id)
    quote = cache.quote(instrument_id)
    mark = cache.mark_price(instrument_id)
    if not isinstance(instrument, CryptoPerpetual) or quote is None or mark is None:
        return None
    if (
        instrument.id != instrument_id
        or quote.instrument_id != instrument_id
        or mark.instrument_id != instrument_id
    ):
        raise _RuntimeMarketConflict("public market input identity conflict")
    if quote.ts_event < sequence.quote_ns or mark.ts_event < sequence.mark_ns:
        raise _RuntimeMarketConflict("public market timestamp moved backward")
    sequence.quote_ns = quote.ts_event
    sequence.mark_ns = mark.ts_event
    checked_at_ns = time.time_ns()
    if any(
        timestamp > checked_at_ns + _FUTURE_TOLERANCE_NS
        or checked_at_ns - timestamp > _MAX_PRICE_AGE_NS
        for timestamp in (quote.ts_event, mark.ts_event)
    ):
        return None
    result = Stage4DemoExecMarketInput(
        instrument=instrument,
        best_bid=quote.bid_price,
        best_ask=quote.ask_price,
        mark_price=mark.value,
        quote_ts_event_ns=quote.ts_event,
        mark_ts_event_ns=mark.ts_event,
        observed_at_ns=checked_at_ns,
    )
    _validate_market_input(result, checked_at_ns=checked_at_ns)
    return result


def _persist_preparation_failure(
    plan: Stage4DemoExecPlan,
    *,
    scenario: DemoEvidenceScenario,
    admission: AdmittedDemoAttempt,
    started_at_ns: int,
    timed_out: bool,
) -> Stage4DemoExecOutcome:
    """Record a started attempt before any order-enabled node can be built."""
    partition = stage4_demo_partition_path(
        repository_root=plan._batch.repository_root,
        evidence_root=plan.evidence_root,
        batch_id=plan.batch_id,
        scenario=scenario,
    )
    missing = EvidenceClassification.MISSING.value
    unknown = EvidenceClassification.UNKNOWN.value
    payload: dict[str, object] = {
        "schema": EVIDENCE_SCHEMA,
        "source_commit": admission.runtime.source_commit,
        "dependency_lock_sha256": admission.runtime.dependency_lock_sha256,
        "runtime": {
            "distribution": NAUTILUS_DISTRIBUTION,
            "version": NAUTILUS_VERSION,
            "upstream_commit": NAUTILUS_UPSTREAM_COMMIT,
            "wheel_sha256": NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
        },
        "environment": DEMO_ENVIRONMENT,
        "config_digest": admission.frozen_config.config_digest,
        "batch_id": plan.batch_id,
        "instrument": {
            "id": DEMO_INSTRUMENT_ID,
            "price_precision": None,
            "price_increment": None,
            "size_precision": None,
            "size_increment": None,
            "minimum_quantity": None,
            "maximum_quantity": None,
            "minimum_notional": None,
        },
        "scenario": scenario.value,
        "started_at": _utc_timestamp(started_at_ns),
        "ended_at": _utc_timestamp(time.time_ns()),
        "result": "FAIL",
        "terminal_state": "HALTED",
        "observations": {
            "market_data": {
                "classification": missing,
                "quote_count": 0,
                "trade_count": 0,
                "timestamp_valid": False,
            },
            "order": {
                "classification": missing,
                "submitted": False,
                "accepted": False,
                "terminal": False,
                "active": False,
                "pending": False,
                "ambiguous": False,
            },
            "fill": {
                "classification": missing,
                "complete": False,
                "partial": False,
                "late": False,
            },
            "position": {
                "classification": unknown,
                "open_count": 0,
                "final_net_quantity": "0",
            },
            "balance": {
                "classification": missing,
                "before_observed": False,
                "after_observed": False,
                "explained_change": False,
            },
            "account_mode": {
                "classification": missing,
                "operator_gate_confirmed": True,
                "config_requested": True,
                "canary_complete": False,
                "one_way_confirmed": False,
                "isolated_confirmed": False,
                "leverage_one_confirmed": False,
                "observed_initial_margin": None,
                "allowed_initial_margin_min": None,
                "allowed_initial_margin_max": None,
            },
        },
        "cleanup": {
            "classification": EvidenceClassification.CLEANUP_INCOMPLETE.value,
            "active_order_count": 0,
            "pending_order_count": 0,
            "open_position_count": 0,
            "unresolved_unknown_count": 1,
            "final_net_quantity": "0",
        },
        "failure": {
            "code": "DATA_TIMEOUT" if timed_out else "DATA_INVALID",
            "phase": "data",
            "diagnostic_codes": ["FAILED"],
        },
    }
    evidence = finalize_stage4_demo_evidence(payload)
    return _write_exec_evidence(partition, scenario, evidence)


def _run_and_persist_attempt(
    plan: Stage4DemoExecAttemptPlan,
) -> Stage4DemoExecOutcome:
    if plan.evidence_partition.exists():
        raise Stage4DemoExecutionError("ExecTester evidence partition already exists")
    observation = asyncio.run(_observe_stage4_demo_attempt(plan))
    evidence = build_stage4_demo_exec_evidence(plan, observation)
    return write_stage4_demo_exec_evidence(
        plan, evidence, phase_observations=observation.phase_observations
    )


def build_stage4_demo_exec_evidence(
    plan: Stage4DemoExecAttemptPlan,
    observation: Stage4DemoExecObservation,
) -> dict[str, object]:
    """Adapt one attempt's public facts into the frozen EvidenceV1 schema."""
    if observation.scenario is not plan.scenario:
        raise Stage4DemoExecutionError("observation scenario does not match its plan")
    if observation.instrument.id != plan.instrument.id:
        raise Stage4DemoExecutionError("observation instrument does not match its plan")

    account_consistent = (
        observation.account_mode.operator_gate_confirmed
        and observation.account_mode.canary_complete
        and observation.account_mode.net_position_shape_consistent
        and observation.account_mode.account_scope_consistent
    )
    market_consistent = (
        observation.quote_count > 0
        and observation.market_timestamps_valid
        and not observation.market_input_conflicting
    )
    order_consistent = (
        observation.order_submitted
        and observation.order_accepted
        and observation.order_terminal
        and observation.order_action_consistent
        and observation.active_order_count == 0
        and observation.pending_order_count == 0
        and not observation.order_ambiguous
    )
    expected_complete_fill = (
        plan.scenario is DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE
    )
    fill_consistent = (
        observation.fill_complete is expected_complete_fill
        and not observation.fill_partial
        and not observation.fill_late
    )
    position_consistent = (
        observation.open_position_count == 0 and observation.final_net_quantity == 0
    )
    balance_consistent = (
        observation.balance_before_observed
        and observation.balance_after_observed
        and observation.balance_change_explained
    )
    cleanup_consistent = (
        observation.active_order_count == 0
        and observation.pending_order_count == 0
        and observation.open_position_count == 0
        and observation.unresolved_unknown_count == 0
        and observation.final_net_quantity == 0
    )
    resolved_failure = observation.failure
    if resolved_failure is None and not all(
        (
            market_consistent,
            order_consistent,
            fill_consistent,
            position_consistent,
            balance_consistent,
            account_consistent,
            cleanup_consistent,
        )
    ):
        resolved_failure = _derive_failure(
            observation,
            account_consistent=account_consistent,
            cleanup_consistent=cleanup_consistent,
        )

    passing = resolved_failure is None
    classification = EvidenceClassification.CONSISTENT.value
    observations: dict[str, object] = {
        "market_data": {
            "classification": _classification(
                market_consistent,
                observation.quote_count > 0 or observation.market_input_conflicting,
            ),
            "quote_count": observation.quote_count,
            "trade_count": observation.trade_count,
            "timestamp_valid": observation.market_timestamps_valid,
        },
        "order": {
            "classification": _classification(
                order_consistent, observation.order_submitted
            ),
            "submitted": observation.order_submitted,
            "accepted": observation.order_accepted,
            "terminal": observation.order_terminal,
            "active": observation.active_order_count > 0,
            "pending": observation.pending_order_count > 0,
            "ambiguous": observation.order_ambiguous,
        },
        "fill": {
            "classification": _classification(
                fill_consistent,
                observation.fill_complete
                or observation.fill_partial
                or (plan.scenario is DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL),
            ),
            "complete": observation.fill_complete,
            "partial": observation.fill_partial,
            "late": observation.fill_late,
        },
        "position": {
            "classification": _classification(position_consistent, True),
            "open_count": observation.open_position_count,
            "final_net_quantity": _decimal_text(observation.final_net_quantity),
        },
        "balance": {
            "classification": _classification(
                balance_consistent,
                observation.balance_before_observed
                or observation.balance_after_observed,
            ),
            "before_observed": observation.balance_before_observed,
            "after_observed": observation.balance_after_observed,
            "explained_change": observation.balance_change_explained,
        },
        "account_mode": {
            "classification": _classification(
                account_consistent,
                observation.account_mode.canary_complete,
            ),
            "operator_gate_confirmed": observation.account_mode.operator_gate_confirmed,
            "config_requested": True,
            "canary_complete": observation.account_mode.canary_complete,
            "one_way_confirmed": False,
            "isolated_confirmed": False,
            "leverage_one_confirmed": False,
            "observed_initial_margin": _optional_decimal_text(
                observation.account_mode.observed_initial_margin
            ),
            "allowed_initial_margin_min": None,
            "allowed_initial_margin_max": None,
        },
    }
    payload: dict[str, object] = {
        "schema": EVIDENCE_SCHEMA,
        "source_commit": plan.runtime.source_commit,
        "dependency_lock_sha256": plan.runtime.dependency_lock_sha256,
        "runtime": {
            "distribution": NAUTILUS_DISTRIBUTION,
            "version": NAUTILUS_VERSION,
            "upstream_commit": NAUTILUS_UPSTREAM_COMMIT,
            "wheel_sha256": NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
        },
        "environment": DEMO_ENVIRONMENT,
        "config_digest": plan.frozen_config.config_digest,
        "batch_id": _batch_id_from_partition(plan.evidence_partition),
        "instrument": _instrument_payload(observation.instrument),
        "scenario": plan.scenario.value,
        "started_at": _utc_timestamp(observation.started_at_ns),
        "ended_at": _utc_timestamp(observation.ended_at_ns),
        "result": "PASS" if passing else "FAIL",
        "terminal_state": "COMPLETE" if passing else "HALTED",
        "observations": observations,
        "cleanup": {
            "classification": classification
            if cleanup_consistent
            else EvidenceClassification.CLEANUP_INCOMPLETE.value,
            "active_order_count": observation.active_order_count,
            "pending_order_count": observation.pending_order_count,
            "open_position_count": observation.open_position_count,
            "unresolved_unknown_count": observation.unresolved_unknown_count,
            "final_net_quantity": _decimal_text(observation.final_net_quantity),
        },
        "failure": (
            None
            if resolved_failure is None
            else {
                "code": resolved_failure.code,
                "phase": resolved_failure.phase,
                "diagnostic_codes": list(resolved_failure.diagnostic_codes),
            }
        ),
    }
    return finalize_stage4_demo_evidence(payload)


def write_stage4_demo_exec_evidence(
    plan: Stage4DemoExecAttemptPlan,
    evidence: dict[str, object],
    *,
    phase_observations: tuple[dict[str, object], ...] = (),
) -> Stage4DemoExecOutcome:
    """Write one validated attempt record to a new external partition."""
    return _write_exec_evidence(
        plan.evidence_partition, plan.scenario, evidence, phase_observations
    )


def _write_exec_evidence(
    partition: Path,
    scenario: DemoEvidenceScenario,
    evidence: dict[str, object],
    phase_observations: tuple[dict[str, object], ...] = (),
) -> Stage4DemoExecOutcome:
    if evidence.get("scenario") != scenario.value:
        raise Stage4DemoExecutionError("evidence scenario does not match its plan")
    if partition.exists():
        raise Stage4DemoExecutionError("ExecTester evidence partition already exists")
    rendered = canonical_stage4_demo_evidence_json(evidence)
    partition.parent.mkdir(parents=True, exist_ok=True)
    try:
        partition.mkdir()
    except FileExistsError as exc:
        raise Stage4DemoExecutionError(
            "ExecTester evidence partition already exists"
        ) from exc
    evidence_path = partition / EXEC_EVIDENCE_FILENAME
    observations_path = partition / _EXEC_OBSERVATIONS_FILENAME
    try:
        with evidence_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.write("\n")
        if phase_observations:
            observations = {
                "evidence_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "source": "nautilus_cache",
                "phases": phase_observations,
            }
            with observations_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(observations, stream, sort_keys=True, ensure_ascii=False)
                stream.write("\n")
    except Exception:
        observations_path.unlink(missing_ok=True)
        evidence_path.unlink(missing_ok=True)
        partition.rmdir()
        raise
    return Stage4DemoExecOutcome(partition, evidence_path, evidence)


def prove_exact_cleanup_quantity(
    plan: Stage4DemoExecAttemptPlan,
    *,
    original_order_terminal: bool,
    active_order_count: int,
    inflight_order_count: int,
    open_position_count: int,
    net_position: Decimal | None,
) -> Stage4DemoCleanupAuthorization | None:
    """Authorize at most one cleanup quantity only after the frozen safety proof."""
    if (
        not original_order_terminal
        or active_order_count != 0
        or inflight_order_count != 0
        or open_position_count != 1
    ):
        return None
    if net_position is None:
        return None
    if net_position == 0:
        return None
    return Stage4DemoCleanupAuthorization(
        signed_position=net_position,
        quantity=exact_reduce_only_quantity(plan.instrument, net_position),
    )


def _possible_cleanup_authorization(
    plan: Stage4DemoExecAttemptPlan,
    snapshot: _ExecPhaseSnapshot,
) -> Stage4DemoCleanupAuthorization | None:
    if snapshot.unresolved_unknown_count != 0:
        return None
    try:
        return prove_exact_cleanup_quantity(
            plan,
            original_order_terminal=snapshot.order_terminal,
            active_order_count=snapshot.active_order_count,
            inflight_order_count=snapshot.inflight_order_count,
            open_position_count=snapshot.open_position_count,
            net_position=snapshot.final_net_quantity,
        )
    except Stage4DemoAdmissionError:
        # A partial fill may leave a known residual that cannot be closed exactly.
        # Preserve its observed quantity in a HALTED record without placing an order.
        return None


async def _observe_stage4_demo_attempt(
    plan: Stage4DemoExecAttemptPlan,
) -> Stage4DemoExecObservation:
    opening = await _run_exec_phase(
        plan,
        plan.phases[0],
        phase_kind="canary",
    )
    authorization = _possible_cleanup_authorization(plan, opening)
    if authorization is None:
        canary = replace(
            opening,
            failure=opening.failure
            or Stage4DemoExecFailure(
                "CLEANUP_INCOMPLETE", "cleanup", ("UNRESOLVED_UNKNOWN",)
            ),
        )
    else:
        closing = await _run_exec_phase(
            plan,
            plan.phases[1],
            phase_kind="cleanup",
            cleanup_authorization=authorization,
            balance_baseline=opening.balance_before_total,
        )
        canary = _merge_cleanup_snapshot(opening, closing)

    if plan.scenario is DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE:
        return _logical_observation(plan, canary=canary, execution=canary)

    if canary.failure is None:
        execution = await _run_exec_phase(
            plan,
            plan.phases[2],
            phase_kind="passive",
        )
    else:
        execution = _skipped_phase_snapshot(canary)

    cleanup_authorization = _possible_cleanup_authorization(plan, execution)
    if (
        execution.failure is not None
        and execution.unresolved_unknown_count == 0
        and cleanup_authorization is not None
    ):
        cleanup_phase = await _run_exec_phase(
            plan,
            plan.phases[3],
            phase_kind="cleanup",
            cleanup_authorization=cleanup_authorization,
            balance_baseline=execution.balance_before_total,
        )
        execution = _merge_cleanup_snapshot(execution, cleanup_phase)

    return _logical_observation(plan, canary=canary, execution=execution)


async def _run_exec_phase(
    plan: Stage4DemoExecAttemptPlan,
    phase: Stage4DemoExecPhasePlan,
    *,
    phase_kind: Literal["canary", "passive", "cleanup"],
    cleanup_authorization: Stage4DemoCleanupAuthorization | None = None,
    balance_baseline: Decimal | None = None,
) -> _ExecPhaseSnapshot:
    started_at_ns = time.time_ns()
    try:
        if phase_kind == "cleanup":
            if cleanup_authorization is None:
                raise Stage4DemoExecutionError("cleanup phase is missing authorization")
            runtime = _build_authorized_cleanup_runtime(
                plan,
                phase,
                cleanup_authorization=cleanup_authorization,
            )
        else:
            if phase.run_condition != "always":
                raise Stage4DemoExecutionError("conditional phase used without proof")
            runtime = _build_exec_tester_runtime_unchecked(plan, phase)
    except Exception:
        return _failed_phase_snapshot(
            started_at_ns=started_at_ns,
            failure=Stage4DemoExecFailure(
                "CONNECT_SUBSCRIPTION_FAILED",
                "construction",
                ("FAILED",),
            ),
        )

    node = runtime.node
    cache = node.cache
    handle = node.handle()
    strategy_id = _require_phase_strategy_id(phase)
    run_task: asyncio.Task[None] = asyncio.create_task(node.run_async())
    failure: Stage4DemoExecFailure | None = None
    account_mode: Stage4DemoAccountModeObservation | None = None
    stop_requested = False
    stop_failed = False
    market_input_conflicting = False
    market_sequence = _MarketTimestampSequence(
        quote_ns=plan.market_input.quote_ts_event_ns,
        mark_ns=plan.market_input.mark_ts_event_ns,
    )
    try:
        await _wait_until(
            lambda: handle.is_running,
            deadline=stage4_demo.start_queue_deadline(DeadlinePhase.CONNECT),
            run_task=run_task,
            failure=Stage4DemoExecFailure(
                "CONNECT_SUBSCRIPTION_FAILED", "data", ("FAILED",)
            ),
        )
        await _wait_until(
            lambda: _market_ready(
                cache,
                plan,
                market_sequence,
                phase_kind=phase_kind,
            ),
            deadline=stage4_demo.start_queue_deadline(DeadlinePhase.PRICE_READINESS),
            run_task=run_task,
            failure=Stage4DemoExecFailure(
                "CONNECT_SUBSCRIPTION_FAILED", "data", ("FAILED",)
            ),
        )
        if phase_kind == "cleanup":
            await _wait_until(
                lambda: bool(cache.positions_open(instrument_id=plan.instrument.id)),
                deadline=stage4_demo.start_queue_deadline(DeadlinePhase.RECONCILIATION),
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "TERMINAL_FACT_UNKNOWN",
                    "reconciliation",
                    ("POSITION_UNKNOWN",),
                ),
            )
            if runtime.cleanup_strategy is None:
                raise Stage4DemoExecutionError("cleanup strategy is missing")
            runtime.cleanup_strategy.close_once(cache)
            await _wait_until(
                lambda: _cleanup_complete(cache, plan, strategy_id),
                deadline=stage4_demo.start_queue_deadline(
                    DeadlinePhase.MARKET_OR_REDUCE_ONLY_FILL
                ),
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "CLEANUP_INCOMPLETE", "cleanup", ("ORDER_UNKNOWN",)
                ),
            )
        else:
            acceptance_deadline = stage4_demo.start_queue_deadline(
                DeadlinePhase.ORDER_ACCEPTANCE
            )
            await _wait_until(
                lambda: bool(
                    cache.orders(
                        instrument_id=plan.instrument.id,
                        strategy_id=strategy_id,
                    )
                ),
                deadline=acceptance_deadline,
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "ORDER_TIMEOUT", "execution", ("ORDER_UNKNOWN",)
                ),
            )
            await _wait_until(
                lambda: _order_ack_resolved(cache, plan, strategy_id),
                deadline=acceptance_deadline,
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "ORDER_AMBIGUOUS", "execution", ("ORDER_UNKNOWN",)
                ),
            )

        if phase_kind == "canary":
            await _wait_until(
                lambda: _canary_filled(cache, plan, strategy_id),
                deadline=stage4_demo.start_queue_deadline(
                    DeadlinePhase.MARKET_OR_REDUCE_ONLY_FILL
                ),
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "ORDER_TIMEOUT", "execution", ("POSITION_UNKNOWN",)
                ),
            )
            # rc4 AccountState has no request correlation ID. Inspect public
            # facts without treating an asynchronous account event as a typed
            # venue-mode acknowledgement.
            account_mode = _capture_account_mode(cache, plan, time.time_ns())
            if not _account_mode_complete(plan, account_mode):
                failure = Stage4DemoExecFailure(
                    "TERMINAL_FACT_UNKNOWN",
                    "account_mode",
                    ("ACCOUNT_MODE_UNKNOWN",),
                )
        elif phase_kind == "passive" and _orders_have_fill(cache, plan, strategy_id):
            failure = Stage4DemoExecFailure(
                "CANCEL_FILL_RACE",
                "execution",
                ("FAILED",),
            )
        stop_requested = True
        try:
            handle.stop()
        except Exception as exc:
            stop_failed = True
            raise _Stage4DemoExecutionRuntimeError(
                Stage4DemoExecFailure("CLEANUP_FAILED", "cleanup", ("FAILED",))
            ) from exc
        if phase_kind == "passive":
            await _wait_after_stop(
                lambda: _passive_cancel_complete(cache, plan, strategy_id),
                deadline=stage4_demo.start_queue_deadline(DeadlinePhase.CANCELLATION),
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "ORDER_TIMEOUT", "execution", ("ORDER_UNKNOWN",)
                ),
            )
        elif phase_kind == "canary":
            await _wait_after_stop(
                lambda: _canary_stop_complete(cache, plan, strategy_id),
                deadline=stage4_demo.start_queue_deadline(
                    DeadlinePhase.MARKET_OR_REDUCE_ONLY_FILL
                ),
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "CLEANUP_INCOMPLETE",
                    "cleanup",
                    ("UNRESOLVED_UNKNOWN",),
                ),
            )
        else:
            await _wait_after_stop(
                lambda: _cleanup_complete(cache, plan, strategy_id),
                deadline=stage4_demo.start_queue_deadline(
                    DeadlinePhase.MARKET_OR_REDUCE_ONLY_FILL
                ),
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "CLEANUP_INCOMPLETE",
                    "cleanup",
                    ("OPEN_POSITION_REMAINS",),
                ),
            )
            if cleanup_authorization is None or not _cleanup_action_matches(
                cache,
                plan,
                strategy_id,
                cleanup_authorization,
            ):
                failure = Stage4DemoExecFailure(
                    "CLEANUP_FAILED",
                    "cleanup",
                    ("FAILED",),
                )
    except _Stage4DemoExecutionRuntimeError as exc:
        market_input_conflicting = (
            exc.failure.code == "TERMINAL_FACT_CONFLICTING"
            and exc.failure.phase == "data"
        )
        failure = failure or exc.failure
    except Exception:
        failure = Stage4DemoExecFailure(
            "CONNECT_SUBSCRIPTION_FAILED",
            "execution",
            ("FAILED",),
        )

    # Once an order-enabled tester node runs, an empty local cache cannot
    # establish that its first quote callback never submitted an order.
    order_submission_unknown = phase_kind in {"canary", "passive"}
    cleanup_failure = (
        Stage4DemoExecFailure("CLEANUP_FAILED", "cleanup", ("FAILED",))
        if stop_failed
        else await _stop_exec_tester(
            handle,
            run_task,
            request_stop=not stop_requested,
        )
    )
    if cleanup_failure is not None:
        failure = cleanup_failure
    ended_at_ns = time.time_ns()
    try:
        account_before_count, pre_order_balance = _pre_order_account_observation(
            cache, plan, strategy_id, phase_kind=phase_kind
        )
        return _capture_phase_snapshot(
            cache,
            plan,
            phase=phase,
            phase_kind=phase_kind,
            started_at_ns=started_at_ns,
            ended_at_ns=ended_at_ns,
            account_before_count=account_before_count,
            account_before_balance=(
                balance_baseline if phase_kind == "cleanup" else pre_order_balance
            ),
            account_mode=account_mode,
            failure=failure,
            order_submission_unknown=order_submission_unknown,
            market_sequence=market_sequence,
            cleanup_authorization=cleanup_authorization,
            market_input_conflicting=market_input_conflicting,
            stop_unconfirmed=cleanup_failure is not None,
        )
    finally:
        if cleanup_failure is None:
            node.dispose()


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    deadline: stage4_demo.DemoDeadline,
    run_task: asyncio.Task[None],
    failure: Stage4DemoExecFailure,
) -> None:
    while True:
        if run_task.done():
            try:
                run_task.result()
            except Exception as exc:
                raise _Stage4DemoExecutionRuntimeError(failure) from exc
            raise _Stage4DemoExecutionRuntimeError(failure)
        try:
            if predicate():
                return
        except _RuntimeMarketConflict as exc:
            raise _Stage4DemoExecutionRuntimeError(
                Stage4DemoExecFailure("TERMINAL_FACT_CONFLICTING", "data", ("FAILED",))
            ) from exc
        except Exception as exc:
            raise _Stage4DemoExecutionRuntimeError(failure) from exc
        if deadline.expired():
            raise _Stage4DemoExecutionRuntimeError(failure)
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, deadline.remaining_seconds()))


async def _wait_after_stop(
    predicate: Callable[[], bool],
    *,
    deadline: stage4_demo.DemoDeadline,
    run_task: asyncio.Task[None],
    failure: Stage4DemoExecFailure,
) -> None:
    while True:
        try:
            if predicate():
                return
        except Exception as exc:
            raise _Stage4DemoExecutionRuntimeError(failure) from exc
        if run_task.done():
            try:
                run_task.result()
            except Exception as exc:
                raise _Stage4DemoExecutionRuntimeError(failure) from exc
            raise _Stage4DemoExecutionRuntimeError(failure)
        if deadline.expired():
            raise _Stage4DemoExecutionRuntimeError(failure)
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, deadline.remaining_seconds()))


async def _stop_exec_tester(
    handle: LiveNodeHandle,
    run_task: asyncio.Task[None],
    *,
    request_stop: bool,
) -> Stage4DemoExecFailure | None:
    completed_before_stop = run_task.done()
    if request_stop:
        try:
            handle.stop()
        except Exception:
            return Stage4DemoExecFailure("CLEANUP_FAILED", "cleanup", ("FAILED",))
    try:
        await asyncio.wait_for(
            run_task,
            timeout=_deadline_seconds(DeadlinePhase.CLEANUP),
        )
    except TimeoutError:
        return Stage4DemoExecFailure("CLEANUP_FAILED", "cleanup", ("FAILED",))
    except Exception:
        if completed_before_stop:
            return None
        return Stage4DemoExecFailure("CLEANUP_FAILED", "cleanup", ("FAILED",))
    return None


def _market_ready(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    sequence: _MarketTimestampSequence,
    *,
    phase_kind: Literal["canary", "passive", "cleanup"] = "canary",
) -> bool:
    observed_instrument = cache.instrument(plan.instrument.id)
    if observed_instrument is None:
        return False
    if not isinstance(observed_instrument, CryptoPerpetual) or not _instrument_matches(
        observed_instrument,
        plan.instrument,
    ):
        raise _RuntimeMarketConflict("runtime instrument drifted from admitted input")
    quote = cache.quote(plan.instrument.id)
    mark = cache.mark_price(plan.instrument.id)
    if quote is None or mark is None:
        return False
    if (
        quote.instrument_id != plan.instrument.id
        or mark.instrument_id != plan.instrument.id
    ):
        return False
    if (
        quote.bid_price.as_decimal() <= 0
        or quote.ask_price.as_decimal() <= quote.bid_price.as_decimal()
        or mark.value.as_decimal() <= 0
    ):
        return False
    if quote.ts_event < sequence.quote_ns or mark.ts_event < sequence.mark_ns:
        raise _RuntimeMarketConflict("market timestamp moved backward")
    sequence.quote_ns = quote.ts_event
    sequence.mark_ns = mark.ts_event
    observed_at_ns = time.time_ns()
    timestamps_ready = _timestamp_is_fresh(
        quote.ts_event, observed_at_ns
    ) and _timestamp_is_fresh(mark.ts_event, observed_at_ns)
    if not timestamps_ready:
        return False

    if phase_kind == "canary":
        runtime_quantity = minimum_order_quantity(observed_instrument, mark.value)
        if runtime_quantity != plan.canary_quantity:
            raise _RuntimeMarketConflict(
                "runtime canary quantity drifted from admitted price input"
            )
    elif phase_kind == "passive":
        runtime_price = quote.bid_price - observed_instrument.price_increment
        runtime_quantity = minimum_order_quantity(observed_instrument, runtime_price)
        if (
            runtime_price != plan.passive_price
            or runtime_quantity != plan.passive_quantity
        ):
            raise _RuntimeMarketConflict(
                "runtime passive price or quantity drifted from admitted input"
            )
    return True


def _order_ack_resolved(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
) -> bool:
    orders = cache.orders(
        instrument_id=plan.instrument.id,
        strategy_id=strategy_id,
    )
    return bool(orders) and all(
        getattr(order, "ts_accepted", None) is not None
        or _order_bool(order, "is_closed")
        for order in orders
    )


def _canary_filled(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
) -> bool:
    orders = cache.orders(
        instrument_id=plan.instrument.id,
        strategy_id=strategy_id,
    )
    positions = cache.positions_open(instrument_id=plan.instrument.id)
    return (
        len(orders) == 1
        and _canary_open_order_matches(orders[0], plan.canary_quantity)
        and len(positions) == 1
        and _signed_position_quantity(positions[0]) == plan.canary_quantity.as_decimal()
    )


def _no_global_unresolved_orders(cache: Cache) -> bool:
    return (
        cache.orders_open_count() == 0
        and cache.orders_inflight_count() == 0
        and not any(
            _order_bool(order, "is_pending_cancel")
            or _order_bool(order, "is_pending_update")
            for order in cache.orders()
        )
    )


def _canary_stop_complete(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
) -> bool:
    return _canary_filled(cache, plan, strategy_id) and _no_global_unresolved_orders(
        cache
    )


def _orders_have_fill(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
) -> bool:
    return any(
        _filled_quantity(order) > 0
        for order in cache.orders(
            instrument_id=plan.instrument.id,
            strategy_id=strategy_id,
        )
    )


def _passive_cancel_complete(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
) -> bool:
    orders = cache.orders(
        instrument_id=plan.instrument.id,
        strategy_id=strategy_id,
    )
    return (
        len(orders) == 1
        and _passive_order_matches(plan, orders[0])
        and _no_global_unresolved_orders(cache)
    )


def _cleanup_complete(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
) -> bool:
    orders = cache.orders(
        instrument_id=plan.instrument.id,
        strategy_id=strategy_id,
    )
    return (
        bool(orders)
        and all(_order_bool(order, "is_closed") for order in orders)
        and _no_global_unresolved_orders(cache)
        and not cache.positions_open()
    )


def _cleanup_cache_matches_authorization(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
    authorization: Stage4DemoCleanupAuthorization,
) -> bool:
    return (
        _authorized_cleanup_position(cache, plan, strategy_id, authorization)
        is not None
    )


def _authorized_cleanup_position(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
    authorization: Stage4DemoCleanupAuthorization,
) -> Position | None:
    if not _no_global_unresolved_orders(cache):
        return None
    positions = cache.positions_open()
    if len(positions) != 1 or positions[0].strategy_id != strategy_id:
        return None
    position = positions[0]
    if position.instrument_id != plan.instrument.id:
        return None
    current = _signed_position_quantity(position)
    if current is None or current != authorization.signed_position:
        return None
    try:
        if (
            exact_reduce_only_quantity(plan.instrument, current)
            != authorization.quantity
        ):
            return None
    except Stage4DemoAdmissionError:
        return None
    return cast(Position, position)


def _cleanup_action_matches(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
    authorization: Stage4DemoCleanupAuthorization,
) -> bool:
    orders = cache.orders(
        instrument_id=plan.instrument.id,
        strategy_id=strategy_id,
    )
    close_orders = [order for order in orders if _order_reduce_only(order)]
    if len(close_orders) != 1 or any(
        not _order_bool(order, "is_closed") for order in orders
    ):
        return False
    order = close_orders[0]
    return _filled_market_order_matches(
        order,
        side="SELL" if authorization.signed_position > 0 else "BUY",
        quantity=authorization.quantity,
        reduce_only=True,
    )


def _capture_account_mode(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    query_at_ns: int,
) -> Stage4DemoAccountModeObservation:
    all_positions = cache.positions_open()
    target_positions = cache.positions_open(instrument_id=plan.instrument.id)
    all_open_orders = cache.orders_open()
    one_way = len(all_positions) == len(target_positions) == 1 and all(
        not str(position.id).endswith(("-LONG", "-SHORT"))
        for position in target_positions
    )
    isolated = one_way and not all_open_orders and _no_global_unresolved_orders(cache)
    account = cache.account(AccountId.from_str(EXEC_ACCOUNT_ID))
    event = getattr(account, "last_event", None) if account is not None else None
    if callable(event):
        event = event()
    info = getattr(event, "info", None)
    margins = getattr(event, "margins", None)
    target_margin_only = (
        isinstance(margins, list)
        and bool(margins)
        and all(
            getattr(margin, "instrument_id", None) == plan.instrument.id
            for margin in margins
        )
    )
    # A missing/uncorrelated margin list cannot prove ISOLATED. Only an
    # observed conflicting margin owner is used as a failure signal.
    isolated = isolated and (
        not isinstance(margins, list) or not margins or target_margin_only
    )
    observed_margin: Decimal | None = None
    # The latest AccountState is uncorrelated with a query in rc4. Preserve
    # its public margin value as a diagnostic, never as 1x confirmation.
    if isinstance(info, dict) and "total_initial_margin" in info:
        try:
            observed_margin = Decimal(str(info["total_initial_margin"]))
            if not observed_margin.is_finite():
                observed_margin = None
        except Exception:
            observed_margin = None

    marks = cache.mark_prices(plan.instrument.id) or []
    mark_values = [
        item.value.as_decimal()
        for item in marks
        if item.instrument_id == plan.instrument.id
        and abs(item.ts_event - query_at_ns) <= _MAX_PRICE_AGE_NS
        and item.value.as_decimal() > 0
    ]
    return Stage4DemoAccountModeObservation(
        operator_gate_confirmed=True,
        canary_complete=True,
        net_position_shape_consistent=one_way,
        account_scope_consistent=isolated,
        observed_initial_margin=observed_margin,
        mark_price_min=min(mark_values) if mark_values else None,
        mark_price_max=max(mark_values) if mark_values else None,
    )


def _account_mode_complete(
    plan: Stage4DemoExecAttemptPlan,
    value: Stage4DemoAccountModeObservation,
) -> bool:
    return (
        value.operator_gate_confirmed
        and value.canary_complete
        and value.net_position_shape_consistent
        and value.account_scope_consistent
    )


def _bounded_phase_source_facts(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    *,
    phase: Stage4DemoExecPhasePlan,
    phase_kind: Literal["canary", "passive", "cleanup"],
    orders: list[object],
    positions: list[object],
    snapshot: _ExecPhaseSnapshot,
) -> dict[str, object]:
    """Keep a small, allowlisted copy of public Nautilus facts outside the repo."""
    order_facts: list[dict[str, object]] = []
    for order in orders[:8]:
        events = getattr(order, "events", ())
        events = events() if callable(events) else events
        event_list = list(events) if isinstance(events, (list, tuple)) else []
        event_facts: list[dict[str, object]] = []
        for event in event_list[-32:]:
            event_facts.append(
                {
                    "type": type(event).__name__,
                    "ts_event": getattr(event, "ts_event", None),
                    "last_qty": _public_quantity_text(getattr(event, "last_qty", None)),
                    "last_px": _public_quantity_text(getattr(event, "last_px", None)),
                }
            )
        order_facts.append(
            {
                "side": _order_enum_name(order, "side"),
                "type": _order_enum_name(order, "order_type"),
                "status": _order_enum_name(order, "status"),
                "quantity": _decimal_text(_order_quantity(order)),
                "filled_quantity": _decimal_text(_filled_quantity(order)),
                "price": _optional_decimal_text(_order_price(order)),
                "reduce_only": _order_reduce_only(order),
                "post_only": _order_bool(order, "is_post_only"),
                "ts_submitted": getattr(order, "ts_submitted", None),
                "ts_accepted": getattr(order, "ts_accepted", None),
                "events": event_facts,
                "events_truncated": len(event_list) > 32,
            }
        )
    account = cache.account(AccountId.from_str(EXEC_ACCOUNT_ID))
    account_event = (
        getattr(account, "last_event", None) if account is not None else None
    )
    account_event = account_event() if callable(account_event) else account_event
    info = getattr(account_event, "info", None)
    initial_margin = (
        info.get("total_initial_margin") if isinstance(info, dict) else None
    )
    account_mode = snapshot.account_mode
    return {
        "phase": phase.name,
        "phase_kind": phase_kind,
        "started_at_ns": snapshot.started_at_ns,
        "ended_at_ns": snapshot.ended_at_ns,
        "orders": order_facts,
        "orders_truncated": len(orders) > 8,
        "positions": [
            {
                "instrument_id": str(getattr(position, "instrument_id", "")),
                "signed_quantity": _optional_decimal_text(
                    _signed_position_quantity(position)
                ),
            }
            for position in positions[:8]
        ],
        "positions_truncated": len(positions) > 8,
        "account": {
            "event_count": _account_event_count(cache),
            "last_event_ts": getattr(account_event, "ts_event", None),
            "total_balance": _optional_decimal_text(
                _account_total_balance(cache, plan)
            ),
            "initial_margin": _public_numeric_text(initial_margin),
            "net_position_shape_consistent": (
                account_mode.net_position_shape_consistent
                if account_mode is not None
                else None
            ),
            "account_scope_consistent": (
                account_mode.account_scope_consistent
                if account_mode is not None
                else None
            ),
            "mark_price_min": (
                _optional_decimal_text(account_mode.mark_price_min)
                if account_mode is not None
                else None
            ),
            "mark_price_max": (
                _optional_decimal_text(account_mode.mark_price_max)
                if account_mode is not None
                else None
            ),
        },
        "global_state": {
            "active_orders": snapshot.active_order_count,
            "inflight_orders": snapshot.inflight_order_count,
            "pending_orders": snapshot.pending_order_count,
            "open_positions": snapshot.open_position_count,
            "net_quantity": _decimal_text(snapshot.final_net_quantity),
            "unresolved_unknown": snapshot.unresolved_unknown_count,
        },
        "diagnostic": (
            None
            if snapshot.failure is None
            else {
                "code": snapshot.failure.code,
                "phase": snapshot.failure.phase,
                "codes": list(snapshot.failure.diagnostic_codes),
            }
        ),
    }


def _public_numeric_text(value: object) -> str | None:
    if value is None:
        return None
    try:
        numeric = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return _decimal_text(numeric) if numeric.is_finite() else None


def _public_quantity_text(value: object) -> str | None:
    decimal_value = getattr(value, "as_decimal", None)
    result = decimal_value() if callable(decimal_value) else value
    return (
        _decimal_text(result)
        if isinstance(result, Decimal) and result.is_finite()
        else None
    )


def _capture_phase_snapshot(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    *,
    phase: Stage4DemoExecPhasePlan,
    phase_kind: Literal["canary", "passive", "cleanup"],
    started_at_ns: int,
    ended_at_ns: int,
    account_before_count: int,
    account_before_balance: Decimal | None,
    account_mode: Stage4DemoAccountModeObservation | None,
    failure: Stage4DemoExecFailure | None,
    market_sequence: _MarketTimestampSequence,
    order_submission_unknown: bool = False,
    cleanup_authorization: Stage4DemoCleanupAuthorization | None = None,
    market_input_conflicting: bool = False,
    stop_unconfirmed: bool = False,
) -> _ExecPhaseSnapshot:
    orders = cache.orders(
        instrument_id=plan.instrument.id,
        strategy_id=_require_phase_strategy_id(phase),
    )
    open_orders = cache.orders_open_count()
    inflight_orders = cache.orders_inflight_count()
    pending_orders = sum(
        _order_bool(order, "is_pending_cancel")
        or _order_bool(order, "is_pending_update")
        for order in cache.orders()
    )
    terminal = bool(orders) and all(_order_bool(order, "is_closed") for order in orders)
    submitted = bool(orders) and all(
        getattr(order, "ts_submitted", None) is not None for order in orders
    )
    accepted = bool(orders) and all(
        getattr(order, "ts_accepted", None) is not None for order in orders
    )
    rejected = any(_order_rejected(order) for order in orders)
    ambiguous = submitted and not terminal and not rejected
    filled_quantities = [_filled_quantity(order) for order in orders]
    order_quantities = [_order_quantity(order) for order in orders]
    complete_fills = bool(orders) and all(
        filled == quantity and quantity > 0
        for filled, quantity in zip(filled_quantities, order_quantities, strict=True)
    )
    partial_fill = any(
        0 < filled < quantity
        for filled, quantity in zip(filled_quantities, order_quantities, strict=True)
    )
    positions = cache.positions_open()
    owned_positions = (
        cache.positions(instrument_id=plan.instrument.id)
        if phase_kind == "cleanup"
        else cache.positions(
            instrument_id=plan.instrument.id,
            strategy_id=_require_phase_strategy_id(phase),
        )
    )
    signed_positions = [_signed_position_quantity(position) for position in positions]
    position_unknown = any(value is None for value in signed_positions)
    final_net = sum(
        (value for value in signed_positions if value is not None),
        start=Decimal(0),
    )
    account_after_count = _account_event_count(cache)
    unresolved = (
        int(ambiguous)
        + int(bool(inflight_orders))
        + int(bool(pending_orders))
        + int(position_unknown)
        + int(not orders and order_submission_unknown)
        + int(stop_unconfirmed)
    )
    resolved_failure = failure
    action_consistent = _phase_order_action_consistent(plan, phase_kind, orders)
    if phase_kind == "cleanup":
        action_consistent = (
            cleanup_authorization is not None
            and _cleanup_action_matches(
                cache,
                plan,
                _require_phase_strategy_id(phase),
                cleanup_authorization,
            )
        )
    if (
        phase_kind != "cleanup"
        and terminal
        and not action_consistent
        and (
            resolved_failure is None
            or resolved_failure.code
            in {
                "CONNECT_SUBSCRIPTION_FAILED",
                "ORDER_TIMEOUT",
                "CLEANUP_INCOMPLETE",
            }
        )
    ):
        resolved_failure = Stage4DemoExecFailure(
            "TERMINAL_FACT_CONFLICTING",
            "reconciliation",
            ("FAILED",),
        )
    if resolved_failure is None and rejected:
        resolved_failure = Stage4DemoExecFailure(
            "ORDER_REJECTED", "execution", ("ORDER_REJECTED",)
        )
    if (
        resolved_failure is None
        and phase_kind == "passive"
        and any(filled > 0 for filled in filled_quantities)
    ):
        resolved_failure = Stage4DemoExecFailure(
            "CANCEL_FILL_RACE", "execution", ("FAILED",)
        )
    if resolved_failure is None and (
        open_orders or inflight_orders or (positions and phase_kind != "canary")
    ):
        resolved_failure = Stage4DemoExecFailure(
            "CLEANUP_INCOMPLETE", "cleanup", ("FAILED",)
        )
    if resolved_failure is None and ambiguous:
        resolved_failure = Stage4DemoExecFailure(
            "ORDER_AMBIGUOUS", "execution", ("ORDER_UNKNOWN",)
        )

    no_fill_accounting = (
        phase_kind == "passive"
        and not any(filled > 0 for filled in filled_quantities)
        and not owned_positions
    )
    balance_after_balance = _account_total_balance(cache, plan)
    balance_reconciled = _balance_reconciled(
        plan,
        before=account_before_balance,
        after=balance_after_balance,
        positions=owned_positions,
        no_fill=no_fill_accounting,
    )

    quote_count = cache.quote_count(plan.instrument.id)
    trade_count = cache.trade_count(plan.instrument.id)
    timestamp_valid = _cached_market_timestamps_valid(
        cache,
        plan,
        ended_at_ns,
        market_sequence,
    )
    if not timestamp_valid:
        observed_pair = (
            cache.quote(plan.instrument.id) is not None
            and cache.mark_price(plan.instrument.id) is not None
        )
        market_input_conflicting = market_input_conflicting or observed_pair
        if resolved_failure is None:
            resolved_failure = Stage4DemoExecFailure(
                "TERMINAL_FACT_CONFLICTING" if observed_pair else "DATA_INVALID",
                "data",
                ("FAILED",),
            )
    snapshot = _ExecPhaseSnapshot(
        started_at_ns=started_at_ns,
        ended_at_ns=ended_at_ns,
        quote_count=quote_count,
        trade_count=trade_count,
        market_timestamps_valid=timestamp_valid,
        order_submitted=submitted,
        order_accepted=accepted,
        order_terminal=terminal,
        active_order_count=open_orders,
        inflight_order_count=inflight_orders,
        pending_order_count=pending_orders,
        order_ambiguous=ambiguous,
        fill_complete=complete_fills,
        fill_partial=partial_fill,
        fill_late=phase_kind == "passive"
        and any(filled > 0 for filled in filled_quantities),
        open_position_count=len(positions),
        final_net_quantity=final_net,
        balance_before_observed=account_before_count > 0,
        balance_after_observed=account_after_count > 0,
        balance_change_explained=(
            account_before_count > 0
            and (account_after_count > account_before_count or no_fill_accounting)
            and terminal
            and unresolved == 0
            and balance_reconciled
        ),
        order_action_consistent=action_consistent,
        account_mode=account_mode,
        unresolved_unknown_count=unresolved,
        failure=resolved_failure,
        balance_before_total=account_before_balance,
        market_input_conflicting=market_input_conflicting,
    )
    return replace(
        snapshot,
        phase_observations=(
            _bounded_phase_source_facts(
                cache,
                plan,
                phase=phase,
                phase_kind=phase_kind,
                orders=orders,
                positions=positions,
                snapshot=snapshot,
            ),
        ),
    )


def _logical_observation(
    plan: Stage4DemoExecAttemptPlan,
    *,
    canary: _ExecPhaseSnapshot,
    execution: _ExecPhaseSnapshot,
) -> Stage4DemoExecObservation:
    account_mode = canary.account_mode or Stage4DemoAccountModeObservation(
        operator_gate_confirmed=True,
        canary_complete=False,
        net_position_shape_consistent=False,
        account_scope_consistent=False,
        observed_initial_margin=None,
        mark_price_min=None,
        mark_price_max=None,
    )
    same_phase = canary is execution
    return Stage4DemoExecObservation(
        scenario=plan.scenario,
        instrument=plan.instrument,
        started_at_ns=canary.started_at_ns,
        ended_at_ns=execution.ended_at_ns,
        quote_count=(
            canary.quote_count
            if same_phase
            else canary.quote_count + execution.quote_count
        ),
        trade_count=(
            canary.trade_count
            if same_phase
            else canary.trade_count + execution.trade_count
        ),
        market_timestamps_valid=(
            canary.market_timestamps_valid and execution.market_timestamps_valid
        ),
        order_submitted=execution.order_submitted,
        order_accepted=execution.order_accepted,
        order_terminal=execution.order_terminal,
        active_order_count=execution.active_order_count,
        pending_order_count=execution.pending_order_count,
        order_ambiguous=execution.order_ambiguous,
        fill_complete=execution.fill_complete,
        fill_partial=execution.fill_partial,
        fill_late=execution.fill_late,
        open_position_count=execution.open_position_count,
        final_net_quantity=execution.final_net_quantity,
        balance_before_observed=canary.balance_before_observed,
        balance_after_observed=execution.balance_after_observed,
        balance_change_explained=(
            canary.balance_change_explained
            if same_phase
            else canary.balance_change_explained and execution.balance_change_explained
        ),
        order_action_consistent=execution.order_action_consistent,
        account_mode=account_mode,
        unresolved_unknown_count=execution.unresolved_unknown_count,
        failure=canary.failure or execution.failure,
        market_input_conflicting=(
            canary.market_input_conflicting or execution.market_input_conflicting
        ),
        phase_observations=(
            canary.phase_observations
            if same_phase
            else canary.phase_observations + execution.phase_observations
        ),
    )


def _skipped_phase_snapshot(canary: _ExecPhaseSnapshot) -> _ExecPhaseSnapshot:
    return _ExecPhaseSnapshot(
        started_at_ns=canary.ended_at_ns,
        ended_at_ns=canary.ended_at_ns,
        quote_count=0,
        trade_count=0,
        market_timestamps_valid=False,
        order_submitted=False,
        order_accepted=False,
        order_terminal=False,
        active_order_count=canary.active_order_count,
        inflight_order_count=canary.inflight_order_count,
        pending_order_count=canary.pending_order_count,
        order_ambiguous=False,
        fill_complete=False,
        fill_partial=False,
        fill_late=False,
        open_position_count=canary.open_position_count,
        final_net_quantity=canary.final_net_quantity,
        balance_before_observed=canary.balance_after_observed,
        balance_after_observed=canary.balance_after_observed,
        balance_change_explained=False,
        order_action_consistent=False,
        account_mode=None,
        unresolved_unknown_count=canary.unresolved_unknown_count,
        failure=canary.failure,
        market_input_conflicting=canary.market_input_conflicting,
    )


def _failed_phase_snapshot(
    *,
    started_at_ns: int,
    failure: Stage4DemoExecFailure,
) -> _ExecPhaseSnapshot:
    return _ExecPhaseSnapshot(
        started_at_ns=started_at_ns,
        ended_at_ns=time.time_ns(),
        quote_count=0,
        trade_count=0,
        market_timestamps_valid=False,
        order_submitted=False,
        order_accepted=False,
        order_terminal=False,
        active_order_count=0,
        inflight_order_count=0,
        pending_order_count=0,
        order_ambiguous=False,
        fill_complete=False,
        fill_partial=False,
        fill_late=False,
        open_position_count=0,
        final_net_quantity=Decimal(0),
        balance_before_observed=False,
        balance_after_observed=False,
        balance_change_explained=False,
        order_action_consistent=False,
        account_mode=None,
        unresolved_unknown_count=1,
        failure=failure,
    )


def _merge_cleanup_snapshot(
    execution: _ExecPhaseSnapshot,
    cleanup: _ExecPhaseSnapshot,
) -> _ExecPhaseSnapshot:
    return _ExecPhaseSnapshot(
        started_at_ns=execution.started_at_ns,
        ended_at_ns=cleanup.ended_at_ns,
        quote_count=execution.quote_count + cleanup.quote_count,
        trade_count=execution.trade_count + cleanup.trade_count,
        market_timestamps_valid=(
            execution.market_timestamps_valid and cleanup.market_timestamps_valid
        ),
        order_submitted=execution.order_submitted,
        order_accepted=execution.order_accepted,
        order_terminal=execution.order_terminal,
        active_order_count=cleanup.active_order_count,
        inflight_order_count=cleanup.inflight_order_count,
        pending_order_count=cleanup.pending_order_count,
        order_ambiguous=execution.order_ambiguous,
        fill_complete=execution.fill_complete,
        fill_partial=execution.fill_partial,
        fill_late=execution.fill_late,
        open_position_count=cleanup.open_position_count,
        final_net_quantity=cleanup.final_net_quantity,
        balance_before_observed=execution.balance_before_observed,
        balance_after_observed=cleanup.balance_after_observed,
        balance_change_explained=(
            cleanup.balance_change_explained
            if execution.account_mode is not None
            else execution.balance_change_explained and cleanup.balance_change_explained
        ),
        order_action_consistent=(
            execution.order_action_consistent and cleanup.order_action_consistent
        ),
        account_mode=execution.account_mode,
        unresolved_unknown_count=cleanup.unresolved_unknown_count,
        failure=cleanup.failure or execution.failure,
        balance_before_total=execution.balance_before_total,
        market_input_conflicting=(
            execution.market_input_conflicting or cleanup.market_input_conflicting
        ),
        phase_observations=(execution.phase_observations + cleanup.phase_observations),
    )


def _account_event_count(cache: Cache) -> int:
    account = cache.account(AccountId.from_str(EXEC_ACCOUNT_ID))
    if account is None:
        return 0
    value = getattr(account, "event_count", 0)
    value = value() if callable(value) else value
    return value if isinstance(value, int) and value >= 0 else 0


def _account_total_balance(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
) -> Decimal | None:
    account = cache.account(AccountId.from_str(EXEC_ACCOUNT_ID))
    if account is None:
        return None
    balance_total = getattr(account, "balance_total", None)
    if not callable(balance_total):
        return None
    try:
        money = balance_total(plan.instrument.quote_currency)
    except Exception:
        return None
    amount = getattr(money, "as_decimal", None)
    if not callable(amount):
        return None
    value = amount()
    return value if isinstance(value, Decimal) and value.is_finite() else None


def _pre_order_account_observation(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
    *,
    phase_kind: Literal["canary", "passive", "cleanup"] = "canary",
) -> tuple[int, Decimal | None]:
    """Use a public account event initialized before this phase's first order."""
    orders = cache.orders(instrument_id=plan.instrument.id, strategy_id=strategy_id)
    submitted: list[int] = []
    for order in orders:
        if phase_kind == "cleanup" and not _order_reduce_only(order):
            continue
        timestamp = getattr(order, "ts_submitted", None)
        if not isinstance(timestamp, int) or timestamp <= 0:
            return 0, None
        submitted.append(timestamp)
    if not submitted:
        return 0, None
    account = cache.account(AccountId.from_str(EXEC_ACCOUNT_ID))
    events = getattr(account, "events", None) if account is not None else None
    if not isinstance(events, (list, tuple)):
        return 0, None
    first_order_ns = min(submitted)
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        event_ns = getattr(event, "ts_init", None)
        if not isinstance(event_ns, int) or event_ns > first_order_ns:
            continue
        balances = getattr(event, "balances", None)
        if not isinstance(balances, (list, tuple)):
            return 0, None
        matching = [
            balance
            for balance in balances
            if getattr(getattr(balance, "total", None), "currency", None)
            == plan.instrument.quote_currency
        ]
        if len(matching) != 1:
            return 0, None
        amount = getattr(matching[0].total, "as_decimal", None)
        if not callable(amount):
            return 0, None
        value = amount()
        if not isinstance(value, Decimal) or not value.is_finite():
            return 0, None
        return index + 1, value
    return 0, None


def _balance_reconciled(
    plan: Stage4DemoExecAttemptPlan,
    *,
    before: Decimal | None,
    after: Decimal | None,
    positions: list[object],
    no_fill: bool,
) -> bool:
    if before is None or after is None:
        return False
    if no_fill:
        return after == before
    if not positions:
        return False
    realized = Decimal(0)
    for position in positions:
        if not _order_bool(position, "is_closed"):
            return False
        pnl = getattr(position, "realized_pnl", None)
        commissions = _position_commissions(position)
        if (
            pnl is None
            or getattr(pnl, "currency", None) != plan.instrument.quote_currency
            or not commissions
            or any(
                getattr(fee, "currency", None) != plan.instrument.quote_currency
                for fee in commissions
            )
        ):
            return False
        amount = getattr(pnl, "as_decimal", None)
        if not callable(amount):
            return False
        value = amount()
        if not isinstance(value, Decimal) or not value.is_finite():
            return False
        for fee in commissions:
            fee_amount = getattr(fee, "as_decimal", None)
            if not callable(fee_amount):
                return False
            observed_fee = fee_amount()
            if (
                not isinstance(observed_fee, Decimal)
                or not observed_fee.is_finite()
                or observed_fee < 0
            ):
                return False
        # Nautilus Position.realized_pnl already includes cost-currency fees.
        realized += value
    quantum = Decimal(1).scaleb(-plan.instrument.quote_currency.precision)
    return abs((after - before) - realized) <= quantum


def _cached_market_timestamps_valid(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    observed_at_ns: int,
    sequence: _MarketTimestampSequence,
) -> bool:
    quote = cache.quote(plan.instrument.id)
    mark = cache.mark_price(plan.instrument.id)
    return (
        quote is not None
        and mark is not None
        and quote.instrument_id == plan.instrument.id
        and mark.instrument_id == plan.instrument.id
        and quote.ts_event >= sequence.quote_ns
        and mark.ts_event >= sequence.mark_ns
        and _timestamp_is_fresh(quote.ts_event, observed_at_ns)
        and _timestamp_is_fresh(mark.ts_event, observed_at_ns)
    )


def _timestamp_is_fresh(timestamp_ns: int, observed_at_ns: int) -> bool:
    return (
        timestamp_ns <= observed_at_ns + _FUTURE_TOLERANCE_NS
        and observed_at_ns - timestamp_ns <= _MAX_PRICE_AGE_NS
    )


def _instrument_matches(
    observed: CryptoPerpetual,
    planned: CryptoPerpetual,
) -> bool:
    return (
        observed.id == planned.id
        and observed.price_precision == planned.price_precision
        and observed.size_precision == planned.size_precision
        and observed.price_increment == planned.price_increment
        and observed.size_increment == planned.size_increment
        and observed.min_quantity == planned.min_quantity
        and observed.max_quantity == planned.max_quantity
        and observed.min_notional == planned.min_notional
    )


def _order_bool(order: object, name: str) -> bool:
    value = getattr(order, name, False)
    value = value() if callable(value) else value
    return value if isinstance(value, bool) else False


def _require_phase_strategy_id(phase: Stage4DemoExecPhasePlan) -> StrategyId:
    strategy_id = phase.tester_config.strategy_id
    if strategy_id is None:
        raise Stage4DemoExecutionError("ExecTester phase strategy identity is missing")
    return strategy_id


def _order_quantity(order: object) -> Decimal:
    value = getattr(order, "quantity", None)
    if value is None:
        return Decimal(0)
    decimal_value = getattr(value, "as_decimal", None)
    if not callable(decimal_value):
        return Decimal(0)
    result = decimal_value()
    return result if isinstance(result, Decimal) else Decimal(0)


def _filled_quantity(order: object) -> Decimal:
    value = getattr(order, "filled_qty", None)
    if value is None:
        return Decimal(0)
    decimal_value = getattr(value, "as_decimal", None)
    if not callable(decimal_value):
        return Decimal(0)
    result = decimal_value()
    return result if isinstance(result, Decimal) else Decimal(0)


def _signed_position_quantity(position: object) -> Decimal | None:
    quantity = getattr(position, "quantity", None)
    decimal_value = getattr(quantity, "as_decimal", None)
    if not callable(decimal_value):
        return None
    result = decimal_value()
    if not isinstance(result, Decimal) or result <= 0:
        return None
    if _order_bool(position, "is_long"):
        return result
    if _order_bool(position, "is_short"):
        return -result
    return None


def _position_commissions(position: object) -> list[object]:
    value = getattr(position, "commissions", None)
    if not callable(value):
        return []
    result = value()
    return list(result) if isinstance(result, list) else []


def _order_rejected(order: object) -> bool:
    return _order_enum_name(order, "status") in {"DENIED", "REJECTED"}


def _order_enum_name(order: object, name: str) -> str:
    value = getattr(order, name, None)
    value = value() if callable(value) else value
    return str(value).upper().rsplit(".", maxsplit=1)[-1]


def _order_price(order: object) -> Decimal | None:
    value = getattr(order, "price", None)
    value = value() if callable(value) else value
    decimal_value = getattr(value, "as_decimal", None)
    if not callable(decimal_value):
        return None
    result = decimal_value()
    return result if isinstance(result, Decimal) else None


def _order_reduce_only(order: object) -> bool:
    return _order_bool(order, "is_reduce_only")


def _filled_market_order_matches(
    order: object,
    *,
    side: str,
    quantity: Quantity,
    reduce_only: bool,
) -> bool:
    expected_quantity = quantity.as_decimal()
    return (
        _order_enum_name(order, "order_type") == "MARKET"
        and _order_enum_name(order, "side") == side
        and _order_reduce_only(order) is reduce_only
        and _order_quantity(order) == expected_quantity
        and _filled_quantity(order) == expected_quantity
        and _order_enum_name(order, "status") == "FILLED"
        and _order_bool(order, "is_closed")
    )


def _canary_open_order_matches(order: object, quantity: Quantity) -> bool:
    return _filled_market_order_matches(
        order,
        side="BUY",
        quantity=quantity,
        reduce_only=False,
    )


def _passive_order_matches(
    plan: Stage4DemoExecAttemptPlan,
    order: object,
) -> bool:
    if plan.passive_price is None or plan.passive_quantity is None:
        return False
    return (
        _order_enum_name(order, "order_type") == "LIMIT"
        and _order_enum_name(order, "side") == "BUY"
        and not _order_reduce_only(order)
        and _order_bool(order, "is_post_only")
        and _order_quantity(order) == plan.passive_quantity.as_decimal()
        and _filled_quantity(order) == 0
        and _order_price(order) == plan.passive_price.as_decimal()
        and getattr(order, "ts_accepted", None) is not None
        and _order_enum_name(order, "status") == "CANCELED"
        and _order_bool(order, "is_closed")
    )


def _phase_order_action_consistent(
    plan: Stage4DemoExecAttemptPlan,
    phase_kind: Literal["canary", "passive", "cleanup"],
    orders: list[object],
) -> bool:
    if phase_kind == "canary":
        return len(orders) == 1 and _canary_open_order_matches(
            orders[0], plan.canary_quantity
        )
    if phase_kind == "passive":
        return len(orders) == 1 and _passive_order_matches(plan, orders[0])
    return True


def _build_attempt_plan(
    *,
    scenario: DemoEvidenceScenario,
    admission: AdmittedDemoAttempt,
    repository_root: Path,
    evidence_root: Path,
    batch_id: str,
    market_input: Stage4DemoExecMarketInput,
) -> Stage4DemoExecAttemptPlan:
    instrument_id = InstrumentId.from_str(DEMO_INSTRUMENT_ID)
    client_id = ClientId.from_str(EXEC_CLIENT_NAME)
    canary_quantity = minimum_order_quantity(
        market_input.instrument,
        market_input.mark_price,
    )
    passive_price: Price | None = None
    passive_quantity: Quantity | None = None
    phases = [
        Stage4DemoExecPhasePlan(
            name="attempt_local_market_canary",
            tester_config=_canary_tester_config(
                scenario=scenario,
                instrument_id=instrument_id,
                client_id=client_id,
                quantity=canary_quantity,
            ),
            run_condition="always",
        )
    ]
    phases.append(
        Stage4DemoExecPhasePlan(
            name="canary_exact_reduce_only_close_after_proof",
            tester_config=_cleanup_tester_config(
                instrument_id=instrument_id,
                client_id=client_id,
                strategy_id=_require_phase_strategy_id(phases[0]),
            ),
            run_condition="cleanup_with_proof",
        )
    )
    if scenario is DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL:
        passive_price = market_input.best_bid - market_input.instrument.price_increment
        if passive_price.as_decimal() <= 0:
            raise Stage4DemoExecutionError("passive price is not positive")
        passive_quantity = minimum_order_quantity(
            market_input.instrument, passive_price
        )
        phases.append(
            Stage4DemoExecPhasePlan(
                name="single_passive_post_only_accept_and_cancel",
                tester_config=_passive_tester_config(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    quantity=passive_quantity,
                ),
                run_condition="always",
            )
        )
        phases.append(
            Stage4DemoExecPhasePlan(
                name="failure_only_exact_reduce_only_cleanup",
                tester_config=_cleanup_tester_config(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    strategy_id=_require_phase_strategy_id(phases[2]),
                ),
                run_condition="failure_with_cleanup_proof",
            )
        )

    data_config = _public_data_client_config()
    observer_config = DataTesterConfig(
        client_id=client_id,
        instrument_ids=[instrument_id],
        subscribe_quotes=True,
        subscribe_trades=True,
        subscribe_mark_prices=True,
        subscribe_instrument=True,
        can_unsubscribe=True,
        log_data=False,
        log_events=False,
        log_commands=False,
    )
    partition = stage4_demo_partition_path(
        repository_root=repository_root,
        evidence_root=evidence_root,
        batch_id=batch_id,
        scenario=scenario,
    )
    return Stage4DemoExecAttemptPlan(
        scenario=scenario,
        runtime=admission.runtime,
        frozen_config=admission.frozen_config,
        evidence_partition=partition,
        instrument=market_input.instrument,
        market_input=market_input,
        canary_quantity=canary_quantity,
        passive_quantity=passive_quantity,
        passive_price=passive_price,
        data_client_config=data_config,
        observer_config=observer_config,
        phases=tuple(phases),
        builtin_strategy=EXEC_TESTER_BUILTIN,
        deadline_seconds=tuple(
            (phase, _deadline_seconds(phase)) for phase in _EXEC_DEADLINE_PHASES
        ),
        _admission=admission,
    )


def _build_exec_tester_node(
    plan: Stage4DemoExecAttemptPlan,
    phase: Stage4DemoExecPhasePlan,
) -> LiveNode:
    """Build an unconditional phase; failure cleanup requires its proof path."""
    if not any(candidate is phase for candidate in plan.phases):
        raise Stage4DemoExecutionError("ExecTester phase is not owned by its plan")
    if phase.run_condition != "always":
        raise Stage4DemoExecutionError(
            "failure-only cleanup requires terminal and zero-order proof"
        )
    return _build_exec_tester_runtime_unchecked(plan, phase).node


def _build_authorized_cleanup_node(
    plan: Stage4DemoExecAttemptPlan,
    phase: Stage4DemoExecPhasePlan,
    *,
    cleanup_authorization: Stage4DemoCleanupAuthorization,
) -> LiveNode:
    """Build the sole cleanup phase after its exact quantity was authorized."""
    return _build_authorized_cleanup_runtime(
        plan,
        phase,
        cleanup_authorization=cleanup_authorization,
    ).node


def _build_authorized_cleanup_runtime(
    plan: Stage4DemoExecAttemptPlan,
    phase: Stage4DemoExecPhasePlan,
    *,
    cleanup_authorization: Stage4DemoCleanupAuthorization,
) -> _ExecTesterRuntime:
    """Build the sole cleanup node with a private proof-gated Strategy."""
    if not any(candidate is phase for candidate in plan.phases):
        raise Stage4DemoExecutionError(
            "ExecTester cleanup phase is not owned by its plan"
        )
    if phase.run_condition not in {"cleanup_with_proof", "failure_with_cleanup_proof"}:
        raise Stage4DemoExecutionError("phase is not an authorized cleanup phase")
    source = plan.phases[0] if phase is plan.phases[1] else plan.phases[2]
    strategy_id = _require_phase_strategy_id(phase)
    if strategy_id != _require_phase_strategy_id(source):
        raise Stage4DemoExecutionError("cleanup strategy does not own the position")
    if (
        exact_reduce_only_quantity(
            plan.instrument,
            cleanup_authorization.signed_position,
        )
        != cleanup_authorization.quantity
    ):
        raise Stage4DemoExecutionError("cleanup quantity is not exact")
    node = _build_exec_node(plan)
    cleanup_strategy = _ExactCleanupStrategy(plan, strategy_id, cleanup_authorization)
    node.add_strategy(cleanup_strategy)
    node.add_builtin_actor(_DATA_TESTER_BUILTIN, plan.observer_config)
    return _ExecTesterRuntime(node=node, cleanup_strategy=cleanup_strategy)


def _build_exec_tester_runtime_unchecked(
    plan: Stage4DemoExecAttemptPlan,
    phase: Stage4DemoExecPhasePlan,
) -> _ExecTesterRuntime:
    """Construct a previously authorized official-tester phase node."""
    node = _build_exec_node(plan)
    node.add_builtin_actor(_DATA_TESTER_BUILTIN, plan.observer_config)
    node.add_builtin_strategy(plan.builtin_strategy, phase.tester_config)
    return _ExecTesterRuntime(node=node)


def _build_exec_node(plan: Stage4DemoExecAttemptPlan) -> LiveNode:
    return (
        LiveNode.builder(
            _EXEC_NODE_NAME,
            TraderId.from_str(_EXEC_TRADER_ID),
            Environment.LIVE,
        )
        .with_timeout_connection(_deadline_seconds(DeadlinePhase.CONNECT))
        .with_timeout_reconciliation(_deadline_seconds(DeadlinePhase.RECONCILIATION))
        .with_timeout_disconnection_secs(_deadline_seconds(DeadlinePhase.CLEANUP))
        .with_delay_post_stop_secs(0)
        .with_delay_shutdown_secs(_deadline_seconds(DeadlinePhase.CLEANUP))
        .with_reconciliation(True)
        .with_risk_engine_config(_exec_tester_only_risk_config())
        .add_data_client(
            EXEC_CLIENT_NAME,
            BinanceDataClientFactory(),
            plan.data_client_config,
        )
        .add_exec_client(
            EXEC_CLIENT_NAME,
            BinanceExecutionClientFactory(),
            build_execution_client_config(
                plan._admission,
                account_id=AccountId.from_str(EXEC_ACCOUNT_ID),
            ),
        )
        .build()
    )


def _exec_tester_only_risk_config() -> LiveRiskEngineConfig:
    """Return the non-exported bypass for this bounded Demo execution entry."""
    return LiveRiskEngineConfig(bypass=True)


def _canary_tester_config(
    *,
    scenario: DemoEvidenceScenario,
    instrument_id: InstrumentId,
    client_id: ClientId,
    quantity: Quantity,
) -> ExecTesterConfig:
    suffix = (
        "MARKET"
        if scenario is DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE
        else "PASSIVE-CANARY"
    )
    return ExecTesterConfig(
        strategy_id=StrategyId.from_str(f"STAGE4-{suffix}-001"),
        instrument_id=instrument_id,
        client_id=client_id,
        order_qty=quantity,
        open_position_on_start_qty=quantity.as_decimal(),
        open_position_on_first_quote=True,
        enable_limit_buys=False,
        enable_limit_sells=False,
        enable_stop_buys=False,
        enable_stop_sells=False,
        cancel_orders_on_stop=True,
        close_positions_on_stop=False,
        close_positions_qty_precision=None,
        reduce_only_on_stop=True,
        dry_run=False,
        log_data=False,
        log_events=False,
        log_commands=False,
    )


def _passive_tester_config(
    *,
    instrument_id: InstrumentId,
    client_id: ClientId,
    quantity: Quantity,
) -> ExecTesterConfig:
    return ExecTesterConfig(
        strategy_id=StrategyId.from_str("STAGE4-PASSIVE-001"),
        instrument_id=instrument_id,
        client_id=client_id,
        order_qty=quantity,
        open_position_on_start_qty=None,
        enable_limit_buys=True,
        enable_limit_sells=False,
        enable_stop_buys=False,
        enable_stop_sells=False,
        tob_offset_ticks=PASSIVE_OFFSET_TICKS,
        order_expire_time_delta_mins=1,
        use_post_only=True,
        limit_aggressive=False,
        modify_orders_to_maintain_tob_offset=False,
        cancel_replace_orders_to_maintain_tob_offset=False,
        cancel_orders_on_stop=True,
        use_individual_cancels_on_stop=True,
        close_positions_on_stop=False,
        reduce_only_on_stop=True,
        dry_run=False,
        log_data=False,
        log_events=False,
        log_commands=False,
    )


def _cleanup_tester_config(
    *,
    instrument_id: InstrumentId,
    client_id: ClientId,
    strategy_id: StrategyId,
) -> ExecTesterConfig:
    """Keep cleanup plan metadata inert on stop; the Strategy owns its close."""
    return ExecTesterConfig(
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        client_id=client_id,
        enable_limit_buys=False,
        enable_limit_sells=False,
        enable_stop_buys=False,
        enable_stop_sells=False,
        cancel_orders_on_stop=True,
        close_positions_on_stop=False,
        close_positions_qty_precision=None,
        reduce_only_on_stop=True,
        dry_run=False,
        log_data=False,
        log_events=False,
        log_commands=False,
    )


def _public_data_client_config() -> BinanceDataClientConfig:
    return BinanceDataClientConfig(
        product_type=BinanceProductType.USD_M,
        environment=BinanceEnvironment.DEMO,
        base_url_http=None,
        base_url_ws=None,
        api_key=None,
        api_secret=None,
        instrument_provider=BinanceInstrumentProviderConfig(
            load_all=False,
            load_ids=[DEMO_INSTRUMENT_ID],
        ),
    )


def _validate_market_input(
    value: Stage4DemoExecMarketInput,
    *,
    checked_at_ns: int | None = None,
    previous: Stage4DemoExecMarketInput | None = None,
) -> None:
    checked_at_ns = time.time_ns() if checked_at_ns is None else checked_at_ns
    if str(value.instrument.id) != DEMO_INSTRUMENT_ID:
        raise Stage4DemoExecutionError(
            "market input instrument is outside the allowlist"
        )
    if value.best_bid.as_decimal() <= 0 or value.best_ask.as_decimal() <= 0:
        raise Stage4DemoExecutionError("market input quote is not positive")
    if value.best_bid.as_decimal() >= value.best_ask.as_decimal():
        raise Stage4DemoExecutionError("market input quote is crossed or locked")
    if value.mark_price.as_decimal() <= 0:
        raise Stage4DemoExecutionError("market input mark price is not positive")
    if value.observed_at_ns > checked_at_ns + _FUTURE_TOLERANCE_NS:
        raise Stage4DemoExecutionError("market observation time is in the future")
    if checked_at_ns - value.observed_at_ns > _MAX_PRICE_AGE_NS:
        raise Stage4DemoExecutionError("market observation is stale at check time")
    if previous is not None and (
        value.observed_at_ns <= previous.observed_at_ns
        or value.quote_ts_event_ns < previous.quote_ts_event_ns
        or value.mark_ts_event_ns < previous.mark_ts_event_ns
    ):
        raise Stage4DemoExecutionError("market input was reused or moved backward")
    for name, timestamp in (
        ("quote", value.quote_ts_event_ns),
        ("mark", value.mark_ts_event_ns),
    ):
        if timestamp > value.observed_at_ns + _FUTURE_TOLERANCE_NS:
            raise Stage4DemoExecutionError(f"{name} timestamp is in the future")
        if value.observed_at_ns - timestamp > _MAX_PRICE_AGE_NS:
            raise Stage4DemoExecutionError(f"{name} input is stale")


def _derive_failure(
    observation: Stage4DemoExecObservation,
    *,
    account_consistent: bool,
    cleanup_consistent: bool,
) -> Stage4DemoExecFailure:
    if not account_consistent:
        return Stage4DemoExecFailure(
            "TERMINAL_FACT_UNKNOWN",
            "account_mode",
            ("ACCOUNT_MODE_UNKNOWN",),
        )
    if observation.order_ambiguous:
        return Stage4DemoExecFailure(
            "ORDER_AMBIGUOUS",
            "execution",
            ("ORDER_UNKNOWN",),
        )
    if observation.fill_partial or observation.fill_late:
        return Stage4DemoExecFailure(
            "CANCEL_FILL_RACE",
            "execution",
            ("FAILED",),
        )
    if not cleanup_consistent:
        diagnostics: list[str] = []
        if observation.active_order_count:
            diagnostics.append("ACTIVE_ORDERS_REMAIN")
        if observation.pending_order_count:
            diagnostics.append("PENDING_ORDERS_REMAIN")
        if observation.open_position_count or observation.final_net_quantity != 0:
            diagnostics.append("OPEN_POSITION_REMAINS")
        if observation.unresolved_unknown_count:
            diagnostics.append("UNRESOLVED_UNKNOWN")
        return Stage4DemoExecFailure(
            "CLEANUP_INCOMPLETE",
            "cleanup",
            tuple(diagnostics or ["FAILED"]),
        )
    return Stage4DemoExecFailure(
        "TERMINAL_FACT_CONFLICTING",
        "reconciliation",
        ("FAILED",),
    )


def _classification(consistent: bool, observed: bool) -> str:
    if consistent:
        return EvidenceClassification.CONSISTENT.value
    if not observed:
        return EvidenceClassification.MISSING.value
    return EvidenceClassification.CONFLICTING.value


def _instrument_payload(instrument: CryptoPerpetual) -> dict[str, object]:
    return {
        "id": str(instrument.id),
        "price_precision": instrument.price_precision,
        "price_increment": _decimal_text(instrument.price_increment.as_decimal()),
        "size_precision": instrument.size_precision,
        "size_increment": _decimal_text(instrument.size_increment.as_decimal()),
        "minimum_quantity": (
            _decimal_text(instrument.min_quantity.as_decimal())
            if instrument.min_quantity is not None
            else None
        ),
        "maximum_quantity": (
            _decimal_text(instrument.max_quantity.as_decimal())
            if instrument.max_quantity is not None
            else None
        ),
        "minimum_notional": (
            _decimal_text(instrument.min_notional.as_decimal())
            if instrument.min_notional is not None
            else None
        ),
    }


def _batch_id_from_partition(partition: Path) -> str:
    # stage4_demo_partition_path creates <root>/<batch_id>/<scenario>.
    return partition.parent.name


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered if rendered not in {"", "-0"} else "0"


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def _utc_timestamp(timestamp_ns: int) -> str:
    value = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _deadline_seconds(phase: DeadlinePhase) -> int:
    return dict(DEADLINE_SECONDS)[phase]


__all__ = [
    "EXEC_TESTER_BUILTIN",
    "PASSIVE_OFFSET_TICKS",
    "Stage4DemoAccountModeObservation",
    "Stage4DemoCleanupAuthorization",
    "Stage4DemoExecAttemptInput",
    "Stage4DemoExecBatchOutcome",
    "Stage4DemoExecFailure",
    "Stage4DemoExecMarketInput",
    "Stage4DemoExecObservation",
    "Stage4DemoExecOutcome",
    "Stage4DemoExecPlan",
    "Stage4DemoExecutionError",
    "build_stage4_demo_exec_evidence",
    "build_stage4_demo_exec_plan",
    "prove_exact_cleanup_quantity",
    "run_stage4_demo_exec",
    "write_stage4_demo_exec_evidence",
]
