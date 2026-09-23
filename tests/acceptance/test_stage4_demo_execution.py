from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from nautilus_trader.adapters.binance import BinanceEnvironment
from nautilus_trader.common import Cache, OrderFactory
from nautilus_trader.core import UUID4
from nautilus_trader.model import (
    AccountBalance,
    AccountId,
    AccountState,
    AccountType,
    ClientId,
    CryptoPerpetual,
    Currency,
    InstrumentId,
    MarginAccount,
    Money,
    OrderSide,
    PositionId,
    Price,
    Quantity,
    StrategyId,
    Symbol,
)

from tracequant.integrations.nautilus import stage4_demo, stage4_demo_execution
from tracequant.integrations.nautilus.stage4_demo import (
    DEMO_INSTRUMENT_ID,
    NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
    NAUTILUS_DISTRIBUTION,
    NAUTILUS_UPSTREAM_COMMIT,
    NAUTILUS_VERSION,
    OPERATOR_CONFIRMATION_TOKEN,
    AdmittedDemoAttempt,
    DeadlinePhase,
    DemoAdmissionBatch,
    DemoConfig,
    RuntimeIdentity,
    freeze_demo_config,
)
from tracequant.integrations.nautilus.stage4_demo_evidence import (
    DemoEvidenceScenario,
    canonical_stage4_demo_evidence_json,
    validate_stage4_demo_evidence,
)
from tracequant.integrations.nautilus.stage4_demo_execution import (
    EXEC_EVIDENCE_FILENAME,
    EXEC_TESTER_BUILTIN,
    PASSIVE_OFFSET_TICKS,
    Stage4DemoAccountModeObservation,
    Stage4DemoCleanupAuthorization,
    Stage4DemoExecAttemptInput,
    Stage4DemoExecAttemptPlan,
    Stage4DemoExecMarketInput,
    Stage4DemoExecObservation,
    Stage4DemoExecutionError,
    build_stage4_demo_exec_evidence,
    build_stage4_demo_exec_plan,
    prove_exact_cleanup_quantity,
    run_stage4_demo_exec,
    write_stage4_demo_exec_evidence,
)


def _runtime() -> RuntimeIdentity:
    return RuntimeIdentity(
        source_commit="1" * 40,
        dependency_lock_sha256="2" * 64,
        distribution=NAUTILUS_DISTRIBUTION,
        version=NAUTILUS_VERSION,
        upstream_commit=NAUTILUS_UPSTREAM_COMMIT,
        wheel_sha256=NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
        python_implementation="CPython",
        python_major=3,
        python_minor=13,
        operating_system="Linux",
        machine="x86_64",
    )


def _instrument() -> CryptoPerpetual:
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(DEMO_INSTRUMENT_ID),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        min_quantity=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("10.000"),
        min_notional=Money.from_str("5.00 USDT"),
        ts_event=0,
        ts_init=0,
    )


def _market_input(offset_ns: int = 0) -> Stage4DemoExecMarketInput:
    observed = time.time_ns() + offset_ns
    return Stage4DemoExecMarketInput(
        instrument=_instrument(),
        best_bid=Price.from_str("50000.00"),
        best_ask=Price.from_str("50000.01"),
        mark_price=Price.from_str("50000.00"),
        quote_ts_event_ns=observed - 100_000_000,
        mark_ts_event_ns=observed - 100_000_000,
        observed_at_ns=observed,
    )


@pytest.fixture(autouse=True)
def _offline_public_market_input(monkeypatch: pytest.MonkeyPatch) -> None:
    observations = 0

    async def acquire(
        _deadline: object,
    ) -> Stage4DemoExecMarketInput:
        nonlocal observations
        observations += 1
        return _market_input(observations * 1_000_000)

    monkeypatch.setattr(
        stage4_demo_execution,
        "_acquire_public_market_input",
        acquire,
    )


@dataclass(frozen=True)
class _AdmittedPlan:
    market_close: Stage4DemoExecAttemptPlan
    passive_cancel: Stage4DemoExecAttemptPlan

    @property
    def attempts(self) -> tuple[Stage4DemoExecAttemptPlan, ...]:
        return (self.market_close, self.passive_cancel)


def _plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[_AdmittedPlan, list[str]]:
    runtime = _runtime()
    frozen = freeze_demo_config(DemoConfig())
    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    batch = cast(
        DemoAdmissionBatch,
        SimpleNamespace(repository_root=repository),
    )
    tokens: list[str] = []

    def admit(*, batch: DemoAdmissionBatch, operator_token: str) -> AdmittedDemoAttempt:
        del batch
        tokens.append(operator_token)
        return cast(
            AdmittedDemoAttempt,
            SimpleNamespace(runtime=runtime, frozen_config=frozen),
        )

    monkeypatch.setattr(stage4_demo_execution, "admit_current_demo_attempt", admit)
    market_input = _market_input()
    passive_input = _market_input(1_000_000_000)
    deferred = build_stage4_demo_exec_plan(
        batch=batch,
        evidence_root=tmp_path / "external-evidence",
        batch_id="batch_abcdefghijklmnopqrstuv",
        acquire_market_close=lambda: Stage4DemoExecAttemptInput(
            market_input,
            OPERATOR_CONFIRMATION_TOKEN,
        ),
        acquire_passive_cancel=lambda: Stage4DemoExecAttemptInput(
            passive_input,
            OPERATOR_CONFIRMATION_TOKEN,
        ),
    )
    market = stage4_demo_execution._admit_deferred_attempt(
        deferred,
        scenario=DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE,
        acquire=deferred.acquire_market_close,
    )
    passive = stage4_demo_execution._admit_deferred_attempt(
        deferred,
        scenario=DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL,
        acquire=deferred.acquire_passive_cancel,
        previous_market_input=market.market_input,
    )
    return _AdmittedPlan(market, passive), tokens


def _observation(
    scenario: DemoEvidenceScenario,
    *,
    ambiguous: bool = False,
    open_position_count: int = 0,
    final_net_quantity: Decimal = Decimal(0),
) -> Stage4DemoExecObservation:
    return Stage4DemoExecObservation(
        scenario=scenario,
        instrument=_instrument(),
        started_at_ns=1_800_000_000_000_000_000,
        ended_at_ns=1_800_000_010_000_000_000,
        quote_count=2,
        trade_count=1,
        market_timestamps_valid=True,
        order_submitted=True,
        order_accepted=not ambiguous,
        order_terminal=not ambiguous,
        active_order_count=0,
        pending_order_count=0,
        order_ambiguous=ambiguous,
        fill_complete=scenario is DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE,
        fill_partial=False,
        fill_late=False,
        open_position_count=open_position_count,
        final_net_quantity=final_net_quantity,
        balance_before_observed=True,
        balance_after_observed=True,
        balance_change_explained=True,
        order_action_consistent=True,
        account_mode=Stage4DemoAccountModeObservation(
            operator_gate_confirmed=True,
            canary_complete=True,
            net_position_shape_consistent=True,
            account_scope_consistent=True,
            observed_initial_margin=Decimal("50.00"),
            mark_price_min=Decimal("49999.00"),
            mark_price_max=Decimal("50001.00"),
        ),
        unresolved_unknown_count=1 if ambiguous else 0,
    )


def _cached_order(
    *,
    side: str,
    order_type: str,
    quantity: str,
    filled: str,
    reduce_only: bool,
    status: str,
    price: str | None = None,
    post_only: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        side=side,
        order_type=order_type,
        quantity=Quantity.from_str(quantity),
        filled_qty=Quantity.from_str(filled),
        is_reduce_only=reduce_only,
        is_post_only=post_only,
        status=status,
        price=Price.from_str(price) if price is not None else None,
        ts_submitted=1,
        ts_accepted=1,
        is_closed=status in {"FILLED", "CANCELED", "EXPIRED"},
    )


def _changed_namespace(value: SimpleNamespace, **changes: object) -> SimpleNamespace:
    return SimpleNamespace(**{**vars(value), **changes})


def test_stage4_demo_exec_plan_is_bounded_and_cannot_target_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, tokens = _plan(monkeypatch, tmp_path)

    assert tokens == [OPERATOR_CONFIRMATION_TOKEN, OPERATOR_CONFIRMATION_TOKEN]
    assert plan.market_close._admission is not plan.passive_cancel._admission
    assert (
        plan.market_close.evidence_partition != plan.passive_cancel.evidence_partition
    )
    assert [attempt.scenario for attempt in plan.attempts] == [
        DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE,
        DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL,
    ]
    assert all(
        attempt.builtin_strategy == EXEC_TESTER_BUILTIN for attempt in plan.attempts
    )
    assert all(
        attempt.data_client_config.environment == BinanceEnvironment.DEMO
        and attempt.data_client_config.base_url_http is None
        and attempt.data_client_config.base_url_ws is None
        for attempt in plan.attempts
    )
    assert all(
        attempt.observer_config.subscribe_quotes is True
        and attempt.observer_config.subscribe_trades is True
        and attempt.observer_config.subscribe_mark_prices is True
        and attempt.observer_config.subscribe_instrument is True
        for attempt in plan.attempts
    )
    assert all(
        dict(attempt.deadline_seconds)[DeadlinePhase.PRICE_READINESS] == 10
        for attempt in plan.attempts
    )

    market, market_close = (phase.tester_config for phase in plan.market_close.phases)
    assert [phase.run_condition for phase in plan.market_close.phases] == [
        "always",
        "cleanup_with_proof",
    ]
    assert market.open_position_on_start_qty == Decimal("0.001")
    assert market.open_position_on_first_quote is True
    assert market.enable_limit_buys is False
    assert market.enable_limit_sells is False
    assert market.enable_stop_buys is False
    assert market.enable_stop_sells is False
    assert market.close_positions_on_stop is False
    assert market_close.open_position_on_start_qty is None
    assert market_close.close_positions_on_stop is False
    assert market_close.reduce_only_on_stop is True
    assert market_close.close_positions_qty_precision is None
    assert market_close.strategy_id == market.strategy_id

    canary, canary_close, passive, cleanup = (
        phase.tester_config for phase in plan.passive_cancel.phases
    )
    assert [phase.run_condition for phase in plan.passive_cancel.phases] == [
        "always",
        "cleanup_with_proof",
        "always",
        "failure_with_cleanup_proof",
    ]
    assert canary.open_position_on_start_qty == Decimal("0.001")
    assert canary.close_positions_on_stop is False
    assert canary_close.close_positions_on_stop is False
    assert canary_close.strategy_id == canary.strategy_id
    assert passive.open_position_on_start_qty is None
    assert passive.enable_limit_buys is True
    assert passive.enable_limit_sells is False
    assert passive.enable_stop_buys is False
    assert passive.enable_stop_sells is False
    assert passive.use_post_only is True
    assert passive.limit_aggressive is False
    assert passive.tob_offset_ticks == PASSIVE_OFFSET_TICKS == 1
    assert passive.order_expire_time_delta_mins == 1
    assert passive.cancel_orders_on_stop is True
    assert passive.use_individual_cancels_on_stop is True
    assert passive.close_positions_on_stop is False
    assert cleanup.open_position_on_start_qty is None
    assert cleanup.enable_limit_buys is False
    assert cleanup.enable_limit_sells is False
    assert cleanup.close_positions_on_stop is False
    assert cleanup.reduce_only_on_stop is True
    assert cleanup.strategy_id == passive.strategy_id
    with pytest.raises(Stage4DemoExecutionError, match="requires terminal"):
        stage4_demo_execution._build_exec_tester_node(
            plan.passive_cancel,
            plan.passive_cancel.phases[-1],
        )

    assert plan.passive_cancel.passive_price == Price.from_str("49999.99")
    assert plan.passive_cancel.passive_quantity == Quantity.from_str("0.001")
    assert plan.market_close.market_input is not plan.passive_cancel.market_input
    assert stage4_demo_execution._exec_tester_only_risk_config().bypass is True
    assert "_exec_tester_only_risk_config" not in stage4_demo_execution.__all__
    assert "_build_exec_tester_node" not in stage4_demo_execution.__all__
    strategy_root = Path("src/tracequant/integrations/nautilus/strategies")
    assert all(
        "stage4_demo_execution" not in path.read_text(encoding="utf-8")
        for path in strategy_root.glob("*.py")
    )
    assert "api_key" not in repr(plan).lower()
    assert "api_secret" not in repr(plan).lower()


