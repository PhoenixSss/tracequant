from collections.abc import Callable

import pytest
from fixtures.domain import make_ohlcv_bar

from quant_system.domain import OHLCVBar


@pytest.fixture
def ohlcv_bar_factory() -> Callable[[], OHLCVBar]:
    return make_ohlcv_bar
