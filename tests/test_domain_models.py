from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fixtures.domain import SAMPLE_END, SAMPLE_START, make_ohlcv_bar

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_instrument_normalizes_is_hashable_and_stringifies() -> None:
    instrument = InstrumentId("  btcusdt  ")

    assert instrument == InstrumentId("BTCUSDT")
    assert str(instrument) == "BTCUSDT"
    assert {instrument} == {InstrumentId("btcusdt")}


@pytest.mark.parametrize(
    "value",
    ["", "   ", "BTC-USDT", "BTC/USDT", "ＢＴＣ", "ß", "A" * 33],
)
def test_instrument_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        InstrumentId(value)


def test_instrument_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="must be a string"):
        InstrumentId(123)  # type: ignore[arg-type]


def test_time_range_is_half_open_and_exposes_duration() -> None:
    interval = TimeRange(SAMPLE_START, SAMPLE_END)

    assert interval.duration == timedelta(minutes=1)
    assert interval.contains(SAMPLE_START)
    assert interval.contains(SAMPLE_END - timedelta(microseconds=1))
    assert not interval.contains(SAMPLE_END)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 1, 1), SAMPLE_END),
        (SAMPLE_START, datetime(2026, 3, 1)),
        (
            datetime(2026, 3, 1, tzinfo=timezone(timedelta(hours=8))),
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
        TimeRange(start, end)


def test_time_range_handles_leap_day_and_month_boundary() -> None:
    interval = TimeRange(
        datetime(2024, 2, 29, 23, 59, tzinfo=UTC),
        datetime(2024, 3, 1, 0, 1, tzinfo=UTC),
    )

    assert interval.duration == timedelta(minutes=2)


def test_time_range_is_immutable_hashable_and_comparable() -> None:
    interval = TimeRange(SAMPLE_START, SAMPLE_END)

    assert interval == TimeRange(SAMPLE_START, SAMPLE_END)
    assert hash(interval) == hash(TimeRange(SAMPLE_START, SAMPLE_END))
    with pytest.raises(FrozenInstanceError):
        interval.end = SAMPLE_START  # type: ignore[misc]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_bar_rejects_non_finite_numbers(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        make_ohlcv_bar(**{field: value})  # type: ignore[arg-type]


def test_bar_rejects_negative_volume() -> None:
    with pytest.raises(ValueError, match="volume must be non-negative"):
        make_ohlcv_bar(volume=-0.1)


def test_bar_rejects_non_float_numeric_fields() -> None:
    with pytest.raises(TypeError, match="open must be a float"):
        OHLCVBar(
            instrument=InstrumentId("BTCUSDT"),
            start=SAMPLE_START,
            end=SAMPLE_END,
            open=100,
            high=110.0,
            low=90.0,
            close=105.0,
            volume=12.5,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": 99.0},
        {"low": 101.0},
        {"high": 104.0, "close": 105.0},
        {"low": 91.0, "close": 90.0},
    ],
)
def test_bar_rejects_invalid_ohlc_relationships(overrides: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        make_ohlcv_bar(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("open_price", "high", "low", "close"),
    [(0.0, 0.0, 0.0, 0.0), (-2.0, -1.0, -3.0, -2.5)],
)
def test_bar_allows_zero_and_negative_prices(
    open_price: float, high: float, low: float, close: float
) -> None:
    bar = make_ohlcv_bar(open=open_price, high=high, low=low, close=close)

    assert bar.open == open_price


def test_models_are_immutable_and_comparable(sample_bar: OHLCVBar) -> None:
    assert sample_bar == make_ohlcv_bar()
    assert hash(sample_bar) == hash(make_ohlcv_bar())

    with pytest.raises(FrozenInstanceError):
        sample_bar.close = 101.0  # type: ignore[misc]
