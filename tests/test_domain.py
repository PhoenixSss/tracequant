"""Unit and boundary tests for the initial public domain models."""

import json
import math
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fixtures.domain import make_instrument, make_ohlcv_bar, make_time_range

from tracequant.domain import DomainValidationError, InstrumentId, OHLCVBar, TimeRange


@pytest.mark.parametrize("value", ["", "   ", "BTC-USDT", "BTC_USDT", "比特币"])
def test_instrument_rejects_empty_non_ascii_and_separators(value: str) -> None:
    with pytest.raises(DomainValidationError):
        InstrumentId(value)


def test_instrument_normalizes_and_is_immutable_hashable() -> None:
    value = InstrumentId("  ethusdt ")

    assert value.value == "ETHUSDT"
    assert str(value) == "ETHUSDT"
    assert value == InstrumentId("ETHUSDT")
    assert hash(value) == hash(InstrumentId("ETHUSDT"))
    assert {value} == {InstrumentId("ETHUSDT")}
    with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
        value.value = "BTCUSDT"  # type: ignore[misc]


def test_instrument_rejects_long_values() -> None:
    with pytest.raises(DomainValidationError):
        InstrumentId("A" * (InstrumentId.MAX_LENGTH + 1))


def test_time_range_normalizes_offsets_and_uses_half_open_semantics() -> None:
    start = datetime(2024, 2, 29, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    end = datetime(2024, 2, 29, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    value = TimeRange(start=start, end=end)

    assert value.start == datetime(2024, 2, 29, 0, 0, tzinfo=UTC)
    assert value.end == datetime(2024, 2, 29, 1, 0, tzinfo=UTC)
    assert value.duration == timedelta(hours=1)
    assert value.start in value
    assert datetime(2024, 2, 29, 1, 0, tzinfo=UTC) not in value


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2024, 2, 29, 0, 0), datetime(2024, 2, 29, 1, 0, tzinfo=UTC)),
        (
            datetime(2024, 2, 29, 1, 0, tzinfo=UTC),
            datetime(2024, 2, 29, 0, 0, tzinfo=UTC),
        ),
        (
            datetime(2024, 2, 29, 1, 0, tzinfo=UTC),
            datetime(2024, 2, 29, 1, 0, tzinfo=UTC),
        ),
    ],
)
def test_time_range_rejects_naive_or_non_positive_intervals(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        TimeRange(start=start, end=end)


def test_time_range_supports_cross_month_and_leap_day() -> None:
    value = make_time_range(
        start=datetime(2024, 2, 29, 23, 59, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 1, tzinfo=UTC),
    )

    assert value.duration == timedelta(minutes=2)


@pytest.mark.parametrize(
    ("open", "high", "low", "close", "volume"),
    [
        (math.nan, 50_025.0, 49_975.0, 50_010.0, 12.5),
        (50_000.0, math.inf, 49_975.0, 50_010.0, 12.5),
        (50_000.0, 50_025.0, -math.inf, 50_010.0, 12.5),
        (50_000.0, 50_025.0, 49_975.0, math.nan, 12.5),
        (50_000.0, 50_025.0, 49_975.0, 50_010.0, math.inf),
        (50_000.0, 50_025.0, 49_975.0, 50_010.0, -1.0),
    ],
)
def test_ohlcv_rejects_non_finite_prices_and_negative_volume(
    open: float, high: float, low: float, close: float, volume: float
) -> None:
    with pytest.raises(DomainValidationError):
        make_ohlcv_bar(
            open=open,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )


@pytest.mark.parametrize(
    ("open", "high", "low", "close"),
    [
        (50_000.0, 49_999.0, 49_975.0, 50_010.0),
        (50_000.0, 50_025.0, 50_001.0, 50_010.0),
        (50_000.0, 50_050.0, 49_975.0, 50_100.0),
    ],
)
def test_ohlcv_rejects_invalid_high_low_relationships(
    open: float, high: float, low: float, close: float
) -> None:
    with pytest.raises(DomainValidationError):
        make_ohlcv_bar(open=open, high=high, low=low, close=close)


def test_ohlcv_allows_zero_and_negative_prices() -> None:
    value = make_ohlcv_bar(open=-2.0, high=-1.0, low=-3.0, close=-2.5)
    zero_value = make_ohlcv_bar(open=0.0, high=0.0, low=0.0, close=0.0)

    assert value.open == -2.0
    assert zero_value.close == 0.0


def test_ohlcv_is_immutable_and_has_fixed_public_fields() -> None:
    value = make_ohlcv_bar()

    assert tuple(value.__dataclass_fields__) == (
        "instrument",
        "start",
        "end",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
        value.volume = 1.0  # type: ignore[misc]


def test_serialization_is_stable_json_compatible_and_round_trips() -> None:
    value = make_ohlcv_bar()
    encoded = value.to_dict()

    assert tuple(encoded) == (
        "instrument",
        "start",
        "end",
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    assert json.loads(json.dumps(encoded)) == encoded
    assert OHLCVBar.from_dict(encoded) == value
    assert TimeRange.from_dict(
        {"start": encoded["start"], "end": encoded["end"]}
    ) == TimeRange(start=value.start, end=value.end)


@pytest.mark.parametrize(
    "malformed",
    [
        {},
        {"instrument": "BTCUSDT"},
        {
            "instrument": "BTCUSDT",
            "start": "2024-02-28T23:45:00Z",
            "end": "2024-02-29T00:00:00Z",
            "open": 50_000.0,
            "high": 50_025.0,
            "low": 49_975.0,
            "close": 50_010.0,
            "volume": 12.5,
            "extra": "not allowed",
        },
        {
            "instrument": "BTCUSDT",
            "start": "2024-02-28T23:45:00",
            "end": "2024-02-29T00:00:00Z",
            "open": 50_000.0,
            "high": 50_025.0,
            "low": 49_975.0,
            "close": 50_010.0,
            "volume": 12.5,
        },
        {
            "instrument": "BTCUSDT",
            "start": "2024-02-28T23:45:00Z",
            "end": "2024-02-29T00:00:00Z",
            "open": "50000",
            "high": 50_025.0,
            "low": 49_975.0,
            "close": 50_010.0,
            "volume": 12.5,
        },
    ],
)
def test_ohlcv_from_dict_rejects_malformed_data(
    malformed: object,
) -> None:
    with pytest.raises(ValueError):
        OHLCVBar.from_dict(malformed)


def test_factories_return_isolated_objects_and_allow_overrides(
    sample_instrument: InstrumentId,
    sample_time_range: TimeRange,
    sample_ohlcv_bar: OHLCVBar,
) -> None:
    other_instrument = make_instrument("ETHUSDT")
    other_range = make_time_range(
        start=datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 15, tzinfo=UTC),
    )
    other_bar = make_ohlcv_bar(
        instrument=other_instrument,
        start=other_range.start,
        end=other_range.end,
    )

    assert sample_instrument != other_instrument
    assert sample_time_range != other_range
    assert sample_ohlcv_bar.instrument == InstrumentId("BTCUSDT")
    assert other_bar.instrument == other_instrument
    assert other_bar.start == other_range.start
