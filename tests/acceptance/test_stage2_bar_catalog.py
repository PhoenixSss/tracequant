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
from nautilus_trader.model import (
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Price,
    Quantity,
    Symbol,
)

from tracequant.integrations.nautilus.stage2_btceth import (
    bars_from_kline_rows,
    build_stage2_binance_instrument_client_config,
    prepare_stage2_bar_catalog,
    query_stage2_bars,
    stage2_bar_type,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_BAR_AGGREGATION,
    STAGE2_BAR_INTERVALS,
    STAGE2_DATASET_ID,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_INTERVAL_MS,
    STAGE2_KLINE_ROOT,
    STAGE2_WINDOW_END_ISO,
    STAGE2_WINDOW_START_ISO,
    Stage2DataError,
    Stage2KlineRow,
    load_stage2_config,
    millis_to_nanos,
    parse_kline_csv,
    stage2_bar_type_str,
    stage2_window,
    validate_kline_row,
    validate_kline_series,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STAGE2_SOURCE = REPOSITORY_ROOT / "src/tracequant/source_data/stage2_btceth.py"
STAGE2_NAUTILUS = (
    REPOSITORY_ROOT / "src/tracequant/integrations/nautilus/stage2_btceth.py"
)
EXAMPLE_CONFIG = REPOSITORY_ROOT / "config/stage2_btceth.example.toml"
FETCHED_AT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
HEADER_RECENT = [
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
SERIES_PRICES = {
    ("BTCUSDT-PERP.BINANCE", "15m"): ("100.00", "101.00", "99.50", "100.50", "1.000"),
    ("BTCUSDT-PERP.BINANCE", "1h"): ("200.00", "201.00", "199.50", "200.50", "2.000"),
    ("BTCUSDT-PERP.BINANCE", "4h"): ("300.00", "301.00", "299.50", "300.50", "3.000"),
    ("ETHUSDT-PERP.BINANCE", "15m"): ("400.00", "401.00", "399.50", "400.50", "4.000"),
    ("ETHUSDT-PERP.BINANCE", "1h"): ("500.00", "501.00", "499.50", "500.50", "5.000"),
    ("ETHUSDT-PERP.BINANCE", "4h"): ("600.00", "601.00", "599.50", "600.50", "6.000"),
}
HEADER_SERIES = {
    ("BTCUSDT-PERP.BINANCE", "1h"),
    ("ETHUSDT-PERP.BINANCE", "15m"),
    ("ETHUSDT-PERP.BINANCE", "4h"),
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
        _instrument("BTCUSDT-PERP.BINANCE", "BTCUSDT", "BTC"),
        _instrument("ETHUSDT-PERP.BINANCE", "ETHUSDT", "ETH"),
    )


def _open_times(interval: str, count: int = 3) -> list[int]:
    start = 1577836800000
    step = STAGE2_INTERVAL_MS[interval]
    return [start + index * step for index in range(count)]


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


def _kline_row(
    open_time: int,
    interval: str,
    prices: tuple[str, str, str, str, str],
) -> Stage2KlineRow:
    values = _row_values(open_time, interval, prices)
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
) -> str:
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
    return digest


def _write_series_zip(
    raw_root: Path,
    instrument_id: str,
    interval: str,
    rows: list[list[str]] | None = None,
    *,
    header: bool | None = None,
) -> Path:
    symbol = "BTCUSDT" if instrument_id.startswith("BTC") else "ETHUSDT"
    name = f"{symbol}-{interval}-2020-01.zip"
    zip_path = raw_root / STAGE2_KLINE_ROOT / symbol / interval / name
    prices = SERIES_PRICES[(instrument_id, interval)]
    payload = rows or [
        _row_values(open_time, interval, prices) for open_time in _open_times(interval)
    ]
    use_header = (instrument_id, interval) in HEADER_SERIES
    if header is not None:
        use_header = header
    _write_zip(
        zip_path,
        payload,
        header=HEADER_RECENT if use_header else None,
        csv_name=f"{symbol}-{interval}-2020-01.csv",
    )
    return zip_path


def _write_all_series(raw_root: Path) -> None:
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        for interval in STAGE2_BAR_INTERVALS:
            _write_series_zip(raw_root, instrument_id, interval)


def _config_text(raw_root: Path, catalog_path: Path, **overrides: object) -> str:
    values: dict[str, object] = {
        "schema": "tracequant-stage2-dataset-v1",
        "dataset_id": STAGE2_DATASET_ID,
        "nautilus_version": "2.0.0rc4",
        "environment": "offline",
        "source": "binance-public-data",
        "market": "futures/um",
        "archive_frequency": "monthly",
        "window_start": STAGE2_WINDOW_START_ISO,
        "window_end": STAGE2_WINDOW_END_ISO,
        "instrument_ids": list(STAGE2_INSTRUMENT_IDS),
        "bar_intervals": list(STAGE2_BAR_INTERVALS),
        "bar_aggregation": STAGE2_BAR_AGGREGATION,
        "raw_root": str(raw_root),
        "catalog_path": str(catalog_path),
    }
    values.update(overrides)
    lines = []
    for key, value in values.items():
        if isinstance(value, list):
            rendered = ", ".join(f'"{item}"' for item in value)
            lines.append(f"{key} = [{rendered}]")
        elif isinstance(value, bool):
            lines.append(f"{key} = {str(value).lower()}")
        else:
            lines.append(f'{key} = "{value}"')
    return "\n".join(lines) + "\n"


def _write_config(
    path: Path, raw_root: Path, catalog_path: Path, **overrides: object
) -> Path:
    path.write_text(_config_text(raw_root, catalog_path, **overrides), encoding="utf-8")
    return path


def test_stage2_bars_round_trip_through_nautilus_catalog(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    _write_all_series(raw_root)
    config_path = _write_config(tmp_path / "dataset.toml", raw_root, catalog_path)

    manifest, coverage = prepare_stage2_bar_catalog(
        config_path,
        repository_root=REPOSITORY_ROOT,
        instruments=_stage2_instruments(),
        fetched_at=FETCHED_AT,
    )

    assert manifest.dataset_id == STAGE2_DATASET_ID
    assert coverage.dataset_id == STAGE2_DATASET_ID
    assert len(coverage.series) == 6
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        for interval in STAGE2_BAR_INTERVALS:
            bar_type = stage2_bar_type_str(instrument_id, interval)
            queried = query_stage2_bars(catalog_path, bar_type)
            prices = SERIES_PRICES[(instrument_id, interval)]
            opens = _open_times(interval)
            assert len(queried) == 3
            assert [str(bar.bar_type) for bar in queried] == [bar_type] * 3
            assert [int(bar.ts_event) for bar in queried] == [
                millis_to_nanos(open_time + STAGE2_INTERVAL_MS[interval] - 1)
                for open_time in opens
            ]
            assert [int(bar.ts_init) for bar in queried] == [
                int(bar.ts_event) for bar in queried
            ]
            assert [str(bar.open) for bar in queried] == [prices[0]] * 3
            assert [str(bar.high) for bar in queried] == [prices[1]] * 3
            assert [str(bar.low) for bar in queried] == [prices[2]] * 3
            assert [str(bar.close) for bar in queried] == [prices[3]] * 3
            assert [str(bar.volume) for bar in queried] == [prices[4]] * 3
    assert all(
        item.duplicate_count == item.out_of_order_count == item.gap_count == 0
        for item in coverage.series
    )


def test_prepare_reuses_frozen_instrument_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    _write_all_series(raw_root)
    config_path = _write_config(tmp_path / "dataset.toml", raw_root, catalog_path)
    fetch_calls = {"count": 0}
    frozen = _stage2_instruments()

    def fake_fetch() -> tuple[tuple[CryptoPerpetual, CryptoPerpetual], datetime]:
        fetch_calls["count"] += 1
        return frozen, FETCHED_AT

    monkeypatch.setattr(
        "tracequant.integrations.nautilus.stage2_btceth.fetch_stage2_instruments",
        fake_fetch,
    )
    prepare_stage2_bar_catalog(config_path, repository_root=REPOSITORY_ROOT)
    snapshot_path = raw_root / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME
    assert fetch_calls["count"] == 1
    assert snapshot_path.is_file()
    assert (catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME).is_file()

    catalog_path_replay = tmp_path / "catalog-replay"
    catalog_path_replay.mkdir()
    replay_config = _write_config(
        tmp_path / "dataset-replay.toml", raw_root, catalog_path_replay
    )
    prepare_stage2_bar_catalog(replay_config, repository_root=REPOSITORY_ROOT)
    assert fetch_calls["count"] == 1
    queried = query_stage2_bars(
        catalog_path_replay, stage2_bar_type_str("BTCUSDT-PERP.BINANCE", "15m")
    )
    assert len(queried) == 3


def test_header_and_headerless_fixtures_share_fixed_schema() -> None:
    prices = SERIES_PRICES[("BTCUSDT-PERP.BINANCE", "1h")]
    rows = [_row_values(open_time, "1h", prices) for open_time in _open_times("1h")]
    headerless = parse_kline_csv(
        "\n".join(",".join(row) for row in rows).encode("utf-8")
    )
    with_header = parse_kline_csv(
        (
            ",".join(HEADER_RECENT) + "\n" + "\n".join(",".join(row) for row in rows)
        ).encode("utf-8")
    )
    assert headerless == with_header
    assert headerless[0].open == prices[0]
    assert headerless[0].close_time == str(
        _open_times("1h")[0] + STAGE2_INTERVAL_MS["1h"] - 1
    )


def test_unknown_csv_format_fails() -> None:
    with pytest.raises(Stage2DataError, match="header|column count|fixed schema"):
        parse_kline_csv(
            b"open_time,open,high,low,close,volume,unknown\n1,2,3,4,5,6,7\n"
        )
    with pytest.raises(Stage2DataError, match="column count"):
        parse_kline_csv(b"1,2,3,4,5,6,7,8,9,10,11\n")


def test_typed_config_rejects_unknown_relative_in_repo_and_identity(
    tmp_path: Path,
) -> None:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    good = tmp_path / "good.toml"
    _write_config(good, raw_root, catalog_path)
    loaded = load_stage2_config(good, repository_root=REPOSITORY_ROOT)
    assert loaded.dataset_id == STAGE2_DATASET_ID

    unknown = tmp_path / "unknown.toml"
    _write_config(unknown, raw_root, catalog_path, include_funding=True)
    with pytest.raises(Stage2DataError, match="unknown fields"):
        load_stage2_config(unknown, repository_root=REPOSITORY_ROOT)

    relative = tmp_path / "relative.toml"
    relative.write_text(
        _config_text(raw_root, catalog_path).replace(
            f'raw_root = "{raw_root}"', 'raw_root = "raw"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(Stage2DataError, match="absolute"):
        load_stage2_config(relative, repository_root=REPOSITORY_ROOT)

    in_repo = tmp_path / "in-repo.toml"
    in_repo.write_text(
        _config_text(raw_root, catalog_path).replace(
            f'raw_root = "{raw_root}"',
            f'raw_root = "{REPOSITORY_ROOT / "config"}"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(Stage2DataError, match="outside the repository"):
        load_stage2_config(in_repo, repository_root=REPOSITORY_ROOT)

    mismatched = tmp_path / "mismatch.toml"
    _write_config(mismatched, raw_root, catalog_path, dataset_id="other-dataset-r1")
    with pytest.raises(Stage2DataError, match="identity"):
        load_stage2_config(mismatched, repository_root=REPOSITORY_ROOT)

    (catalog_path / "existing.txt").write_text("no", encoding="utf-8")
    nonempty = tmp_path / "nonempty.toml"
    _write_config(nonempty, raw_root, catalog_path)
    with pytest.raises(Stage2DataError, match="already exists"):
        load_stage2_config(nonempty, repository_root=REPOSITORY_ROOT)


def test_checksum_mismatch_rejects_catalog_write(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    catalog_path = tmp_path / "catalog"
    raw_root.mkdir()
    catalog_path.mkdir()
    _write_all_series(raw_root)
    zip_path = (
        raw_root / STAGE2_KLINE_ROOT / "BTCUSDT" / "1h" / "BTCUSDT-1h-2020-01.zip"
    )
    Path(f"{zip_path}.CHECKSUM").write_text(
        f"{'0' * 64}  {zip_path.name}\n", encoding="utf-8"
    )
    config_path = _write_config(tmp_path / "dataset.toml", raw_root, catalog_path)
    with pytest.raises(Stage2DataError, match="checksum"):
        prepare_stage2_bar_catalog(
            config_path,
            repository_root=REPOSITORY_ROOT,
            instruments=_stage2_instruments(),
            fetched_at=FETCHED_AT,
        )
    assert list(catalog_path.iterdir()) == []
    assert not (raw_root / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME).exists()


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        ("duplicate", "duplicate"),
        ("reverse", "out of order"),
        ("gap", "gap"),
        ("illegal_ohlc", "OHLC"),
        ("negative_volume", "volume"),
        ("wrong_interval", "close time|interval"),
        ("window", "declared window"),
    ],
)
def test_invalid_klines_are_rejected(mutate: str, match: str) -> None:
    prices = SERIES_PRICES[("BTCUSDT-PERP.BINANCE", "15m")]
    opens = _open_times("15m")
    rows = [_kline_row(open_time, "15m", prices) for open_time in opens]
    window_start, window_end = stage2_window()
    if mutate == "duplicate":
        rows = [rows[0], rows[0], rows[2]]
        with pytest.raises(Stage2DataError, match=match):
            validate_kline_series(
                rows,
                instrument_id="BTCUSDT-PERP.BINANCE",
                interval="15m",
                source_checksum="abc",
            )
        return
    if mutate == "reverse":
        rows = [rows[1], rows[0], rows[2]]
        with pytest.raises(Stage2DataError, match=match):
            validate_kline_series(
                rows,
                instrument_id="BTCUSDT-PERP.BINANCE",
                interval="15m",
                source_checksum="abc",
            )
        return
    if mutate == "gap":
        rows = [rows[0], rows[2]]
        with pytest.raises(Stage2DataError, match=match):
            validate_kline_series(
                rows,
                instrument_id="BTCUSDT-PERP.BINANCE",
                interval="15m",
                source_checksum="abc",
            )
        return
    if mutate == "illegal_ohlc":
        bad = _kline_row(
            opens[0], "15m", ("100.00", "99.00", "101.00", "100.50", "1.000")
        )
        with pytest.raises(Stage2DataError, match=match):
            validate_kline_row(
                bad, interval="15m", window_start=window_start, window_end=window_end
            )
        return
    if mutate == "negative_volume":
        bad = _kline_row(
            opens[0], "15m", ("100.00", "101.00", "99.50", "100.50", "-1.000")
        )
        with pytest.raises(Stage2DataError, match=match):
            validate_kline_row(
                bad, interval="15m", window_start=window_start, window_end=window_end
            )
        return
    if mutate == "wrong_interval":
        values = _row_values(opens[0], "1h", prices)
        bad = Stage2KlineRow(
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
        with pytest.raises(Stage2DataError, match=match):
            validate_kline_row(
                bad, interval="15m", window_start=window_start, window_end=window_end
            )
        return
    bad = _kline_row(1577833200000, "15m", prices)
    with pytest.raises(Stage2DataError, match=match):
        validate_kline_row(
            bad, interval="15m", window_start=window_start, window_end=window_end
        )


def test_wrong_symbol_is_rejected() -> None:
    with pytest.raises(Stage2DataError, match="instrument"):
        stage2_bar_type_str("XRPUSDT-PERP.BINANCE", "15m")
    prices = SERIES_PRICES[("BTCUSDT-PERP.BINANCE", "15m")]
    rows = [_kline_row(open_time, "15m", prices) for open_time in _open_times("15m")]
    with pytest.raises(Stage2DataError, match="instrument"):
        validate_kline_series(
            rows,
            instrument_id="XRPUSDT-PERP.BINANCE",
            interval="15m",
            source_checksum="abc",
        )


def test_bars_are_built_from_source_strings() -> None:
    prices = SERIES_PRICES[("ETHUSDT-PERP.BINANCE", "4h")]
    rows = [_kline_row(open_time, "4h", prices) for open_time in _open_times("4h")]
    bars = bars_from_kline_rows(
        rows, instrument_id="ETHUSDT-PERP.BINANCE", interval="4h"
    )
    assert bars[0].bar_type == stage2_bar_type("ETHUSDT-PERP.BINANCE", "4h")
    assert str(bars[0].open) == prices[0]
    assert str(bars[0].volume) == prices[4]
    assert (
        int(bars[0].ts_event)
        == int(bars[0].ts_init)
        == millis_to_nanos(int(rows[0].close_time))
    )


def test_example_config_is_tracked_and_has_no_secrets() -> None:
    text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    assert STAGE2_DATASET_ID in text
    assert STAGE2_BAR_AGGREGATION in text
    assert "api_key" not in text
    assert "api_secret" not in text
    assert "BEGIN PRIVATE" not in text


def test_instrument_client_config_has_no_credentials() -> None:
    config = build_stage2_binance_instrument_client_config("http://proxy.example:8080")
    assert config.has_proxy_url
    source = STAGE2_NAUTILUS.read_text(encoding="utf-8")
    assert "api_key=None" in source
    assert "api_secret=None" in source


def test_nautilus_boundary_owns_catalog_writes() -> None:
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
    assert "write_instruments" in nautilus
    assert "write_bars" in nautilus
    assert "query_bars" in nautilus
    assert "skip_disjoint_check=True" not in nautilus
    assert "download-kline" not in nautilus
    assert "def fetch_stage2_nautilus_crosscheck" in nautilus
    assert "def prepare_stage2_bar_catalog" in nautilus
    prepare_start = nautilus.index("def prepare_stage2_bar_catalog")
    prepare_end = nautilus.index("def prepare_stage2_mark_funding_catalog")
    assert "request_bars" not in nautilus[prepare_start:prepare_end]
