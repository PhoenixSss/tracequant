"""Small deterministic factories for public domain models."""

from datetime import UTC, datetime

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange

DEFAULT_START = datetime(2026, 2, 28, 23, 59, tzinfo=UTC)
DEFAULT_END = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


def make_instrument_id(value: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(value)


def make_time_range(
    *, start: datetime = DEFAULT_START, end: datetime = DEFAULT_END
) -> TimeRange:
    return TimeRange(start=start, end=end)


def make_ohlcv_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    open: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 12.5,
) -> OHLCVBar:
    return OHLCVBar(
        instrument=instrument or make_instrument_id(),
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def invalid_numeric_overrides() -> tuple[dict[str, float], ...]:
    """Return fresh invalid samples without sharing mutable dictionaries."""
    return (
        {"open": float("nan")},
        {"high": float("inf")},
        {"low": float("-inf")},
        {"volume": -0.01},
        {"high": 99.0},
        {"low": 103.0},
    )
