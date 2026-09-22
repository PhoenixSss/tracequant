from __future__ import annotations

import json
from pathlib import Path

import pytest
from nautilus_trader.adapters.binance import (
    BinanceDataClientConfig,
    BinanceEnvironment,
    BinanceProductType,
)
from nautilus_trader.model import (
    AggressorSide,
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Money,
    Price,
    Quantity,
    QuoteTick,
    Symbol,
    TradeId,
    TradeTick,
)
from nautilus_trader.testkit import DataTesterConfig

from tracequant.integrations.nautilus import stage4_demo_data
from tracequant.integrations.nautilus.stage4_demo import (
    DEMO_INSTRUMENT_ID,
    NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
    NAUTILUS_DISTRIBUTION,
    NAUTILUS_UPSTREAM_COMMIT,
    NAUTILUS_VERSION,
    DeadlinePhase,
    RuntimeIdentity,
)
from tracequant.integrations.nautilus.stage4_demo_data import (
    DATA_TESTER_BUILTIN,
    Stage4DemoDataObservation,
    build_stage4_demo_data_evidence,
    build_stage4_demo_data_plan,
    write_stage4_demo_data_evidence,
)
from tracequant.integrations.nautilus.stage4_demo_evidence import (
    validate_stage4_demo_evidence,
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


def _quote(timestamp: int, instrument_id: str = DEMO_INSTRUMENT_ID) -> QuoteTick:
    return QuoteTick(
        instrument_id=InstrumentId.from_str(instrument_id),
        bid_price=Price.from_str("50000.00"),
        ask_price=Price.from_str("50000.01"),
        bid_size=Quantity.from_str("0.100"),
        ask_size=Quantity.from_str("0.200"),
        ts_event=timestamp,
        ts_init=timestamp,
    )


def _trade(timestamp: int, instrument_id: str = DEMO_INSTRUMENT_ID) -> TradeTick:
    return TradeTick(
        instrument_id=InstrumentId.from_str(instrument_id),
        price=Price.from_str("50000.00"),
        size=Quantity.from_str("0.001"),
        aggressor_side=AggressorSide.BUY,
        trade_id=TradeId("stage4-trade"),
        ts_event=timestamp,
        ts_init=timestamp,
    )


def _plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> stage4_demo_data.Stage4DemoDataPlan:
    runtime = _runtime()
    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    monkeypatch.setattr(stage4_demo_data, "capture_runtime_identity", lambda _: runtime)
    return build_stage4_demo_data_plan(
        repository_root=repository,
        expected_runtime=runtime,
        evidence_root=tmp_path / "evidence",
        batch_id="batch_abcdefghijklmnopqrstuv",
    )


def test_stage4_demo_data_plan_uses_official_tester_and_locked_instrument(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(monkeypatch, tmp_path)

    assert isinstance(plan.data_client_config, BinanceDataClientConfig)
    assert plan.data_client_config.product_type == BinanceProductType.USD_M
    assert plan.data_client_config.environment == BinanceEnvironment.DEMO
    assert plan.data_client_config.base_url_http is None
    assert plan.data_client_config.base_url_ws is None
    assert plan.data_client_config.instrument_provider.load_all is False
    assert plan.data_client_config.instrument_provider.load_ids == [DEMO_INSTRUMENT_ID]

    assert plan.builtin_actor == DATA_TESTER_BUILTIN == "DataTester"
    assert isinstance(plan.tester_config, DataTesterConfig)
    assert [str(item) for item in plan.tester_config.instrument_ids] == [
        DEMO_INSTRUMENT_ID
    ]
    assert plan.tester_config.subscribe_quotes is True
    assert plan.tester_config.subscribe_trades is True
    assert plan.tester_config.subscribe_instrument is True
    assert plan.tester_config.subscribe_book_deltas is False
    assert plan.tester_config.subscribe_book_depth is False
    assert plan.tester_config.subscribe_bars is False
    assert plan.tester_config.request_instruments is False
    assert plan.tester_config.request_quotes is False
    assert plan.tester_config.request_trades is False
    assert plan.deadline_seconds == (
        (DeadlinePhase.CONNECT, 30),
        (DeadlinePhase.READY, 60),
        (DeadlinePhase.DATA_OBSERVATION, 60),
    )


def test_stage4_demo_data_adapts_public_facts_to_external_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(monkeypatch, tmp_path)
    observed_at = 1_800_000_000_000_000_000
    observation = Stage4DemoDataObservation(
        instrument=_instrument(),
        quotes=(_quote(observed_at - 2_000_000_000),),
        trades=(_trade(observed_at - 1_000_000_000),),
        quote_count=1,
        trade_count=1,
        started_at_ns=observed_at - 3_000_000_000,
        ended_at_ns=observed_at,
        observed_at_ns=observed_at,
    )

    evidence = build_stage4_demo_data_evidence(plan, observation)
    validate_stage4_demo_evidence(evidence)
    outcome = write_stage4_demo_data_evidence(plan, evidence)

    assert evidence["result"] == "PASS"
    assert evidence["terminal_state"] == "COMPLETE"
    assert evidence["failure"] is None
    observations = evidence["observations"]
    assert isinstance(observations, dict)
    assert observations["market_data"] == {
        "classification": "consistent",
        "quote_count": 1,
        "trade_count": 1,
        "timestamp_valid": True,
    }
    for name in ("order", "fill", "position", "balance", "account_mode"):
        value = observations[name]
        assert isinstance(value, dict)
        assert value["classification"] == "not_applicable"
    assert evidence["cleanup"] == {
        "classification": "not_applicable",
        "active_order_count": 0,
        "pending_order_count": 0,
        "open_position_count": 0,
        "unresolved_unknown_count": 0,
        "final_net_quantity": "0",
    }
    assert outcome.evidence_partition.parent.parent == (tmp_path / "evidence").resolve()
    assert json.loads(outcome.evidence_path.read_text("utf-8")) == evidence
    rendered = outcome.evidence_path.read_text("utf-8")
    assert str(plan.repository_root) not in rendered
    assert "credential" not in rendered.lower()


@pytest.mark.parametrize(
    ("quotes", "trades", "expected_code"),
    [
        ((), (), "DATA_TIMEOUT"),
        (
            (_quote(1_800_000_002_000_000_000),),
            (_trade(1_800_000_000_000_000_000),),
            "DATA_INVALID",
        ),
        (
            (
                _quote(1_800_000_000_000_000_000),
                _quote(1_799_999_999_000_000_000),
            ),
            (_trade(1_800_000_000_000_000_000),),
            "DATA_INVALID",
        ),
        (
            (_quote(1_800_000_000_000_000_000, "ETHUSDT-PERP.BINANCE"),),
            (_trade(1_800_000_000_000_000_000),),
            "DATA_INVALID",
        ),
    ],
)
def test_stage4_demo_data_fails_closed_on_missing_wrong_or_invalid_streams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    quotes: tuple[QuoteTick, ...],
    trades: tuple[TradeTick, ...],
    expected_code: str,
) -> None:
    plan = _plan(monkeypatch, tmp_path)
    observed_at = 1_800_000_000_000_000_000
    observation = Stage4DemoDataObservation(
        instrument=_instrument(),
        quotes=quotes,
        trades=trades,
        quote_count=len(quotes),
        trade_count=len(trades),
        started_at_ns=observed_at - 1_000_000_000,
        ended_at_ns=observed_at,
        observed_at_ns=observed_at,
    )

    evidence = build_stage4_demo_data_evidence(plan, observation)
    validate_stage4_demo_evidence(evidence)

    assert evidence["result"] == "FAIL"
    assert evidence["terminal_state"] == "HALTED"
    failure = evidence["failure"]
    assert isinstance(failure, dict)
    assert failure["code"] == expected_code


def test_stage4_demo_data_rejects_runtime_drift_and_partition_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(stage4_demo_data, "capture_runtime_identity", lambda _: runtime)
    with pytest.raises(stage4_demo_data.Stage4DemoDataError, match="drifted"):
        build_stage4_demo_data_plan(
            repository_root=repository,
            expected_runtime=RuntimeIdentity(
                **{**runtime.__dict__, "source_commit": "3" * 40}
            ),
            evidence_root=tmp_path / "evidence",
            batch_id="batch_abcdefghijklmnopqrstuv",
        )

    plan = _plan(monkeypatch, tmp_path / "fresh")
    plan.evidence_partition.mkdir(parents=True)
    with pytest.raises(stage4_demo_data.Stage4DemoDataError, match="already exists"):
        write_stage4_demo_data_evidence(plan, {})