def test_stage4_demo_exec_plan_defers_attempt_inputs_and_admission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    batch = cast(
        DemoAdmissionBatch,
        SimpleNamespace(repository_root=tmp_path / "repository"),
    )

    def acquire(name: str) -> Stage4DemoExecAttemptInput:
        events.append(name)
        return Stage4DemoExecAttemptInput(
            _market_input(len(events) * 1_000_000),
            OPERATOR_CONFIRMATION_TOKEN,
        )

    def admit(*, batch: DemoAdmissionBatch, operator_token: str) -> AdmittedDemoAttempt:
        del batch, operator_token
        events.append("admit")
        return cast(
            AdmittedDemoAttempt,
            SimpleNamespace(
                runtime=_runtime(),
                frozen_config=freeze_demo_config(DemoConfig()),
            ),
        )

    monkeypatch.setattr(stage4_demo_execution, "admit_current_demo_attempt", admit)
    plan = build_stage4_demo_exec_plan(
        batch=batch,
        evidence_root=tmp_path / "evidence",
        batch_id="batch_abcdefghijklmnopqrstuv",
        acquire_market_close=lambda: acquire("market"),
        acquire_passive_cancel=lambda: acquire("passive"),
    )

    assert events == []
    assert "operator" not in repr(plan).lower()
    stage4_demo_execution._admit_deferred_attempt(
        plan,
        scenario=DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE,
        acquire=plan.acquire_market_close,
    )
    assert events == ["market", "admit"]


@pytest.mark.parametrize(
    "scenario",
    [
        DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE,
        DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL,
    ],
)
def test_stage4_demo_exec_attempts_adapt_independent_nautilus_owned_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: DemoEvidenceScenario,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = (
        plan.market_close
        if scenario is DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE
        else plan.passive_cancel
    )

    evidence = build_stage4_demo_exec_evidence(attempt, _observation(scenario))
    validate_stage4_demo_evidence(evidence)

    assert evidence["result"] == "PASS"
    assert evidence["terminal_state"] == "COMPLETE"
    assert evidence["scenario"] == scenario.value
    observations = cast(dict[str, object], evidence["observations"])
    account_mode = cast(dict[str, object], observations["account_mode"])
    assert account_mode["operator_gate_confirmed"] is True
    assert account_mode["config_requested"] is True
    assert account_mode["canary_complete"] is True
    assert account_mode["one_way_confirmed"] is False
    assert account_mode["isolated_confirmed"] is False
    assert account_mode["leverage_one_confirmed"] is False
    fill = cast(dict[str, object], observations["fill"])
    assert fill["complete"] is (
        scenario is DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE
    )
    assert evidence["cleanup"] == {
        "classification": "consistent",
        "active_order_count": 0,
        "pending_order_count": 0,
        "open_position_count": 0,
        "unresolved_unknown_count": 0,
        "final_net_quantity": "0",
    }


def test_stage4_demo_exec_missing_canary_order_keeps_unknown_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    cache = cast(
        Cache,
        SimpleNamespace(
            orders=lambda **kwargs: [],
            orders_open_count=lambda **kwargs: 0,
            orders_inflight_count=lambda **kwargs: 0,
            positions_open=lambda **kwargs: [],
            positions=lambda **kwargs: [],
            account=lambda *args: None,
            quote_count=lambda *args: 0,
            trade_count=lambda *args: 0,
            quote=lambda *args: None,
            mark_price=lambda *args: None,
        ),
    )
    failure = stage4_demo_execution.Stage4DemoExecFailure(
        "ORDER_TIMEOUT", "execution", ("ORDER_UNKNOWN",)
    )
    snapshot = stage4_demo_execution._capture_phase_snapshot(
        cache,
        attempt,
        phase=attempt.phases[0],
        phase_kind="canary",
        started_at_ns=1,
        ended_at_ns=2,
        account_before_count=0,
        account_before_balance=None,
        account_mode=None,
        market_sequence=stage4_demo_execution._MarketTimestampSequence(0, 0),
        failure=failure,
        order_submission_unknown=True,
    )
    evidence = build_stage4_demo_exec_evidence(
        attempt,
        stage4_demo_execution._logical_observation(
            attempt, canary=snapshot, execution=snapshot
        ),
    )
    validate_stage4_demo_evidence(evidence)
    assert snapshot.unresolved_unknown_count == 1
    assert evidence["result"] == "FAIL"
    assert evidence["terminal_state"] == "HALTED"
    cleanup = cast(dict[str, object], evidence["cleanup"])
    assert cleanup["classification"] == "cleanup_incomplete"
    assert cleanup["unresolved_unknown_count"] == 1


@pytest.mark.parametrize("raise_on_stop", [False, True])
def test_stage4_demo_exec_failed_canary_stop_cannot_auto_close_outstanding_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raise_on_stop: bool,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    phase = attempt.phases[0]
    stopped_with_auto_close: list[bool] = []
    stop_event = asyncio.Event()

    async def run_async() -> None:
        await stop_event.wait()

    def stop() -> None:
        stopped_with_auto_close.append(phase.tester_config.close_positions_on_stop)
        stop_event.set()
        if raise_on_stop:
            raise RuntimeError("stop failed")

    node = SimpleNamespace(
        cache=SimpleNamespace(
            account=lambda *args: None,
            orders=lambda **kwargs: [],
        ),
        handle=lambda: SimpleNamespace(stop=stop),
        run_async=run_async,
        dispose=lambda: None,
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_build_exec_tester_runtime_unchecked",
        lambda *args: SimpleNamespace(node=node),
    )

    async def timeout(*args: object, **kwargs: object) -> None:
        raise stage4_demo_execution._Stage4DemoExecutionRuntimeError(
            stage4_demo_execution.Stage4DemoExecFailure(
                "ORDER_TIMEOUT", "execution", ("ORDER_UNKNOWN",)
            )
        )

    monkeypatch.setattr(stage4_demo_execution, "_wait_until", timeout)
    outstanding = replace(
        stage4_demo_execution._failed_phase_snapshot(
            started_at_ns=1,
            failure=stage4_demo_execution.Stage4DemoExecFailure(
                "ORDER_TIMEOUT", "execution", ("ORDER_UNKNOWN",)
            ),
        ),
        active_order_count=1,
        inflight_order_count=1,
        open_position_count=1,
        final_net_quantity=Decimal("0.001"),
    )
    captured_stop_state: list[bool] = []

    def capture(
        *args: object, **kwargs: object
    ) -> stage4_demo_execution._ExecPhaseSnapshot:
        captured_stop_state.append(bool(kwargs["stop_unconfirmed"]))
        return outstanding

    monkeypatch.setattr(stage4_demo_execution, "_capture_phase_snapshot", capture)

    result = asyncio.run(
        stage4_demo_execution._run_exec_phase(attempt, phase, phase_kind="canary")
    )
    assert stopped_with_auto_close == [False]
    assert captured_stop_state == [raise_on_stop]
    assert result.active_order_count == 1
    assert result.unresolved_unknown_count == 1


