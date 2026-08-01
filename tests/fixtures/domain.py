"""Deterministic domain model factories for tests that need explicit overrides."""

from __future__ import annotations

from datetime import datetime

from quant_system.core.time import UTC
from quant_system.domain import InstrumentId, OHLCVBar, TimeRange

VALID_INSTRUMENT_VALUE = "BTCUSDT"
VALID_RANGE_START = datetime(2026, 2, 28, 23, 45, tzinfo=UTC)
VALID_RANGE_END = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
VALID_OPEN = 100.0
VALID_HIGH = 105.0
VALID_LOW = 95.0
VALID_CLOSE = 101.5
VALID_VOLUME = 12.25

INVALID_INSTRUMENT_VALUES = (
    "",
    "   ",
    "BTC-USDT",
    "BTC_USDT",
    "BTC/USDT",
    "BTCUSDT!",
    "\u6bd4\u7279\u5e01",
    "A" * 33,
)


def make_instrument(value: str = VALID_INSTRUMENT_VALUE) -> InstrumentId:
    """Return a fresh valid instrument id."""
    return InstrumentId(value)


def make_time_range(
    *,
    start: datetime = VALID_RANGE_START,
    end: datetime = VALID_RANGE_END,
) -> TimeRange:
    """Return a fresh valid UTC half-open time range."""
    return TimeRange(start=start, end=end)


def make_ohlcv_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = VALID_RANGE_START,
    end: datetime = VALID_RANGE_END,
    open: float = VALID_OPEN,
    high: float = VALID_HIGH,
    low: float = VALID_LOW,
    close: float = VALID_CLOSE,
    volume: float = VALID_VOLUME,
) -> OHLCVBar:
    """Return a fresh valid OHLCV bar with explicit override hooks."""
    return OHLCVBar(
        instrument=make_instrument() if instrument is None else instrument,
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
