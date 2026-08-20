"""Factories for explicit, deterministic domain test data."""

from datetime import UTC, datetime

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange

DEFAULT_START = datetime(2026, 2, 28, 23, 45, tzinfo=UTC)
DEFAULT_END = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


def make_instrument(value: str = "BTCUSDT") -> InstrumentId:
    """Build an instrument with an explicitly overridable identifier."""
    return InstrumentId(value)


def make_time_range(
    *, start: datetime = DEFAULT_START, end: datetime = DEFAULT_END
) -> TimeRange:
    """Build a fixed UTC time range with explicitly overridable fields."""
    return TimeRange(start=start, end=end)


def make_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    open: float = 100.0,
    high: float = 110.0,
    low: float = 90.0,
    close: float = 105.0,
    volume: float = 42.5,
) -> OHLCVBar:
    """Build a fixed OHLCV bar while allowing every field to be overridden."""
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


def invalid_bar_payload(**overrides: object) -> dict[str, object]:
    """Build an explicit invalid-input payload for deserialization tests."""
    payload: dict[str, object] = {
        "instrument": "BTCUSDT",
        "start": "2026-02-28T23:45:00Z",
        "end": "2026-03-01T00:00:00Z",
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 42.5,
    }
    payload.update(overrides)
    return payload