@pytest.mark.parametrize("stop_behavior", ["normal", "unconfirmed", "raises"])
def test_stage4_demo_exec_filled_canary_stops_with_position_for_exact_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_behavior: str,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    phase = attempt.phases[0]
    strategy_id = stage4_demo_execution._require_phase_strategy_id(phase)
    stopped = asyncio.Event()
    disposed: list[bool] = []
    account_events = 1
    order = _cached_order(
        side="BUY",
        order_type="MARKET",
        quantity="0.001",
        filled="0.001",
        reduce_only=False,
        status="FILLED",
    )
    position = SimpleNamespace(
        instrument_id=attempt.instrument.id,
        strategy_id=strategy_id,
        quantity=Quantity.from_str("0.001"),
        is_long=True,
        is_short=False,
    )
    cache = SimpleNamespace(
        instrument=lambda instrument_id: attempt.instrument,
        quote=lambda instrument_id: SimpleNamespace(
            instrument_id=attempt.instrument.id,
            bid_price=attempt.market_input.best_bid,
            ask_price=attempt.market_input.best_ask,
            ts_event=attempt.market_input.quote_ts_event_ns,
        ),
        mark_price=lambda instrument_id: SimpleNamespace(
            instrument_id=attempt.instrument.id,
            value=attempt.market_input.mark_price,
            ts_event=attempt.market_input.mark_ts_event_ns,
        ),
        quote_count=lambda instrument_id: 1,
        trade_count=lambda instrument_id: 1,
        orders=lambda **kwargs: [order],
        orders_open_count=lambda **kwargs: 0,
        orders_inflight_count=lambda **kwargs: 0,
        positions_open=lambda **kwargs: [position],
        positions=lambda **kwargs: [position],
        account=lambda *args: None,
    )

    async def run_async() -> None:
        await stopped.wait()

    stop_calls = 0

    def stop() -> None:
        nonlocal stop_calls
        stop_calls += 1
        stopped.set()
        if stop_behavior == "raises":
            raise RuntimeError("stop failed")

    node = SimpleNamespace(
        cache=cache,
        handle=lambda: SimpleNamespace(is_running=True, stop=stop),
        run_async=run_async,
        dispose=lambda: disposed.append(True),
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_build_exec_tester_runtime_unchecked",
        lambda *args: SimpleNamespace(node=node),
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_account_event_count",
        lambda cache: account_events,
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_capture_account_mode",
        lambda *args: _observation(attempt.scenario).account_mode,
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_pre_order_account_observation",
        lambda *args, **kwargs: (1, Decimal("100")),
    )

    if stop_behavior == "raises":

        def no_cleanup(*args: object, **kwargs: object) -> None:
            pytest.fail("a new cleanup node started after stop raised")

        monkeypatch.setattr(
            stage4_demo_execution, "_build_authorized_cleanup_runtime", no_cleanup
        )
        outcome = stage4_demo_execution._run_and_persist_attempt(attempt)
        assert stop_calls == 1
        assert stopped.is_set()
        assert not disposed
        assert outcome.evidence["terminal_state"] == "HALTED"
        failure = cast(dict[str, object], outcome.evidence["failure"])
        cleanup = cast(dict[str, object], outcome.evidence["cleanup"])
        assert failure["code"] == "CLEANUP_FAILED"
        assert cleanup["unresolved_unknown_count"] == 1
        assert json.loads(outcome.evidence_path.read_text()) == outcome.evidence
        return

    if stop_behavior == "unconfirmed":

        async def failed_stop(
            *args: object, **kwargs: object
        ) -> stage4_demo_execution.Stage4DemoExecFailure:
            return stage4_demo_execution.Stage4DemoExecFailure(
                "CLEANUP_FAILED", "cleanup", ("FAILED",)
            )

        def no_cleanup(*args: object, **kwargs: object) -> None:
            pytest.fail("a new cleanup node started after unconfirmed canary stop")

        monkeypatch.setattr(stage4_demo_execution, "_stop_exec_tester", failed_stop)
        monkeypatch.setattr(
            stage4_demo_execution, "_build_authorized_cleanup_runtime", no_cleanup
        )
        observation = asyncio.run(
            stage4_demo_execution._observe_stage4_demo_attempt(attempt)
        )
        evidence = build_stage4_demo_exec_evidence(attempt, observation)
        assert stopped.is_set()
        assert not disposed
        assert observation.order_terminal
        assert observation.active_order_count == 0
        assert cache.orders_inflight_count(instrument_id=attempt.instrument.id) == 0
        assert observation.open_position_count == 1
        assert observation.unresolved_unknown_count == 1
        assert observation.failure == stage4_demo_execution.Stage4DemoExecFailure(
            "CLEANUP_FAILED", "cleanup", ("FAILED",)
        )
        assert evidence["terminal_state"] == "HALTED"
        return

    snapshot = asyncio.run(
        stage4_demo_execution._run_exec_phase(attempt, phase, phase_kind="canary")
    )
    assert stopped.is_set()
    assert disposed == [True]
    assert phase.tester_config.close_positions_on_stop is False
    assert snapshot.failure is None
    assert snapshot.order_terminal and snapshot.fill_complete
    assert snapshot.active_order_count == snapshot.inflight_order_count == 0
    assert snapshot.open_position_count == 1
    assert snapshot.final_net_quantity == Decimal("0.001")
    assert snapshot.unresolved_unknown_count == 0
    assert prove_exact_cleanup_quantity(
        attempt,
        original_order_terminal=snapshot.order_terminal,
        active_order_count=snapshot.active_order_count,
        inflight_order_count=snapshot.inflight_order_count,
        open_position_count=snapshot.open_position_count,
        net_position=snapshot.final_net_quantity,
    ) == Stage4DemoCleanupAuthorization(
        signed_position=Decimal("0.001"),
        quantity=Quantity.from_str("0.001"),
    )


def test_stage4_demo_exec_canary_close_receives_exact_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    account_mode = _observation(attempt.scenario).account_mode
    opening = replace(
        stage4_demo_execution._failed_phase_snapshot(
            started_at_ns=1,
            failure=stage4_demo_execution.Stage4DemoExecFailure(
                "CLEANUP_INCOMPLETE", "cleanup", ("FAILED",)
            ),
        ),
        quote_count=1,
        market_timestamps_valid=True,
        order_submitted=True,
        order_accepted=True,
        order_terminal=True,
        fill_complete=True,
        open_position_count=1,
        final_net_quantity=Decimal("0.001"),
        balance_before_observed=True,
        balance_after_observed=True,
        order_action_consistent=True,
        account_mode=account_mode,
        unresolved_unknown_count=0,
        failure=None,
        balance_before_total=Decimal("100"),
    )
    closing = replace(
        opening,
        order_submitted=True,
        order_accepted=True,
        order_terminal=True,
        open_position_count=0,
        final_net_quantity=Decimal(0),
        balance_change_explained=True,
        account_mode=None,
    )
    phases: list[str] = []

    async def run_phase(
        _plan: Stage4DemoExecAttemptPlan,
        _phase: object,
        *,
        phase_kind: str,
        cleanup_authorization: Stage4DemoCleanupAuthorization | None = None,
        balance_baseline: Decimal | None = None,
    ) -> stage4_demo_execution._ExecPhaseSnapshot:
        phases.append(phase_kind)
        if phase_kind == "canary":
            assert cleanup_authorization is None
            return opening
        assert cleanup_authorization == Stage4DemoCleanupAuthorization(
            signed_position=Decimal("0.001"),
            quantity=Quantity.from_str("0.001"),
        )
        assert balance_baseline == Decimal("100")
        return closing

    monkeypatch.setattr(stage4_demo_execution, "_run_exec_phase", run_phase)
    observation = asyncio.run(
        stage4_demo_execution._observe_stage4_demo_attempt(attempt)
    )
    evidence = build_stage4_demo_exec_evidence(attempt, observation)
    validate_stage4_demo_evidence(evidence)
    assert phases == ["canary", "cleanup"]
    assert evidence["result"] == "PASS"


@pytest.mark.parametrize("residual", ["0.0005", "0.0035", "11.000"])
def test_stage4_demo_exec_unrepresentable_canary_residual_persists_halted_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    residual: str,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    opening = replace(
        stage4_demo_execution._failed_phase_snapshot(
            started_at_ns=1,
            failure=stage4_demo_execution.Stage4DemoExecFailure(
                "CLEANUP_INCOMPLETE", "cleanup", ("OPEN_POSITION_REMAINS",)
            ),
        ),
        order_submitted=True,
        order_accepted=True,
        order_terminal=True,
        fill_partial=True,
        open_position_count=1,
        final_net_quantity=Decimal(residual),
        unresolved_unknown_count=0,
    )
    phases: list[str] = []

    async def run_phase(
        _plan: Stage4DemoExecAttemptPlan,
        _phase: object,
        *,
        phase_kind: str,
        cleanup_authorization: object = None,
        balance_baseline: Decimal | None = None,
    ) -> stage4_demo_execution._ExecPhaseSnapshot:
        del cleanup_authorization, balance_baseline
        phases.append(phase_kind)
        return opening

    monkeypatch.setattr(stage4_demo_execution, "_run_exec_phase", run_phase)
    outcome = stage4_demo_execution._run_and_persist_attempt(attempt)

    assert phases == ["canary"]
    assert outcome.evidence["terminal_state"] == "HALTED"
    cleanup = cast(dict[str, object], outcome.evidence["cleanup"])
    assert cleanup["classification"] == "cleanup_incomplete"
    assert Decimal(str(cleanup["final_net_quantity"])) == Decimal(residual)
    assert json.loads(outcome.evidence_path.read_text()) == outcome.evidence


def test_stage4_demo_exec_unrepresentable_passive_residual_persists_halted_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.passive_cancel
    opening = replace(
        stage4_demo_execution._failed_phase_snapshot(
            started_at_ns=1,
            failure=stage4_demo_execution.Stage4DemoExecFailure(
                "CLEANUP_INCOMPLETE", "cleanup", ("FAILED",)
            ),
        ),
        order_submitted=True,
        order_accepted=True,
        order_terminal=True,
        fill_complete=True,
        open_position_count=1,
        final_net_quantity=Decimal("0.001"),
        account_mode=_observation(attempt.scenario).account_mode,
        unresolved_unknown_count=0,
        failure=None,
    )
    closing = replace(
        opening,
        open_position_count=0,
        final_net_quantity=Decimal(0),
        account_mode=None,
    )
    passive = replace(
        opening,
        fill_complete=False,
        fill_partial=True,
        open_position_count=1,
        final_net_quantity=Decimal("0.0035"),
        account_mode=None,
        failure=stage4_demo_execution.Stage4DemoExecFailure(
            "CANCEL_FILL_RACE", "execution", ("FAILED",)
        ),
    )
    phases: list[str] = []

    async def run_phase(
        _plan: Stage4DemoExecAttemptPlan,
        _phase: object,
        *,
        phase_kind: str,
        cleanup_authorization: Stage4DemoCleanupAuthorization | None = None,
        balance_baseline: Decimal | None = None,
    ) -> stage4_demo_execution._ExecPhaseSnapshot:
        del balance_baseline
        phases.append(phase_kind)
        if phase_kind == "canary":
            return opening
        if phase_kind == "cleanup":
            assert cleanup_authorization is not None
            return closing
        return passive

    monkeypatch.setattr(stage4_demo_execution, "_run_exec_phase", run_phase)
    outcome = stage4_demo_execution._run_and_persist_attempt(attempt)

    assert phases == ["canary", "cleanup", "passive"]
    assert outcome.evidence["terminal_state"] == "HALTED"
    failure = cast(dict[str, object], outcome.evidence["failure"])
    cleanup = cast(dict[str, object], outcome.evidence["cleanup"])
    assert failure["code"] == "CANCEL_FILL_RACE"
    assert cleanup["classification"] == "cleanup_incomplete"
    assert cleanup["final_net_quantity"] == "0.0035"
    assert json.loads(outcome.evidence_path.read_text()) == outcome.evidence


def test_stage4_demo_exec_failed_canary_never_starts_close_without_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    failure = stage4_demo_execution.Stage4DemoExecFailure(
        "ORDER_TIMEOUT", "execution", ("ORDER_UNKNOWN",)
    )
    outstanding = replace(
        stage4_demo_execution._failed_phase_snapshot(started_at_ns=1, failure=failure),
        active_order_count=1,
        inflight_order_count=1,
        open_position_count=1,
        final_net_quantity=Decimal("0.001"),
    )
    phases: list[str] = []

    async def run_phase(
        _plan: Stage4DemoExecAttemptPlan,
        _phase: object,
        *,
        phase_kind: str,
        cleanup_authorization: object = None,
        balance_baseline: Decimal | None = None,
    ) -> stage4_demo_execution._ExecPhaseSnapshot:
        del cleanup_authorization, balance_baseline
        phases.append(phase_kind)
        return outstanding

    monkeypatch.setattr(stage4_demo_execution, "_run_exec_phase", run_phase)
    for attempt in plan.attempts:
        phases.clear()
        observation = asyncio.run(
            stage4_demo_execution._observe_stage4_demo_attempt(attempt)
        )
        assert phases == ["canary"]
        assert observation.failure == failure
        assert observation.unresolved_unknown_count == 1


