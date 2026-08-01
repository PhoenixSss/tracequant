"""Initial shared domain value objects for research market data."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from quant_system.core.time import ensure_aware, format_utc, is_utc, to_utc

__all__ = ["DomainValidationError", "InstrumentId", "OHLCVBar", "TimeRange"]


class DomainValidationError(ValueError):
    """Raised when a domain value object receives invalid data."""


_INSTRUMENT_PATTERN: Final = re.compile(r"^[A-Z0-9]+$")
_MAX_INSTRUMENT_LENGTH: Final = 32


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    """Standardized instrument identifier for the current market-data context."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise DomainValidationError("instrument must be a string")
        normalized = self.value.strip().upper()
        if normalized == "":
            raise DomainValidationError("instrument must not be empty")
        if len(normalized) > _MAX_INSTRUMENT_LENGTH:
            raise DomainValidationError(
                f"instrument must be at most {_MAX_INSTRUMENT_LENGTH} characters"
            )
        if _INSTRUMENT_PATTERN.fullmatch(normalized) is None:
            raise DomainValidationError(
                "instrument must contain only ASCII uppercase letters and digits"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the stable JSON-compatible representation."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> InstrumentId:
        """Create an instrument identifier from its serialized string form."""
        if not isinstance(value, str):
            raise DomainValidationError("instrument payload must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True)
class TimeRange:
    """UTC half-open time range using [start, end) semantics."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _require_utc_datetime(self.start, field_name="start")
        end = _require_utc_datetime(self.end, field_name="end")
        if start >= end:
            raise DomainValidationError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        """Return the range duration."""
        return self.end - self.start

    def contains(self, value: datetime) -> bool:
        """Return whether an aware UTC datetime lies in [start, end)."""
        checked = _require_utc_datetime(value, field_name="value")
        return self.start <= checked < self.end

    def to_dict(self) -> dict[str, str]:
        """Return the stable JSON-compatible representation."""
        return {
            "start": format_utc(self.start),
            "end": format_utc(self.end),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> TimeRange:
        """Create a time range from its serialized mapping form."""
        payload = _require_mapping_keys(value, required=("start", "end"))
        return cls(
            start=_parse_utc_iso(payload["start"], field_name="start"),
            end=_parse_utc_iso(payload["end"], field_name="end"),
        )


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """Basic OHLCV bar for one instrument and one UTC half-open interval."""

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
        open_price = _require_finite_float(self.open, field_name="open")
        high_price = _require_finite_float(self.high, field_name="high")
        low_price = _require_finite_float(self.low, field_name="low")
        close_price = _require_finite_float(self.close, field_name="close")
        volume = _require_finite_float(self.volume, field_name="volume")
        if volume < 0.0:
            raise DomainValidationError("volume must be non-negative")
        if high_price < max(open_price, low_price, close_price):
            raise DomainValidationError("high must be at least open, low, and close")
        if low_price > min(open_price, high_price, close_price):
            raise DomainValidationError("low must be at most open, high, and close")
        object.__setattr__(self, "start", time_range.start)
        object.__setattr__(self, "end", time_range.end)

    @property
    def time_range(self) -> TimeRange:
        """Return the bar interval as a TimeRange."""
        return TimeRange(self.start, self.end)

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON-compatible representation."""
        _validate_serializable_float_fields(self)
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
    def from_dict(cls, value: Mapping[str, object]) -> OHLCVBar:
        """Create a bar from its serialized mapping form."""
        payload = _require_mapping_keys(
            value,
            required=(
                "instrument",
                "start",
                "end",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ),
        )
        return cls(
            instrument=InstrumentId.from_dict(payload["instrument"]),
            start=_parse_utc_iso(payload["start"], field_name="start"),
            end=_parse_utc_iso(payload["end"], field_name="end"),
            open=_require_finite_float(payload["open"], field_name="open"),
            high=_require_finite_float(payload["high"], field_name="high"),
            low=_require_finite_float(payload["low"], field_name="low"),
            close=_require_finite_float(payload["close"], field_name="close"),
            volume=_require_finite_float(payload["volume"], field_name="volume"),
        )


def _require_mapping_keys(
    value: Mapping[str, object], *, required: tuple[str, ...]
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DomainValidationError("payload must be a mapping")
    actual_keys = set(value)
    required_keys = set(required)
    missing = sorted(required_keys - actual_keys)
    extra = sorted(actual_keys - required_keys)
    if missing:
        raise DomainValidationError(f"payload missing fields: {', '.join(missing)}")
    if extra:
        raise DomainValidationError(f"payload has extra fields: {', '.join(extra)}")
    return value


def _require_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    try:
        aware = ensure_aware(value)
    except ValueError as exc:
        raise DomainValidationError(f"{field_name} must be timezone-aware") from exc
    if not is_utc(aware):
        raise DomainValidationError(f"{field_name} must be UTC")
    return to_utc(aware)


def _parse_utc_iso(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DomainValidationError(f"{field_name} must be valid ISO 8601") from exc
    return _require_utc_datetime(parsed, field_name=field_name)


def _require_finite_float(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, float):
        raise DomainValidationError(f"{field_name} must be a float")
    if not math.isfinite(value):
        raise DomainValidationError(f"{field_name} must be finite")
    return value


def _validate_serializable_float_fields(value: OHLCVBar) -> None:
    _require_finite_float(value.open, field_name="open")
    _require_finite_float(value.high, field_name="high")
    _require_finite_float(value.low, field_name="low")
    _require_finite_float(value.close, field_name="close")
    _require_finite_float(value.volume, field_name="volume")
