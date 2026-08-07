from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from tracequant.core.time import (
    ensure_aware,
    format_utc,
    is_utc,
    parse_utc,
    to_utc,
)


class _IndeterminateTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> None:
        return None

    def dst(self, dt: datetime | None) -> None:
        return None

    def tzname(self, dt: datetime | None) -> str:
        return "indeterminate"


def test_ensure_aware_returns_same_aware_datetime() -> None:
    value = datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC)

    assert ensure_aware(value) is value


def test_ensure_aware_rejects_naive_datetime_with_clear_message() -> None:
    value = datetime(2026, 7, 19, 7, 1, 20)

    with pytest.raises(ValueError, match="^datetime must be timezone-aware$"):
        ensure_aware(value)


def test_ensure_aware_rejects_timezone_with_no_offset() -> None:
    value = datetime(2026, 7, 19, 7, 1, 20, tzinfo=_IndeterminateTimezone())

    with pytest.raises(ValueError, match="^datetime must be timezone-aware$"):
        ensure_aware(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            datetime(2026, 7, 19, 7, 1, 20, tzinfo=timezone(timedelta(hours=8))),
            datetime(2026, 7, 18, 23, 1, 20, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 19, 22, 1, 20, tzinfo=timezone(timedelta(hours=-5))),
            datetime(2026, 7, 20, 3, 1, 20, tzinfo=UTC),
        ),
    ],
)
def test_to_utc_converts_offsets_across_date_boundaries(
    value: datetime, expected: datetime
) -> None:
    assert to_utc(value) == expected


def test_to_utc_handles_utc_input_and_preserves_microseconds() -> None:
    value = datetime(2026, 7, 19, 7, 1, 20, 123456, tzinfo=UTC)

    result = to_utc(value)

    assert result == value
    assert result.tzinfo is UTC
    assert result.microsecond == 123456


def test_to_utc_does_not_modify_input() -> None:
    value = datetime(2026, 7, 19, 7, 1, 20, tzinfo=timezone(timedelta(hours=8)))
    original = value

    to_utc(value)

    assert value is original
    assert value.utcoffset() == timedelta(hours=8)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 7, 19, 7, 1, 20),
        datetime(2026, 7, 19, 7, 1, 20, tzinfo=_IndeterminateTimezone()),
    ],
)
def test_to_utc_rejects_datetime_without_valid_offset(value: datetime) -> None:
    with pytest.raises(ValueError, match="^datetime must be timezone-aware$"):
        to_utc(value)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC),
        datetime(2026, 7, 19, 7, 1, 20, tzinfo=timezone.utc),  # noqa: UP017
        datetime(
            2026,
            7,
            19,
            7,
            1,
            20,
            tzinfo=timezone(timedelta(0), name="zero offset"),
        ),
    ],
)
def test_is_utc_accepts_any_valid_zero_offset(value: datetime) -> None:
    assert is_utc(value)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 7, 19, 7, 1, 20, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 7, 19, 7, 1, 20, tzinfo=timezone(timedelta(hours=-5))),
        datetime(2026, 7, 19, 7, 1, 20),
        datetime(2026, 7, 19, 7, 1, 20, tzinfo=_IndeterminateTimezone()),
    ],
)
def test_is_utc_rejects_nonzero_or_missing_offset(value: datetime) -> None:
    assert not is_utc(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-19T07:01:20Z", datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC)),
        (
            "2026-07-19T07:01:20+00:00",
            datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC),
        ),
        (
            "2026-07-19T07:01:20+08:00",
            datetime(2026, 7, 18, 23, 1, 20, tzinfo=UTC),
        ),
        (
            "2026-07-19T22:01:20-05:00",
            datetime(2026, 7, 20, 3, 1, 20, tzinfo=UTC),
        ),
        (
            "2026-07-19T07:01:20.123456Z",
            datetime(2026, 7, 19, 7, 1, 20, 123456, tzinfo=UTC),
        ),
    ],
)
def test_parse_utc_parses_and_normalizes_iso_8601_offsets(
    value: str, expected: datetime
) -> None:
    result = parse_utc(value)

    assert result == expected
    assert result.tzinfo is UTC


def test_parse_utc_rejects_iso_string_without_timezone() -> None:
    with pytest.raises(ValueError, match="^datetime must be timezone-aware$"):
        parse_utc("2026-07-19T07:01:20")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2026-02-30T07:01:20Z",
        "2026-07-19T25:01:20Z",
    ],
)
def test_parse_utc_rejects_empty_or_invalid_iso_string(value: str) -> None:
    with pytest.raises(ValueError):
        parse_utc(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC), "2026-07-19T07:01:20Z"),
        (
            datetime(2026, 7, 19, 7, 1, 20, tzinfo=timezone(timedelta(hours=8))),
            "2026-07-18T23:01:20Z",
        ),
        (
            datetime(2026, 7, 19, 22, 1, 20, tzinfo=timezone(timedelta(hours=-5))),
            "2026-07-20T03:01:20Z",
        ),
        (
            datetime(2026, 7, 19, 7, 1, 20, 123456, tzinfo=UTC),
            "2026-07-19T07:01:20.123456Z",
        ),
    ],
)
def test_format_utc_converts_to_utc_with_z_suffix(
    value: datetime, expected: str
) -> None:
    assert format_utc(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 7, 19, 7, 1, 20),
        datetime(2026, 7, 19, 7, 1, 20, tzinfo=_IndeterminateTimezone()),
    ],
)
def test_format_utc_rejects_datetime_without_valid_offset(value: datetime) -> None:
    with pytest.raises(ValueError, match="^datetime must be timezone-aware$"):
        format_utc(value)


def test_format_utc_does_not_modify_input() -> None:
    value = datetime(2026, 7, 19, 7, 1, 20, tzinfo=timezone(timedelta(hours=8)))
    original = value

    format_utc(value)

    assert value is original
    assert value.utcoffset() == timedelta(hours=8)