def test_stage4_demo_exec_ambiguous_response_halts_without_retry_or_unsafe_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    observation = _observation(
        DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL,
        ambiguous=True,
        open_position_count=1,
        final_net_quantity=Decimal("0.001"),
    )

    evidence = build_stage4_demo_exec_evidence(plan.passive_cancel, observation)
    validate_stage4_demo_evidence(evidence)

    assert evidence["result"] == "FAIL"
    assert evidence["terminal_state"] == "HALTED"
    assert evidence["failure"] == {
        "code": "ORDER_AMBIGUOUS",
        "phase": "execution",
        "diagnostic_codes": ["ORDER_UNKNOWN"],
    }
    assert (
        prove_exact_cleanup_quantity(
            plan.passive_cancel,
            original_order_terminal=False,
            active_order_count=0,
            inflight_order_count=0,
            open_position_count=1,
            net_position=Decimal("0.001"),
        )
        is None
    )


def test_stage4_demo_exec_cleanup_is_exact_and_only_after_terminal_zero_order_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)

    assert prove_exact_cleanup_quantity(
        plan.passive_cancel,
        original_order_terminal=True,
        active_order_count=0,
        inflight_order_count=0,
        open_position_count=1,
        net_position=Decimal("-0.003"),
    ) == Stage4DemoCleanupAuthorization(
        signed_position=Decimal("-0.003"),
        quantity=Quantity.from_str("0.003"),
    )
    assert (
        prove_exact_cleanup_quantity(
            plan.passive_cancel,
            original_order_terminal=True,
            active_order_count=1,
            inflight_order_count=0,
            open_position_count=1,
            net_position=Decimal("0.003"),
        )
        is None
    )
    with pytest.raises(Exception, match="without rounding"):
        prove_exact_cleanup_quantity(
            plan.passive_cancel,
            original_order_terminal=True,
            active_order_count=0,
            inflight_order_count=0,
            open_position_count=1,
            net_position=Decimal("0.0035"),
        )


def test_stage4_demo_exec_cleanup_binds_current_position_and_actual_close_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.passive_cancel
    authorization = Stage4DemoCleanupAuthorization(
        signed_position=Decimal("0.003"),
        quantity=Quantity.from_str("0.003"),
    )
    strategy_id = stage4_demo_execution._require_phase_strategy_id(attempt.phases[3])
    position = SimpleNamespace(
        instrument_id=attempt.instrument.id,
        strategy_id=strategy_id,
        quantity=Quantity.from_str("0.003"),
        is_long=True,
        is_short=False,
    )
    close_order = _cached_order(
        side="SELL",
        order_type="MARKET",
        quantity="0.003",
        filled="0.003",
        reduce_only=True,
        status="FILLED",
    )
    observed_orders: list[object] = [close_order]
    cache = cast(
        Cache,
        SimpleNamespace(
            positions_open=lambda **kwargs: [position],
            orders_open_count=lambda **kwargs: 0,
            orders_inflight_count=lambda **kwargs: 0,
            orders=lambda **kwargs: observed_orders,
        ),
    )

    assert stage4_demo_execution._cleanup_cache_matches_authorization(
        cache,
        attempt,
        strategy_id,
        authorization,
    )
    assert stage4_demo_execution._cleanup_action_matches(
        cache,
        attempt,
        strategy_id,
        authorization,
    )
    position.quantity = Quantity.from_str("0.004")
    assert not stage4_demo_execution._cleanup_cache_matches_authorization(
        cache,
        attempt,
        strategy_id,
        authorization,
    )
    position.quantity = Quantity.from_str("0.003")
    position.strategy_id = StrategyId.from_str("EXTERNAL")
    assert not stage4_demo_execution._cleanup_cache_matches_authorization(
        cache,
        attempt,
        strategy_id,
        authorization,
    )
    position.strategy_id = strategy_id

    # Reconciliation may retain the terminal entry order under the reused ID.
    entry_order = SimpleNamespace(is_reduce_only=False, is_closed=True)
    observed_orders[:] = [entry_order, close_order]
    assert stage4_demo_execution._cleanup_action_matches(
        cache, attempt, strategy_id, authorization
    )
    observed_orders[:] = [entry_order, close_order, close_order]
    assert not stage4_demo_execution._cleanup_action_matches(
        cache, attempt, strategy_id, authorization
    )
    for changes in (
        {"order_type": "LIMIT"},
        {"status": "CANCELED"},
        {"status": "REJECTED"},
        {"filled_qty": Quantity.from_str("0.000")},
        {"filled_qty": Quantity.from_str("0.002")},
        {"quantity": Quantity.from_str("0.002")},
    ):
        observed_orders[:] = [entry_order, _changed_namespace(close_order, **changes)]
        assert not stage4_demo_execution._cleanup_action_matches(
            cache, attempt, strategy_id, authorization
        )


