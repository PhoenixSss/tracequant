"""Small deterministic factories for public domain-model tests."""

from datetime import UTC, datetime
from typing import Protocol

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange

FIXED_START = datetime(2024, 2, 29, 23, 45, tzinfo=UTC)
FIXED_END = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)


class InstrumentFactory(Protocol):
    def __call__(self, value: str = "BTCUSDT") -> InstrumentId: ...


class TimeRangeFactory(Protocol):
    def __call__(
        self, *, start: datetime = FIXED_START, end: datetime = FIXED_END
    ) -> TimeRange: ...


class BarFactory(Protocol):
    def __call__(
        self,
        *,
        instrument: InstrumentId | None = None,
        start: datetime = FIXED_START,
        end: datetime = FIXED_END,
        open: float = 100.0,
        high: float = 110.0,
        low: float = 90.0,
        close: float = 105.0,
        volume: float = 12.5,
    ) -> OHLCVBar: ...


def make_instrument(value: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(value)


def make_time_range(
    *, start: datetime = FIXED_START, end: datetime = FIXED_END
) -> TimeRange:
    return TimeRange(start=start, end=end)


def make_bar(
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
        instrument=instrument if instrument is not None else make_instrument(),
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
