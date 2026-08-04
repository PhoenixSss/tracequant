import json
import math
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from fixtures.domain import make_ohlcv_bar, make_ohlcv_payload, make_time_range

from quant_system.domain import DomainValidationError, InstrumentId, OHLCVBar, TimeRange


def test_instrument_id_normalizes_strips_and_uppercases() -> None:
    instrument = InstrumentId(" btcusdt ")

    assert instrument.value == "BTCUSDT"
    assert str(instrument) == "BTCUSDT"
    assert instrument.to_dict() == "BTCUSDT"


def test_instrument_id_is_hashable_and_comparable() -> None:
    assert {InstrumentId("ethusdt"), InstrumentId("ETHUSDT")} == {
        InstrumentId("ETHUSDT")
    }


@pytest.mark.parametrize(
    "value", ["", "   ", "BTC-USDT", "BTC_USDT", "BTC/USDT", "测试", "A" * 33]
)
def test_instrument_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(DomainValidationError):
        InstrumentId(value)


def test_instrument_id_rejects_non_string_payload() -> None:
    with pytest.raises(DomainValidationError):
        InstrumentId.from_dict(123)


def test_time_range_is_half_open_and_reports_duration() -> None:
    start = datetime(2026, 2, 28, 23, 45, tzinfo=UTC)
    end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    value = TimeRange(start=start, end=end)

    assert value.duration == timedelta(minutes=15)
    assert start in value
    assert datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC) in value
    assert end not in value


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_time_range_rejects_empty_or_reversed_intervals(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(DomainValidationError):
        TimeRange(start=start, end=end)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_time_range_rejects_naive_or_non_utc_datetimes(value: datetime) -> None:
    with pytest.raises(DomainValidationError):
        TimeRange(start=value, end=datetime(2026, 1, 2, tzinfo=UTC))


def test_time_range_serializes_and_round_trips() -> None:
    value = make_time_range()

    payload = value.to_dict()

    assert payload == {
        "start": "2026-02-28T23:45:00Z",
        "end": "2026-03-01T00:00:00Z",
    }
    assert TimeRange.from_dict(payload) == value


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2026-01-01T00:00:00Z"},
        {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T00:15:00Z",
            "extra": "nope",
        },
        {"start": "2026-01-01T00:00:00", "end": "2026-01-01T00:15:00Z"},
    ],
)
def test_time_range_rejects_invalid_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        TimeRange.from_dict(payload)


def test_ohlcv_bar_serializes_to_json_compatible_stable_payload(
    ohlcv_bar_factory: Callable[[], OHLCVBar],
) -> None:
    value = ohlcv_bar_factory()

    payload = value.to_dict()

    assert payload == {
        "instrument": "BTCUSDT",
        "start": "2026-02-28T23:45:00Z",
        "end": "2026-03-01T00:00:00Z",
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 101.5,
        "volume": 12.25,
    }
    assert json.loads(json.dumps(payload)) == payload
    assert OHLCVBar.from_dict(payload) == value


def test_ohlcv_bar_is_immutable() -> None:
    value = make_ohlcv_bar()

    with pytest.raises(FrozenInstanceError):
        setattr(value, "close", 102.0)


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_ohlcv_bar_rejects_non_finite_numbers(field: str, value: float) -> None:
    with pytest.raises(DomainValidationError):
        OHLCVBar.from_dict(make_ohlcv_payload(**{field: value}))


def test_ohlcv_bar_rejects_negative_volume() -> None:
    with pytest.raises(DomainValidationError):
        make_ohlcv_bar(volume=-0.01)


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": 99.0},
        {"low": 102.0},
    ],
)
def test_ohlcv_bar_rejects_invalid_ohlc_relationships(
    overrides: dict[str, float],
) -> None:
    with pytest.raises(DomainValidationError):
        OHLCVBar.from_dict(make_ohlcv_payload(**overrides))


def test_ohlcv_bar_currently_allows_zero_and_negative_prices() -> None:
    value = make_ohlcv_bar(open=0.0, high=1.0, low=-2.0, close=-1.0)

    assert value.open == 0.0
    assert value.low == -2.0


@pytest.mark.parametrize(
    "payload",
    [
        make_ohlcv_payload(extra="nope"),
        {key: value for key, value in make_ohlcv_payload().items() if key != "close"},
        make_ohlcv_payload(instrument=123),
        make_ohlcv_payload(start="2026-02-28T23:45:00"),
        make_ohlcv_payload(open=True),
    ],
)
def test_ohlcv_bar_rejects_invalid_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        OHLCVBar.from_dict(payload)


def test_ohlcv_bar_rejects_non_instrument_constructor_value() -> None:
    with pytest.raises(DomainValidationError):
        OHLCVBar(
            instrument=cast(InstrumentId, "BTCUSDT"),
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1.0,
        )
