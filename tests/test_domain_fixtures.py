from collections.abc import Callable

from fixtures.domain import make_next_bar, make_ohlcv_bar, make_time_range

from quant_system.domain import OHLCVBar


def test_shared_factory_returns_distinct_equal_objects() -> None:
    first = make_ohlcv_bar()
    second = make_ohlcv_bar()

    assert first == second
    assert first is not second


def test_shared_factory_allows_explicit_overrides() -> None:
    bar = make_next_bar()

    assert bar.start == make_time_range().end
    assert bar.close == 103.0


def test_pytest_fixture_reuses_shared_factory(
    ohlcv_bar_factory: Callable[[], OHLCVBar],
) -> None:
    assert ohlcv_bar_factory() == make_ohlcv_bar()
