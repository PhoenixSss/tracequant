import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from math import inf, nan

import pytest
from fixtures.domain import BarFactory, InstrumentFactory, TimeRangeFactory

from tracequant.domain import InstrumentId, OHLCVBar, TimeRange


def test_instrument_normalizes_and_has_value_semantics(
    instrument_factory: InstrumentFactory,
) -> None:
    first = instrument_factory("  ethusdc  ")
    second = instrument_factory("ETHUSDC")

    assert first == second
    assert hash(first) == hash(second)
    assert str(first) == "ETHUSDC"
    with pytest.raises(FrozenInstanceError):
        first.value = "BTCUSDT"  # type: ignore[misc]


@pytest.mark.parametrize(
    "invalid_value",
    ["", "   ", "BTC-USDT", "BTC/USDT", "ＢＴＣＵＳＤＴ", "A" * 33],
)
def test_instrument_rejects_invalid_values(invalid_value: str) -> None:
    with pytest.raises(ValueError):
        InstrumentId(invalid_value)


@pytest.mark.parametrize("invalid_value", ["ß", "ſ"])
def test_instrument_rejects_non_ascii_before_uppercase(invalid_value: str) -> None:
    with pytest.raises(ValueError, match="ASCII"):
        InstrumentId(invalid_value)


def test_time_range_normalizes_non_utc_aware_values(
    time_range_factory: TimeRangeFactory,
) -> None:
    interval = time_range_factory(
        start=datetime(2024, 3, 1, 7, 45, tzinfo=timezone(timedelta(hours=8))),
        end=datetime(2024, 3, 1, 8, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert interval.start == datetime(2024, 2, 29, 23, 45, tzinfo=UTC)
    assert interval.end == datetime(2024, 3, 1, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2024, 1, 1), datetime(2024, 1, 2, tzinfo=UTC)),
        (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2)),
        (
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2024, 1, 2, tzinfo=UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
        ),
    ],
)
def test_time_range_rejects_naive_or_non_increasing_bounds(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        TimeRange(start=start, end=end)


def test_time_range_supports_cross_day_month_and_leap_day() -> None:
    interval = TimeRange(
        start=datetime(2024, 2, 29, 23, 59, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 1, tzinfo=UTC),
    )

    assert interval.to_dict() == {
        "start": "2024-02-29T23:59:00Z",
        "end": "2024-03-01T00:01:00Z",
    }


def test_time_range_is_immutable_and_has_value_semantics(
    time_range_factory: TimeRangeFactory,
) -> None:
    first = time_range_factory()
    second = time_range_factory()

    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.end = first.start  # type: ignore[misc]


@pytest.mark.parametrize("invalid_value", [nan, inf, -inf])
@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_bar_rejects_non_finite_numbers(
    bar_factory: BarFactory, field: str, invalid_value: float
) -> None:
    values = {field: invalid_value}
    with pytest.raises(ValueError, match=f"{field} must be finite"):
        bar_factory(**values)  # type: ignore[arg-type]


def test_bar_rejects_negative_volume(bar_factory: BarFactory) -> None:
    with pytest.raises(ValueError, match="volume"):
        bar_factory(volume=-0.1)


def test_bar_reuses_time_range_validation_and_is_immutable(
    bar_factory: BarFactory,
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        bar_factory(start=datetime(2024, 1, 1))

    bar = bar_factory()
    with pytest.raises(FrozenInstanceError):
        bar.close = 101.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "values",
    [
        {"open": 101.0, "high": 100.0},
        {"close": 101.0, "high": 100.0},
        {"low": 101.0, "high": 100.0},
        {"open": 89.0, "low": 90.0},
        {"close": 89.0, "low": 90.0},
    ],
)
def test_bar_rejects_invalid_ohlc_relationships(
    bar_factory: BarFactory, values: dict[str, float]
) -> None:
    with pytest.raises(ValueError):
        bar_factory(**values)  # type: ignore[arg-type]


def test_bar_currently_allows_zero_and_negative_prices(
    bar_factory: BarFactory,
) -> None:
    zero = bar_factory(open=0.0, high=0.0, low=0.0, close=0.0)
    negative = bar_factory(open=-2.0, high=-1.0, low=-3.0, close=-2.5)

    assert zero.open == 0.0
    assert negative.low == -3.0


def test_serialization_round_trip_and_factory_calls_are_isolated(
    bar_factory: BarFactory,
) -> None:
    first = bar_factory()
    second = bar_factory()

    assert first == second
    assert first is not second
    payload = first.to_dict()
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
    assert OHLCVBar.from_dict(payload) == first


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2024-01-01T00:00:00Z"},
        {
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-02T00:00:00Z",
            "extra": "unexpected",
        },
        {"start": 1, "end": "2024-01-02T00:00:00Z"},
        {"start": "2024-01-01T00:00:00", "end": "2024-01-02T00:00:00Z"},
    ],
)
def test_time_range_rejects_malformed_deserialization(payload: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        TimeRange.from_dict(payload)


@pytest.mark.parametrize("payload", [1, None, {"value": "BTCUSDT"}])
def test_instrument_rejects_malformed_deserialization(payload: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        InstrumentId.from_dict(payload)


def test_bar_rejects_missing_extra_and_wrong_serialized_fields(
    bar_factory: BarFactory,
) -> None:
    valid = bar_factory().to_dict()
    missing = dict(valid)
    del missing["volume"]
    extra = {**valid, "trades": 1.0}
    wrong_type = {**valid, "volume": 1}

    for payload in (missing, extra, wrong_type):
        with pytest.raises((TypeError, ValueError)):
            OHLCVBar.from_dict(payload)
