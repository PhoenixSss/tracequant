"""Binance USDⓈ-M 1m contract-Kline archive backfill.

The adapter implements the archive-only supported path frozen by the public-
history source contract.  It plans complete UTC months as monthly objects and
the remaining intersecting UTC days as daily objects, verifies Binance's
published checksum, parses the complete 12-column wire schema, and delegates
all persistence and immutable-conflict decisions to :class:`RawStore`.
"""

from __future__ import annotations

import csv
import hashlib
import http.client
import io
import math
import re
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

import polars as pl

from tracequant.data.public_history import (
    BinanceArchiveObjectBoundary,
    BinanceKlineInterval,
    BinancePublicHistoryDataType,
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceKind,
)
from tracequant.data.raw_store import (
    RawAcquisitionManifest,
    RawAcquisitionResponse,
    RawArtifact,
    RawArtifactConflictError,
    RawArtifactNotFoundError,
    RawArtifactValidationError,
    RawObjectIdentity,
    RawSourceObject,
    RawSourceProvenance,
    RawStore,
)
from tracequant.domain import InstrumentId, TimeRange

__all__ = [
    "ArchiveHttpResponse",
    "BinanceArchiveCoverageGapPlan",
    "BinanceArchiveObjectPlan",
    "BinanceContractKlineBackfill",
    "BinanceContractKlineObjectResult",
    "BinanceContractKlineRunResult",
    "BinanceContractKlineStatus",
    "plan_binance_contract_kline_archives",
]

_ARCHIVE_ROOT: Final = "https://data.binance.vision"
_SCHEMA_IDENTIFIER: Final = "binance.um.contract-kline.csv.v1"
_PRODUCER_VERSION: Final = "tracequant/0.1.0"
_ONE_MINUTE_MS: Final = 60_000
_MAX_ARCHIVE_BYTES: Final = 512 * 1024 * 1024
# Frozen per-instrument archive coverage from the approved Research contract.
# These are observed object boundaries, not values to advance from wall time.
_RESEARCH_ARCHIVE_COVERAGE: Final = {
    "BTCUSDT": {
        "monthly": (date(2020, 1, 1), date(2026, 7, 1)),
        "daily": (date(2019, 12, 31), date(2026, 8, 29)),
    },
    "ETHUSDT": {
        "monthly": (date(2020, 1, 1), date(2026, 7, 1)),
        "daily": (date(2019, 12, 31), date(2026, 8, 29)),
    },
}
_CHECKSUM_PATTERN: Final = re.compile(
    r"\A([0-9A-Fa-f]{64})[ \t]+[*]?([^\r\n]+)[\r\n]*\Z"
)
_COLUMNS: Final = (
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
_DTYPES: Final = {
    "open_time": pl.Int64,
    "open": pl.String,
    "high": pl.String,
    "low": pl.String,
    "close": pl.String,
    "volume": pl.String,
    "close_time": pl.Int64,
    "quote_volume": pl.String,
    "count": pl.Int64,
    "taker_buy_volume": pl.String,
    "taker_buy_quote_volume": pl.String,
    "ignore": pl.String,
}


@dataclass(frozen=True, slots=True)
class ArchiveHttpResponse:
    """Bounded response returned by an injectable archive HTTP transport."""

    status: int
    body: bytes
    headers: Mapping[str, str]


class ArchiveHttpGet(Protocol):
    def __call__(self, url: str, timeout: float) -> ArchiveHttpResponse: ...


@dataclass(frozen=True, slots=True)
class BinanceArchiveObjectPlan:
    """One deterministic official archive object needed by a caller range."""

    request: BinancePublicHistoryRequest
    object_key: str
    url: str
    checksum_url: str
    member_name: str


@dataclass(frozen=True, slots=True)
class BinanceArchiveCoverageGapPlan:
    """One UTC day for which Research did not prove an archive object exists."""

    instrument: InstrumentId
    request_range: TimeRange
    boundary: BinanceArchiveObjectBoundary
    detail: str

    @property
    def request(self) -> BinancePublicHistoryRequest:
        """Return the source request identity represented by this gap."""
        return BinancePublicHistoryRequest(
            instrument=self.instrument,
            data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
            request_range=self.request_range,
            source_kind=BinancePublicHistorySourceKind.ARCHIVE_DAILY,
            interval=BinanceKlineInterval.ONE_MINUTE,
            archive_object_boundary=self.boundary,
        )


type BinanceContractKlinePlan = BinanceArchiveObjectPlan | BinanceArchiveCoverageGapPlan


class BinanceContractKlineStatus(StrEnum):
    PUBLISHED = "published"
    EXISTING = "existing"
    COVERAGE_GAP = "coverage_gap"
    NOT_FOUND = "not_found"
    CHECKSUM_NOT_FOUND = "checksum_not_found"
    RETRYABLE_FAILURE = "retryable_failure"
    INVALID_CONTENT = "invalid_content"
    LOCAL_FAILURE = "local_failure"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class BinanceContractKlineObjectResult:
    plan: BinanceContractKlinePlan
    status: BinanceContractKlineStatus
    artifact_path: Path | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class BinanceContractKlineRunResult:
    """All object outcomes for one request; success is deliberately all-or-none."""

    request_range: TimeRange
    objects: tuple[BinanceContractKlineObjectResult, ...]

    @property
    def completed(self) -> bool:
        successful = {
            BinanceContractKlineStatus.PUBLISHED,
            BinanceContractKlineStatus.EXISTING,
        }
        return bool(self.objects) and all(
            item.status in successful for item in self.objects
        )


class _InvalidContentError(ValueError):
    def __init__(
        self, message: str, *, response: ArchiveHttpResponse | None = None
    ) -> None:
        super().__init__(message)
        self.response = response


class _CoverageGapError(ValueError):
    pass


class _RetryableDownloadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        resource: str,
        response: ArchiveHttpResponse | None = None,
    ) -> None:
        super().__init__(message)
        self.resource = resource
        self.response = response


