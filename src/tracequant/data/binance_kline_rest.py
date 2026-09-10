"""Bounded Binance USDⓈ-M 1m Kline REST acquisition.

The public acquisition boundary consumes an already verified, finite coverage
conclusion.  It never extends that conclusion from the current date and never
uses synthetic fixtures as source-availability evidence.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol

import polars as pl

from tracequant.core.time import to_utc
from tracequant.data.public_history import (
    BinanceKlineInterval,
    BinancePriceIndexId,
    BinancePublicHistorySubject,
)
from tracequant.data.public_history_rest import (
    BinanceRestEndpoint,
    BinanceRestPageProvenance,
    BinanceRestPageRequest,
)
from tracequant.data.raw_store import (
    RawAcquisitionManifest,
    RawAcquisitionResponse,
    RawArtifactConflictError,
    RawArtifactValidationError,
    RawObjectIdentity,
    RawRestPageSourceObject,
    RawRevisionIdentity,
    RawStore,
    RawStoreError,
)
from tracequant.domain import InstrumentId, TimeRange

__all__ = [
    "BinanceKlineRestAcquisition",
    "BinanceKlineRestAttemptResult",
    "BinanceKlineRestBudget",
    "BinanceKlineRestCoverage",
    "BinanceKlineRestCoverageStatus",
    "BinanceKlineRestHttpGet",
    "BinanceKlineRestHttpResponse",
    "BinanceKlineRestPageResult",
    "BinanceKlineRestRunResult",
    "BinanceKlineRestStatus",
]

_BASE_URL: Final = "https://fapi.binance.com"
_PRODUCER_VERSION: Final = "tracequant/0.1.0"
_MINUTE_MS: Final = 60_000
_APPROVED_COVERAGE_VERSION: Final = "issue-295-probe-run-2026-09-09T18:33:30.868474Z"
_APPROVED_COVERAGE_REFERENCE: Final = (
    "docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json"
)
_APPROVED_COVERAGE_SHA256: Final = (
    "30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45"
)
_APPROVED_START_MS: Final = 1_788_975_180_000
_APPROVED_END_MS: Final = 1_788_978_780_000
_ALLOWED_SUBJECTS: Final = frozenset({"BTCUSDT", "ETHUSDT"})
_RESPONSE_HEADERS: Final = frozenset(
    {
        "content-type",
        "date",
        "retry-after",
        "x-mbx-used-weight",
        "x-mbx-used-weight-1m",
    }
)
_CONTRACT_COLUMNS: Final = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
_PRICE_COLUMNS: Final = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "ignore_5",
    "close_time",
    "ignore_7",
    "ignore_8",
    "ignore_9",
    "ignore_10",
    "ignore_11",
)
_SCHEMA_IDENTIFIERS: Final = {
    BinanceRestEndpoint.CONTRACT_KLINES: "binance.um.contract-kline.rest-json.v1",
    BinanceRestEndpoint.MARK_PRICE_KLINES: ("binance.um.mark-price-kline.rest-json.v1"),
    BinanceRestEndpoint.INDEX_PRICE_KLINES: (
        "binance.um.index-price-kline.rest-json.v1"
    ),
}


@dataclass(frozen=True, slots=True)
class BinanceKlineRestHttpResponse:
    """Exact bounded bytes returned by an injectable public HTTP transport."""

    status: int
    body: bytes
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("status must be an HTTP status code")
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")
        if not isinstance(self.headers, Mapping):
            raise TypeError("headers must be a mapping")
        normalized: dict[str, str] = {}
        for name, value in self.headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("HTTP header names and values must be strings")
            normalized[name] = value
        object.__setattr__(self, "headers", MappingProxyType(normalized))


class BinanceKlineRestHttpGet(Protocol):
    def __call__(
        self, url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _default_http_get(
    url: str, timeout: float, maximum_response_bytes: int
) -> BinanceKlineRestHttpResponse:
    request = urllib.request.Request(url, headers={"User-Agent": _PRODUCER_VERSION})
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(maximum_response_bytes + 1)
            return BinanceKlineRestHttpResponse(
                status=response.status,
                body=body,
                headers=dict(response.headers.items()),
            )
    except urllib.error.HTTPError as error:
        body = error.read(maximum_response_bytes + 1)
        return BinanceKlineRestHttpResponse(
            status=error.code,
            body=body,
            headers=dict(error.headers.items()) if error.headers is not None else {},
        )


class BinanceKlineRestCoverageStatus(StrEnum):
    """Source-research conclusion consumed before any network access."""

    SUPPORTED = "supported"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class BinanceKlineRestCoverage:
    """Finite per-endpoint and per-subject REST availability evidence."""

    status: BinanceKlineRestCoverageStatus
    endpoint: BinanceRestEndpoint
    subject: BinancePublicHistorySubject
    allowed_range: TimeRange
    evidence_version: str
    evidence_reference: str
    evidence_sha256: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.status, BinanceKlineRestCoverageStatus):
            try:
                status = BinanceKlineRestCoverageStatus(self.status)
            except (TypeError, ValueError) as error:
                raise ValueError("unsupported REST coverage status") from error
            object.__setattr__(self, "status", status)
        if not isinstance(self.endpoint, BinanceRestEndpoint):
            try:
                endpoint = BinanceRestEndpoint(self.endpoint)
            except (TypeError, ValueError) as error:
                raise ValueError("unsupported REST coverage endpoint") from error
            object.__setattr__(self, "endpoint", endpoint)
        if not isinstance(self.subject, (InstrumentId, BinancePriceIndexId)):
            raise TypeError(
                "coverage subject must be an instrument or price-index pair"
            )
        if not isinstance(self.allowed_range, TimeRange):
            raise TypeError("allowed_range must be a TimeRange")
        for field in ("evidence_version", "evidence_reference"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be a non-empty string")
        if (
            not isinstance(self.evidence_sha256, str)
            or len(self.evidence_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.evidence_sha256
            )
        ):
            raise ValueError("evidence_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime")
        try:
            observed_at = to_utc(self.observed_at)
        except ValueError as error:
            raise ValueError("observed_at must be timezone-aware") from error
        object.__setattr__(self, "observed_at", observed_at)


@dataclass(frozen=True, slots=True)
class BinanceKlineRestBudget:
    """Finite network, pagination, retry, elapsed-time, and byte limits."""

    timeout_seconds: float
    maximum_pages: int
    maximum_attempts_per_page: int
    maximum_elapsed_seconds: float
    maximum_response_bytes: int

    def __post_init__(self) -> None:
        for field in ("timeout_seconds", "maximum_elapsed_seconds"):
            value = getattr(self, field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"{field} must be a number")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be finite and greater than zero")
            object.__setattr__(self, field, float(value))
        for field in (
            "maximum_pages",
            "maximum_attempts_per_page",
            "maximum_response_bytes",
        ):
            value = getattr(self, field)
            if type(value) is not int:
                raise TypeError(f"{field} must be an integer")
            if value <= 0:
                raise ValueError(f"{field} must be greater than zero")


class BinanceKlineRestStatus(StrEnum):
    COMPLETE = "complete"
    REST_BOUNDARY_UNKNOWN = "rest_boundary_unknown"
    UNSUPPORTED = "unsupported"
    INVALID_REQUEST = "invalid_request"
    LEGAL_EMPTY = "legal_empty"
    INVALID_RESPONSE = "invalid_response"
    RETRYABLE_FAILURE = "retryable_failure"
    RETRY_EXHAUSTED = "retry_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LOCAL_FAILURE = "local_failure"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class BinanceKlineRestAttemptResult:
    """One HTTP or transport attempt without resetting the page identity."""

    attempt_number: int
    page_attempt_number: int
    outcome: str
    http_status: int | None
    response_sha256: str | None
    detail: str
    waited_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class BinanceKlineRestPageResult:
    """One requested REST page and its exact Raw publication reference."""

    request: BinanceRestPageRequest
    attempts: tuple[BinanceKlineRestAttemptResult, ...]
    status: BinanceKlineRestStatus
    short_page: bool
    record_count: int
    actual_record_range: TimeRange | None
    response_sha256: str | None
    revision: RawRevisionIdentity | None
    artifact_path: Path | None
    detail: str


@dataclass(frozen=True, slots=True)
class BinanceKlineRestRunResult:
    """Bounded acquisition result; only ``COMPLETE`` satisfies the full range."""

    status: BinanceKlineRestStatus
    coverage: BinanceKlineRestCoverage | None
    pages: tuple[BinanceKlineRestPageResult, ...]
    requested_range: TimeRange
    actual_record_range: TimeRange | None
    unmet_range: TimeRange | None
    termination_reason: str
    attempts_used: int
    pages_used: int
    elapsed_seconds: float

    @property
    def complete(self) -> bool:
        return self.status is BinanceKlineRestStatus.COMPLETE


@dataclass(slots=True)
class _BudgetTracker:
    budget: BinanceKlineRestBudget
    coverage: BinanceKlineRestCoverage | None
    monotonic_clock: Callable[[], float]
    started_at: float
    transport_seconds: float = 0.0
    waited_seconds: float = 0.0
    attempts: int = 0
    pages: int = 0

    @property
    def elapsed(self) -> float:
        wall_elapsed = max(0.0, self.monotonic_clock() - self.started_at)
        return max(wall_elapsed, self.transport_seconds + self.waited_seconds)

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget.maximum_elapsed_seconds - self.elapsed)


class _InvalidPageError(ValueError):
    pass


def _datetime_ms(value: datetime) -> int:
    normalized = to_utc(value)
    return int(normalized.timestamp() * 1_000)


def _sanitize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for name, value in headers.items():
        normalized = name.strip().lower()
        header_value = value.strip()
        if (
            normalized in _RESPONSE_HEADERS
            and normalized not in sanitized
            and len(header_value) <= 1024
        ):
            sanitized[normalized] = header_value
    return sanitized


def _response_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _require_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise _InvalidPageError(f"{field} must be a non-negative integer")
    return value


def _require_finite_numeric_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _InvalidPageError(f"{field} must be a non-empty numeric string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise _InvalidPageError(f"{field} must be a finite numeric string") from error
    if not number.is_finite():
        raise _InvalidPageError(f"{field} must be a finite numeric string")
    return value


def _parse_kline_page(
    request: BinanceRestPageRequest,
    body: bytes,
    prior_rows: Mapping[int, tuple[object, ...]],
) -> tuple[pl.DataFrame, TimeRange, dict[int, tuple[object, ...]]]:
    try:
        decoded = body.decode("utf-8")
        payload = json.loads(
            decoded,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise _InvalidPageError("response body is not strict UTF-8 JSON") from error
    if not isinstance(payload, list):
        raise _InvalidPageError("response JSON must be an array, not an error object")
    if not payload:
        raise _InvalidPageError("empty")
    if len(payload) > request.limit:
        raise _InvalidPageError("response contains more rows than the page limit")

    seen: dict[int, tuple[object, ...]] = {}
    previous_seen: int | None = None
    for row_number, raw_row in enumerate(payload):
        if not isinstance(raw_row, list) or len(raw_row) != 12:
            raise _InvalidPageError(
                f"row {row_number} must contain exactly 12 array elements"
            )
        open_time = _require_int(raw_row[0], field=f"row {row_number} open_time")
        frozen_row = tuple(raw_row)
        prior = prior_rows.get(open_time)
        if prior is not None:
            detail = "conflicting duplicate" if prior != frozen_row else "duplicate"
            raise _InvalidPageError(
                f"row {row_number} has a cross-page {detail} open_time"
            )
        previous = seen.get(open_time)
        if previous is not None:
            detail = "conflicting duplicate" if previous != frozen_row else "duplicate"
            raise _InvalidPageError(f"row {row_number} has a {detail} open_time")
        if previous_seen is not None and open_time < previous_seen:
            raise _InvalidPageError(f"row {row_number} is out of order")
        seen[open_time] = frozen_row
        previous_seen = open_time

    rows: list[list[object]] = []
    previous_open: int | None = None
    assert request.page_boundary is not None
    expected_open = request.page_boundary.start_time_ms
    for row_number, raw_row in enumerate(payload):
        assert isinstance(raw_row, list)
        open_time = _require_int(raw_row[0], field=f"row {row_number} open_time")
        close_time = _require_int(raw_row[6], field=f"row {row_number} close_time")
        if open_time % _MINUTE_MS != 0:
            raise _InvalidPageError(f"row {row_number} open_time is not minute-aligned")
        if close_time != open_time + _MINUTE_MS - 1:
            raise _InvalidPageError(
                f"row {row_number} does not describe one complete minute"
            )
        if not (
            request.page_boundary.start_time_ms
            <= open_time
            <= request.page_boundary.end_time_ms
        ):
            raise _InvalidPageError(f"row {row_number} is outside the page boundary")
        if open_time != expected_open:
            raise _InvalidPageError(f"row {row_number} leaves a missing minute")
        expected_open += _MINUTE_MS
        previous_open = open_time

        parsed: list[object] = [open_time]
        for index, name in zip(range(1, 6), _CONTRACT_COLUMNS[1:6], strict=True):
            parsed.append(
                _require_finite_numeric_string(
                    raw_row[index], field=f"row {row_number} {name}"
                )
            )
        parsed.append(close_time)
        parsed.append(
            _require_finite_numeric_string(
                raw_row[7], field=f"row {row_number} field_7"
            )
        )
        parsed.append(_require_int(raw_row[8], field=f"row {row_number} field_8"))
        for index in range(9, 12):
            parsed.append(
                _require_finite_numeric_string(
                    raw_row[index], field=f"row {row_number} field_{index}"
                )
            )
        rows.append(parsed)

    columns = (
        _CONTRACT_COLUMNS
        if request.endpoint is BinanceRestEndpoint.CONTRACT_KLINES
        else _PRICE_COLUMNS
    )
    frame = pl.DataFrame(rows, schema=list(columns), orient="row")
    assert previous_open is not None
    first_open = next(iter(seen))
    actual_range = TimeRange(
        start=datetime.fromtimestamp(first_open / 1_000, tz=UTC),
        end=datetime.fromtimestamp((previous_open + _MINUTE_MS) / 1_000, tz=UTC),
    )
    return frame, actual_range, seen


def _retry_after_seconds(value: str | None, *, now: datetime) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isascii() and stripped.isdigit() and len(stripped) <= 10:
        seconds = float(int(stripped))
    else:
        try:
            target = parsedate_to_datetime(stripped)
            if target.tzinfo is None:
                return None
            seconds = (to_utc(target) - to_utc(now)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


class BinanceKlineRestAcquisition:
    """Acquire verified Binance Kline pages and publish immutable Raw revisions."""

    def __init__(
        self,
        store: RawStore,
        *,
        http_get: BinanceKlineRestHttpGet | None = None,
        clock: Callable[[], datetime] | None = None,
        wait: Callable[[float], None] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(store, RawStore):
            raise TypeError("store must be a RawStore")
        self._store = store
        self._http_get = http_get or _default_http_get
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wait = wait or time.sleep
        self._monotonic_clock = monotonic_clock or time.monotonic

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return a datetime")
        try:
            return to_utc(value)
        except ValueError as error:
            raise ValueError("clock must return a timezone-aware datetime") from error

    @staticmethod
    def _request_url(request: BinanceRestPageRequest) -> str:
        query = urllib.parse.urlencode(sorted(request.normalized_params.items()))
        return f"{_BASE_URL}{request.endpoint.value}?{query}"

    @staticmethod
    def _coverage_failure(
        request: BinanceRestPageRequest,
        coverage: BinanceKlineRestCoverage | None,
    ) -> tuple[BinanceKlineRestStatus, str] | None:
        if coverage is None:
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "REST coverage evidence is missing",
            )
        if coverage.status is BinanceKlineRestCoverageStatus.UNKNOWN:
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "REST coverage conclusion is unknown",
            )
        if coverage.status is BinanceKlineRestCoverageStatus.UNSUPPORTED:
            return (
                BinanceKlineRestStatus.UNSUPPORTED,
                "REST coverage conclusion is unsupported",
            )
        if request.endpoint != coverage.endpoint or request.subject != coverage.subject:
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "coverage endpoint or typed subject does not match the request",
            )
        if (
            coverage.evidence_version != _APPROVED_COVERAGE_VERSION
            or coverage.evidence_reference != _APPROVED_COVERAGE_REFERENCE
            or coverage.evidence_sha256 != _APPROVED_COVERAGE_SHA256
        ):
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "coverage evidence binding is missing or unrecognized",
            )
        coverage_start = _datetime_ms(coverage.allowed_range.start)
        coverage_end = _datetime_ms(coverage.allowed_range.end)
        if coverage_start < _APPROVED_START_MS or coverage_end > _APPROVED_END_MS:
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "coverage range extends beyond the frozen source observation",
            )
        request_start = _datetime_ms(request.caller_range.start)
        request_end = _datetime_ms(request.caller_range.end)
        if request_start < coverage_start or request_end > coverage_end:
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "request range is outside the supplied coverage range",
            )
        return None

    def _request_failure(
        self, request: BinanceRestPageRequest
    ) -> tuple[BinanceKlineRestStatus, str] | None:
        if request.endpoint not in _SCHEMA_IDENTIFIERS:
            return (
                BinanceKlineRestStatus.UNSUPPORTED,
                "only contract, mark-price, and index-price Klines are supported",
            )
        if request.interval is not BinanceKlineInterval.ONE_MINUTE:
            return (BinanceKlineRestStatus.UNSUPPORTED, "only 1m Klines are supported")
        if str(request.subject) not in _ALLOWED_SUBJECTS:
            return (
                BinanceKlineRestStatus.UNSUPPORTED,
                "only BTCUSDT and ETHUSDT are supported",
            )
        if request.endpoint is BinanceRestEndpoint.INDEX_PRICE_KLINES:
            if not isinstance(request.subject, BinancePriceIndexId):
                return (
                    BinanceKlineRestStatus.UNSUPPORTED,
                    "index-price Klines require a typed price-index pair",
                )
        elif not isinstance(request.subject, InstrumentId):
            return (
                BinanceKlineRestStatus.UNSUPPORTED,
                "contract and mark-price Klines require a typed instrument",
            )
        start_ms = _datetime_ms(request.caller_range.start)
        end_ms = _datetime_ms(request.caller_range.end)
        if start_ms % _MINUTE_MS or end_ms % _MINUTE_MS:
            return (
                BinanceKlineRestStatus.INVALID_REQUEST,
                "caller range must be aligned to complete UTC minutes",
            )
        if request.page_boundary != request.caller_bounds:
            return (
                BinanceKlineRestStatus.INVALID_REQUEST,
                "public run must start at the complete normalized caller range",
            )
        closed_bar_end_ms = (_datetime_ms(self._now()) // _MINUTE_MS) * _MINUTE_MS
        if end_ms > closed_bar_end_ms:
            return (
                BinanceKlineRestStatus.INVALID_REQUEST,
                "caller range includes the current unclosed 1m bar",
            )
        return None

    def _record_attempt(
        self,
        request: BinanceRestPageRequest,
        *,
        status: BinanceKlineRestStatus,
        detail: str,
        source_url: str,
        response: BinanceKlineRestHttpResponse | None,
    ) -> None:
        raw_response = (
            None
            if response is None
            else RawAcquisitionResponse(
                status=response.status,
                headers=_sanitize_headers(response.headers),
                body=response.body,
            )
        )
        manifest = RawAcquisitionManifest(
            manifest_schema_version=1,
            completed=False,
            object_identity=RawObjectIdentity.from_rest_page_request(request),
            caller_request_range=request.caller_range,
            status=status.value,
            detail=detail,
            recorded_at=self._now(),
            source_url=source_url,
            checksum_url=None,
            source_http_status=(
                raw_response.status if raw_response is not None else None
            ),
            source_http_headers=(
                raw_response.headers if raw_response is not None else {}
            ),
            source_body_sha256=(
                raw_response.body_sha256 if raw_response is not None else None
            ),
            checksum_http_status=None,
            checksum_http_headers={},
            checksum_response_sha256=None,
        )
        self._store.write_acquisition_manifest(
            manifest,
            source_response=raw_response,
        )

    @staticmethod
    def _unmet_range(requested: TimeRange, cursor_ms: int) -> TimeRange | None:
        end_ms = _datetime_ms(requested.end)
        if cursor_ms >= end_ms:
            return None
        return TimeRange(
            start=datetime.fromtimestamp(cursor_ms / 1_000, tz=UTC),
            end=requested.end,
        )

    def _finish(
        self,
        *,
        status: BinanceKlineRestStatus,
        request: BinanceRestPageRequest,
        pages: list[BinanceKlineRestPageResult],
        tracker: _BudgetTracker,
        cursor_ms: int,
        reason: str,
    ) -> BinanceKlineRestRunResult:
        actual_range = (
            None
            if not pages or pages[0].actual_record_range is None
            else TimeRange(
                start=pages[0].actual_record_range.start,
                end=next(
                    page.actual_record_range.end
                    for page in reversed(pages)
                    if page.actual_record_range is not None
                ),
            )
        )
        return BinanceKlineRestRunResult(
            status=status,
            coverage=tracker.coverage,
            pages=tuple(pages),
            requested_range=request.caller_range,
            actual_record_range=actual_range,
            unmet_range=self._unmet_range(request.caller_range, cursor_ms),
            termination_reason=reason,
            attempts_used=tracker.attempts,
            pages_used=tracker.pages,
            elapsed_seconds=tracker.elapsed,
        )

    def run(
        self,
        request: BinanceRestPageRequest,
        coverage: BinanceKlineRestCoverage | None,
        budget: BinanceKlineRestBudget,
    ) -> BinanceKlineRestRunResult:
        """Fetch, validate, and publish every page needed for the caller range."""
        if not isinstance(request, BinanceRestPageRequest):
            raise TypeError("request must be a BinanceRestPageRequest")
        if coverage is not None and not isinstance(coverage, BinanceKlineRestCoverage):
            raise TypeError("coverage must be a BinanceKlineRestCoverage or None")
        if not isinstance(budget, BinanceKlineRestBudget):
            raise TypeError("budget must be a BinanceKlineRestBudget")
        tracker = _BudgetTracker(
            budget=budget,
            coverage=coverage,
            monotonic_clock=self._monotonic_clock,
            started_at=self._monotonic_clock(),
        )
        pages: list[BinanceKlineRestPageResult] = []
        observed_rows: dict[int, tuple[object, ...]] = {}
        cursor_ms = request.caller_bounds.start_time_ms

        failure = self._request_failure(request)
        if failure is None:
            failure = self._coverage_failure(request, coverage)
        if failure is not None:
            status, detail = failure
            return self._finish(
                status=status,
                request=request,
                pages=pages,
                tracker=tracker,
                cursor_ms=cursor_ms,
                reason=detail,
            )

        current = request
        caller_end_ms = request.caller_bounds.end_time_ms + 1
        while cursor_ms < caller_end_ms:
            if tracker.pages >= budget.maximum_pages or tracker.remaining <= 0:
                return self._finish(
                    status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                    request=request,
                    pages=pages,
                    tracker=tracker,
                    cursor_ms=cursor_ms,
                    reason="page or total elapsed-time budget exhausted",
                )
            tracker.pages += 1
            attempts: list[BinanceKlineRestAttemptResult] = []
            response: BinanceKlineRestHttpResponse | None = None
            url = self._request_url(current)

            for page_attempt in range(1, budget.maximum_attempts_per_page + 1):
                if tracker.remaining <= 0:
                    return self._finish(
                        status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                        request=request,
                        pages=pages,
                        tracker=tracker,
                        cursor_ms=cursor_ms,
                        reason="total elapsed-time budget exhausted before HTTP attempt",
                    )
                tracker.attempts += 1
                started = self._monotonic_clock()
                try:
                    response = self._http_get(
                        url,
                        min(budget.timeout_seconds, tracker.remaining),
                        budget.maximum_response_bytes,
                    )
                except (
                    TimeoutError,
                    ConnectionError,
                    OSError,
                    http.client.HTTPException,
                    urllib.error.URLError,
                ) as error:
                    tracker.transport_seconds += max(
                        0.0, self._monotonic_clock() - started
                    )
                    detail = str(error).strip() or type(error).__name__
                    try:
                        self._record_attempt(
                            current,
                            status=BinanceKlineRestStatus.RETRYABLE_FAILURE,
                            detail=f"transport attempt {page_attempt}: {detail}",
                            source_url=url,
                            response=None,
                        )
                    except (RawStoreError, OSError, ValueError) as store_error:
                        return self._finish(
                            status=BinanceKlineRestStatus.LOCAL_FAILURE,
                            request=request,
                            pages=pages,
                            tracker=tracker,
                            cursor_ms=cursor_ms,
                            reason=f"failed to persist transport evidence: {store_error}",
                        )
                    delay = float(min(2 ** (page_attempt - 1), 60))
                    will_retry = page_attempt < budget.maximum_attempts_per_page
                    attempts.append(
                        BinanceKlineRestAttemptResult(
                            attempt_number=tracker.attempts,
                            page_attempt_number=page_attempt,
                            outcome="transport_error",
                            http_status=None,
                            response_sha256=None,
                            detail=detail,
                            waited_seconds=(
                                delay
                                if will_retry and delay <= tracker.remaining
                                else 0.0
                            ),
                        )
                    )
                    response = None
                    if not will_retry:
                        break
                    if delay > tracker.remaining:
                        pages.append(
                            BinanceKlineRestPageResult(
                                request=current,
                                attempts=tuple(attempts),
                                status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                                short_page=False,
                                record_count=0,
                                actual_record_range=None,
                                response_sha256=None,
                                revision=None,
                                artifact_path=None,
                                detail="transport backoff exceeds remaining elapsed budget",
                            )
                        )
                        return self._finish(
                            status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                            request=request,
                            pages=pages,
                            tracker=tracker,
                            cursor_ms=cursor_ms,
                            reason="retry wait cannot fit in the elapsed-time budget",
                        )
                    try:
                        self._wait(delay)
                    except Exception as wait_error:
                        return self._finish(
                            status=BinanceKlineRestStatus.LOCAL_FAILURE,
                            request=request,
                            pages=pages,
                            tracker=tracker,
                            cursor_ms=cursor_ms,
                            reason=f"retry wait failed: {wait_error}",
                        )
                    tracker.waited_seconds += delay
                    continue
                else:
                    tracker.transport_seconds += max(
                        0.0, self._monotonic_clock() - started
                    )
                    if len(response.body) > budget.maximum_response_bytes:
                        bounded = BinanceKlineRestHttpResponse(
                            status=response.status,
                            body=response.body[: budget.maximum_response_bytes + 1],
                            headers=response.headers,
                        )
                        detail = "response exceeds maximum_response_bytes"
                        try:
                            self._record_attempt(
                                current,
                                status=BinanceKlineRestStatus.INVALID_RESPONSE,
                                detail=detail,
                                source_url=url,
                                response=bounded,
                            )
                        except (RawStoreError, OSError, ValueError) as store_error:
                            return self._finish(
                                status=BinanceKlineRestStatus.LOCAL_FAILURE,
                                request=request,
                                pages=pages,
                                tracker=tracker,
                                cursor_ms=cursor_ms,
                                reason=f"failed to persist oversized response: {store_error}",
                            )
                        attempts.append(
                            BinanceKlineRestAttemptResult(
                                attempt_number=tracker.attempts,
                                page_attempt_number=page_attempt,
                                outcome="response_too_large",
                                http_status=bounded.status,
                                response_sha256=_response_digest(bounded.body),
                                detail=detail,
                            )
                        )
                        pages.append(
                            BinanceKlineRestPageResult(
                                request=current,
                                attempts=tuple(attempts),
                                status=BinanceKlineRestStatus.INVALID_RESPONSE,
                                short_page=False,
                                record_count=0,
                                actual_record_range=None,
                                response_sha256=_response_digest(bounded.body),
                                revision=None,
                                artifact_path=None,
                                detail=detail,
                            )
                        )
                        return self._finish(
                            status=BinanceKlineRestStatus.INVALID_RESPONSE,
                            request=request,
                            pages=pages,
                            tracker=tracker,
                            cursor_ms=cursor_ms,
                            reason=detail,
                        )

                    if tracker.remaining <= 0:
                        digest = _response_digest(response.body)
                        detail = (
                            "total elapsed-time budget exhausted during HTTP attempt"
                        )
                        try:
                            self._record_attempt(
                                current,
                                status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                                detail=detail,
                                source_url=url,
                                response=response,
                            )
                        except (RawStoreError, OSError, ValueError) as store_error:
                            return self._finish(
                                status=BinanceKlineRestStatus.LOCAL_FAILURE,
                                request=request,
                                pages=pages,
                                tracker=tracker,
                                cursor_ms=cursor_ms,
                                reason=(
                                    "failed to persist elapsed-budget response: "
                                    f"{store_error}"
                                ),
                            )
                        attempts.append(
                            BinanceKlineRestAttemptResult(
                                attempt_number=tracker.attempts,
                                page_attempt_number=page_attempt,
                                outcome="elapsed_budget_exhausted",
                                http_status=response.status,
                                response_sha256=digest,
                                detail=detail,
                            )
                        )
                        pages.append(
                            BinanceKlineRestPageResult(
                                request=current,
                                attempts=tuple(attempts),
                                status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                                short_page=False,
                                record_count=0,
                                actual_record_range=None,
                                response_sha256=digest,
                                revision=None,
                                artifact_path=None,
                                detail=detail,
                            )
                        )
                        return self._finish(
                            status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                            request=request,
                            pages=pages,
                            tracker=tracker,
                            cursor_ms=cursor_ms,
                            reason=detail,
                        )

                    retryable = response.status == 429 or 500 <= response.status <= 599
                    if retryable:
                        digest = _response_digest(response.body)
                        detail = f"retryable HTTP {response.status}"
                        try:
                            self._record_attempt(
                                current,
                                status=BinanceKlineRestStatus.RETRYABLE_FAILURE,
                                detail=detail,
                                source_url=url,
                                response=response,
                            )
                        except (RawStoreError, OSError, ValueError) as store_error:
                            return self._finish(
                                status=BinanceKlineRestStatus.LOCAL_FAILURE,
                                request=request,
                                pages=pages,
                                tracker=tracker,
                                cursor_ms=cursor_ms,
                                reason=f"failed to persist HTTP evidence: {store_error}",
                            )
                        retry_delay = _retry_after_seconds(
                            _sanitize_headers(response.headers).get("retry-after"),
                            now=self._now(),
                        )
                        if retry_delay is None:
                            retry_delay = float(min(2 ** (page_attempt - 1), 60))
                        attempts.append(
                            BinanceKlineRestAttemptResult(
                                attempt_number=tracker.attempts,
                                page_attempt_number=page_attempt,
                                outcome="retryable_http",
                                http_status=response.status,
                                response_sha256=digest,
                                detail=detail,
                                waited_seconds=(
                                    retry_delay
                                    if page_attempt < budget.maximum_attempts_per_page
                                    and retry_delay <= tracker.remaining
                                    else 0.0
                                ),
                            )
                        )
                        if page_attempt == budget.maximum_attempts_per_page:
                            response = None
                            break
                        if retry_delay > tracker.remaining:
                            pages.append(
                                BinanceKlineRestPageResult(
                                    request=current,
                                    attempts=tuple(attempts),
                                    status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                                    short_page=False,
                                    record_count=0,
                                    actual_record_range=None,
                                    response_sha256=digest,
                                    revision=None,
                                    artifact_path=None,
                                    detail="Retry-After/backoff exceeds remaining elapsed budget",
                                )
                            )
                            return self._finish(
                                status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                                request=request,
                                pages=pages,
                                tracker=tracker,
                                cursor_ms=cursor_ms,
                                reason="retry wait cannot fit in the elapsed-time budget",
                            )
                        try:
                            self._wait(retry_delay)
                        except Exception as error:
                            return self._finish(
                                status=BinanceKlineRestStatus.LOCAL_FAILURE,
                                request=request,
                                pages=pages,
                                tracker=tracker,
                                cursor_ms=cursor_ms,
                                reason=f"retry wait failed: {error}",
                            )
                        tracker.waited_seconds += retry_delay
                        response = None
                        continue
                    break

            if response is None:
                detail = "per-page retry budget exhausted"
                pages.append(
                    BinanceKlineRestPageResult(
                        request=current,
                        attempts=tuple(attempts),
                        status=BinanceKlineRestStatus.RETRY_EXHAUSTED,
                        short_page=False,
                        record_count=0,
                        actual_record_range=None,
                        response_sha256=None,
                        revision=None,
                        artifact_path=None,
                        detail=detail,
                    )
                )
                return self._finish(
                    status=BinanceKlineRestStatus.RETRY_EXHAUSTED,
                    request=request,
                    pages=pages,
                    tracker=tracker,
                    cursor_ms=cursor_ms,
                    reason=detail,
                )

            digest = _response_digest(response.body)
            attempts.append(
                BinanceKlineRestAttemptResult(
                    attempt_number=tracker.attempts,
                    page_attempt_number=page_attempt,
                    outcome="received",
                    http_status=response.status,
                    response_sha256=digest,
                    detail=f"HTTP {response.status}",
                )
            )
            if not 200 <= response.status < 300:
                detail = f"non-retryable HTTP {response.status}"
                try:
                    self._record_attempt(
                        current,
                        status=BinanceKlineRestStatus.INVALID_RESPONSE,
                        detail=detail,
                        source_url=url,
                        response=response,
                    )
                except (RawStoreError, OSError, ValueError) as store_error:
                    detail = f"failed to persist HTTP error evidence: {store_error}"
                    status = BinanceKlineRestStatus.LOCAL_FAILURE
                else:
                    status = BinanceKlineRestStatus.INVALID_RESPONSE
                pages.append(
                    BinanceKlineRestPageResult(
                        request=current,
                        attempts=tuple(attempts),
                        status=status,
                        short_page=False,
                        record_count=0,
                        actual_record_range=None,
                        response_sha256=digest,
                        revision=None,
                        artifact_path=None,
                        detail=detail,
                    )
                )
                return self._finish(
                    status=status,
                    request=request,
                    pages=pages,
                    tracker=tracker,
                    cursor_ms=cursor_ms,
                    reason=detail,
                )

            try:
                frame, actual_range, page_rows = _parse_kline_page(
                    current, response.body, observed_rows
                )
            except _InvalidPageError as error:
                detail = str(error)
                status = (
                    BinanceKlineRestStatus.LEGAL_EMPTY
                    if detail == "empty"
                    else BinanceKlineRestStatus.INVALID_RESPONSE
                )
                try:
                    self._record_attempt(
                        current,
                        status=status,
                        detail=detail,
                        source_url=url,
                        response=response,
                    )
                except (RawStoreError, OSError, ValueError) as store_error:
                    detail = (
                        f"failed to persist invalid response evidence: {store_error}"
                    )
                    status = BinanceKlineRestStatus.LOCAL_FAILURE
                pages.append(
                    BinanceKlineRestPageResult(
                        request=current,
                        attempts=tuple(attempts),
                        status=status,
                        short_page=False,
                        record_count=0,
                        actual_record_range=None,
                        response_sha256=digest,
                        revision=None,
                        artifact_path=None,
                        detail=detail,
                    )
                )
                return self._finish(
                    status=status,
                    request=request,
                    pages=pages,
                    tracker=tracker,
                    cursor_ms=cursor_ms,
                    reason=detail,
                )

            if tracker.remaining <= 0:
                detail = "total elapsed-time budget exhausted while validating page"
                try:
                    self._record_attempt(
                        current,
                        status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                        detail=detail,
                        source_url=url,
                        response=response,
                    )
                except (RawStoreError, OSError, ValueError) as store_error:
                    return self._finish(
                        status=BinanceKlineRestStatus.LOCAL_FAILURE,
                        request=request,
                        pages=pages,
                        tracker=tracker,
                        cursor_ms=cursor_ms,
                        reason=(
                            "failed to persist validation-budget response: "
                            f"{store_error}"
                        ),
                    )
                pages.append(
                    BinanceKlineRestPageResult(
                        request=current,
                        attempts=tuple(attempts),
                        status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                        short_page=frame.height < current.limit,
                        record_count=frame.height,
                        actual_record_range=actual_range,
                        response_sha256=digest,
                        revision=None,
                        artifact_path=None,
                        detail=detail,
                    )
                )
                return self._finish(
                    status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                    request=request,
                    pages=pages,
                    tracker=tracker,
                    cursor_ms=cursor_ms,
                    reason=detail,
                )

            provenance = BinanceRestPageProvenance.from_response(
                request=current,
                response_body=response.body,
                observed_at=self._now(),
                http_status=response.status,
                response_headers=_sanitize_headers(response.headers),
                record_count=frame.height,
            )
            source = RawRestPageSourceObject(
                request=current,
                rows=frame,
                actual_record_range=actual_range,
                raw_schema_identifier=_SCHEMA_IDENTIFIERS[current.endpoint],
                producer_version=_PRODUCER_VERSION,
                provenance=provenance,
                response_body=response.body,
            )
            revision = source.revision_identity
            existed = self._store.revision_path_for(source.identity, revision).exists()
            try:
                artifact = self._store.write(source)
            except RawArtifactConflictError as error:
                status = BinanceKlineRestStatus.CONFLICT
                detail = str(error)
                artifact_path = None
            except (RawArtifactValidationError, RawStoreError, OSError) as error:
                status = BinanceKlineRestStatus.LOCAL_FAILURE
                detail = str(error)
                artifact_path = None
            else:
                status = BinanceKlineRestStatus.COMPLETE
                detail = "existing immutable revision" if existed else "published"
                artifact_path = artifact.path

            short_page = frame.height < current.limit
            pages.append(
                BinanceKlineRestPageResult(
                    request=current,
                    attempts=tuple(attempts),
                    status=status,
                    short_page=short_page,
                    record_count=frame.height,
                    actual_record_range=actual_range,
                    response_sha256=digest,
                    revision=revision
                    if status is BinanceKlineRestStatus.COMPLETE
                    else None,
                    artifact_path=artifact_path,
                    detail=detail,
                )
            )
            if status is not BinanceKlineRestStatus.COMPLETE:
                return self._finish(
                    status=status,
                    request=request,
                    pages=pages,
                    tracker=tracker,
                    cursor_ms=cursor_ms,
                    reason=detail,
                )
            observed_rows.update(page_rows)

            last_open_ms = int(frame["open_time"][-1])
            next_cursor = last_open_ms + _MINUTE_MS
            if next_cursor <= cursor_ms:
                return self._finish(
                    status=BinanceKlineRestStatus.INVALID_RESPONSE,
                    request=request,
                    pages=pages,
                    tracker=tracker,
                    cursor_ms=cursor_ms,
                    reason="page cursor did not advance",
                )
            cursor_ms = next_cursor
            if tracker.remaining <= 0:
                return self._finish(
                    status=BinanceKlineRestStatus.BUDGET_EXHAUSTED,
                    request=request,
                    pages=pages,
                    tracker=tracker,
                    cursor_ms=cursor_ms,
                    reason="total elapsed-time budget exhausted after Raw publication",
                )
            if cursor_ms < caller_end_ms:
                current = current.next_page(last_open_ms)

        return self._finish(
            status=BinanceKlineRestStatus.COMPLETE,
            request=request,
            pages=pages,
            tracker=tracker,
            cursor_ms=cursor_ms,
            reason="requested range was fully published as immutable REST pages",
        )
