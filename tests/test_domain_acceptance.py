import json
from datetime import UTC, datetime

import pytest
from fixtures.domain import BarFactory

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_public_domain_models_support_validated_json_round_trip(
    bar_factory: BarFactory,
) -> None:
    instrument = InstrumentId("  btcusdt  ")
    interval = TimeRange(
        start=datetime(2026, 8, 28, 0, 0, tzinfo=UTC),
        end=datetime(2026, 8, 28, 0, 1, tzinfo=UTC),
    )
    bar = bar_factory(
        instrument=instrument,
        start=interval.start,
        end=interval.end,
        open=0.0,
        high=1.0,
        low=-1.0,
        close=0.5,
    )

    instrument_data = json.loads(json.dumps(instrument.to_dict()))
    interval_data = json.loads(json.dumps(interval.to_dict()))
    bar_data = json.loads(json.dumps(bar.to_dict()))

    assert str(instrument) == "BTCUSDT"
    assert InstrumentId.from_dict(instrument_data) == instrument
    assert TimeRange.from_dict(interval_data) == interval
    assert OHLCVBar.from_dict(bar_data) == bar

    with pytest.raises(ValueError):
        InstrumentId("BTC/USDT")
    with pytest.raises(ValueError):
        TimeRange(interval.start, interval.start)
    with pytest.raises(ValueError):
        bar_factory(high=-2.0)
