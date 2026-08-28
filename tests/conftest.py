from typing import Unpack

import pytest
from fixtures.domain import BarFactory, BarOverrides, make_ohlcv_bar

from tracequant.domain import OHLCVBar


@pytest.fixture
def bar_factory() -> BarFactory:
    """Provide the shared deterministic bar factory with function scope."""

    def factory(**overrides: Unpack[BarOverrides]) -> OHLCVBar:
        return make_ohlcv_bar(**overrides)

    return factory
