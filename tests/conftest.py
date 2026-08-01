from collections.abc import Callable

import pytest

from quant_system.domain import InstrumentId
from tests.fixtures.domain import make_instrument


@pytest.fixture
def instrument_factory() -> Callable[[str], InstrumentId]:
    return make_instrument
