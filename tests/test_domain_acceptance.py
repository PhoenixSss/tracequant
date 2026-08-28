import json
from datetime import UTC, datetime

import pytest
from fixtures.domain import BarFactory, InstrumentFactory, TimeRangeFactory

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_public_domain_models_support_validated_json_round_trip(
    instrument_factory: InstrumentFactory,
    time_range_factory: TimeRangeFactory,
    bar_factory: BarFactory,
) -> None:
    instrument = instrument_factory("  btcusdt  ")
    interval = time_range_factory(
        start=datetime(2024, 2, 29, 23, 45, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
    )
    bar = bar_factory(instrument=instrument, start=interval.start, end=interval.end)

    assert str(instrument) == "BTCUSDT"
    for model in (instrument, interval, bar):
        serialized = model.to_dict()
        json.dumps(serialized, allow_nan=False)
        assert type(model).from_dict(serialized) == model

    with pytest.raises(ValueError):
        InstrumentId("BTC-USDT")
    with pytest.raises(ValueError):
        TimeRange(start=interval.start, end=interval.start)
    with pytest.raises(ValueError):
        bar_factory(high=99.0)


def test_public_import_path_exposes_only_the_initial_domain_models() -> None:
    assert InstrumentId.__module__ == "tracequant.domain.models"
    assert TimeRange.__module__ == "tracequant.domain.models"
    assert OHLCVBar.__module__ == "tracequant.domain.models"
