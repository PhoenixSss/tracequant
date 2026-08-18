from datetime import UTC, datetime, timedelta, timezone

import pytest

from tests.fixtures.domain import DEFAULT_START, make_time_range
from tracequant.domain import TimeRange


def test_time_range_has_half_open_semantics_and_duration() -> None:
    interval = make_time_range()

    assert interval.duration == timedelta(minutes=1)
    assert interval.contains(interval.start)
    assert interval.contains(interval.end - timedelta(microseconds=1))
    assert not interval.contains(interval.end)


def test_time_range_handles_leap_day_and_month_boundary() -> None:
    start = datetime(2028, 2, 29, 23, 59, tzinfo=UTC)
    interval = make_time_range(start=start)

    assert interval.start == start
    assert interval.end == datetime(2028, 3, 1, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 1, 1), datetime(2026, 1, 2, tzinfo=UTC)),
        (
            datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
            datetime(2026, 1, 2, tzinfo=UTC),
        ),
        (DEFAULT_START, DEFAULT_START),
        (DEFAULT_START + timedelta(minutes=1), DEFAULT_START),
    ],
)
def test_time_range_rejects_non_utc_and_invalid_intervals(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        TimeRange(start, end)


def test_time_range_serialization_is_stable_and_round_trips() -> None:
    interval = make_time_range()

    payload = interval.to_dict()

    assert payload == {
        "start": "2026-02-28T23:59:00Z",
        "end": "2026-03-01T00:00:00Z",
    }
    assert TimeRange.from_dict(payload) == interval


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2026-01-01T00:00:00Z"},
        {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-02T00:00:00Z",
            "extra": True,
        },
        {"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00Z"},
        {
            "start": "2026-01-01T08:00:00+08:00",
            "end": "2026-01-02T00:00:00Z",
        },
        {"start": 1, "end": "2026-01-02T00:00:00Z"},
    ],
)
def test_time_range_rejects_invalid_serialized_data(
    payload: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        TimeRange.from_dict(payload)
