from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fixtures.domain import SAMPLE_END, SAMPLE_START, make_time_range

from tracequant.domain import TimeRange


def test_time_range_is_half_open_and_serializes_stably() -> None:
    interval = make_time_range()

    assert interval.start == SAMPLE_START
    assert interval.end == SAMPLE_END
    assert interval.to_dict() == {
        "start": "2026-02-28T23:59:00Z",
        "end": "2026-03-01T00:00:00Z",
    }


def test_time_range_normalizes_aware_offsets_via_utc_api() -> None:
    interval = TimeRange(
        start=datetime(2026, 3, 1, 7, 59, tzinfo=timezone(timedelta(hours=8))),
        end=datetime(2026, 3, 1, 8, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert interval.start == SAMPLE_START
    assert interval.start.tzinfo is UTC


@pytest.mark.parametrize("field", ["start", "end"])
def test_time_range_rejects_naive_datetime(field: str) -> None:
    values = {"start": SAMPLE_START, "end": SAMPLE_END}
    values[field] = datetime(2026, 3, 1, 0, 0)

    with pytest.raises(ValueError, match="datetime must be timezone-aware"):
        TimeRange(**values)


@pytest.mark.parametrize(
    ("start", "end"),
    [(SAMPLE_START, SAMPLE_START), (SAMPLE_END, SAMPLE_START)],
)
def test_time_range_rejects_empty_or_reversed_interval(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError, match="start must be earlier than end"):
        TimeRange(start=start, end=end)


def test_time_range_from_dict_rejects_missing_extra_and_naive_data() -> None:
    with pytest.raises(ValueError, match="invalid fields"):
        TimeRange.from_dict({"start": "2026-01-01T00:00:00Z"})
    with pytest.raises(ValueError, match="invalid fields"):
        TimeRange.from_dict(
            {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:01:00Z",
                "timezone": "UTC",
            }
        )
    with pytest.raises(ValueError, match="datetime must be timezone-aware"):
        TimeRange.from_dict(
            {"start": "2026-01-01T00:00:00", "end": "2026-01-01T00:01:00Z"}
        )


def test_time_range_is_immutable() -> None:
    interval = make_time_range()

    with pytest.raises(FrozenInstanceError):
        interval.start = SAMPLE_END  # type: ignore[misc]
