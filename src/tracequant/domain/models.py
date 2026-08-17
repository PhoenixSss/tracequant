"""Immutable, validated domain values shared by research modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Final

from tracequant.core.time import format_utc, is_utc, parse_utc, to_utc

_MAX_INSTRUMENT_LENGTH: Final = 32


def _require_exact_fields(
    value: Mapping[str, object], expected: frozenset[str], model: str
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"invalid {model} fields: missing={missing}, extra={extra}")


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _require_float(value: object, field: str) -> float:
    if not isinstance(value, float):
        raise TypeError(f"{field} must be a float")
    if not isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def _require_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if not is_utc(value):
        raise ValueError(f"{field} must be timezone-aware UTC")
    return to_utc(value)


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """A normalized instrument identifier without exchange metadata."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("instrument value must be a string")
        normalized = self.value.strip()
        if not normalized:
            raise ValueError("instrument value must not be empty")
        if not normalized.isascii():
            raise ValueError(
                "instrument value must contain only ASCII letters and digits"
            )
        normalized = normalized.upper()
        if not normalized.isalnum():
            raise ValueError(
                "instrument value must contain only ASCII letters and digits"
            )
        if len(normalized) > _MAX_INSTRUMENT_LENGTH:
            raise ValueError(
                f"instrument value must be at most {_MAX_INSTRUMENT_LENGTH} characters"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the stable JSON-compatible representation."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> InstrumentId:
        """Build an identifier from its JSON-compatible representation."""
        return cls(_require_string(value, "instrument"))


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A timezone-aware UTC half-open interval ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _require_utc(self.start, "start")
        end = _require_utc(self.end, "end")
        if start >= end:
            raise ValueError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        """Return the duration of the interval."""
        return self.end - self.start

    def contains(self, value: datetime) -> bool:
        """Return whether an aware UTC datetime is inside ``[start, end)``."""
        candidate = _require_utc(value, "value")
        return self.start <= candidate < self.end

    def to_dict(self) -> dict[str, str]:
        """Return a stable JSON-compatible representation."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> TimeRange:
        """Build an interval from its JSON-compatible representation."""
        _require_exact_fields(value, frozenset({"start", "end"}), "TimeRange")
        return cls(
            start=parse_utc(_require_string(value["start"], "start")),
            end=parse_utc(_require_string(value["end"], "end")),
        )


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """A validated OHLCV bar for one instrument and half-open UTC interval."""

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

        prices = {
            "open": _require_float(self.open, "open"),
            "high": _require_float(self.high, "high"),
            "low": _require_float(self.low, "low"),
            "close": _require_float(self.close, "close"),
        }
        volume = _require_float(self.volume, "volume")
        if volume < 0:
            raise ValueError("volume must be non-negative")
        if prices["high"] < max(prices.values()):
            raise ValueError("high must not be below open, low, or close")
        if prices["low"] > min(prices.values()):
            raise ValueError("low must not be above open, high, or close")

    @property
    def time_range(self) -> TimeRange:
        """Return the bar's half-open time interval."""
        return TimeRange(self.start, self.end)

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-compatible representation."""
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
        """Build a bar from its JSON-compatible representation."""
        fields = frozenset(
            {"instrument", "start", "end", "open", "high", "low", "close", "volume"}
        )
        _require_exact_fields(value, fields, "OHLCVBar")
        return cls(
            instrument=InstrumentId.from_dict(value["instrument"]),
            start=parse_utc(_require_string(value["start"], "start")),
            end=parse_utc(_require_string(value["end"], "end")),
            open=_require_float(value["open"], "open"),
            high=_require_float(value["high"], "high"),
            low=_require_float(value["low"], "low"),
            close=_require_float(value["close"], "close"),
            volume=_require_float(value["volume"], "volume"),
        )
