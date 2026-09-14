from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.backtest import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestNode,
    BacktestRunConfig,
    BacktestVenueConfig,
)
from nautilus_trader.common import LoggerConfig, LogLevel
from nautilus_trader.model import (
    AccountType,
    Bar,
    CryptoPerpetual,
    Currency,
    FundingRateUpdate,
    InstrumentId,
    OmsType,
    OrderSide,
    Price,
    Quantity,
    Symbol,
    TraderId,
)
from nautilus_trader.trading import Strategy, StrategyConfig

from tracequant.integrations.nautilus import UPSTREAM_RELEASE_IDENTITY
from tracequant.integrations.nautilus.stage2_btceth import (
    bars_from_kline_rows,
    compare_loaded_windows,
    crosscheck_stage2_catalog,
    funding_rate_updates_from_rows,
    load_stage2_backtest_bars,
    loaded_window,
    main,
    prepare_stage2_combined_catalog,
    prepare_stage2_dataset,
    query_stage2_bars,
    require_no_index_or_1m_reference,
    stage2_bar_type,
    stage2_price_spec,
    stage2_sma_parity,
)
from tracequant.research.source_schema import stage2_split_bounds
from tracequant.research.views import load_bars, sma_close
from tracequant.source_data.stage2_btceth import (
    STAGE2_BAR_AGGREGATION,
    STAGE2_BAR_INTERVALS,
    STAGE2_CONFIG_ENV,
    STAGE2_CROSSCHECK_END_ISO,
    STAGE2_CROSSCHECK_START_ISO,
    STAGE2_DATA_TYPE_BARS,
    STAGE2_DATA_TYPE_FUNDING,
    STAGE2_DATA_TYPE_MARK,
    STAGE2_DATASET_ID,
    STAGE2_EXPECTED_SOURCE_COUNT,
    STAGE2_FUNDING_ROOT,
    STAGE2_GENERATION_COMMAND,
    STAGE2_INCLUDE_INDEX_PRICE,
    STAGE2_INDEX_ROOT,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INTERVAL_MS,
    STAGE2_KLINE_ROOT,
    STAGE2_MARK_GAP_EXPLANATION,
    STAGE2_MARK_INTERVAL,
    STAGE2_MARK_ROOT,
    STAGE2_NAUTILUS_TAIL_ENABLED,
    STAGE2_WINDOW_END_ISO,
    STAGE2_WINDOW_START_ISO,
    Stage2CoverageReport,
    Stage2DataError,
    Stage2FundingRow,
    Stage2KlineRow,
    Stage2SeriesCoverage,
    Stage2SourceManifest,
    build_stage2_acceptance_record,
    expected_source_inventory_digest,
    expected_stage2_source_specs,
    parse_utc,
    require_complete_acceptance_record,
    require_complete_source_inventory,
    require_no_local_absolute_paths,
    require_tail_disabled,
    stage2_bar_type_str,
    stage2_index_coverage_conclusion,
    stage2_month_keys,
    unix_millis,
    validate_funding_series,
    validate_kline_series,
    validate_mark_series,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_RECORD = (
    REPOSITORY_ROOT / "docs/product/stage2-btceth-dataset-acceptance.json"
)
FETCHED_AT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
BTC = "BTCUSDT-PERP.BINANCE"
ETH = "ETHUSDT-PERP.BINANCE"
START_MS = 1577836800000
BAR_COUNT_15M = 30
CROSSCHECK_START = parse_utc(STAGE2_CROSSCHECK_START_ISO)
ACCEPTANCE_KEYS = {
    "catalog_evidence",
    "coverage_summary",
    "crosscheck",
    "dataset_digest",
    "dataset_id",
    "expected_source_count",
    "expected_source_inventory_digest",
    "generation_command",
    "homology",
    "include_index_price",
    "index_coverage",
    "nautilus_tail_enabled",
    "nautilus_version",
    "runtime_identity",
    "schema",
    "source_manifest_digest",
    "splits",
    "verification_test",
}
SERIES_PRICES = {
    (BTC, "15m"): ("100.00", "101.00", "99.50", "100.50", "1.000"),
    (BTC, "1h"): ("200.00", "201.00", "199.50", "200.50", "2.000"),
    (BTC, "4h"): ("300.00", "301.00", "299.50", "300.50", "3.000"),
    (ETH, "15m"): ("400.00", "401.00", "399.50", "400.50", "4.000"),
    (ETH, "1h"): ("500.00", "501.00", "499.50", "500.50", "5.000"),
    (ETH, "4h"): ("600.00", "601.00", "599.50", "600.50", "6.000"),
}
MARK_PRICES = {
    BTC: ("10000.00", "10000.00", "10000.00", "10000.00", "1.000"),
    ETH: ("400.00", "401.00", "399.50", "400.50", "4.000"),
}
FUNDING_RATES = {
    BTC: (("0.0001", "8"), ("-0.0002", "4")),
    ETH: (("0.0003", "8"), ("-0.0004", "1")),
}


def _instrument(instrument_id: str, symbol: str, base: str) -> CryptoPerpetual:
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(instrument_id),
        raw_symbol=Symbol(symbol),
        base_currency=Currency.from_str(base),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
    )


