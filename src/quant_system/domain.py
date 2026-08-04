"""Initial immutable domain value objects for research data."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Self

from quant_system.core.time import ensure_aware, format_utc, is_utc, parse_utc

__all__ = [
    "DomainValidationError",
    "InstrumentId",
    "OHLCVBar",
    "TimeRange",
]


class DomainValidationError(ValueError):
    """Raised when a domain value object receives invalid input."""


_INSTRUMENT_PATTERN = re.compile(r"^[A-Z0-9]{1,32}$")


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """Canonical instrument identifier for the current single-market context."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise DomainValidationError("instrument value must be a string")
        normalized = self.value.strip().upper()
        if not normalized:
            raise DomainValidationError("instrument value must not be empty")
        if not _INSTRUMENT_PATTERN.fullmatch(normalized):
            raise DomainValidationError(
                "instrument value must contain 1-32 ASCII uppercase letters or digits"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, str):
            raise DomainValidationError("instrument payload must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Half-open UTC time interval: [start, end)."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _require_utc_datetime(self.start, "start")
        end = _require_utc_datetime(self.end, "end")
        if start >= end:
            raise DomainValidationError("time range start must be before end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def contains(self, value: datetime) -> bool:
        checked = _require_utc_datetime(value, "value")
        return self.start <= checked < self.end

    def __contains__(self, value: object) -> bool:
        if not isinstance(value, datetime):
            return False
        return self.contains(value)

    def to_dict(self) -> dict[str, str]:
        return {
            "start": format_utc(self.start),
            "end": format_utc(self.end),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        payload = _require_exact_mapping(value, {"start", "end"}, "time range")
        return cls(
            start=_require_utc_iso(payload["start"], "start"),
            end=_require_utc_iso(payload["end"], "end"),
        )


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """Basic immutable OHLCV bar for one instrument and UTC interval."""

    instrument: InstrumentId
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise DomainValidationError("instrument must be an InstrumentId")
        time_range = TimeRange(self.start, self.end)
        open_price = _require_finite_float(self.open, "open")
        high_price = _require_finite_float(self.high, "high")
        low_price = _require_finite_float(self.low, "low")
        close_price = _require_finite_float(self.close, "close")
        volume = _require_finite_float(self.volume, "volume")
        if volume < 0:
            raise DomainValidationError("volume must be non-negative")
        if high_price < max(open_price, low_price, close_price):
            raise DomainValidationError("high must be at least open, low, and close")
        if low_price > min(open_price, high_price, close_price):
            raise DomainValidationError("low must be at most open, high, and close")
        object.__setattr__(self, "start", time_range.start)
        object.__setattr__(self, "end", time_range.end)
        object.__setattr__(self, "open", open_price)
        object.__setattr__(self, "high", high_price)
        object.__setattr__(self, "low", low_price)
        object.__setattr__(self, "close", close_price)
        object.__setattr__(self, "volume", volume)

    @property
    def time_range(self) -> TimeRange:
        return TimeRange(self.start, self.end)

    def to_dict(self) -> dict[str, str | float]:
        return {
            "instrument": self.instrument.to_dict(),
            "start": format_utc(self.start),
            "end": format_utc(self.end),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        payload = _require_exact_mapping(
            value,
            {"instrument", "start", "end", "open", "high", "low", "close", "volume"},
            "OHLCV bar",
        )
        return cls(
            instrument=InstrumentId.from_dict(payload["instrument"]),
            start=_require_utc_iso(payload["start"], "start"),
            end=_require_utc_iso(payload["end"], "end"),
            open=_require_finite_float(payload["open"], "open"),
            high=_require_finite_float(payload["high"], "high"),
            low=_require_finite_float(payload["low"], "low"),
            close=_require_finite_float(payload["close"], "close"),
            volume=_require_finite_float(payload["volume"], "volume"),
        )


def _require_utc_datetime(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field} must be a datetime")
    try:
        checked = ensure_aware(value)
    except ValueError as exc:
        raise DomainValidationError(f"{field} must be timezone-aware") from exc
    if not is_utc(checked):
        raise DomainValidationError(f"{field} must be UTC")
    return checked


def _require_utc_iso(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field} must be an ISO 8601 string")
    try:
        return parse_utc(value)
    except ValueError as exc:
        raise DomainValidationError(
            f"{field} must be a valid aware ISO 8601 datetime"
        ) from exc


def _require_finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainValidationError(f"{field} must be a float")
    result = float(value)
    if not math.isfinite(result):
        raise DomainValidationError(f"{field} must be finite")
    return result


def _require_exact_mapping(
    value: object, expected_keys: set[str], payload_name: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DomainValidationError(f"{payload_name} payload must be a dict")
    actual_keys = set(value)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        details: list[str] = []
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        if extra:
            details.append(f"extra keys: {', '.join(extra)}")
        raise DomainValidationError(
            f"{payload_name} payload has invalid fields ({'; '.join(details)})"
        )
    return value
