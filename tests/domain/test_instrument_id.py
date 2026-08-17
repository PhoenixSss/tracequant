from dataclasses import FrozenInstanceError

import pytest

from tracequant.domain import InstrumentId


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" btcusdt ", "BTCUSDT"), ("Eth123", "ETH123")],
)
def test_instrument_id_normalizes_input(raw: str, expected: str) -> None:
    instrument = InstrumentId(raw)

    assert str(instrument) == expected
    assert instrument.to_dict() == expected


def test_instrument_id_rejects_invalid_shared_inputs(
    invalid_instrument_values: tuple[str, ...],
) -> None:
    for raw in invalid_instrument_values:
        with pytest.raises(ValueError):
            InstrumentId(raw)


def test_instrument_id_rejects_non_string_input() -> None:
    with pytest.raises(TypeError, match="instrument must be a string"):
        InstrumentId.from_dict(123)


def test_instrument_id_is_immutable_comparable_and_hashable(
    instrument_id: InstrumentId,
) -> None:
    assert instrument_id == InstrumentId("btcusdt")
    assert {instrument_id, InstrumentId("BTCUSDT")} == {InstrumentId("BTCUSDT")}

    with pytest.raises(FrozenInstanceError):
        instrument_id.value = "ETHUSDT"  # type: ignore[misc]
