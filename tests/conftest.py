from collections.abc import Iterator

import pytest
from fixtures.domain import make_bar

from tracequant.domain import InstrumentId, OHLCVBar


@pytest.fixture
def instrument_id() -> InstrumentId:
    return InstrumentId("BTCUSDT")


@pytest.fixture
def invalid_instrument_values() -> tuple[str, ...]:
    return ("", "   ", "BTC-USDT", "BTC/USDT", "比特币", "ß", "A" * 33)


@pytest.fixture
def sample_bar(instrument_id: InstrumentId) -> Iterator[OHLCVBar]:
    yield make_bar(instrument=instrument_id)
