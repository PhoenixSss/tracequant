from __future__ import annotations

import csv
import hashlib
import io
import json
import tomllib
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, cast

STAGE2_CONFIG_SCHEMA: Final = "tracequant-stage2-dataset-v1"
STAGE2_SOURCE_SCHEMA: Final = "tracequant-stage2-source-v1"
STAGE2_DATASET_ID: Final = "binance-usdm-btceth-202001-202608-r1"
STAGE2_NAUTILUS_VERSION: Final = "2.0.0rc4"
STAGE2_ENVIRONMENT: Final = "offline"
STAGE2_SOURCE: Final = "binance-public-data"
STAGE2_MARKET: Final = "futures/um"
STAGE2_ARCHIVE_FREQUENCY: Final = "monthly"
STAGE2_WINDOW_START_ISO: Final = "2020-01-01T00:00:00Z"
STAGE2_WINDOW_END_ISO: Final = "2026-09-01T00:00:00Z"
STAGE2_INSTRUMENT_IDS: Final = (
    "BTCUSDT-PERP.BINANCE",
    "ETHUSDT-PERP.BINANCE",
)
STAGE2_BAR_INTERVALS: Final = ("15m", "1h", "4h")
STAGE2_BAR_AGGREGATION: Final = "LAST-EXTERNAL"
STAGE2_SOURCE_KIND: Final = "binance_public_data"
STAGE2_DATA_TYPE_BARS: Final = "bars"
STAGE2_DATA_TYPE_MARK: Final = "mark_price"
STAGE2_DATA_TYPE_FUNDING: Final = "funding"
STAGE2_MANIFEST_FILENAME: Final = "stage2_source_manifest.json"
STAGE2_COVERAGE_FILENAME: Final = "stage2_coverage.json"
STAGE2_INSTRUMENT_SNAPSHOT_FILENAME: Final = "stage2_instrument_snapshot.json"
STAGE2_DIGEST_FILENAME: Final = "stage2_dataset_digest.json"
STAGE2_ACCEPTANCE_SCHEMA: Final = "tracequant-stage2-acceptance-v2"
STAGE2_PUBLIC_DATA_ORIGIN: Final = "https://data.binance.vision"
STAGE2_KLINE_ROOT: Final = "data/futures/um/monthly/klines"
STAGE2_MARK_ROOT: Final = "data/futures/um/monthly/markPriceKlines"
STAGE2_DAILY_MARK_ROOT: Final = "data/futures/um/daily/markPriceKlines"
STAGE2_FUNDING_ROOT: Final = "data/futures/um/monthly/fundingRate"
STAGE2_INDEX_ROOT: Final = "data/futures/um/monthly/indexPriceKlines"
STAGE2_MARK_INTERVAL: Final = "15m"
STAGE2_INDEX_INTERVAL: Final = "15m"
STAGE2_FUNDING_STREAM_ID: Final = "stage2-funding"
STAGE2_MARK_MAX_AGE_MS: Final = 15 * 60 * 1000
STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS: Final = 60 * 1000
STAGE2_EXPECTED_SOURCE_COUNT: Final = 800
STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT: Final = 18
STAGE2_INCLUDE_INDEX_PRICE: Final = False
STAGE2_NAUTILUS_TAIL_ENABLED: Final = False
STAGE2_CROSSCHECK_START_ISO: Final = "2026-08-25T00:00:00Z"
STAGE2_CROSSCHECK_END_ISO: Final = "2026-09-01T00:00:00Z"
STAGE2_SMA_PARITY_START_ISO: Final = STAGE2_WINDOW_START_ISO
STAGE2_SMA_PARITY_END_ISO: Final = "2020-01-02T00:00:00Z"
STAGE2_SMA_PARITY_INTERVAL: Final = "15m"
STAGE2_SMA_PARITY_PERIODS: Final = (10, 20)
STAGE2_GENERATION_COMMAND: Final = (
    "uv run --frozen python -m tracequant.integrations.nautilus.stage2_btceth"
)
STAGE2_CONFIG_ENV: Final = "TRACEQUANT_STAGE2_CONFIG"
STAGE2_MARK_GAP_EXPLANATION: Final = (
    "Binance monthly and daily markPriceKlines both omit the 2020-01-19T13:15Z "
    "and 2023-11-10T03:45Z intervals; no funding event overlaps either omission"
)
STAGE2_MARK_ALLOWED_GAPS: Final = (
    (1579438800000, 1579440600000),
    (1699587000000, 1699588800000),
)
MS_NS: Final = 1_000_000

STAGE2_INSTRUMENT_SYMBOLS: Final = {
    "BTCUSDT-PERP.BINANCE": "BTCUSDT",
    "ETHUSDT-PERP.BINANCE": "ETHUSDT",
}
STAGE2_MARK_GAP_FILL_DATES: Final = {
    "BTCUSDT-PERP.BINANCE": (
        "2019-12-31",
        "2020-01-19",
        "2021-07-01",
        "2021-07-24",
        "2021-07-25",
        "2021-07-26",
        "2021-07-27",
        "2022-07-31",
        "2022-10-02",
        "2023-02-24",
        "2023-11-10",
        "2026-06-29",
    ),
    "ETHUSDT-PERP.BINANCE": (
        "2019-12-31",
        "2020-01-19",
        "2022-10-02",
        "2023-02-24",
        "2023-11-10",
        "2026-06-29",
    ),
}
STAGE2_INTERVAL_MS: Final = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}
STAGE2_INTERVAL_BAR_SPEC: Final = {
    "15m": "15-MINUTE",
    "1h": "1-HOUR",
    "4h": "4-HOUR",
}
_CANONICAL_KLINE_FIELDS: Final = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
_CANONICAL_FUNDING_FIELDS: Final = (
    "calc_time",
    "funding_interval_hours",
    "last_funding_rate",
)
_FUNDING_HEADER_ALIASES: Final = {
    "calc_time": "calc_time",
    "funding_interval_hours": "funding_interval_hours",
    "last_funding_rate": "last_funding_rate",
}
_HEADER_ALIASES: Final = {
    "open_time": "open_time",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "close_time": "close_time",
    "quote_volume": "quote_volume",
    "quote_asset_volume": "quote_volume",
    "count": "count",
    "number_of_trades": "count",
    "taker_buy_volume": "taker_buy_volume",
    "taker_buy_base_asset_volume": "taker_buy_volume",
    "taker_buy_quote_volume": "taker_buy_quote_volume",
    "taker_buy_quote_asset_volume": "taker_buy_quote_volume",
    "ignore": "ignore",
}
_ALLOWED_CONFIG_KEYS: Final = {
    "schema",
    "dataset_id",
    "nautilus_version",
    "environment",
    "source",
    "market",
    "archive_frequency",
    "window_start",
    "window_end",
    "instrument_ids",
    "bar_intervals",
    "bar_aggregation",
    "raw_root",
    "catalog_path",
}
_HEX_DIGITS: Final = "0123456789abcdefABCDEF"


class Stage2DataError(ValueError):
    """Raised when stage 2 source identity, config, or kline integrity is invalid."""


@dataclass(frozen=True)
class Stage2DatasetConfig:
    schema: str
    dataset_id: str
    nautilus_version: str
    environment: str
    source: str
    market: str
    archive_frequency: str
    window_start: datetime
    window_end: datetime
    instrument_ids: tuple[str, ...]
    bar_intervals: tuple[str, ...]
    bar_aggregation: str
    raw_root: Path
    catalog_path: Path


@dataclass(frozen=True)
class Stage2KlineRow:
    open_time: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    close_time: str
    quote_volume: str
    count: str
    taker_buy_volume: str
    taker_buy_quote_volume: str
    ignore: str


@dataclass(frozen=True)
class Stage2KlineArchive:
    instrument_id: str
    interval: str
    symbol: str
    year: int
    month: int
    zip_path: Path
    checksum_path: Path
    source_url: str
    checksum_url: str
    day: int | None = None


@dataclass(frozen=True)
class Stage2FundingRow:
    calc_time: str
    funding_interval_hours: str
    last_funding_rate: str


@dataclass(frozen=True)
class Stage2FundingArchive:
    instrument_id: str
    symbol: str
    year: int
    month: int
    zip_path: Path
    checksum_path: Path
    source_url: str
    checksum_url: str


@dataclass(frozen=True)
class Stage2SourceObject:
    path: str
    source_url: str
    sha256: str
    checksum_url: str
    source_kind: str
    instrument_id: str
    data_type: str
    start_ns: int
    end_ns: int
    rows: int

    def to_json_dict(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "source_url": self.source_url,
            "sha256": self.sha256,
            "checksum_url": self.checksum_url,
            "source_kind": self.source_kind,
            "instrument_id": self.instrument_id,
            "data_type": self.data_type,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "rows": self.rows,
        }


@dataclass(frozen=True)
class Stage2SourceManifest:
    schema: str
    dataset_id: str
    nautilus_version: str
    instrument_snapshot_checksum: str
    instrument_snapshot_fetched_at: str
    sources: tuple[Stage2SourceObject, ...]
    supplemental_sources: tuple[Stage2SourceObject, ...] = ()

    def instrument_snapshot_identity(self) -> dict[str, str]:
        return {
            "checksum_sha256": self.instrument_snapshot_checksum,
            "fetched_at": self.instrument_snapshot_fetched_at,
            "filename": STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
        }

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "dataset_id": self.dataset_id,
            "nautilus_version": self.nautilus_version,
            "instrument_snapshot": self.instrument_snapshot_identity(),
            "sources": [item.to_json_dict() for item in self.sources],
            "supplemental_sources": [
                item.to_json_dict() for item in self.supplemental_sources
            ],
        }


