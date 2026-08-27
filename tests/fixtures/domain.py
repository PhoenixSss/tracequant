"""Deterministic factories for tests that exercise public domain models."""

from datetime import UTC, datetime, timedelta

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange

DEFAULT_START = datetime(2024, 2, 28, 23, 45, tzinfo=UTC)
DEFAULT_END = DEFAULT_START + timedelta(minutes=15)


def make_instrument(value: str = "BTCUSDT") -> InstrumentId:
    """Create an instrument with an explicit, easy-to-see default."""
    return InstrumentId(value)


def make_time_range(
    start: datetime = DEFAULT_START, end: datetime = DEFAULT_END
) -> TimeRange:
    """Create a deterministic UTC interval with explicit field overrides."""
    return TimeRange(start=start, end=end)


def make_ohlcv_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    open: float = 50_000.0,
    high: float = 50_025.0,
    low: float = 49_975.0,
    close: float = 50_010.0,
    volume: float = 12.5,
) -> OHLCVBar:
    """Create a fresh deterministic bar; every field can be overridden."""
    return OHLCVBar(
        instrument=instrument if instrument is not None else make_instrument(),
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
