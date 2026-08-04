"""Factories for the small public domain models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from quant_system.core import InstrumentId, OHLCVBar, TimeRange

DEFAULT_START = datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC)
DEFAULT_END = DEFAULT_START + timedelta(minutes=5)


def make_instrument(value: str = "BTCUSDT") -> InstrumentId:
    """Create a deterministic valid instrument identifier."""
    return InstrumentId(value)


def make_time_range(
    *, start: datetime = DEFAULT_START, end: datetime = DEFAULT_END
) -> TimeRange:
    """Create a deterministic interval while allowing explicit overrides."""
    return TimeRange(start=start, end=end)


def make_ohlcv_bar(
    *,
    instrument: InstrumentId | str = "BTCUSDT",
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    open: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 12.5,
) -> OHLCVBar:
    """Create a deterministic valid bar with explicit field overrides."""
    normalized_instrument = (
        instrument if isinstance(instrument, InstrumentId) else InstrumentId(instrument)
    )
    return OHLCVBar(
        instrument=normalized_instrument,
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def make_invalid_bar_payload(**overrides: Any) -> dict[str, Any]:
    """Return a fresh serialized payload suitable for invalid-input tests."""
    payload: dict[str, Any] = make_ohlcv_bar().to_dict()
    payload.update(overrides)
    return payload
