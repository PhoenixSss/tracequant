import json
from datetime import UTC, datetime

import pytest
from fixtures.domain import make_instrument_id, make_ohlcv_bar, make_time_range

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_instrument_id_scalar_round_trip() -> None:
    instrument = make_instrument_id("ethusdt")

    encoded = instrument.to_json_value()

    assert encoded == "ETHUSDT"
    assert InstrumentId.from_json_value(encoded) == instrument
    assert json.loads(json.dumps(encoded)) == encoded


def test_time_range_stable_json_round_trip() -> None:
    interval = make_time_range()

    encoded = interval.to_dict()

    assert encoded == {
        "start": "2024-02-29T23:45:00Z",
        "end": "2024-03-01T00:00:00Z",
    }
    assert TimeRange.from_dict(json.loads(json.dumps(encoded))) == interval


def test_deserialization_uses_existing_utc_parser_to_normalize_offsets() -> None:
    interval = TimeRange.from_dict(
        {
            "start": "2024-03-01T07:45:00+08:00",
            "end": "2024-03-01T08:00:00+08:00",
        }
    )

    assert interval.start == datetime(2024, 2, 29, 23, 45, tzinfo=UTC)
    assert interval.end == datetime(2024, 3, 1, 0, 0, tzinfo=UTC)


def test_ohlcv_bar_stable_json_round_trip(sample_bar: OHLCVBar) -> None:
    encoded = sample_bar.to_dict()

    assert encoded == {
        "instrument": "BTCUSDT",
        "start": "2024-02-29T23:45:00Z",
        "end": "2024-03-01T00:00:00Z",
        "open": 60_000.0,
        "high": 61_000.0,
        "low": 59_500.0,
        "close": 60_500.0,
        "volume": 125.5,
    }
    assert OHLCVBar.from_dict(json.loads(json.dumps(encoded))) == sample_bar


@pytest.mark.parametrize(
    "data",
    [
        {"start": "2024-01-01T00:00:00Z"},
        {
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-01T00:15:00Z",
            "extra": True,
        },
        {"start": 1, "end": "2024-01-01T00:15:00Z"},
        {"start": "2024-01-01T00:00:00", "end": "2024-01-01T00:15:00Z"},
    ],
)
def test_time_range_deserialization_rejects_invalid_data(data: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        TimeRange.from_dict(data)


@pytest.mark.parametrize(
    "mutation",
    [
        ("missing", None),
        ("extra", True),
        ("volume", 1),
        ("instrument", None),
        ("start", "2024-02-29T23:45:00"),
    ],
)
def test_bar_deserialization_rejects_invalid_data(
    sample_bar: OHLCVBar, mutation: tuple[str, object]
) -> None:
    data = sample_bar.to_dict()
    field, value = mutation
    if field == "missing":
        del data["close"]
    else:
        data[field] = value

    with pytest.raises((TypeError, ValueError)):
        OHLCVBar.from_dict(data)


def test_factory_calls_do_not_share_serialized_state(sample_bar: OHLCVBar) -> None:
    first = sample_bar.to_dict()
    second = make_ohlcv_bar().to_dict()

    first["instrument"] = "ETHUSDT"

    assert second["instrument"] == "BTCUSDT"
