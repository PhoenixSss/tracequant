"""Initial public domain models for research market data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import ClassVar

from tracequant.core.time import format_utc, parse_utc, to_utc

__all__ = ["DomainValidationError", "InstrumentId", "OHLCVBar", "TimeRange"]


class DomainValidationError(ValueError):
    """Raised when a value violates a public domain-model invariant."""


def _require_fields(
    data: object, expected: frozenset[str], model_name: str
) -> Mapping[str, object]:
    if not isinstance(data, Mapping):
        raise TypeError(f"{model_name} data must be a mapping")
    keys = set(data.keys())
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected, key=str)
        raise DomainValidationError(
            f"{model_name} fields do not match; missing={missing}, extra={extra}"
        )
    return data


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _require_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    try:
        return to_utc(value)
    except ValueError as error:
        raise DomainValidationError(f"{field_name} must be timezone-aware") from error


def _parse_datetime(value: object, field_name: str) -> datetime:
    text = _require_string(value, field_name)
    try:
        return parse_utc(text)
    except ValueError as error:
        raise DomainValidationError(
            f"{field_name} must be an aware ISO 8601 datetime"
        ) from error


def _require_finite_float(value: object, field_name: str) -> float:
    if not isinstance(value, float):
        raise TypeError(f"{field_name} must be a float")
    if not isfinite(value):
        raise DomainValidationError(f"{field_name} must be finite")
    return value


@dataclass(frozen=True, order=True, slots=True)
class InstrumentId:
    """A normalized symbol identifier for the current single-market context."""

    MAX_LENGTH: ClassVar[int] = 32

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("instrument value must be a string")
        normalized = self.value.strip().upper()
        if not normalized:
            raise DomainValidationError("instrument must not be empty")
        if len(normalized) > self.MAX_LENGTH:
            raise DomainValidationError(
                f"instrument must not exceed {self.MAX_LENGTH} characters"
            )
        if not normalized.isascii() or not normalized.isalnum():
            raise DomainValidationError(
                "instrument must contain only ASCII uppercase letters and digits"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the stable JSON-compatible scalar representation."""
        return self.value

    @classmethod
    def from_dict(cls, data: object) -> InstrumentId:
        """Construct an instrument from its JSON-compatible representation."""
        return cls(_require_string(data, "instrument"))


@dataclass(frozen=True, order=True, slots=True)
class TimeRange:
    """A timezone-aware UTC half-open interval ``[start, end)``."""

    start: datetime
    end: datetime

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"start", "end"})

    def __post_init__(self) -> None:
        start = _require_datetime(self.start, "start")
        end = _require_datetime(self.end, "end")
        if start >= end:
            raise DomainValidationError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def to_dict(self) -> dict[str, str]:
        """Return stable JSON-compatible public fields."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, data: object) -> TimeRange:
        """Construct a range from strict JSON-compatible public fields."""
        fields = _require_fields(data, cls._FIELDS, cls.__name__)
        return cls(
            start=_parse_datetime(fields["start"], "start"),
            end=_parse_datetime(fields["end"], "end"),
        )


@dataclass(frozen=True, order=True, slots=True)
class OHLCVBar:
    """A validated OHLCV observation over a UTC half-open interval."""

    instrument: InstrumentId
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"instrument", "start", "end", "open", "high", "low", "close", "volume"}
    )

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")
        interval = TimeRange(self.start, self.end)
        values = {
            "open": _require_finite_float(self.open, "open"),
            "high": _require_finite_float(self.high, "high"),
            "low": _require_finite_float(self.low, "low"),
            "close": _require_finite_float(self.close, "close"),
            "volume": _require_finite_float(self.volume, "volume"),
        }
        if values["volume"] < 0:
            raise DomainValidationError("volume must be greater than or equal to zero")
        if values["high"] < max(values["open"], values["low"], values["close"]):
            raise DomainValidationError("high must not be below open, low, or close")
        if values["low"] > min(values["open"], values["high"], values["close"]):
            raise DomainValidationError("low must not be above open, high, or close")
        object.__setattr__(self, "start", interval.start)
        object.__setattr__(self, "end", interval.end)

    def to_dict(self) -> dict[str, str | float]:
        """Return stable JSON-compatible public fields."""
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
    def from_dict(cls, data: object) -> OHLCVBar:
        """Construct a bar from strict JSON-compatible public fields."""
        fields = _require_fields(data, cls._FIELDS, cls.__name__)
        return cls(
            instrument=InstrumentId.from_dict(fields["instrument"]),
            start=_parse_datetime(fields["start"], "start"),
            end=_parse_datetime(fields["end"], "end"),
            open=_require_finite_float(fields["open"], "open"),
            high=_require_finite_float(fields["high"], "high"),
            low=_require_finite_float(fields["low"], "low"),
            close=_require_finite_float(fields["close"], "close"),
            volume=_require_finite_float(fields["volume"], "volume"),
        )
