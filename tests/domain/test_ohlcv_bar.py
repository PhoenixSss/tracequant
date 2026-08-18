import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest

from tests.fixtures.domain import invalid_ohlcv_numeric_values, make_ohlcv_bar
from tracequant.domain import OHLCVBar


def test_bar_is_immutable_and_factory_calls_are_isolated(
    bar_factory: Callable[..., OHLCVBar],
) -> None:
    first = bar_factory()
    second = bar_factory()

    assert first == second
    assert first is not second
    assert first.instrument is not second.instrument
    with pytest.raises(FrozenInstanceError):
        setattr(first, "close", 99.0)


@pytest.mark.parametrize(("field", "value"), invalid_ohlcv_numeric_values())
def test_bar_rejects_invalid_numeric_values(field: str, value: float) -> None:
    values = {
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 102.0,
        "volume": 12.5,
    }
    values[field] = value

    with pytest.raises(ValueError):
        make_ohlcv_bar(
            open=values["open"],
            high=values["high"],
            low=values["low"],
            close=values["close"],
            volume=values["volume"],
        )


@pytest.mark.parametrize(
    ("high", "low"),
    [
        (99.0, 95.0),
        (105.0, 103.0),
    ],
)
def test_bar_rejects_invalid_ohlc_relationships(high: float, low: float) -> None:
    with pytest.raises(ValueError):
        make_ohlcv_bar(high=high, low=low)


@pytest.mark.parametrize(
    ("open", "high", "low", "close"),
    [
        (0.0, 1.0, -1.0, 0.5),
        (-3.0, -1.0, -4.0, -2.0),
    ],
)
def test_bar_allows_zero_and_negative_finite_prices(
    open: float,
    high: float,
    low: float,
    close: float,
) -> None:
    bar = make_ohlcv_bar(open=open, high=high, low=low, close=close)

    assert bar.open == open


def test_bar_serialization_is_json_compatible_stable_and_round_trips(
    bar_factory: Callable[..., OHLCVBar],
) -> None:
    bar = bar_factory()

    payload = bar.to_dict()

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
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
    assert OHLCVBar.from_dict(payload) == bar


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("volume"),
        lambda payload: payload.update(extra=True),
        lambda payload: payload.update(open=1),
        lambda payload: payload.update(instrument=None),
        lambda payload: payload.update(start="2026-02-28T23:59:00"),
    ],
)
def test_bar_rejects_invalid_serialized_data(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = make_ohlcv_bar().to_dict()
    mutation(payload)

    with pytest.raises((TypeError, ValueError)):
        OHLCVBar.from_dict(payload)
