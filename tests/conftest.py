from collections.abc import Iterator

import pytest
from fixtures.domain import make_ohlcv_bar

from tracequant.domain import OHLCVBar


@pytest.fixture
def sample_bar() -> Iterator[OHLCVBar]:
    """Provide a fresh, deterministic OHLCV bar with function scope."""
    yield make_ohlcv_bar()
