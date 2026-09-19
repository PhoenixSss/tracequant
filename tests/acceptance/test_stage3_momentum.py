from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from nautilus_trader.model import Bar, CryptoPerpetual, Price
from nautilus_trader.trading import Strategy

from tests.acceptance import test_stage3_features as feature_fixture
from tracequant.integrations.nautilus import stage3_momentum as momentum
from tracequant.integrations.nautilus.stage3_momentum import (
    canonical_business_result,
    run_stage3_momentum_backtest,
)
from tracequant.integrations.nautilus.strategies.stage3_momentum import (
    BASE_TAKER_FEE,
    MOMENTUM_THRESHOLD,
    Stage3MomentumError,
    Stage3MomentumParameters,
    Stage3MomentumStrategy,
    momentum_signal,
    target_quantity,
)
from tracequant.research.stage3_features import (
    FEATURE_SCHEMA_DIGEST,
    HOUR_NS,
    Stage3Config,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_INSTRUMENT_IDS,
    datetime_to_nanos,
)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _accepted_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path, Stage3Config, Path]:
    catalog, record_path, record, snapshot, artifact_lock = (
        feature_fixture._build_accepted_catalog(tmp_path)
    )
    template = feature_fixture._bind_fixture_identity(
        monkeypatch, record, snapshot, artifact_lock
    )
    monkeypatch.setattr(
        momentum, "STAGE3_MOMENTUM_START", _iso(feature_fixture.EVALUATION_START)
    )
    monkeypatch.setattr(
        momentum, "STAGE3_MOMENTUM_END", _iso(feature_fixture.EVALUATION_END)
    )
    return catalog, record_path, template, artifact_lock


def test_stage3_momentum_runs_from_stage2_catalog_with_nautilus_accounting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, record_path, template, artifact_lock = _accepted_fixture(
        monkeypatch, tmp_path
    )
    first_config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "run-a",
        artifact_lock_path=artifact_lock,
    )
    second_config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "run-b",
        artifact_lock_path=artifact_lock,
    )

    first = run_stage3_momentum_backtest(
        first_config, acceptance_record_path=record_path
    )
    second = run_stage3_momentum_backtest(
        second_config, acceptance_record_path=record_path
    )

    assert issubclass(Stage3MomentumStrategy, Strategy)
    assert first.reports.decisions
    assert first.reports.orders
    assert first.reports.fills
    assert first.reports.positions
    assert first.reports.account
    assert first.reports.result
    assert first.reports.terminal["open_order_count"] == 0
    assert Decimal(cast(str, first.reports.summary["total_commission"])) > 0
    assert Decimal(cast(str, first.reports.summary["total_funding"])) != 0
    assert all(item["status"] == "FILLED" for item in first.reports.orders)
    assert all(
        cast(int, item["fill_ts"]) == cast(int, item["expected_fill_ts"])
        and cast(int, item["fill_ts"]) > cast(int, item["decision_ts"])
        for item in first.reports.associations
    )
    for fill in first.reports.fills:
        commission = Decimal(cast(str, fill["commission"]).split()[0])
        expected = (
            Decimal(cast(str, fill["filled_qty"]))
            * Decimal(cast(str, fill["avg_px"]))
            * BASE_TAKER_FEE
        ).quantize(Decimal("0.00000001"))
        assert commission == expected

    manifest = cast(
        dict[str, object],
        json.loads((first.partition / "manifest.json").read_text(encoding="utf-8")),
    )
    parameters = cast(dict[str, object], manifest["parameters"])
    assert parameters["lookback_hours"] == 24
    assert parameters["threshold"] == "0.005"
    assert parameters["target_notional_usdt"] == "10000"
    assert parameters["oms_type"] == "NETTING"
    assert manifest["feature_schema_digest"] == FEATURE_SCHEMA_DIGEST
    assert manifest["requirements_baseline"] == {
        "base_sha": momentum.STAGE3_REQUIREMENTS_BASE_SHA,
        "blob_sha": momentum.STAGE3_REQUIREMENTS_BLOB_SHA,
        "path": momentum.STAGE3_REQUIREMENTS_RELATIVE_PATH,
    }
    fee_provenance = cast(
        list[dict[str, object]],
        json.loads(
            (first.partition / "fee-provenance.json").read_text(encoding="utf-8")
        ),
    )
    assert {item["instrument_id"] for item in fee_provenance} == set(
        STAGE2_INSTRUMENT_IDS
    )
    assert all(
        item["effective_maker_fee"] == "0.0002"
        and item["effective_taker_fee"] == "0.0004"
        and item["catalog_matches_snapshot"] is True
        for item in fee_provenance
    )
    assert {
        "account.json",
        "associations.json",
        "decisions.json",
        "fee-provenance.json",
        "fills.json",
        "funding.json",
        "manifest.json",
        "orders.json",
        "positions.json",
        "result.json",
        "stage3_partition_identity.json",
        "summary.json",
        "terminal.json",
    } == {item.name for item in first.partition.iterdir()}
    assert first.result_digest == second.result_digest
    assert first.run_identity == second.run_identity
    assert canonical_business_result(first) == canonical_business_result(second)

    with pytest.raises(Stage3MomentumError, match="new empty identity partition"):
        run_stage3_momentum_backtest(first_config, acceptance_record_path=record_path)


