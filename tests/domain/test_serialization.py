"""Strict JSON-compatible domain serialization."""

import json

import pytest
from fixtures.domain import make_ohlcv_bar, make_time_range

from tracequant.domain import OHLCVBar


def test_bar_serialization_is_stable_and_json_compatible(
    ohlcv_bar: OHLCVBar,
) -> None:
    expected = {
        "instrument": "BTCUSDT",
        "start": "2026-02-28T23:59:00Z",
        "end": "2026-03-01T00:00:00Z",
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 102.0,
        "volume": 12.5,
    }

    assert ohlcv_bar.to_dict() == expected
    assert json.loads(json.dumps(ohlcv_bar.to_dict(), allow_nan=False)) == expected


def test_bar_round_trip_preserves_float_values(ohlcv_bar: OHLCVBar) -> None:
    encoded = json.dumps(ohlcv_bar.to_dict(), allow_nan=False)

    assert OHLCVBar.from_dict(json.loads(encoded)) == ohlcv_bar


@pytest.mark.parametrize(
    "data",
    [
        {},
        {**make_ohlcv_bar().to_dict(), "unexpected": 1.0},
        {**make_ohlcv_bar().to_dict(), "volume": "12.5"},
        {**make_ohlcv_bar().to_dict(), "volume": 12},
        {**make_ohlcv_bar().to_dict(), "instrument": None},
        {**make_ohlcv_bar().to_dict(), "start": "2026-02-28T23:59:00"},
    ],
)
def test_bar_rejects_missing_extra_or_wrong_serialized_fields(data: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        OHLCVBar.from_dict(data)


def test_shared_factories_are_deterministic_and_isolated() -> None:
    first_interval = make_time_range()
    second_interval = make_time_range()
    first_bar = make_ohlcv_bar()
    second_bar = make_ohlcv_bar()

    assert first_interval == second_interval
    assert first_interval is not second_interval
    assert first_bar == second_bar
    assert first_bar is not second_bar
    assert first_bar.instrument is not second_bar.instrument
