from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nautilus_trader.model import Bar, BarType, Price, Quantity

from tracequant.integrations.nautilus.stage1_btcusdt import (
    bars_checksum,
    build_stage1_binance_data_client_config,
    prepare_selected_btcusdt_history,
    read_selected_btcusdt_history,
    validate_selected_btcusdt_bars,
)
from tracequant.source_data.stage1_btcusdt import (
    HOUR_NS,
    STAGE1_BAR_TYPE,
    STAGE1_DATA_PATH,
    STAGE1_SOURCE_URL,
    Stage1DataError,
    closed_bar_ts_event,
    request_segments,
    stage1_window,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STAGE1_SOURCE = (
    REPOSITORY_ROOT / "src/tracequant/integrations/nautilus/stage1_btcusdt.py"
)
FETCHED_AT = datetime(2026, 9, 13, 12, 36, 22, tzinfo=UTC)


def _bar(bar_type: BarType, ts_event: int, close: str = "100.00") -> Bar:
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


def _bars_for(
    window_start: datetime,
    window_end: datetime,
    *,
    bar_type: BarType | None = None,
) -> list[Bar]:
    selected = bar_type or BarType.from_str(STAGE1_BAR_TYPE)
    ts_event = closed_bar_ts_event(window_start)
    last = closed_bar_ts_event(window_end - timedelta(hours=1))
    bars: list[Bar] = []
    while ts_event <= last:
        bars.append(_bar(selected, ts_event))
        ts_event += HOUR_NS
    return bars


def test_selected_btcusdt_history_reaches_nautilus_catalog(tmp_path: Path) -> None:
    window_start, window_end = stage1_window()
    source_bars = _bars_for(window_start, window_end)

    catalog_bars, provenance = prepare_selected_btcusdt_history(
        tmp_path,
        bars=source_bars,
        window_start=window_start,
        window_end=window_end,
        fetched_at=FETCHED_AT,
    )

    assert provenance.data_path == STAGE1_DATA_PATH
    assert provenance.source_url == STAGE1_SOURCE_URL
    assert provenance.bar_type == STAGE1_BAR_TYPE
    assert provenance.bar_count == 2160
    assert len(catalog_bars) == provenance.bar_count
    assert [bar.bar_type for bar in catalog_bars] == [
        BarType.from_str(STAGE1_BAR_TYPE)
    ] * provenance.bar_count
    event_times = [int(bar.ts_event) for bar in catalog_bars]
    assert event_times == sorted(event_times)
    assert (
        event_times[0] == provenance.first_ts_event == closed_bar_ts_event(window_start)
    )
    assert (
        event_times[-1]
        == provenance.last_ts_event
        == closed_bar_ts_event(window_end - timedelta(hours=1))
    )

    reread, loaded = read_selected_btcusdt_history(tmp_path)
    assert [int(bar.ts_event) for bar in reread] == event_times
    assert loaded.checksum_sha256 == provenance.checksum_sha256
    assert bars_checksum(reread) == provenance.checksum_sha256


def test_checksum_mismatch_rejects_catalog_write(tmp_path: Path) -> None:
    window_start, window_end = stage1_window()
    source_bars = _bars_for(window_start, window_end)

    with pytest.raises(Stage1DataError, match="checksum"):
        prepare_selected_btcusdt_history(
            tmp_path,
            bars=source_bars,
            window_start=window_start,
            window_end=window_end,
            expected_checksum="0" * 64,
            fetched_at=FETCHED_AT,
        )


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ("duplicate", "strictly increasing|non-hourly gap|duplicate"),
        ("reverse", "strictly increasing"),
        ("gap", "non-hourly gap"),
        ("wrong_symbol", "bar type, symbol, or interval"),
    ],
)
def test_invalid_bars_are_not_written(tmp_path: Path, mutate: str, match: str) -> None:
    start = datetime(2026, 6, 15, tzinfo=UTC)
    end = datetime(2026, 6, 15, 3, tzinfo=UTC)
    bars = _bars_for(start, end)
    if mutate == "duplicate":
        bars[1] = _bar(bars[1].bar_type, int(bars[0].ts_event))
    elif mutate == "reverse":
        bars[1] = _bar(bars[1].bar_type, int(bars[0].ts_event) - HOUR_NS)
    elif mutate == "gap":
        bars[1] = _bar(bars[1].bar_type, int(bars[1].ts_event) + HOUR_NS)
    else:
        bars[1] = _bar(
            BarType.from_str("ETHUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"),
            int(bars[1].ts_event),
        )

    with pytest.raises(Stage1DataError, match=match):
        validate_selected_btcusdt_bars(bars, window_start=start, window_end=end)


def test_existing_catalog_partition_is_immutable(tmp_path: Path) -> None:
    window_start, window_end = stage1_window()
    source_bars = _bars_for(window_start, window_end)
    prepare_selected_btcusdt_history(
        tmp_path,
        bars=source_bars,
        window_start=window_start,
        window_end=window_end,
        fetched_at=FETCHED_AT,
    )
    with pytest.raises(Stage1DataError, match="already exists"):
        prepare_selected_btcusdt_history(
            tmp_path,
            bars=source_bars,
            window_start=window_start,
            window_end=window_end,
            fetched_at=FETCHED_AT,
        )


def test_relative_catalog_root_is_rejected() -> None:
    window_start, window_end = stage1_window()
    with pytest.raises(Stage1DataError, match="absolute"):
        prepare_selected_btcusdt_history(
            Path("catalog"),
            bars=_bars_for(window_start, window_end),
            window_start=window_start,
            window_end=window_end,
            fetched_at=FETCHED_AT,
        )


def test_nautilus_client_config_has_no_credentials() -> None:
    config = build_stage1_binance_data_client_config("http://proxy.example:8080")
    assert config.has_proxy_url
    assert STAGE1_DATA_PATH == "USE_NAUTILUS"
    segments = request_segments(*stage1_window())
    assert len(segments) == 3
    assert all((end - start).days == 30 for start, end in segments)


def test_selected_path_is_nautilus_only() -> None:
    source = STAGE1_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "nautilus_trader.adapters.binance" in imported
    assert "data.binance.vision" not in source
    assert "BinanceExecutionClientFactory" not in source
    assert "USE_BINANCE_ARCHIVE" not in source
    assert "api_key=None" in source
    assert "api_secret=None" in source
