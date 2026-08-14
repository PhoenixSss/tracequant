from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fixtures.domain import (
    SAMPLE_END,
    SAMPLE_START,
    invalid_bar_overrides,
    invalid_instrument_values,
    make_instrument_id,
    make_ohlcv_bar,
    make_time_range,
)

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_instrument_id_normalizes_and_supports_value_semantics() -> None:
    instrument = InstrumentId("  btcusdt  ")

    assert str(instrument) == "BTCUSDT"
    assert instrument == InstrumentId("BTCUSDT")
    assert hash(instrument) == hash(InstrumentId("BTCUSDT"))
    assert {instrument, InstrumentId("btcusdt")} == {instrument}
    assert repr(instrument) == "InstrumentId(value='BTCUSDT')"


@pytest.mark.parametrize("value", invalid_instrument_values())
def test_instrument_id_rejects_invalid_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        InstrumentId(value)  # type: ignore[arg-type]


def test_models_are_frozen(sample_bar: OHLCVBar) -> None:
    with pytest.raises(FrozenInstanceError):
        sample_bar.close = 1.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        sample_bar.instrument.value = "ETHUSDT"  # type: ignore[misc]


def test_time_range_has_half_open_semantics_and_duration() -> None:
    interval = make_time_range()

    assert interval.duration == timedelta(minutes=15)
    assert interval.contains(SAMPLE_START)
    assert interval.contains(SAMPLE_END - timedelta(microseconds=1))
    assert not interval.contains(SAMPLE_END)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2024, 1, 1), SAMPLE_END),
        (SAMPLE_START, datetime(2024, 3, 1)),
        (
            datetime(2024, 3, 1, tzinfo=timezone(timedelta(hours=8))),
            SAMPLE_END,
        ),
        (SAMPLE_START, SAMPLE_START),
        (SAMPLE_END, SAMPLE_START),
    ],
)
def test_time_range_rejects_non_utc_and_invalid_intervals(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        TimeRange(start=start, end=end)


def test_time_range_accepts_cross_month_leap_day_interval() -> None:
    interval = TimeRange(
        start=datetime(2024, 2, 29, 23, 59, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 1, tzinfo=UTC),
    )

    assert interval.duration == timedelta(minutes=2)


@pytest.mark.parametrize("overrides", invalid_bar_overrides())
def test_ohlcv_bar_rejects_invalid_numeric_values(
    overrides: dict[str, float],
) -> None:
    with pytest.raises(ValueError):
        make_ohlcv_bar(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize("price", [0.0, -1.0])
def test_ohlcv_bar_allows_zero_and_negative_prices(price: float) -> None:
    bar = make_ohlcv_bar(open=price, high=price, low=price, close=price)

    assert bar.open == price
    assert bar.close == price


def test_ohlcv_bar_rejects_non_float_numeric_fields() -> None:
    with pytest.raises(TypeError, match="open must be a float"):
        make_ohlcv_bar(open=1)


def test_shared_factory_returns_independent_objects(sample_bar: OHLCVBar) -> None:
    another = make_ohlcv_bar(instrument=make_instrument_id("ETHUSDT"))

    assert another is not sample_bar
    assert another.instrument is not sample_bar.instrument
    assert another.instrument == InstrumentId("ETHUSDT")
