"""Shared pytest fixtures used by multiple model test modules."""

import pytest

from quant_system.core import InstrumentId, OHLCVBar, TimeRange
from tests.fixtures.domain import make_instrument, make_ohlcv_bar, make_time_range


@pytest.fixture
def valid_instrument() -> InstrumentId:
    return make_instrument()


@pytest.fixture
def valid_time_range() -> TimeRange:
    return make_time_range()


@pytest.fixture
def valid_ohlcv_bar() -> OHLCVBar:
    return make_ohlcv_bar()
