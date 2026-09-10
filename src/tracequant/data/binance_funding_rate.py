"""Binance USDⓈ-M settled-funding monthly archive backfill adapter.

Funding rows are events rather than candles.  The adapter therefore records
the observed event-point range separately from its fail-closed object coverage
decision.  Official archive acquisition, evidence, and immutable Raw
publication remain delegated to the shared Binance public-archive seam.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final

import polars as pl

from tracequant.data.binance_public_archive import (
    ArchiveHttpGet,
    ArchiveHttpResponse,
    BinanceArchiveAcquisitionStatus,
    BinanceArchiveObjectPlan,
    BinanceArchiveParseResult,
    BinancePublicArchiveAcquisition,
    _coverage_gap,
    _invalid_content,
)
from tracequant.data.public_history import (
    BinanceArchiveObjectBoundary,
    BinancePublicHistoryDataType,
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceKind,
)
from tracequant.data.raw_store import RawStore
from tracequant.domain import InstrumentId, TimeRange

__all__ = [
    "ArchiveHttpResponse",
    "BinanceArchiveObjectPlan",
    "BinanceFundingRateBackfill",
    "BinanceFundingRateCoverageGapPlan",
    "BinanceFundingRateCoverageStatus",
    "BinanceFundingRateObjectResult",
    "BinanceFundingRateRunResult",
    "BinanceFundingRateStatus",
    "plan_binance_funding_rate_archives",
]

_ARCHIVE_ROOT: Final = "https://data.binance.vision"
_SCHEMA_IDENTIFIER: Final = "binance.um.funding-rate.csv.v1"
_MICROSECOND: Final = timedelta(microseconds=1)
_MILLISECONDS_PER_HOUR: Final = 60 * 60 * 1000
_MAX_SIGNED_INT64_TEXT: Final = str(2**63 - 1)
_COLUMNS: Final = (
    "calc_time",
    "funding_interval_hours",
    "last_funding_rate",
)
_DTYPES: Final = {
    "calc_time": pl.Int64,
    "funding_interval_hours": pl.Int64,
    "last_funding_rate": pl.String,
}
_DECIMAL_PATTERN: Final = re.compile(
    r"\A[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)

# Frozen object-existence evidence from the approved Research contract.  This
# is an inclusive monthly range, not a promise about current or future objects.
_RESEARCH_MONTHLY_COVERAGE: Final = {
    "BTCUSDT": (date(2020, 1, 1), date(2026, 7, 1)),
    "ETHUSDT": (date(2020, 1, 1), date(2026, 7, 1)),
}


BinanceFundingRateStatus = BinanceArchiveAcquisitionStatus


class BinanceFundingRateCoverageStatus(StrEnum):
    """Independent event-coverage judgment for one planned monthly object."""

    COMPLETE = "complete"
    GAP = "gap"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BinanceFundingRateCoverageGapPlan:
    """One month for which Research did not prove a funding archive object."""

    instrument: InstrumentId
    request_range: TimeRange
    boundary: BinanceArchiveObjectBoundary
    detail: str

    @property
    def request(self) -> BinancePublicHistoryRequest:
        """Return the monthly funding source identity represented by this gap."""
        return BinancePublicHistoryRequest(
            instrument=self.instrument,
            data_type=BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
            request_range=self.request_range,
            source_kind=BinancePublicHistorySourceKind.ARCHIVE_MONTHLY,
            interval=None,
            archive_object_boundary=self.boundary,
        )


type BinanceFundingRatePlan = (
    BinanceArchiveObjectPlan | BinanceFundingRateCoverageGapPlan
)


@dataclass(frozen=True, slots=True)
class BinanceFundingRateObjectResult:
    plan: BinanceFundingRatePlan
    status: BinanceFundingRateStatus
    artifact_path: Path | None = None
    detail: str | None = None
    actual_record_range: TimeRange | None = None

    @property
    def request_range(self) -> TimeRange:
        return self.plan.request.request_range

    @property
    def coverage_status(self) -> BinanceFundingRateCoverageStatus:
        if self.status in {
            BinanceFundingRateStatus.PUBLISHED,
            BinanceFundingRateStatus.EXISTING,
        }:
            return BinanceFundingRateCoverageStatus.COMPLETE
        if self.status is BinanceFundingRateStatus.COVERAGE_GAP and self.detail:
            if "unknown" not in self.detail and "does not prove" not in self.detail:
                return BinanceFundingRateCoverageStatus.GAP
        return BinanceFundingRateCoverageStatus.UNKNOWN


@dataclass(frozen=True, slots=True)
class BinanceFundingRateRunResult:
    """Per-month outcomes for one request; completion is deliberately all-or-none."""

    request_range: TimeRange
    objects: tuple[BinanceFundingRateObjectResult, ...]

    @property
    def completed(self) -> bool:
        successful = {
            BinanceFundingRateStatus.PUBLISHED,
            BinanceFundingRateStatus.EXISTING,
        }
        return bool(self.objects) and all(
            item.status in successful for item in self.objects
        )


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _utc_midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _month_start(value: datetime) -> date:
    return date(value.year, value.month, 1)


def _archive_object_range(plan: BinanceArchiveObjectPlan) -> TimeRange:
    boundary = plan.request.archive_object_boundary
    assert boundary is not None
    return TimeRange(
        start=_utc_midnight(boundary.period_start),
        end=_utc_midnight(_next_month(boundary.period_start)),
    )


def _build_plan(
    instrument: InstrumentId,
    request_range: TimeRange,
    boundary: BinanceArchiveObjectBoundary,
) -> BinanceArchiveObjectPlan:
    suffix = boundary.period_start.strftime("%Y-%m")
    filename = f"{instrument}-fundingRate-{suffix}.zip"
    object_key = f"data/futures/um/monthly/fundingRate/{instrument}/{filename}"
    request = BinancePublicHistoryRequest(
        instrument=instrument,
        data_type=BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
        request_range=request_range,
        source_kind=BinancePublicHistorySourceKind.ARCHIVE_MONTHLY,
        interval=None,
        archive_object_boundary=boundary,
    )
    return BinanceArchiveObjectPlan(
        request=request,
        object_key=object_key,
        url=f"{_ARCHIVE_ROOT}/{object_key}",
        checksum_url=f"{_ARCHIVE_ROOT}/{object_key}.CHECKSUM",
        member_name=f"{instrument}-fundingRate-{suffix}.csv",
    )


def plan_binance_funding_rate_archives(
    instrument: InstrumentId,
    request_range: TimeRange,
) -> tuple[BinanceFundingRatePlan, ...]:
    """Map every request-intersecting month to one full object or explicit gap."""
    if not isinstance(instrument, InstrumentId):
        raise TypeError("instrument must be an InstrumentId")
    if not isinstance(request_range, TimeRange):
        raise TypeError("request_range must be a TimeRange")

    coverage = _RESEARCH_MONTHLY_COVERAGE.get(str(instrument))
    if coverage is None:
        raise ValueError(
            f"instrument {instrument!s} has no frozen funding-rate archive coverage"
        )

    first_month, last_month = coverage
    cursor = _month_start(request_range.start)
    final_month = _month_start(request_range.end - _MICROSECOND)
    plans: list[BinanceFundingRatePlan] = []
    while cursor <= final_month:
        boundary = BinanceArchiveObjectBoundary.month(cursor.year, cursor.month)
        if first_month <= cursor <= last_month:
            plans.append(_build_plan(instrument, request_range, boundary))
        else:
            plans.append(
                BinanceFundingRateCoverageGapPlan(
                    instrument=instrument,
                    request_range=request_range,
                    boundary=boundary,
                    detail=(
                        "Research did not prove a settled-funding monthly archive "
                        f"object for {instrument!s} in {cursor:%Y-%m}"
                    ),
                )
            )
        cursor = _next_month(cursor)
    return tuple(plans)


def _parse_nonnegative_int(value: str, *, field: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise _invalid_content(f"{field} must be a non-negative integer")
    normalized = value.lstrip("0") or "0"
    if len(normalized) > len(_MAX_SIGNED_INT64_TEXT) or (
        len(normalized) == len(_MAX_SIGNED_INT64_TEXT)
        and normalized > _MAX_SIGNED_INT64_TEXT
    ):
        raise _invalid_content(f"{field} exceeds signed 64-bit range")
    return int(normalized)


def _parse_positive_int(value: str, *, field: str) -> int:
    parsed = _parse_nonnegative_int(value, field=field)
    if parsed == 0:
        raise _invalid_content(f"{field} must be a positive integer")
    return parsed


def _validate_rate(value: str) -> None:
    if _DECIMAL_PATTERN.fullmatch(value) is None:
        raise _invalid_content("last_funding_rate must be a finite numeric string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise _invalid_content(
            "last_funding_rate must be a finite numeric string"
        ) from error
    if not parsed.is_finite():
        raise _invalid_content("last_funding_rate must be a finite numeric string")


def _validate_event_coverage(
    plan: BinanceArchiveObjectPlan,
    calc_times: list[int],
    intervals: list[int],
    actual_range: TimeRange,
) -> None:
    object_range = _archive_object_range(plan)
    object_start_ms = int(object_range.start.timestamp() * 1000)
    object_end_ms = int(object_range.end.timestamp() * 1000)
    declared_intervals = set(intervals)
    if len(declared_intervals) != 1:
        raise _coverage_gap(
            "funding interval changes make source-object coverage unknown",
            actual_record_range=actual_range,
        )
    interval_ms = intervals[0] * _MILLISECONDS_PER_HOUR
    if calc_times[0] != object_start_ms:
        raise _coverage_gap(
            "funding archive is missing the first monthly event",
            actual_record_range=actual_range,
        )
    for previous, current in zip(calc_times, calc_times[1:], strict=False):
        if current - previous != interval_ms:
            raise _coverage_gap(
                "funding archive contains a missing event for its declared interval",
                actual_record_range=actual_range,
            )
    if calc_times[-1] + interval_ms != object_end_ms:
        raise _coverage_gap(
            "funding archive tail does not prove complete monthly event coverage",
            actual_record_range=actual_range,
        )


def _parse_archive(
    plan: BinanceArchiveObjectPlan, payload: bytes
) -> BinanceArchiveParseResult:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _invalid_content("CSV member is not UTF-8") from error

    parsed_rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if not parsed_rows or tuple(parsed_rows[0]) != _COLUMNS:
        raise _invalid_content("CSV header must exactly match the funding schema")
    parsed_rows = parsed_rows[1:]
    if not parsed_rows:
        raise _invalid_content("CSV contains no data rows")

    calc_times: list[int] = []
    intervals: list[int] = []
    rates: list[str] = []
    previous_calc_time: int | None = None
    object_range = _archive_object_range(plan)
    object_start_ms = int(object_range.start.timestamp() * 1000)
    object_end_ms = int(object_range.end.timestamp() * 1000)
    for row_number, row in enumerate(parsed_rows, start=1):
        if len(row) != len(_COLUMNS):
            raise _invalid_content(f"CSV row {row_number} does not have 3 columns")
        calc_time = _parse_nonnegative_int(row[0], field="calc_time")
        interval = _parse_positive_int(row[1], field="funding_interval_hours")
        _validate_rate(row[2])
        try:
            datetime.fromtimestamp(calc_time / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise _invalid_content("calc_time is not interpretable as UTC") from error
        if not object_start_ms <= calc_time < object_end_ms:
            raise _invalid_content(
                "calc_time is outside the funding archive object month"
            )
        if previous_calc_time is not None and calc_time <= previous_calc_time:
            raise _invalid_content("calc_time values must be strictly increasing")
        previous_calc_time = calc_time
        calc_times.append(calc_time)
        intervals.append(interval)
        rates.append(row[2])

    actual_range = TimeRange(
        start=datetime.fromtimestamp(calc_times[0] / 1000, tz=UTC),
        end=datetime.fromtimestamp((calc_times[-1] + 1) / 1000, tz=UTC),
    )
    _validate_event_coverage(plan, calc_times, intervals, actual_range)
    frame = pl.DataFrame(
        {
            "calc_time": calc_times,
            "funding_interval_hours": intervals,
            "last_funding_rate": rates,
        },
        schema=_DTYPES,
    )
    return BinanceArchiveParseResult(
        rows=frame,
        actual_record_range=actual_range,
        validation_evidence=(
            "funding_csv_schema_and_rows_verified",
            "funding_event_time_range_recorded",
            "funding_fixed_interval_object_coverage_verified",
        ),
    )


def _validate_existing_complete_object_coverage(
    plan: BinanceArchiveObjectPlan, actual_range: TimeRange
) -> None:
    """Validate the point-range shape of a previously verified funding object."""
    object_range = _archive_object_range(plan)
    if (
        actual_range.start != object_range.start
        or actual_range.end <= actual_range.start
        or actual_range.end > object_range.end
    ):
        raise _coverage_gap(
            "existing funding event range is incompatible with its source month",
            actual_record_range=actual_range,
        )


class _FundingRateArchiveAdapter:
    raw_schema_identifier = _SCHEMA_IDENTIFIER

    def parse_member(
        self, plan: BinanceArchiveObjectPlan, payload: bytes
    ) -> BinanceArchiveParseResult:
        return _parse_archive(plan, payload)

    def validate_complete_object_coverage(
        self, plan: BinanceArchiveObjectPlan, actual_range: TimeRange
    ) -> None:
        _validate_existing_complete_object_coverage(plan, actual_range)

    def validate_required_coverage(
        self, plan: BinanceArchiveObjectPlan, actual_range: TimeRange
    ) -> None:
        # Event coverage was proven independently while parsing the complete
        # monthly source object.  The point range must not be compared with an
        # arbitrary continuous caller range.
        _validate_existing_complete_object_coverage(plan, actual_range)


class BinanceFundingRateBackfill:
    """Execute the official settled-funding monthly archive path."""

    def __init__(
        self,
        store: RawStore,
        *,
        http_get: ArchiveHttpGet | None = None,
        timeout: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._acquisition = BinancePublicArchiveAcquisition(
            store,
            http_get=http_get,
            timeout=timeout,
            clock=clock,
        )

    def run(
        self, instrument: InstrumentId, request_range: TimeRange
    ) -> BinanceFundingRateRunResult:
        plans = plan_binance_funding_rate_archives(instrument, request_range)
        results = tuple(self._process(plan) for plan in plans)
        return BinanceFundingRateRunResult(
            request_range=request_range,
            objects=results,
        )

    def _run_controlled_plan(
        self, plan: BinanceArchiveObjectPlan
    ) -> BinanceFundingRateObjectResult:
        """Execute one orchestrator-derived monthly object."""
        if not isinstance(plan, BinanceArchiveObjectPlan):
            raise TypeError("plan must be a BinanceArchiveObjectPlan")
        if (
            plan.request.data_type
            is not BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
        ):
            raise ValueError("plan must identify settled funding-rate data")
        return self._process(plan)

    def _process(self, plan: BinanceFundingRatePlan) -> BinanceFundingRateObjectResult:
        if isinstance(plan, BinanceFundingRateCoverageGapPlan):
            outcome = self._acquisition.record_failure(
                plan.request,
                BinanceFundingRateStatus.COVERAGE_GAP,
                plan.detail,
            )
        else:
            outcome = self._acquisition.acquire(plan, _FundingRateArchiveAdapter())
        return BinanceFundingRateObjectResult(
            plan=plan,
            status=outcome.status,
            artifact_path=outcome.artifact_path,
            detail=outcome.detail,
            actual_record_range=outcome.actual_record_range,
        )
