import json
import math
import os
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quant_system.domain import DomainValidationError, InstrumentId, OHLCVBar
from tests.fixtures.domain import (
    VALID_RANGE_END,
    VALID_RANGE_START,
    make_instrument,
    make_ohlcv_bar,
)


def test_ohlcv_bar_uses_instrument_and_utc_interval(
    ohlcv_bar_factory: object,
) -> None:
    assert callable(ohlcv_bar_factory)
    bar = make_ohlcv_bar()

    assert bar.instrument == InstrumentId("BTCUSDT")
    assert bar.start == VALID_RANGE_START
    assert bar.end == VALID_RANGE_END
    assert bar.time_range.start == VALID_RANGE_START
    assert bar.time_range.end == VALID_RANGE_END


def test_ohlcv_bar_is_immutable_and_comparable() -> None:
    bar = make_ohlcv_bar()

    with pytest.raises(FrozenInstanceError):
        bar.close = 102.0  # type: ignore[misc]

    assert bar == make_ohlcv_bar()


@pytest.mark.parametrize("field_name", ["open", "high", "low", "close", "volume"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_ohlcv_bar_rejects_non_finite_numbers(field_name: str, value: float) -> None:
    with pytest.raises(DomainValidationError, match=f"{field_name} must be finite"):
        if field_name == "open":
            make_ohlcv_bar(open=value)
        elif field_name == "high":
            make_ohlcv_bar(high=value)
        elif field_name == "low":
            make_ohlcv_bar(low=value)
        elif field_name == "close":
            make_ohlcv_bar(close=value)
        else:
            make_ohlcv_bar(volume=value)


def test_ohlcv_bar_rejects_negative_volume() -> None:
    with pytest.raises(DomainValidationError, match="volume must be non-negative"):
        make_ohlcv_bar(volume=-0.01)


def test_ohlcv_bar_rejects_high_below_close() -> None:
    with pytest.raises(DomainValidationError):
        make_ohlcv_bar(high=100.0, close=101.0)


def test_ohlcv_bar_rejects_low_above_close() -> None:
    with pytest.raises(DomainValidationError):
        make_ohlcv_bar(low=101.0, close=100.0)


def test_ohlcv_bar_allows_zero_and_negative_prices_for_research_data() -> None:
    bar = make_ohlcv_bar(open=0.0, high=1.0, low=-2.0, close=-1.0)

    assert bar.open == 0.0
    assert bar.low == -2.0
    assert bar.close == -1.0


def test_ohlcv_bar_rejects_non_instrument_id() -> None:
    with pytest.raises(DomainValidationError):
        OHLCVBar(
            instrument="BTCUSDT",  # type: ignore[arg-type]
            start=VALID_RANGE_START,
            end=VALID_RANGE_END,
            open=100.0,
            high=105.0,
            low=95.0,
            close=101.5,
            volume=12.25,
        )


def test_ohlcv_bar_rejects_naive_start() -> None:
    with pytest.raises(DomainValidationError):
        make_ohlcv_bar(start=datetime(2026, 2, 28, 23, 45))


def test_ohlcv_bar_rejects_non_utc_end() -> None:
    with pytest.raises(DomainValidationError):
        make_ohlcv_bar(
            end=datetime(2026, 3, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        )


def test_ohlcv_bar_rejects_integer_price() -> None:
    with pytest.raises(DomainValidationError):
        OHLCVBar(
            instrument=make_instrument(),
            start=VALID_RANGE_START,
            end=VALID_RANGE_END,
            open=100,
            high=105.0,
            low=95.0,
            close=101.5,
            volume=12.25,
        )


def test_ohlcv_bar_serializes_to_json_compatible_stable_dict() -> None:
    bar = make_ohlcv_bar(instrument=make_instrument("ethusdt"))

    payload = bar.to_dict()

    assert payload == {
        "instrument": "ETHUSDT",
        "start": "2026-02-28T23:45:00Z",
        "end": "2026-03-01T00:00:00Z",
        "open": 100.0,
        "high": 105.0,
        "low": 95.0,
        "close": 101.5,
        "volume": 12.25,
    }
    assert json.loads(json.dumps(payload)) == payload
    assert OHLCVBar.from_dict(payload) == bar


@pytest.mark.parametrize(
    "payload",
    [
        {
            "instrument": "BTCUSDT",
            "start": "2026-02-28T23:45:00Z",
            "end": "2026-03-01T00:00:00Z",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 101.5,
        },
        {
            "instrument": "BTCUSDT",
            "start": "2026-02-28T23:45:00Z",
            "end": "2026-03-01T00:00:00Z",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 101.5,
            "volume": 12.25,
            "timeframe": "15m",
        },
        {
            "instrument": "BTC-USDT",
            "start": "2026-02-28T23:45:00Z",
            "end": "2026-03-01T00:00:00Z",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 101.5,
            "volume": 12.25,
        },
        {
            "instrument": "BTCUSDT",
            "start": "2026-02-28T23:45:00",
            "end": "2026-03-01T00:00:00Z",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 101.5,
            "volume": 12.25,
        },
        {
            "instrument": "BTCUSDT",
            "start": "2026-03-01T07:45:00+08:00",
            "end": "2026-03-01T00:00:00Z",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 101.5,
            "volume": 12.25,
        },
        {
            "instrument": "BTCUSDT",
            "start": "2026-02-28T23:45:00Z",
            "end": "2026-03-01T00:00:00Z",
            "open": 100,
            "high": 105.0,
            "low": 95.0,
            "close": 101.5,
            "volume": 12.25,
        },
    ],
)
def test_ohlcv_bar_from_dict_rejects_invalid_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(DomainValidationError):
        OHLCVBar.from_dict(payload)


def test_shared_factory_returns_isolated_objects() -> None:
    first = make_ohlcv_bar()
    second = make_ohlcv_bar()

    assert first == second
    assert first is not second
    assert first.instrument is not second.instrument
    assert first.to_dict() is not second.to_dict()


def test_domain_module_import_has_no_io_or_configuration_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("QUANT_SYSTEM_ENV", "production")
    monkeypatch.chdir(tmp_path)

    import quant_system.domain as module

    assert str(module.InstrumentId("BTCUSDT")) == str(InstrumentId("BTCUSDT"))
    assert "QUANT_SYSTEM_ENV" in os.environ
    assert list(tmp_path.iterdir()) == []
