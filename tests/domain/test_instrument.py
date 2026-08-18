from dataclasses import FrozenInstanceError

import pytest

from tests.fixtures.domain import make_instrument
from tracequant.domain import InstrumentId


def test_instrument_normalizes_and_serializes_as_string() -> None:
    instrument = make_instrument("  btcusdt  ")

    assert str(instrument) == "BTCUSDT"
    assert instrument.to_json_value() == "BTCUSDT"
    assert InstrumentId.from_json_value("BTCUSDT") == instrument


@pytest.mark.parametrize("value", ["", "   ", "BTC-USDT", "BTC/USDT", "比特币", "ß"])
def test_instrument_rejects_empty_or_invalid_characters(value: str) -> None:
    with pytest.raises(ValueError):
        InstrumentId(value)


def test_instrument_rejects_values_over_conservative_limit() -> None:
    with pytest.raises(ValueError, match="at most 32"):
        InstrumentId("A" * 33)


def test_instrument_is_immutable_hashable_and_orderable() -> None:
    first = InstrumentId("BTCUSDT")
    second = InstrumentId("ETHUSDT")

    assert sorted([second, first]) == [first, second]
    assert {first, InstrumentId("btcusdt")} == {first}
    with pytest.raises(FrozenInstanceError):
        setattr(first, "value", "ETHUSDT")


@pytest.mark.parametrize("value", [None, 1, True])
def test_instrument_rejects_non_string_input(value: object) -> None:
    with pytest.raises(TypeError):
        InstrumentId.from_json_value(value)
