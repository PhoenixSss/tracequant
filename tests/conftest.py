"""Shared pytest fixtures used by more than one test module."""

import pytest
from fixtures.domain import make_invalid_ohlcv_payload, make_ohlcv_bar

from tracequant.domain import OHLCVBar


@pytest.fixture
def sample_bar() -> OHLCVBar:
    """Return a fresh deterministic valid bar for each test."""
    return make_ohlcv_bar()


@pytest.fixture
def invalid_bar_payload() -> dict[str, object]:
    """Return a fresh deterministic invalid payload for each test."""
    return make_invalid_ohlcv_payload()
