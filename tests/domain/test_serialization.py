import json

import pytest
from fixtures.domain import make_bar, make_time_range

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_all_models_round_trip_through_json() -> None:
    instrument = InstrumentId("BTCUSDT")
    interval = make_time_range()
    bar = make_bar(instrument=instrument)

    assert (
        InstrumentId.from_dict(json.loads(json.dumps(instrument.to_dict())))
        == instrument
    )
    assert TimeRange.from_dict(json.loads(json.dumps(interval.to_dict()))) == interval
    assert OHLCVBar.from_dict(json.loads(json.dumps(bar.to_dict()))) == bar


def test_serialized_instrument_rejects_invalid_shared_inputs(
    invalid_instrument_values: tuple[str, ...],
) -> None:
    for raw in invalid_instrument_values:
        with pytest.raises(ValueError):
            InstrumentId.from_dict(json.loads(json.dumps(raw)))


def test_bar_serialization_has_stable_public_fields() -> None:
    assert list(make_bar().to_dict()) == [
        "instrument",
        "start",
        "end",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]


@pytest.mark.parametrize(
    "data",
    [
        {"instrument": "BTCUSDT"},
        {**make_bar().to_dict(), "trade_count": 1},
        {**make_bar().to_dict(), "volume": "12.5"},
        {**make_bar().to_dict(), "open": True},
    ],
)
def test_bar_from_dict_rejects_invalid_fields_and_types(
    data: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        OHLCVBar.from_dict(data)


def test_factories_return_isolated_objects() -> None:
    first = make_bar()
    second = make_bar()

    assert first == second
    assert first is not second
    assert first.instrument is not second.instrument
