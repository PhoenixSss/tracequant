"""Small public domain models for research data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Self

from quant_system.core.time import ensure_aware, format_utc, is_utc, parse_utc

__all__ = [
    "DomainValidationError",
    "InstrumentId",
    "OHLCVBar",
    "TimeRange",
]


class DomainValidationError(ValueError):
    """Raised when a public domain model receives invalid input."""


_MAX_INSTRUMENT_LENGTH = 32


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """Standardized instrument identifier for the current market-data context."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise DomainValidationError("instrument must be a string")
        normalized = self.value.strip().upper()
        if not normalized:
            raise DomainValidationError("instrument must not be empty")
        if len(normalized) > _MAX_INSTRUMENT_LENGTH:
            raise DomainValidationError(
                f"instrument length must be at most {_MAX_INSTRUMENT_LENGTH}"
            )
        if not normalized.isascii() or not normalized.isalnum():
            raise DomainValidationError(
                "instrument must contain only ASCII letters and digits"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Serialize the identifier to a JSON-compatible string."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Deserialize an identifier from its JSON-compatible form."""
        if not isinstance(value, str):
            raise DomainValidationError("instrument payload must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True)
class TimeRange:
    """UTC half-open time interval with ``[start, end)`` semantics."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _require_utc_datetime(self.start, "start")
        end = _require_utc_datetime(self.end, "end")
        if start >= end:
            raise DomainValidationError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> float:
        """Return interval duration in seconds."""
        return (self.end - self.start).total_seconds()

    def contains(self, value: datetime) -> bool:
        """Return whether a UTC datetime falls within ``[start, end)``."""
        checked = _require_utc_datetime(value, "value")
        return self.start <= checked < self.end

    def to_dict(self) -> dict[str, str]:
        """Serialize the interval to stable JSON-compatible fields."""
        return {
            "start": format_utc(self.start),
            "end": format_utc(self.end),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Deserialize an interval from stable JSON-compatible fields."""
        payload = _require_exact_keys(value, {"start", "end"}, "time range")
        return cls(
            start=_parse_utc_payload(payload["start"], "start"),
            end=_parse_utc_payload(payload["end"], "end"),
        )


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """Basic OHLCV bar for one instrument over one UTC half-open interval."""

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
        interval = TimeRange(self.start, self.end)
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
        object.__setattr__(self, "start", interval.start)
        object.__setattr__(self, "end", interval.end)
        object.__setattr__(self, "open", open_price)
        object.__setattr__(self, "high", high_price)
        object.__setattr__(self, "low", low_price)
        object.__setattr__(self, "close", close_price)
        object.__setattr__(self, "volume", volume)

    @property
    def time_range(self) -> TimeRange:
        """Return the bar interval as a ``TimeRange`` value object."""
        return TimeRange(self.start, self.end)

    def to_dict(self) -> dict[str, str | float]:
        """Serialize the bar to stable JSON-compatible fields."""
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
        """Deserialize a bar from stable JSON-compatible fields."""
        payload = _require_exact_keys(
            value,
            {"instrument", "start", "end", "open", "high", "low", "close", "volume"},
            "OHLCV bar",
        )
        return cls(
            instrument=InstrumentId.from_dict(payload["instrument"]),
            start=_parse_utc_payload(payload["start"], "start"),
            end=_parse_utc_payload(payload["end"], "end"),
            open=_require_float_payload(payload["open"], "open"),
            high=_require_float_payload(payload["high"], "high"),
            low=_require_float_payload(payload["low"], "low"),
            close=_require_float_payload(payload["close"], "close"),
            volume=_require_float_payload(payload["volume"], "volume"),
        )


def _require_utc_datetime(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} must be a datetime")
    try:
        aware = ensure_aware(value)
    except ValueError as exc:
        raise DomainValidationError(f"{field_name} must be timezone-aware") from exc
    if not is_utc(aware):
        raise DomainValidationError(f"{field_name} must be UTC")
    return aware


def _parse_utc_payload(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be a UTC ISO 8601 string")
    if not (value.endswith("Z") or value.endswith("+00:00")):
        raise DomainValidationError(f"{field_name} must include a UTC timezone")
    try:
        parsed = parse_utc(value)
    except ValueError as exc:
        raise DomainValidationError(
            f"{field_name} must be a valid UTC datetime"
        ) from exc
    return _require_utc_datetime(parsed, field_name)


def _require_finite_float(value: float, field_name: str) -> float:
    if not isinstance(value, float):
        raise DomainValidationError(f"{field_name} must be a float")
    if not isfinite(value):
        raise DomainValidationError(f"{field_name} must be finite")
    return value


def _require_float_payload(value: object, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise DomainValidationError(f"{field_name} must be numeric")
    return _require_finite_float(float(value), field_name)


def _require_exact_keys(
    value: object, expected_keys: set[str], payload_name: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DomainValidationError(f"{payload_name} payload must be an object")
    keys = set(value)
    missing = expected_keys - keys
    extra = keys - expected_keys
    if missing:
        raise DomainValidationError(
            f"{payload_name} payload missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise DomainValidationError(
            f"{payload_name} payload has extra fields: {', '.join(sorted(extra))}"
        )
    return value