def test_stage4_demo_exec_cleanup_node_has_no_stop_close_tester(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    authorization = Stage4DemoCleanupAuthorization(
        signed_position=Decimal("0.001"),
        quantity=Quantity.from_str("0.001"),
    )
    strategies: list[object] = []
    builtins: list[str] = []
    node = SimpleNamespace(
        add_strategy=strategies.append,
        add_builtin_actor=lambda name, config: None,
        add_builtin_strategy=lambda name, config: builtins.append(name),
    )
    monkeypatch.setattr(stage4_demo_execution, "_build_exec_node", lambda plan: node)

    runtime = stage4_demo_execution._build_authorized_cleanup_runtime(
        attempt,
        attempt.phases[1],
        cleanup_authorization=authorization,
    )

    assert cast(object, runtime.node) is node
    assert strategies == [runtime.cleanup_strategy]
    assert runtime.cleanup_strategy is not None
    assert runtime.cleanup_strategy.strategy_id == (
        stage4_demo_execution._require_phase_strategy_id(attempt.phases[0])
    )
    cleanup_config = runtime.cleanup_strategy.config
    assert cleanup_config is not None
    assert cleanup_config.manage_stop is False
    assert cleanup_config.external_order_claims == [attempt.instrument.id]
    assert attempt.phases[0].tester_config.external_order_claims is None
    assert builtins == []


def test_stage4_demo_exec_private_close_is_one_shot_and_checks_current_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    strategy_id = stage4_demo_execution._require_phase_strategy_id(attempt.phases[1])
    authorization = Stage4DemoCleanupAuthorization(
        signed_position=Decimal("0.001"),
        quantity=Quantity.from_str("0.001"),
    )
    position = SimpleNamespace(
        id="POSITION-001",
        instrument_id=attempt.instrument.id,
        strategy_id=strategy_id,
        quantity=Quantity.from_str("0.001"),
        is_long=True,
        is_short=False,
    )
    active_orders = 0
    cache = cast(
        Cache,
        SimpleNamespace(
            positions_open=lambda **kwargs: [position],
            orders_open_count=lambda **kwargs: active_orders,
            orders_inflight_count=lambda **kwargs: 0,
            orders=lambda **kwargs: [],
        ),
    )
    created: list[dict[str, object]] = []
    submitted: list[tuple[object, object, object]] = []
    close_order = object()

    def market(**kwargs: object) -> object:
        created.append(kwargs)
        return close_order

    class RecordingCleanup(stage4_demo_execution._ExactCleanupStrategy):
        @property
        def order_factory(self) -> OrderFactory:
            return cast(OrderFactory, SimpleNamespace(market=market))

        def submit_order(
            self,
            order: Any,
            position_id: PositionId | None = None,
            client_id: ClientId | None = None,
            params: dict[Any, Any] | None = None,
        ) -> None:
            submitted.append((order, position_id, client_id))

    strategy = RecordingCleanup(attempt, strategy_id, authorization)
    cleanup_config = strategy.config
    assert cleanup_config is not None
    assert cleanup_config.manage_stop is False
    active_orders = 1
    with pytest.raises(stage4_demo_execution._Stage4DemoExecutionRuntimeError):
        strategy.close_once(cache)
    strategy.on_stop()
    assert created == []
    assert submitted == []
    with pytest.raises(Stage4DemoExecutionError, match="already requested"):
        strategy.close_once(cache)

    active_orders = 0
    position.quantity = Quantity.from_str("0.002")
    changed = RecordingCleanup(attempt, strategy_id, authorization)
    with pytest.raises(stage4_demo_execution._Stage4DemoExecutionRuntimeError):
        changed.close_once(cache)
    changed.on_stop()
    assert created == []
    assert submitted == []

    position.quantity = Quantity.from_str("0.001")
    approved = RecordingCleanup(attempt, strategy_id, authorization)
    approved.close_once(cache)
    assert created == [
        {
            "instrument_id": attempt.instrument.id,
            "order_side": OrderSide.SELL,
            "quantity": authorization.quantity,
            "reduce_only": True,
        }
    ]
    assert len(submitted) == 1
    assert submitted[0][0] is close_order
    assert submitted[0][1] == position.id
    assert str(submitted[0][2]) == stage4_demo_execution.EXEC_CLIENT_NAME
    with pytest.raises(Stage4DemoExecutionError, match="already requested"):
        approved.close_once(cache)
    approved.on_stop()
    assert len(submitted) == 1


def test_stage4_demo_exec_failed_cleanup_recheck_stops_without_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    phase = attempt.phases[1]
    strategy_id = stage4_demo_execution._require_phase_strategy_id(phase)
    authorization = Stage4DemoCleanupAuthorization(
        signed_position=Decimal("0.001"),
        quantity=Quantity.from_str("0.001"),
    )
    position = SimpleNamespace(
        id="POSITION-001",
        instrument_id=attempt.instrument.id,
        strategy_id=strategy_id,
        quantity=Quantity.from_str("0.002"),
        is_long=True,
        is_short=False,
    )
    submitted: list[object] = []

    class RecordingCleanup(stage4_demo_execution._ExactCleanupStrategy):
        @property
        def order_factory(self) -> OrderFactory:
            return cast(OrderFactory, SimpleNamespace(market=lambda **kwargs: object()))

        def submit_order(
            self,
            order: Any,
            position_id: PositionId | None = None,
            client_id: ClientId | None = None,
            params: dict[Any, Any] | None = None,
        ) -> None:
            submitted.append(order)

    strategy = RecordingCleanup(attempt, strategy_id, authorization)
    cache = SimpleNamespace(
        positions_open=lambda **kwargs: [position],
        positions=lambda **kwargs: [position],
        orders_open_count=lambda **kwargs: 0,
        orders_inflight_count=lambda **kwargs: 0,
        orders=lambda **kwargs: [],
        quote_count=lambda *args: 1,
        trade_count=lambda *args: 0,
        account=lambda *args: None,
    )
    stopped = asyncio.Event()

    async def run_async() -> None:
        await stopped.wait()

    node = SimpleNamespace(
        cache=cache,
        handle=lambda: SimpleNamespace(is_running=True, stop=stopped.set),
        run_async=run_async,
        dispose=lambda: None,
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_build_authorized_cleanup_runtime",
        lambda *args, **kwargs: SimpleNamespace(
            node=node, account_query=None, cleanup_strategy=strategy
        ),
    )
    monkeypatch.setattr(
        stage4_demo_execution, "_market_ready", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_pre_order_account_observation",
        lambda *args, **kwargs: (0, None),
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_cached_market_timestamps_valid",
        lambda *args, **kwargs: True,
    )
    snapshot = asyncio.run(
        stage4_demo_execution._run_exec_phase(
            attempt,
            phase,
            phase_kind="cleanup",
            cleanup_authorization=authorization,
        )
    )
    assert stopped.is_set()
    assert submitted == []
    assert phase.tester_config.close_positions_on_stop is False
    assert snapshot.failure == stage4_demo_execution.Stage4DemoExecFailure(
        "TERMINAL_FACT_UNKNOWN", "reconciliation", ("POSITION_UNKNOWN",)
    )


def test_stage4_demo_exec_unfilled_cleanup_cannot_publish_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    authorization = Stage4DemoCleanupAuthorization(
        signed_position=Decimal("0.001"),
        quantity=Quantity.from_str("0.001"),
    )
    strategy_id = stage4_demo_execution._require_phase_strategy_id(attempt.phases[1])
    canceled = _cached_order(
        side="SELL",
        order_type="MARKET",
        quantity="0.001",
        filled="0.000",
        reduce_only=True,
        status="CANCELED",
    )
    flat_cache = cast(
        Cache,
        SimpleNamespace(
            orders=lambda **kwargs: [canceled],
            orders_open_count=lambda **kwargs: 0,
            orders_inflight_count=lambda **kwargs: 0,
            positions_open=lambda **kwargs: [],
        ),
    )
    assert stage4_demo_execution._cleanup_complete(flat_cache, attempt, strategy_id)
    assert not stage4_demo_execution._cleanup_action_matches(
        flat_cache, attempt, strategy_id, authorization
    )

    account_mode = _observation(attempt.scenario).account_mode
    opening = replace(
        stage4_demo_execution._failed_phase_snapshot(
            started_at_ns=1,
            failure=stage4_demo_execution.Stage4DemoExecFailure(
                "CLEANUP_FAILED", "cleanup", ("FAILED",)
            ),
        ),
        quote_count=1,
        market_timestamps_valid=True,
        order_submitted=True,
        order_accepted=True,
        order_terminal=True,
        fill_complete=True,
        balance_before_observed=True,
        balance_after_observed=True,
        balance_change_explained=True,
        order_action_consistent=True,
        account_mode=account_mode,
        unresolved_unknown_count=0,
        failure=None,
    )
    cleanup = replace(
        opening,
        order_action_consistent=False,
        failure=stage4_demo_execution.Stage4DemoExecFailure(
            "CLEANUP_FAILED", "cleanup", ("FAILED",)
        ),
    )
    merged = stage4_demo_execution._merge_cleanup_snapshot(opening, cleanup)
    evidence = build_stage4_demo_exec_evidence(
        attempt,
        stage4_demo_execution._logical_observation(
            attempt, canary=merged, execution=merged
        ),
    )
    assert not merged.order_action_consistent
    assert evidence["result"] == "FAIL"
    observations = cast(dict[str, object], evidence["observations"])
    order = cast(dict[str, object], observations["order"])
    failure = cast(dict[str, object], evidence["failure"])
    assert order["classification"] == "conflicting"
    assert failure["code"] == "CLEANUP_FAILED"


def test_stage4_demo_exec_passive_terminal_must_be_canceled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.passive_cancel
    canceled = _cached_order(
        side="BUY",
        order_type="LIMIT",
        quantity="0.001",
        filled="0.000",
        reduce_only=False,
        status="CANCELED",
        price="49999.99",
        post_only=True,
    )

    def cache_with(order: object) -> Cache:
        return cast(
            Cache,
            SimpleNamespace(
                orders=lambda **kwargs: [order],
                orders_open_count=lambda **kwargs: 0,
                orders_inflight_count=lambda **kwargs: 0,
            ),
        )

    strategy_id = stage4_demo_execution._require_phase_strategy_id(attempt.phases[2])
    assert stage4_demo_execution._passive_cancel_complete(
        cache_with(canceled), attempt, strategy_id
    )
    assert not stage4_demo_execution._passive_cancel_complete(
        cache_with(_changed_namespace(canceled, is_post_only=False)),
        attempt,
        strategy_id,
    )
    assert not stage4_demo_execution._passive_cancel_complete(
        cache_with(_changed_namespace(canceled, status="EXPIRED")),
        attempt,
        strategy_id,
    )
    assert not stage4_demo_execution._passive_cancel_complete(
        cache_with(_changed_namespace(canceled, price=Price.from_str("50000.00"))),
        attempt,
        strategy_id,
    )


def test_stage4_demo_exec_market_record_does_not_double_phase_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    account_mode = Stage4DemoAccountModeObservation(
        operator_gate_confirmed=True,
        canary_complete=True,
        net_position_shape_consistent=True,
        account_scope_consistent=True,
        observed_initial_margin=Decimal("50.00"),
        mark_price_min=Decimal("49999.00"),
        mark_price_max=Decimal("50001.00"),
    )
    snapshot = replace(
        stage4_demo_execution._failed_phase_snapshot(
            started_at_ns=1,
            failure=stage4_demo_execution.Stage4DemoExecFailure(
                "TERMINAL_FACT_UNKNOWN",
                "reconciliation",
                ("FAILED",),
            ),
        ),
        quote_count=3,
        trade_count=2,
        market_timestamps_valid=True,
        order_submitted=True,
        order_accepted=True,
        order_terminal=True,
        fill_complete=True,
        balance_before_observed=True,
        balance_after_observed=True,
        balance_change_explained=True,
        order_action_consistent=True,
        account_mode=account_mode,
        unresolved_unknown_count=0,
        failure=None,
    )

    observation = stage4_demo_execution._logical_observation(
        plan.market_close,
        canary=snapshot,
        execution=snapshot,
    )

    assert observation.quote_count == 3
    assert observation.trade_count == 2

    canary = replace(snapshot, phase_observations=({"phase": "canary"},))
    cleanup = replace(snapshot, phase_observations=({"phase": "cleanup"},))
    closed_canary = stage4_demo_execution._merge_cleanup_snapshot(canary, cleanup)
    market = stage4_demo_execution._logical_observation(
        plan.market_close, canary=closed_canary, execution=closed_canary
    )
    passive = stage4_demo_execution._logical_observation(
        plan.passive_cancel,
        canary=closed_canary,
        execution=replace(snapshot, phase_observations=({"phase": "passive"},)),
    )
    assert [item["phase"] for item in market.phase_observations] == [
        "canary",
        "cleanup",
    ]
    assert [item["phase"] for item in passive.phase_observations] == [
        "canary",
        "cleanup",
        "passive",
    ]


def test_stage4_demo_exec_runtime_price_must_match_admitted_passive_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.passive_cancel
    now_ns = time.time_ns()

    def cache_with_bid(bid: str) -> Cache:
        return cast(
            Cache,
            SimpleNamespace(
                instrument=lambda instrument_id: attempt.instrument,
                quote=lambda instrument_id: SimpleNamespace(
                    instrument_id=attempt.instrument.id,
                    bid_price=Price.from_str(bid),
                    ask_price=Price.from_str("50000.02"),
                    ts_event=now_ns,
                ),
                mark_price=lambda instrument_id: SimpleNamespace(
                    instrument_id=attempt.instrument.id,
                    value=Price.from_str("50000.00"),
                    ts_event=now_ns,
                ),
            ),
        )

    sequence = stage4_demo_execution._MarketTimestampSequence(quote_ns=0, mark_ns=0)
    assert stage4_demo_execution._market_ready(
        cache_with_bid("50000.00"),
        attempt,
        sequence,
        phase_kind="passive",
    )
    with pytest.raises(Stage4DemoExecutionError, match="drifted"):
        stage4_demo_execution._market_ready(
            cache_with_bid("50000.01"),
            attempt,
            sequence,
            phase_kind="passive",
        )


@pytest.mark.parametrize(
    ("bid", "timestamp_rollback"),
    [("50000.01", False), ("50000.00", True)],
)
def test_stage4_demo_exec_runtime_drift_is_conflicting_in_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bid: str,
    timestamp_rollback: bool,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.passive_cancel
    phase = attempt.phases[2]
    now_ns = time.time_ns()
    stopped = asyncio.Event()
    canceled = _cached_order(
        side="BUY",
        order_type="LIMIT",
        quantity=str(attempt.passive_quantity),
        filled="0.000",
        reduce_only=False,
        status="CANCELED",
        price=str(attempt.passive_price),
    )
    cache = SimpleNamespace(
        instrument=lambda instrument_id: attempt.instrument,
        quote=lambda instrument_id: SimpleNamespace(
            instrument_id=attempt.instrument.id,
            bid_price=Price.from_str(bid),
            ask_price=Price.from_str("50000.02"),
            ts_event=(
                attempt.market_input.quote_ts_event_ns - 1
                if timestamp_rollback
                else now_ns
            ),
        ),
        mark_price=lambda instrument_id: SimpleNamespace(
            instrument_id=attempt.instrument.id,
            value=Price.from_str("50000.00"),
            ts_event=now_ns,
        ),
        quote_count=lambda instrument_id: 1,
        trade_count=lambda instrument_id: 0,
        orders=lambda **kwargs: [canceled],
        orders_open_count=lambda **kwargs: 0,
        orders_inflight_count=lambda **kwargs: 0,
        positions_open=lambda **kwargs: [],
        positions=lambda **kwargs: [],
        account=lambda *args: None,
    )

    async def run_async() -> None:
        await stopped.wait()

    node = SimpleNamespace(
        cache=cache,
        handle=lambda: SimpleNamespace(is_running=True, stop=stopped.set),
        run_async=run_async,
        dispose=lambda: None,
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_build_exec_tester_runtime_unchecked",
        lambda *args: SimpleNamespace(node=node),
    )

    snapshot = asyncio.run(
        stage4_demo_execution._run_exec_phase(attempt, phase, phase_kind="passive")
    )
    assert snapshot.failure == stage4_demo_execution.Stage4DemoExecFailure(
        "TERMINAL_FACT_CONFLICTING", "data", ("FAILED",)
    )
    assert snapshot.market_timestamps_valid is not timestamp_rollback
    assert snapshot.market_input_conflicting
    observation = replace(
        _observation(attempt.scenario),
        failure=snapshot.failure,
        market_input_conflicting=snapshot.market_input_conflicting,
    )
    evidence = build_stage4_demo_exec_evidence(attempt, observation)
    assert evidence["result"] == "FAIL"
    assert evidence["terminal_state"] == "HALTED"
    observations = cast(dict[str, object], evidence["observations"])
    market_data = cast(dict[str, object], observations["market_data"])
    assert market_data["classification"] == "conflicting"


def test_stage4_demo_exec_action_conflict_cannot_produce_pass_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    observation = replace(
        _observation(DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE),
        order_action_consistent=False,
    )

    evidence = build_stage4_demo_exec_evidence(plan.market_close, observation)

    assert evidence["result"] == "FAIL"
    assert evidence["failure"] == {
        "code": "TERMINAL_FACT_CONFLICTING",
        "phase": "reconciliation",
        "diagnostic_codes": ["FAILED"],
    }


def test_stage4_demo_exec_construction_failure_becomes_terminal_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)

    def fail_construction(*args: object, **kwargs: object) -> object:
        raise RuntimeError("construction failed")

    monkeypatch.setattr(
        stage4_demo_execution,
        "_build_exec_tester_runtime_unchecked",
        fail_construction,
    )
    observation = asyncio.run(
        stage4_demo_execution._observe_stage4_demo_attempt(plan.market_close)
    )

    assert observation.failure is not None
    assert observation.failure.phase == "construction"
    assert observation.unresolved_unknown_count == 1


