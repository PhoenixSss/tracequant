"""Binance USDⓈ-M 1m contract-Kline archive backfill adapter.

The adapter owns contract-Kline planning, parsing, and coverage semantics.  It
delegates official archive acquisition, evidence, and Raw publication to the
typed shared Binance public-archive seam.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
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
    BinanceKlineInterval,
    BinancePublicHistoryDataType,
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceKind,
)
from tracequant.data.raw_store import RawStore
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
_ONE_MINUTE_MS: Final = 60_000
_MAX_SIGNED_INT64_TEXT: Final = str(2**63 - 1)
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

# Frozen per-instrument archive coverage from the approved Research contract.
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


BinanceContractKlineStatus = BinanceArchiveAcquisitionStatus


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


def _archive_object_range(plan: BinanceArchiveObjectPlan) -> TimeRange:
    boundary = plan.request.archive_object_boundary
    assert boundary is not None
    object_start = _utc_midnight(boundary.period_start)
    object_end = (
        _utc_midnight(_next_month(boundary.period_start))
        if boundary.granularity.value == "month"
        else object_start + timedelta(days=1)
    )
    return TimeRange(start=object_start, end=object_end)


def _required_record_range(plan: BinanceArchiveObjectPlan) -> TimeRange:
    object_range = _archive_object_range(plan)
    return TimeRange(
        start=max(plan.request.request_range.start, object_range.start),
        end=min(plan.request.request_range.end, object_range.end),
    )


def _validate_complete_object_coverage(
    plan: BinanceArchiveObjectPlan, actual_range: TimeRange
) -> None:
    if actual_range != _archive_object_range(plan):
        raise _coverage_gap(
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
        raise _coverage_gap(
            "archive rows do not cover the caller range within this source object"
        )


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


def _validate_decimal(value: str, *, field: str, nonnegative: bool = False) -> None:
    from decimal import Decimal, InvalidOperation

    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise _invalid_content(f"{field} is not a decimal") from error
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise _invalid_content(f"{field} has an invalid numeric value")


def _parse_archive(
    plan: BinanceArchiveObjectPlan, payload: bytes
) -> BinanceArchiveParseResult:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _invalid_content("CSV member is not UTF-8") from error

    parsed_rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if parsed_rows and tuple(parsed_rows[0]) == _COLUMNS:
        parsed_rows = parsed_rows[1:]
    if not parsed_rows:
        raise _invalid_content("CSV contains no data rows")

    columns: dict[str, list[str | int]] = {name: [] for name in _COLUMNS}
    previous_open: int | None = None
    object_range = _archive_object_range(plan)
    object_start_ms = int(object_range.start.timestamp() * 1000)
    object_end_ms = int(object_range.end.timestamp() * 1000)
    for row_number, row in enumerate(parsed_rows, start=1):
        if len(row) != len(_COLUMNS):
            raise _invalid_content(f"CSV row {row_number} does not have 12 columns")
        open_time = _parse_nonnegative_int(row[0], field="open_time")
        close_time = _parse_nonnegative_int(row[6], field="close_time")
        count = _parse_nonnegative_int(row[8], field="count")
        try:
            datetime.fromtimestamp(open_time / 1000, tz=UTC)
            datetime.fromtimestamp(close_time / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise _invalid_content("row timestamp is not interpretable") from error
        if (
            open_time % _ONE_MINUTE_MS != 0
            or close_time != open_time + _ONE_MINUTE_MS - 1
        ):
            raise _invalid_content("row does not have valid 1m timestamp boundaries")
        if not object_start_ms <= open_time < object_end_ms:
            raise _invalid_content(
                "row open_time is outside the archive object boundary"
            )
        if previous_open is not None:
            if open_time <= previous_open:
                raise _invalid_content(
                    "row open_time values must be strictly increasing"
                )
            if open_time != previous_open + _ONE_MINUTE_MS:
                raise _coverage_gap("archive rows contain a missing 1m timestamp")
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
    return BinanceArchiveParseResult(
        rows=frame,
        actual_record_range=actual_range,
        validation_evidence=("csv_schema_and_rows_verified",),
    )


class _ContractKlineArchiveAdapter:
    raw_schema_identifier = _SCHEMA_IDENTIFIER

    def parse_member(
        self, plan: BinanceArchiveObjectPlan, payload: bytes
    ) -> BinanceArchiveParseResult:
        return _parse_archive(plan, payload)

    def validate_complete_object_coverage(
        self, plan: BinanceArchiveObjectPlan, actual_range: TimeRange
    ) -> None:
        _validate_complete_object_coverage(plan, actual_range)

    def validate_required_coverage(
        self, plan: BinanceArchiveObjectPlan, actual_range: TimeRange
    ) -> None:
        _validate_required_coverage(plan, actual_range)


class BinanceContractKlineBackfill:
    """Execute the official contract-Kline archive path."""

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
    ) -> BinanceContractKlineRunResult:
        plans = plan_binance_contract_kline_archives(instrument, request_range)
        results = tuple(self._process(plan) for plan in plans)
        return BinanceContractKlineRunResult(
            request_range=request_range, objects=results
        )

    def run_plan(
        self, plan: BinanceArchiveObjectPlan
    ) -> BinanceContractKlineObjectResult:
        """Execute one caller-supplied, explicitly evidenced archive object."""
        if not isinstance(plan, BinanceArchiveObjectPlan):
            raise TypeError("plan must be a BinanceArchiveObjectPlan")
        if plan.request.data_type is not BinancePublicHistoryDataType.CONTRACT_KLINE:
            raise ValueError("plan must identify contract-Kline data")
        return self._process(plan)

    def _process(
        self, plan: BinanceContractKlinePlan
    ) -> BinanceContractKlineObjectResult:
        if isinstance(plan, BinanceArchiveCoverageGapPlan):
            outcome = self._acquisition.record_failure(
                plan.request,
                BinanceContractKlineStatus.COVERAGE_GAP,
                plan.detail,
            )
        else:
            outcome = self._acquisition.acquire(plan, _ContractKlineArchiveAdapter())
        return BinanceContractKlineObjectResult(
            plan,
            outcome.status,
            artifact_path=outcome.artifact_path,
            detail=outcome.detail,
        )
