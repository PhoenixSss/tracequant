import pytest
from fixtures.domain import ohlcv_bar, time_range

from quant_system.domain import OHLCVBar, TimeRange


@pytest.fixture
def sample_time_range() -> TimeRange:
    return time_range()


@pytest.fixture
def sample_ohlcv_bar() -> OHLCVBar:
    return ohlcv_bar()
