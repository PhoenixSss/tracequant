from __future__ import annotations

import ast
import tomllib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest
from nautilus_trader.model import (
    Bar,
    CryptoPerpetual,
    Currency,
    InstrumentId,
    MarkPriceUpdate,
    Price,
    Quantity,
    Symbol,
)
from nautilus_trader.persistence import ParquetDataCatalog

from tracequant.integrations.nautilus.stage2_btceth import (
    nautilus_close_sma,
    query_stage2_bars,
    query_stage2_mark_prices,
    stage2_bar_type,
    stage2_price_spec,
)
from tracequant.research.source_schema import (
    quantize_price,
    require_as_of,
    require_monotonic_unique_timestamps,
    split_window,
    stage2_split_bounds,
    validate_time_splits,
)
from tracequant.research.views import (
    load_bars,
    load_mark_prices,
    require_feature_as_of,
    sma_close,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_DATASET_ID,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_INTERVAL_MS,
    STAGE2_MANIFEST_FILENAME,
    STAGE2_NAUTILUS_VERSION,
    STAGE2_SOURCE_SCHEMA,
    STAGE2_WINDOW_END_ISO,
    STAGE2_WINDOW_START_ISO,
    Stage2DataError,
    datetime_to_nanos,
    millis_to_nanos,
    parse_utc,
    stage2_bar_type_str,
    unix_millis,
    write_json,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = REPOSITORY_ROOT / "src/tracequant/research"
NAUTILUS_STAGE2 = (
    REPOSITORY_ROOT / "src/tracequant/integrations/nautilus/stage2_btceth.py"
)
BTC = "BTCUSDT-PERP.BINANCE"
ETH = "ETHUSDT-PERP.BINANCE"
BAR_COUNT = 30
FORBIDDEN_RESEARCH_PACKAGES = {
    "duckdb",
    "jupyter",
    "jupyter-core",
    "jupyterlab",
    "mlflow",
    "notebook",
    "optuna",
    "prefect",
    "dagster",
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


def _close_at(index: int) -> str:
    return str((Decimal("100.00") + Decimal("0.01") * index).quantize(Decimal("0.01")))


def _bar(
    instrument_id: str,
    interval: str,
    open_ms: int,
    close: str,
    volume: str = "1.000",
) -> Bar:
    ts_event = millis_to_nanos(open_ms + STAGE2_INTERVAL_MS[interval] - 1)
    price = Price.from_str(close)
    return Bar(
        bar_type=stage2_bar_type(instrument_id, interval),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_str(volume),
        ts_event=ts_event,
        ts_init=ts_event,
    )


def _bar_at(
    instrument_id: str,
    interval: str,
    ts_event: int,
    close: str,
) -> Bar:
    price = Price.from_str(close)
    return Bar(
        bar_type=stage2_bar_type(instrument_id, interval),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_str("1.000"),
        ts_event=ts_event,
        ts_init=ts_event,
    )


def _write_manifest(catalog_path: Path, **overrides: object) -> None:
    snapshot = {
        "checksum_sha256": "1" * 64,
        "fetched_at": "2026-09-14T12:00:00Z",
    }
    payload: dict[str, object] = {
        "schema": STAGE2_SOURCE_SCHEMA,
        "dataset_id": STAGE2_DATASET_ID,
        "instrument_snapshot": {
            **snapshot,
            "filename": STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
        },
        "nautilus_version": STAGE2_NAUTILUS_VERSION,
        "sources": [],
    }
    payload.update(overrides)
    write_json(catalog_path / STAGE2_MANIFEST_FILENAME, payload)
    write_json(catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME, snapshot)


def _write_research_catalog(root: Path) -> tuple[Path, str]:
    catalog_path = root / "catalog"
    catalog_path.mkdir()
    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_instruments(
        [
            _instrument(BTC, "BTCUSDT", "BTC"),
            _instrument(ETH, "ETHUSDT", "ETH"),
        ]
    )
    start_ms = unix_millis(parse_utc(STAGE2_WINDOW_START_ISO))
    train_bars = [
        _bar(BTC, "15m", start_ms + index * STAGE2_INTERVAL_MS["15m"], _close_at(index))
        for index in range(BAR_COUNT)
    ]
    bounds = stage2_split_bounds()
    boundary_bars = [
        _bar_at(BTC, "15m", datetime_to_nanos(bounds["validation"][0]), "200.00"),
        _bar_at(BTC, "15m", datetime_to_nanos(bounds["test"][0]), "300.00"),
    ]
    catalog.write_bars(train_bars + boundary_bars)
    marks = [
        MarkPriceUpdate(
            instrument_id=InstrumentId.from_str(BTC),
            value=Price.from_str(_close_at(index)),
            ts_event=int(train_bars[index].ts_event),
            ts_init=int(train_bars[index].ts_init),
        )
        for index in (0, 1, 2)
    ]
    catalog.write_mark_price_updates(marks)
    _write_manifest(catalog_path)
    return catalog_path, stage2_bar_type_str(BTC, "15m")


def _file_mtimes(root: Path) -> dict[str, int]:
    return {
        str(path): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()
    }


def _lock_packages() -> list[dict[str, Any]]:
    with (REPOSITORY_ROOT / "uv.lock").open("rb") as stream:
        return cast(list[dict[str, Any]], tomllib.load(stream)["package"])


def _assert_sma_parity(
    frame: pl.DataFrame,
    bars: tuple[Bar, ...],
    *,
    period: int,
    precision: int,
    tick: Decimal,
) -> None:
    research_values = sma_close(frame, period)
    nautilus_values = nautilus_close_sma(bars, period)
    compared = 0
    for research_value, nautilus_value in zip(
        research_values, nautilus_values, strict=True
    ):
        if research_value is None or nautilus_value is None:
            assert research_value is None and nautilus_value is None
            continue
        research_tick = quantize_price(research_value, precision=precision)
        nautilus_tick = quantize_price(
            Decimal(str(nautilus_value)), precision=precision
        )
        assert abs(research_tick - nautilus_tick) <= tick
        compared += 1
    assert compared == len(bars) - period + 1


def test_catalog_bars_drive_polars_view_and_nautilus_sma_parity(tmp_path: Path) -> None:
    catalog_path, bar_type = _write_research_catalog(tmp_path)
    bounds = stage2_split_bounds()
    train_start, train_end = bounds["train"]
    validation_start, validation_end = bounds["validation"]
    test_start, test_end = bounds["test"]
    assert train_end == validation_start
    assert validation_end == test_start
    assert train_start == parse_utc(STAGE2_WINDOW_START_ISO)
    assert test_end == parse_utc(STAGE2_WINDOW_END_ISO)

    before = _file_mtimes(catalog_path)
    frame = load_bars(catalog_path, bar_type, train_start, train_end)
    marks = load_mark_prices(catalog_path, BTC, train_start, train_end)
    native = query_stage2_bars(
        catalog_path,
        bar_type,
        start_ns=datetime_to_nanos(train_start),
        end_ns=datetime_to_nanos(train_end) - 1,
    )
    native_marks = query_stage2_mark_prices(
        catalog_path,
        BTC,
        start_ns=datetime_to_nanos(train_start),
        end_ns=datetime_to_nanos(train_end) - 1,
    )
    after = _file_mtimes(catalog_path)

    assert before == after
    assert frame.height == len(native) == BAR_COUNT
    assert marks.height == len(native_marks) == 3
    assert [dtype for dtype in frame.schema.values()] == [
        pl.String,
        pl.String,
        pl.String,
        pl.String,
        pl.String,
        pl.String,
        pl.String,
        pl.Int64,
        pl.Int64,
    ]
    assert pl.Float64 not in frame.schema.values()
    assert pl.Float64 not in marks.schema.values()
    assert frame.get_column("bar_type").to_list() == [
        str(bar.bar_type) for bar in native
    ]
    assert frame.get_column("instrument_id").to_list() == [BTC] * BAR_COUNT
    assert frame.get_column("ts_event").to_list() == [
        int(bar.ts_event) for bar in native
    ]
    assert frame.get_column("ts_init").to_list() == [int(bar.ts_init) for bar in native]
    assert frame.get_column("open").to_list() == [str(bar.open) for bar in native]
    assert frame.get_column("high").to_list() == [str(bar.high) for bar in native]
    assert frame.get_column("low").to_list() == [str(bar.low) for bar in native]
    assert frame.get_column("close").to_list() == [str(bar.close) for bar in native]
    assert frame.get_column("volume").to_list() == [str(bar.volume) for bar in native]
    assert marks.get_column("value").to_list() == [
        str(mark.value) for mark in native_marks
    ]
    assert int(frame.get_column("ts_event")[0]) == int(native[0].ts_event)
    assert int(frame.get_column("ts_event")[-1]) == int(native[-1].ts_event)
    last_train = frame.get_column("ts_event").max()
    assert isinstance(last_train, int)
    assert last_train < datetime_to_nanos(validation_start)

    validation = load_bars(catalog_path, bar_type, validation_start, validation_end)
    test = load_bars(catalog_path, bar_type, test_start, test_end)
    train_times = set(frame.get_column("ts_event").to_list())
    validation_times = set(validation.get_column("ts_event").to_list())
    test_times = set(test.get_column("ts_event").to_list())
    assert train_times.isdisjoint(validation_times)
    assert train_times.isdisjoint(test_times)
    assert validation_times.isdisjoint(test_times)
    assert datetime_to_nanos(validation_start) in validation_times
    assert datetime_to_nanos(validation_start) not in train_times
    assert datetime_to_nanos(test_start) in test_times
    assert datetime_to_nanos(test_start) not in validation_times

    precision, tick = stage2_price_spec(catalog_path, BTC)
    _assert_sma_parity(
        frame, native, period=10, precision=precision, tick=Decimal(tick)
    )
    _assert_sma_parity(
        frame, native, period=20, precision=precision, tick=Decimal(tick)
    )


def test_polars_is_locked_without_research_platform_dependencies() -> None:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        dependencies = tomllib.load(stream)["project"]["dependencies"]
    assert "polars==1.44.2" in dependencies
    packages = _lock_packages()
    polars_entries = [entry for entry in packages if entry["name"] == "polars"]
    assert len(polars_entries) == 1
    assert polars_entries[0]["version"] == "1.44.2"
    names = {entry["name"] for entry in packages}
    assert names.isdisjoint(FORBIDDEN_RESEARCH_PACKAGES)


def test_fixed_splits_reject_inverted_overlap_and_out_of_bounds() -> None:
    dataset_start = parse_utc(STAGE2_WINDOW_START_ISO)
    dataset_end = parse_utc(STAGE2_WINDOW_END_ISO)
    validate_time_splits(
        stage2_split_bounds(), dataset_start=dataset_start, dataset_end=dataset_end
    )
    with pytest.raises(Stage2DataError, match="inverted"):
        validate_time_splits(
            {
                "train": (dataset_end, dataset_start),
            },
            dataset_start=dataset_start,
            dataset_end=dataset_end,
        )
    with pytest.raises(Stage2DataError, match="overlap"):
        validate_time_splits(
            {
                "train": (dataset_start, parse_utc("2024-06-01T00:00:00Z")),
                "validation": (
                    parse_utc("2024-01-01T00:00:00Z"),
                    parse_utc("2025-01-01T00:00:00Z"),
                ),
            },
            dataset_start=dataset_start,
            dataset_end=dataset_end,
        )
    with pytest.raises(Stage2DataError, match="outside the declared window"):
        validate_time_splits(
            {
                "train": (parse_utc("2019-01-01T00:00:00Z"), dataset_end),
            },
            dataset_start=dataset_start,
            dataset_end=dataset_end,
        )


def test_loader_rejects_empty_identity_window_and_future_reads(tmp_path: Path) -> None:
    catalog_path, bar_type = _write_research_catalog(tmp_path)
    train_start, train_end = split_window("train")
    with pytest.raises(Stage2DataError, match="no bars"):
        load_bars(
            catalog_path,
            bar_type,
            parse_utc("2023-06-01T00:00:00Z"),
            parse_utc("2023-06-01T01:00:00Z"),
        )
    with pytest.raises(Stage2DataError, match="identity"):
        load_bars(tmp_path, bar_type, train_start, train_end)
    other = tmp_path / "other"
    other.mkdir()
    _write_manifest(other, dataset_id="other-dataset-r1")
    with pytest.raises(Stage2DataError, match="identity"):
        load_bars(other, bar_type, train_start, train_end)
    mismatched_snapshot = tmp_path / "mismatched-snapshot"
    mismatched_snapshot.mkdir()
    _write_manifest(mismatched_snapshot)
    write_json(
        mismatched_snapshot / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
        {
            "checksum_sha256": "2" * 64,
            "fetched_at": "2026-09-14T12:00:00Z",
        },
    )
    with pytest.raises(Stage2DataError, match="snapshot identity"):
        load_bars(mismatched_snapshot, bar_type, train_start, train_end)
    with pytest.raises(Stage2DataError, match="inverted"):
        load_bars(catalog_path, bar_type, train_end, train_start)
    with pytest.raises(Stage2DataError, match="outside the declared window"):
        load_bars(
            catalog_path,
            bar_type,
            parse_utc("2019-12-01T00:00:00Z"),
            train_end,
        )
    with pytest.raises(Stage2DataError, match="not a stage 2 target"):
        load_bars(
            catalog_path,
            "XRPUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL",
            train_start,
            train_end,
        )
    with pytest.raises(Stage2DataError, match="not a stage 2 target"):
        load_mark_prices(catalog_path, "XRPUSDT-PERP.BINANCE", train_start, train_end)
    frame = load_bars(catalog_path, bar_type, train_start, train_end)
    decision = datetime(2020, 1, 1, 0, 10, tzinfo=UTC)
    with pytest.raises(Stage2DataError, match="decision timestamp"):
        require_feature_as_of(frame, decision_time=decision)
    with pytest.raises(Stage2DataError, match="decision timestamp"):
        require_as_of(decision_time=decision, data_end=train_end)
    require_feature_as_of(frame, decision_time=train_end)


def test_loader_rejects_reversed_or_duplicate_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path, bar_type = _write_research_catalog(tmp_path)
    train_start, train_end = split_window("train")
    original = load_bars(catalog_path, bar_type, train_start, train_end)
    records = original.to_dicts()
    reversed_records = tuple(reversed(records))
    duplicated = (records[0], records[0], *records[1:])

    def reversed_projection(
        *_args: object, **_kwargs: object
    ) -> tuple[dict[str, str | int], ...]:
        return reversed_records

    monkeypatch.setattr(
        "tracequant.research.views.project_stage2_bars", reversed_projection
    )
    with pytest.raises(Stage2DataError, match="out of order"):
        load_bars(catalog_path, bar_type, train_start, train_end)

    def duplicate_projection(
        *_args: object, **_kwargs: object
    ) -> tuple[dict[str, str | int], ...]:
        return tuple(duplicated)

    monkeypatch.setattr(
        "tracequant.research.views.project_stage2_bars", duplicate_projection
    )
    with pytest.raises(Stage2DataError, match="duplicate"):
        load_bars(catalog_path, bar_type, train_start, train_end)
    require_monotonic_unique_timestamps(original.get_column("ts_event").to_list())


def test_research_modules_stay_read_only_and_nautilus_free() -> None:
    nautilus_source = NAUTILUS_STAGE2.read_text(encoding="utf-8")
    assert "query_bars" in nautilus_source
    assert "query_mark_price_updates" in nautilus_source
    assert "write_bars" in nautilus_source
    research_files = list(RESEARCH_ROOT.glob("*.py"))
    assert research_files
    for path in research_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name.startswith("nautilus_trader") for name in imported)
        assert "write_bars" not in source
        assert "write_mark_price_updates" not in source
        assert "write_instruments" not in source
        assert "ParquetDataCatalog" not in source
