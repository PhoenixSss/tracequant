import importlib
from collections.abc import Callable

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_domain_models_have_one_minimal_public_import_path() -> None:
    domain = importlib.import_module("tracequant.domain")

    assert domain.InstrumentId is InstrumentId
    assert domain.TimeRange is TimeRange
    assert domain.OHLCVBar is OHLCVBar


def test_shared_factory_is_function_scoped(
    bar_factory: Callable[..., OHLCVBar],
) -> None:
    assert bar_factory() is not bar_factory()
