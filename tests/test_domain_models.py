from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from math import inf, nan

import pytest

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_instrument_normalizes_compares_hashes_and_is_immutable() -> None:
    instrument = InstrumentId("  ethusdc  ")

    assert str(instrument) == "ETHUSDC"
    assert instrument == InstrumentId("ETHUSDC")
    assert hash(instrument) == hash(InstrumentId("ethusdc"))
    with pytest.raises(FrozenInstanceError):
        instrument.value = "BTCUSDT"  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    ["", "   ", "BTC-USDT", "BTC/USDT", "ＢＴＣＵＳＤＴ", "A" * 33],
)
def test_instrument_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        InstrumentId(value)


def test_instrument_accepts_conservative_maximum_length() -> None:
    assert str(InstrumentId("a" * InstrumentId.MAX_LENGTH)) == "A" * 32


def test_time_range_normalizes_aware_offsets_and_uses_half_open_semantics() -> None:
    interval = TimeRange(
        datetime(2024, 2, 29, 23, 0, tzinfo=timezone(timedelta(hours=-1))),
        datetime(2024, 3, 1, 1, 0, tzinfo=UTC),
    )

    assert interval.start == datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
    assert interval.duration == timedelta(hours=1)
    assert interval.contains(interval.start)
    assert not interval.contains(interval.end)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2024, 1, 1), datetime(2024, 1, 2, tzinfo=UTC)),
        (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2)),
        (
            datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=8))),
            datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=8))),
        ),
        (
            datetime(2024, 3, 1, tzinfo=UTC),
            datetime(2024, 2, 29, tzinfo=UTC),
        ),
    ],
)
def test_time_range_rejects_naive_or_nonincreasing_bounds(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        TimeRange(start, end)


@pytest.mark.parametrize("value", [nan, inf, -inf])
@pytest.mark.parametrize("field_name", ["open", "high", "low", "close", "volume"])
def test_bar_rejects_nonfinite_values(
    ohlcv_bar_factory: Callable[..., OHLCVBar], field_name: str, value: float
) -> None:
    with pytest.raises(ValueError, match=f"^{field_name} must be finite$"):
        ohlcv_bar_factory(**{field_name: value})


def test_bar_rejects_negative_volume(
    ohlcv_bar_factory: Callable[..., OHLCVBar],
) -> None:
    with pytest.raises(ValueError, match="volume"):
        ohlcv_bar_factory(volume=-0.1)


@pytest.mark.parametrize(
    "values",
    [
        {"open": 10.0, "high": 9.0, "low": 8.0, "close": 9.0},
        {"open": 8.0, "high": 10.0, "low": 9.0, "close": 9.0},
    ],
)
def test_bar_rejects_invalid_ohlc_relationships(
    ohlcv_bar_factory: Callable[..., OHLCVBar], values: dict[str, float]
) -> None:
    with pytest.raises(ValueError):
        ohlcv_bar_factory(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
        {"open": -5.0, "high": -1.0, "low": -10.0, "close": -3.0},
    ],
)
def test_bar_allows_zero_and_negative_prices(
    ohlcv_bar_factory: Callable[..., OHLCVBar], values: dict[str, float]
) -> None:
    bar = ohlcv_bar_factory(**values)

    assert bar.open == values["open"]
    assert bar.close == values["close"]


def test_factory_calls_return_isolated_immutable_models(
    ohlcv_bar_factory: Callable[..., OHLCVBar],
) -> None:
    first = ohlcv_bar_factory(instrument=InstrumentId("BTCUSDT"), volume=1.0)
    second = ohlcv_bar_factory(instrument=InstrumentId("ETHUSDT"), volume=2.0)

    assert first is not second
    assert first.instrument == InstrumentId("BTCUSDT")
    assert second.instrument == InstrumentId("ETHUSDT")
    assert first == OHLCVBar.from_dict(first.to_dict())
    with pytest.raises(FrozenInstanceError):
        first.volume = 3.0  # type: ignore[misc]


def test_time_range_is_immutable_and_comparable() -> None:
    interval = TimeRange(
        datetime(2024, 2, 29, 23, 45, tzinfo=UTC),
        datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
    )

    assert interval == TimeRange.from_dict(interval.to_dict())
    with pytest.raises(FrozenInstanceError):
        interval.end = datetime(2024, 3, 1, 0, 15, tzinfo=UTC)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("model", "value"),
    [
        (TimeRange, {"start": "2024-01-01T00:00:00Z"}),
        (
            TimeRange,
            {
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-02T00:00:00Z",
                "extra": "rejected",
            },
        ),
        (
            TimeRange,
            {"start": "2024-01-01T00:00:00", "end": "2024-01-02T00:00:00Z"},
        ),
        (
            OHLCVBar,
            {
                "instrument": "BTCUSDT",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-02T00:00:00Z",
                "open": 1,
                "high": 2.0,
                "low": 0.0,
                "close": 1.0,
                "volume": 1.0,
            },
        ),
        (
            OHLCVBar,
            {
                "instrument": "BTCUSDT",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-02T00:00:00Z",
                "open": 1.0,
                "high": 2.0,
                "low": 0.0,
                "close": 1.0,
            },
        ),
        (
            OHLCVBar,
            {
                "instrument": "BTCUSDT",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-02T00:00:00Z",
                "open": 1.0,
                "high": 2.0,
                "low": 0.0,
                "close": 1.0,
                "volume": 1.0,
                "extra": "rejected",
            },
        ),
    ],
)
def test_deserialization_rejects_malformed_values(
    model: type[TimeRange] | type[OHLCVBar], value: dict[str, object]
) -> None:
    with pytest.raises((TypeError, ValueError)):
        model.from_dict(value)


@pytest.mark.parametrize("value", [1, None, {"instrument": "BTCUSDT"}])
def test_instrument_deserialization_rejects_non_string_values(value: object) -> None:
    with pytest.raises(TypeError):
        InstrumentId.from_json_value(value)
