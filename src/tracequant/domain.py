"""Small shared domain models for the Research MVP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from re import fullmatch
from typing import ClassVar

from tracequant.core.time import format_utc, is_utc, parse_utc, to_utc

__all__ = ["InstrumentId", "OHLCVBar", "TimeRange"]


def _require_exact_fields(data: object, expected: frozenset[str]) -> dict[str, object]:
    if not isinstance(data, dict):
        raise TypeError("serialized value must be a dict")
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"serialized fields mismatch: missing={missing}, extra={extra}"
        )
    return data


def _require_float(name: str, value: object) -> float:
    if type(value) is not float:
        raise TypeError(f"{name} must be a float")
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    """A normalized instrument identifier without exchange metadata."""

    MAX_LENGTH: ClassVar[int] = 32

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("instrument identifier must be a string")
        normalized = self.value.strip().upper()
        if not normalized:
            raise ValueError("instrument identifier must not be empty")
        if len(normalized) > self.MAX_LENGTH:
            raise ValueError(
                f"instrument identifier must be at most {self.MAX_LENGTH} characters"
            )
        if fullmatch(r"[A-Z0-9]+", normalized, flags=0) is None:
            raise ValueError(
                "instrument identifier must contain only ASCII uppercase letters and digits"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_json_value(self) -> str:
        """Return the JSON-compatible scalar representation."""
        return self.value

    @classmethod
    def from_json_value(cls, value: object) -> InstrumentId:
        """Build an identifier from its JSON-compatible scalar representation."""
        if not isinstance(value, str):
            raise TypeError("serialized instrument identifier must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A timezone-aware UTC half-open interval ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise TypeError("start and end must be datetime values")
        if not is_utc(self.start) or not is_utc(self.end):
            raise ValueError("start and end must be timezone-aware UTC datetimes")
        start = to_utc(self.start)
        end = to_utc(self.end)
        if start >= end:
            raise ValueError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        """Return the interval duration."""
        return self.end - self.start

    def contains(self, value: datetime) -> bool:
        """Return whether a UTC datetime falls within ``[start, end)``."""
        if not isinstance(value, datetime):
            raise TypeError("value must be a datetime")
        if not is_utc(value):
            raise ValueError("value must be a timezone-aware UTC datetime")
        normalized = to_utc(value)
        return self.start <= normalized < self.end

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON-compatible representation."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, data: object) -> TimeRange:
        """Build an interval from its serialized representation."""
        values = _require_exact_fields(data, frozenset({"start", "end"}))
        start = values["start"]
        end = values["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise TypeError("serialized start and end must be strings")
        return cls(start=parse_utc(start), end=parse_utc(end))


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

    _SERIALIZED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"instrument", "start", "end", "open", "high", "low", "close", "volume"}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")
        interval = TimeRange(self.start, self.end)
        object.__setattr__(self, "start", interval.start)
        object.__setattr__(self, "end", interval.end)

        open_price = _require_float("open", self.open)
        high = _require_float("high", self.high)
        low = _require_float("low", self.low)
        close = _require_float("close", self.close)
        volume = _require_float("volume", self.volume)
        if volume < 0.0:
            raise ValueError("volume must be non-negative")
        if high < max(open_price, low, close):
            raise ValueError("high must not be below open, low, or close")
        if low > min(open_price, high, close):
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
    def from_dict(cls, data: object) -> OHLCVBar:
        """Build a bar from its serialized representation."""
        values = _require_exact_fields(data, cls._SERIALIZED_FIELDS)
        start = values["start"]
        end = values["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise TypeError("serialized start and end must be strings")
        return cls(
            instrument=InstrumentId.from_json_value(values["instrument"]),
            start=parse_utc(start),
            end=parse_utc(end),
            open=_require_float("open", values["open"]),
            high=_require_float("high", values["high"]),
            low=_require_float("low", values["low"]),
            close=_require_float("close", values["close"]),
            volume=_require_float("volume", values["volume"]),
        )
