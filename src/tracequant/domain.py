"""Initial immutable domain models for TraceQuant Research MVP."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Final, Self

from tracequant.core.time import ensure_aware, format_utc, is_utc, parse_utc, to_utc

__all__ = ["InstrumentId", "TimeRange", "OHLCVBar"]

_INSTRUMENT_MAX_LENGTH: Final = 32


def _require_exact_fields(data: Mapping[str, object], expected: frozenset[str]) -> None:
    actual = frozenset(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"invalid fields: missing={missing}, extra={extra}")


def _normalize_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    ensure_aware(value)
    if not is_utc(value):
        raise ValueError(f"{field} must use UTC")
    return to_utc(value)


def _parse_strict_utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO 8601 datetime") from exc
    _normalize_utc(parsed, field=field)
    return parse_utc(value)


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    """A normalized symbol identifier for the current market-data context."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("instrument value must be a string")
        stripped = self.value.strip()
        if not stripped:
            raise ValueError("instrument value must not be empty")
        if not stripped.isascii():
            raise ValueError(
                "instrument value must contain only ASCII letters and digits"
            )
        normalized = stripped.upper()
        if len(normalized) > _INSTRUMENT_MAX_LENGTH:
            raise ValueError(
                f"instrument value must be at most {_INSTRUMENT_MAX_LENGTH} characters"
            )
        if not normalized.isalnum():
            raise ValueError(
                "instrument value must contain only ASCII letters and digits"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_json_value(self) -> str:
        """Return the stable JSON-compatible string representation."""
        return self.value

    @classmethod
    def from_json_value(cls, value: object) -> Self:
        """Build an identifier from its JSON-compatible representation."""
        if not isinstance(value, str):
            raise TypeError("instrument JSON value must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True, order=True)
class TimeRange:
    """A UTC half-open time interval ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _normalize_utc(self.start, field="start")
        end = _normalize_utc(self.end, field="end")
        if start >= end:
            raise ValueError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        """Return the interval duration."""
        return self.end - self.start

    def contains(self, value: datetime) -> bool:
        """Return whether a UTC datetime is within the half-open interval."""
        normalized = _normalize_utc(value, field="value")
        return self.start <= normalized < self.end

    def to_dict(self) -> dict[str, str]:
        """Return the stable JSON-compatible representation."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        """Build a range from a strict JSON-compatible mapping."""
        _require_exact_fields(data, frozenset({"start", "end"}))
        return cls(
            start=_parse_strict_utc(data["start"], field="start"),
            end=_parse_strict_utc(data["end"], field="end"),
        )


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """An immutable OHLCV bar for one instrument and UTC interval."""

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
            raise TypeError("instrument must be an InstrumentId")
        interval = TimeRange(self.start, self.end)
        object.__setattr__(self, "start", interval.start)
        object.__setattr__(self, "end", interval.end)

        for field in ("open", "high", "low", "close", "volume"):
            value = getattr(self, field)
            if type(value) is not float:
                raise TypeError(f"{field} must be a float")
            if not isfinite(value):
                raise ValueError(f"{field} must be finite")
        if self.volume < 0.0:
            raise ValueError("volume must be non-negative")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must not be below open, low, or close")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must not be above open, high, or close")

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON-compatible representation."""
        return {
            "instrument": self.instrument.to_json_value(),
            "start": format_utc(self.start),
            "end": format_utc(self.end),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        """Build a bar from a strict JSON-compatible mapping."""
        expected = frozenset(
            {"instrument", "start", "end", "open", "high", "low", "close", "volume"}
        )
        _require_exact_fields(data, expected)
        return cls(
            instrument=InstrumentId.from_json_value(data["instrument"]),
            start=_parse_strict_utc(data["start"], field="start"),
            end=_parse_strict_utc(data["end"], field="end"),
            open=_require_float(data["open"], field="open"),
            high=_require_float(data["high"], field="high"),
            low=_require_float(data["low"], field="low"),
            close=_require_float(data["close"], field="close"),
            volume=_require_float(data["volume"], field="volume"),
        )


def _require_float(value: object, *, field: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field} must be a float")
    return value
