"""Immutable, validated domain values shared by research modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import ClassVar, Self

from tracequant.core.time import format_utc, is_utc, parse_utc, to_utc


def _require_exact_fields(
    value: Mapping[str, object], expected: frozenset[str], model_name: str
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"invalid {model_name} fields: missing={missing}, extra={extra}"
        )


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _require_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    return value


def _require_float(value: object, field_name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field_name} must be a float")
    if not isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    """A normalized instrument identifier without exchange metadata."""

    value: str

    MAX_LENGTH: ClassVar[int] = 32

    def __post_init__(self) -> None:
        value = _require_string(self.value, "instrument").strip().upper()
        if not value:
            raise ValueError("instrument must not be empty")
        if len(value) > self.MAX_LENGTH:
            raise ValueError(f"instrument must not exceed {self.MAX_LENGTH} characters")
        if not value.isascii() or not value.isalnum():
            raise ValueError(
                "instrument must contain only ASCII uppercase letters and digits"
            )
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the JSON-compatible string representation."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Build an identifier from its JSON-compatible representation."""
        return cls(_require_string(value, "instrument"))


@dataclass(frozen=True, slots=True, order=True)
class TimeRange:
    """A non-empty, half-open UTC interval: ``[start, end)``."""

    start: datetime
    end: datetime

    _SERIALIZED_FIELDS: ClassVar[frozenset[str]] = frozenset({"start", "end"})

    def __post_init__(self) -> None:
        start = _require_datetime(self.start, "start")
        end = _require_datetime(self.end, "end")
        if not is_utc(start) or not is_utc(end):
            raise ValueError("start and end must be timezone-aware UTC datetimes")
        start = to_utc(start)
        end = to_utc(end)
        if start >= end:
            raise ValueError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        """Return the interval duration."""
        return self.end - self.start

    def contains(self, value: datetime) -> bool:
        """Return whether an aware UTC datetime is in ``[start, end)``."""
        value = _require_datetime(value, "value")
        if not is_utc(value):
            raise ValueError("value must be a timezone-aware UTC datetime")
        return self.start <= to_utc(value) < self.end

    def to_dict(self) -> dict[str, str]:
        """Return a stable JSON-compatible representation."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        """Build a range from a strict JSON-compatible representation."""
        _require_exact_fields(value, cls._SERIALIZED_FIELDS, "TimeRange")
        start = parse_utc(_require_string(value["start"], "start"))
        end = parse_utc(_require_string(value["end"], "end"))
        return cls(start=start, end=end)


@dataclass(frozen=True, slots=True, order=True)
class OHLCVBar:
    """An immutable OHLCV bar for one instrument and UTC time range.

    Prices are floats and may be zero or negative for research datasets. Volume
    must be non-negative. All numeric values must be finite.
    """

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

        interval = TimeRange(start=self.start, end=self.end)
        object.__setattr__(self, "start", interval.start)
        object.__setattr__(self, "end", interval.end)

        numeric_values = {
            "open": _require_float(self.open, "open"),
            "high": _require_float(self.high, "high"),
            "low": _require_float(self.low, "low"),
            "close": _require_float(self.close, "close"),
            "volume": _require_float(self.volume, "volume"),
        }
        if numeric_values["volume"] < 0.0:
            raise ValueError("volume must be non-negative")
        if numeric_values["high"] < max(
            numeric_values["open"], numeric_values["low"], numeric_values["close"]
        ):
            raise ValueError("high must be at least open, low, and close")
        if numeric_values["low"] > min(
            numeric_values["open"], numeric_values["high"], numeric_values["close"]
        ):
            raise ValueError("low must be at most open, high, and close")

    def to_dict(self) -> dict[str, str | float]:
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
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        """Build a bar from a strict JSON-compatible representation."""
        _require_exact_fields(value, cls._SERIALIZED_FIELDS, "OHLCVBar")
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
