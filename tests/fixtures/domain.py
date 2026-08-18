"""Factories for explicit, deterministic domain test values."""

from datetime import UTC, datetime, timedelta

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange

DEFAULT_START = datetime(2026, 2, 28, 23, 59, tzinfo=UTC)


def make_instrument(value: str = "BTCUSDT") -> InstrumentId:
    return InstrumentId(value)


def make_time_range(
    *,
    start: datetime = DEFAULT_START,
    end: datetime | None = None,
) -> TimeRange:
    return TimeRange(start=start, end=end or start + timedelta(minutes=1))


def make_ohlcv_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = DEFAULT_START,
    end: datetime | None = None,
    open: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: float = 12.5,
) -> OHLCVBar:
    return OHLCVBar(
        instrument=instrument or make_instrument(),
        start=start,
        end=end or start + timedelta(minutes=1),
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def invalid_ohlcv_numeric_values() -> tuple[tuple[str, float], ...]:
    return (
        ("open", float("nan")),
        ("high", float("inf")),
        ("low", float("-inf")),
        ("close", float("nan")),
        ("volume", -1.0),
    )
