from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from nautilus_trader.adapters.binance import BinanceEnvironment
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
    EXEC_TESTER_BUILTIN,
    PASSIVE_OFFSET_TICKS,
    Stage4DemoAccountModeObservation,
    Stage4DemoExecMarketInput,
    Stage4DemoExecObservation,
    Stage4DemoExecPlan,
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
    observed = 1_800_000_000_000_000_000 + offset_ns
    return Stage4DemoExecMarketInput(
        instrument=_instrument(),
        best_bid=Price.from_str("50000.00"),
        best_ask=Price.from_str("50000.01"),
        mark_price=Price.from_str("50000.00"),
        quote_ts_event_ns=observed - 100_000_000,
        mark_ts_event_ns=observed - 100_000_000,
        observed_at_ns=observed,
    )


def _plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Stage4DemoExecPlan, list[str]]:
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
    plan = build_stage4_demo_exec_plan(
        batch=batch,
        evidence_root=tmp_path / "external-evidence",
        batch_id="batch_abcdefghijklmnopqrstuv",
        market_close_input=_market_input(),
        passive_cancel_input=_market_input(1_000_000_000),
        market_operator_token=OPERATOR_CONFIRMATION_TOKEN,
        passive_operator_token=OPERATOR_CONFIRMATION_TOKEN,
    )
    return plan, tokens


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
    ) == Quantity.from_str("0.003")
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


def test_stage4_demo_exec_rejects_stale_or_non_allowlisted_public_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan, _ = _plan(monkeypatch, tmp_path)
    del plan
    value = _market_input()
    stale = Stage4DemoExecMarketInput(
        **{
            **value.__dict__,
            "quote_ts_event_ns": value.observed_at_ns - 6_000_000_000,
        }
    )
    batch = cast(DemoAdmissionBatch, SimpleNamespace(repository_root=tmp_path / "repo"))
    shared = _market_input()
    with pytest.raises(Stage4DemoExecutionError, match="cannot reuse"):
        build_stage4_demo_exec_plan(
            batch=batch,
            evidence_root=tmp_path / "evidence",
            batch_id="batch_abcdefghijklmnopqrstuv",
            market_close_input=shared,
            passive_cancel_input=shared,
            market_operator_token=OPERATOR_CONFIRMATION_TOKEN,
            passive_operator_token=OPERATOR_CONFIRMATION_TOKEN,
        )
    with pytest.raises(Stage4DemoExecutionError, match="stale"):
        build_stage4_demo_exec_plan(
            batch=batch,
            evidence_root=tmp_path / "evidence",
            batch_id="batch_abcdefghijklmnopqrstuv",
            market_close_input=stale,
            passive_cancel_input=_market_input(1_000_000_000),
            market_operator_token=OPERATOR_CONFIRMATION_TOKEN,
            passive_operator_token=OPERATOR_CONFIRMATION_TOKEN,
        )
    with pytest.raises(Stage4DemoExecutionError, match="outside the allowlist"):
        build_stage4_demo_exec_plan(
            batch=batch,
            evidence_root=tmp_path / "evidence",
            batch_id="batch_abcdefghijklmnopqrstuv",
            market_close_input=replace(
                _market_input(),
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
            passive_cancel_input=_market_input(1_000_000_000),
            market_operator_token=OPERATOR_CONFIRMATION_TOKEN,
            passive_operator_token=OPERATOR_CONFIRMATION_TOKEN,
        )


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
    plan, _ = _plan(monkeypatch, tmp_path)

    async def observe(
        runtime_plan: Stage4DemoExecPlan,
    ) -> tuple[Stage4DemoExecObservation, Stage4DemoExecObservation]:
        assert runtime_plan.market_close.market_input is not (
            runtime_plan.passive_cancel.market_input
        )
        return (
            _observation(DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE),
            _observation(DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL),
        )

    monkeypatch.setattr(stage4_demo_execution, "_observe_stage4_demo_exec", observe)
    batch = cast(
        DemoAdmissionBatch,
        SimpleNamespace(repository_root=tmp_path / "repository"),
    )

    outcome = run_stage4_demo_exec(
        batch=batch,
        evidence_root=tmp_path / "run-evidence",
        batch_id="batch_abcdefghijklmnopqrstuv",
        market_close_input=_market_input(),
        passive_cancel_input=_market_input(1_000_000_000),
        market_operator_token=OPERATOR_CONFIRMATION_TOKEN,
        passive_operator_token=OPERATOR_CONFIRMATION_TOKEN,
    )

    assert outcome.market_close.evidence["scenario"] == (
        DemoEvidenceScenario.EXEC_TESTER_MARKET_CLOSE.value
    )
    assert outcome.passive_cancel.evidence["scenario"] == (
        DemoEvidenceScenario.EXEC_TESTER_PASSIVE_CANCEL.value
    )
    assert outcome.market_close.evidence_path != outcome.passive_cancel.evidence_path
