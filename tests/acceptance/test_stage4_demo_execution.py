from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from nautilus_trader.adapters.binance import BinanceEnvironment
from nautilus_trader.common import Cache
from nautilus_trader.model import (
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Money,
    Price,
    Quantity,
    Symbol,
)

from tracequant.integrations.nautilus import stage4_demo_execution
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
        account_mode=Stage4DemoAccountModeObservation(
            operator_gate_confirmed=True,
            canary_complete=True,
            one_way_confirmed=True,
            isolated_confirmed=True,
            observed_initial_margin=Decimal("50.00"),
            mark_price_min=Decimal("49999.00"),
            mark_price_max=Decimal("50001.00"),
        ),
        unresolved_unknown_count=1 if ambiguous else 0,
    )


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

    market = plan.market_close.phases[0].tester_config
    assert plan.market_close.phases[0].run_condition == "always"
    assert market.open_position_on_start_qty == Decimal("0.001")
    assert market.open_position_on_first_quote is True
    assert market.enable_limit_buys is False
    assert market.enable_limit_sells is False
    assert market.enable_stop_buys is False
    assert market.enable_stop_sells is False
    assert market.close_positions_on_stop is True
    assert market.reduce_only_on_stop is True
    assert market.close_positions_qty_precision is None

    canary, passive, cleanup = (
        phase.tester_config for phase in plan.passive_cancel.phases
    )
    assert [phase.run_condition for phase in plan.passive_cancel.phases] == [
        "always",
        "always",
        "failure_with_cleanup_proof",
    ]
    assert canary.open_position_on_start_qty == Decimal("0.001")
    assert canary.close_positions_on_stop is True
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
    assert cleanup.close_positions_on_stop is True
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
    assert account_mode["canary_complete"] is True
    assert account_mode["one_way_confirmed"] is True
    assert account_mode["isolated_confirmed"] is True
    assert account_mode["leverage_one_confirmed"] is True
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
    position = SimpleNamespace(
        quantity=Quantity.from_str("0.003"),
        is_long=True,
        is_short=False,
    )
    close_order = SimpleNamespace(
        quantity=Quantity.from_str("0.003"),
        side="SELL",
        is_reduce_only=True,
    )
    cache = cast(
        Cache,
        SimpleNamespace(
            positions_open=lambda **kwargs: [position],
            orders_open_count=lambda **kwargs: 0,
            orders_inflight_count=lambda **kwargs: 0,
            orders=lambda **kwargs: [close_order],
        ),
    )

    assert stage4_demo_execution._cleanup_cache_matches_authorization(
        cache,
        attempt,
        authorization,
    )
    assert stage4_demo_execution._cleanup_action_matches(
        cache,
        attempt,
        stage4_demo_execution._require_phase_strategy_id(attempt.phases[2]),
        authorization,
    )
    position.quantity = Quantity.from_str("0.004")
    assert not stage4_demo_execution._cleanup_cache_matches_authorization(
        cache,
        attempt,
        authorization,
    )


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
                bid_price=Price.from_str("50000.00"),
                ask_price=Price.from_str("50000.01"),
                ts_event=attempt.market_input.quote_ts_event_ns - 1,
            ),
            mark_price=lambda instrument_id: SimpleNamespace(
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
