from __future__ import annotations

import ast
import csv
import hashlib
import io
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
from nautilus_trader.persistence import ParquetDataCatalog
from nautilus_trader.trading import Strategy, StrategyConfig

from tracequant.integrations.nautilus.stage2_btceth import (
    mark_price_updates_from_kline_rows,
    prepare_stage2_mark_funding_catalog,
    query_stage2_funding_files,
    query_stage2_mark_prices,
    stage2_bar_type,
    stage2_funding_backtest_data_config,
)
from tracequant.research.views import load_funding, load_mark_prices
from tracequant.source_data.stage2_btceth import (
    STAGE2_BAR_AGGREGATION,
    STAGE2_BAR_INTERVALS,
    STAGE2_DATASET_ID,
    STAGE2_FUNDING_ROOT,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INTERVAL_MS,
    STAGE2_MARK_INTERVAL,
    STAGE2_MARK_ROOT,
    STAGE2_WINDOW_END_ISO,
    STAGE2_WINDOW_START_ISO,
    Stage2DataError,
    Stage2FundingRow,
    Stage2KlineRow,
    millis_to_nanos,
    parse_funding_csv,
    parse_utc,
    require_recent_mark_for_funding,
    stage2_window,
    validate_funding_row,
    validate_funding_series,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STAGE2_SOURCE = REPOSITORY_ROOT / "src/tracequant/source_data/stage2_btceth.py"
STAGE2_NAUTILUS = (
    REPOSITORY_ROOT / "src/tracequant/integrations/nautilus/stage2_btceth.py"
)
FETCHED_AT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
BTC = "BTCUSDT-PERP.BINANCE"
ETH = "ETHUSDT-PERP.BINANCE"
BTC_BAR_TYPE = "BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL"
START_MS = 1577836800000
MARK_HEADER = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]
FUNDING_HEADER = ["calc_time", "funding_interval_hours", "last_funding_rate"]
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


def _open_times(count: int = 3) -> list[int]:
    step = STAGE2_INTERVAL_MS[STAGE2_MARK_INTERVAL]
    return [START_MS + index * step for index in range(count)]


def _funding_times() -> tuple[int, int]:
    return START_MS + 30 * 60 * 1000 + 1, START_MS + 45 * 60 * 1000


def _row_values(
    open_time: int,
    prices: tuple[str, str, str, str, str],
) -> list[str]:
    close_time = open_time + STAGE2_INTERVAL_MS[STAGE2_MARK_INTERVAL] - 1
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


def _kline_row(
    open_time: int,
    prices: tuple[str, str, str, str, str],
) -> Stage2KlineRow:
    values = _row_values(open_time, prices)
    return Stage2KlineRow(
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


def _write_mark_zip(raw_root: Path, instrument_id: str) -> Path:
    symbol = "BTCUSDT" if instrument_id.startswith("BTC") else "ETHUSDT"
    name = f"{symbol}-{STAGE2_MARK_INTERVAL}-2020-01.zip"
    zip_path = raw_root / STAGE2_MARK_ROOT / symbol / STAGE2_MARK_INTERVAL / name
    prices = MARK_PRICES[instrument_id]
    payload = [_row_values(open_time, prices) for open_time in _open_times()]
    _write_zip(
        zip_path,
        payload,
        header=MARK_HEADER if instrument_id == ETH else None,
        csv_name=f"{symbol}-{STAGE2_MARK_INTERVAL}-2020-01.csv",
    )
    return zip_path


def _write_funding_zip(raw_root: Path, instrument_id: str) -> Path:
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
        header=FUNDING_HEADER if instrument_id == BTC else None,
        csv_name=f"{symbol}-fundingRate-2020-01.csv",
    )
    return zip_path


def _write_all_series(raw_root: Path) -> None:
    for instrument_id in STAGE2_INSTRUMENT_IDS:
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


def _write_config(path: Path, raw_root: Path, catalog_path: Path) -> Path:
    path.write_text(_config_text(raw_root, catalog_path), encoding="utf-8")
    return path


def _prepare_catalog(tmp_path: Path) -> Path:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    _write_all_series(raw_root)
    config_path = _write_config(tmp_path / "dataset.toml", raw_root, catalog_path)
    prepare_stage2_mark_funding_catalog(
        config_path,
        repository_root=REPOSITORY_ROOT,
        instruments=_stage2_instruments(),
        fetched_at=FETCHED_AT,
    )
    return catalog_path


def _write_btc_bars(catalog_path: Path) -> None:
    catalog = ParquetDataCatalog(str(catalog_path))
    bar_type = stage2_bar_type(BTC, STAGE2_MARK_INTERVAL)
    price = Price.from_str(MARK_PRICES[BTC][3])
    bars = []
    for open_time in _open_times():
        ts_event = millis_to_nanos(
            open_time + STAGE2_INTERVAL_MS[STAGE2_MARK_INTERVAL] - 1
        )
        bars.append(
            Bar(
                bar_type=bar_type,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Quantity.from_str("1.000"),
                ts_event=ts_event,
                ts_init=ts_event,
            )
        )
    catalog.write_bars(bars)


