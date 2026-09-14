from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from tracequant.integrations.nautilus.stage2_btceth import (
    project_stage2_bars,
    project_stage2_mark_prices,
    query_stage2_funding_files,
)
from tracequant.research.source_schema import (
    require_monotonic_unique_timestamps,
    validate_query_window,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_BAR_INTERVALS,
    STAGE2_INSTRUMENT_IDS,
    Stage2DataError,
    datetime_to_nanos,
    require_stage2_catalog_identity,
    require_utc,
    stage2_bar_type_str,
)


def load_bars(
    catalog_path: Path,
    bar_type: str,
    start: datetime,
    end: datetime,
) -> pl.DataFrame:
    catalog = Path(catalog_path)
    _require_stage2_bar_type(bar_type)
    require_stage2_catalog_identity(catalog)
    start_ns, end_ns = validate_query_window(start, end)
    records = project_stage2_bars(
        catalog, bar_type, start_ns=start_ns, end_ns_exclusive=end_ns
    )
    return _frame_from_records(
        records,
        {
            "bar_type": pl.String,
            "instrument_id": pl.String,
            "open": pl.String,
            "high": pl.String,
            "low": pl.String,
            "close": pl.String,
            "volume": pl.String,
            "ts_event": pl.Int64,
            "ts_init": pl.Int64,
        },
        empty="catalog query returned no bars",
    )


def load_mark_prices(
    catalog_path: Path,
    instrument_id: str,
    start: datetime,
    end: datetime,
) -> pl.DataFrame:
    catalog = Path(catalog_path)
    if instrument_id not in STAGE2_INSTRUMENT_IDS:
        raise Stage2DataError("instrument is not a stage 2 target")
    require_stage2_catalog_identity(catalog)
    start_ns, end_ns = validate_query_window(start, end)
    records = project_stage2_mark_prices(
        catalog, instrument_id, start_ns=start_ns, end_ns_exclusive=end_ns
    )
    return _frame_from_records(
        records,
        {
            "instrument_id": pl.String,
            "value": pl.String,
            "ts_event": pl.Int64,
            "ts_init": pl.Int64,
        },
        empty="catalog query returned no mark prices",
    )


def load_funding(
    catalog_path: Path,
    instrument_id: str,
    start: datetime,
    end: datetime,
) -> pl.LazyFrame:
    catalog = Path(catalog_path)
    if instrument_id not in STAGE2_INSTRUMENT_IDS:
        raise Stage2DataError("instrument is not a stage 2 target")
    require_stage2_catalog_identity(catalog)
    start_ns, end_ns = validate_query_window(start, end)
    files = query_stage2_funding_files(
        catalog,
        instrument_id,
        start_ns=start_ns,
        end_ns=end_ns - 1,
    )
    if not files:
        raise Stage2DataError("catalog query returned no funding files")
    frame = (
        pl.scan_parquet([str(path) for path in files])
        .filter(
            (pl.col("instrument_id") == instrument_id)
            & (pl.col("ts_event") >= start_ns)
            & (pl.col("ts_event") < end_ns)
        )
        .collect()
    )
    if frame.height == 0:
        raise Stage2DataError("catalog query returned no funding")
    require_monotonic_unique_timestamps(
        [int(value) for value in frame.get_column("ts_event").to_list()]
    )
    for dtype in frame.schema.values():
        if dtype in {pl.Float32, pl.Float64}:
            raise Stage2DataError("research view must not use floating-point prices")
    return frame.lazy()


def sma_close(frame: pl.DataFrame, period: int) -> tuple[Decimal | None, ...]:
    if period <= 0:
        raise Stage2DataError("sma period must be positive")
    closes = [Decimal(value) for value in frame.get_column("close").to_list()]
    values: list[Decimal | None] = []
    for index in range(len(closes)):
        if index + 1 < period:
            values.append(None)
        else:
            window = closes[index + 1 - period : index + 1]
            values.append(sum(window, Decimal(0)) / Decimal(period))
    return tuple(values)


def require_feature_as_of(frame: pl.DataFrame, *, decision_time: datetime) -> None:
    max_ts = _require_int(frame.get_column("ts_event").max())
    if max_ts > datetime_to_nanos(require_utc(decision_time)):
        raise Stage2DataError("feature data is after the decision timestamp")


def _require_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Stage2DataError("catalog query returned no bars")
    return value


def _frame_from_records(
    records: Sequence[dict[str, str | int]],
    schema: dict[str, type[pl.DataType] | pl.DataType],
    *,
    empty: str,
) -> pl.DataFrame:
    if not records:
        raise Stage2DataError(empty)
    frame = pl.DataFrame(records, schema=schema)
    if frame.height == 0:
        raise Stage2DataError(empty)
    require_monotonic_unique_timestamps(frame.get_column("ts_event").to_list())
    for dtype in frame.schema.values():
        if dtype in {pl.Float32, pl.Float64}:
            raise Stage2DataError("research view must not use floating-point prices")
    return frame


def _require_stage2_bar_type(bar_type: str) -> None:
    known = {
        stage2_bar_type_str(instrument_id, interval)
        for instrument_id in STAGE2_INSTRUMENT_IDS
        for interval in STAGE2_BAR_INTERVALS
    }
    if bar_type not in known:
        raise Stage2DataError("bar type is not a stage 2 target")
