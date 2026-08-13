"""Instrument identifier invariants."""

from dataclasses import FrozenInstanceError

import pytest
from fixtures.domain import make_instrument_id

from tracequant.domain import InstrumentId


def test_instrument_normalizes_whitespace_and_case() -> None:
    instrument = InstrumentId("  btcusdt  ")

    assert instrument.value == "BTCUSDT"
    assert str(instrument) == "BTCUSDT"


@pytest.mark.parametrize(
    "value", ["", "   ", "BTC-USDT", "BTC/USDT", "比特币", "ß", "A" * 33]
)
def test_instrument_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        InstrumentId(value)


def test_instrument_rejects_non_string_input() -> None:
    with pytest.raises(TypeError, match="must be a string"):
        InstrumentId.from_dict(123)


def test_instrument_is_immutable_hashable_and_orderable() -> None:
    instrument_id = make_instrument_id()

    with pytest.raises(FrozenInstanceError):
        instrument_id.value = "ETHUSDT"  # type: ignore[misc]

    assert {instrument_id, InstrumentId("BTCUSDT")} == {instrument_id}
    assert instrument_id < InstrumentId("ETHUSDT")


def test_instrument_string_round_trip() -> None:
    instrument_id = make_instrument_id()

    assert InstrumentId.from_dict(instrument_id.to_dict()) == instrument_id


def test_instrument_factory_returns_distinct_objects() -> None:
    first = make_instrument_id()
    second = make_instrument_id()

    assert first == second
    assert first is not second
