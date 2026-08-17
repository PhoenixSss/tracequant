import json

import pytest
from fixtures.domain import make_ohlcv_bar

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_instrument_serialization_round_trip() -> None:
    instrument = InstrumentId("ethusdt")

    assert InstrumentId.from_dict(instrument.to_dict()) == instrument


def test_time_range_serialization_round_trip(sample_bar: OHLCVBar) -> None:
    interval = sample_bar.time_range

    assert interval.to_dict() == {
        "start": "2026-02-28T23:59:00Z",
        "end": "2026-03-01T00:00:00Z",
    }
    assert TimeRange.from_dict(interval.to_dict()) == interval


def test_bar_serialization_is_stable_json_and_round_trips(sample_bar: OHLCVBar) -> None:
    expected = {
        "instrument": "BTCUSDT",
        "start": "2026-02-28T23:59:00Z",
        "end": "2026-03-01T00:00:00Z",
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 12.5,
    }

    assert sample_bar.to_dict() == expected
    assert json.loads(json.dumps(sample_bar.to_dict(), allow_nan=False)) == expected
    assert OHLCVBar.from_dict(expected) == sample_bar


def test_deserialization_normalizes_aware_offset_via_utc_tool() -> None:
    payload = make_ohlcv_bar().to_dict()
    payload["start"] = "2026-03-01T07:59:00+08:00"
    payload["end"] = "2026-03-01T08:00:00+08:00"

    assert OHLCVBar.from_dict(payload) == make_ohlcv_bar()


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2026-01-01T00:00:00Z"},
        {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-02T00:00:00Z",
            "extra": "value",
        },
        {"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00Z"},
        {"start": 1, "end": "2026-01-02T00:00:00Z"},
    ],
)
def test_time_range_rejects_invalid_serialized_data(payload: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        TimeRange.from_dict(payload)


def test_bar_rejects_missing_extra_and_wrong_type_fields(
    sample_bar: OHLCVBar,
) -> None:
    missing = sample_bar.to_dict()
    del missing["volume"]
    extra = {**sample_bar.to_dict(), "trade_count": 1}
    wrong_type = {**sample_bar.to_dict(), "volume": 1}

    for payload in (missing, extra, wrong_type):
        with pytest.raises((TypeError, ValueError)):
            OHLCVBar.from_dict(payload)


def test_bar_rejects_invalid_shared_payload(
    invalid_bar_payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="volume must be non-negative"):
        OHLCVBar.from_dict(invalid_bar_payload)
