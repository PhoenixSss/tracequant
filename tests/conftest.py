from collections.abc import Callable

import pytest
from fixtures.domain import make_ohlcv_bar

from tracequant.domain import OHLCVBar


@pytest.fixture
def ohlcv_bar_factory() -> Callable[..., OHLCVBar]:
    """Return the deterministic bar factory with function-scoped isolation."""
    return make_ohlcv_bar