def _stage2_instruments() -> tuple[CryptoPerpetual, CryptoPerpetual]:
    return (
        _instrument(BTC, "BTCUSDT", "BTC"),
        _instrument(ETH, "ETHUSDT", "ETH"),
    )


def _month_start_ms(year: int, month: int) -> int:
    return unix_millis(datetime(year, month, 1, tzinfo=UTC))


def _open_times(
    interval: str, count: int = 3, *, start_ms: int = START_MS
) -> list[int]:
    step = STAGE2_INTERVAL_MS[interval]
    return [start_ms + index * step for index in range(count)]


def _funding_times(start_ms: int = START_MS) -> tuple[int, int]:
    return start_ms + 30 * 60 * 1000 + 1, start_ms + 45 * 60 * 1000


def _always_available(_url: str) -> bool:
    return True


def _row_values(
    open_time: int,
    interval: str,
    prices: tuple[str, str, str, str, str],
) -> list[str]:
    close_time = open_time + STAGE2_INTERVAL_MS[interval] - 1
    open_px, high_px, low_px, close_px, volume = prices
    return [
        str(open_time),
        open_px,
        high_px,
        low_px,
        close_px,
        volume,
        str(close_time),
        "10.000",
        "12",
        "0.500",
        "5.000",
        "0",
    ]