def test_stage4_demo_exec_public_readiness_waits_for_fresh_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()
    instrument = _instrument()
    instrument_id = instrument.id
    now_ns = time.time_ns()
    reads = 0
    stopped = False

    class CacheStub:
        def instrument(self, _instrument_id: InstrumentId) -> CryptoPerpetual:
            return instrument

        def quote(self, _instrument_id: InstrumentId) -> SimpleNamespace:
            nonlocal reads
            reads += 1
            return SimpleNamespace(
                instrument_id=instrument_id,
                bid_price=Price.from_str("50000.00"),
                ask_price=Price.from_str("50000.01"),
                ts_event=now_ns - 6_000_000_000 if reads == 1 else time.time_ns(),
            )

        def mark_price(self, _instrument_id: InstrumentId) -> SimpleNamespace:
            return SimpleNamespace(
                instrument_id=instrument_id,
                value=Price.from_str("50000.00"),
                ts_event=now_ns - 6_000_000_000 if reads == 1 else time.time_ns(),
            )

    class NodeStub:
        cache = CacheStub()

        def add_builtin_actor(self, *_args: object) -> None:
            pass

        def handle(self) -> SimpleNamespace:
            return SimpleNamespace(is_running=True)

        async def run_async(self) -> None:
            await asyncio.Event().wait()

        def dispose(self) -> None:
            pass

    node = NodeStub()

    class BuilderStub:
        def __getattr__(self, _name: str) -> object:
            return lambda *_args, **_kwargs: self

        def build(self) -> NodeStub:
            return node

    async def stop(
        _handle: object,
        run_task: asyncio.Task[None],
        *,
        request_stop: bool,
    ) -> None:
        nonlocal stopped
        assert request_stop
        stopped = True
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        return None

    monkeypatch.setattr(
        stage4_demo_execution,
        "LiveNode",
        SimpleNamespace(builder=lambda *_args: BuilderStub()),
    )
    monkeypatch.setattr(stage4_demo_execution, "_stop_exec_tester", stop)
    deadline = stage4_demo.start_queue_deadline(DeadlinePhase.PRICE_READINESS)

    result = asyncio.run(stage4_demo_execution._acquire_public_market_input(deadline))

    assert reads >= 2
    assert stopped
    assert not deadline.expired()
    assert result.quote_ts_event_ns > now_ns - 5_000_000_000
    assert result.mark_ts_event_ns > now_ns - 5_000_000_000


def test_stage4_demo_exec_rejects_stale_or_non_allowlisted_public_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del monkeypatch, tmp_path
    value = _market_input()
    stale = Stage4DemoExecMarketInput(
        **{
            **value.__dict__,
            "quote_ts_event_ns": value.observed_at_ns - 6_000_000_000,
        }
    )
    with pytest.raises(Stage4DemoExecutionError, match="stale"):
        stage4_demo_execution._validate_market_input(stale)
    stale_observation = replace(
        value,
        observed_at_ns=time.time_ns() - 6_000_000_000,
        quote_ts_event_ns=time.time_ns() - 6_100_000_000,
        mark_ts_event_ns=time.time_ns() - 6_100_000_000,
    )
    with pytest.raises(Stage4DemoExecutionError, match="at check time"):
        stage4_demo_execution._validate_market_input(stale_observation)
    with pytest.raises(Stage4DemoExecutionError, match="reused or moved backward"):
        stage4_demo_execution._validate_market_input(
            replace(value),
            previous=value,
        )
    with pytest.raises(Stage4DemoExecutionError, match="outside the allowlist"):
        stage4_demo_execution._validate_market_input(
            replace(
                value,
                instrument=CryptoPerpetual(
                    instrument_id=InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
                    raw_symbol=Symbol("ETHUSDT"),
                    base_currency=Currency.from_str("ETH"),
                    quote_currency=Currency.from_str("USDT"),
                    settlement_currency=Currency.from_str("USDT"),
                    is_inverse=False,
                    price_precision=2,
                    size_precision=3,
                    price_increment=Price.from_str("0.01"),
                    size_increment=Quantity.from_str("0.001"),
                    min_quantity=Quantity.from_str("0.001"),
                    max_quantity=Quantity.from_str("10.000"),
                    min_notional=Money.from_str("5.00 USDT"),
                    ts_event=0,
                    ts_init=0,
                ),
            ),
        )


def test_stage4_demo_exec_rejects_runtime_market_timestamp_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    cache = cast(
        Cache,
        SimpleNamespace(
            instrument=lambda instrument_id: attempt.instrument,
            quote=lambda instrument_id: SimpleNamespace(
                instrument_id=attempt.instrument.id,
                bid_price=Price.from_str("50000.00"),
                ask_price=Price.from_str("50000.01"),
                ts_event=attempt.market_input.quote_ts_event_ns - 1,
            ),
            mark_price=lambda instrument_id: SimpleNamespace(
                instrument_id=attempt.instrument.id,
                value=Price.from_str("50000.00"),
                ts_event=attempt.market_input.mark_ts_event_ns,
            ),
        ),
    )
    sequence = stage4_demo_execution._MarketTimestampSequence(
        quote_ns=attempt.market_input.quote_ts_event_ns,
        mark_ns=attempt.market_input.mark_ts_event_ns,
    )

    with pytest.raises(Stage4DemoExecutionError, match="moved backward"):
        stage4_demo_execution._market_ready(cache, attempt, sequence)


def test_stage4_demo_exec_writes_each_record_once_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    evidence = build_stage4_demo_exec_evidence(
        plan.market_close,
        _observation(DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE),
    )

    outcome = write_stage4_demo_exec_evidence(plan.market_close, evidence)

    assert json.loads(outcome.evidence_path.read_text("utf-8")) == evidence
    rendered = outcome.evidence_path.read_text("utf-8").lower()
    assert "api_key" not in rendered
    assert "api_secret" not in rendered
    with pytest.raises(Stage4DemoExecutionError, match="already exists"):
        write_stage4_demo_exec_evidence(plan.market_close, evidence)


def test_stage4_demo_exec_run_persists_two_independent_attempt_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    runtime = _runtime()
    frozen = freeze_demo_config(DemoConfig())

    def admit(*, batch: DemoAdmissionBatch, operator_token: str) -> AdmittedDemoAttempt:
        del batch
        assert operator_token == OPERATOR_CONFIRMATION_TOKEN
        events.append("admit")
        return cast(
            AdmittedDemoAttempt,
            SimpleNamespace(runtime=runtime, frozen_config=frozen),
        )

    async def observe(
        runtime_plan: Stage4DemoExecAttemptPlan,
    ) -> Stage4DemoExecObservation:
        events.append(f"observe:{runtime_plan.scenario.value}")
        return _observation(runtime_plan.scenario)

    monkeypatch.setattr(stage4_demo_execution, "admit_current_demo_attempt", admit)
    monkeypatch.setattr(stage4_demo_execution, "_observe_stage4_demo_attempt", observe)
    batch = cast(
        DemoAdmissionBatch,
        SimpleNamespace(repository_root=tmp_path / "repository"),
    )
    market_input = _market_input()
    passive_input = _market_input(1_000_000_000)

    def acquire_market() -> Stage4DemoExecAttemptInput:
        events.append("acquire:market")
        return Stage4DemoExecAttemptInput(
            market_input,
            OPERATOR_CONFIRMATION_TOKEN,
        )

    def acquire_passive() -> Stage4DemoExecAttemptInput:
        events.append("acquire:passive")
        assert len(list((tmp_path / "run-evidence").rglob(EXEC_EVIDENCE_FILENAME))) == 1
        return Stage4DemoExecAttemptInput(
            passive_input,
            OPERATOR_CONFIRMATION_TOKEN,
        )

    outcome = run_stage4_demo_exec(
        batch=batch,
        evidence_root=tmp_path / "run-evidence",
        batch_id="batch_abcdefghijklmnopqrstuv",
        acquire_market_close=acquire_market,
        acquire_passive_cancel=acquire_passive,
    )

    assert outcome.market_close.evidence["scenario"] == (
        DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value
    )
    assert outcome.passive_cancel.evidence["scenario"] == (
        DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL.value
    )
    assert outcome.market_close.evidence_path != outcome.passive_cancel.evidence_path
    assert events == [
        "acquire:market",
        "admit",
        "observe:exec_tester_market_close",
        "acquire:passive",
        "admit",
        "observe:exec_tester_passive_cancel",
    ]


def test_stage4_demo_exec_market_halt_is_persisted_and_blocks_passive_admission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    frozen = freeze_demo_config(DemoConfig())
    events: list[str] = []

    def admit(*, batch: DemoAdmissionBatch, operator_token: str) -> AdmittedDemoAttempt:
        del batch, operator_token
        events.append("admit")
        return cast(
            AdmittedDemoAttempt,
            SimpleNamespace(runtime=runtime, frozen_config=frozen),
        )

    async def observe(
        runtime_plan: Stage4DemoExecAttemptPlan,
    ) -> Stage4DemoExecObservation:
        events.append(f"observe:{runtime_plan.scenario.value}")
        return _observation(runtime_plan.scenario, ambiguous=True)

    def acquire_market() -> Stage4DemoExecAttemptInput:
        events.append("acquire:market")
        return Stage4DemoExecAttemptInput(
            _market_input(),
            OPERATOR_CONFIRMATION_TOKEN,
        )

    def acquire_passive() -> Stage4DemoExecAttemptInput:
        events.append("acquire:passive")
        return Stage4DemoExecAttemptInput(
            _market_input(1_000_000_000),
            OPERATOR_CONFIRMATION_TOKEN,
        )

    monkeypatch.setattr(stage4_demo_execution, "admit_current_demo_attempt", admit)
    monkeypatch.setattr(stage4_demo_execution, "_observe_stage4_demo_attempt", observe)
    batch = cast(
        DemoAdmissionBatch,
        SimpleNamespace(repository_root=tmp_path / "repository"),
    )

    with pytest.raises(
        Stage4DemoExecutionError, match="passive attempt was not admitted"
    ):
        run_stage4_demo_exec(
            batch=batch,
            evidence_root=tmp_path / "halt-evidence",
            batch_id="batch_abcdefghijklmnopqrstuv",
            acquire_market_close=acquire_market,
            acquire_passive_cancel=acquire_passive,
        )

    assert events == [
        "acquire:market",
        "admit",
        "observe:exec_tester_market_close",
    ]
    evidence_paths = list((tmp_path / "halt-evidence").rglob(EXEC_EVIDENCE_FILENAME))
    assert len(evidence_paths) == 1
    assert json.loads(evidence_paths[0].read_text("utf-8"))["result"] == "FAIL"