def test_momentum_state_machine_covers_long_flat_short_and_reversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, _record_path, _template, _artifact_lock = _accepted_fixture(
        monkeypatch, tmp_path
    )
    loaded = momentum._load_native_stage3_data(
        catalog,
        start=feature_fixture.DATASET_START,
        end=feature_fixture.EVALUATION_END,
    )
    rewritten: list[Bar] = []
    counts = {value: 0 for value in STAGE2_INSTRUMENT_IDS}
    for bar in loaded.bars:
        instrument_id = str(bar.bar_type.instrument_id)
        index = counts[instrument_id]
        counts[instrument_id] += 1
        if index < 172:
            close = Decimal("100") + Decimal(index) * Decimal("0.20")
        elif index < 210:
            close = Decimal("80")
        else:
            close = Decimal("80") - Decimal(index - 210) * Decimal("0.05")
        price = Price.from_str(f"{close:.2f}")
        rewritten.append(
            Bar(
                bar_type=bar.bar_type,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=bar.volume,
                ts_event=bar.ts_event,
                ts_init=bar.ts_init,
            )
        )
    parameters = Stage3MomentumParameters(
        evaluation_start_ns=datetime_to_nanos(feature_fixture.EVALUATION_START),
        evaluation_end_ns=datetime_to_nanos(feature_fixture.EVALUATION_END),
    )
    reports = momentum._run_engine(replace(loaded, bars=tuple(rewritten)), parameters)

    assert {item["signal"] for item in reports.decisions} == {
        "long",
        "flat",
        "short",
    }
    reversals = [
        item for item in reports.decisions if item["action"] == "reversal_close"
    ]
    assert reversals
    for reversal in reversals:
        later_opens = [
            item
            for item in reports.decisions
            if item["instrument_id"] == reversal["instrument_id"]
            and cast(int, item["decision_ts"]) > cast(int, reversal["decision_ts"])
            and item["signal"] == reversal["signal"]
            and item["action"] == "submit_delta"
            and item.get("current_qty_at_execution") == "0"
        ]
        assert later_opens
    assert any(
        item["action"] == "submit_delta"
        and item["current_qty"] != "0"
        and item["signal"] in {"long", "short"}
        for item in reports.decisions
    )
    assert all(
        cast(int, item["fill_ts"]) == cast(int, item["decision_ts"]) + HOUR_NS
        for item in reports.associations
    )
    assert reports.terminal["open_order_count"] == 0


def test_momentum_signal_and_decimal_sizing_are_strictly_frozen() -> None:
    instrument = feature_fixture._perpetual(feature_fixture.BTC, "BTCUSDT", "BTC")
    assert momentum_signal(0.005, MOMENTUM_THRESHOLD) == "flat"
    assert momentum_signal(-0.005, MOMENTUM_THRESHOLD) == "flat"
    assert momentum_signal(0.0050001, MOMENTUM_THRESHOLD) == "long"
    assert momentum_signal(-0.0050001, MOMENTUM_THRESHOLD) == "short"
    assert target_quantity(
        signal="long",
        close=Decimal("33333.33"),
        instrument=instrument,
        target_notional=Decimal("10000"),
    ) == Decimal("0.300")
    assert target_quantity(
        signal="short",
        close=Decimal("33333.33"),
        instrument=instrument,
        target_notional=Decimal("10000"),
    ) == Decimal("-0.300")
    assert (
        target_quantity(
            signal="flat",
            close=Decimal("33333.33"),
            instrument=instrument,
            target_notional=Decimal("10000"),
        )
        == 0
    )

    payload = instrument.to_dict()
    payload["min_quantity"] = "1.000"
    constrained = CryptoPerpetual.from_dict(payload)
    with pytest.raises(Stage3MomentumError, match="below instrument minimum"):
        target_quantity(
            signal="long",
            close=Decimal("33333.33"),
            instrument=constrained,
            target_notional=Decimal("10000"),
        )
