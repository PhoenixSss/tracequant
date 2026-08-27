"""Initial Research MVP domain models.

The models in this module are deliberately small, immutable value objects.  They
contain no exchange, storage, configuration, or application orchestration
concerns.
"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Self, cast

from tracequant.core.time import format_utc, parse_utc, to_utc

__all__ = ["DomainValidationError", "InstrumentId", "OHLCVBar", "TimeRange"]


class DomainValidationError(ValueError):
    """Raised when input violates a public domain model invariant."""


_INSTRUMENT_PATTERN = re.compile(r"[A-Z0-9]+\Z")
_TIME_RANGE_FIELDS = frozenset({"start", "end"})
_OHLCV_FIELDS = frozenset(
    {"instrument", "start", "end", "open", "high", "low", "close", "volume"}
)


def _require_mapping(
    value: object, fields: frozenset[str], model: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{model} data must be a mapping")
    if set(value) != fields:
        raise DomainValidationError(
            f"{model} data must contain exactly: {', '.join(sorted(fields))}"
        )
    return cast(Mapping[str, object], value)


def _require_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field} must be a datetime")
    return value


def _require_float(value: object, field: str) -> float:
    if type(value) is not float:
        raise DomainValidationError(f"{field} must be a float")
    if not math.isfinite(value):
        raise DomainValidationError(f"{field} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """A normalized single-market instrument identifier."""

    value: str
    MAX_LENGTH: ClassVar[int] = 32

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise DomainValidationError("instrument must be a string")

        trimmed = self.value.strip()
        if not trimmed:
            raise DomainValidationError("instrument must not be empty")
        if not trimmed.isascii():
            raise DomainValidationError("instrument must contain ASCII characters only")

        normalized = trimmed.upper()
        if len(normalized) > self.MAX_LENGTH:
            raise DomainValidationError(
                f"instrument must be at most {self.MAX_LENGTH} characters"
            )
        if _INSTRUMENT_PATTERN.fullmatch(normalized) is None:
            raise DomainValidationError(
                "instrument must contain only ASCII letters and digits"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the JSON-compatible normalized identifier."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Build an identifier from its JSON-compatible string form."""
        if not isinstance(value, str):
            raise DomainValidationError("instrument data must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A UTC half-open interval ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = to_utc(_require_datetime(self.start, "start"))
        end = to_utc(_require_datetime(self.end, "end"))
        if start >= end:
            raise DomainValidationError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        """Return the interval duration."""
        return self.end - self.start

    def __contains__(self, value: object) -> bool:
        if not isinstance(value, datetime):
            return False
        value_utc = to_utc(value)
        return self.start <= value_utc < self.end

    def to_dict(self) -> dict[str, str]:
        """Return the stable JSON-compatible interval representation."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Build an interval from its stable UTC ISO 8601 representation."""
        data = _require_mapping(value, _TIME_RANGE_FIELDS, "TimeRange")
        start = data["start"]
        end = data["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise DomainValidationError("TimeRange datetimes must be strings")
        return cls(parse_utc(start), parse_utc(end))


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """One validated OHLCV bar over a UTC half-open interval."""

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
        object.__setattr__(self, "start", interval.start)
        object.__setattr__(self, "end", interval.end)

        open_price = _require_float(self.open, "open")
        high_price = _require_float(self.high, "high")
        low_price = _require_float(self.low, "low")
        close_price = _require_float(self.close, "close")
        volume = _require_float(self.volume, "volume")
        if volume < 0:
            raise DomainValidationError("volume must be non-negative")
        if (
            high_price < open_price
            or high_price < low_price
            or high_price < close_price
        ):
            raise DomainValidationError("high must not be lower than OHLC prices")
        if low_price > open_price or low_price > high_price or low_price > close_price:
            raise DomainValidationError("low must not be higher than OHLC prices")

    def to_dict(self) -> dict[str, str | float]:
        """Return the stable JSON-compatible bar representation."""
        return {
            "instrument": self.instrument.to_dict(),
            "start": format_utc(self.start),
            "end": format_utc(self.end),
            "open": _require_float(self.open, "open"),
            "high": _require_float(self.high, "high"),
            "low": _require_float(self.low, "low"),
            "close": _require_float(self.close, "close"),
            "volume": _require_float(self.volume, "volume"),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Build a bar from its stable JSON-compatible representation."""
        data = _require_mapping(value, _OHLCV_FIELDS, "OHLCVBar")
        instrument = InstrumentId.from_dict(data["instrument"])
        start = data["start"]
        end = data["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise DomainValidationError("OHLCVBar datetimes must be strings")
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
