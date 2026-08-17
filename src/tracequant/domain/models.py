"""Small immutable domain models shared by research components."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Final

from tracequant.core.time import format_utc, parse_utc, to_utc

_INSTRUMENT_MAX_LENGTH: Final = 32
_TIME_RANGE_FIELDS: Final = frozenset({"start", "end"})
_BAR_FIELDS: Final = frozenset(
    {"instrument", "start", "end", "open", "high", "low", "close", "volume"}
)


def _require_exact_fields(data: Mapping[str, object], expected: frozenset[str]) -> None:
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"invalid fields: missing={missing}, extra={extra}")


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _as_finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} must be a number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    """A normalized symbol for the current single-market-data context."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("instrument must be a string")
        stripped = self.value.strip()
        if not stripped:
            raise ValueError("instrument must not be empty")
        if not stripped.isascii():
            raise ValueError("instrument must contain only ASCII letters and digits")
        normalized = stripped.upper()
        if not normalized.isalnum():
            raise ValueError("instrument must contain only ASCII letters and digits")
        if len(normalized) > _INSTRUMENT_MAX_LENGTH:
            raise ValueError(
                f"instrument must be at most {_INSTRUMENT_MAX_LENGTH} characters"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> str:
        """Return the stable JSON-compatible representation."""
        return self.value

    @classmethod
    def from_dict(cls, value: object) -> "InstrumentId":
        """Build an instrument from its JSON-compatible representation."""
        return cls(_require_string(value, "instrument"))


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A UTC half-open time interval ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = to_utc(self.start)
        end = to_utc(self.end)
        if start >= end:
            raise ValueError("start must be earlier than end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def to_dict(self) -> dict[str, str]:
        """Return the stable JSON-compatible representation."""
        return {"start": format_utc(self.start), "end": format_utc(self.end)}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "TimeRange":
        """Build a time range from its JSON-compatible representation."""
        _require_exact_fields(data, _TIME_RANGE_FIELDS)
        return cls(
            start=parse_utc(_require_string(data["start"], "start")),
            end=parse_utc(_require_string(data["end"], "end")),
        )


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """A validated OHLCV bar over a UTC half-open time interval."""

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
            object.__setattr__(
                self, field, _as_finite_float(getattr(self, field), field)
            )

        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must not be below open, low, or close")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must not be above open, high, or close")

    def to_dict(self) -> dict[str, str | float]:
        """Return the stable JSON-compatible representation."""
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
    def from_dict(cls, data: Mapping[str, object]) -> "OHLCVBar":
        """Build a bar from its JSON-compatible representation."""
        _require_exact_fields(data, _BAR_FIELDS)
        return cls(
            instrument=InstrumentId.from_dict(data["instrument"]),
            start=parse_utc(_require_string(data["start"], "start")),
            end=parse_utc(_require_string(data["end"], "end")),
            open=_as_finite_float(data["open"], "open"),
            high=_as_finite_float(data["high"], "high"),
            low=_as_finite_float(data["low"], "low"),
            close=_as_finite_float(data["close"], "close"),
            volume=_as_finite_float(data["volume"], "volume"),
        )
