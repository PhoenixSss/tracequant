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
    compare_loaded_windows,
    crosscheck_stage2_catalog,
    load_stage2_backtest_bars,
    load_stage2_backtest_funding,
    loaded_window,
    prepare_stage2_combined_catalog,
    query_stage2_bars,
    require_no_index_or_1m_reference,
    stage2_bar_type,
    stage2_price_spec,
    stage2_sma_parity,
)
from tracequant.research.source_schema import stage2_split_bounds
from tracequant.research.views import load_bars, load_funding, sma_close
from tracequant.source_data.stage2_btceth import (
    STAGE2_BAR_AGGREGATION,
    STAGE2_BAR_INTERVALS,
    STAGE2_CROSSCHECK_END_ISO,
    STAGE2_CROSSCHECK_START_ISO,
    STAGE2_DATASET_ID,
    STAGE2_EXPECTED_SOURCE_COUNT,
    STAGE2_FUNDING_ROOT,
    STAGE2_GENERATION_COMMAND,
    STAGE2_INCLUDE_INDEX_PRICE,
    STAGE2_INDEX_ROOT,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INTERVAL_MS,
    STAGE2_KLINE_ROOT,
    STAGE2_MARK_INTERVAL,
    STAGE2_MARK_ROOT,
    STAGE2_NAUTILUS_TAIL_ENABLED,
    STAGE2_WINDOW_END_ISO,
    STAGE2_WINDOW_START_ISO,
    Stage2CoverageReport,
    Stage2DataError,
    Stage2SourceManifest,
    expected_source_inventory_digest,
    expected_stage2_source_specs,
    millis_to_nanos,
    parse_utc,
    require_complete_source_inventory,
    require_no_local_absolute_paths,
    require_tail_disabled,
    stage2_bar_type_str,
    stage2_index_coverage_conclusion,
    stage2_month_keys,
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


def _open_times(interval: str, count: int = 3) -> list[int]:
    step = STAGE2_INTERVAL_MS[interval]
    return [START_MS + index * step for index in range(count)]


def _funding_times() -> tuple[int, int]:
    return START_MS + 30 * 60 * 1000 + 1, START_MS + 45 * 60 * 1000


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
    count: int,
) -> None:
    symbol = "BTCUSDT" if instrument_id.startswith("BTC") else "ETHUSDT"
    name = f"{symbol}-{interval}-2020-01.zip"
    zip_path = raw_root / STAGE2_KLINE_ROOT / symbol / interval / name
    prices = SERIES_PRICES[(instrument_id, interval)]
    payload = []
    for index, open_time in enumerate(_open_times(interval, count)):
        close = prices[3]
        if instrument_id == BTC and interval == "15m":
            close = str(
                (Decimal("100.00") + Decimal("0.01") * index).quantize(Decimal("0.01"))
            )
        row_prices = (prices[0], prices[1], prices[2], close, prices[4])
        payload.append(_row_values(open_time, interval, row_prices))
    _write_zip(
        zip_path,
        payload,
        header=None,
        csv_name=f"{symbol}-{interval}-2020-01.csv",
    )


def _write_mark_zip(raw_root: Path, instrument_id: str) -> None:
    symbol = "BTCUSDT" if instrument_id.startswith("BTC") else "ETHUSDT"
    name = f"{symbol}-{STAGE2_MARK_INTERVAL}-2020-01.zip"
    zip_path = raw_root / STAGE2_MARK_ROOT / symbol / STAGE2_MARK_INTERVAL / name
    prices = MARK_PRICES[instrument_id]
    payload = [
        _row_values(open_time, STAGE2_MARK_INTERVAL, prices)
        for open_time in _open_times(STAGE2_MARK_INTERVAL)
    ]
    _write_zip(
        zip_path,
        payload,
        header=None,
        csv_name=f"{symbol}-{STAGE2_MARK_INTERVAL}-2020-01.csv",
    )


def _write_funding_zip(raw_root: Path, instrument_id: str) -> None:
    symbol = "BTCUSDT" if instrument_id.startswith("BTC") else "ETHUSDT"
    name = f"{symbol}-fundingRate-2020-01.zip"
    zip_path = raw_root / STAGE2_FUNDING_ROOT / symbol / name
    rates = FUNDING_RATES[instrument_id]
    times = _funding_times()
    payload = [
        [str(times[0]), rates[0][1], rates[0][0]],
        [str(times[1]), rates[1][1], rates[1][0]],
    ]
    _write_zip(
        zip_path,
        payload,
        header=None,
        csv_name=f"{symbol}-fundingRate-2020-01.csv",
    )