@dataclass(frozen=True, slots=True)
class _DownloadedResponse:
    body: bytes
    status: int
    headers: Mapping[str, str]


class _DownloadNotFoundError(FileNotFoundError):
    def __init__(
        self,
        url: str,
        *,
        resource: str,
        response: ArchiveHttpResponse | None = None,
    ) -> None:
        super().__init__(url)
        self.resource = resource
        self.response = response


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _utc_midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _build_plan(
    instrument: InstrumentId,
    request_range: TimeRange,
    boundary: BinanceArchiveObjectBoundary,
) -> BinanceArchiveObjectPlan:
    monthly = boundary.granularity.value == "month"
    cadence = "monthly" if monthly else "daily"
    suffix = (
        boundary.period_start.strftime("%Y-%m")
        if monthly
        else boundary.period_start.isoformat()
    )
    filename = f"{instrument}-1m-{suffix}.zip"
    object_key = f"data/futures/um/{cadence}/klines/{instrument}/1m/{filename}"
    request = BinancePublicHistoryRequest(
        instrument=instrument,
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=request_range,
        source_kind=(
            BinancePublicHistorySourceKind.ARCHIVE_MONTHLY
            if monthly
            else BinancePublicHistorySourceKind.ARCHIVE_DAILY
        ),
        interval=BinanceKlineInterval.ONE_MINUTE,
        archive_object_boundary=boundary,
    )
    return BinanceArchiveObjectPlan(
        request=request,
        object_key=object_key,
        url=f"{_ARCHIVE_ROOT}/{object_key}",
        checksum_url=f"{_ARCHIVE_ROOT}/{object_key}.CHECKSUM",
        member_name=f"{instrument}-1m-{suffix}.csv",
    )


def plan_binance_contract_kline_archives(
    instrument: InstrumentId,
    request_range: TimeRange,
) -> tuple[BinanceContractKlinePlan, ...]:
    """Map a UTC ``[start, end)`` range to proven objects or explicit gaps."""
    if not isinstance(instrument, InstrumentId):
        raise TypeError("instrument must be an InstrumentId")
    if not isinstance(request_range, TimeRange):
        raise TypeError("request_range must be a TimeRange")

    coverage = _RESEARCH_ARCHIVE_COVERAGE.get(str(instrument))
    if coverage is None:
        raise ValueError(
            f"instrument {instrument!s} has no frozen contract-Kline archive coverage"
        )

    monthly_first, monthly_last = coverage["monthly"]
    daily_first, daily_last = coverage["daily"]
    cursor = request_range.start.date()
    final_day = (request_range.end - timedelta(microseconds=1)).date()
    plans: list[BinanceContractKlinePlan] = []
    while cursor <= final_day:
        following_month = _next_month(cursor)
        can_use_month = (
            cursor.day == 1
            and monthly_first <= cursor <= monthly_last
            and request_range.start <= _utc_midnight(cursor)
            and request_range.end >= _utc_midnight(following_month)
        )
        if can_use_month:
            boundary = BinanceArchiveObjectBoundary.month(cursor.year, cursor.month)
            cursor = following_month
        else:
            boundary = BinanceArchiveObjectBoundary.day(cursor)
            cursor += timedelta(days=1)
        if (
            boundary.granularity.value == "month"
            or daily_first <= boundary.period_start <= daily_last
        ):
            plans.append(_build_plan(instrument, request_range, boundary))
        else:
            plans.append(
                BinanceArchiveCoverageGapPlan(
                    instrument=instrument,
                    request_range=request_range,
                    boundary=boundary,
                    detail=(
                        "Research did not prove a contract-Kline archive object "
                        f"for {instrument!s} on {boundary.period_start.isoformat()}"
                    ),
                )
            )
    return tuple(plans)


