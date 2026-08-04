import json
from datetime import UTC, datetime

import pytest

from quant_system.core import InstrumentId, OHLCVBar, TimeRange
from tests.fixtures.domain import make_invalid_bar_payload, make_ohlcv_bar


def test_instrument_serializes_as_a_string(valid_instrument: InstrumentId) -> None:
    assert valid_instrument.to_dict() == "BTCUSDT"
    assert InstrumentId.from_dict(valid_instrument.to_dict()) == valid_instrument


def test_time_range_serialization_is_stable_and_json_compatible(
    valid_time_range: TimeRange,
) -> None:
    payload = valid_time_range.to_dict()
    assert list(payload) == ["start", "end"]
    assert json.loads(json.dumps(payload)) == payload
    assert TimeRange.from_dict(payload) == valid_time_range


def test_ohlcv_round_trip_is_stable_and_json_compatible(
    valid_ohlcv_bar: OHLCVBar,
) -> None:
    payload = valid_ohlcv_bar.to_dict()
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
    encoded = json.dumps(payload, allow_nan=False)
    assert OHLCVBar.from_dict(json.loads(encoded)) == valid_ohlcv_bar


@pytest.mark.parametrize(
    "payload",
    [
        make_invalid_bar_payload(),
        make_invalid_bar_payload(extra=True),
        make_invalid_bar_payload(start="2026-07-19T07:01:20"),
        make_invalid_bar_payload(open=1),
    ],
)
def test_ohlcv_deserialization_rejects_missing_extra_naive_or_wrong_types(
    payload: dict[str, object],
) -> None:
    if payload is not None:
        payload = dict(payload)
    if "extra" in payload:
        with pytest.raises(ValueError):
            OHLCVBar.from_dict(payload)
    elif payload["start"] == "2026-07-19T07:01:20":
        with pytest.raises(ValueError):
            OHLCVBar.from_dict(payload)
    elif payload["open"] == 1:
        with pytest.raises(TypeError):
            OHLCVBar.from_dict(payload)
    else:
        payload.pop("volume")
        with pytest.raises(ValueError):
            OHLCVBar.from_dict(payload)


def test_deserialization_accepts_utc_offset_through_existing_utc_api() -> None:
    payload = make_ohlcv_bar().to_dict()
    payload["start"] = "2026-07-19T15:01:20+08:00"
    payload["end"] = "2026-07-19T15:06:20+08:00"
    result = OHLCVBar.from_dict(payload)
    assert result.start == datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC)