def _write_zip(
    path: Path,
    rows: list[list[str]],
    *,
    header: list[str] | None,
    csv_name: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if header is not None:
        writer.writerow(header)
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(csv_name, buffer.getvalue().encode("utf-8"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(f"{path}.CHECKSUM").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def _write_kline_zip(
    raw_root: Path,
    instrument_id: str,
    interval: str,
    *,
    year: int,
    month: int,
    count: int,
    start_ms: int,
) -> None:
    symbol = "BTCUSDT" if instrument_id.startswith("BTC") else "ETHUSDT"
    name = f"{symbol}-{interval}-{year:04d}-{month:02d}.zip"
    zip_path = raw_root / STAGE2_KLINE_ROOT / symbol / interval / name
    prices = SERIES_PRICES[(instrument_id, interval)]
    payload = []
    for index, open_time in enumerate(_open_times(interval, count, start_ms=start_ms)):
        close = prices[3]
        if instrument_id == BTC and interval == "15m" and year == 2020 and month == 1:
            close = str(
                (Decimal("100.00") + Decimal("0.01") * index).quantize(Decimal("0.01"))
            )
        row_prices = (prices[0], prices[1], prices[2], close, prices[4])
        payload.append(_row_values(open_time, interval, row_prices))
    _write_zip(
        zip_path,
        payload,
        header=None,
        csv_name=f"{symbol}-{interval}-{year:04d}-{month:02d}.csv",
    )


def _write_mark_zip(
    raw_root: Path,
    instrument_id: str,
    *,
    year: int,
    month: int,
    start_ms: int,
    count: int = 3,
) -> None:
    symbol = "BTCUSDT" if instrument_id.startswith("BTC") else "ETHUSDT"
    name = f"{symbol}-{STAGE2_MARK_INTERVAL}-{year:04d}-{month:02d}.zip"
    zip_path = raw_root / STAGE2_MARK_ROOT / symbol / STAGE2_MARK_INTERVAL / name
    prices = MARK_PRICES[instrument_id]
    payload = [
        _row_values(open_time, STAGE2_MARK_INTERVAL, prices)
        for open_time in _open_times(STAGE2_MARK_INTERVAL, count, start_ms=start_ms)
    ]
    _write_zip(
        zip_path,
        payload,
        header=None,
        csv_name=f"{symbol}-{STAGE2_MARK_INTERVAL}-{year:04d}-{month:02d}.csv",
    )


def _write_funding_zip(
    raw_root: Path,
    instrument_id: str,
    *,
    year: int,
    month: int,
    start_ms: int,
) -> None:
    symbol = "BTCUSDT" if instrument_id.startswith("BTC") else "ETHUSDT"
    name = f"{symbol}-fundingRate-{year:04d}-{month:02d}.zip"
    zip_path = raw_root / STAGE2_FUNDING_ROOT / symbol / name
    rates = FUNDING_RATES[instrument_id]
    times = _funding_times(start_ms)
    payload = [
        [str(times[0]), rates[0][1], rates[0][0]],
        [str(times[1]), rates[1][1], rates[1][0]],
    ]
    _write_zip(
        zip_path,
        payload,
        header=None,
        csv_name=f"{symbol}-fundingRate-{year:04d}-{month:02d}.csv",
    )


def _series_start_ms(year: int, month: int) -> int:
    if year == 2026 and month == 8:
        return unix_millis(CROSSCHECK_START)
    return _month_start_ms(year, month)


def _write_all_series(raw_root: Path) -> None:
    for year, month in stage2_month_keys():
        start_ms = _series_start_ms(year, month)
        for instrument_id in STAGE2_INSTRUMENT_IDS:
            for interval in STAGE2_BAR_INTERVALS:
                if interval == "15m" and year == 2020 and month == 1:
                    count = BAR_COUNT_15M
                elif interval == "1h" and year == 2026 and month == 8:
                    count = 7 * 24
                elif interval == "15m":
                    count = 3
                else:
                    count = 2
                _write_kline_zip(
                    raw_root,
                    instrument_id,
                    interval,
                    year=year,
                    month=month,
                    count=count,
                    start_ms=start_ms,
                )
            _write_mark_zip(
                raw_root,
                instrument_id,
                year=year,
                month=month,
                start_ms=start_ms,
            )
            _write_funding_zip(
                raw_root,
                instrument_id,
                year=year,
                month=month,
                start_ms=start_ms,
            )


def _config_text(raw_root: Path, catalog_path: Path) -> str:
    instrument_ids = ", ".join(f'"{item}"' for item in STAGE2_INSTRUMENT_IDS)
    bar_intervals = ", ".join(f'"{item}"' for item in STAGE2_BAR_INTERVALS)
    return (
        "\n".join(
            [
                'schema = "tracequant-stage2-dataset-v1"',
                f'dataset_id = "{STAGE2_DATASET_ID}"',
                'nautilus_version = "2.0.0rc4"',
                'environment = "offline"',
                'source = "binance-public-data"',
                'market = "futures/um"',
                'archive_frequency = "monthly"',
                f'window_start = "{STAGE2_WINDOW_START_ISO}"',
                f'window_end = "{STAGE2_WINDOW_END_ISO}"',
                f"instrument_ids = [{instrument_ids}]",
                f"bar_intervals = [{bar_intervals}]",
                f'bar_aggregation = "{STAGE2_BAR_AGGREGATION}"',
                f'raw_root = "{raw_root}"',
                f'catalog_path = "{catalog_path}"',
            ]
        )
        + "\n"
    )


def _kline_rows(
    instrument_id: str,
    interval: str,
    count: int,
    start_ms: int,
) -> tuple[Stage2KlineRow, ...]:
    prices = SERIES_PRICES[(instrument_id, interval)]
    rows: list[Stage2KlineRow] = []
    for index, open_time in enumerate(_open_times(interval, count, start_ms=start_ms)):
        close = prices[3]
        if instrument_id == BTC and interval == "15m" and start_ms == START_MS:
            close = str(
                (Decimal("100.00") + Decimal("0.01") * index).quantize(Decimal("0.01"))
            )
        values = _row_values(
            open_time, interval, (prices[0], prices[1], prices[2], close, prices[4])
        )
        rows.append(
            Stage2KlineRow(
                open_time=values[0],
                open=values[1],
                high=values[2],
                low=values[3],
                close=values[4],
                volume=values[5],
                close_time=values[6],
                quote_volume=values[7],
                count=values[8],
                taker_buy_volume=values[9],
                taker_buy_quote_volume=values[10],
                ignore=values[11],
            )
        )
    return tuple(rows)


def _independent_crosscheck() -> tuple[
    dict[str, tuple[Bar, ...]],
    dict[str, tuple[FundingRateUpdate, ...]],
]:
    start_ms = unix_millis(CROSSCHECK_START)
    bars: dict[str, tuple[Bar, ...]] = {}
    funding: dict[str, tuple[FundingRateUpdate, ...]] = {}
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        rows = _kline_rows(instrument_id, "1h", 7 * 24, start_ms)
        bars[instrument_id] = bars_from_kline_rows(
            rows, instrument_id=instrument_id, interval="1h"
        )
        times = _funding_times(start_ms)
        rates = FUNDING_RATES[instrument_id]
        funding[instrument_id] = funding_rate_updates_from_rows(
            (
                Stage2FundingRow(str(times[0]), rates[0][1], rates[0][0]),
                Stage2FundingRow(str(times[1]), rates[1][1], rates[1][0]),
            ),
            instrument_id=instrument_id,
        )
    return bars, funding


def _write_config(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    _write_all_series(raw_root)
    config_path = tmp_path / "dataset.toml"
    config_path.write_text(_config_text(raw_root, catalog_path), encoding="utf-8")
    return config_path, catalog_path, raw_root


def _allow_sparse_archive_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the 800-file orchestration fixture small; gap behavior is tested directly."""

    def _kline_coverage(
        rows: tuple[Stage2KlineRow, ...] | list[Stage2KlineRow],
        *,
        instrument_id: str,
        interval: str,
        source_checksum: str,
    ) -> Stage2SeriesCoverage:
        return Stage2SeriesCoverage(
            instrument_id=instrument_id,
            data_type=STAGE2_DATA_TYPE_BARS,
            bar_interval=interval,
            row_count=len(rows),
            first_ts_event=int(rows[0].close_time) * 1_000_000,
            last_ts_event=int(rows[-1].close_time) * 1_000_000,
            duplicate_count=0,
            out_of_order_count=0,
            gap_count=0,
            source_checksum=source_checksum,
        )

    def _mark_coverage(
        rows: tuple[Stage2KlineRow, ...] | list[Stage2KlineRow],
        *,
        instrument_id: str,
        source_checksum: str,
    ) -> Stage2SeriesCoverage:
        coverage = _kline_coverage(
            rows,
            instrument_id=instrument_id,
            interval=STAGE2_MARK_INTERVAL,
            source_checksum=source_checksum,
        )
        return Stage2SeriesCoverage(
            instrument_id=coverage.instrument_id,
            data_type=STAGE2_DATA_TYPE_MARK,
            bar_interval=coverage.bar_interval,
            row_count=coverage.row_count,
            first_ts_event=coverage.first_ts_event,
            last_ts_event=coverage.last_ts_event,
            duplicate_count=coverage.duplicate_count,
            out_of_order_count=coverage.out_of_order_count,
            gap_count=coverage.gap_count,
            source_checksum=coverage.source_checksum,
        )

    def _funding_coverage(
        rows: tuple[Stage2FundingRow, ...] | list[Stage2FundingRow],
        *,
        instrument_id: str,
        source_checksum: str,
    ) -> Stage2SeriesCoverage:
        return Stage2SeriesCoverage(
            instrument_id=instrument_id,
            data_type=STAGE2_DATA_TYPE_FUNDING,
            bar_interval="",
            row_count=len(rows),
            first_ts_event=int(rows[0].calc_time) * 1_000_000,
            last_ts_event=int(rows[-1].calc_time) * 1_000_000,
            duplicate_count=0,
            out_of_order_count=0,
            gap_count=0,
            source_checksum=source_checksum,
        )

    module = "tracequant.integrations.nautilus.stage2_btceth"
    monkeypatch.setattr(f"{module}.validate_kline_series", _kline_coverage)
    monkeypatch.setattr(f"{module}.validate_mark_series", _mark_coverage)
    monkeypatch.setattr(f"{module}.validate_funding_series", _funding_coverage)
    monkeypatch.setattr(
        f"{module}.discover_stage2_mark_gap_fill_archives", lambda _config: ()
    )


def _prepare_catalog(
    tmp_path: Path,
) -> tuple[Path, Stage2SourceManifest, Stage2CoverageReport]:
    config_path, catalog_path, _raw_root = _write_config(tmp_path)
    manifest, coverage, written = prepare_stage2_combined_catalog(
        config_path,
        repository_root=REPOSITORY_ROOT,
        instruments=_stage2_instruments(),
        fetched_at=FETCHED_AT,
    )
    assert written == catalog_path
    return catalog_path, manifest, coverage


class _OpenLong(Strategy):
    def __init__(self) -> None:
        super().__init__(
            StrategyConfig(oms_type=OmsType.NETTING, use_uuid_client_order_ids=False)
        )
        self._instrument_id = InstrumentId.from_str(BTC)
        self._bar_type = stage2_bar_type(BTC, STAGE2_MARK_INTERVAL)
        self._opened = False
        self.funding_seen: list[tuple[int, str]] = []

    def on_start(self) -> None:
        self.subscribe_bars(self._bar_type)
        self.subscribe_mark_prices(self._instrument_id)
        self.subscribe_funding_rates(self._instrument_id)

    def on_bar(self, bar: Bar) -> None:
        if self._opened:
            return
        self.submit_order(
            self.order_factory.market(
                self._instrument_id, OrderSide.BUY, Quantity.from_str("1.000")
            )
        )
        self._opened = True

    def on_funding_rate(self, event: FundingRateUpdate) -> None:
        self.funding_seen.append((int(event.ts_event), str(event.rate)))


def _run_settlement(
    catalog_path: Path, strategy: Strategy, *, include_funding: bool
) -> dict[str, str]:
    usdt = Currency.from_str("USDT")
    venue = BacktestVenueConfig(
        name="BINANCE",
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=["100000 USDT"],
        base_currency=usdt,
        default_leverage=Decimal("1"),
        bar_execution=True,
        use_random_ids=False,
    )
    engine_cfg = BacktestEngineConfig(
        trader_id=TraderId.from_str("TRACEQUANT-001"),
        bypass_logging=True,
        run_analysis=False,
        logging=LoggerConfig(bypass_logging=True, stdout_level=LogLevel.OFF),
    )
    data = [
        BacktestDataConfig(
            data_type="Bar",
            catalog_path=str(catalog_path),
            bar_types=[stage2_bar_type_str(BTC, STAGE2_MARK_INTERVAL)],
        ),
        BacktestDataConfig(
            data_type="MarkPriceUpdate",
            catalog_path=str(catalog_path),
            instrument_id=InstrumentId.from_str(BTC),
        ),
    ]
    if include_funding:
        data.append(
            BacktestDataConfig(
                data_type="FundingRateUpdate",
                catalog_path=str(catalog_path),
                instrument_id=InstrumentId.from_str(BTC),
            )
        )
    run_cfg = BacktestRunConfig(
        venues=[venue],
        data=data,
        engine=engine_cfg,
        dispose_on_completion=True,
    )
    node = BacktestNode([run_cfg])
    node.build()
    node.add_strategy(run_cfg.id, strategy)
    results = node.run()
    return dict(results[0].summary)


def test_frozen_stage2_dataset_drives_research_and_backtest_from_one_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _allow_sparse_archive_fixture(monkeypatch)
    config_path, catalog_path, _raw_root = _write_config(tmp_path)
    independent_bars, independent_funding = _independent_crosscheck()
    record = prepare_stage2_dataset(
        config_path,
        repository_root=REPOSITORY_ROOT,
        instruments=_stage2_instruments(),
        fetched_at=FETCHED_AT,
        fetch_crosscheck=lambda: (independent_bars, independent_funding),
        checksum_probe=_always_available,
        acceptance_path=tmp_path / "acceptance.json",
    )
    manifest = json.loads(
        (catalog_path / "stage2_source_manifest.json").read_text(encoding="utf-8")
    )
    source_urls = [item["source_url"] for item in manifest["sources"]]
    require_complete_source_inventory(source_urls)
    assert len(source_urls) == STAGE2_EXPECTED_SOURCE_COUNT == 800
    assert manifest["dataset_id"] == STAGE2_DATASET_ID
    require_no_index_or_1m_reference(catalog_path)
    require_tail_disabled()
    assert STAGE2_NAUTILUS_TAIL_ENABLED is False
    assert STAGE2_INCLUDE_INDEX_PRICE is False
    index = record["index_coverage"]
    assert isinstance(index, dict)
    assert index["downloaded"] is False
    assert index["written_to_catalog"] is False
    assert index["checksum_available"] is True
    assert index["object_count"] == 160
    assert index["month_count"] == 80
    assert "objects" in index
    assert len(expected_stage2_source_specs()) == 800
    assert len(stage2_month_keys()) == 80
    assert ACCEPTANCE_KEYS <= set(record)
    require_no_local_absolute_paths(record)
    assert record["expected_source_count"] == 800
    assert record["generation_command"] == STAGE2_GENERATION_COMMAND
    assert record["runtime_identity"] == UPSTREAM_RELEASE_IDENTITY
    crosscheck = record["crosscheck"]
    assert isinstance(crosscheck, dict)
    assert crosscheck["written_to_catalog"] is False
    assert crosscheck["start"] == STAGE2_CROSSCHECK_START_ISO
    assert crosscheck["end"] == STAGE2_CROSSCHECK_END_ISO
    assert crosscheck["source"] == "injected"
    coverage_summary = record["coverage_summary"]
    assert isinstance(coverage_summary, list)
    assert len(coverage_summary) == 10
    assert {item["data_type"] for item in coverage_summary} == {
        "bars",
        "mark_price",
        "funding",
    }
    for item in coverage_summary:
        assert isinstance(item, dict)
        if item["gap_count"]:
            assert item["gap_explanation"] == STAGE2_MARK_GAP_EXPLANATION
    homology = record["homology"]
    assert isinstance(homology, dict)
    for split in ("train", "validation", "test"):
        for instrument_id in STAGE2_INSTRUMENT_IDS:
            bars_window = homology[f"{split}:{instrument_id}:bars"]
            funding_window = homology[f"{split}:{instrument_id}:funding"]
            assert isinstance(bars_window, dict)
            assert isinstance(funding_window, dict)
            assert bars_window["row_count"] > 0
            assert funding_window["row_count"] > 0

    bounds = stage2_split_bounds()
    train_start, train_end = bounds["train"]
    validation_start, validation_end = bounds["validation"]
    test_start, test_end = bounds["test"]
    assert train_end == validation_start
    assert validation_end == test_start
    bar_type = stage2_bar_type_str(BTC, "1h")
    research_bars = load_bars(catalog_path, bar_type, train_start, train_end)
    backtest_bars = load_stage2_backtest_bars(
        catalog_path, bar_type, start=train_start, end=train_end
    )
    compare_loaded_windows(
        {
            "bar_type": bar_type,
            "data_type": "bars",
            "dataset_id": STAGE2_DATASET_ID,
            "first_ts_event": int(research_bars.get_column("ts_event")[0]),
            "instrument_id": BTC,
            "last_ts_event": int(research_bars.get_column("ts_event")[-1]),
            "row_count": research_bars.height,
        },
        loaded_window(
            backtest_bars,
            dataset_id=STAGE2_DATASET_ID,
            data_type="bars",
            instrument_id=BTC,
            bar_type=bar_type,
        ),
    )
    validation_bars = load_bars(
        catalog_path, bar_type, validation_start, validation_end
    )
    test_bars = load_bars(catalog_path, bar_type, test_start, test_end)
    assert validation_bars.height > 0
    assert test_bars.height > 0
    train_last = int(research_bars.get_column("ts_event")[-1])
    validation_first = int(validation_bars.get_column("ts_event")[0])
    test_first = int(test_bars.get_column("ts_event")[0])
    assert train_last < validation_first
    assert int(validation_bars.get_column("ts_event")[-1]) < test_first

    sma_type = stage2_bar_type_str(BTC, "15m")
    sma_start = parse_utc(STAGE2_WINDOW_START_ISO)
    sma_end = datetime(2020, 1, 2, tzinfo=UTC)
    sma_frame = load_bars(catalog_path, sma_type, sma_start, sma_end)
    sma_bars = query_stage2_bars(
        catalog_path,
        sma_type,
        start_ns=int(sma_frame.get_column("ts_event")[0]),
        end_ns=int(sma_frame.get_column("ts_event")[-1]),
    )
    precision, tick = stage2_price_spec(catalog_path, BTC)
    stage2_sma_parity(
        sma_close(sma_frame, 10),
        sma_bars,
        period=10,
        precision=precision,
        tick=Decimal(tick),
    )
    stage2_sma_parity(
        sma_close(sma_frame, 20),
        sma_bars,
        period=20,
        precision=precision,
        tick=Decimal(tick),
    )

    long_off = _OpenLong()
    long_on = _OpenLong()
    without = _run_settlement(catalog_path, long_off, include_funding=False)
    with_funding = _run_settlement(catalog_path, long_on, include_funding=True)
    assert long_on.funding_seen
    assert Decimal(
        with_funding["account.BINANCE.balance.USDT.total"].split()[0]
    ) != Decimal(without["account.BINANCE.balance.USDT.total"].split()[0])
    assert int(with_funding["iterations"]) == int(without["iterations"]) + len(
        long_on.funding_seen
    )

    tracked = json.loads(ACCEPTANCE_RECORD.read_text(encoding="utf-8"))
    require_no_local_absolute_paths(tracked)
    assert ACCEPTANCE_KEYS <= set(tracked)
    assert tracked["dataset_id"] == STAGE2_DATASET_ID
    assert tracked["expected_source_count"] == 800
    assert (
        tracked["expected_source_inventory_digest"]
        == expected_source_inventory_digest()
    )
    assert tracked["include_index_price"] is False
    assert tracked["nautilus_tail_enabled"] is False
    assert tracked["runtime_identity"] == UPSTREAM_RELEASE_IDENTITY
    assert tracked["generation_command"] == STAGE2_GENERATION_COMMAND
    assert tracked["crosscheck"]["start"] == STAGE2_CROSSCHECK_START_ISO
    assert tracked["crosscheck"]["end"] == STAGE2_CROSSCHECK_END_ISO
    assert tracked["crosscheck"]["written_to_catalog"] is False
    require_complete_acceptance_record(
        tracked,
        require_gap_fill_sources=True,
        require_live_crosscheck=True,
    )

    written_coverage = json.loads(
        (catalog_path / "stage2_coverage.json").read_text(encoding="utf-8")
    )
    assert written_coverage["catalog_path"] == str(catalog_path)
    assert all(
        item["catalog_path"] == str(catalog_path) for item in written_coverage["series"]
    )


def test_expected_source_inventory_is_800_complete_months() -> None:
    specs = expected_stage2_source_specs()
    assert len(specs) == 800
    urls = [item.source_url for item in specs]
    require_complete_source_inventory(urls)
    with pytest.raises(Stage2DataError, match="missing months|duplicate|drifted"):
        require_complete_source_inventory(urls[:-1])
    with pytest.raises(Stage2DataError, match="duplicate"):
        require_complete_source_inventory([*urls, urls[0]])
    index = stage2_index_coverage_conclusion(checksum_probe=_always_available)
    objects = index["objects"]
    assert isinstance(objects, list)
    assert index["checksum_available"] is True
    assert index["continuous_months"] is True
    for item in objects:
        assert isinstance(item, dict)
        assert str(item["source_url"]).startswith(
            f"https://data.binance.vision/{STAGE2_INDEX_ROOT}/"
        )
        assert str(item["checksum_url"]).endswith(".CHECKSUM")


def test_index_checksum_probe_changes_conclusion() -> None:
    missing = stage2_index_coverage_conclusion(checksum_probe=lambda _url: False)
    assert missing["checksum_available"] is False
    assert missing["continuous_months"] is True
    with pytest.raises(Stage2DataError, match="index checksum availability"):
        build_stage2_acceptance_record(
            manifest=Stage2SourceManifest(
                schema="tracequant-stage2-source-v1",
                dataset_id=STAGE2_DATASET_ID,
                nautilus_version="2.0.0rc4",
                sources=(),
            ),
            coverage=Stage2CoverageReport(dataset_id=STAGE2_DATASET_ID, series=()),
            runtime_identity=UPSTREAM_RELEASE_IDENTITY,
            crosscheck={},
            homology={},
            checksum_probe=lambda _url: False,
        )


def test_dataset_prepare_rejects_missing_index_before_catalog_write(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    config_path = tmp_path / "dataset.toml"
    config_path.write_text(_config_text(raw_root, catalog_path), encoding="utf-8")
    with pytest.raises(Stage2DataError, match="index checksum availability"):
        prepare_stage2_dataset(
            config_path,
            repository_root=REPOSITORY_ROOT,
            checksum_probe=lambda _url: False,
        )
    assert list(catalog_path.iterdir()) == []


def test_index_continuity_uses_month_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tracequant.source_data.stage2_btceth.stage2_month_keys",
        lambda: ((2020, 1), (2020, 3)),
    )
    conclusion = stage2_index_coverage_conclusion(checksum_probe=_always_available)
    assert conclusion["continuous_months"] is False
    assert conclusion["object_count"] == 4
    with pytest.raises(Stage2DataError, match="not continuous"):
        build_stage2_acceptance_record(
            manifest=Stage2SourceManifest(
                schema="tracequant-stage2-source-v1",
                dataset_id=STAGE2_DATASET_ID,
                nautilus_version="2.0.0rc4",
                sources=(),
            ),
            coverage=Stage2CoverageReport(dataset_id=STAGE2_DATASET_ID, series=()),
            runtime_identity=UPSTREAM_RELEASE_IDENTITY,
            crosscheck={},
            homology={},
            checksum_probe=_always_available,
        )


def test_combined_catalog_rejects_incomplete_inventory(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    _write_kline_zip(
        raw_root,
        BTC,
        "15m",
        year=2020,
        month=1,
        count=3,
        start_ms=START_MS,
    )
    config_path = tmp_path / "dataset.toml"
    config_path.write_text(_config_text(raw_root, catalog_path), encoding="utf-8")
    with pytest.raises(Stage2DataError, match="missing"):
        prepare_stage2_combined_catalog(
            config_path,
            repository_root=REPOSITORY_ROOT,
            instruments=_stage2_instruments(),
            fetched_at=FETCHED_AT,
        )
    assert list(catalog_path.iterdir()) == []


def test_unexplained_intra_month_gap_fails() -> None:
    rows = _kline_rows(BTC, "15m", 2, START_MS)
    gapped = (
        rows[0],
        Stage2KlineRow(
            open_time=str(int(rows[1].open_time) + STAGE2_INTERVAL_MS["15m"]),
            open=rows[1].open,
            high=rows[1].high,
            low=rows[1].low,
            close=rows[1].close,
            volume=rows[1].volume,
            close_time=str(int(rows[1].close_time) + STAGE2_INTERVAL_MS["15m"]),
            quote_volume=rows[1].quote_volume,
            count=rows[1].count,
            taker_buy_volume=rows[1].taker_buy_volume,
            taker_buy_quote_volume=rows[1].taker_buy_quote_volume,
            ignore=rows[1].ignore,
        ),
    )
    with pytest.raises(Stage2DataError, match="gap"):
        validate_kline_series(
            gapped,
            instrument_id=BTC,
            interval="15m",
            source_checksum="abc",
        )


def test_cross_month_kline_gap_fails() -> None:
    rows = _kline_rows(BTC, "15m", 1, START_MS)
    next_month = _kline_rows(
        BTC,
        "15m",
        1,
        unix_millis(datetime(2020, 2, 28, tzinfo=UTC)),
    )
    with pytest.raises(Stage2DataError, match="gap"):
        validate_kline_series(
            (*rows, *next_month),
            instrument_id=BTC,
            interval="15m",
            source_checksum="abc",
        )


def test_only_exact_verified_mark_omissions_are_explained() -> None:
    known = (
        *_kline_rows(BTC, "15m", 1, 1579438800000),
        *_kline_rows(BTC, "15m", 1, 1579440600000),
    )
    coverage = validate_mark_series(
        known,
        instrument_id=BTC,
        source_checksum="abc",
    )
    assert coverage.gap_count == 1
    assert coverage.gap_explanation == STAGE2_MARK_GAP_EXPLANATION

    arbitrary = (
        *_kline_rows(BTC, "15m", 1, START_MS),
        *_kline_rows(BTC, "15m", 1, START_MS + 3 * STAGE2_INTERVAL_MS["15m"]),
    )
    with pytest.raises(Stage2DataError, match="gap"):
        validate_mark_series(
            arbitrary,
            instrument_id=BTC,
            source_checksum="abc",
        )


def test_cross_month_funding_gap_fails() -> None:
    rows = (
        Stage2FundingRow(str(START_MS), "8", "0.0001"),
        Stage2FundingRow(
            str(unix_millis(datetime(2020, 2, 28, tzinfo=UTC))),
            "8",
            "0.0001",
        ),
    )
    with pytest.raises(Stage2DataError, match="gap"):
        validate_funding_series(
            rows,
            instrument_id=BTC,
            source_checksum="abc",
        )


def test_acceptance_record_rejects_local_absolute_paths() -> None:
    with pytest.raises(Stage2DataError, match="local absolute paths"):
        require_no_local_absolute_paths({"catalog": "/tmp/stage2-catalog"})


def test_generation_command_entrypoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: dict[str, object] = {}

    def _fake_prepare(config_path: Path, **kwargs: object) -> dict[str, object]:
        called["config"] = config_path
        called["acceptance"] = kwargs.get("acceptance_path")
        return {}

    monkeypatch.setattr(
        "tracequant.integrations.nautilus.stage2_btceth.prepare_stage2_dataset",
        _fake_prepare,
    )
    config_path = tmp_path / "dataset.toml"
    monkeypatch.setenv(STAGE2_CONFIG_ENV, str(config_path))
    assert main() == 0
    assert called["config"] == config_path
    assert called["acceptance"] == (
        REPOSITORY_ROOT / "docs/product/stage2-btceth-dataset-acceptance.json"
    )
    monkeypatch.delenv(STAGE2_CONFIG_ENV)
    with pytest.raises(Stage2DataError, match=STAGE2_CONFIG_ENV):
        main()


def test_crosscheck_mismatch_and_empty_window_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _allow_sparse_archive_fixture(monkeypatch)
    catalog_path, _, _ = _prepare_catalog(tmp_path)
    with pytest.raises(Stage2DataError, match="bar count"):
        crosscheck_stage2_catalog(
            catalog_path,
            nautilus_bars={BTC: (), ETH: ()},
            nautilus_funding={BTC: (), ETH: ()},
        )
    bars, funding = _independent_crosscheck()
    with pytest.raises(Stage2DataError, match="bar count"):
        crosscheck_stage2_catalog(
            catalog_path,
            nautilus_bars={BTC: bars[BTC][:1], ETH: ()},
            nautilus_funding={BTC: funding[BTC], ETH: funding[ETH]},
        )
