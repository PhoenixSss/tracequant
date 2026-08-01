import json
import math
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fixtures.domain import (
    DEFAULT_END,
    DEFAULT_INSTRUMENT_VALUE,
    DEFAULT_START,
    LEAP_DAY_START,
    instrument_id,
    invalid_ohlcv_payload,
    next_bar_end,
    next_bar_start,
    ohlcv_bar,
    time_range,
)

from quant_system.domain import (
    DomainValidationError,
    InstrumentId,
    OHLCVBar,
    TimeRange,
)


def test_instrument_id_normalizes_compares_hashes_and_serializes() -> None:
    instrument = InstrumentId(" btcusdt ")

    assert instrument.value == DEFAULT_INSTRUMENT_VALUE
    assert str(instrument) == DEFAULT_INSTRUMENT_VALUE
    assert instrument == InstrumentId(DEFAULT_INSTRUMENT_VALUE)
    assert {instrument, InstrumentId(DEFAULT_INSTRUMENT_VALUE)} == {instrument}
    assert instrument.to_dict() == DEFAULT_INSTRUMENT_VALUE
    assert InstrumentId.from_dict(instrument.to_dict()) == instrument


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "BTC-USDT",
        "BTC_USDT",
        "BTC/USDT",
        "\uff22\uff34\uff23",
        "A" * 33,
    ],
)
def test_instrument_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(DomainValidationError):
        InstrumentId(value)


def test_instrument_id_rejects_non_string_payload() -> None:
    with pytest.raises(DomainValidationError):
        InstrumentId.from_dict(123)


def test_models_are_frozen(sample_ohlcv_bar: OHLCVBar) -> None:
    with pytest.raises(FrozenInstanceError):
        sample_ohlcv_bar.volume = 43.0  # type: ignore[misc]


def test_time_range_uses_utc_half_open_semantics(
    sample_time_range: TimeRange,
) -> None:
    assert sample_time_range.start == DEFAULT_START
    assert sample_time_range.end == DEFAULT_END
    assert sample_time_range.duration == 15 * 60
    assert sample_time_range.contains(DEFAULT_START)
    assert sample_time_range.contains(DEFAULT_END - timedelta(microseconds=1))
    assert not sample_time_range.contains(DEFAULT_END)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 1, 1), DEFAULT_END),
        (DEFAULT_START, datetime(2026, 1, 1)),
        (datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))), DEFAULT_END),
        (DEFAULT_START, datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-5)))),
        (DEFAULT_START, DEFAULT_START),
        (DEFAULT_END, DEFAULT_START),
    ],
)
def test_time_range_rejects_invalid_datetimes(start: datetime, end: datetime) -> None:
    with pytest.raises(DomainValidationError):
        TimeRange(start=start, end=end)


def test_time_range_supports_cross_month_and_leap_day_boundaries() -> None:
    cross_month = time_range()
    leap_day = time_range(
        start=LEAP_DAY_START,
        end=datetime(2028, 2, 29, 0, 15, tzinfo=UTC),
    )

    assert cross_month.start.month == 2
    assert cross_month.end.month == 3
    assert leap_day.start.day == 29
    assert leap_day.duration == 15 * 60


def test_time_range_serializes_stably_and_round_trips(
    sample_time_range: TimeRange,
) -> None:
    payload = sample_time_range.to_dict()

    assert payload == {
        "start": "2026-02-28T23:45:00Z",
        "end": "2026-03-01T00:00:00Z",
    }
    assert TimeRange.from_dict(payload) == sample_time_range
    json.dumps(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2026-02-28T23:45:00", "end": "2026-03-01T00:00:00Z"},
        {"start": "2026-02-28T23:45:00+08:00", "end": "2026-03-01T00:00:00Z"},
        {"start": "2026-02-28T23:45:00Z"},
        {
            "start": "2026-02-28T23:45:00Z",
            "end": "2026-03-01T00:00:00Z",
            "timezone": "UTC",
        },
    ],
)
def test_time_range_rejects_invalid_serialized_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DomainValidationError):
        TimeRange.from_dict(payload)


def test_ohlcv_bar_serializes_stably_round_trips_and_is_json_compatible(
    sample_ohlcv_bar: OHLCVBar,
) -> None:
    payload = sample_ohlcv_bar.to_dict()

    assert payload == {
        "instrument": "BTCUSDT",
        "start": "2026-02-28T23:45:00Z",
        "end": "2026-03-01T00:00:00Z",
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 101.5,
        "volume": 42.0,
    }
    assert OHLCVBar.from_dict(payload) == sample_ohlcv_bar
    json.dumps(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", math.nan),
        ("high", math.inf),
        ("low", -math.inf),
        ("close", math.nan),
        ("volume", math.inf),
        ("volume", -1.0),
    ],
)
def test_ohlcv_bar_rejects_non_finite_or_negative_volume(
    field: str, value: float
) -> None:
    with pytest.raises(DomainValidationError):
        if field == "open":
            ohlcv_bar(open=value)
        elif field == "high":
            ohlcv_bar(high=value)
        elif field == "low":
            ohlcv_bar(low=value)
        elif field == "close":
            ohlcv_bar(close=value)
        else:
            ohlcv_bar(volume=value)


@pytest.mark.parametrize(
    ("open_price", "high_price", "low_price", "close_price"),
    [
        (100.0, 99.0, 95.0, 101.5),
        (100.0, 100.0, 95.0, 101.0),
        (100.0, 105.0, 100.1, 101.5),
        (100.0, 105.0, 96.0, 95.0),
    ],
)
def test_ohlcv_bar_rejects_invalid_ohlc_relationships(
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
) -> None:
    with pytest.raises(DomainValidationError):
        ohlcv_bar(
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
        )


def test_ohlcv_bar_allows_zero_and_negative_prices() -> None:
    zero = ohlcv_bar(open=0.0, high=1.0, low=-1.0, close=0.0)
    negative = ohlcv_bar(open=-5.0, high=-1.0, low=-10.0, close=-4.0)

    assert zero.open == 0.0
    assert negative.close == -4.0


@pytest.mark.parametrize(
    "payload",
    [
        invalid_ohlcv_payload(),
        {**ohlcv_bar().to_dict(), "extra": "not public"},
        {key: value for key, value in ohlcv_bar().to_dict().items() if key != "close"},
        {**ohlcv_bar().to_dict(), "instrument": 123},
        {**ohlcv_bar().to_dict(), "open": "100.0"},
        {**ohlcv_bar().to_dict(), "start": "2026-02-28T23:45:00"},
    ],
)
def test_ohlcv_bar_rejects_invalid_serialized_payloads(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DomainValidationError):
        OHLCVBar.from_dict(payload)


def test_bar_time_range_matches_range_model() -> None:
    bar = ohlcv_bar(start=next_bar_start(), end=next_bar_end())

    assert bar.time_range == TimeRange(start=next_bar_start(), end=next_bar_end())


def test_factory_calls_do_not_share_mutable_payload_state() -> None:
    first = invalid_ohlcv_payload()
    second = invalid_ohlcv_payload()

    first["instrument"] = "ETHUSDT"

    assert second["instrument"] == DEFAULT_INSTRUMENT_VALUE


def test_public_import_path_exposes_minimal_domain_models() -> None:
    assert InstrumentId("ETHUSDT") == instrument_id("ethusdt")
    assert time_range().contains(DEFAULT_START)
