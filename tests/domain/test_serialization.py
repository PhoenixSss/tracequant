import json

import pytest

from tests.fixtures.domain import invalid_bar_payload, make_bar
from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_public_domain_import_path_exposes_only_initial_models() -> None:
    from tracequant import domain

    assert domain.__all__ == ["InstrumentId", "OHLCVBar", "TimeRange"]


def test_instrument_string_serialization_round_trip() -> None:
    instrument = InstrumentId(" btcusdt ")

    assert instrument.to_dict() == "BTCUSDT"
    assert InstrumentId.from_dict(instrument.to_dict()) == instrument


def test_instrument_rejects_non_string_serialized_data() -> None:
    with pytest.raises(TypeError, match="^instrument must be a string$"):
        InstrumentId.from_dict(123)


def test_time_range_serialization_is_stable_and_round_trips() -> None:
    interval = TimeRange.from_dict(
        {
            "start": "2026-03-01T07:45:00+08:00",
            "end": "2026-03-01T08:00:00+08:00",
        }
    )

    assert interval.to_dict() == {
        "start": "2026-02-28T23:45:00Z",
        "end": "2026-03-01T00:00:00Z",
    }
    assert TimeRange.from_dict(interval.to_dict()) == interval


def test_shared_sample_bar_is_json_compatible_and_round_trips(
    sample_bar: OHLCVBar,
) -> None:
    payload = sample_bar.to_dict()

    encoded = json.dumps(payload, allow_nan=False, sort_keys=True)

    assert json.loads(encoded) == payload
    assert OHLCVBar.from_dict(json.loads(encoded)) == sample_bar
    assert list(payload) == [
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
    "payload",
    [
        {"start": "2026-01-01T00:00:00Z"},
        {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T00:01:00Z",
            "extra": "value",
        },
        {"start": "2026-01-01T00:00:00", "end": "2026-01-01T00:01:00Z"},
        {"start": 1, "end": "2026-01-01T00:01:00Z"},
    ],
)
def test_time_range_rejects_invalid_serialized_data(
    payload: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        TimeRange.from_dict(payload)


@pytest.mark.parametrize(
    "payload",
    [
        invalid_bar_payload(volume=-1.0),
        invalid_bar_payload(high=float("nan")),
        invalid_bar_payload(start="2026-02-28T23:45:00"),
        invalid_bar_payload(open=100),
        {key: value for key, value in invalid_bar_payload().items() if key != "close"},
        {**invalid_bar_payload(), "extra": "value"},
    ],
)
def test_bar_rejects_invalid_serialized_data(payload: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        OHLCVBar.from_dict(payload)


def test_factory_calls_and_serialized_payloads_are_isolated() -> None:
    first = make_bar()
    second = make_bar()
    first_payload = first.to_dict()
    second_payload = second.to_dict()

    first_payload["instrument"] = "ETHUSDT"

    assert first is not second
    assert first.instrument is not second.instrument
    assert second_payload["instrument"] == "BTCUSDT"
    assert second.instrument == InstrumentId("BTCUSDT")
