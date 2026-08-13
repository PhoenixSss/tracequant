"""UTC half-open interval invariants."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fixtures.domain import DEFAULT_END, DEFAULT_START, make_time_range

from tracequant.domain import TimeRange


def test_time_range_accepts_cross_month_interval() -> None:
    interval = make_time_range()

    assert interval.start == DEFAULT_START
    assert interval.end == DEFAULT_END


def test_time_range_accepts_leap_day_interval() -> None:
    interval = make_time_range(
        start=datetime(2028, 2, 29, 23, 59, tzinfo=UTC),
        end=datetime(2028, 3, 1, 0, 0, tzinfo=UTC),
    )

    assert interval.start.day == 29


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 1, 1), datetime(2026, 1, 2, tzinfo=UTC)),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2)),
        (
            datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
            datetime(2026, 1, 2, tzinfo=UTC),
        ),
        (
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=-5))),
        ),
    ],
)
def test_time_range_rejects_naive_or_non_utc_values(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        TimeRange(start=start, end=end)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_time_range_rejects_empty_or_reversed_interval(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError, match="start must be earlier than end"):
        TimeRange(start=start, end=end)


def test_time_range_is_immutable() -> None:
    interval = make_time_range()

    with pytest.raises(FrozenInstanceError):
        interval.end = interval.start  # type: ignore[misc]


def test_time_range_round_trip_uses_stable_utc_iso_fields() -> None:
    interval = make_time_range()

    assert interval.to_dict() == {
        "start": "2026-02-28T23:59:00Z",
        "end": "2026-03-01T00:00:00Z",
    }
    assert TimeRange.from_dict(interval.to_dict()) == interval


def test_time_range_deserialization_normalizes_explicit_offsets() -> None:
    interval = TimeRange.from_dict(
        {"start": "2026-03-01T07:59:00+08:00", "end": "2026-03-01T08:00:00+08:00"}
    )

    assert interval == make_time_range()


@pytest.mark.parametrize(
    "value",
    [
        {"start": "2026-01-01T00:00:00Z"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z", "extra": 1},
        {"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00Z"},
        {"start": 1, "end": "2026-01-02T00:00:00Z"},
    ],
)
def test_time_range_rejects_invalid_serialized_data(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        TimeRange.from_dict(value)
