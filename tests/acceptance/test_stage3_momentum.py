from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest
from nautilus_trader.model import Bar, CryptoPerpetual, Currency, Price
from nautilus_trader.trading import Strategy

from tests.acceptance import test_stage3_features as feature_fixture
from tracequant.integrations.nautilus import stage3_momentum as momentum
from tracequant.integrations.nautilus.stage3_momentum import (
    canonical_business_result,
    run_stage3_momentum_backtest,
)
from tracequant.integrations.nautilus.strategies import (
    stage3_momentum as strategy_module,
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
            Decimal(cast(str, fill["last_qty"]))
            * Decimal(cast(str, fill["last_px"]))
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
    assert manifest["fee_provenance"] == fee_provenance
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
    repeated = momentum._run_engine(replace(loaded, bars=tuple(rewritten)), parameters)

    assert {item["signal"] for item in reports.decisions} == {
        "long",
        "flat",
        "short",
    }
    reversals = [
        item for item in reports.decisions if item["action"] == "reversal_close"
    ]
    assert reversals
    decisions_by_id = {item["decision_id"]: item for item in reports.decisions}
    associations_by_order: dict[object, list[dict[str, object]]] = {}
    for association in reports.associations:
        associations_by_order.setdefault(association["order_id"], []).append(
            association
        )
    for reversal in reversals:
        open_decision = decisions_by_id[reversal["reversal_open_decision_id"]]
        assert open_decision is not reversal
        assert open_decision["action"] == "reversal_open"
        close_intent = next(
            item
            for item in cast(list[dict[str, object]], reversal["order_intents"])
            if item["leg"] == "reversal_close"
        )
        open_intent = next(
            item
            for item in cast(list[dict[str, object]], open_decision["order_intents"])
            if item["leg"] == "reversal_open"
        )
        close_fills = associations_by_order[close_intent["order_id"]]
        open_fills = associations_by_order[open_intent["order_id"]]
        flat_sequence = cast(int, reversal["flat_confirmation_sequence"])
        assert all(
            item["fill_ts"] == reversal["flat_confirmation_ts"] for item in close_fills
        )
        assert all(
            item["fill_ts"] == open_intent["expected_fill_ts"]
            and cast(int, item["fill_ts"])
            == cast(int, open_decision["decision_ts"]) + HOUR_NS
            for item in open_fills
        )
        assert cast(int, close_intent["submit_sequence"]) < cast(
            int, open_decision["decision_sequence"]
        ) < min(cast(int, item["fill_event_sequence"]) for item in close_fills) and max(
            cast(int, item["fill_event_sequence"]) for item in close_fills
        ) < flat_sequence < cast(int, open_fills[0]["order_submit_sequence"]) < min(
            cast(int, item["fill_event_sequence"]) for item in open_fills
        )
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
    closed_cycles = [item for item in reports.positions if item["is_closed"]]
    assert closed_cycles
    assert all(item["is_snapshot"] for item in closed_cycles)
    assert reports.summary["trade_count"] == len(closed_cycles)
    assert reports.summary["position_count"] == len(reports.positions)
    assert reports.positions == repeated.positions
    assert reports.summary == repeated.summary
    assert reports.terminal["open_order_count"] == 0


@pytest.mark.parametrize("status", ["INITIALIZED", "SUBMITTED", "PENDING_UPDATE"])
def test_ready_decision_is_retained_while_any_prior_order_is_unsettled(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    class UnsettledOrder:
        is_closed = False

        def __init__(self) -> None:
            self.status = status

    class UnsettledOrderCache:
        @staticmethod
        def positions_open(*, instrument_id: object) -> list[object]:
            return []

        @staticmethod
        def orders(*, instrument_id: object) -> list[object]:
            return [UnsettledOrder()]

    parameters = Stage3MomentumParameters(
        evaluation_start_ns=0,
        evaluation_end_ns=3 * HOUR_NS,
    )
    strategy = Stage3MomentumStrategy(parameters)
    instrument_id = STAGE2_INSTRUMENT_IDS[0]
    strategy._instruments[instrument_id] = feature_fixture._perpetual(
        feature_fixture.BTC, "BTCUSDT", "BTC"
    )
    setattr(strategy, "_test_cache", UnsettledOrderCache())
    monkeypatch.setattr(
        Stage3MomentumStrategy,
        "cache",
        property(lambda instance: getattr(instance, "_test_cache")),
    )

    strategy._queue_decision(
        instrument_id=instrument_id,
        decision_ts=HOUR_NS,
        close=Decimal("100"),
        ret_24h=0.01,
        signal="long",
        target=Decimal("1"),
    )

    decision = strategy.decisions[-1]
    assert strategy._pending[instrument_id] is decision
    assert decision["action"] == "queued"
    assert decision["reason"] == "awaiting_b1"
    assert decision["queue_reason"] == "unsettled_order"


def test_unknown_order_state_fails_closed() -> None:
    class UnknownOrderCache:
        @staticmethod
        def orders(*, instrument_id: object | None = None) -> list[object]:
            return [object()]

    with pytest.raises(Stage3MomentumError, match="unknown terminal state"):
        strategy_module.unsettled_orders(UnknownOrderCache())


def test_reversal_state_keeps_only_the_latest_target_before_confirmation() -> None:
    close: dict[str, object] = {"decision_id": "close", "target_qty": "1"}
    first_update: dict[str, object] = {
        "decision_id": "first",
        "target_qty": "-1",
    }
    latest_update: dict[str, object] = {
        "decision_id": "latest",
        "target_qty": "-2",
    }
    state = strategy_module._ReversalState(
        close_decision=close,
        latest_decision=close,
        close_order_id="close-order",
    )

    state.replace_target(first_update)
    state.replace_target(latest_update)

    assert state.latest_decision is latest_update
    assert close["reversal_open_decision_id"] == "latest"


def test_native_fill_contract_rejects_incomplete_order() -> None:
    decision: dict[str, object] = {
        "decision_sequence": 0,
        "decision_ts": 0,
        "order_ids": ["O-1"],
        "order_intents": [
            {
                "expected_fill_ts": HOUR_NS,
                "leg": "direct",
                "order_id": "O-1",
                "submit_sequence": 1,
            }
        ],
        "reason": "submitted",
    }
    order: dict[str, object] = {
        "client_order_id": "O-1",
        "filled_qty": "2",
        "last_event_reason": None,
        "quantity": "2",
        "status": "FILLED",
    }
    fills: list[dict[str, object]] = [
        {
            "client_order_id": "O-1",
            "commission": "0.02000000 USDT",
            "event_sequence": 2,
            "last_px": "100",
            "last_qty": "0.5",
            "ts_event": HOUR_NS,
        },
        {
            "client_order_id": "O-1",
            "commission": "0.06000000 USDT",
            "event_sequence": 3,
            "last_px": "100",
            "last_qty": "1.5",
            "ts_event": HOUR_NS,
        },
    ]

    momentum._require_fee_and_execution_contract(
        [decision], [order], fills, Currency.from_str("USDT")
    )
    incomplete = {**order, "filled_qty": "0.5", "status": "CANCELED"}
    with pytest.raises(Stage3MomentumError, match="not completely filled"):
        momentum._require_fee_and_execution_contract(
            [decision], [incomplete], fills[:1], Currency.from_str("USDT")
        )


def test_run_root_claim_is_exclusive(tmp_path: Path) -> None:
    run_root = tmp_path / "contended-run"
    barrier = Barrier(2)

    def claim() -> str:
        barrier.wait()
        try:
            momentum._claim_run_root(run_root)
        except Stage3MomentumError:
            return "rejected"
        return "claimed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: claim(), range(2)))

    assert sorted(results) == ["claimed", "rejected"]


def test_relevant_code_digest_tracks_declared_closure_only(tmp_path: Path) -> None:
    runner = tmp_path / "runner.py"
    view = tmp_path / "views.py"
    unrelated = tmp_path / "notes.md"
    runner.write_text("runner-v1", encoding="utf-8")
    view.write_text("view-v1", encoding="utf-8")
    files = (("runner.py", runner), ("views.py", view))
    original = momentum._relevant_code_digest(files)

    unrelated.write_text("unrelated-v2", encoding="utf-8")
    assert momentum._relevant_code_digest(files) == original
    view.write_text("view-v2", encoding="utf-8")
    assert momentum._relevant_code_digest(files) != original
    assert {name for name, _path in momentum._relevant_code_files()} >= {
        "integrations/nautilus/stage2_artifact.py",
        "integrations/nautilus/stage2_btceth.py",
        "research/views.py",
        "source_data/stage2_btceth.py",
    }


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
