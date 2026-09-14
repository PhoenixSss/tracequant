from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final

from tracequant.source_data.stage2_btceth import (
    STAGE2_WINDOW_END_ISO,
    STAGE2_WINDOW_START_ISO,
    Stage2DataError,
    datetime_to_nanos,
    parse_utc,
    require_utc,
)

STAGE2_TRAIN_END_ISO: Final = "2024-01-01T00:00:00Z"
STAGE2_VALIDATION_END_ISO: Final = "2025-01-01T00:00:00Z"
STAGE2_SPLIT_NAMES: Final = ("train", "validation", "test")


def stage2_split_bounds() -> dict[str, tuple[datetime, datetime]]:
    dataset_start = parse_utc(STAGE2_WINDOW_START_ISO)
    train_end = parse_utc(STAGE2_TRAIN_END_ISO)
    validation_end = parse_utc(STAGE2_VALIDATION_END_ISO)
    dataset_end = parse_utc(STAGE2_WINDOW_END_ISO)
    bounds = {
        "train": (dataset_start, train_end),
        "validation": (train_end, validation_end),
        "test": (validation_end, dataset_end),
    }
    validate_time_splits(bounds, dataset_start=dataset_start, dataset_end=dataset_end)
    return bounds


def split_window(name: str) -> tuple[datetime, datetime]:
    try:
        return stage2_split_bounds()[name]
    except KeyError as exc:
        raise Stage2DataError("time split name is not a stage 2 target") from exc


def validate_time_splits(
    splits: Mapping[str, tuple[datetime, datetime]],
    *,
    dataset_start: datetime,
    dataset_end: datetime,
) -> None:
    start = require_utc(dataset_start)
    end = require_utc(dataset_end)
    windows = tuple(
        (require_utc(window_start), require_utc(window_end))
        for window_start, window_end in splits.values()
    )
    for window_start, window_end in windows:
        if window_start >= window_end:
            raise Stage2DataError("time split is inverted")
        if window_start < start or window_end > end:
            raise Stage2DataError("time split is outside the declared window")
    items = list(windows)
    for index, (left_start, left_end) in enumerate(items):
        for right_start, right_end in items[index + 1 :]:
            if left_start < right_end and right_start < left_end:
                raise Stage2DataError("time splits overlap")


def validate_query_window(start: datetime, end: datetime) -> tuple[int, int]:
    window_start = require_utc(start)
    window_end = require_utc(end)
    dataset_start = parse_utc(STAGE2_WINDOW_START_ISO)
    dataset_end = parse_utc(STAGE2_WINDOW_END_ISO)
    if window_start >= window_end:
        raise Stage2DataError("query window is inverted")
    if window_start < dataset_start or window_end > dataset_end:
        raise Stage2DataError("query window is outside the declared window")
    return datetime_to_nanos(window_start), datetime_to_nanos(window_end)


def require_as_of(*, decision_time: datetime, data_end: datetime) -> None:
    if require_utc(data_end) > require_utc(decision_time):
        raise Stage2DataError("feature data is after the decision timestamp")


def require_monotonic_unique_timestamps(timestamps: Sequence[int]) -> None:
    seen: set[int] = set()
    previous: int | None = None
    for ts_event in timestamps:
        if ts_event in seen:
            raise Stage2DataError("catalog records contain duplicate event times")
        seen.add(ts_event)
        if previous is not None and ts_event < previous:
            raise Stage2DataError("catalog records are out of order")
        previous = ts_event


def quantize_price(value: Decimal, *, precision: int) -> Decimal:
    if precision < 0:
        raise Stage2DataError("price precision must be non-negative")
    return value.quantize(Decimal(10) ** -precision)
