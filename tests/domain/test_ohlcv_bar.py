"""OHLCV bar validation and immutable value behavior."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fixtures.domain import invalid_numeric_overrides, make_ohlcv_bar

from tracequant.domain import OHLCVBar


def test_bar_exposes_expected_values(ohlcv_bar: OHLCVBar) -> None:
    assert str(ohlcv_bar.instrument) == "BTCUSDT"
    assert ohlcv_bar.open == 100.0
    assert ohlcv_bar.high == 105.0
    assert ohlcv_bar.low == 95.0
    assert ohlcv_bar.close == 102.0
    assert ohlcv_bar.volume == 12.5


@pytest.mark.parametrize("overrides", invalid_numeric_overrides())
def test_bar_rejects_invalid_numeric_values(overrides: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        make_ohlcv_bar(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_bar_rejects_each_non_finite_field(value: float) -> None:
    for field in ("open", "high", "low", "close", "volume"):
        with pytest.raises(ValueError, match=f"{field} must be finite"):
            make_ohlcv_bar(**{field: value})  # type: ignore[arg-type]


def test_bar_requires_python_float_values() -> None:
    with pytest.raises(TypeError, match="open must be a float"):
        make_ohlcv_bar(open=100)


@pytest.mark.parametrize(
    ("open", "high", "low", "close"),
    [(0.0, 1.0, -1.0, 0.5), (-5.0, -1.0, -10.0, -7.0)],
)
def test_bar_allows_zero_and_negative_prices(
    open: float, high: float, low: float, close: float
) -> None:
    bar = make_ohlcv_bar(open=open, high=high, low=low, close=close)

    assert bar.open == open
    assert bar.close == close


def test_bar_rejects_naive_or_non_utc_interval() -> None:
    with pytest.raises(ValueError):
        make_ohlcv_bar(start=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="start must be UTC"):
        make_ohlcv_bar(
            start=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
            end=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_bar_is_immutable_and_hashable(ohlcv_bar: OHLCVBar) -> None:
    with pytest.raises(FrozenInstanceError):
        ohlcv_bar.close = 101.0  # type: ignore[misc]

    assert {ohlcv_bar, make_ohlcv_bar()} == {ohlcv_bar}


def test_invalid_samples_are_isolated() -> None:
    first = invalid_numeric_overrides()
    second = invalid_numeric_overrides()

    assert first is not second
    assert all(left is not right for left, right in zip(first, second, strict=True))
