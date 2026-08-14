"""Explicit deterministic factories for shared domain test data."""

from datetime import UTC, datetime

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange

SAMPLE_START = datetime(2024, 2, 29, 23, 45, tzinfo=UTC)
SAMPLE_END = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)


def make_instrument_id(value: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(value)


def make_time_range(
    *, start: datetime = SAMPLE_START, end: datetime = SAMPLE_END
) -> TimeRange:
    return TimeRange(start=start, end=end)


def make_ohlcv_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = SAMPLE_START,
    end: datetime = SAMPLE_END,
    open: float = 60_000.0,
    high: float = 61_000.0,
    low: float = 59_500.0,
    close: float = 60_500.0,
    volume: float = 125.5,
) -> OHLCVBar:
    return OHLCVBar(
        instrument=instrument if instrument is not None else make_instrument_id(),
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def invalid_instrument_values() -> list[object]:
    """Return a fresh collection of representative invalid identifier inputs."""
    return ["", "   ", "BTC-USDT", "BTC/USDT", "比特币", "A" * 33, None, 123]


def invalid_bar_overrides() -> list[dict[str, float]]:
    """Return fresh invalid numeric overrides for parameterized tests."""
    return [
        {"open": float("nan")},
        {"high": float("inf")},
        {"low": float("-inf")},
        {"volume": -1.0},
        {"high": 59_000.0},
        {"low": 62_000.0},
    ]
