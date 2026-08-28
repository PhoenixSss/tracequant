from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from fixtures.domain import (
    FIXED_START,
    BarFactory,
    BarOverrides,
    make_ohlcv_bar,
    make_time_range,
)

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_instrument_normalizes_and_supports_value_semantics() -> None:
    left = InstrumentId("  ethusdt ")
    right = InstrumentId("ETHUSDT")

    assert left == right
    assert hash(left) == hash(right)
    assert str(left) == "ETHUSDT"


@pytest.mark.parametrize(
    "value",
    ["", "   ", "BTC/USDT", "BTC-USDT", "ＢＴＣUSDT", "A" * 33],
)
def test_instrument_rejects_empty_invalid_non_ascii_or_long_values(value: str) -> None:
    with pytest.raises(ValueError):
        InstrumentId(value)


def test_domain_models_are_immutable(bar_factory: BarFactory) -> None:
    instrument = InstrumentId("BTCUSDT")
    interval = make_time_range()
    bar = bar_factory()

    with pytest.raises(FrozenInstanceError):
        instrument.value = "ETHUSDT"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        interval.end = interval.start  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        bar.close = 99.0  # type: ignore[misc]


def test_time_range_rejects_naive_and_invalid_intervals() -> None:
    aware = datetime(2026, 8, 28, tzinfo=UTC)

    with pytest.raises(ValueError, match="timezone-aware"):
        TimeRange(datetime(2026, 8, 28), aware)
    with pytest.raises(ValueError, match="earlier"):
        TimeRange(aware, aware)
    with pytest.raises(ValueError, match="earlier"):
        TimeRange(aware + timedelta(seconds=1), aware)


def test_time_range_normalizes_non_utc_aware_datetimes() -> None:
    interval = TimeRange(
        datetime(2026, 3, 1, 8, 0, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 3, 1, 9, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert interval == TimeRange(
        datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 1, 0, tzinfo=UTC),
    )
    assert interval.start.tzinfo is UTC


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (
            datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
            datetime(2026, 2, 1, 0, 0, tzinfo=UTC),
        ),
        (
            datetime(2024, 2, 29, 23, 59, tzinfo=UTC),
            datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
        ),
    ],
)
def test_time_range_supports_cross_month_and_leap_day(
    start: datetime, end: datetime
) -> None:
    assert TimeRange(start, end) == make_time_range(start=start, end=end)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_bar_rejects_non_finite_numbers(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        make_ohlcv_bar(**cast(BarOverrides, {field: value}))


def test_bar_rejects_negative_volume_and_invalid_price_relationships() -> None:
    with pytest.raises(ValueError, match="volume"):
        make_ohlcv_bar(volume=-0.1)
    with pytest.raises(ValueError, match="high"):
        make_ohlcv_bar(high=99.0)
    with pytest.raises(ValueError, match="low"):
        make_ohlcv_bar(low=103.0)


def test_bar_allows_zero_and_negative_prices() -> None:
    bar = make_ohlcv_bar(open=-2.0, high=0.0, low=-3.0, close=-1.0)

    assert bar.open == -2.0
    assert bar.high == 0.0


def test_shared_factory_calls_are_isolated(
    bar_factory: BarFactory,
) -> None:
    first = bar_factory(close=101.0)
    second = bar_factory()

    assert first.close == 101.0
    assert second.close == 102.0
    assert first is not second


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2026-08-28T00:00:00Z"},
        {
            "start": "2026-08-28T00:00:00Z",
            "end": "2026-08-28T00:01:00Z",
            "extra": True,
        },
        {"start": "2026-08-28T00:00:00", "end": "2026-08-28T00:01:00Z"},
        {"start": 123, "end": "2026-08-28T00:01:00Z"},
    ],
)
def test_time_range_rejects_malformed_deserialization(payload: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        TimeRange.from_dict(payload)


def test_bar_serialization_is_stable_and_round_trips() -> None:
    bar = make_ohlcv_bar()

    assert list(bar.to_dict()) == [
        "instrument",
        "start",
        "end",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    assert OHLCVBar.from_dict(bar.to_dict()) == bar


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**make_ohlcv_bar().to_dict(), "extra": "value"},
        {**make_ohlcv_bar().to_dict(), "open": 100},
        {**make_ohlcv_bar().to_dict(), "instrument": ["BTCUSDT"]},
        {**make_ohlcv_bar().to_dict(), "start": "2026-08-28T00:00:00"},
    ],
)
def test_bar_rejects_malformed_deserialization(payload: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        OHLCVBar.from_dict(payload)


def test_factory_uses_fixed_utc_values() -> None:
    bar = make_ohlcv_bar()

    assert bar.start == FIXED_START
    assert bar.end == FIXED_START + timedelta(minutes=1)
