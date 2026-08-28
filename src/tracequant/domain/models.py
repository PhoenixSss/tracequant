"""Validated, immutable market-data domain models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from string import ascii_uppercase, digits
from typing import Final, Self

from tracequant.core.time import format_utc, parse_utc, to_utc

_INSTRUMENT_CHARACTERS: Final = frozenset(ascii_uppercase + digits)
_MAX_INSTRUMENT_LENGTH: Final = 32


class DomainValidationError(ValueError):
    """Raised when a value violates a public domain-model invariant."""


def _require_exact_fields(
    value: object, *, expected: frozenset[str], model: str
) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{model} serialized value must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{model} serialized field names must be strings")

    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"extra fields: {', '.join(sorted(extra))}")
        raise DomainValidationError(f"invalid {model} fields ({'; '.join(details)})")
    return value


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _require_float(value: object, *, field: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field} must be a float")
    if not isfinite(value):
        raise DomainValidationError(f"{field} must be finite")
    return value


def _normalize_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    try:
        return to_utc(value)
    except ValueError as error:
        raise DomainValidationError(f"{field} must be timezone-aware") from error


def _parse_datetime(value: object, *, field: str) -> datetime:
    text = _require_string(value, field=field)
    try:
        return parse_utc(text)
    except ValueError as error:
        raise DomainValidationError(
            f"{field} must be an aware ISO 8601 datetime"
        ) from error


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """A normalized instrument identifier in the current single-market context."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("instrument value must be a string")
        normalized = self.value.strip().upper()
        if not normalized:
            raise DomainValidationError("instrument value must not be empty")
        if len(normalized) > _MAX_INSTRUMENT_LENGTH:
            raise DomainValidationError(
                f"instrument value must be at most {_MAX_INSTRUMENT_LENGTH} characters"
            )
        if any(character not in _INSTRUMENT_CHARACTERS for character in normalized):
            raise DomainValidationError(
                "instrument value must contain only ASCII uppercase letters and digits"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the stable JSON-compatible string representation."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Create an identifier from its JSON-compatible representation."""
        return cls(_require_string(value, field="instrument"))


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A timezone-aware UTC half-open interval ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = _normalize_datetime(self.start, field="start")
        end = _normalize_datetime(self.end, field="end")
        if start >= end:
            raise DomainValidationError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def to_dict(self) -> dict[str, str]:
        """Return stable JSON-compatible interval fields."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Create a range from exact serialized fields."""
        fields = _require_exact_fields(
            value, expected=frozenset({"start", "end"}), model="TimeRange"
        )
        return cls(
            start=_parse_datetime(fields["start"], field="start"),
            end=_parse_datetime(fields["end"], field="end"),
        )


@dataclass(frozen=True, slots=True)
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

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentId):
            raise TypeError("instrument must be an InstrumentId")

        interval = TimeRange(start=self.start, end=self.end)
        object.__setattr__(self, "start", interval.start)
        object.__setattr__(self, "end", interval.end)

        for field in ("open", "high", "low", "close", "volume"):
            _require_float(getattr(self, field), field=field)

        if self.volume < 0:
            raise DomainValidationError("volume must be greater than or equal to zero")
        if self.high < max(self.open, self.low, self.close):
            raise DomainValidationError("high must not be below open, low, or close")
        if self.low > min(self.open, self.high, self.close):
            raise DomainValidationError("low must not be above open, high, or close")

    def to_dict(self) -> dict[str, str | float]:
        """Return stable JSON-compatible bar fields."""
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
        """Create a bar from exact serialized fields."""
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "instrument",
                    "start",
                    "end",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                }
            ),
            model="OHLCVBar",
        )
        return cls(
            instrument=InstrumentId.from_dict(fields["instrument"]),
            start=_parse_datetime(fields["start"], field="start"),
            end=_parse_datetime(fields["end"], field="end"),
            open=_require_float(fields["open"], field="open"),
            high=_require_float(fields["high"], field="high"),
            low=_require_float(fields["low"], field="low"),
            close=_require_float(fields["close"], field="close"),
            volume=_require_float(fields["volume"], field="volume"),
        )
