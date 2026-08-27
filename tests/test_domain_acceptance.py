import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_public_domain_models_support_validated_json_round_trip(
    ohlcv_bar_factory: Callable[..., OHLCVBar],
) -> None:
    instrument = InstrumentId("  btcusdt  ")
    time_range = TimeRange(
        start=datetime(2024, 2, 29, 23, 45, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
    )
    bar = ohlcv_bar_factory(
        instrument=instrument,
        start=time_range.start,
        end=time_range.end,
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=12.5,
    )

    assert instrument == InstrumentId.from_json_value(instrument.to_json_value())
    assert time_range == TimeRange.from_dict(time_range.to_dict())
    assert bar == OHLCVBar.from_dict(bar.to_dict())
    payload = {
        "instrument": instrument.to_json_value(),
        "time_range": time_range.to_dict(),
        "bar": bar.to_dict(),
    }
    assert json.loads(json.dumps(payload)) == payload

    with pytest.raises(ValueError):
        InstrumentId("BTC-USDT")
    with pytest.raises(ValueError):
        TimeRange(time_range.start, time_range.start)
    with pytest.raises(ValueError):
        ohlcv_bar_factory(
            instrument=instrument,
            start=time_range.start,
            end=time_range.end,
            open=100.0,
            high=99.0,
            low=90.0,
            close=95.0,
            volume=12.5,
        )
