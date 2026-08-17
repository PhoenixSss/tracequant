"""Deterministic factories for public domain models."""

from datetime import UTC, datetime

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange

SAMPLE_START = datetime(2026, 2, 28, 23, 59, tzinfo=UTC)
SAMPLE_END = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


def make_time_range(
    *, start: datetime = SAMPLE_START, end: datetime = SAMPLE_END
) -> TimeRange:
    return TimeRange(start=start, end=end)


def make_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = SAMPLE_START,
    end: datetime = SAMPLE_END,
    open: float = 100.0,
    high: float = 110.0,
    low: float = 90.0,
    close: float = 105.0,
    volume: float = 12.5,
) -> OHLCVBar:
    return OHLCVBar(
        instrument=instrument if instrument is not None else InstrumentId("BTCUSDT"),
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
