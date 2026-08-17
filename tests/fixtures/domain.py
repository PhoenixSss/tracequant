"""Small deterministic factories for public domain models."""

from datetime import UTC, datetime

from tracequant.domain import InstrumentId, OHLCVBar

SAMPLE_START = datetime(2026, 2, 28, 23, 59, tzinfo=UTC)
SAMPLE_END = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


def make_ohlcv_bar(
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
    """Return a fresh valid bar while allowing every field to be explicit."""
    return OHLCVBar(
        instrument=instrument or InstrumentId("BTCUSDT"),
        start=start,
        end=end,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def make_invalid_ohlcv_payload(**overrides: object) -> dict[str, object]:
    """Return a fresh invalid serialization payload with explicit overrides."""
    payload = make_ohlcv_bar().to_dict()
    payload["volume"] = -1.0
    payload.update(overrides)
    return payload
