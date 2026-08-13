"""Function-scoped fixtures reused across domain test modules."""

import pytest
from fixtures.domain import make_ohlcv_bar

from tracequant.domain import OHLCVBar


@pytest.fixture
def ohlcv_bar() -> OHLCVBar:
    return make_ohlcv_bar()