@dataclass(frozen=True)
class Stage2SeriesCoverage:
    instrument_id: str
    data_type: str
    bar_interval: str
    row_count: int
    first_ts_event: int
    last_ts_event: int
    duplicate_count: int
    out_of_order_count: int
    gap_count: int
    source_checksum: str
    gap_explanation: str = ""

    def to_json_dict(self) -> dict[str, str | int]:
        payload: dict[str, str | int] = {
            "instrument_id": self.instrument_id,
            "data_type": self.data_type,
            "bar_interval": self.bar_interval,
            "row_count": self.row_count,
            "first_ts_event": self.first_ts_event,
            "last_ts_event": self.last_ts_event,
            "duplicate_count": self.duplicate_count,
            "out_of_order_count": self.out_of_order_count,
            "gap_count": self.gap_count,
            "gap_explanation": self.gap_explanation,
            "source_checksum": self.source_checksum,
            "source_sha256": self.source_checksum,
        }
        if self.bar_interval:
            payload["bar_type"] = stage2_bar_type_str(
                self.instrument_id, self.bar_interval
            )
        return payload


@dataclass(frozen=True)
class Stage2CoverageReport:
    dataset_id: str
    series: tuple[Stage2SeriesCoverage, ...]

    def to_json_dict(self, catalog_path: Path | None = None) -> dict[str, object]:
        series = []
        for item in self.series:
            entry: dict[str, object] = dict(item.to_json_dict())
            entry["dataset_id"] = self.dataset_id
            if catalog_path is not None:
                entry["catalog_path"] = str(catalog_path)
            series.append(entry)
        payload: dict[str, object] = {
            "dataset_id": self.dataset_id,
            "series": series,
        }
        if catalog_path is not None:
            payload["catalog_path"] = str(catalog_path)
        return payload


@dataclass(frozen=True)
class Stage2SourceSpec:
    relative_path: str
    source_url: str
    checksum_url: str
    instrument_id: str
    data_type: str
    year: int
    month: int

    def to_json_dict(self) -> dict[str, str | int]:
        return {
            "relative_path": self.relative_path,
            "source_url": self.source_url,
            "checksum_url": self.checksum_url,
            "instrument_id": self.instrument_id,
            "data_type": self.data_type,
            "year": self.year,
            "month": self.month,
        }


def parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise Stage2DataError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def isoformat_utc(value: datetime) -> str:
    return require_utc(value).isoformat().replace("+00:00", "Z")


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise Stage2DataError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def unix_millis(value: datetime) -> int:
    utc = require_utc(value)
    seconds = int(
        (utc.replace(microsecond=0) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()
    )
    return seconds * 1000 + utc.microsecond // 1000


def millis_to_nanos(value: int) -> int:
    if value < 0:
        raise Stage2DataError("timestamp must be non-negative")
    return value * MS_NS


def datetime_to_nanos(value: datetime) -> int:
    return millis_to_nanos(unix_millis(require_utc(value)))


def stage2_window() -> tuple[datetime, datetime]:
    return parse_utc(STAGE2_WINDOW_START_ISO), parse_utc(STAGE2_WINDOW_END_ISO)


def stage2_bar_type_str(instrument_id: str, interval: str) -> str:
    try:
        spec = STAGE2_INTERVAL_BAR_SPEC[interval]
    except KeyError as exc:
        raise Stage2DataError("bar interval is not a stage 2 target") from exc
    if instrument_id not in STAGE2_INSTRUMENT_SYMBOLS:
        raise Stage2DataError("instrument is not a stage 2 target")
    return f"{instrument_id}-{spec}-{STAGE2_BAR_AGGREGATION}"


def load_stage2_config(path: Path, *, repository_root: Path) -> Stage2DatasetConfig:
    if not path.is_file():
        raise Stage2DataError("stage 2 config path must be an existing file")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise Stage2DataError("stage 2 config is not valid TOML") from exc
    unknown = set(payload) - _ALLOWED_CONFIG_KEYS
    if unknown:
        raise Stage2DataError("stage 2 config contains unknown fields")
    missing = _ALLOWED_CONFIG_KEYS - set(payload)
    if missing:
        raise Stage2DataError("stage 2 config is missing required fields")
    instrument_ids = _require_str_tuple(payload, "instrument_ids")
    bar_intervals = _require_str_tuple(payload, "bar_intervals")
    window_start = parse_utc(_require_str(payload, "window_start"))
    window_end = parse_utc(_require_str(payload, "window_end"))
    locked_start, locked_end = stage2_window()
    config = Stage2DatasetConfig(
        schema=_require_str(payload, "schema"),
        dataset_id=_require_str(payload, "dataset_id"),
        nautilus_version=_require_str(payload, "nautilus_version"),
        environment=_require_str(payload, "environment"),
        source=_require_str(payload, "source"),
        market=_require_str(payload, "market"),
        archive_frequency=_require_str(payload, "archive_frequency"),
        window_start=window_start,
        window_end=window_end,
        instrument_ids=instrument_ids,
        bar_intervals=bar_intervals,
        bar_aggregation=_require_str(payload, "bar_aggregation"),
        raw_root=_require_external_dir(
            _require_str(payload, "raw_root"),
            repository_root=repository_root,
            field="raw_root",
        ),
        catalog_path=_require_external_dir(
            _require_str(payload, "catalog_path"),
            repository_root=repository_root,
            field="catalog_path",
        ),
    )
    _require_locked_identity(config, locked_start=locked_start, locked_end=locked_end)
    if any(config.catalog_path.iterdir()):
        raise Stage2DataError("catalog target already exists and must not be mutated")
    return config


def discover_stage2_kline_archives(
    config: Stage2DatasetConfig,
    *,
    require_complete: bool = False,
) -> tuple[Stage2KlineArchive, ...]:
    archives: list[Stage2KlineArchive] = []
    for instrument_id in config.instrument_ids:
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        for interval in config.bar_intervals:
            found = 0
            for year, month in _month_keys(config.window_start, config.window_end):
                name = f"{symbol}-{interval}-{year:04d}-{month:02d}.zip"
                zip_path = (
                    config.raw_root / STAGE2_KLINE_ROOT / symbol / interval / name
                )
                if not zip_path.exists():
                    if require_complete:
                        raise Stage2DataError("stage 2 kline source archive is missing")
                    continue
                checksum_path = Path(f"{zip_path}.CHECKSUM")
                if not checksum_path.is_file():
                    raise Stage2DataError("official checksum file is missing")
                relative = f"{STAGE2_KLINE_ROOT}/{symbol}/{interval}/{name}"
                source_url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
                archives.append(
                    Stage2KlineArchive(
                        instrument_id=instrument_id,
                        interval=interval,
                        symbol=symbol,
                        year=year,
                        month=month,
                        zip_path=zip_path,
                        checksum_path=checksum_path,
                        source_url=source_url,
                        checksum_url=f"{source_url}.CHECKSUM",
                    )
                )
                found += 1
            if found == 0:
                raise Stage2DataError("stage 2 kline source archive is missing")
    return tuple(archives)


def discover_stage2_mark_archives(
    config: Stage2DatasetConfig,
    *,
    require_complete: bool = False,
) -> tuple[Stage2KlineArchive, ...]:
    archives: list[Stage2KlineArchive] = []
    for instrument_id in config.instrument_ids:
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        found = 0
        for year, month in _month_keys(config.window_start, config.window_end):
            name = f"{symbol}-{STAGE2_MARK_INTERVAL}-{year:04d}-{month:02d}.zip"
            zip_path = (
                config.raw_root
                / STAGE2_MARK_ROOT
                / symbol
                / STAGE2_MARK_INTERVAL
                / name
            )
            if not zip_path.exists():
                if require_complete:
                    raise Stage2DataError("stage 2 mark source archive is missing")
                continue
            checksum_path = Path(f"{zip_path}.CHECKSUM")
            if not checksum_path.is_file():
                raise Stage2DataError("official checksum file is missing")
            relative = f"{STAGE2_MARK_ROOT}/{symbol}/{STAGE2_MARK_INTERVAL}/{name}"
            source_url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
            archives.append(
                Stage2KlineArchive(
                    instrument_id=instrument_id,
                    interval=STAGE2_MARK_INTERVAL,
                    symbol=symbol,
                    year=year,
                    month=month,
                    zip_path=zip_path,
                    checksum_path=checksum_path,
                    source_url=source_url,
                    checksum_url=f"{source_url}.CHECKSUM",
                )
            )
            found += 1
        if found == 0:
            raise Stage2DataError("stage 2 mark source archive is missing")
    return tuple(archives)


def discover_stage2_mark_gap_fill_archives(
    config: Stage2DatasetConfig,
) -> tuple[Stage2KlineArchive, ...]:
    archives: list[Stage2KlineArchive] = []
    for instrument_id in config.instrument_ids:
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        for date_text in STAGE2_MARK_GAP_FILL_DATES[instrument_id]:
            day = datetime.fromisoformat(date_text).replace(tzinfo=UTC)
            name = f"{symbol}-{STAGE2_MARK_INTERVAL}-{date_text}.zip"
            zip_path = (
                config.raw_root
                / STAGE2_DAILY_MARK_ROOT
                / symbol
                / STAGE2_MARK_INTERVAL
                / name
            )
            if not zip_path.is_file():
                raise Stage2DataError("stage 2 daily mark gap-fill archive is missing")
            checksum_path = Path(f"{zip_path}.CHECKSUM")
            if not checksum_path.is_file():
                raise Stage2DataError("official daily mark checksum file is missing")
            relative = (
                f"{STAGE2_DAILY_MARK_ROOT}/{symbol}/{STAGE2_MARK_INTERVAL}/{name}"
            )
            source_url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
            archives.append(
                Stage2KlineArchive(
                    instrument_id=instrument_id,
                    interval=STAGE2_MARK_INTERVAL,
                    symbol=symbol,
                    year=day.year,
                    month=day.month,
                    day=day.day,
                    zip_path=zip_path,
                    checksum_path=checksum_path,
                    source_url=source_url,
                    checksum_url=f"{source_url}.CHECKSUM",
                )
            )
    if len(archives) != STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT:
        raise Stage2DataError("stage 2 daily mark gap-fill inventory is incomplete")
    return tuple(archives)


def discover_stage2_funding_archives(
    config: Stage2DatasetConfig,
    *,
    require_complete: bool = False,
) -> tuple[Stage2FundingArchive, ...]:
    archives: list[Stage2FundingArchive] = []
    for instrument_id in config.instrument_ids:
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        found = 0
        for year, month in _month_keys(config.window_start, config.window_end):
            name = f"{symbol}-fundingRate-{year:04d}-{month:02d}.zip"
            zip_path = config.raw_root / STAGE2_FUNDING_ROOT / symbol / name
            if not zip_path.exists():
                if require_complete:
                    raise Stage2DataError("stage 2 funding source archive is missing")
                continue
            checksum_path = Path(f"{zip_path}.CHECKSUM")
            if not checksum_path.is_file():
                raise Stage2DataError("official checksum file is missing")
            relative = f"{STAGE2_FUNDING_ROOT}/{symbol}/{name}"
            source_url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
            archives.append(
                Stage2FundingArchive(
                    instrument_id=instrument_id,
                    symbol=symbol,
                    year=year,
                    month=month,
                    zip_path=zip_path,
                    checksum_path=checksum_path,
                    source_url=source_url,
                    checksum_url=f"{source_url}.CHECKSUM",
                )
            )
            found += 1
        if found == 0:
            raise Stage2DataError("stage 2 funding source archive is missing")
    return tuple(archives)


def read_verified_kline_archive(
    archive: Stage2KlineArchive,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[tuple[Stage2KlineRow, ...], str]:
    digest = verify_zip_checksum(archive.zip_path, archive.checksum_path)
    archive_date = f"{archive.year:04d}-{archive.month:02d}"
    if archive.day is not None:
        archive_date = f"{archive_date}-{archive.day:02d}"
    csv_name = f"{archive.symbol}-{archive.interval}-{archive_date}.csv"
    try:
        with zipfile.ZipFile(archive.zip_path) as bundle:
            names = bundle.namelist()
            if names != [csv_name]:
                raise Stage2DataError(
                    "kline zip contents do not match the official CSV"
                )
            payload = bundle.read(csv_name)
    except zipfile.BadZipFile as exc:
        raise Stage2DataError("kline zip is invalid") from exc
    rows = parse_kline_csv(payload)
    if not rows:
        raise Stage2DataError("kline csv has no records")
    if archive.day is not None:
        month_start = datetime(archive.year, archive.month, archive.day, tzinfo=UTC)
        month_end = month_start + timedelta(days=1)
    else:
        month_start = datetime(archive.year, archive.month, 1, tzinfo=UTC)
        if archive.month == 12:
            month_end = datetime(archive.year + 1, 1, 1, tzinfo=UTC)
        else:
            month_end = datetime(archive.year, archive.month + 1, 1, tzinfo=UTC)
    month_start_ms = unix_millis(month_start)
    month_end_ms = unix_millis(month_end)
    window_start_ms = unix_millis(window_start)
    window_end_ms = unix_millis(window_end)
    interval_ms = STAGE2_INTERVAL_MS[archive.interval]
    for row in rows:
        _validate_kline_row_values(
            row,
            interval_ms=interval_ms,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        open_time = _require_int_string(row.open_time, field="open_time")
        if open_time < month_start_ms or open_time >= month_end_ms:
            raise Stage2DataError("kline record is outside the archive month")
    return rows, digest


def read_verified_funding_archive(
    archive: Stage2FundingArchive,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[tuple[Stage2FundingRow, ...], str]:
    digest = verify_zip_checksum(archive.zip_path, archive.checksum_path)
    csv_name = (
        f"{archive.symbol}-fundingRate-{archive.year:04d}-{archive.month:02d}.csv"
    )
    try:
        with zipfile.ZipFile(archive.zip_path) as bundle:
            names = bundle.namelist()
            if names != [csv_name]:
                raise Stage2DataError(
                    "funding zip contents do not match the official CSV"
                )
            payload = bundle.read(csv_name)
    except zipfile.BadZipFile as exc:
        raise Stage2DataError("funding zip is invalid") from exc
    rows = parse_funding_csv(payload)
    if not rows:
        raise Stage2DataError("funding csv has no records")
    month_start = datetime(archive.year, archive.month, 1, tzinfo=UTC)
    if archive.month == 12:
        month_end = datetime(archive.year + 1, 1, 1, tzinfo=UTC)
    else:
        month_end = datetime(archive.year, archive.month + 1, 1, tzinfo=UTC)
    month_start_ms = unix_millis(month_start)
    month_end_ms = unix_millis(month_end)
    window_start_ms = unix_millis(window_start)
    window_end_ms = unix_millis(window_end)
    for row in rows:
        _validate_funding_row_values(
            row,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        calc_time = _require_int_string(row.calc_time, field="calc_time")
        if calc_time < month_start_ms or calc_time >= month_end_ms:
            raise Stage2DataError("funding record is outside the archive month")
    return rows, digest


def parse_funding_csv(payload: bytes) -> tuple[Stage2FundingRow, ...]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Stage2DataError("funding csv is not valid UTF-8") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        first = next(reader)
    except StopIteration as exc:
        raise Stage2DataError("funding csv is empty") from exc
    if not first:
        raise Stage2DataError("funding csv has an empty record")
    if _is_int_string(first[0].strip()):
        rows = [first, *reader]
        field_order = list(_CANONICAL_FUNDING_FIELDS)
    else:
        field_order = _map_funding_header(first)
        rows = list(reader)
    parsed: list[Stage2FundingRow] = []
    for raw in rows:
        if len(raw) != 3:
            raise Stage2DataError("funding csv column count is not the fixed schema")
        values = {field: raw[index].strip() for index, field in enumerate(field_order)}
        parsed.append(
            Stage2FundingRow(
                calc_time=values["calc_time"],
                funding_interval_hours=values["funding_interval_hours"],
                last_funding_rate=values["last_funding_rate"],
            )
        )
    return tuple(parsed)


def validate_funding_row(
    row: Stage2FundingRow,
    *,
    window_start: datetime,
    window_end: datetime,
) -> None:
    _validate_funding_row_values(
        row,
        window_start_ms=unix_millis(window_start),
        window_end_ms=unix_millis(window_end),
    )


def validate_funding_series(
    rows: Sequence[Stage2FundingRow],
    *,
    instrument_id: str,
    source_checksum: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> Stage2SeriesCoverage:
    if instrument_id not in STAGE2_INSTRUMENT_SYMBOLS:
        raise Stage2DataError("instrument is not a stage 2 target")
    if not rows:
        raise Stage2DataError("funding series is empty")
    seen: set[int] = set()
    duplicate_count = 0
    out_of_order_count = 0
    gap_count = 0
    previous: int | None = None
    previous_interval_ms = 0
    first_ts = 0
    last_ts = 0
    for index, row in enumerate(rows):
        calc_time = _require_int_string(row.calc_time, field="calc_time")
        interval_ms = funding_interval_minutes(row) * 60 * 1000
        ts_event = millis_to_nanos(calc_time)
        if index == 0:
            first_ts = ts_event
        last_ts = ts_event
        if calc_time in seen:
            duplicate_count += 1
        seen.add(calc_time)
        if previous is not None:
            if calc_time < previous:
                out_of_order_count += 1
            elif (
                calc_time - previous
                > previous_interval_ms + STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS
            ):
                gap_count += 1
        previous = calc_time
        previous_interval_ms = interval_ms
    if duplicate_count:
        raise Stage2DataError("funding series contains duplicate event times")
    if out_of_order_count:
        raise Stage2DataError("funding series is out of order")
    if gap_count:
        raise Stage2DataError("funding series contains a gap")
    if (window_start is None) != (window_end is None):
        raise Stage2DataError("funding coverage window is incomplete")
    if window_start is not None and window_end is not None:
        expected_start = unix_millis(window_start)
        expected_end = unix_millis(window_end)
        first_time = _require_int_string(rows[0].calc_time, field="calc_time")
        last_time = _require_int_string(rows[-1].calc_time, field="calc_time")
        last_interval_ms = funding_interval_minutes(rows[-1]) * 60 * 1000
        if (
            abs(first_time - expected_start) > STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS
            or abs(last_time + last_interval_ms - expected_end)
            > STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS
        ):
            raise Stage2DataError("funding series does not cover the declared window")
    return Stage2SeriesCoverage(
        instrument_id=instrument_id,
        data_type=STAGE2_DATA_TYPE_FUNDING,
        bar_interval="",
        row_count=len(rows),
        first_ts_event=first_ts,
        last_ts_event=last_ts,
        duplicate_count=duplicate_count,
        out_of_order_count=out_of_order_count,
        gap_count=gap_count,
        source_checksum=source_checksum,
        gap_explanation="",
    )


def validate_mark_series(
    rows: Sequence[Stage2KlineRow],
    *,
    instrument_id: str,
    source_checksum: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> Stage2SeriesCoverage:
    coverage = validate_kline_series(
        rows,
        instrument_id=instrument_id,
        interval=STAGE2_MARK_INTERVAL,
        source_checksum=source_checksum,
        window_start=window_start,
        window_end=window_end,
        _allowed_gaps=STAGE2_MARK_ALLOWED_GAPS,
    )
    return Stage2SeriesCoverage(
        instrument_id=coverage.instrument_id,
        data_type=STAGE2_DATA_TYPE_MARK,
        bar_interval=STAGE2_MARK_INTERVAL,
        row_count=coverage.row_count,
        first_ts_event=coverage.first_ts_event,
        last_ts_event=coverage.last_ts_event,
        duplicate_count=coverage.duplicate_count,
        out_of_order_count=coverage.out_of_order_count,
        gap_count=coverage.gap_count,
        source_checksum=coverage.source_checksum,
        gap_explanation=(STAGE2_MARK_GAP_EXPLANATION if coverage.gap_count else ""),
    )


def require_recent_mark_for_funding(
    marks: Sequence[Stage2KlineRow],
    fundings: Sequence[Stage2FundingRow],
) -> None:
    mark_times = tuple(
        millis_to_nanos(_require_int_string(row.close_time, field="close_time"))
        for row in marks
    )
    max_age_ns = STAGE2_MARK_MAX_AGE_MS * MS_NS
    for funding in fundings:
        funding_ts = millis_to_nanos(
            _require_int_string(funding.calc_time, field="calc_time")
        )
        prior = tuple(ts for ts in mark_times if ts <= funding_ts)
        if not prior:
            raise Stage2DataError("funding event is missing a recent mark")
        age = funding_ts - prior[-1]
        if age > max_age_ns:
            raise Stage2DataError("funding event is missing a recent mark")


def verify_zip_checksum(zip_path: Path, checksum_path: Path) -> str:
    expected = _parse_checksum_file(checksum_path, zip_path.name)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest.lower() != expected.lower():
        raise Stage2DataError("checksum does not match the official source record")
    return digest.lower()


def parse_kline_csv(payload: bytes) -> tuple[Stage2KlineRow, ...]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Stage2DataError("kline csv is not valid UTF-8") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        first = next(reader)
    except StopIteration as exc:
        raise Stage2DataError("kline csv is empty") from exc
    if not first:
        raise Stage2DataError("kline csv has an empty record")
    if _is_int_string(first[0].strip()):
        rows = [first, *reader]
        field_order = list(_CANONICAL_KLINE_FIELDS)
    else:
        field_order = _map_header(first)
        rows = list(reader)
    parsed: list[Stage2KlineRow] = []
    for raw in rows:
        if len(raw) != 12:
            raise Stage2DataError("kline csv column count is not the fixed schema")
        values = {field: raw[index].strip() for index, field in enumerate(field_order)}
        parsed.append(
            Stage2KlineRow(
                open_time=values["open_time"],
                open=values["open"],
                high=values["high"],
                low=values["low"],
                close=values["close"],
                volume=values["volume"],
                close_time=values["close_time"],
                quote_volume=values["quote_volume"],
                count=values["count"],
                taker_buy_volume=values["taker_buy_volume"],
                taker_buy_quote_volume=values["taker_buy_quote_volume"],
                ignore=values["ignore"],
            )
        )
    return tuple(parsed)


def validate_kline_row(
    row: Stage2KlineRow,
    *,
    interval: str,
    window_start: datetime,
    window_end: datetime,
) -> None:
    if interval not in STAGE2_INTERVAL_MS:
        raise Stage2DataError("bar interval is not a stage 2 target")
    _validate_kline_row_values(
        row,
        interval_ms=STAGE2_INTERVAL_MS[interval],
        window_start_ms=unix_millis(window_start),
        window_end_ms=unix_millis(window_end),
    )


def validate_kline_series(
    rows: Sequence[Stage2KlineRow],
    *,
    instrument_id: str,
    interval: str,
    source_checksum: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    _allowed_gaps: tuple[tuple[int, int], ...] = (),
) -> Stage2SeriesCoverage:
    if instrument_id not in STAGE2_INSTRUMENT_SYMBOLS:
        raise Stage2DataError("instrument is not a stage 2 target")
    if interval not in STAGE2_INTERVAL_MS:
        raise Stage2DataError("bar interval is not a stage 2 target")
    if not rows:
        raise Stage2DataError("kline series is empty")
    interval_ms = STAGE2_INTERVAL_MS[interval]
    seen: set[int] = set()
    duplicate_count = 0
    out_of_order_count = 0
    gap_count = 0
    unexplained_gap_count = 0
    previous_open: int | None = None
    first_ts = 0
    last_ts = 0
    for index, row in enumerate(rows):
        open_time = _require_int_string(row.open_time, field="open_time")
        close_time = _require_int_string(row.close_time, field="close_time")
        ts_event = millis_to_nanos(close_time)
        if index == 0:
            first_ts = ts_event
        last_ts = ts_event
        if open_time in seen:
            duplicate_count += 1
        seen.add(open_time)
        if previous_open is not None:
            if open_time < previous_open:
                out_of_order_count += 1
            elif open_time - previous_open != interval_ms:
                gap_count += 1
                if (previous_open, open_time) not in _allowed_gaps:
                    unexplained_gap_count += 1
        previous_open = open_time
    if duplicate_count:
        raise Stage2DataError("kline series contains duplicate event times")
    if out_of_order_count:
        raise Stage2DataError("kline series is out of order")
    if unexplained_gap_count:
        raise Stage2DataError("kline series contains a gap")
    if (window_start is None) != (window_end is None):
        raise Stage2DataError("kline coverage window is incomplete")
    if window_start is not None and window_end is not None:
        expected_start = unix_millis(window_start)
        expected_end = unix_millis(window_end)
        first_open = _require_int_string(rows[0].open_time, field="open_time")
        last_open = _require_int_string(rows[-1].open_time, field="open_time")
        if first_open != expected_start or last_open + interval_ms != expected_end:
            raise Stage2DataError("kline series does not cover the declared window")
    return Stage2SeriesCoverage(
        instrument_id=instrument_id,
        data_type=STAGE2_DATA_TYPE_BARS,
        bar_interval=interval,
        row_count=len(rows),
        first_ts_event=first_ts,
        last_ts_event=last_ts,
        duplicate_count=duplicate_count,
        out_of_order_count=out_of_order_count,
        gap_count=gap_count,
        source_checksum=source_checksum,
        gap_explanation=(STAGE2_MARK_GAP_EXPLANATION if gap_count else ""),
    )


def build_source_object(
    archive: Stage2KlineArchive,
    rows: tuple[Stage2KlineRow, ...],
    sha256: str,
    *,
    data_type: str = STAGE2_DATA_TYPE_BARS,
) -> Stage2SourceObject:
    first = millis_to_nanos(_require_int_string(rows[0].close_time, field="close_time"))
    last = millis_to_nanos(_require_int_string(rows[-1].close_time, field="close_time"))
    return Stage2SourceObject(
        path=str(archive.zip_path),
        source_url=archive.source_url,
        sha256=sha256,
        checksum_url=archive.checksum_url,
        source_kind=STAGE2_SOURCE_KIND,
        instrument_id=archive.instrument_id,
        data_type=data_type,
        start_ns=first,
        end_ns=last,
        rows=len(rows),
    )


def build_funding_source_object(
    archive: Stage2FundingArchive,
    rows: tuple[Stage2FundingRow, ...],
    sha256: str,
) -> Stage2SourceObject:
    first = millis_to_nanos(_require_int_string(rows[0].calc_time, field="calc_time"))
    last = millis_to_nanos(_require_int_string(rows[-1].calc_time, field="calc_time"))
    return Stage2SourceObject(
        path=str(archive.zip_path),
        source_url=archive.source_url,
        sha256=sha256,
        checksum_url=archive.checksum_url,
        source_kind=STAGE2_SOURCE_KIND,
        instrument_id=archive.instrument_id,
        data_type=STAGE2_DATA_TYPE_FUNDING,
        start_ns=first,
        end_ns=last,
        rows=len(rows),
    )


def require_stage2_manifest_identity(catalog_path: Path) -> None:
    if not catalog_path.is_absolute():
        raise Stage2DataError("catalog_path must be an absolute path")
    if not catalog_path.is_dir():
        raise Stage2DataError("catalog_path must be an existing directory")
    manifest_path = catalog_path / STAGE2_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise Stage2DataError("catalog identity does not match")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Stage2DataError("catalog identity does not match") from exc
    if not isinstance(payload, dict):
        raise Stage2DataError("catalog identity does not match")
    if payload.get("schema") != STAGE2_SOURCE_SCHEMA:
        raise Stage2DataError("catalog identity does not match")
    if payload.get("dataset_id") != STAGE2_DATASET_ID:
        raise Stage2DataError("catalog identity does not match")
    if payload.get("nautilus_version") != STAGE2_NAUTILUS_VERSION:
        raise Stage2DataError("catalog identity does not match")
    snapshot_identity = payload.get("instrument_snapshot")
    if not isinstance(snapshot_identity, Mapping):
        raise Stage2DataError("catalog instrument snapshot identity is missing")
    if snapshot_identity.get("filename") != STAGE2_INSTRUMENT_SNAPSHOT_FILENAME:
        raise Stage2DataError("catalog instrument snapshot identity does not match")
    snapshot_checksum = snapshot_identity.get("checksum_sha256")
    snapshot_fetched_at = snapshot_identity.get("fetched_at")
    if (
        not isinstance(snapshot_checksum, str)
        or not _is_sha256_string(snapshot_checksum)
        or not isinstance(snapshot_fetched_at, str)
    ):
        raise Stage2DataError("catalog instrument snapshot identity does not match")
    parse_utc(snapshot_fetched_at)
    snapshot_path = catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME
    if not snapshot_path.is_file():
        raise Stage2DataError("catalog instrument snapshot is missing")
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Stage2DataError("catalog instrument snapshot is invalid") from exc
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("checksum_sha256") != snapshot_checksum
        or snapshot.get("fetched_at") != snapshot_fetched_at
    ):
        raise Stage2DataError("catalog instrument snapshot identity does not match")


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def combined_source_checksum(digests: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for item in digests:
        digest.update(f"{item}\n".encode())
    return digest.hexdigest()


def stage2_month_keys() -> tuple[tuple[int, int], ...]:
    start, end = stage2_window()
    return _month_keys(start, end)


def expected_stage2_source_specs() -> tuple[Stage2SourceSpec, ...]:
    specs: list[Stage2SourceSpec] = []
    months = stage2_month_keys()
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        for interval in STAGE2_BAR_INTERVALS:
            for year, month in months:
                specs.append(
                    _source_spec(
                        STAGE2_KLINE_ROOT,
                        symbol,
                        interval,
                        year,
                        month,
                        instrument_id=instrument_id,
                        data_type=STAGE2_DATA_TYPE_BARS,
                    )
                )
        for year, month in months:
            specs.append(
                _source_spec(
                    STAGE2_MARK_ROOT,
                    symbol,
                    STAGE2_MARK_INTERVAL,
                    year,
                    month,
                    instrument_id=instrument_id,
                    data_type=STAGE2_DATA_TYPE_MARK,
                )
            )
        for year, month in months:
            name = f"{symbol}-fundingRate-{year:04d}-{month:02d}.zip"
            relative = f"{STAGE2_FUNDING_ROOT}/{symbol}/{name}"
            source_url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
            specs.append(
                Stage2SourceSpec(
                    relative_path=relative,
                    source_url=source_url,
                    checksum_url=f"{source_url}.CHECKSUM",
                    instrument_id=instrument_id,
                    data_type=STAGE2_DATA_TYPE_FUNDING,
                    year=year,
                    month=month,
                )
            )
    if len(specs) != STAGE2_EXPECTED_SOURCE_COUNT:
        raise Stage2DataError("expected source inventory is not 800 objects")
    return tuple(specs)


def expected_source_inventory_digest() -> str:
    payload = [item.to_json_dict() for item in expected_stage2_source_specs()]
    return _canonical_digest(payload)


def market_data_manifest_digest(manifest: Stage2SourceManifest) -> str:
    def _identity(item: Stage2SourceObject) -> dict[str, str | int]:
        return {
            "checksum_url": item.checksum_url,
            "data_type": item.data_type,
            "end_ns": item.end_ns,
            "instrument_id": item.instrument_id,
            "rows": item.rows,
            "sha256": item.sha256,
            "source_url": item.source_url,
            "start_ns": item.start_ns,
        }

    payload = {
        "sources": [_identity(item) for item in manifest.sources],
        "supplemental_sources": [
            _identity(item) for item in manifest.supplemental_sources
        ],
    }
    return _canonical_digest(payload)


def source_manifest_digest(manifest: Stage2SourceManifest) -> str:
    return _canonical_digest(
        {
            "instrument_snapshot": manifest.instrument_snapshot_identity(),
            "market_data_manifest_digest": market_data_manifest_digest(manifest),
        }
    )


def require_complete_source_inventory(source_urls: Sequence[str]) -> None:
    expected = tuple(spec.source_url for spec in expected_stage2_source_specs())
    if len(source_urls) != len(set(source_urls)):
        raise Stage2DataError("source manifest contains duplicate objects")
    if tuple(sorted(source_urls)) != tuple(sorted(expected)):
        raise Stage2DataError(
            "source manifest is missing months, has duplicate objects, or drifted"
        )


def require_tail_disabled() -> None:
    if STAGE2_NAUTILUS_TAIL_ENABLED:
        raise Stage2DataError("nautilus tail must stay disabled")


def probe_index_checksum_url(url: str) -> bool:
    if not url.startswith(f"{STAGE2_PUBLIC_DATA_ORIGIN}/") or not url.endswith(
        ".CHECKSUM"
    ):
        return False
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "tracequant-stage2-index/1"},
    )
    for _attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if not 200 <= int(getattr(response, "status", 0)) < 300:
                    continue
                fields = response.read(256).decode("utf-8").split()
                return (
                    len(fields) == 2
                    and _is_sha256_string(fields[0])
                    and fields[1].endswith(".zip")
                )
        except (
            UnicodeDecodeError,
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            OSError,
        ):
            continue
    return False


def stage2_index_coverage_conclusion(
    *,
    checksum_probe: Callable[[str], bool] | None = None,
) -> dict[str, object]:
    if STAGE2_INCLUDE_INDEX_PRICE:
        raise Stage2DataError("index price must not be included in the first catalog")
    months = stage2_month_keys()
    continuous_months = True
    try:
        _require_continuous_months(months)
    except Stage2DataError:
        continuous_months = False
    probe = checksum_probe if checksum_probe is not None else probe_index_checksum_url
    objects: list[dict[str, str | int]] = []
    checksum_available = True
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        for year, month in months:
            name = f"{symbol}-{STAGE2_INDEX_INTERVAL}-{year:04d}-{month:02d}.zip"
            relative = f"{STAGE2_INDEX_ROOT}/{symbol}/{STAGE2_INDEX_INTERVAL}/{name}"
            source_url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
            checksum_url = f"{source_url}.CHECKSUM"
            if not probe(checksum_url):
                checksum_available = False
            objects.append(
                {
                    "checksum_url": checksum_url,
                    "instrument_id": instrument_id,
                    "month": month,
                    "source_url": source_url,
                    "year": year,
                }
            )
    expected = len(STAGE2_INSTRUMENT_IDS) * len(months)
    if len(objects) != expected:
        continuous_months = False
    return {
        "checksum_available": checksum_available,
        "continuous_months": continuous_months,
        "downloaded": False,
        "include_index_price": False,
        "interval": STAGE2_INDEX_INTERVAL,
        "month_count": len(months),
        "object_count": len(objects),
        "objects": objects,
        "written_to_catalog": False,
    }


def coverage_summary(
    report: Stage2CoverageReport,
) -> tuple[dict[str, str | int], ...]:
    for item in report.series:
        if item.duplicate_count or item.out_of_order_count:
            raise Stage2DataError(
                "coverage report contains duplicate or out-of-order rows"
            )
        if item.gap_count and not (
            item.data_type == STAGE2_DATA_TYPE_MARK
            and item.gap_count == len(STAGE2_MARK_ALLOWED_GAPS)
            and item.gap_explanation == STAGE2_MARK_GAP_EXPLANATION
        ):
            raise Stage2DataError("coverage report contains an unexplained gap")
    summary: list[dict[str, str | int]] = []
    for item in report.series:
        entry: dict[str, str | int] = {
            "data_type": item.data_type,
            "dataset_id": report.dataset_id,
            "duplicate_count": item.duplicate_count,
            "first_ts_event": item.first_ts_event,
            "gap_count": item.gap_count,
            "gap_explanation": item.gap_explanation,
            "instrument_id": item.instrument_id,
            "last_ts_event": item.last_ts_event,
            "out_of_order_count": item.out_of_order_count,
            "row_count": item.row_count,
            "source_sha256": item.source_checksum,
        }
        if item.bar_interval:
            entry["bar_type"] = stage2_bar_type_str(
                item.instrument_id, item.bar_interval
            )
        summary.append(entry)
    return tuple(summary)


def dataset_digest_payload(
    *,
    manifest: Stage2SourceManifest,
    coverage: Stage2CoverageReport,
    runtime_identity: str,
) -> dict[str, object]:
    return {
        "coverage": coverage_summary(coverage),
        "dataset_id": manifest.dataset_id,
        "instrument_snapshot": manifest.instrument_snapshot_identity(),
        "market_data_manifest_digest": market_data_manifest_digest(manifest),
        "nautilus_version": manifest.nautilus_version,
        "runtime_identity": runtime_identity,
        "source_manifest_digest": source_manifest_digest(manifest),
    }


def dataset_digest(
    *,
    manifest: Stage2SourceManifest,
    coverage: Stage2CoverageReport,
    runtime_identity: str,
) -> str:
    return _canonical_digest(
        dataset_digest_payload(
            manifest=manifest,
            coverage=coverage,
            runtime_identity=runtime_identity,
        )
    )


def acceptance_record_digest(record: Mapping[str, object]) -> str:
    return _canonical_digest(
        {key: value for key, value in record.items() if key != "acceptance_digest"}
    )


def build_stage2_acceptance_record(
    *,
    manifest: Stage2SourceManifest,
    coverage: Stage2CoverageReport,
    runtime_identity: str,
    crosscheck: Mapping[str, object],
    homology: Mapping[str, object],
    sma_parity: Mapping[str, object],
    checksum_probe: Callable[[str], bool] | None = None,
    index_coverage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    require_tail_disabled()
    resolved_index_coverage = (
        dict(index_coverage)
        if index_coverage is not None
        else stage2_index_coverage_conclusion(checksum_probe=checksum_probe)
    )
    if resolved_index_coverage.get("checksum_available") is not True:
        raise Stage2DataError("index checksum availability is incomplete")
    if resolved_index_coverage.get("continuous_months") is not True:
        raise Stage2DataError("index month objects are not continuous")
    if len(manifest.sources) != STAGE2_EXPECTED_SOURCE_COUNT:
        raise Stage2DataError("source manifest does not contain 800 objects")
    require_complete_source_inventory([item.source_url for item in manifest.sources])
    record: dict[str, object] = {
        "catalog_evidence": {
            "coverage_filename": STAGE2_COVERAGE_FILENAME,
            "dataset_digest_filename": STAGE2_DIGEST_FILENAME,
            "instrument_snapshot_filename": STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
            "source_manifest_filename": STAGE2_MANIFEST_FILENAME,
            "source_object_count": len(manifest.sources),
            "supplemental_source_count": len(manifest.supplemental_sources),
        },
        "coverage_summary": list(coverage_summary(coverage)),
        "crosscheck": dict(crosscheck),
        "dataset_digest": dataset_digest(
            manifest=manifest,
            coverage=coverage,
            runtime_identity=runtime_identity,
        ),
        "dataset_id": STAGE2_DATASET_ID,
        "expected_source_count": STAGE2_EXPECTED_SOURCE_COUNT,
        "expected_source_inventory_digest": expected_source_inventory_digest(),
        "generation_command": STAGE2_GENERATION_COMMAND,
        "homology": dict(homology),
        "instrument_snapshot": manifest.instrument_snapshot_identity(),
        "market_data_manifest_digest": market_data_manifest_digest(manifest),
        "include_index_price": STAGE2_INCLUDE_INDEX_PRICE,
        "index_coverage": resolved_index_coverage,
        "nautilus_tail_enabled": STAGE2_NAUTILUS_TAIL_ENABLED,
        "nautilus_version": STAGE2_NAUTILUS_VERSION,
        "runtime_identity": runtime_identity,
        "schema": STAGE2_ACCEPTANCE_SCHEMA,
        "sma_parity": dict(sma_parity),
        "source_manifest_digest": source_manifest_digest(manifest),
        "splits": {
            "test": ["2025-01-01T00:00:00Z", STAGE2_WINDOW_END_ISO],
            "train": [STAGE2_WINDOW_START_ISO, "2024-01-01T00:00:00Z"],
            "validation": ["2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"],
        },
        "verification_test": (
            "tests/acceptance/test_stage2_dataset.py::"
            "test_frozen_stage2_dataset_drives_research_and_backtest_from_one_catalog"
        ),
    }
    record["acceptance_digest"] = acceptance_record_digest(record)
    require_complete_acceptance_record(record)
    require_no_local_absolute_paths(record)
    return record


def require_complete_acceptance_record(
    record: Mapping[str, object],
    *,
    require_gap_fill_sources: bool = False,
    require_live_crosscheck: bool = False,
) -> None:
    expected_keys = {
        "acceptance_digest",
        "catalog_evidence",
        "coverage_summary",
        "crosscheck",
        "dataset_digest",
        "dataset_id",
        "expected_source_count",
        "expected_source_inventory_digest",
        "generation_command",
        "homology",
        "include_index_price",
        "index_coverage",
        "instrument_snapshot",
        "market_data_manifest_digest",
        "nautilus_tail_enabled",
        "nautilus_version",
        "runtime_identity",
        "schema",
        "sma_parity",
        "source_manifest_digest",
        "splits",
        "verification_test",
    }
    if set(record) != expected_keys:
        raise Stage2DataError("acceptance record fields do not match the schema")
    if record.get("schema") != STAGE2_ACCEPTANCE_SCHEMA:
        raise Stage2DataError("acceptance record schema does not match")
    if record.get("dataset_id") != STAGE2_DATASET_ID:
        raise Stage2DataError("acceptance record dataset identity does not match")
    if record.get("expected_source_count") != STAGE2_EXPECTED_SOURCE_COUNT:
        raise Stage2DataError("acceptance record source count does not match")
    if record.get("expected_source_inventory_digest") != (
        expected_source_inventory_digest()
    ):
        raise Stage2DataError("acceptance record source inventory does not match")
    if record.get("generation_command") != STAGE2_GENERATION_COMMAND:
        raise Stage2DataError("acceptance record generation command does not match")
    if record.get("nautilus_version") != STAGE2_NAUTILUS_VERSION:
        raise Stage2DataError("acceptance record Nautilus identity does not match")
    if record.get("verification_test") != (
        "tests/acceptance/test_stage2_dataset.py::"
        "test_frozen_stage2_dataset_drives_research_and_backtest_from_one_catalog"
    ):
        raise Stage2DataError("acceptance record verification test does not match")
    if record.get("include_index_price") is not False:
        raise Stage2DataError("acceptance record index policy does not match")
    if record.get("nautilus_tail_enabled") is not False:
        raise Stage2DataError("acceptance record tail policy does not match")
    expected_splits = {
        "test": ["2025-01-01T00:00:00Z", STAGE2_WINDOW_END_ISO],
        "train": [STAGE2_WINDOW_START_ISO, "2024-01-01T00:00:00Z"],
        "validation": ["2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"],
    }
    if record.get("splits") != expected_splits:
        raise Stage2DataError("acceptance record splits do not match")
    index_coverage = record.get("index_coverage")
    expected_index_coverage = stage2_index_coverage_conclusion(
        checksum_probe=lambda _url: True
    )
    if index_coverage != expected_index_coverage:
        raise Stage2DataError("acceptance record index coverage does not match")
    evidence = record.get("catalog_evidence")
    if not isinstance(evidence, Mapping):
        raise Stage2DataError("acceptance record catalog evidence is missing")
    if set(evidence) != {
        "coverage_filename",
        "dataset_digest_filename",
        "instrument_snapshot_filename",
        "source_manifest_filename",
        "source_object_count",
        "supplemental_source_count",
    }:
        raise Stage2DataError(
            "acceptance record catalog evidence schema does not match"
        )
    if evidence.get("source_manifest_filename") != STAGE2_MANIFEST_FILENAME:
        raise Stage2DataError("acceptance record source manifest locator is missing")
    if evidence.get("coverage_filename") != STAGE2_COVERAGE_FILENAME:
        raise Stage2DataError("acceptance record coverage locator is missing")
    if evidence.get("dataset_digest_filename") != STAGE2_DIGEST_FILENAME:
        raise Stage2DataError("acceptance record dataset digest locator is missing")
    if evidence.get("instrument_snapshot_filename") != (
        STAGE2_INSTRUMENT_SNAPSHOT_FILENAME
    ):
        raise Stage2DataError(
            "acceptance record instrument snapshot locator is missing"
        )
    if evidence.get("source_object_count") != STAGE2_EXPECTED_SOURCE_COUNT:
        raise Stage2DataError("acceptance record catalog evidence is incomplete")
    supplemental_source_count = evidence.get("supplemental_source_count")
    if not isinstance(supplemental_source_count, int):
        raise Stage2DataError("acceptance record supplemental evidence is missing")
    if require_gap_fill_sources and supplemental_source_count != (
        STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT
    ):
        raise Stage2DataError("tracked acceptance gap-fill evidence is incomplete")
    for key in (
        "dataset_digest",
        "market_data_manifest_digest",
        "source_manifest_digest",
    ):
        digest = record.get(key)
        if not isinstance(digest, str) or not _is_sha256_string(digest):
            raise Stage2DataError(f"acceptance record {key} is not a SHA-256 digest")
        if digest == "0" * 64:
            raise Stage2DataError(f"acceptance record {key} is a placeholder")

    instrument_snapshot = record.get("instrument_snapshot")
    if not isinstance(instrument_snapshot, Mapping):
        raise Stage2DataError("acceptance record instrument snapshot is missing")
    if set(instrument_snapshot) != {"checksum_sha256", "fetched_at", "filename"}:
        raise Stage2DataError(
            "acceptance record instrument snapshot schema does not match"
        )
    if instrument_snapshot.get("filename") != STAGE2_INSTRUMENT_SNAPSHOT_FILENAME:
        raise Stage2DataError(
            "acceptance record instrument snapshot locator is missing"
        )
    snapshot_checksum = instrument_snapshot.get("checksum_sha256")
    if (
        not isinstance(snapshot_checksum, str)
        or not _is_sha256_string(snapshot_checksum)
        or snapshot_checksum == "0" * 64
    ):
        raise Stage2DataError(
            "acceptance record instrument snapshot is not source-bound"
        )
    snapshot_fetched_at = instrument_snapshot.get("fetched_at")
    if not isinstance(snapshot_fetched_at, str):
        raise Stage2DataError("acceptance record instrument fetch time is missing")
    parse_utc(snapshot_fetched_at)
    normalized_snapshot = dict(instrument_snapshot)
    market_data_digest = record.get("market_data_manifest_digest")
    expected_source_manifest_digest = _canonical_digest(
        {
            "instrument_snapshot": normalized_snapshot,
            "market_data_manifest_digest": market_data_digest,
        }
    )
    if record.get("source_manifest_digest") != expected_source_manifest_digest:
        raise Stage2DataError(
            "acceptance record source manifest identity does not match"
        )
    expected_dataset_digest = _canonical_digest(
        {
            "coverage": record.get("coverage_summary"),
            "dataset_id": record.get("dataset_id"),
            "instrument_snapshot": normalized_snapshot,
            "market_data_manifest_digest": market_data_digest,
            "nautilus_version": record.get("nautilus_version"),
            "runtime_identity": record.get("runtime_identity"),
            "source_manifest_digest": expected_source_manifest_digest,
        }
    )
    if record.get("dataset_digest") != expected_dataset_digest:
        raise Stage2DataError("acceptance record dataset identity does not match")

    sma_parity = record.get("sma_parity")
    if not isinstance(sma_parity, Mapping) or sma_parity.get("passed") is not True:
        raise Stage2DataError("acceptance record SMA parity is missing")
    if set(sma_parity) != {"end", "interval", "passed", "series", "start"}:
        raise Stage2DataError("acceptance record SMA parity schema does not match")
    if (
        sma_parity.get("start") != STAGE2_SMA_PARITY_START_ISO
        or sma_parity.get("end") != STAGE2_SMA_PARITY_END_ISO
        or sma_parity.get("interval") != STAGE2_SMA_PARITY_INTERVAL
    ):
        raise Stage2DataError("acceptance record SMA parity window does not match")
    sma_series = sma_parity.get("series")
    if not isinstance(sma_series, list) or len(sma_series) != len(
        STAGE2_INSTRUMENT_IDS
    ):
        raise Stage2DataError("acceptance record SMA parity series are incomplete")
    observed_sma: set[str] = set()
    for item in sma_series:
        if not isinstance(item, Mapping):
            raise Stage2DataError("acceptance record SMA parity series is invalid")
        if set(item) != {
            "bar_type",
            "instrument_id",
            "periods",
            "price_precision",
            "row_count",
        }:
            raise Stage2DataError("acceptance record SMA parity schema does not match")
        instrument_id = item.get("instrument_id")
        row_count = item.get("row_count")
        if (
            not isinstance(instrument_id, str)
            or instrument_id not in STAGE2_INSTRUMENT_IDS
            or item.get("bar_type")
            != stage2_bar_type_str(instrument_id, STAGE2_SMA_PARITY_INTERVAL)
            or not isinstance(row_count, int)
            or row_count <= max(STAGE2_SMA_PARITY_PERIODS)
            or not isinstance(item.get("price_precision"), int)
            or item["price_precision"] < 0
        ):
            raise Stage2DataError("acceptance record SMA parity instrument is invalid")
        results = item.get("periods")
        if not isinstance(results, list) or {
            result.get("period") for result in results if isinstance(result, Mapping)
        } != set(STAGE2_SMA_PARITY_PERIODS):
            raise Stage2DataError("acceptance record SMA parity periods are incomplete")
        for result in results:
            if not isinstance(result, Mapping):
                raise Stage2DataError("acceptance record SMA parity contains failures")
            if set(result) != {
                "compared_points",
                "max_absolute_error",
                "passed",
                "period",
                "price_tick",
            }:
                raise Stage2DataError(
                    "acceptance record SMA parity schema does not match"
                )
            period = result.get("period")
            compared_points = result.get("compared_points")
            try:
                error = Decimal(str(result.get("max_absolute_error")))
                price_tick = Decimal(str(result.get("price_tick")))
            except InvalidOperation as exc:
                raise Stage2DataError(
                    "acceptance record SMA parity contains failures"
                ) from exc
            if (
                result.get("passed") is not True
                or not isinstance(period, int)
                or not isinstance(compared_points, int)
                or compared_points != row_count - period + 1
                or not error.is_finite()
                or error < 0
                or not price_tick.is_finite()
                or price_tick <= 0
                or error > price_tick
            ):
                raise Stage2DataError("acceptance record SMA parity contains failures")
        observed_sma.add(instrument_id)
    if observed_sma != set(STAGE2_INSTRUMENT_IDS):
        raise Stage2DataError("acceptance record SMA parity instruments are incomplete")

    coverage = record.get("coverage_summary")
    if not isinstance(coverage, list) or len(coverage) != 10:
        raise Stage2DataError("acceptance record coverage is incomplete")
    expected_coverage = {
        (data_type, instrument_id, stage2_bar_type_str(instrument_id, interval))
        for instrument_id in STAGE2_INSTRUMENT_IDS
        for data_type, interval in (
            *((STAGE2_DATA_TYPE_BARS, item) for item in STAGE2_BAR_INTERVALS),
            (STAGE2_DATA_TYPE_MARK, STAGE2_MARK_INTERVAL),
        )
    }
    expected_coverage.update(
        (STAGE2_DATA_TYPE_FUNDING, instrument_id, "")
        for instrument_id in STAGE2_INSTRUMENT_IDS
    )
    observed_coverage: set[tuple[object, object, object]] = set()
    coverage_by_series: dict[tuple[str, str], Mapping[str, object]] = {}
    for item in coverage:
        if not isinstance(item, Mapping):
            raise Stage2DataError("acceptance record coverage entry is invalid")
        expected_coverage_keys = {
            "data_type",
            "dataset_id",
            "duplicate_count",
            "first_ts_event",
            "gap_count",
            "gap_explanation",
            "instrument_id",
            "last_ts_event",
            "out_of_order_count",
            "row_count",
            "source_sha256",
        }
        if item.get("data_type") != STAGE2_DATA_TYPE_FUNDING:
            expected_coverage_keys.add("bar_type")
        if set(item) != expected_coverage_keys:
            raise Stage2DataError("acceptance record coverage schema does not match")
        if item.get("dataset_id") != STAGE2_DATASET_ID:
            raise Stage2DataError("acceptance record coverage dataset does not match")
        if not isinstance(item.get("row_count"), int) or item["row_count"] <= 0:
            raise Stage2DataError("acceptance record coverage is empty")
        if (
            not isinstance(item.get("first_ts_event"), int)
            or not isinstance(item.get("last_ts_event"), int)
            or item["first_ts_event"] > item["last_ts_event"]
        ):
            raise Stage2DataError("acceptance record coverage range is invalid")
        if any(item.get(key) != 0 for key in ("duplicate_count", "out_of_order_count")):
            raise Stage2DataError("acceptance record coverage contains failures")
        if item.get("gap_count") != 0 and not (
            item.get("data_type") == STAGE2_DATA_TYPE_MARK
            and item.get("gap_count") == len(STAGE2_MARK_ALLOWED_GAPS)
            and item.get("gap_explanation") == STAGE2_MARK_GAP_EXPLANATION
        ):
            raise Stage2DataError("acceptance record coverage contains failures")
        source_digest = item.get("source_sha256")
        if (
            not isinstance(source_digest, str)
            or not _is_sha256_string(source_digest)
            or source_digest == "0" * 64
        ):
            raise Stage2DataError("acceptance record coverage is not source-bound")
        observed_coverage.add(
            (
                item.get("data_type"),
                item.get("instrument_id"),
                item.get("bar_type", ""),
            )
        )
        data_type = item.get("data_type")
        instrument_id = item.get("instrument_id")
        if isinstance(data_type, str) and isinstance(instrument_id, str):
            if data_type == STAGE2_DATA_TYPE_FUNDING or item.get(
                "bar_type"
            ) == stage2_bar_type_str(instrument_id, "1h"):
                coverage_by_series[(instrument_id, data_type)] = item
    if observed_coverage != expected_coverage:
        raise Stage2DataError("acceptance record coverage identities are incomplete")

    crosscheck = record.get("crosscheck")
    if not isinstance(crosscheck, Mapping):
        raise Stage2DataError("acceptance record cross-check is missing")
    if set(crosscheck) != {
        "compared_records",
        "end",
        "series",
        "source",
        "start",
        "written_to_catalog",
    }:
        raise Stage2DataError("acceptance record cross-check schema does not match")
    if (
        not isinstance(crosscheck.get("compared_records"), int)
        or crosscheck["compared_records"] <= 0
    ):
        raise Stage2DataError("acceptance record cross-check is empty")
    crosscheck_series = crosscheck.get("series")
    if not isinstance(crosscheck_series, list) or len(crosscheck_series) != 4:
        raise Stage2DataError("acceptance record cross-check series are incomplete")
    if any(
        not isinstance(item, Mapping)
        or set(item) != {"data_type", "instrument_id", "row_count"}
        or not isinstance(item.get("row_count"), int)
        or item["row_count"] <= 0
        for item in crosscheck_series
    ):
        raise Stage2DataError("acceptance record cross-check series are empty")
    compared_records = sum(
        item["row_count"] for item in crosscheck_series if isinstance(item, Mapping)
    )
    if crosscheck.get("compared_records") != compared_records:
        raise Stage2DataError("acceptance record cross-check count does not match")
    observed_crosscheck = {
        (item.get("data_type"), item.get("instrument_id"))
        for item in crosscheck_series
        if isinstance(item, Mapping)
    }
    expected_crosscheck = {
        (data_type, instrument_id)
        for instrument_id in STAGE2_INSTRUMENT_IDS
        for data_type in (STAGE2_DATA_TYPE_BARS, STAGE2_DATA_TYPE_FUNDING)
    }
    if observed_crosscheck != expected_crosscheck:
        raise Stage2DataError("acceptance record cross-check identities are incomplete")
    if (
        crosscheck.get("start") != STAGE2_CROSSCHECK_START_ISO
        or crosscheck.get("end") != STAGE2_CROSSCHECK_END_ISO
    ):
        raise Stage2DataError("acceptance record cross-check window does not match")
    if crosscheck.get("written_to_catalog") is not False:
        raise Stage2DataError("acceptance cross-check must not write to the catalog")
    if require_live_crosscheck and crosscheck.get("source") != (
        "nautilus_bars+binance_rest_funding"
    ):
        raise Stage2DataError("tracked acceptance requires a Nautilus cross-check")

    homology = record.get("homology")
    expected_homology = {
        f"{split}:{instrument_id}:{data_type}"
        for split in ("train", "validation", "test")
        for instrument_id in STAGE2_INSTRUMENT_IDS
        for data_type in (STAGE2_DATA_TYPE_BARS, STAGE2_DATA_TYPE_FUNDING)
    }
    if not isinstance(homology, Mapping) or set(homology) != expected_homology:
        raise Stage2DataError("acceptance record homology is incomplete")
    validated_homology: dict[str, Mapping[str, object]] = {}
    for split in ("train", "validation", "test"):
        for instrument_id in STAGE2_INSTRUMENT_IDS:
            for data_type in (STAGE2_DATA_TYPE_BARS, STAGE2_DATA_TYPE_FUNDING):
                key = f"{split}:{instrument_id}:{data_type}"
                item = homology[key]
                expected_bar_type = (
                    stage2_bar_type_str(instrument_id, "1h")
                    if data_type == STAGE2_DATA_TYPE_BARS
                    else None
                )
                if (
                    not isinstance(item, Mapping)
                    or set(item)
                    != {
                        "data_type",
                        "dataset_id",
                        "first_ts_event",
                        "instrument_id",
                        "last_ts_event",
                        "row_count",
                        *(
                            {"bar_type"}
                            if data_type == STAGE2_DATA_TYPE_BARS
                            else set()
                        ),
                    }
                    or item.get("dataset_id") != STAGE2_DATASET_ID
                    or item.get("instrument_id") != instrument_id
                    or item.get("data_type") != data_type
                    or (
                        data_type == STAGE2_DATA_TYPE_BARS
                        and item.get("bar_type") != expected_bar_type
                    )
                    or not isinstance(item.get("row_count"), int)
                    or item["row_count"] <= 0
                    or not isinstance(item.get("first_ts_event"), int)
                    or not isinstance(item.get("last_ts_event"), int)
                    or item["first_ts_event"] > item["last_ts_event"]
                ):
                    raise Stage2DataError(
                        "acceptance record homology contains invalid windows"
                    )
                validated_homology[key] = item

    for instrument_id in STAGE2_INSTRUMENT_IDS:
        for data_type in (STAGE2_DATA_TYPE_BARS, STAGE2_DATA_TYPE_FUNDING):
            windows = [
                validated_homology[f"{split}:{instrument_id}:{data_type}"]
                for split in ("train", "validation", "test")
            ]
            coverage_item = coverage_by_series[(instrument_id, data_type)]
            if (
                sum(cast(int, item["row_count"]) for item in windows)
                != coverage_item.get("row_count")
                or windows[0]["first_ts_event"] != coverage_item.get("first_ts_event")
                or windows[-1]["last_ts_event"] != coverage_item.get("last_ts_event")
                or any(
                    cast(int, left["last_ts_event"])
                    >= cast(int, right["first_ts_event"])
                    for left, right in zip(windows, windows[1:])
                )
            ):
                raise Stage2DataError(
                    "acceptance record homology does not match catalog coverage"
                )

    acceptance_digest = record.get("acceptance_digest")
    if (
        not isinstance(acceptance_digest, str)
        or not _is_sha256_string(acceptance_digest)
        or acceptance_digest == "0" * 64
        or acceptance_digest != acceptance_record_digest(record)
    ):
        raise Stage2DataError("acceptance record digest does not match")


def require_no_local_absolute_paths(payload: object) -> None:
    if isinstance(payload, Mapping):
        for value in payload.values():
            require_no_local_absolute_paths(value)
        return
    if isinstance(payload, (list, tuple)):
        for value in payload:
            require_no_local_absolute_paths(value)
        return
    if (
        isinstance(payload, str)
        and payload.startswith("/")
        and not payload.startswith(("http://", "https://"))
    ):
        raise Stage2DataError("acceptance record must not contain local absolute paths")


def _source_spec(
    root: str,
    symbol: str,
    interval: str,
    year: int,
    month: int,
    *,
    instrument_id: str,
    data_type: str,
) -> Stage2SourceSpec:
    name = f"{symbol}-{interval}-{year:04d}-{month:02d}.zip"
    relative = f"{root}/{symbol}/{interval}/{name}"
    source_url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
    return Stage2SourceSpec(
        relative_path=relative,
        source_url=source_url,
        checksum_url=f"{source_url}.CHECKSUM",
        instrument_id=instrument_id,
        data_type=data_type,
        year=year,
        month=month,
    )


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_continuous_months(months: Sequence[tuple[int, int]]) -> None:
    if not months:
        raise Stage2DataError("index month objects are not continuous")
    previous = months[0]
    for year, month in months[1:]:
        expected_year = previous[0] + (1 if previous[1] == 12 else 0)
        expected_month = 1 if previous[1] == 12 else previous[1] + 1
        if (year, month) != (expected_year, expected_month):
            raise Stage2DataError("index month objects are not continuous")
        previous = (year, month)


def _require_locked_identity(
    config: Stage2DatasetConfig,
    *,
    locked_start: datetime,
    locked_end: datetime,
) -> None:
    if config.schema != STAGE2_CONFIG_SCHEMA:
        raise Stage2DataError("dataset schema identity does not match")
    if config.dataset_id != STAGE2_DATASET_ID:
        raise Stage2DataError("dataset identity does not match")
    if config.nautilus_version != STAGE2_NAUTILUS_VERSION:
        raise Stage2DataError("nautilus version identity does not match")
    if config.environment != STAGE2_ENVIRONMENT:
        raise Stage2DataError("environment identity does not match")
    if config.source != STAGE2_SOURCE:
        raise Stage2DataError("source identity does not match")
    if config.market != STAGE2_MARKET:
        raise Stage2DataError("market identity does not match")
    if config.archive_frequency != STAGE2_ARCHIVE_FREQUENCY:
        raise Stage2DataError("archive frequency identity does not match")
    if config.window_start != locked_start or config.window_end != locked_end:
        raise Stage2DataError("dataset window identity does not match")
    if config.instrument_ids != STAGE2_INSTRUMENT_IDS:
        raise Stage2DataError("instrument identity does not match")
    if config.bar_intervals != STAGE2_BAR_INTERVALS:
        raise Stage2DataError("bar interval identity does not match")
    if config.bar_aggregation != STAGE2_BAR_AGGREGATION:
        raise Stage2DataError("bar aggregation identity does not match")


def _require_external_dir(value: str, *, repository_root: Path, field: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise Stage2DataError(f"{field} must be an absolute path")
    resolved = candidate.resolve()
    repo = repository_root.resolve()
    if resolved == repo or resolved.is_relative_to(repo):
        raise Stage2DataError(f"{field} must be outside the repository")
    if not resolved.is_dir():
        raise Stage2DataError(f"{field} must be an existing directory")
    return resolved


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise Stage2DataError(f"config field {key} must be a non-empty string")
    return value


def _require_str_tuple(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise Stage2DataError(f"config field {key} must be a non-empty string array")
    return tuple(value)


def _month_keys(
    window_start: datetime, window_end: datetime
) -> tuple[tuple[int, int], ...]:
    start = require_utc(window_start).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    end = require_utc(window_end)
    months: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        months.append((cursor.year, cursor.month))
        year = cursor.year + (1 if cursor.month == 12 else 0)
        month = 1 if cursor.month == 12 else cursor.month + 1
        cursor = datetime(year, month, 1, tzinfo=UTC)
    return tuple(months)


def _parse_checksum_file(path: Path, zip_name: str) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise Stage2DataError("official checksum file is empty")
    first = text.splitlines()[0].split()
    if not first or not _is_sha256_string(first[0]):
        raise Stage2DataError("official checksum file is invalid")
    if len(first) > 1 and first[1].lstrip("*") not in {zip_name, ""}:
        raise Stage2DataError("official checksum file name does not match")
    return first[0]


def _map_funding_header(fields: list[str]) -> list[str]:
    mapped: list[str] = []
    seen: set[str] = set()
    for field in fields:
        normalized = field.strip().lower().replace(" ", "_")
        canonical = _FUNDING_HEADER_ALIASES.get(normalized)
        if canonical is None:
            raise Stage2DataError("funding csv header is not a known official schema")
        if canonical in seen:
            raise Stage2DataError("funding csv header maps to duplicate columns")
        seen.add(canonical)
        mapped.append(canonical)
    if tuple(mapped) != _CANONICAL_FUNDING_FIELDS:
        raise Stage2DataError("funding csv header is not the fixed schema")
    return mapped


def _validate_funding_row_values(
    row: Stage2FundingRow,
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> None:
    calc_time = _require_int_string(row.calc_time, field="calc_time")
    if calc_time < window_start_ms or calc_time >= window_end_ms:
        raise Stage2DataError("funding record is outside the declared window")
    hours = _require_int_string(
        row.funding_interval_hours, field="funding_interval_hours"
    )
    if hours <= 0:
        raise Stage2DataError("funding interval is illegal")
    _require_decimal_string(row.last_funding_rate, field="last_funding_rate")


def funding_interval_minutes(row: Stage2FundingRow) -> int:
    hours = _require_int_string(
        row.funding_interval_hours, field="funding_interval_hours"
    )
    if hours <= 0:
        raise Stage2DataError("funding interval is illegal")
    return hours * 60


def _map_header(fields: list[str]) -> list[str]:
    mapped: list[str] = []
    seen: set[str] = set()
    for field in fields:
        normalized = field.strip().lower().replace(" ", "_")
        canonical = _HEADER_ALIASES.get(normalized)
        if canonical is None:
            raise Stage2DataError("kline csv header is not a known official schema")
        if canonical in seen:
            raise Stage2DataError("kline csv header maps to duplicate columns")
        seen.add(canonical)
        mapped.append(canonical)
    if tuple(mapped) != _CANONICAL_KLINE_FIELDS and seen != set(
        _CANONICAL_KLINE_FIELDS
    ):
        raise Stage2DataError("kline csv header is not the fixed schema")
    if len(mapped) != 12:
        raise Stage2DataError("kline csv column count is not the fixed schema")
    if set(mapped) != set(_CANONICAL_KLINE_FIELDS):
        raise Stage2DataError("kline csv header is not the fixed schema")
    return mapped


def _validate_kline_row_values(
    row: Stage2KlineRow,
    *,
    interval_ms: int,
    window_start_ms: int,
    window_end_ms: int,
) -> None:
    open_time = _require_int_string(row.open_time, field="open_time")
    close_time = _require_int_string(row.close_time, field="close_time")
    if close_time != open_time + interval_ms - 1:
        raise Stage2DataError("kline close time does not match the interval")
    if open_time < window_start_ms or close_time >= window_end_ms:
        raise Stage2DataError("kline record is outside the declared window")
    open_px = _require_decimal_string(row.open, field="open")
    high_px = _require_decimal_string(row.high, field="high")
    low_px = _require_decimal_string(row.low, field="low")
    close_px = _require_decimal_string(row.close, field="close")
    volume = _require_decimal_string(row.volume, field="volume")
    _require_decimal_string(row.quote_volume, field="quote_volume")
    _require_int_string(row.count, field="count")
    _require_decimal_string(row.taker_buy_volume, field="taker_buy_volume")
    _require_decimal_string(row.taker_buy_quote_volume, field="taker_buy_quote_volume")
    _require_int_string(row.ignore, field="ignore")
    if high_px < low_px or high_px < open_px or high_px < close_px:
        raise Stage2DataError("kline OHLC relationship is illegal")
    if low_px > open_px or low_px > close_px:
        raise Stage2DataError("kline OHLC relationship is illegal")
    if volume < 0:
        raise Stage2DataError("kline volume is negative")


def _require_int_string(value: str, *, field: str) -> int:
    if not _is_int_string(value):
        raise Stage2DataError(f"kline field {field} is not a valid integer")
    return int(value)


def _require_decimal_string(value: str, *, field: str) -> Decimal:
    if not value or value.strip() != value:
        raise Stage2DataError(f"kline field {field} is not a valid number")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise Stage2DataError(f"kline field {field} is not a valid number") from exc
    if not parsed.is_finite():
        raise Stage2DataError(f"kline field {field} is not a valid number")
    if parsed < 0 and field == "volume":
        raise Stage2DataError("kline volume is negative")
    return parsed


def _is_int_string(value: str) -> bool:
    if value == "0":
        return True
    return bool(value) and value[0] != "0" and value.isdigit()


def _is_sha256_string(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX_DIGITS for char in value)
