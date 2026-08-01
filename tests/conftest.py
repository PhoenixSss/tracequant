from collections.abc import Callable

import pytest

from quant_system.domain import OHLCVBar
from tests.fixtures.domain import make_ohlcv_bar


@pytest.fixture
def ohlcv_bar_factory() -> Callable[[], OHLCVBar]:
    return make_ohlcv_bar
