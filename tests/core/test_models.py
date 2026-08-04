from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from math import inf, nan

import pytest

from quant_system.core import InstrumentId, OHLCVBar, TimeRange
from tests.fixtures.domain import make_invalid_bar_payload, make_ohlcv_bar


def test_public_models_are_available_and_immutable(
    valid_instrument: InstrumentId,
    valid_time_range: TimeRange,
    valid_ohlcv_bar: OHLCVBar,
) -> None:
    assert str(valid_instrument) == "BTCUSDT"
    assert valid_time_range.duration == timedelta(minutes=5)
    assert valid_ohlcv_bar.instrument == valid_instrument
    with pytest.raises(FrozenInstanceError):
        valid_ohlcv_bar.volume = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(" btcusdt ", "BTCUSDT"), ("ETH123", "ETH123")],
)
def test_instrument_id_normalizes_trimmed_ascii_values(
    value: str, expected: str
) -> None:
    result = InstrumentId(value)
    assert result.value == expected
    assert str(result) == expected
    assert result == InstrumentId(expected)
    assert hash(result) == hash(InstrumentId(expected))


@pytest.mark.parametrize(
    "value",
    ["", "   ", "BTC-USDT", "BTC_USDT", "比特币", "A" * 33],
)
def test_instrument_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        InstrumentId(value)


def test_time_range_normalizes_aware_offsets_and_is_half_open() -> None:
    start = datetime(2026, 7, 19, 15, 1, 20, tzinfo=timezone(timedelta(hours=8)))
    end = datetime(2026, 7, 19, 15, 6, 20, tzinfo=timezone(timedelta(hours=8)))
    result = TimeRange(start, end)

    assert result.start == datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC)
    assert result.contains(result.start)
    assert not result.contains(result.end)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 7, 19, 7, 1, 20), datetime(2026, 7, 19, 7, 6, 20)),
        (
            datetime(2026, 7, 19, 7, 6, 20, tzinfo=UTC),
            datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC),
            datetime(2026, 7, 19, 7, 1, 20, tzinfo=UTC),
        ),
    ],
)
def test_time_range_rejects_naive_or_non_positive_intervals(
    start: datetime, end: datetime
) -> None:
    with pytest.raises(ValueError):
        TimeRange(start, end)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", nan),
        ("high", inf),
        ("low", -inf),
        ("volume", nan),
        ("volume", -1.0),
    ],
)
def test_ohlcv_bar_rejects_non_finite_or_negative_volume(
    field: str, value: float
) -> None:
    with pytest.raises(ValueError):
        if field == "open":
            make_ohlcv_bar(open=value)
        elif field == "high":
            make_ohlcv_bar(high=value)
        elif field == "low":
            make_ohlcv_bar(low=value)
        else:
            make_ohlcv_bar(volume=value)


@pytest.mark.parametrize(
    ("high", "low"),
    [
        (99.0, 95.0),
        (105.0, 101.0),
    ],
)
def test_ohlcv_bar_rejects_invalid_ohlc_relationships(high: float, low: float) -> None:
    with pytest.raises(ValueError):
        make_ohlcv_bar(high=high, low=low)


def test_zero_and_negative_prices_are_allowed_by_current_domain_boundary() -> None:
    bar = make_ohlcv_bar(open=-1.0, high=0.0, low=-2.0, close=-1.5)
    assert bar.open == -1.0
    assert bar.high == 0.0


def test_factory_calls_return_isolated_objects() -> None:
    first = make_ohlcv_bar()
    second = make_ohlcv_bar()
    assert first == second
    assert first is not second
    assert first.to_dict() is not second.to_dict()


def test_invalid_payload_factory_allows_explicit_field_override() -> None:
    payload = make_invalid_bar_payload(volume=-1.0)
    assert payload["volume"] == -1.0