def _write_all_series(raw_root: Path) -> None:
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        for interval in STAGE2_BAR_INTERVALS:
            count = BAR_COUNT_15M if interval == "15m" else 3
            _write_kline_zip(raw_root, instrument_id, interval, count=count)
        _write_mark_zip(raw_root, instrument_id)
        _write_funding_zip(raw_root, instrument_id)


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


def _prepare_catalog(
    tmp_path: Path,
) -> tuple[Path, Stage2SourceManifest, Stage2CoverageReport]:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    _write_all_series(raw_root)
    config_path = tmp_path / "dataset.toml"
    config_path.write_text(_config_text(raw_root, catalog_path), encoding="utf-8")
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
    tmp_path: Path,
) -> None:
    catalog_path, manifest, coverage = _prepare_catalog(tmp_path)
    assert manifest.dataset_id == STAGE2_DATASET_ID
    assert len(coverage.series) == 10
    assert {item.data_type for item in coverage.series} == {
        "bars",
        "mark_price",
        "funding",
    }
    require_no_index_or_1m_reference(catalog_path)
    require_tail_disabled()
    assert STAGE2_NAUTILUS_TAIL_ENABLED is False
    assert STAGE2_INCLUDE_INDEX_PRICE is False
    index = stage2_index_coverage_conclusion()
    assert index["downloaded"] is False
    assert index["written_to_catalog"] is False
    assert index["object_count"] == 160
    assert index["month_count"] == 80
    assert len(expected_stage2_source_specs()) == STAGE2_EXPECTED_SOURCE_COUNT == 800
    assert len(stage2_month_keys()) == 80

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
    bar_window = compare_loaded_windows(
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
    research_funding = load_funding(catalog_path, BTC, train_start, train_end).collect()
    backtest_funding = load_stage2_backtest_funding(
        catalog_path,
        BTC,
        bar_type,
        start=train_start,
        end=train_end,
    )
    funding_window = compare_loaded_windows(
        {
            "data_type": "funding",
            "dataset_id": STAGE2_DATASET_ID,
            "first_ts_event": int(research_funding.get_column("ts_event")[0]),
            "instrument_id": BTC,
            "last_ts_event": int(research_funding.get_column("ts_event")[-1]),
            "row_count": research_funding.height,
        },
        loaded_window(
            backtest_funding,
            dataset_id=STAGE2_DATASET_ID,
            data_type="funding",
            instrument_id=BTC,
        ),
    )
    with pytest.raises(Stage2DataError, match="no bars"):
        load_bars(catalog_path, bar_type, validation_start, validation_end)
    with pytest.raises(Stage2DataError, match="no bars"):
        load_bars(catalog_path, bar_type, test_start, test_end)

    sma_type = stage2_bar_type_str(BTC, "15m")
    sma_frame = load_bars(catalog_path, sma_type, train_start, train_end)
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

    crosscheck = crosscheck_stage2_catalog(
        catalog_path,
        nautilus_bars={
            BTC: query_stage2_bars(
                catalog_path,
                stage2_bar_type_str(BTC, "1h"),
                start_ns=millis_to_nanos(
                    _open_times("1h")[0] + STAGE2_INTERVAL_MS["1h"] - 1
                ),
                end_ns=millis_to_nanos(
                    _open_times("1h")[-1] + STAGE2_INTERVAL_MS["1h"] - 1
                ),
            ),
            ETH: query_stage2_bars(
                catalog_path,
                stage2_bar_type_str(ETH, "1h"),
                start_ns=millis_to_nanos(
                    _open_times("1h")[0] + STAGE2_INTERVAL_MS["1h"] - 1
                ),
                end_ns=millis_to_nanos(
                    _open_times("1h")[-1] + STAGE2_INTERVAL_MS["1h"] - 1
                ),
            ),
        },
        nautilus_funding={
            BTC: backtest_funding,
            ETH: load_stage2_backtest_funding(
                catalog_path,
                ETH,
                stage2_bar_type_str(ETH, "1h"),
                start=train_start,
                end=train_end,
            ),
        },
        start=parse_utc(STAGE2_WINDOW_START_ISO),
        end=datetime(2020, 1, 2, tzinfo=UTC),
    )
    assert crosscheck["written_to_catalog"] is False
    assert STAGE2_CROSSCHECK_START_ISO == "2026-08-25T00:00:00Z"
    assert STAGE2_CROSSCHECK_END_ISO == "2026-09-01T00:00:00Z"

    long_off = _OpenLong()
    long_on = _OpenLong()
    without = _run_settlement(catalog_path, long_off, include_funding=False)
    with_funding = _run_settlement(catalog_path, long_on, include_funding=True)
    assert len(long_on.funding_seen) == 2
    assert Decimal(
        with_funding["account.BINANCE.balance.USDT.total"].split()[0]
    ) != Decimal(without["account.BINANCE.balance.USDT.total"].split()[0])
    assert int(with_funding["iterations"]) == int(without["iterations"]) + 2

    from tracequant.source_data.stage2_btceth import build_stage2_acceptance_record

    record = build_stage2_acceptance_record(
        manifest=manifest,
        coverage=coverage,
        runtime_identity=UPSTREAM_RELEASE_IDENTITY,
        crosscheck=crosscheck,
        homology={"bars": bar_window, "funding": funding_window},
    )
    require_no_local_absolute_paths(record)
    assert record["expected_source_count"] == 800
    assert record["generation_command"] == STAGE2_GENERATION_COMMAND
    assert record["runtime_identity"] == UPSTREAM_RELEASE_IDENTITY
    tracked = json.loads(ACCEPTANCE_RECORD.read_text(encoding="utf-8"))
    require_no_local_absolute_paths(tracked)
    assert tracked["dataset_id"] == STAGE2_DATASET_ID
    assert tracked["expected_source_count"] == 800
    assert (
        tracked["expected_source_inventory_digest"]
        == expected_source_inventory_digest()
    )
    assert tracked["include_index_price"] is False
    assert tracked["nautilus_tail_enabled"] is False
    assert tracked["runtime_identity"] == UPSTREAM_RELEASE_IDENTITY


def test_expected_source_inventory_is_800_complete_months() -> None:
    specs = expected_stage2_source_specs()
    assert len(specs) == 800
    urls = [item.source_url for item in specs]
    require_complete_source_inventory(urls)
    with pytest.raises(Stage2DataError, match="missing months|duplicate|drifted"):
        require_complete_source_inventory(urls[:-1])
    with pytest.raises(Stage2DataError, match="duplicate"):
        require_complete_source_inventory([*urls, urls[0]])
    index = stage2_index_coverage_conclusion()
    objects = index["objects"]
    assert isinstance(objects, list)
    for item in objects:
        assert isinstance(item, dict)
        assert str(item["source_url"]).startswith(
            f"https://data.binance.vision/{STAGE2_INDEX_ROOT}/"
        )
        assert str(item["checksum_url"]).endswith(".CHECKSUM")


def test_crosscheck_mismatch_and_empty_window_fail(tmp_path: Path) -> None:
    catalog_path, _, _ = _prepare_catalog(tmp_path)
    with pytest.raises(Stage2DataError, match="no overlapping records"):
        crosscheck_stage2_catalog(
            catalog_path,
            nautilus_bars={BTC: (), ETH: ()},
            nautilus_funding={BTC: (), ETH: ()},
        )
    bars = query_stage2_bars(
        catalog_path,
        stage2_bar_type_str(BTC, "1h"),
        start_ns=millis_to_nanos(_open_times("1h")[0] + STAGE2_INTERVAL_MS["1h"] - 1),
        end_ns=millis_to_nanos(_open_times("1h")[-1] + STAGE2_INTERVAL_MS["1h"] - 1),
    )
    with pytest.raises(Stage2DataError, match="bar count"):
        crosscheck_stage2_catalog(
            catalog_path,
            nautilus_bars={BTC: bars[:1], ETH: ()},
            nautilus_funding={BTC: (), ETH: ()},
            start=parse_utc(STAGE2_WINDOW_START_ISO),
            end=datetime(2020, 1, 2, tzinfo=UTC),
        )
