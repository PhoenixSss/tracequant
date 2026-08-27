"""Deterministic factories for public domain models."""

from datetime import UTC, datetime

from tracequant.domain import InstrumentId, OHLCVBar

FIXED_START = datetime(2024, 2, 29, 23, 45, tzinfo=UTC)
FIXED_END = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)


def make_ohlcv_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = FIXED_START,
    end: datetime = FIXED_END,
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
