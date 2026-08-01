from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from quant_system.core.time import UTC
from quant_system.domain import DomainValidationError, TimeRange
from tests.fixtures.domain import VALID_RANGE_END, VALID_RANGE_START, make_time_range


def test_time_range_is_utc_half_open_and_has_duration() -> None:
    time_range = make_time_range()

    assert time_range.start == VALID_RANGE_START
    assert time_range.end == VALID_RANGE_END
    assert time_range.duration == timedelta(minutes=15)
    assert time_range.contains(VALID_RANGE_START)
    assert time_range.contains(VALID_RANGE_END - timedelta(microseconds=1))
    assert not time_range.contains(VALID_RANGE_END)


def test_time_range_is_immutable_and_comparable() -> None:
    time_range = make_time_range()

    with pytest.raises(FrozenInstanceError):
        time_range.start = VALID_RANGE_END  # type: ignore[misc]

    assert time_range == TimeRange(start=VALID_RANGE_START, end=VALID_RANGE_END)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 2, 28, 23, 45), VALID_RANGE_END),
        (VALID_RANGE_START, datetime(2026, 3, 1, 0, 0)),
        (
            datetime(2026, 2, 28, 23, 45, tzinfo=timezone(timedelta(hours=8))),
            VALID_RANGE_END,
        ),
        (
            VALID_RANGE_START,
            datetime(2026, 3, 1, 8, 0, tzinfo=timezone(timedelta(hours=8))),
        ),
        (VALID_RANGE_START, VALID_RANGE_START),
        (VALID_RANGE_END, VALID_RANGE_START),
    ],
)
def test_time_range_rejects_invalid_datetime_boundaries(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(DomainValidationError):
        TimeRange(start=start, end=end)


def test_time_range_supports_cross_day_month_and_leap_day_boundaries() -> None:
    time_range = TimeRange(
        start=datetime(2024, 2, 29, 23, 59, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 1, tzinfo=UTC),
    )

    assert time_range.duration == timedelta(minutes=2)


def test_time_range_contains_rejects_naive_and_non_utc_datetime() -> None:
    time_range = make_time_range()

    with pytest.raises(DomainValidationError):
        time_range.contains(datetime(2026, 2, 28, 23, 50))

    with pytest.raises(DomainValidationError):
        time_range.contains(
            datetime(2026, 3, 1, 7, 50, tzinfo=timezone(timedelta(hours=8)))
        )


def test_time_range_serializes_to_stable_utc_iso_strings() -> None:
    time_range = make_time_range()

    assert time_range.to_dict() == {
        "start": "2026-02-28T23:45:00Z",
        "end": "2026-03-01T00:00:00Z",
    }
    assert TimeRange.from_dict(time_range.to_dict()) == time_range


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2026-02-28T23:45:00Z"},
        {"start": "2026-02-28T23:45:00Z", "end": "2026-03-01T00:00:00Z", "x": "y"},
        {"start": "2026-02-28T23:45:00", "end": "2026-03-01T00:00:00Z"},
        {"start": "2026-03-01T07:45:00+08:00", "end": "2026-03-01T00:00:00Z"},
        {"start": 1, "end": "2026-03-01T00:00:00Z"},
    ],
)
def test_time_range_from_dict_rejects_invalid_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DomainValidationError):
        TimeRange.from_dict(payload)
