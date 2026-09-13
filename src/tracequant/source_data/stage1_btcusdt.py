from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

STAGE1_DATA_PATH: Final = "USE_NAUTILUS"
STAGE1_SOURCE_URL: Final = "https://fapi.binance.com"
STAGE1_INSTRUMENT_ID: Final = "BTCUSDT-PERP.BINANCE"
STAGE1_BAR_TYPE: Final = "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
STAGE1_WINDOW_START_ISO: Final = "2026-06-15T00:00:00Z"
STAGE1_WINDOW_END_ISO: Final = "2026-09-13T00:00:00Z"
STAGE1_MAX_BARS_PER_REQUEST: Final = 1500
STAGE1_SEGMENT_DAYS: Final = 30
STAGE1_MIN_HOURS: Final = 720
STAGE1_MAX_HOURS: Final = 2160
STAGE1_CATALOG_SCHEMA: Final = "stage1-btcusdt-1h"
STAGE1_CATALOG_ENVIRONMENT: Final = "offline"
STAGE1_PROVENANCE_FILENAME: Final = "stage1_btcusdt_provenance.json"
HOUR_NS: Final = 3_600_000_000_000


class Stage1DataError(ValueError):
    """Raised when stage 1 source identity or bar integrity is invalid."""


@dataclass(frozen=True)
class Stage1BarProvenance:
    source_url: str
    data_path: str
    fetched_at: datetime
    window_start: datetime
    window_end: datetime
    bar_count: int
    checksum_sha256: str
    bar_type: str
    first_ts_event: int
    last_ts_event: int
    runtime_identity: str

    def to_json_dict(self) -> dict[str, str | int]:
        return {
            "source_url": self.source_url,
            "data_path": self.data_path,
            "fetched_at": isoformat_utc(self.fetched_at),
            "window_start": isoformat_utc(self.window_start),
            "window_end": isoformat_utc(self.window_end),
            "bar_count": self.bar_count,
            "checksum_sha256": self.checksum_sha256,
            "bar_type": self.bar_type,
            "first_ts_event": self.first_ts_event,
            "last_ts_event": self.last_ts_event,
            "runtime_identity": self.runtime_identity,
        }

    @classmethod
    def from_json_dict(cls, payload: dict[str, object]) -> Stage1BarProvenance:
        return cls(
            source_url=_require_str(payload, "source_url"),
            data_path=_require_str(payload, "data_path"),
            fetched_at=parse_utc(_require_str(payload, "fetched_at")),
            window_start=parse_utc(_require_str(payload, "window_start")),
            window_end=parse_utc(_require_str(payload, "window_end")),
            bar_count=_require_int(payload, "bar_count"),
            checksum_sha256=_require_str(payload, "checksum_sha256"),
            bar_type=_require_str(payload, "bar_type"),
            first_ts_event=_require_int(payload, "first_ts_event"),
            last_ts_event=_require_int(payload, "last_ts_event"),
            runtime_identity=_require_str(payload, "runtime_identity"),
        )


def parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise Stage1DataError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def isoformat_utc(value: datetime) -> str:
    return require_utc(value).isoformat().replace("+00:00", "Z")


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise Stage1DataError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def unix_nanos(value: datetime) -> int:
    utc = require_utc(value)
    seconds = int(
        (utc.replace(microsecond=0) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()
    )
    return seconds * 1_000_000_000 + utc.microsecond * 1_000


def closed_bar_ts_event(hour_open: datetime) -> int:
    close = require_utc(hour_open) + timedelta(hours=1) - timedelta(milliseconds=1)
    return unix_nanos(close)


def stage1_window() -> tuple[datetime, datetime]:
    return parse_utc(STAGE1_WINDOW_START_ISO), parse_utc(STAGE1_WINDOW_END_ISO)


def expected_bar_count(window_start: datetime, window_end: datetime) -> int:
    start = require_utc(window_start)
    end = require_utc(window_end)
    delta = end - start
    seconds = delta.total_seconds()
    if seconds <= 0 or seconds % 3600:
        raise Stage1DataError("window must be a positive whole-hour interval")
    return int(seconds) // 3600


def require_hour_aligned(value: datetime, *, field: str) -> datetime:
    utc = require_utc(value)
    if utc.minute or utc.second or utc.microsecond:
        raise Stage1DataError(f"{field} must be hour-aligned UTC")
    return utc


def require_stage1_window(
    window_start: datetime, window_end: datetime
) -> tuple[datetime, datetime]:
    start = require_hour_aligned(window_start, field="window_start")
    end = require_hour_aligned(window_end, field="window_end")
    hours = expected_bar_count(start, end)
    if hours < STAGE1_MIN_HOURS or hours > STAGE1_MAX_HOURS:
        raise Stage1DataError("stage 1 window must cover 30-90 days of 1h bars")
    return start, end


def request_segments(
    window_start: datetime,
    window_end: datetime,
    *,
    segment_days: int = STAGE1_SEGMENT_DAYS,
) -> tuple[tuple[datetime, datetime], ...]:
    start, end = require_stage1_window(window_start, window_end)
    if segment_days <= 0:
        raise Stage1DataError("segment_days must be positive")
    step = timedelta(days=segment_days)
    segments: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        hours = expected_bar_count(cursor, nxt)
        if hours > STAGE1_MAX_BARS_PER_REQUEST:
            raise Stage1DataError("request segment exceeds the 1500-bar server cap")
        segments.append((cursor, nxt))
        cursor = nxt
    return tuple(segments)


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise Stage1DataError(f"provenance field {key} must be a non-empty string")
    return value


def _require_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise Stage1DataError(f"provenance field {key} must be an integer")
    return value
