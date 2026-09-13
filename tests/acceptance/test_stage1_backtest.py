from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.model import Bar, BarType, Price, Quantity
from nautilus_trader.trading import Strategy

from tracequant.integrations.nautilus.stage1_backtest import (
    LIVE_NOT_APPROVED,
    OFFLINE_BACKTEST_ONLY,
    STAGE1_STARTING_USDT,
    Stage1BacktestError,
    canonical_business_result,
    run_stage1_offline_backtest,
)
from tracequant.integrations.nautilus.stage1_btcusdt import (
    prepare_selected_btcusdt_history,
)
from tracequant.integrations.nautilus.strategies.stage1_ma_cross import Stage1MaCross
from tracequant.source_data.stage1_btcusdt import (
    HOUR_NS,
    STAGE1_BAR_TYPE,
    closed_bar_ts_event,
    stage1_window,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STAGE1_BACKTEST_SOURCE = (
    REPOSITORY_ROOT / "src/tracequant/integrations/nautilus/stage1_backtest.py"
)
STAGE1_STRATEGY_SOURCE = (
    REPOSITORY_ROOT
    / "src/tracequant/integrations/nautilus/strategies/stage1_ma_cross.py"
)
FETCHED_AT = datetime(2026, 9, 13, 12, 36, 22, tzinfo=UTC)


def _bar(bar_type: BarType, ts_event: int, close: str) -> Bar:
    price = Price.from_str(close)
    return Bar(
        bar_type=bar_type,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_str("1.000"),
        ts_event=ts_event,
        ts_init=ts_event,
    )


def _crossing_bars(window_start: datetime, window_end: datetime) -> list[Bar]:
    bar_type = BarType.from_str(STAGE1_BAR_TYPE)
    first = closed_bar_ts_event(window_start)
    last = closed_bar_ts_event(window_end - timedelta(hours=1))
    count = int((last - first) / HOUR_NS) + 1
    bars: list[Bar] = []
    ts_event = first
    index = 0
    while ts_event <= last:
        bars.append(_bar(bar_type, ts_event, _crossing_close(index, count)))
        ts_event += HOUR_NS
        index += 1
    return bars


def _crossing_close(index: int, count: int) -> str:
    third = max(count // 3, 1)
    if index < third:
        close = 100.0 - (index / third) * 20.0
    elif index < 2 * third:
        close = 80.0 + ((index - third) / third) * 40.0
    else:
        remaining = max(count - 2 * third, 1)
        close = 120.0 - ((index - 2 * third) / remaining) * 30.0
    return f"{round(close * 10) / 10:.2f}"


def _prepare_catalog(catalog_root: Path) -> None:
    window_start, window_end = stage1_window()
    prepare_selected_btcusdt_history(
        catalog_root,
        bars=_crossing_bars(window_start, window_end),
        window_start=window_start,
        window_end=window_end,
        fetched_at=FETCHED_AT,
    )


def test_stage1_native_strategy_completes_offline_backtest(tmp_path: Path) -> None:
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    _prepare_catalog(catalog_root)
    first_root = tmp_path / "run-a"
    second_root = tmp_path / "run-b"
    first_root.mkdir()
    second_root.mkdir()

    first = run_stage1_offline_backtest(catalog_root, first_root)
    second = run_stage1_offline_backtest(catalog_root, second_root)

    assert issubclass(Stage1MaCross, Strategy)
    assert first.reports.orders
    assert first.reports.fills
    assert first.reports.positions
    assert first.reports.account
    assert first.reports.summary
    sides = [str(record["side"]) for record in first.reports.fills]
    assert "BUY" in sides
    assert "SELL" in sides
    assert first.reports.summary["orders.open"] == "0"
    assert first.reports.summary["positions.open"] == "0"
    assert all(record["status"] == "FILLED" for record in first.reports.orders)
    assert all(record["side"] == "FLAT" for record in first.reports.positions)
    assert all(record["ts_closed"] is not None for record in first.reports.positions)

    starting = Decimal(STAGE1_STARTING_USDT)
    ending = Decimal(str(first.reports.account["total"]).split()[0])
    realized = sum(
        (
            Decimal(str(record["realized_pnl"]).split()[0])
            for record in first.reports.positions
            if record["realized_pnl"] is not None
        ),
        Decimal("0"),
    )
    commissions = sum(
        (
            Decimal(str(record["commission"]).split()[0])
            for record in first.reports.fills
            if record["commission"] is not None
        ),
        Decimal("0"),
    )
    assert ending - starting == realized
    assert commissions > 0
    assert canonical_business_result(first) == canonical_business_result(second)
    assert (first.partition / "orders.json").is_file()
    assert (first.partition / "fills.json").is_file()
    assert (first.partition / "positions.json").is_file()
    assert (first.partition / "account.json").is_file()
    assert (first.partition / "summary.json").is_file()
    assert OFFLINE_BACKTEST_ONLY is True
    assert LIVE_NOT_APPROVED is True


def test_relative_run_root_is_rejected(tmp_path: Path) -> None:
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    _prepare_catalog(catalog_root)
    with pytest.raises(Stage1BacktestError, match="absolute"):
        run_stage1_offline_backtest(catalog_root, Path("runs"))


def test_mismatched_nautilus_version_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_root = tmp_path / "catalog"
    run_root = tmp_path / "runs"
    catalog_root.mkdir()
    run_root.mkdir()
    monkeypatch.setattr(
        "tracequant.integrations.nautilus.stage1_backtest.distribution_version",
        lambda: "0.0.0",
    )
    with pytest.raises(Stage1BacktestError, match="version"):
        run_stage1_offline_backtest(catalog_root, run_root)


def test_existing_run_partition_is_immutable(tmp_path: Path) -> None:
    catalog_root = tmp_path / "catalog"
    run_root = tmp_path / "runs"
    catalog_root.mkdir()
    run_root.mkdir()
    _prepare_catalog(catalog_root)
    run_stage1_offline_backtest(catalog_root, run_root)
    with pytest.raises(Stage1BacktestError, match="already exists"):
        run_stage1_offline_backtest(catalog_root, run_root)


def test_stage1_backtest_path_has_no_exchange_client() -> None:
    for source_path in (STAGE1_BACKTEST_SOURCE, STAGE1_STRATEGY_SOURCE):
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert "nautilus_trader.adapters.binance" not in imported
        assert "BinanceExecutionClientFactory" not in source
        assert "LiveNode" not in source
        assert "api_key" not in source
        assert "api_secret" not in source
