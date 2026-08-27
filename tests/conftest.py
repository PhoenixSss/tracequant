"""Shared fixtures used by multiple domain test modules."""

import pytest
from fixtures.domain import make_instrument, make_ohlcv_bar, make_time_range

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


@pytest.fixture(scope="function")
def sample_instrument() -> InstrumentId:
    return make_instrument()


@pytest.fixture(scope="function")
def sample_time_range() -> TimeRange:
    return make_time_range()


@pytest.fixture(scope="function")
def sample_ohlcv_bar() -> OHLCVBar:
    return make_ohlcv_bar()
