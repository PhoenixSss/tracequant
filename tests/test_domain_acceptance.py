"""Critical Outcome coverage for the public domain API."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_public_domain_models_support_validated_json_round_trip() -> None:
    start = datetime(2024, 2, 29, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    instrument = InstrumentId("  btcusdt ")
    time_range = TimeRange(start=start, end=end)
    bar = OHLCVBar(
        instrument=instrument,
        start=time_range.start,
        end=time_range.end,
        open=50_000.0,
        high=50_025.0,
        low=49_975.0,
        close=50_010.0,
        volume=12.5,
    )

    instrument_data = instrument.to_dict()
    time_range_data = time_range.to_dict()
    bar_data = bar.to_dict()
    json.dumps(instrument_data)
    json.dumps(time_range_data)
    json.dumps(bar_data)

    assert InstrumentId.from_dict(instrument_data) == instrument
    assert TimeRange.from_dict(time_range_data) == time_range
    assert OHLCVBar.from_dict(bar_data) == bar

    with pytest.raises(ValueError):
        InstrumentId("BTC-USDT")
    with pytest.raises(ValueError):
        TimeRange(start=end, end=start)
    with pytest.raises(ValueError):
        OHLCVBar(
            instrument=instrument,
            start=start,
            end=end,
            open=50_000.0,
            high=49_000.0,
            low=49_500.0,
            close=49_750.0,
            volume=12.5,
        )


def test_acceptance_uses_shared_bar_fixture(sample_ohlcv_bar: OHLCVBar) -> None:
    assert sample_ohlcv_bar.instrument == InstrumentId("BTCUSDT")
