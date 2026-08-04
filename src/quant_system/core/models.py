"""Small immutable domain models for research market data."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Self

from .time import format_utc, parse_utc, to_utc

__all__ = ["InstrumentId", "OHLCVBar", "TimeRange"]

_INSTRUMENT_PATTERN: Final = re.compile(r"^[A-Z0-9]+$")
_INSTRUMENT_MAX_LENGTH: Final = 32
_TIME_RANGE_KEYS: Final = frozenset({"start", "end"})
_OHLCV_KEYS: Final = frozenset(
    {"instrument", "start", "end", "open", "high", "low", "close", "volume"}
)


def _require_exact_keys(data: Mapping[str, object], expected: frozenset[str]) -> None:
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"invalid fields; missing={missing}, extra={extra}")


def _require_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    return value


def _require_float(value: object, field_name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field_name} must be a float")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


@dataclass(frozen=True, slots=True, init=False)
class InstrumentId:
    """A normalized single-market instrument identifier.

    Identifiers are trimmed, uppercased, and then restricted to one through
    thirty-two ASCII letters or digits. Venue, exchange, and quote metadata are
    intentionally outside this Research MVP value object.
    """

    value: str

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("instrument must be a string")
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("instrument must not be empty")
        if len(normalized) > _INSTRUMENT_MAX_LENGTH:
            raise ValueError("instrument must be at most 32 characters")
        if _INSTRUMENT_PATTERN.fullmatch(normalized) is None:
            raise ValueError("instrument must contain only ASCII letters and digits")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the JSON-compatible scalar representation."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Build an identifier from its JSON-compatible scalar representation."""
        if not isinstance(value, str):
            raise TypeError("instrument must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A UTC half-open time interval, ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = to_utc(_require_datetime(self.start, "start"))
        end = to_utc(_require_datetime(self.end, "end"))
        if start >= end:
            raise ValueError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        """Return the interval duration."""
        return self.end - self.start

    def contains(self, value: datetime) -> bool:
        """Return whether a datetime is inside the half-open interval."""
        normalized = to_utc(_require_datetime(value, "value"))
        return self.start <= normalized < self.end

    def to_dict(self) -> dict[str, str]:
        """Return a deterministic JSON-compatible representation."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        """Build an interval from strict UTC ISO-compatible fields."""
        _require_exact_keys(data, _TIME_RANGE_KEYS)
        start = data["start"]
        end = data["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise TypeError("start and end must be ISO datetime strings")
        return cls(parse_utc(start), parse_utc(end))


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """An immutable OHLCV bar over a UTC half-open interval."""

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
        values = {
            "open": _require_float(self.open, "open"),
            "high": _require_float(self.high, "high"),
            "low": _require_float(self.low, "low"),
            "close": _require_float(self.close, "close"),
            "volume": _require_float(self.volume, "volume"),
        }
        if values["volume"] < 0.0:
            raise ValueError("volume must be non-negative")
        if values["high"] < max(values["open"], values["low"], values["close"]):
            raise ValueError("high must be at least open, low, and close")
        if values["low"] > min(values["open"], values["high"], values["close"]):
            raise ValueError("low must be at most open, high, and close")
        object.__setattr__(self, "start", interval.start)
        object.__setattr__(self, "end", interval.end)
        for field_name, value in values.items():
            object.__setattr__(self, field_name, value)

    def to_dict(self) -> dict[str, str | float]:
        """Return a deterministic JSON-compatible representation."""
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
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        """Build a bar from strict JSON-compatible fields."""
        _require_exact_keys(data, _OHLCV_KEYS)
        instrument = InstrumentId.from_dict(data["instrument"])
        start = data["start"]
        end = data["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise TypeError("start and end must be ISO datetime strings")
        return cls(
            instrument=instrument,
            start=parse_utc(start),
            end=parse_utc(end),
            open=_require_float(data["open"], "open"),
            high=_require_float(data["high"], "high"),
            low=_require_float(data["low"], "low"),
            close=_require_float(data["close"], "close"),
            volume=_require_float(data["volume"], "volume"),
        )
