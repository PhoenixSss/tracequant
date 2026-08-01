"""Deterministic domain model factories for tests."""

from datetime import UTC, datetime, timedelta

from quant_system.domain import InstrumentId, OHLCVBar, TimeRange

DEFAULT_INSTRUMENT_VALUE = "BTCUSDT"
DEFAULT_START = datetime(2026, 2, 28, 23, 45, tzinfo=UTC)
DEFAULT_END = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
LEAP_DAY_START = datetime(2028, 2, 29, 0, 0, tzinfo=UTC)


def instrument_id(value: str = DEFAULT_INSTRUMENT_VALUE) -> InstrumentId:
    return InstrumentId(value)


def time_range(
    *,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
) -> TimeRange:
    return TimeRange(start=start, end=end)


def ohlcv_bar(
    *,
    instrument: InstrumentId | None = None,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
    open: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 101.5,
    volume: float = 42.0,
) -> OHLCVBar:
    return OHLCVBar(
        instrument=instrument if instrument is not None else instrument_id(),
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def invalid_ohlcv_payload() -> dict[str, object]:
    payload: dict[str, object] = dict(ohlcv_bar().to_dict())
    payload["volume"] = -1.0
    return payload


def next_bar_start() -> datetime:
    return DEFAULT_END


def next_bar_end() -> datetime:
    return DEFAULT_END + timedelta(minutes=15)
