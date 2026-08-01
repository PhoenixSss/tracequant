from dataclasses import FrozenInstanceError

import pytest

from quant_system.domain import DomainValidationError, InstrumentId
from tests.fixtures.domain import INVALID_INSTRUMENT_VALUES, make_instrument


def test_instrument_id_normalizes_whitespace_and_case() -> None:
    instrument = InstrumentId(" btcusdt ")

    assert instrument.value == "BTCUSDT"
    assert str(instrument) == "BTCUSDT"


def test_instrument_id_is_immutable_comparable_and_hashable() -> None:
    instrument = make_instrument()

    with pytest.raises(FrozenInstanceError):
        instrument.value = "ETHUSDT"  # type: ignore[misc]

    assert instrument == InstrumentId("btcusdt")
    assert {instrument, InstrumentId("BTCUSDT")} == {InstrumentId("BTCUSDT")}
    assert sorted([InstrumentId("ETHUSDT"), InstrumentId("BTCUSDT")]) == [
        InstrumentId("BTCUSDT"),
        InstrumentId("ETHUSDT"),
    ]


@pytest.mark.parametrize("value", INVALID_INSTRUMENT_VALUES)
def test_instrument_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(DomainValidationError):
        InstrumentId(value)


def test_instrument_id_rejects_non_string_input() -> None:
    with pytest.raises(DomainValidationError, match="instrument must be a string"):
        InstrumentId(123)  # type: ignore[arg-type]


def test_instrument_id_serializes_as_string() -> None:
    instrument = make_instrument("ethusdc")

    assert instrument.to_dict() == "ETHUSDC"
    assert InstrumentId.from_dict("ethusdc") == instrument


def test_instrument_id_from_dict_rejects_non_string_payload() -> None:
    with pytest.raises(DomainValidationError, match="payload must be a string"):
        InstrumentId.from_dict({"value": "BTCUSDT"})
