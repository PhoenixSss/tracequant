"""Validated, immutable market-data domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import ClassVar, Self

from tracequant.core.time import format_utc, parse_utc, to_utc


def _require_exact_fields(
    value: object, expected: frozenset[str], model_name: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{model_name} serialized value must be a dict")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{model_name} serialized field names must be strings")
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"{model_name} serialized fields do not match; "
            f"missing={missing}, extra={extra}"
        )
    return value


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _require_float(value: object, field_name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field_name} must be a float")
    if not isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """A normalized instrument identifier for the current market-data context."""

    MAX_LENGTH: ClassVar[int] = 32

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("instrument value must be a string")
        normalized = self.value.strip().upper()
        if not normalized:
            raise ValueError("instrument value must not be empty")
        if len(normalized) > self.MAX_LENGTH:
            raise ValueError(
                f"instrument value must be at most {self.MAX_LENGTH} characters"
            )
        if not normalized.isascii() or not normalized.isalnum():
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
        """Construct an identifier from its JSON-compatible representation."""
        return cls(_require_string(value, "instrument"))


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A timezone-aware UTC half-open interval ``[start, end)``."""

    start: datetime
    end: datetime

    _SERIALIZED_FIELDS: ClassVar[frozenset[str]] = frozenset({"start", "end"})

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise TypeError("start and end must be datetime values")
        start = to_utc(self.start)
        end = to_utc(self.end)
        if start >= end:
            raise ValueError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def contains(self, value: datetime) -> bool:
        """Return whether an aware datetime is within this half-open interval."""
        return self.start <= to_utc(value) < self.end

    def to_dict(self) -> dict[str, str]:
        """Return the stable JSON-compatible representation."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Construct a time range from its serialized representation."""
        fields = _require_exact_fields(value, cls._SERIALIZED_FIELDS, cls.__name__)
        return cls(
            start=parse_utc(_require_string(fields["start"], "start")),
            end=parse_utc(_require_string(fields["end"], "end")),
        )


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """A validated OHLCV observation for a UTC half-open interval."""

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

        for field_name in ("open", "high", "low", "close", "volume"):
            _require_float(getattr(self, field_name), field_name)
        if self.volume < 0:
            raise ValueError("volume must be greater than or equal to zero")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must not be below open, low, or close")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must not be above open, high, or close")

    @property
    def time_range(self) -> TimeRange:
        return TimeRange(self.start, self.end)

    def to_dict(self) -> dict[str, str | float]:
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
    def from_dict(cls, value: object) -> Self:
        """Construct a bar from its serialized representation."""
        fields = _require_exact_fields(value, cls._SERIALIZED_FIELDS, cls.__name__)
        return cls(
            instrument=InstrumentId.from_json_value(fields["instrument"]),
            start=parse_utc(_require_string(fields["start"], "start")),
            end=parse_utc(_require_string(fields["end"], "end")),
            open=_require_float(fields["open"], "open"),
            high=_require_float(fields["high"], "high"),
            low=_require_float(fields["low"], "low"),
            close=_require_float(fields["close"], "close"),
            volume=_require_float(fields["volume"], "volume"),
        )