def _default_http_get(url: str, timeout: float) -> ArchiveHttpResponse:
    request = urllib.request.Request(url, headers={"User-Agent": "tracequant/0.1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(_MAX_ARCHIVE_BYTES + 1)
            if len(body) > _MAX_ARCHIVE_BYTES:
                raise _InvalidContentError("archive response exceeds the size limit")
            return ArchiveHttpResponse(
                status=response.status,
                body=body,
                headers=dict(response.headers.items()),
            )
    except urllib.error.HTTPError as error:
        return ArchiveHttpResponse(
            status=error.code,
            body=error.read(4096),
            headers=dict(error.headers.items()) if error.headers is not None else {},
        )
    except (TimeoutError, urllib.error.URLError):
        raise


def _download(
    http_get: ArchiveHttpGet,
    url: str,
    timeout: float,
    *,
    resource: str,
) -> _DownloadedResponse:
    try:
        response = http_get(url, timeout)
    except _InvalidContentError:
        raise
    except (
        TimeoutError,
        ConnectionError,
        OSError,
        http.client.HTTPException,
    ) as error:
        raise _RetryableDownloadError(str(error), resource=resource) from error
    if response.status == 404:
        raise _DownloadNotFoundError(url, resource=resource, response=response)
    if response.status == 429 or 500 <= response.status <= 599:
        raise _RetryableDownloadError(
            f"HTTP {response.status} for {url}",
            resource=resource,
            response=response,
        )
    if response.status < 200 or response.status >= 300:
        raise _InvalidContentError(
            f"unexpected HTTP {response.status} for {url}", response=response
        )
    if not response.body:
        raise _InvalidContentError(f"empty response for {url}", response=response)
    return _DownloadedResponse(
        body=response.body,
        status=response.status,
        headers=dict(response.headers),
    )


def _declared_checksum(payload: bytes, expected_filename: str) -> str:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise _InvalidContentError("checksum response is not ASCII") from error
    match = _CHECKSUM_PATTERN.fullmatch(text)
    if match is None or match.group(2) != expected_filename:
        raise _InvalidContentError(
            "checksum response has an unexpected format or filename"
        )
    return match.group(1).lower()


def _parse_nonnegative_int(value: str, *, field: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise _InvalidContentError(f"{field} must be a non-negative integer")
    parsed = int(value)
    if parsed > 2**63 - 1:
        raise _InvalidContentError(f"{field} exceeds signed 64-bit range")
    return parsed


def _validate_decimal(value: str, *, field: str, nonnegative: bool = False) -> None:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise _InvalidContentError(f"{field} is not a decimal") from error
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise _InvalidContentError(f"{field} has an invalid numeric value")


def _archive_object_range(plan: BinanceArchiveObjectPlan) -> TimeRange:
    boundary = plan.request.archive_object_boundary
    assert boundary is not None
    object_start = _utc_midnight(boundary.period_start)
    object_end = (
        _utc_midnight(_next_month(boundary.period_start))
        if boundary.granularity.value == "month"
        else object_start + timedelta(days=1)
    )
    return TimeRange(
        start=object_start,
        end=object_end,
    )


def _required_record_range(plan: BinanceArchiveObjectPlan) -> TimeRange:
    object_range = _archive_object_range(plan)
    return TimeRange(
        start=max(plan.request.request_range.start, object_range.start),
        end=min(plan.request.request_range.end, object_range.end),
    )


def _validate_complete_object_coverage(
    plan: BinanceArchiveObjectPlan, actual_range: TimeRange
) -> None:
    object_range = _archive_object_range(plan)
    if actual_range != object_range:
        raise _CoverageGapError(
            "archive rows do not cover the complete source object boundary"
        )


def _validate_required_coverage(
    plan: BinanceArchiveObjectPlan, actual_range: TimeRange
) -> None:
    required_range = _required_record_range(plan)
    if (
        actual_range.start > required_range.start
        or actual_range.end < required_range.end
    ):
        raise _CoverageGapError(
            "archive rows do not cover the caller range within this source object"
        )


def _parse_archive(
    plan: BinanceArchiveObjectPlan, payload: bytes
) -> tuple[pl.DataFrame, TimeRange]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1 or members[0].filename != plan.member_name:
                raise _InvalidContentError(
                    "ZIP must contain exactly the expected CSV member"
                )
            member = members[0]
            if member.flag_bits & 0x1:
                raise _InvalidContentError("encrypted ZIP members are not supported")
            if member.file_size > _MAX_ARCHIVE_BYTES:
                raise _InvalidContentError("CSV member exceeds the size limit")
            csv_payload = archive.read(member)
            if len(csv_payload) > _MAX_ARCHIVE_BYTES:
                raise _InvalidContentError("CSV member exceeds the size limit")
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as error:
        raise _InvalidContentError("archive is not a readable ZIP") from error
    try:
        text = csv_payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _InvalidContentError("CSV member is not UTF-8") from error

    parsed_rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if parsed_rows and tuple(parsed_rows[0]) == _COLUMNS:
        parsed_rows = parsed_rows[1:]
    if not parsed_rows:
        raise _InvalidContentError("CSV contains no data rows")

    columns: dict[str, list[str | int]] = {name: [] for name in _COLUMNS}
    previous_open: int | None = None
    object_range = _archive_object_range(plan)
    object_start_ms = int(object_range.start.timestamp() * 1000)
    object_end_ms = int(object_range.end.timestamp() * 1000)
    for row_number, row in enumerate(parsed_rows, start=1):
        if len(row) != len(_COLUMNS):
            raise _InvalidContentError(f"CSV row {row_number} does not have 12 columns")
        open_time = _parse_nonnegative_int(row[0], field="open_time")
        close_time = _parse_nonnegative_int(row[6], field="close_time")
        count = _parse_nonnegative_int(row[8], field="count")
        try:
            datetime.fromtimestamp(open_time / 1000, tz=UTC)
            datetime.fromtimestamp(close_time / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise _InvalidContentError("row timestamp is not interpretable") from error
        if (
            open_time % _ONE_MINUTE_MS != 0
            or close_time != open_time + _ONE_MINUTE_MS - 1
        ):
            raise _InvalidContentError(
                "row does not have valid 1m timestamp boundaries"
            )
        if not object_start_ms <= open_time < object_end_ms:
            raise _InvalidContentError(
                "row open_time is outside the archive object boundary"
            )
        if previous_open is not None:
            if open_time <= previous_open:
                raise _InvalidContentError(
                    "row open_time values must be strictly increasing"
                )
            if open_time != previous_open + _ONE_MINUTE_MS:
                raise _CoverageGapError("archive rows contain a missing 1m timestamp")
        previous_open = open_time
        for index in (1, 2, 3, 4, 5, 7, 9, 10, 11):
            _validate_decimal(
                row[index],
                field=_COLUMNS[index],
                nonnegative=index in (5, 7, 9, 10),
            )
        typed: tuple[str | int, ...] = (
            open_time,
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            close_time,
            row[7],
            count,
            row[9],
            row[10],
            row[11],
        )
        for name, value in zip(_COLUMNS, typed, strict=True):
            columns[name].append(value)

    frame = pl.DataFrame(columns, schema=_DTYPES)
    first_open = int(frame.item(0, "open_time"))
    last_open = int(frame.item(frame.height - 1, "open_time"))
    actual_range = TimeRange(
        start=datetime.fromtimestamp(first_open / 1000, tz=UTC),
        end=datetime.fromtimestamp((last_open + _ONE_MINUTE_MS) / 1000, tz=UTC),
    )
    _validate_complete_object_coverage(plan, actual_range)
    _validate_required_coverage(plan, actual_range)
    return frame, actual_range


class BinanceContractKlineBackfill:
    """Execute the official archive path without credentials or import-time I/O."""

    def __init__(
        self,
        store: RawStore,
        *,
        http_get: ArchiveHttpGet | None = None,
        timeout: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, RawStore):
            raise TypeError("store must be a RawStore")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and greater than zero")
        self._store = store
        self._http_get = http_get or _default_http_get
        self._timeout = timeout
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(
        self, instrument: InstrumentId, request_range: TimeRange
    ) -> BinanceContractKlineRunResult:
        plans = plan_binance_contract_kline_archives(instrument, request_range)
        results = tuple(self._process(plan) for plan in plans)
        return BinanceContractKlineRunResult(
            request_range=request_range, objects=results
        )

    @staticmethod
    def _to_raw_response(
        response: ArchiveHttpResponse | _DownloadedResponse | None,
    ) -> RawAcquisitionResponse | None:
        if response is None:
            return None
        return RawAcquisitionResponse(
            status=response.status,
            headers=response.headers,
            body=response.body,
        )

    def _failure_result(
        self,
        plan: BinanceContractKlinePlan,
        status: BinanceContractKlineStatus,
        detail: str,
        *,
        artifact_path: Path | None = None,
        source_response: RawAcquisitionResponse | None = None,
        checksum_response: RawAcquisitionResponse | None = None,
    ) -> BinanceContractKlineObjectResult:
        request = plan.request
        manifest = RawAcquisitionManifest(
            manifest_schema_version=1,
            completed=False,
            object_identity=RawObjectIdentity.from_request(request),
            caller_request_range=request.request_range,
            status=status.value,
            detail=detail,
            recorded_at=self._clock(),
            source_url=plan.url if isinstance(plan, BinanceArchiveObjectPlan) else None,
            checksum_url=(
                plan.checksum_url
                if isinstance(plan, BinanceArchiveObjectPlan)
                else None
            ),
            source_http_status=(
                source_response.status if source_response is not None else None
            ),
            source_http_headers=(
                source_response.headers if source_response is not None else {}
            ),
            source_body_sha256=(
                source_response.body_sha256 if source_response is not None else None
            ),
            checksum_http_status=(
                checksum_response.status if checksum_response is not None else None
            ),
            checksum_http_headers=(
                checksum_response.headers if checksum_response is not None else {}
            ),
            checksum_response_sha256=(
                checksum_response.body_sha256 if checksum_response is not None else None
            ),
        )
        self._store.write_acquisition_manifest(
            manifest,
            source_response=source_response,
            checksum_response=checksum_response,
        )
        return BinanceContractKlineObjectResult(
            plan, status, artifact_path=artifact_path, detail=detail
        )

    def _process(
        self, plan: BinanceContractKlinePlan
    ) -> BinanceContractKlineObjectResult:
        if isinstance(plan, BinanceArchiveCoverageGapPlan):
            return self._failure_result(
                plan, BinanceContractKlineStatus.COVERAGE_GAP, plan.detail
            )

        existing: RawArtifact | None = None
        try:
            existing = self._store.read_request(plan.request)
        except RawArtifactNotFoundError:
            pass
        except (RawArtifactValidationError, OSError) as error:
            return self._failure_result(
                plan, BinanceContractKlineStatus.LOCAL_FAILURE, str(error)
            )
        else:
            try:
                _validate_complete_object_coverage(
                    plan, existing.manifest.actual_record_range
                )
                _validate_required_coverage(plan, existing.manifest.actual_record_range)
            except _CoverageGapError as error:
                return self._failure_result(
                    plan,
                    BinanceContractKlineStatus.COVERAGE_GAP,
                    str(error),
                    artifact_path=existing.path,
                )

        checksum_payload: _DownloadedResponse | None = None
        archive_payload: _DownloadedResponse | None = None
        checksum_response: RawAcquisitionResponse | None = None
        archive_response: RawAcquisitionResponse | None = None
        resource = "checksum"
        try:
            checksum_payload = _download(
                self._http_get,
                plan.checksum_url,
                self._timeout,
                resource="checksum",
            )
            declared = _declared_checksum(
                checksum_payload.body, plan.url.rsplit("/", 1)[-1]
            )
            resource = "archive"
            archive_payload = _download(
                self._http_get,
                plan.url,
                self._timeout,
                resource="archive",
            )
            actual = hashlib.sha256(archive_payload.body).hexdigest()
            if actual != declared:
                raise _InvalidContentError(
                    "archive SHA-256 does not match upstream checksum"
                )
            frame, actual_range = _parse_archive(plan, archive_payload.body)
            source = RawSourceObject(
                request=plan.request,
                rows=frame,
                actual_record_range=actual_range,
                raw_schema_identifier=_SCHEMA_IDENTIFIER,
                producer_version=_PRODUCER_VERSION,
                upstream_checksum=f"sha256:{declared}",
                upstream_revision=plan.url,
                provenance=RawSourceProvenance(
                    object_key=plan.object_key,
                    source_url=plan.url,
                    acquired_at=self._clock(),
                    source_http_status=archive_payload.status,
                    source_http_headers=archive_payload.headers,
                    checksum_url=plan.checksum_url,
                    checksum_http_status=checksum_payload.status,
                    checksum_http_headers=checksum_payload.headers,
                    checksum_response_sha256=hashlib.sha256(
                        checksum_payload.body
                    ).hexdigest(),
                    archive_sha256=actual,
                    csv_member=plan.member_name,
                    validation_evidence=(
                        "checksum_response_verified",
                        "archive_sha256_matches_checksum",
                        "zip_member_structure_verified",
                        "csv_schema_and_rows_verified",
                        "source_object_coverage_verified",
                    ),
                ),
                archive_payload=archive_payload.body,
                checksum_response_body=checksum_payload.body,
            )
            artifact = self._store.write(source)
        except _DownloadNotFoundError as error:
            status = (
                BinanceContractKlineStatus.CHECKSUM_NOT_FOUND
                if error.resource == "checksum"
                else BinanceContractKlineStatus.NOT_FOUND
            )
            if error.resource == "checksum":
                checksum_response = self._to_raw_response(error.response)
            else:
                archive_response = self._to_raw_response(error.response)
            return self._failure_result(
                plan,
                status,
                str(error),
                source_response=self._to_raw_response(archive_payload)
                or archive_response,
                checksum_response=self._to_raw_response(checksum_payload)
                or checksum_response,
            )
        except _RetryableDownloadError as error:
            if error.resource == "checksum":
                checksum_response = self._to_raw_response(error.response)
            else:
                archive_response = self._to_raw_response(error.response)
            return self._failure_result(
                plan,
                BinanceContractKlineStatus.RETRYABLE_FAILURE,
                str(error),
                source_response=self._to_raw_response(archive_payload)
                or archive_response,
                checksum_response=self._to_raw_response(checksum_payload)
                or checksum_response,
            )
        except _CoverageGapError as error:
            return self._failure_result(
                plan,
                BinanceContractKlineStatus.COVERAGE_GAP,
                str(error),
                source_response=self._to_raw_response(archive_payload),
                checksum_response=self._to_raw_response(checksum_payload),
            )
        except (csv.Error, _InvalidContentError) as error:
            response = (
                error.response if isinstance(error, _InvalidContentError) else None
            )
            if response is not None:
                if resource == "checksum" and checksum_payload is None:
                    checksum_payload = _DownloadedResponse(
                        body=response.body,
                        status=response.status,
                        headers=response.headers,
                    )
                elif resource == "archive" and archive_payload is None:
                    archive_payload = _DownloadedResponse(
                        body=response.body,
                        status=response.status,
                        headers=response.headers,
                    )
            return self._failure_result(
                plan,
                BinanceContractKlineStatus.INVALID_CONTENT,
                str(error),
                source_response=self._to_raw_response(archive_payload),
                checksum_response=self._to_raw_response(checksum_payload),
            )
        except RawArtifactConflictError as error:
            return self._failure_result(
                plan,
                BinanceContractKlineStatus.CONFLICT,
                str(error),
                source_response=self._to_raw_response(archive_payload),
                checksum_response=self._to_raw_response(checksum_payload),
            )
        except (
            RawArtifactNotFoundError,
            RawArtifactValidationError,
            OSError,
        ) as error:
            return self._failure_result(
                plan,
                BinanceContractKlineStatus.LOCAL_FAILURE,
                str(error),
                source_response=self._to_raw_response(archive_payload),
                checksum_response=self._to_raw_response(checksum_payload),
            )
        status = (
            BinanceContractKlineStatus.EXISTING
            if existing is not None
            else BinanceContractKlineStatus.PUBLISHED
        )
        return BinanceContractKlineObjectResult(
            plan, status, artifact_path=artifact.path
        )
