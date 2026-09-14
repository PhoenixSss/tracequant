from __future__ import annotations

import csv
import hashlib
import io
import json
import tomllib
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

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
STAGE2_ACCEPTANCE_SCHEMA: Final = "tracequant-stage2-acceptance-v1"
STAGE2_PUBLIC_DATA_ORIGIN: Final = "https://data.binance.vision"
STAGE2_KLINE_ROOT: Final = "data/futures/um/monthly/klines"
STAGE2_MARK_ROOT: Final = "data/futures/um/monthly/markPriceKlines"
STAGE2_FUNDING_ROOT: Final = "data/futures/um/monthly/fundingRate"
STAGE2_INDEX_ROOT: Final = "data/futures/um/monthly/indexPriceKlines"
STAGE2_MARK_INTERVAL: Final = "15m"
STAGE2_INDEX_INTERVAL: Final = "15m"
STAGE2_FUNDING_STREAM_ID: Final = "stage2-funding"
STAGE2_MARK_MAX_AGE_MS: Final = 15 * 60 * 1000
STAGE2_EXPECTED_SOURCE_COUNT: Final = 800
STAGE2_INCLUDE_INDEX_PRICE: Final = False
STAGE2_NAUTILUS_TAIL_ENABLED: Final = False
STAGE2_CROSSCHECK_START_ISO: Final = "2026-08-25T00:00:00Z"
STAGE2_CROSSCHECK_END_ISO: Final = "2026-09-01T00:00:00Z"
STAGE2_GENERATION_COMMAND: Final = (
    "uv run --frozen python -m tracequant.integrations.nautilus.stage2_btceth"
)
MS_NS: Final = 1_000_000