@pytest.mark.parametrize("failed_scenario", ["market", "passive"])
def test_stage4_demo_exec_preparation_failure_persists_started_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_scenario: str,
) -> None:
    runtime = _runtime()
    frozen = freeze_demo_config(DemoConfig())
    admissions = 0
    public_reads = 0

    def admit(*, batch: DemoAdmissionBatch, operator_token: str) -> AdmittedDemoAttempt:
        nonlocal admissions
        del batch
        assert operator_token == OPERATOR_CONFIRMATION_TOKEN
        admissions += 1
        return cast(
            AdmittedDemoAttempt,
            SimpleNamespace(runtime=runtime, frozen_config=frozen),
        )

    async def public_market(_deadline: object) -> Stage4DemoExecMarketInput:
        nonlocal public_reads
        public_reads += 1
        if failed_scenario == "market" or public_reads == 2:
            raise Stage4DemoExecutionError("public market input unavailable")
        return _market_input()

    async def observe(
        runtime_plan: Stage4DemoExecAttemptPlan,
    ) -> Stage4DemoExecObservation:
        return _observation(runtime_plan.scenario)

    monkeypatch.setattr(stage4_demo_execution, "admit_current_demo_attempt", admit)
    monkeypatch.setattr(
        stage4_demo_execution, "_acquire_public_market_input", public_market
    )
    monkeypatch.setattr(stage4_demo_execution, "_observe_stage4_demo_attempt", observe)
    if failed_scenario == "market":
        monkeypatch.setattr(
            stage4_demo,
            "start_queue_deadline",
            lambda _phase: SimpleNamespace(expired=lambda: True),
        )
    batch = cast(
        DemoAdmissionBatch,
        SimpleNamespace(repository_root=tmp_path / "repository"),
    )
    evidence_root = tmp_path / "external-evidence"

    with pytest.raises(Stage4DemoExecutionError, match="record="):
        run_stage4_demo_exec(
            batch=batch,
            evidence_root=evidence_root,
            batch_id="batch_abcdefghijklmnopqrstuv",
            acquire_market_close=lambda: Stage4DemoExecAttemptInput(
                _market_input(), OPERATOR_CONFIRMATION_TOKEN
            ),
            acquire_passive_cancel=lambda: Stage4DemoExecAttemptInput(
                _market_input(1_000_000_000), OPERATOR_CONFIRMATION_TOKEN
            ),
        )

    paths = list(evidence_root.rglob(EXEC_EVIDENCE_FILENAME))
    assert len(paths) == admissions == (1 if failed_scenario == "market" else 2)
    failed = next(
        json.loads(path.read_text("utf-8"))
        for path in paths
        if failed_scenario in str(path.parent)
    )
    validate_stage4_demo_evidence(failed)
    assert failed["result"] == "FAIL"
    assert failed["terminal_state"] == "HALTED"
    assert failed["failure"]["code"] == (
        "DATA_TIMEOUT" if failed_scenario == "market" else "DATA_INVALID"
    )
    assert failed["instrument"]["price_increment"] is None
    assert failed["observations"]["market_data"]["classification"] == "missing"
    assert failed["observations"]["account_mode"]["operator_gate_confirmed"]
    assert failed["cleanup"]["classification"] == "cleanup_incomplete"
    assert failed["cleanup"]["unresolved_unknown_count"] == 1


def test_stage4_demo_exec_rejects_inflated_caller_constraints_before_admission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    public = _market_input()
    altered = replace(
        public,
        instrument=CryptoPerpetual(
            instrument_id=public.instrument.id,
            raw_symbol=Symbol("BTCUSDT"),
            base_currency=Currency.from_str("BTC"),
            quote_currency=Currency.from_str("USDT"),
            settlement_currency=Currency.from_str("USDT"),
            is_inverse=False,
            price_precision=2,
            size_precision=0,
            price_increment=Price.from_str("0.01"),
            size_increment=Quantity.from_str("1"),
            min_quantity=Quantity.from_str("1"),
            max_quantity=Quantity.from_str("10"),
            min_notional=Money.from_str("5.00 USDT"),
            ts_event=0,
            ts_init=0,
        ),
    )
    batch = cast(
        DemoAdmissionBatch,
        SimpleNamespace(repository_root=tmp_path / "repository"),
    )

    def admit(**_kwargs: object) -> AdmittedDemoAttempt:
        return cast(
            AdmittedDemoAttempt,
            SimpleNamespace(
                runtime=_runtime(),
                frozen_config=freeze_demo_config(DemoConfig()),
            ),
        )

    monkeypatch.setattr(stage4_demo_execution, "admit_current_demo_attempt", admit)
    plan = build_stage4_demo_exec_plan(
        batch=batch,
        evidence_root=tmp_path / "evidence",
        batch_id="batch_abcdefghijklmnopqrstuv",
        acquire_market_close=lambda: Stage4DemoExecAttemptInput(
            altered, OPERATOR_CONFIRMATION_TOKEN
        ),
        acquire_passive_cancel=lambda: Stage4DemoExecAttemptInput(
            public, OPERATOR_CONFIRMATION_TOKEN
        ),
    )
    with pytest.raises(Stage4DemoExecutionError, match="record="):
        stage4_demo_execution._admit_deferred_attempt(
            plan,
            scenario=DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE,
            acquire=plan.acquire_market_close,
        )
    record_path = (
        tmp_path
        / "evidence"
        / "batch_abcdefghijklmnopqrstuv"
        / DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value
        / EXEC_EVIDENCE_FILENAME
    )
    record = json.loads(record_path.read_text("utf-8"))
    validate_stage4_demo_evidence(record)
    assert record["result"] == "FAIL"
    assert record["failure"]["code"] == "DATA_INVALID"


def test_stage4_demo_exec_uncorrelated_account_margin_is_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    observed_at_ns = time.time_ns()
    event = SimpleNamespace(
        ts_event=observed_at_ns - 10_000_000_000,
        ts_init=observed_at_ns - 10_000_000_000,
        info={"total_initial_margin": "25.00"},
        margins=[SimpleNamespace(instrument_id=attempt.instrument.id)],
    )
    position = SimpleNamespace(id="BTCUSDT-BINANCE-001")
    cache = cast(
        Cache,
        SimpleNamespace(
            account=lambda *args: SimpleNamespace(last_event=event),
            positions_open=lambda **kwargs: [position],
            orders_open=lambda: [],
            orders_open_count=lambda: 0,
            orders_inflight_count=lambda: 0,
            orders=lambda: [],
            mark_prices=lambda *args: [],
        ),
    )
    observed = stage4_demo_execution._capture_account_mode(
        cache, attempt, observed_at_ns
    )
    assert observed.observed_initial_margin == Decimal("25.00")
    assert stage4_demo_execution._account_mode_complete(attempt, observed)

    event.margins = [SimpleNamespace(instrument_id="OTHER")]
    conflicting = stage4_demo_execution._capture_account_mode(
        cache, attempt, observed_at_ns
    )
    assert not conflicting.account_scope_consistent
    assert not stage4_demo_execution._account_mode_complete(attempt, conflicting)


def test_stage4_demo_exec_canary_rejects_observed_position_conflict_before_passive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    bad_mode = replace(
        _observation(DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL).account_mode,
        net_position_shape_consistent=False,
        observed_initial_margin=Decimal("25.00"),
    )
    assert not stage4_demo_execution._account_mode_complete(
        plan.passive_cancel, bad_mode
    )
    failure = stage4_demo_execution.Stage4DemoExecFailure(
        "TERMINAL_FACT_UNKNOWN",
        "account_mode",
        ("ACCOUNT_MODE_UNKNOWN",),
    )
    canary = replace(
        stage4_demo_execution._failed_phase_snapshot(
            started_at_ns=1,
            failure=failure,
        ),
        account_mode=bad_mode,
    )
    phases: list[str] = []

    async def run_phase(
        _plan: Stage4DemoExecAttemptPlan,
        _phase: object,
        *,
        phase_kind: str,
        cleanup_authorization: object = None,
    ) -> stage4_demo_execution._ExecPhaseSnapshot:
        del cleanup_authorization
        phases.append(phase_kind)
        return canary

    monkeypatch.setattr(stage4_demo_execution, "_run_exec_phase", run_phase)
    observation = asyncio.run(
        stage4_demo_execution._observe_stage4_demo_attempt(plan.passive_cancel)
    )
    assert phases == ["canary"]
    assert observation.failure == failure


def test_stage4_demo_exec_uses_pre_order_account_event_after_fast_fill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    strategy_id = stage4_demo_execution._require_phase_strategy_id(attempt.phases[0])

    def account_event(balance: str, ts_init: int) -> AccountState:
        total = Money.from_str(f"{balance} USDT")
        return AccountState(
            AccountId.from_str("BINANCE-001"),
            AccountType.MARGIN,
            [AccountBalance(total, Money.from_str("0.00 USDT"), total)],
            [],
            True,
            UUID4(),
            ts_init,
            ts_init,
        )

    account = MarginAccount(account_event("100.00", 10), True)
    account.apply(account_event("99.95", 30))
    observed_orders: list[object] = [SimpleNamespace(ts_submitted=20)]
    cache = cast(
        Cache,
        SimpleNamespace(
            orders=lambda **kwargs: observed_orders,
            account=lambda *args: account,
        ),
    )
    count, before = stage4_demo_execution._pre_order_account_observation(
        cache, attempt, strategy_id
    )
    assert (count, before) == (1, Decimal("100.00"))
    position = SimpleNamespace(
        is_closed=True,
        realized_pnl=Money.from_str("-0.10 USDT"),
        commissions=lambda: [Money.from_str("0.05 USDT")],
    )
    assert stage4_demo_execution._balance_reconciled(
        attempt,
        before=before,
        after=Decimal("99.90"),
        positions=[position],
        no_fill=False,
    )
    assert not stage4_demo_execution._balance_reconciled(
        attempt,
        before=Decimal("99.95"),
        after=Decimal("99.90"),
        positions=[position],
        no_fill=False,
    )

    observed_orders[:] = [
        SimpleNamespace(ts_submitted=5, is_reduce_only=False),
        SimpleNamespace(ts_submitted=20, is_reduce_only=True),
    ]
    assert stage4_demo_execution._pre_order_account_observation(
        cache, attempt, strategy_id, phase_kind="cleanup"
    ) == (1, Decimal("100.00"))

    account = MarginAccount(account_event("99.95", 30), True)
    assert stage4_demo_execution._pre_order_account_observation(
        cache, attempt, strategy_id, phase_kind="cleanup"
    ) == (0, None)


