from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tests.fixtures.domain import make_bar, make_time_range
from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        (" btcusdt ", "BTCUSDT"),
        ("ETHUSDC", "ETHUSDC"),
        ("ABC123", "ABC123"),
        ("A" * 32, "A" * 32),
    ],
)
def test_instrument_normalizes_valid_values(raw: str, normalized: str) -> None:
    instrument = InstrumentId(raw)

    assert instrument.value == normalized
    assert str(instrument) == normalized


@pytest.mark.parametrize(
    "value",
    ["", "   ", "BTC-USDT", "BTC/USDT", "BTC_USDT", "比特币", "A" * 33],
)
def test_instrument_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        InstrumentId(value)


def test_instrument_is_immutable_comparable_and_hashable() -> None:
    instrument = InstrumentId("btcusdt")

    assert instrument == InstrumentId("BTCUSDT")
    assert {instrument, InstrumentId("BTCUSDT")} == {instrument}
    with pytest.raises(FrozenInstanceError):
        instrument.value = "ETHUSDT"  # type: ignore[misc]


def test_time_range_is_half_open_and_reports_duration() -> None:
    interval = make_time_range()

    assert interval.duration == timedelta(minutes=15)
    assert interval.contains(interval.start)
    assert interval.contains(interval.end - timedelta(microseconds=1))
    assert not interval.contains(interval.end)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 2, 28, 23, 50),
        datetime(2026, 3, 1, 7, 50, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_time_range_contains_rejects_non_utc_values(value: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        make_time_range().contains(value)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 2, 28, 23, 45), datetime(2026, 3, 1, 0, 0, tzinfo=UTC)),
        (
            datetime(2026, 2, 28, 23, 45, tzinfo=timezone(timedelta(hours=8))),
            datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 3, 1, 0, 1, tzinfo=UTC),
            datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        ),
    ],
)
def test_time_range_rejects_non_utc_and_invalid_intervals(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        TimeRange(start=start, end=end)


def test_time_range_handles_leap_day_and_is_immutable() -> None:
    interval = TimeRange(
        start=datetime(2024, 2, 29, 23, 45, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
    )

    assert interval.duration == timedelta(minutes=15)
    with pytest.raises(FrozenInstanceError):
        interval.start = interval.end  # type: ignore[misc]


def test_shared_sample_bar_is_explicit_and_valid(sample_bar: OHLCVBar) -> None:
    assert sample_bar.instrument == InstrumentId("BTCUSDT")
    assert sample_bar.open == 100.0
    assert sample_bar.high == 110.0
    assert sample_bar.low == 90.0
    assert sample_bar.close == 105.0
    assert sample_bar.volume == 42.5


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_bar_rejects_non_finite_numbers(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=f"^{field} must be finite$"):
        make_bar(**{field: value})  # type: ignore[arg-type]


def test_bar_rejects_negative_volume() -> None:
    with pytest.raises(ValueError, match="^volume must be non-negative$"):
        make_bar(volume=-0.1)


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [1, True])
def test_bar_requires_python_float_values(field: str, value: object) -> None:
    with pytest.raises(TypeError, match=f"^{field} must be a float$"):
        make_bar(**{field: value})  # type: ignore[arg-type]


def test_bar_requires_instrument_id() -> None:
    with pytest.raises(TypeError, match="^instrument must be an InstrumentId$"):
        make_bar(instrument="BTCUSDT")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [{"high": 99.0}, {"low": 101.0}, {"high": 104.0, "close": 105.0}],
)
def test_bar_rejects_invalid_ohlc_relationships(overrides: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        make_bar(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("open", "high", "low", "close"),
    [(0.0, 0.0, 0.0, 0.0), (-5.0, -1.0, -10.0, -3.0)],
)
def test_bar_allows_zero_and_negative_prices(
    open: float, high: float, low: float, close: float
) -> None:
    bar = make_bar(open=open, high=high, low=low, close=close)

    assert (bar.open, bar.high, bar.low, bar.close) == (open, high, low, close)


def test_bar_is_immutable_comparable_and_hashable(sample_bar: OHLCVBar) -> None:
    assert sample_bar == make_bar()
    assert {sample_bar, make_bar()} == {sample_bar}
    with pytest.raises(FrozenInstanceError):
        sample_bar.close = 99.0  # type: ignore[misc]