class _OpenLong(Strategy):
    def __init__(self) -> None:
        super().__init__(
            StrategyConfig(oms_type=OmsType.NETTING, use_uuid_client_order_ids=False)
        )
        self._instrument_id = InstrumentId.from_str(BTC)
        self._bar_type = stage2_bar_type(BTC, STAGE2_MARK_INTERVAL)
        self._opened = False
        self.funding_seen: list[tuple[int, str, int | None]] = []

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
        self.funding_seen.append((int(event.ts_event), str(event.rate), event.interval))


class _OpenShort(_OpenLong):
    def on_bar(self, bar: Bar) -> None:
        if self._opened:
            return
        self.submit_order(
            self.order_factory.market(
                self._instrument_id, OrderSide.SELL, Quantity.from_str("1.000")
            )
        )
        self._opened = True


def _run_backtest(
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
            bar_types=[BTC_BAR_TYPE],
        ),
        BacktestDataConfig(
            data_type="MarkPriceUpdate",
            catalog_path=str(catalog_path),
            instrument_id=InstrumentId.from_str(BTC),
        ),
    ]
    if include_funding:
        data.append(stage2_funding_backtest_data_config(catalog_path, BTC))
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


def _balance(summary: dict[str, str]) -> Decimal:
    return Decimal(summary["account.BINANCE.balance.USDT.total"].split()[0])


def test_native_catalog_funding_settles_exactly_once_with_recent_mark(
    tmp_path: Path,
) -> None:
    catalog_path = _prepare_catalog(tmp_path)
    _write_btc_bars(catalog_path)
    start = parse_utc(STAGE2_WINDOW_START_ISO)
    end = parse_utc(STAGE2_WINDOW_END_ISO)
    files = query_stage2_funding_files(
        catalog_path,
        BTC,
        start_ns=millis_to_nanos(START_MS),
        end_ns=millis_to_nanos(_funding_times()[1]),
    )
    assert files
    frame = load_funding(catalog_path, BTC, start, end).collect()
    assert frame.height == 2
    assert frame.get_column("rate").to_list() == ["0.0001", "-0.0002"]
    assert frame.get_column("interval").to_list() == [480, 240]
    times = _funding_times()
    assert [int(value) for value in frame.get_column("ts_event").to_list()] == [
        millis_to_nanos(times[0]),
        millis_to_nanos(times[1]),
    ]
    assert (
        frame.get_column("ts_event").to_list() == frame.get_column("ts_init").to_list()
    )
    assert (
        frame.get_column("ts_event").to_list()
        == frame.get_column("next_funding_ns").to_list()
    )
    marks = query_stage2_mark_prices(catalog_path, BTC)
    assert [str(mark.value) for mark in marks] == [MARK_PRICES[BTC][3]] * 3
    assert [int(mark.ts_event) for mark in marks] == [
        millis_to_nanos(open_time + STAGE2_INTERVAL_MS[STAGE2_MARK_INTERVAL] - 1)
        for open_time in _open_times()
    ]
    research_marks = load_mark_prices(catalog_path, BTC, start, end)
    assert research_marks.height == 3
    long_off = _OpenLong()
    long_on = _OpenLong()
    short_off = _OpenShort()
    short_on = _OpenShort()
    long_without = _run_backtest(catalog_path, long_off, include_funding=False)
    long_with = _run_backtest(catalog_path, long_on, include_funding=True)
    short_without = _run_backtest(catalog_path, short_off, include_funding=False)
    short_with = _run_backtest(catalog_path, short_on, include_funding=True)
    assert long_on.funding_seen == [
        (millis_to_nanos(times[0]), "0.0001", 480),
        (millis_to_nanos(times[1]), "-0.0002", 240),
    ]
    assert short_on.funding_seen == long_on.funding_seen
    assert _balance(long_with) - _balance(long_without) == Decimal("1.00000000")
    assert _balance(short_with) - _balance(short_without) == Decimal("-1.00000000")
    assert (
        int(long_with["account.BINANCE.event_count"])
        == int(long_without["account.BINANCE.event_count"]) + 4
    )
    assert int(long_with["iterations"]) == int(long_without["iterations"]) + 2
    assert int(short_with["iterations"]) == int(short_without["iterations"]) + 2
    assert len(long_on.funding_seen) == frame.height


def test_header_and_headerless_funding_share_fixed_schema() -> None:
    times = _funding_times()
    rows = [
        [str(times[0]), "8", "0.0001"],
        [str(times[1]), "4", "-0.0002"],
    ]
    headerless = parse_funding_csv(
        "\n".join(",".join(row) for row in rows).encode("utf-8")
    )
    with_header = parse_funding_csv(
        (
            ",".join(FUNDING_HEADER) + "\n" + "\n".join(",".join(row) for row in rows)
        ).encode("utf-8")
    )
    assert headerless == with_header
    assert headerless[0].last_funding_rate == "0.0001"
    assert headerless[1].funding_interval_hours == "4"


def test_official_scientific_notation_funding_rate_is_valid() -> None:
    window_start, window_end = stage2_window()
    row = Stage2FundingRow("1578124800000", "8", "8.4E-7")
    validate_funding_row(row, window_start=window_start, window_end=window_end)