STAGE2_INSTRUMENT_SYMBOLS: Final = {
    "BTCUSDT-PERP.BINANCE": "BTCUSDT",
    "ETHUSDT-PERP.BINANCE": "ETHUSDT",
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
    sources: tuple[Stage2SourceObject, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "dataset_id": self.dataset_id,
            "nautilus_version": self.nautilus_version,
            "sources": [item.to_json_dict() for item in self.sources],
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


def discover_stage2_funding_archives(
    config: Stage2DatasetConfig,
) -> tuple[Stage2FundingArchive, ...]:
    archives: list[Stage2FundingArchive] = []
    for instrument_id in config.instrument_ids:
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        found = 0
        for year, month in _month_keys(config.window_start, config.window_end):
            name = f"{symbol}-fundingRate-{year:04d}-{month:02d}.zip"
            zip_path = config.raw_root / STAGE2_FUNDING_ROOT / symbol / name
            if not zip_path.exists():
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
    csv_name = f"{archive.symbol}-{archive.interval}-{archive.year:04d}-{archive.month:02d}.csv"
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
) -> Stage2SeriesCoverage:
    if instrument_id not in STAGE2_INSTRUMENT_SYMBOLS:
        raise Stage2DataError("instrument is not a stage 2 target")
    if not rows:
        raise Stage2DataError("funding series is empty")
    seen: set[int] = set()
    duplicate_count = 0
    out_of_order_count = 0
    previous: int | None = None
    first_ts = 0
    last_ts = 0
    for index, row in enumerate(rows):
        calc_time = _require_int_string(row.calc_time, field="calc_time")
        ts_event = millis_to_nanos(calc_time)
        if index == 0:
            first_ts = ts_event
        last_ts = ts_event
        if calc_time in seen:
            duplicate_count += 1
        seen.add(calc_time)
        if previous is not None and calc_time < previous:
            out_of_order_count += 1
        previous = calc_time
    if duplicate_count:
        raise Stage2DataError("funding series contains duplicate event times")
    if out_of_order_count:
        raise Stage2DataError("funding series is out of order")
    return Stage2SeriesCoverage(
        instrument_id=instrument_id,
        data_type=STAGE2_DATA_TYPE_FUNDING,
        bar_interval="",
        row_count=len(rows),
        first_ts_event=first_ts,
        last_ts_event=last_ts,
        duplicate_count=duplicate_count,
        out_of_order_count=out_of_order_count,
        gap_count=0,
        source_checksum=source_checksum,
    )


def validate_mark_series(
    rows: Sequence[Stage2KlineRow],
    *,
    instrument_id: str,
    source_checksum: str,
) -> Stage2SeriesCoverage:
    coverage = validate_kline_series(
        rows,
        instrument_id=instrument_id,
        interval=STAGE2_MARK_INTERVAL,
        source_checksum=source_checksum,
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
        previous_open = open_time
    if duplicate_count:
        raise Stage2DataError("kline series contains duplicate event times")
    if out_of_order_count:
        raise Stage2DataError("kline series is out of order")
    if gap_count:
        raise Stage2DataError("kline series contains a gap")
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


def require_stage2_catalog_identity(catalog_path: Path) -> None:
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


def source_manifest_digest(manifest: Stage2SourceManifest) -> str:
    payload = [
        {
            "checksum_url": item.checksum_url,
            "data_type": item.data_type,
            "end_ns": item.end_ns,
            "instrument_id": item.instrument_id,
            "rows": item.rows,
            "sha256": item.sha256,
            "source_url": item.source_url,
            "start_ns": item.start_ns,
        }
        for item in manifest.sources
    ]
    return _canonical_digest(payload)


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


def stage2_index_coverage_conclusion() -> dict[str, object]:
    if STAGE2_INCLUDE_INDEX_PRICE:
        raise Stage2DataError("index price must not be included in the first catalog")
    months = stage2_month_keys()
    _require_continuous_months(months)
    objects: list[dict[str, str | int]] = []
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        for year, month in months:
            name = f"{symbol}-{STAGE2_INDEX_INTERVAL}-{year:04d}-{month:02d}.zip"
            relative = f"{STAGE2_INDEX_ROOT}/{symbol}/{STAGE2_INDEX_INTERVAL}/{name}"
            source_url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
            objects.append(
                {
                    "checksum_url": f"{source_url}.CHECKSUM",
                    "instrument_id": instrument_id,
                    "month": month,
                    "source_url": source_url,
                    "year": year,
                }
            )
    return {
        "checksum_available": True,
        "continuous_months": True,
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
        if item.gap_count:
            raise Stage2DataError("coverage report contains an unexplained gap")
    summary: list[dict[str, str | int]] = []
    for item in report.series:
        entry: dict[str, str | int] = {
            "data_type": item.data_type,
            "dataset_id": report.dataset_id,
            "duplicate_count": item.duplicate_count,
            "first_ts_event": item.first_ts_event,
            "gap_count": item.gap_count,
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


def build_stage2_acceptance_record(
    *,
    manifest: Stage2SourceManifest,
    coverage: Stage2CoverageReport,
    runtime_identity: str,
    crosscheck: Mapping[str, object],
    homology: Mapping[str, object],
) -> dict[str, object]:
    require_tail_disabled()
    record: dict[str, object] = {
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
        "include_index_price": STAGE2_INCLUDE_INDEX_PRICE,
        "index_coverage": stage2_index_coverage_conclusion(),
        "nautilus_tail_enabled": STAGE2_NAUTILUS_TAIL_ENABLED,
        "nautilus_version": STAGE2_NAUTILUS_VERSION,
        "runtime_identity": runtime_identity,
        "schema": STAGE2_ACCEPTANCE_SCHEMA,
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
    require_no_local_absolute_paths(record)
    return record


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
    negative = value.startswith("-")
    unsigned = value[1:] if negative else value
    if not _is_decimal_string(unsigned):
        raise Stage2DataError(f"kline field {field} is not a valid number")
    if negative and field == "volume":
        raise Stage2DataError("kline volume is negative")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise Stage2DataError(f"kline field {field} is not a valid number") from exc


def _is_int_string(value: str) -> bool:
    if value == "0":
        return True
    return bool(value) and value[0] != "0" and value.isdigit()


def _is_decimal_string(value: str) -> bool:
    if "." not in value:
        return _is_int_string(value)
    whole, fraction = value.split(".", 1)
    if not fraction or not fraction.isdigit():
        return False
    return _is_int_string(whole)


def _is_sha256_string(value: str) -> bool:
    return len(value) == 64 and all(char in _HEX_DIGITS for char in value)
