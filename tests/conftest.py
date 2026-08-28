"""Fixtures that are genuinely shared across domain test modules."""

import pytest
from fixtures.domain import (
    BarFactory,
    InstrumentFactory,
    TimeRangeFactory,
    make_bar,
    make_instrument,
    make_time_range,
)


@pytest.fixture
def instrument_factory() -> InstrumentFactory:
    return make_instrument


@pytest.fixture
def time_range_factory() -> TimeRangeFactory:
    return make_time_range


@pytest.fixture
def bar_factory() -> BarFactory:
    return make_bar