def test_checksum_mismatch_rejects_mark_funding_write(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    _write_all_series(raw_root)
    zip_path = _write_funding_zip(raw_root, BTC)
    Path(f"{zip_path}.CHECKSUM").write_text(
        f"{'0' * 64}  {zip_path.name}\n", encoding="utf-8"
    )
    config_path = _write_config(tmp_path / "dataset.toml", raw_root, catalog_path)
    with pytest.raises(Stage2DataError, match="checksum"):
        prepare_stage2_mark_funding_catalog(
            config_path,
            repository_root=REPOSITORY_ROOT,
            instruments=_stage2_instruments(),
            fetched_at=FETCHED_AT,
        )
    assert list(catalog_path.iterdir()) == []


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ("duplicate", "duplicate"),
        ("reverse", "out of order"),
        ("illegal_interval", "interval"),
        ("illegal_rate", "valid number"),
        ("window", "declared window"),
    ],
)
def test_invalid_funding_is_rejected(mutate: str, match: str) -> None:
    times = _funding_times()
    rows = [
        Stage2FundingRow(str(times[0]), "8", "0.0001"),
        Stage2FundingRow(str(times[1]), "4", "-0.0002"),
    ]
    window_start, window_end = stage2_window()
    if mutate == "duplicate":
        with pytest.raises(Stage2DataError, match=match):
            validate_funding_series(
                [rows[0], rows[0]],
                instrument_id=BTC,
                source_checksum="abc",
            )
        return
    if mutate == "reverse":
        with pytest.raises(Stage2DataError, match=match):
            validate_funding_series(
                [rows[1], rows[0]],
                instrument_id=BTC,
                source_checksum="abc",
            )
        return
    if mutate == "illegal_interval":
        with pytest.raises(Stage2DataError, match=match):
            validate_funding_row(
                Stage2FundingRow(str(times[0]), "0", "0.0001"),
                window_start=window_start,
                window_end=window_end,
            )
        return
    if mutate == "illegal_rate":
        with pytest.raises(Stage2DataError, match=match):
            validate_funding_row(
                Stage2FundingRow(str(times[0]), "8", "not-a-rate"),
                window_start=window_start,
                window_end=window_end,
            )
        return
    with pytest.raises(Stage2DataError, match=match):
        validate_funding_row(
            Stage2FundingRow("1577833200000", "8", "0.0001"),
            window_start=window_start,
            window_end=window_end,
        )


def test_missing_stale_or_future_mark_fails_funding_coverage() -> None:
    prices = MARK_PRICES[BTC]
    marks = [_kline_row(open_time, prices) for open_time in _open_times()]
    times = _funding_times()
    valid = [
        Stage2FundingRow(str(times[0]), "8", "0.0001"),
        Stage2FundingRow(str(times[1]), "4", "-0.0002"),
    ]
    require_recent_mark_for_funding(marks, valid)
    with pytest.raises(Stage2DataError, match="recent mark"):
        require_recent_mark_for_funding([], valid)
    stale = Stage2FundingRow(str(times[1] + 20 * 60 * 1000), "8", "0.0001")
    with pytest.raises(Stage2DataError, match="recent mark"):
        require_recent_mark_for_funding(marks, [stale])
    future_only = [
        _kline_row(START_MS + 2 * STAGE2_INTERVAL_MS[STAGE2_MARK_INTERVAL], prices)
    ]
    early = Stage2FundingRow(str(START_MS + 15 * 60 * 1000), "8", "0.0001")
    with pytest.raises(Stage2DataError, match="recent mark"):
        require_recent_mark_for_funding(future_only, [early])


def test_marks_are_built_from_close_and_close_time() -> None:
    prices = MARK_PRICES[ETH]
    rows = [_kline_row(open_time, prices) for open_time in _open_times()]
    marks = mark_price_updates_from_kline_rows(rows, instrument_id=ETH)
    assert str(marks[0].instrument_id) == ETH
    assert str(marks[0].value) == prices[3]
    assert (
        int(marks[0].ts_event)
        == int(marks[0].ts_init)
        == millis_to_nanos(int(rows[0].close_time))
    )


def test_unknown_funding_csv_format_fails() -> None:
    with pytest.raises(Stage2DataError, match="header|column count|fixed schema"):
        parse_funding_csv(b"calc_time,unknown\n1,2\n")
    with pytest.raises(Stage2DataError, match="column count"):
        parse_funding_csv(b"1,2\n")


def test_nautilus_boundary_owns_mark_funding_writes() -> None:
    source = STAGE2_SOURCE.read_text(encoding="utf-8")
    nautilus = STAGE2_NAUTILUS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.startswith("nautilus_trader") for name in imported)
    assert "write_mark_price_updates" in nautilus
    assert "StreamingFeatherWriter" in nautilus
    assert "convert_stream_to_data" in nautilus
    assert "query_files" in nautilus
    assert "write_custom_data" not in nautilus
    assert "write_index_price_updates" not in nautilus
    assert "skip_disjoint_check=True" not in nautilus
    assert "IndexPriceUpdate" not in nautilus
