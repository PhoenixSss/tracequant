from dataclasses import FrozenInstanceError
from math import inf, nan
from typing import Literal

import pytest
from fixtures.domain import SAMPLE_END, SAMPLE_START, make_bar

from tracequant.domain import InstrumentId, OHLCVBar


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_bar_rejects_non_finite_numbers(
    field: Literal["open", "high", "low", "close", "volume"], value: float
) -> None:
    with pytest.raises(ValueError, match=f"{field} must be finite"):
        if field == "open":
            make_bar(open=value)
        elif field == "high":
            make_bar(high=value)
        elif field == "low":
            make_bar(low=value)
        elif field == "close":
            make_bar(close=value)
        else:
            make_bar(volume=value)


def test_bar_rejects_negative_volume() -> None:
    with pytest.raises(ValueError, match="volume must be non-negative"):
        make_bar(volume=-0.1)


@pytest.mark.parametrize(
    ("high", "low"),
    [(99.0, 90.0), (110.0, 106.0)],
)
def test_bar_rejects_invalid_ohlc_relationships(high: float, low: float) -> None:
    with pytest.raises(ValueError):
        make_bar(high=high, low=low)


@pytest.mark.parametrize(
    ("open_price", "high", "low", "close"),
    [
        (0.0, 1.0, -1.0, 0.5),
        (-2.0, -1.0, -3.0, -1.5),
    ],
)
def test_bar_allows_zero_and_negative_prices_when_ohlc_is_valid(
    open_price: float, high: float, low: float, close: float
) -> None:
    bar = make_bar(open=open_price, high=high, low=low, close=close)

    assert bar.open == open_price


def test_bar_requires_instrument_id() -> None:
    with pytest.raises(TypeError, match="instrument must be an InstrumentId"):
        OHLCVBar(
            instrument="BTCUSDT",  # type: ignore[arg-type]
            start=SAMPLE_START,
            end=SAMPLE_END,
            open=100.0,
            high=110.0,
            low=90.0,
            close=105.0,
            volume=12.5,
        )


def test_bar_is_immutable_hashable_and_fixture_is_function_scoped(
    sample_bar: OHLCVBar, instrument_id: InstrumentId
) -> None:
    assert sample_bar.instrument == instrument_id
    assert hash(sample_bar) == hash(make_bar(instrument=instrument_id))

    with pytest.raises(FrozenInstanceError):
        sample_bar.close = 0.0  # type: ignore[misc]
