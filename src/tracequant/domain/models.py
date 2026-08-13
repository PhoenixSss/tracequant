"""Small immutable domain values shared by research-facing modules."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import cast

from tracequant.core.time import ensure_aware, format_utc, is_utc, parse_utc

_INSTRUMENT_MAX_LENGTH = 32


def _object_fields(
    value: object, expected: frozenset[str], model_name: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{model_name} data must be a dict")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{model_name} field names must be strings")

    fields = cast(dict[str, object], value)
    actual = frozenset(fields)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"{model_name} fields do not match: missing={missing}, extra={extra}"
        )
    return fields


def _string_field(fields: dict[str, object], name: str) -> str:
    value = fields[name]
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _float_field(fields: dict[str, object], name: str) -> float:
    value = fields[name]
    if type(value) is not float:
        raise TypeError(f"{name} must be a float")
    return value


def _validate_utc(value: datetime, name: str) -> None:
    ensure_aware(value)
    if not is_utc(value):
        raise ValueError(f"{name} must be UTC")


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    """A normalized instrument symbol for the current market-data context."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("instrument value must be a string")

        stripped = self.value.strip()
        if not stripped:
            raise ValueError("instrument value must not be empty")
        if len(stripped) > _INSTRUMENT_MAX_LENGTH:
            raise ValueError(
                f"instrument value must be at most {_INSTRUMENT_MAX_LENGTH} characters"
            )
        if not all(
            "A" <= character <= "Z"
            or "a" <= character <= "z"
            or "0" <= character <= "9"
            for character in stripped
        ):
            raise ValueError(
                "instrument value must contain only ASCII letters and digits"
            )

        object.__setattr__(self, "value", stripped.upper())

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the stable JSON-compatible string representation."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> "InstrumentId":
        """Build an instrument from its JSON-compatible representation."""
        if not isinstance(value, str):
            raise TypeError("InstrumentId data must be a string")
        return cls(value)


@dataclass(frozen=True, slots=True, order=True)
class TimeRange:
    """A UTC half-open interval with semantics ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime):
            raise TypeError("start must be a datetime")
        if not isinstance(self.end, datetime):
            raise TypeError("end must be a datetime")
        _validate_utc(self.start, "start")
        _validate_utc(self.end, "end")
        if self.start >= self.end:
            raise ValueError("start must be earlier than end")

    def to_dict(self) -> dict[str, str]:
        """Return stable JSON-compatible UTC interval fields."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, value: object) -> "TimeRange":
        """Build an interval, normalizing explicit ISO offsets through the UTC API."""
        fields = _object_fields(value, frozenset({"start", "end"}), "TimeRange")
        return cls(
            start=parse_utc(_string_field(fields, "start")),
            end=parse_utc(_string_field(fields, "end")),
        )


@dataclass(frozen=True, slots=True, order=True)
class OHLCVBar:
    """An immutable OHLCV observation over a UTC half-open interval."""

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
        TimeRange(self.start, self.end)

        numeric_fields = {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        for name, value in numeric_fields.items():
            if type(value) is not float:
                raise TypeError(f"{name} must be a float")
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")

        if self.volume < 0.0:
            raise ValueError("volume must be non-negative")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must not be below open, low, or close")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must not be above open, high, or close")

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
    def from_dict(cls, value: object) -> "OHLCVBar":
        """Build a bar from its strict JSON-compatible public fields."""
        expected = frozenset(
            {"instrument", "start", "end", "open", "high", "low", "close", "volume"}
        )
        fields = _object_fields(value, expected, "OHLCVBar")
        interval = TimeRange.from_dict({"start": fields["start"], "end": fields["end"]})
        return cls(
            instrument=InstrumentId.from_dict(fields["instrument"]),
            start=interval.start,
            end=interval.end,
            open=_float_field(fields, "open"),
            high=_float_field(fields, "high"),
            low=_float_field(fields, "low"),
            close=_float_field(fields, "close"),
            volume=_float_field(fields, "volume"),
        )
