"""UTC datetime validation, conversion, parsing, and formatting."""

from datetime import UTC, datetime, timedelta

__all__ = ["UTC", "ensure_aware", "format_utc", "is_utc", "parse_utc", "to_utc"]

_AWARE_REQUIRED_MESSAGE = "datetime must be timezone-aware"


def ensure_aware(value: datetime) -> datetime:
    """Return an aware datetime unchanged; raise ValueError for a naive value."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(_AWARE_REQUIRED_MESSAGE)
    return value


def to_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC; raise ValueError for a naive value."""
    return ensure_aware(value).astimezone(UTC)


def is_utc(value: datetime) -> bool:
    """Return whether a datetime has a zero offset, or False if it is naive."""
    if value.tzinfo is None:
        return False
    offset = value.utcoffset()
    return offset is not None and offset == timedelta(0)


def parse_utc(value: str) -> datetime:
    """Parse ISO 8601 to UTC; raise ValueError if its datetime is naive."""
    return to_utc(datetime.fromisoformat(value))


def format_utc(value: datetime) -> str:
    """Format an aware datetime in UTC with Z; raise ValueError if it is naive."""
    formatted = to_utc(value).isoformat()
    return f"{formatted.removesuffix('+00:00')}Z"
