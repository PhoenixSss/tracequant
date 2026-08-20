"""Shared pytest fixtures used by multiple test modules."""

import pytest

from tests.fixtures.domain import make_bar
from tracequant.domain import OHLCVBar


@pytest.fixture
def sample_bar() -> OHLCVBar:
    """Return a fresh deterministic bar for each requesting test."""
    return make_bar()