def test_stage4_demo_exec_balance_reconciles_numeric_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    reconcile = stage4_demo_execution._balance_reconciled
    attempt = plan.market_close
    position = SimpleNamespace(
        is_closed=True,
        realized_pnl=Money.from_str("1.50 USDT"),
        commissions=lambda: [Money.from_str("0.10 USDT")],
    )
    assert reconcile(
        attempt,
        before=Decimal("100"),
        after=Decimal("101.50"),
        positions=[position],
        no_fill=False,
    )
    assert not reconcile(
        attempt,
        before=Decimal("100"),
        after=Decimal("102"),
        positions=[position],
        no_fill=False,
    )
    assert reconcile(
        attempt,
        before=Decimal("100"),
        after=Decimal("100"),
        positions=[],
        no_fill=True,
    )
    assert not reconcile(
        attempt,
        before=Decimal("100"),
        after=Decimal("100.01"),
        positions=[],
        no_fill=True,
    )


def test_stage4_demo_exec_global_state_blocks_cleanup_and_final_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    strategy_id = stage4_demo_execution._require_phase_strategy_id(attempt.phases[1])
    authorization = Stage4DemoCleanupAuthorization(
        signed_position=Decimal("0.001"), quantity=Quantity.from_str("0.001")
    )
    target = SimpleNamespace(
        instrument_id=attempt.instrument.id,
        strategy_id=strategy_id,
        quantity=Quantity.from_str("0.001"),
        is_long=True,
        is_short=False,
    )
    unrelated = SimpleNamespace(
        instrument_id=InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
        strategy_id=strategy_id,
        quantity=Quantity.from_str("0.001"),
        is_long=True,
        is_short=False,
    )
    canary_order = _cached_order(
        side="BUY",
        order_type="MARKET",
        quantity="0.001",
        filled="0.001",
        reduce_only=False,
        status="FILLED",
    )
    state: dict[str, Any] = {
        "orders": [canary_order],
        "positions": [target],
        "active_orders": 0,
    }

    def orders(**kwargs: object) -> list[object]:
        return [canary_order] if kwargs else state["orders"]

    cache = cast(
        Cache,
        SimpleNamespace(
            orders_open_count=lambda **kwargs: state["active_orders"],
            orders_inflight_count=lambda **kwargs: 0,
            orders=orders,
            positions_open=lambda **kwargs: state["positions"],
            positions=lambda **kwargs: [target],
            account=lambda *args: None,
            quote_count=lambda *args: 0,
            trade_count=lambda *args: 0,
            quote=lambda *args: None,
            mark_price=lambda *args: None,
        ),
    )
    assert stage4_demo_execution._cleanup_cache_matches_authorization(
        cache, attempt, strategy_id, authorization
    )
    state["orders"].append(SimpleNamespace(is_pending_cancel=True))
    assert not stage4_demo_execution._cleanup_cache_matches_authorization(
        cache, attempt, strategy_id, authorization
    )
    state["orders"].pop()
    state["positions"].append(unrelated)
    assert not stage4_demo_execution._cleanup_cache_matches_authorization(
        cache, attempt, strategy_id, authorization
    )
    state["active_orders"] = 1
    snapshot = stage4_demo_execution._capture_phase_snapshot(
        cache,
        attempt,
        phase=attempt.phases[0],
        phase_kind="canary",
        started_at_ns=1,
        ended_at_ns=2,
        account_before_count=0,
        account_before_balance=None,
        account_mode=None,
        market_sequence=stage4_demo_execution._MarketTimestampSequence(0, 0),
        failure=None,
    )
    assert snapshot.active_order_count == 1
    assert snapshot.open_position_count == 2
    observation = stage4_demo_execution._logical_observation(
        attempt, canary=snapshot, execution=snapshot
    )
    evidence = build_stage4_demo_exec_evidence(attempt, observation)
    assert evidence["result"] == "FAIL"
    cleanup = cast(dict[str, object], evidence["cleanup"])
    assert cleanup["active_order_count"] == 1
    assert cleanup["open_position_count"] == 2


def test_stage4_demo_exec_persists_nautilus_phase_facts_in_attempt_partition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    quantity = attempt.canary_quantity
    order = _cached_order(
        side="BUY",
        order_type="MARKET",
        quantity=str(quantity),
        filled=str(quantity),
        reduce_only=False,
        status="FILLED",
    )
    order.events = [
        SimpleNamespace(
            ts_event=123, last_qty=quantity, last_px=Price.from_str("50000.00")
        )
    ]
    position = SimpleNamespace(
        instrument_id=attempt.instrument.id,
        quantity=quantity,
        is_long=True,
        is_short=False,
    )
    account = SimpleNamespace(
        event_count=1,
        last_event=SimpleNamespace(
            ts_event=124, info={"total_initial_margin": "50.00"}
        ),
        balance_total=lambda currency: Money.from_str("100.00 USDT"),
    )
    cache = cast(
        Cache,
        SimpleNamespace(
            orders=lambda **kwargs: [order],
            orders_open_count=lambda **kwargs: 0,
            orders_inflight_count=lambda **kwargs: 0,
            positions_open=lambda **kwargs: [position],
            positions=lambda **kwargs: [position],
            account=lambda *args: account,
            quote_count=lambda *args: 1,
            trade_count=lambda *args: 1,
            quote=lambda *args: None,
            mark_price=lambda *args: None,
        ),
    )
    snapshot = stage4_demo_execution._capture_phase_snapshot(
        cache,
        attempt,
        phase=attempt.phases[0],
        phase_kind="canary",
        started_at_ns=100,
        ended_at_ns=200,
        account_before_count=1,
        account_before_balance=Decimal("100.00"),
        account_mode=_observation(attempt.scenario).account_mode,
        failure=None,
        market_sequence=stage4_demo_execution._MarketTimestampSequence(0, 0),
    )
    facts = cast(dict[str, Any], snapshot.phase_observations[0])
    assert facts["orders"][0]["filled_quantity"] == str(quantity)
    assert facts["orders"][0]["events"][0]["last_qty"] == str(quantity)
    assert facts["account"]["initial_margin"] == "50"
    assert facts["positions"][0]["instrument_id"] == str(attempt.instrument.id)

    observation = stage4_demo_execution._logical_observation(
        attempt, canary=snapshot, execution=snapshot
    )
    evidence = build_stage4_demo_exec_evidence(attempt, observation)
    outcome = write_stage4_demo_exec_evidence(
        attempt, evidence, phase_observations=observation.phase_observations
    )
    sidecar = json.loads((outcome.evidence_partition / "observations.json").read_text())
    rendered = canonical_stage4_demo_evidence_json(evidence)
    assert sidecar["evidence_sha256"] == hashlib.sha256(rendered.encode()).hexdigest()
    assert sidecar["phases"] == [facts]
    assert sidecar["source"] == "nautilus_cache"


def test_stage4_demo_exec_observed_timestamp_rollback_after_readiness_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.passive_cancel
    phase = attempt.phases[2]
    now_ns = time.time_ns()
    state = {"quote_ns": now_ns}
    stopped = asyncio.Event()
    canceled = _cached_order(
        side="BUY",
        order_type="LIMIT",
        quantity=str(attempt.passive_quantity),
        filled="0.000",
        reduce_only=False,
        status="CANCELED",
        price=str(attempt.passive_price),
    )
    cache = SimpleNamespace(
        instrument=lambda instrument_id: attempt.instrument,
        quote=lambda instrument_id: SimpleNamespace(
            instrument_id=attempt.instrument.id,
            bid_price=Price.from_str("50000.00"),
            ask_price=Price.from_str("50000.02"),
            ts_event=state["quote_ns"],
        ),
        mark_price=lambda instrument_id: SimpleNamespace(
            instrument_id=attempt.instrument.id,
            value=Price.from_str("50000.00"),
            ts_event=now_ns,
        ),
        quote_count=lambda instrument_id: 1,
        trade_count=lambda instrument_id: 0,
        orders=lambda **kwargs: [canceled],
        orders_open_count=lambda **kwargs: 0,
        orders_inflight_count=lambda **kwargs: 0,
        positions_open=lambda **kwargs: [],
        positions=lambda **kwargs: [],
        account=lambda *args: None,
    )

    async def run_async() -> None:
        await stopped.wait()

    def stop() -> None:
        state["quote_ns"] = now_ns - 1
        stopped.set()

    node = SimpleNamespace(
        cache=cache,
        handle=lambda: SimpleNamespace(is_running=True, stop=stop),
        run_async=run_async,
        dispose=lambda: None,
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_build_exec_tester_runtime_unchecked",
        lambda *args: SimpleNamespace(node=node),
    )

    snapshot = asyncio.run(
        stage4_demo_execution._run_exec_phase(attempt, phase, phase_kind="passive")
    )
    assert not snapshot.market_timestamps_valid
    assert snapshot.market_input_conflicting
    assert snapshot.failure is not None
    observation = replace(
        _observation(attempt.scenario),
        market_timestamps_valid=snapshot.market_timestamps_valid,
    )
    evidence = build_stage4_demo_exec_evidence(attempt, observation)
    assert evidence["result"] == "FAIL"
    assert evidence["terminal_state"] == "HALTED"


def test_stage4_demo_exec_post_start_data_failure_missing_order_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    attempt = plan.market_close
    stopped = asyncio.Event()
    cache = SimpleNamespace(
        orders=lambda **kwargs: [],
        orders_open_count=lambda **kwargs: 0,
        orders_inflight_count=lambda **kwargs: 0,
        positions_open=lambda **kwargs: [],
        positions=lambda **kwargs: [],
        account=lambda *args: None,
        quote_count=lambda *args: 0,
        trade_count=lambda *args: 0,
        quote=lambda *args: None,
        mark_price=lambda *args: None,
    )

    async def run_async() -> None:
        await stopped.wait()

    node = SimpleNamespace(
        cache=cache,
        handle=lambda: SimpleNamespace(is_running=True, stop=stopped.set),
        run_async=run_async,
        dispose=lambda: None,
    )
    monkeypatch.setattr(
        stage4_demo_execution,
        "_build_exec_tester_runtime_unchecked",
        lambda *args: SimpleNamespace(node=node),
    )
    calls = 0

    async def fail_readiness(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise stage4_demo_execution._Stage4DemoExecutionRuntimeError(
                stage4_demo_execution.Stage4DemoExecFailure(
                    "CONNECT_SUBSCRIPTION_FAILED", "data", ("FAILED",)
                )
            )

    monkeypatch.setattr(stage4_demo_execution, "_wait_until", fail_readiness)
    snapshot = asyncio.run(
        stage4_demo_execution._run_exec_phase(
            attempt, attempt.phases[0], phase_kind="canary"
        )
    )
    assert calls == 2
    assert snapshot.unresolved_unknown_count == 1
    evidence = build_stage4_demo_exec_evidence(
        attempt,
        stage4_demo_execution._logical_observation(
            attempt, canary=snapshot, execution=snapshot
        ),
    )
    cleanup = cast(dict[str, object], evidence["cleanup"])
    assert evidence["result"] == "FAIL"
    assert cleanup["classification"] == "cleanup_incomplete"
