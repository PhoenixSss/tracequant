"""Bounded official ExecTester entry for Stage 4 Binance Demo execution."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

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
PASSIVE_OFFSET_TICKS: Final = 1
_EXEC_NODE_NAME: Final = "TRACEQUANT-STAGE4-EXEC-TESTER"
_EXEC_TRADER_ID: Final = "TRACEQUANT-001"
_FUTURE_TOLERANCE_NS: Final = 1_000_000_000
_MAX_PRICE_AGE_NS: Final = 5_000_000_000
_MAX_MARK_SPREAD_TEXT: Final = "0.005"
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
class Stage4DemoExecPhasePlan:
    """One sequential official-tester phase; phases never overlap."""

    name: str
    tester_config: ExecTesterConfig
    run_condition: Literal["always", "failure_with_cleanup_proof"]


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
    """The two mandatory, independent Stage 4 ExecTester attempts."""

    market_close: Stage4DemoExecAttemptPlan
    passive_cancel: Stage4DemoExecAttemptPlan

    @property
    def attempts(self) -> tuple[Stage4DemoExecAttemptPlan, ...]:
        return (self.market_close, self.passive_cancel)


@dataclass(frozen=True)
class Stage4DemoAccountModeObservation:
    """Attempt-local public cache/account facts captured after its canary."""

    operator_gate_confirmed: bool
    canary_complete: bool
    one_way_confirmed: bool
    isolated_confirmed: bool
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
    account_mode: Stage4DemoAccountModeObservation
    unresolved_unknown_count: int
    failure: Stage4DemoExecFailure | None = None


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
    account_mode: Stage4DemoAccountModeObservation | None
    unresolved_unknown_count: int
    failure: Stage4DemoExecFailure | None


class _AccountQueryProbe(Strategy):
    """Private query-only Strategy; it has no order-construction path."""

    def __init__(self) -> None:
        super().__init__(
            StrategyConfig(
                strategy_id=StrategyId.from_str("STAGE4-ACCOUNT-QUERY-001"),
                log_events=False,
                log_commands=False,
            )
        )
        self._query_requested = False

    def query_once(self) -> None:
        if self._query_requested:
            raise Stage4DemoExecutionError("account query cannot be repeated")
        self._query_requested = True
        self.query_account(
            AccountId.from_str(EXEC_ACCOUNT_ID),
            ClientId.from_str(EXEC_CLIENT_NAME),
        )


@dataclass(frozen=True)
class _ExecTesterRuntime:
    node: LiveNode
    account_query: _AccountQueryProbe


def build_stage4_demo_exec_plan(
    *,
    batch: DemoAdmissionBatch,
    evidence_root: Path,
    batch_id: str,
    market_close_input: Stage4DemoExecMarketInput,
    passive_cancel_input: Stage4DemoExecMarketInput,
    market_operator_token: str,
    passive_operator_token: str,
) -> Stage4DemoExecPlan:
    """Build the only order-enabled plan: two separately gated attempts."""
    if market_close_input is passive_cancel_input:
        raise Stage4DemoExecutionError("ExecTester attempts cannot reuse price input")
    _validate_market_input(market_close_input)
    _validate_market_input(passive_cancel_input)
    market_admission = admit_current_demo_attempt(
        batch=batch,
        operator_token=market_operator_token,
    )
    passive_admission = admit_current_demo_attempt(
        batch=batch,
        operator_token=passive_operator_token,
    )
    if market_admission is passive_admission:
        raise Stage4DemoExecutionError(
            "ExecTester attempts require independent admission proofs"
        )

    market_plan = _build_attempt_plan(
        scenario=DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE,
        admission=market_admission,
        repository_root=batch.repository_root,
        evidence_root=evidence_root,
        batch_id=batch_id,
        market_input=market_close_input,
    )
    passive_plan = _build_attempt_plan(
        scenario=DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL,
        admission=passive_admission,
        repository_root=batch.repository_root,
        evidence_root=evidence_root,
        batch_id=batch_id,
        market_input=passive_cancel_input,
    )
    if market_plan.evidence_partition == passive_plan.evidence_partition:
        raise Stage4DemoExecutionError(
            "ExecTester attempts require separate partitions"
        )
    return Stage4DemoExecPlan(
        market_close=market_plan,
        passive_cancel=passive_plan,
    )


def run_stage4_demo_exec(
    *,
    batch: DemoAdmissionBatch,
    evidence_root: Path,
    batch_id: str,
    market_close_input: Stage4DemoExecMarketInput,
    passive_cancel_input: Stage4DemoExecMarketInput,
    market_operator_token: str,
    passive_operator_token: str,
) -> Stage4DemoExecBatchOutcome:
    """Run both fixed logical attempts and persist their separate records."""
    plan = build_stage4_demo_exec_plan(
        batch=batch,
        evidence_root=evidence_root,
        batch_id=batch_id,
        market_close_input=market_close_input,
        passive_cancel_input=passive_cancel_input,
        market_operator_token=market_operator_token,
        passive_operator_token=passive_operator_token,
    )
    if any(attempt.evidence_partition.exists() for attempt in plan.attempts):
        raise Stage4DemoExecutionError("ExecTester evidence partition already exists")
    market_observation, passive_observation = asyncio.run(
        _observe_stage4_demo_exec(plan)
    )
    market_evidence = build_stage4_demo_exec_evidence(
        plan.market_close,
        market_observation,
    )
    passive_evidence = build_stage4_demo_exec_evidence(
        plan.passive_cancel,
        passive_observation,
    )
    return Stage4DemoExecBatchOutcome(
        market_close=write_stage4_demo_exec_evidence(
            plan.market_close,
            market_evidence,
        ),
        passive_cancel=write_stage4_demo_exec_evidence(
            plan.passive_cancel,
            passive_evidence,
        ),
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

    allowed_margin = _allowed_initial_margin(plan, observation.account_mode)
    account_consistent = (
        observation.account_mode.operator_gate_confirmed
        and observation.account_mode.canary_complete
        and observation.account_mode.one_way_confirmed
        and observation.account_mode.isolated_confirmed
        and allowed_margin is not None
        and observation.account_mode.observed_initial_margin is not None
        and allowed_margin[0]
        <= observation.account_mode.observed_initial_margin
        <= allowed_margin[1]
    )
    market_consistent = (
        observation.quote_count > 0 and observation.market_timestamps_valid
    )
    order_consistent = (
        observation.order_submitted
        and observation.order_accepted
        and observation.order_terminal
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
                market_consistent, observation.quote_count > 0
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
            "canary_complete": observation.account_mode.canary_complete,
            "one_way_confirmed": observation.account_mode.one_way_confirmed,
            "isolated_confirmed": observation.account_mode.isolated_confirmed,
            "leverage_one_confirmed": account_consistent,
            "observed_initial_margin": _optional_decimal_text(
                observation.account_mode.observed_initial_margin
            ),
            "allowed_initial_margin_min": (
                _decimal_text(allowed_margin[0]) if allowed_margin is not None else None
            ),
            "allowed_initial_margin_max": (
                _decimal_text(allowed_margin[1]) if allowed_margin is not None else None
            ),
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
) -> Stage4DemoExecOutcome:
    """Write one validated attempt record to a new external partition."""
    if evidence.get("scenario") != plan.scenario.value:
        raise Stage4DemoExecutionError("evidence scenario does not match its plan")
    if plan.evidence_partition.exists():
        raise Stage4DemoExecutionError("ExecTester evidence partition already exists")
    rendered = canonical_stage4_demo_evidence_json(evidence)
    plan.evidence_partition.parent.mkdir(parents=True, exist_ok=True)
    try:
        plan.evidence_partition.mkdir()
    except FileExistsError as exc:
        raise Stage4DemoExecutionError(
            "ExecTester evidence partition already exists"
        ) from exc
    evidence_path = plan.evidence_partition / EXEC_EVIDENCE_FILENAME
    try:
        with evidence_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.write("\n")
    except Exception:
        evidence_path.unlink(missing_ok=True)
        plan.evidence_partition.rmdir()
        raise
    return Stage4DemoExecOutcome(plan.evidence_partition, evidence_path, evidence)


def prove_exact_cleanup_quantity(
    plan: Stage4DemoExecAttemptPlan,
    *,
    original_order_terminal: bool,
    active_order_count: int,
    inflight_order_count: int,
    open_position_count: int,
    net_position: Decimal | None,
) -> Quantity | None:
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
    return exact_reduce_only_quantity(plan.instrument, net_position)


async def _observe_stage4_demo_exec(
    plan: Stage4DemoExecPlan,
) -> tuple[Stage4DemoExecObservation, Stage4DemoExecObservation]:
    market_phase = await _run_exec_phase(
        plan.market_close,
        plan.market_close.phases[0],
        phase_kind="canary",
    )
    passive_canary = await _run_exec_phase(
        plan.passive_cancel,
        plan.passive_cancel.phases[0],
        phase_kind="canary",
    )
    if passive_canary.failure is None:
        passive_phase = await _run_exec_phase(
            plan.passive_cancel,
            plan.passive_cancel.phases[1],
            phase_kind="passive",
        )
    else:
        passive_phase = _skipped_phase_snapshot(passive_canary)

    cleanup_quantity = prove_exact_cleanup_quantity(
        plan.passive_cancel,
        original_order_terminal=passive_phase.order_terminal,
        active_order_count=passive_phase.active_order_count,
        inflight_order_count=passive_phase.inflight_order_count,
        open_position_count=passive_phase.open_position_count,
        net_position=passive_phase.final_net_quantity,
    )
    if (
        passive_phase.failure is not None
        and passive_phase.unresolved_unknown_count == 0
        and cleanup_quantity is not None
    ):
        cleanup_phase = await _run_exec_phase(
            plan.passive_cancel,
            plan.passive_cancel.phases[2],
            phase_kind="cleanup",
            cleanup_quantity=cleanup_quantity,
        )
        passive_phase = _merge_cleanup_snapshot(passive_phase, cleanup_phase)

    return (
        _logical_observation(
            plan.market_close,
            canary=market_phase,
            execution=market_phase,
        ),
        _logical_observation(
            plan.passive_cancel,
            canary=passive_canary,
            execution=passive_phase,
        ),
    )


async def _run_exec_phase(
    plan: Stage4DemoExecAttemptPlan,
    phase: Stage4DemoExecPhasePlan,
    *,
    phase_kind: Literal["canary", "passive", "cleanup"],
    cleanup_quantity: Quantity | None = None,
) -> _ExecPhaseSnapshot:
    if phase_kind == "cleanup":
        if cleanup_quantity is None:
            raise Stage4DemoExecutionError("cleanup phase is missing authorization")
        runtime = _build_authorized_cleanup_runtime(
            plan,
            phase,
            cleanup_quantity=cleanup_quantity,
        )
    else:
        if phase.run_condition != "always":
            raise Stage4DemoExecutionError("conditional phase used without proof")
        runtime = _build_exec_tester_runtime_unchecked(plan, phase)

    node = runtime.node
    cache = node.cache
    handle = node.handle()
    started_at_ns = time.time_ns()
    account_before_count = _account_event_count(cache)
    strategy_id = _require_phase_strategy_id(phase)
    run_task: asyncio.Task[None] = asyncio.create_task(node.run_async())
    failure: Stage4DemoExecFailure | None = None
    account_mode: Stage4DemoAccountModeObservation | None = None
    stop_requested = False
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
            lambda: _market_ready(cache, plan),
            deadline=stage4_demo.start_queue_deadline(DeadlinePhase.READY),
            run_task=run_task,
            failure=Stage4DemoExecFailure(
                "CONNECT_SUBSCRIPTION_FAILED", "data", ("FAILED",)
            ),
        )
        account_before_count = _account_event_count(cache)
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
            query_at_ns = time.time_ns()
            previous_account_events = _account_event_count(cache)
            runtime.account_query.query_once()
            await _wait_until(
                lambda: _account_event_count(cache) > previous_account_events,
                deadline=stage4_demo.start_queue_deadline(DeadlinePhase.RECONCILIATION),
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "TERMINAL_FACT_UNKNOWN",
                    "account_mode",
                    ("ACCOUNT_MODE_UNKNOWN",),
                ),
            )
            account_mode = _capture_account_mode(cache, plan, query_at_ns)
            if not _account_mode_complete(account_mode):
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
        handle.stop()
        stop_requested = True
        if phase_kind == "canary":
            await _wait_after_stop(
                lambda: _canary_cleanup_complete(cache, plan, strategy_id),
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
        elif phase_kind == "passive":
            await _wait_after_stop(
                lambda: _passive_cancel_complete(cache, plan, strategy_id),
                deadline=stage4_demo.start_queue_deadline(DeadlinePhase.CANCELLATION),
                run_task=run_task,
                failure=Stage4DemoExecFailure(
                    "ORDER_TIMEOUT", "execution", ("ORDER_UNKNOWN",)
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
    except _Stage4DemoExecutionRuntimeError as exc:
        failure = failure or exc.failure
    except Exception:
        failure = Stage4DemoExecFailure(
            "CONNECT_SUBSCRIPTION_FAILED",
            "execution",
            ("FAILED",),
        )

    cleanup_failure = await _stop_exec_tester(
        handle,
        run_task,
        request_stop=not stop_requested,
    )
    if cleanup_failure is not None:
        failure = cleanup_failure
    ended_at_ns = time.time_ns()
    try:
        return _capture_phase_snapshot(
            cache,
            plan,
            phase=phase,
            phase_kind=phase_kind,
            started_at_ns=started_at_ns,
            ended_at_ns=ended_at_ns,
            account_before_count=account_before_count,
            account_mode=account_mode,
            failure=failure,
        )
    finally:
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
        handle.stop()
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


def _market_ready(cache: Cache, plan: Stage4DemoExecAttemptPlan) -> bool:
    observed_instrument = cache.instrument(plan.instrument.id)
    if not isinstance(observed_instrument, CryptoPerpetual) or not _instrument_matches(
        observed_instrument,
        plan.instrument,
    ):
        return False
    quote = cache.quote(plan.instrument.id)
    mark = cache.mark_price(plan.instrument.id)
    if quote is None or mark is None:
        return False
    if (
        quote.bid_price.as_decimal() <= 0
        or quote.ask_price.as_decimal() <= quote.bid_price.as_decimal()
        or mark.value.as_decimal() <= 0
    ):
        return False
    observed_at_ns = time.time_ns()
    return _timestamp_is_fresh(quote.ts_event, observed_at_ns) and _timestamp_is_fresh(
        mark.ts_event, observed_at_ns
    )


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
        and _order_bool(orders[0], "is_closed")
        and _filled_quantity(orders[0]) == _order_quantity(orders[0])
        and len(positions) == 1
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


def _canary_cleanup_complete(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    strategy_id: StrategyId,
) -> bool:
    orders = cache.orders(
        instrument_id=plan.instrument.id,
        strategy_id=strategy_id,
    )
    return (
        len(orders) == 2
        and all(_order_bool(order, "is_closed") for order in orders)
        and all(
            _filled_quantity(order) == _order_quantity(order) > 0 for order in orders
        )
        and cache.orders_open_count(instrument_id=plan.instrument.id) == 0
        and cache.orders_inflight_count(instrument_id=plan.instrument.id) == 0
        and not cache.positions_open(instrument_id=plan.instrument.id)
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
        and _order_bool(orders[0], "is_closed")
        and cache.orders_open_count(instrument_id=plan.instrument.id) == 0
        and cache.orders_inflight_count(instrument_id=plan.instrument.id) == 0
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
        and cache.orders_open_count(instrument_id=plan.instrument.id) == 0
        and cache.orders_inflight_count(instrument_id=plan.instrument.id) == 0
        and not cache.positions_open(instrument_id=plan.instrument.id)
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
    isolated = one_way and not all_open_orders
    account = cache.account(AccountId.from_str(EXEC_ACCOUNT_ID))
    event = getattr(account, "last_event", None) if account is not None else None
    if callable(event):
        event = event()
    event_ts = getattr(event, "ts_event", None)
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
    isolated = isolated and target_margin_only
    observed_margin: Decimal | None = None
    if (
        isinstance(event_ts, int)
        and abs(event_ts - query_at_ns) <= _MAX_PRICE_AGE_NS
        and isinstance(info, dict)
        and "total_initial_margin" in info
    ):
        try:
            observed_margin = Decimal(str(info["total_initial_margin"]))
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
        one_way_confirmed=one_way,
        isolated_confirmed=isolated,
        observed_initial_margin=observed_margin,
        mark_price_min=min(mark_values) if mark_values else None,
        mark_price_max=max(mark_values) if mark_values else None,
    )


def _account_mode_complete(value: Stage4DemoAccountModeObservation) -> bool:
    return (
        value.operator_gate_confirmed
        and value.canary_complete
        and value.one_way_confirmed
        and value.isolated_confirmed
        and value.observed_initial_margin is not None
        and value.mark_price_min is not None
        and value.mark_price_max is not None
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
    account_mode: Stage4DemoAccountModeObservation | None,
    failure: Stage4DemoExecFailure | None,
) -> _ExecPhaseSnapshot:
    orders = cache.orders(
        instrument_id=plan.instrument.id,
        strategy_id=_require_phase_strategy_id(phase),
    )
    open_orders = cache.orders_open_count(instrument_id=plan.instrument.id)
    inflight_orders = cache.orders_inflight_count(instrument_id=plan.instrument.id)
    pending_orders = sum(
        _order_bool(order, "is_pending_cancel")
        or _order_bool(order, "is_pending_update")
        for order in orders
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
    positions = cache.positions_open(instrument_id=plan.instrument.id)
    owned_positions = cache.positions(
        instrument_id=plan.instrument.id,
        strategy_id=_require_phase_strategy_id(phase),
    )
    signed_positions = [_signed_position_quantity(position) for position in positions]
    position_unknown = any(value is None for value in signed_positions)
    final_net = sum(
        (value for value in signed_positions if value is not None),
        start=Decimal(0),
    )
    account_after_count = _account_event_count(cache)
    unresolved = int(ambiguous) + int(bool(inflight_orders)) + int(position_unknown)
    resolved_failure = failure
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
    if resolved_failure is None and (open_orders or inflight_orders or positions):
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
    filled_accounting = bool(owned_positions) and all(
        _order_bool(position, "is_closed")
        and getattr(position, "realized_pnl", None) is not None
        and bool(_position_commissions(position))
        for position in owned_positions
    )

    quote_count = cache.quote_count(plan.instrument.id)
    trade_count = cache.trade_count(plan.instrument.id)
    timestamp_valid = _cached_market_timestamps_valid(
        cache,
        plan,
        ended_at_ns,
    )
    return _ExecPhaseSnapshot(
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
            and (no_fill_accounting or filled_accounting)
        ),
        account_mode=account_mode,
        unresolved_unknown_count=unresolved,
        failure=resolved_failure,
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
        one_way_confirmed=False,
        isolated_confirmed=False,
        observed_initial_margin=None,
        mark_price_min=None,
        mark_price_max=None,
    )
    return Stage4DemoExecObservation(
        scenario=plan.scenario,
        instrument=plan.instrument,
        started_at_ns=canary.started_at_ns,
        ended_at_ns=execution.ended_at_ns,
        quote_count=canary.quote_count + execution.quote_count,
        trade_count=canary.trade_count + execution.trade_count,
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
            canary.balance_change_explained and execution.balance_change_explained
        ),
        account_mode=account_mode,
        unresolved_unknown_count=execution.unresolved_unknown_count,
        failure=canary.failure or execution.failure,
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
        account_mode=None,
        unresolved_unknown_count=canary.unresolved_unknown_count,
        failure=canary.failure,
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
            execution.balance_change_explained and cleanup.balance_change_explained
        ),
        account_mode=None,
        unresolved_unknown_count=cleanup.unresolved_unknown_count,
        failure=cleanup.failure or execution.failure,
    )


def _account_event_count(cache: Cache) -> int:
    account = cache.account(AccountId.from_str(EXEC_ACCOUNT_ID))
    if account is None:
        return 0
    value = getattr(account, "event_count", 0)
    value = value() if callable(value) else value
    return value if isinstance(value, int) and value >= 0 else 0


def _cached_market_timestamps_valid(
    cache: Cache,
    plan: Stage4DemoExecAttemptPlan,
    observed_at_ns: int,
) -> bool:
    quote = cache.quote(plan.instrument.id)
    mark = cache.mark_price(plan.instrument.id)
    return (
        quote is not None
        and mark is not None
        and quote.instrument_id == plan.instrument.id
        and mark.instrument_id == plan.instrument.id
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
    status = getattr(order, "status", None)
    status = status() if callable(status) else status
    return str(status).upper().rsplit(".", maxsplit=1)[-1] in {
        "DENIED",
        "REJECTED",
    }


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
            name="attempt_local_market_canary_and_exact_reduce_only_close",
            tester_config=_canary_tester_config(
                scenario=scenario,
                instrument_id=instrument_id,
                client_id=client_id,
                quantity=canary_quantity,
            ),
            run_condition="always",
        )
    ]
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
                ),
                run_condition="failure_with_cleanup_proof",
            )
        )

    provider = BinanceInstrumentProviderConfig(
        load_all=False,
        load_ids=[DEMO_INSTRUMENT_ID],
    )
    data_config = BinanceDataClientConfig(
        product_type=BinanceProductType.USD_M,
        environment=BinanceEnvironment.DEMO,
        base_url_http=None,
        base_url_ws=None,
        api_key=None,
        api_secret=None,
        instrument_provider=provider,
    )
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
    cleanup_quantity: Quantity,
) -> LiveNode:
    """Build the sole cleanup phase after its exact quantity was authorized."""
    return _build_authorized_cleanup_runtime(
        plan,
        phase,
        cleanup_quantity=cleanup_quantity,
    ).node


def _build_authorized_cleanup_runtime(
    plan: Stage4DemoExecAttemptPlan,
    phase: Stage4DemoExecPhasePlan,
    *,
    cleanup_quantity: Quantity,
) -> _ExecTesterRuntime:
    """Build the cleanup runtime only from an exact authorized quantity."""
    if not any(candidate is phase for candidate in plan.phases):
        raise Stage4DemoExecutionError(
            "ExecTester cleanup phase is not owned by its plan"
        )
    if phase.run_condition != "failure_with_cleanup_proof":
        raise Stage4DemoExecutionError("phase is not the failure-only cleanup phase")
    if (
        exact_reduce_only_quantity(plan.instrument, cleanup_quantity.as_decimal())
        != cleanup_quantity
    ):
        raise Stage4DemoExecutionError("cleanup quantity is not exact")
    return _build_exec_tester_runtime_unchecked(plan, phase)


def _build_exec_tester_runtime_unchecked(
    plan: Stage4DemoExecAttemptPlan,
    phase: Stage4DemoExecPhasePlan,
) -> _ExecTesterRuntime:
    """Construct a previously authorized official-tester phase node."""
    node = (
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
    account_query = _AccountQueryProbe()
    node.add_strategy(account_query)
    node.add_builtin_actor(_DATA_TESTER_BUILTIN, plan.observer_config)
    node.add_builtin_strategy(plan.builtin_strategy, phase.tester_config)
    return _ExecTesterRuntime(node=node, account_query=account_query)


def _exec_tester_only_risk_config() -> LiveRiskEngineConfig:
    """Return the non-exported bypass used only by the official ExecTester node."""
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
        close_positions_on_stop=True,
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
) -> ExecTesterConfig:
    return ExecTesterConfig(
        strategy_id=StrategyId.from_str("STAGE4-PASSIVE-001"),
        instrument_id=instrument_id,
        client_id=client_id,
        enable_limit_buys=False,
        enable_limit_sells=False,
        enable_stop_buys=False,
        enable_stop_sells=False,
        cancel_orders_on_stop=True,
        close_positions_on_stop=True,
        close_positions_qty_precision=None,
        reduce_only_on_stop=True,
        dry_run=False,
        log_data=False,
        log_events=False,
        log_commands=False,
    )


def _validate_market_input(value: Stage4DemoExecMarketInput) -> None:
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
    for name, timestamp in (
        ("quote", value.quote_ts_event_ns),
        ("mark", value.mark_ts_event_ns),
    ):
        if timestamp > value.observed_at_ns + _FUTURE_TOLERANCE_NS:
            raise Stage4DemoExecutionError(f"{name} timestamp is in the future")
        if value.observed_at_ns - timestamp > _MAX_PRICE_AGE_NS:
            raise Stage4DemoExecutionError(f"{name} input is stale")


def _allowed_initial_margin(
    plan: Stage4DemoExecAttemptPlan,
    observation: Stage4DemoAccountModeObservation,
) -> tuple[Decimal, Decimal] | None:
    minimum = observation.mark_price_min
    maximum = observation.mark_price_max
    if minimum is None or maximum is None or minimum <= 0 or maximum < minimum:
        return None
    if (maximum - minimum) / minimum > Decimal(_MAX_MARK_SPREAD_TEXT):
        return None
    quantity = plan.canary_quantity.as_decimal()
    currency_quantum = Decimal(1).scaleb(-plan.instrument.quote_currency.precision)
    allowance = (
        quantity * plan.instrument.price_increment.as_decimal() + currency_quantum
    )
    return (
        max(Decimal(0), quantity * minimum - allowance),
        quantity * maximum + allowance,
    )


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
