"""Deterministic factories for public domain-model tests."""

from datetime import UTC, datetime, timedelta
from typing import NotRequired, Protocol, TypedDict, Unpack

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


class BarOverrides(TypedDict):
    instrument: NotRequired[InstrumentId]
    start: NotRequired[datetime]
    end: NotRequired[datetime]
    open: NotRequired[float]
    high: NotRequired[float]
    low: NotRequired[float]
    close: NotRequired[float]
    volume: NotRequired[float]


class BarFactory(Protocol):
    def __call__(self, **overrides: Unpack[BarOverrides]) -> OHLCVBar: ...


FIXED_START = datetime(2024, 2, 29, 23, 59, tzinfo=UTC)


def make_time_range(
    *, start: datetime = FIXED_START, end: datetime | None = None
) -> TimeRange:
    """Build a fixed UTC range while keeping relevant values explicit."""
    return TimeRange(
        start=start, end=end if end is not None else start + timedelta(minutes=1)
    )


def make_ohlcv_bar(**overrides: Unpack[BarOverrides]) -> OHLCVBar:
    """Build a valid deterministic bar with explicit per-call overrides."""
    return OHLCVBar(
        instrument=overrides.get("instrument", InstrumentId("BTCUSDT")),
        start=overrides.get("start", FIXED_START),
        end=overrides.get("end", FIXED_START + timedelta(minutes=1)),
        open=overrides.get("open", 100.0),
        high=overrides.get("high", 105.0),
        low=overrides.get("low", 95.0),
        close=overrides.get("close", 102.0),
        volume=overrides.get("volume", 12.5),
    )
