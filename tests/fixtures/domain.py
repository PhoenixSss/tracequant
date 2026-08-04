"""Deterministic factories for domain model tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant_system.domain import InstrumentId, OHLCVBar, TimeRange

DEFAULT_INSTRUMENT = InstrumentId("BTCUSDT")
DEFAULT_START = datetime(2026, 2, 28, 23, 45, tzinfo=UTC)
DEFAULT_END = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


def make_time_range(
    *, start: datetime = DEFAULT_START, end: datetime = DEFAULT_END
) -> TimeRange:
    return TimeRange(start=start, end=end)


def make_ohlcv_bar(
    *,
    instrument: InstrumentId = DEFAULT_INSTRUMENT,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    open: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 101.5,
    volume: float = 12.25,
) -> OHLCVBar:
    return OHLCVBar(
        instrument=instrument,
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def make_ohlcv_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = dict(make_ohlcv_bar().to_dict())
    payload.update(overrides)
    return payload


def make_next_bar() -> OHLCVBar:
    return make_ohlcv_bar(
        start=DEFAULT_END,
        end=DEFAULT_END + timedelta(minutes=15),
        open=101.5,
        high=104.0,
        low=100.0,
        close=103.0,
        volume=9.5,
    )
