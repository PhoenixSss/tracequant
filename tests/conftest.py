"""Shared pytest fixtures used by multiple test modules."""

from collections.abc import Callable

import pytest

from tests.fixtures.domain import make_ohlcv_bar
from tracequant.domain import OHLCVBar


@pytest.fixture
def bar_factory() -> Callable[..., OHLCVBar]:
    """Return a function-scoped deterministic OHLCV bar factory."""
    return make_ohlcv_bar
