"""Finite source planning and execution for Binance USDⓈ-M public history.

This module is the library-level orchestration seam above the existing archive
and REST consumers.  A plan is pure data: it consumes an explicit finite
request set, exact source evidence, and a finite shared budget without touching
the network or filesystem.  ``run`` validates that binding again and then
executes only the source candidates named by the plan.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Final

import polars as pl

from tracequant.core.time import format_utc, to_utc
from tracequant.data.binance_contract_kline import BinanceContractKlineBackfill
from tracequant.data.binance_funding_rate import BinanceFundingRateBackfill
from tracequant.data.binance_funding_rate_rest import (
    BinanceFundingRateRestAcquisition,
)
from tracequant.data.binance_index_price_kline import BinanceIndexPriceKlineBackfill
from tracequant.data.binance_kline_rest import (
    BinanceKlineRestAcquisition,
    BinanceKlineRestBudget,
    BinanceKlineRestBudgetExceeded,
    BinanceKlineRestCoverage,
    BinanceKlineRestHttpGet,
    BinanceKlineRestHttpResponse,
    BinanceKlineRestPageResult,
    BinanceKlineRestRunResult,
    BinanceKlineRestStatus,
)
from tracequant.data.binance_kline_rest import (
    _default_http_get as _default_rest_http_get,
)
from tracequant.data.binance_mark_price_kline import BinanceMarkPriceKlineBackfill
from tracequant.data.binance_public_archive import (
    ArchiveHttpBudgetExceeded,
    ArchiveHttpGet,
    ArchiveHttpResponse,
    BinanceArchiveAcquisitionStatus,
    BinanceArchiveObjectPlan,
)
from tracequant.data.binance_public_archive import (
    _default_http_get as _default_archive_http_get,
)
from tracequant.data.public_history import (
    BinanceArchiveObjectBoundary,
    BinanceArchiveObjectGranularity,
    BinanceKlineInterval,
    BinanceMarket,
    BinancePriceIndexId,
    BinancePublicHistoryDataType,
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceKind,
    BinancePublicHistorySubject,
)
from tracequant.data.public_history_rest import (
    BinanceRestEndpoint,
    BinanceRestPageRequest,
)
from tracequant.data.raw_store import (
    RawArtifact,
    RawObjectIdentity,
    RawRevisionIdentity,
    RawStore,
    RawStoreError,
)
from tracequant.domain import InstrumentId, TimeRange

__all__ = [
    "BinanceArchiveEvidenceStatus",
    "BinancePublicHistoryAcquisition",
    "BinancePublicHistoryAcquisitionBudget",
    "BinancePublicHistoryAcquisitionPlan",
    "BinancePublicHistoryAcquisitionRequest",
    "BinancePublicHistoryArchiveEvidence",
    "BinancePublicHistoryConflict",
    "BinancePublicHistoryConflictPolicy",
    "BinancePublicHistoryCoverage",
    "BinancePublicHistoryObligationPlan",
    "BinancePublicHistoryObligationResult",
    "BinancePublicHistoryPurpose",
    "BinancePublicHistoryRawReference",
    "BinancePublicHistoryRequestResult",
    "BinancePublicHistoryRunResult",
    "BinancePublicHistoryRunStatus",
    "BinancePublicHistorySourceResult",
    "BinancePublicHistorySourceStep",
]

_ALLOWED_SUBJECTS: Final = frozenset({"BTCUSDT", "ETHUSDT"})
_KLINE_TYPES: Final = frozenset(
    {
        BinancePublicHistoryDataType.CONTRACT_KLINE,
        BinancePublicHistoryDataType.MARK_PRICE_KLINE,
        BinancePublicHistoryDataType.INDEX_PRICE_KLINE,
    }
)
_ARCHIVE_ROOT: Final = "https://data.binance.vision"
_ONE_MINUTE_MS: Final = 60_000
_APPROVED_ARCHIVE_EVIDENCE_VERSION: Final = (
    "issue-279-probe-run-2026-09-09T10:58:43.717234Z"
)
_APPROVED_ARCHIVE_EVIDENCE_REFERENCE: Final = (
    "docs/research/binance-usdm-feature11-window-probes.json"
)
_APPROVED_ARCHIVE_EVIDENCE_SHA256: Final = (
    "c4080de0dff862cebd1ad3363c8048c67805316a1700c4ff9e2b1621eaeb64d6"
)


class BinancePublicHistoryPurpose(StrEnum):
    BACKFILL = "backfill"
    RECENT = "recent"
    GAP = "gap"


class BinancePublicHistoryConflictPolicy(StrEnum):
    """The only safe v1 policy: preserve every Raw and report disagreement."""

    PRESERVE_AND_REPORT = "preserve_and_report"


class BinanceArchiveEvidenceStatus(StrEnum):
    SUPPORTED = "supported"
    NOT_FOUND = "not_found"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class BinancePublicHistoryRunStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CONFLICT = "conflict"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


def _coerce_enum[EnumT: StrEnum](
    value: EnumT | str, enum_type: type[EnumT], *, field: str
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unsupported {field}") from error


def _require_sha256(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _subject_payload(subject: BinancePublicHistorySubject) -> dict[str, str]:
    return {
        "kind": (
            "price_index_pair"
            if isinstance(subject, BinancePriceIndexId)
            else "instrument"
        ),
        "value": str(subject),
    }


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryAcquisitionRequest:
    """One explicit, finite orchestration obligation."""

    subject: BinancePublicHistorySubject
    data_type: BinancePublicHistoryDataType
    request_range: TimeRange
    purpose: BinancePublicHistoryPurpose
    output_root: Path
    conflict_policy: BinancePublicHistoryConflictPolicy = (
        BinancePublicHistoryConflictPolicy.PRESERVE_AND_REPORT
    )
    gap_reason: str | None = None
    market: BinanceMarket = BinanceMarket.USD_M

    def __post_init__(self) -> None:
        data_type = _coerce_enum(
            self.data_type, BinancePublicHistoryDataType, field="data type"
        )
        purpose = _coerce_enum(
            self.purpose, BinancePublicHistoryPurpose, field="purpose"
        )
        policy = _coerce_enum(
            self.conflict_policy,
            BinancePublicHistoryConflictPolicy,
            field="conflict policy",
        )
        market = _coerce_enum(self.market, BinanceMarket, field="market")
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "conflict_policy", policy)
        object.__setattr__(self, "market", market)
        if market is not BinanceMarket.USD_M:
            raise ValueError("only Binance USDⓈ-M public history is supported")
        if not isinstance(self.subject, (InstrumentId, BinancePriceIndexId)):
            raise TypeError("subject must be an InstrumentId or BinancePriceIndexId")
        if str(self.subject) not in _ALLOWED_SUBJECTS:
            raise ValueError("only BTCUSDT and ETHUSDT are supported")
        if data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE:
            if not isinstance(self.subject, BinancePriceIndexId):
                raise ValueError(
                    "index-price history requires a typed price-index pair"
                )
        elif not isinstance(self.subject, InstrumentId):
            raise ValueError("this data family requires a typed instrument")
        if not isinstance(self.request_range, TimeRange):
            raise TypeError("request_range must be a TimeRange")
        if data_type in _KLINE_TYPES:
            start_ms = int(self.request_range.start.timestamp() * 1_000)
            end_ms = int(self.request_range.end.timestamp() * 1_000)
            if start_ms % _ONE_MINUTE_MS or end_ms % _ONE_MINUTE_MS:
                raise ValueError(
                    "Kline request ranges must align to complete UTC minutes"
                )
        output_root = Path(self.output_root)
        if not str(output_root):
            raise ValueError("output_root must not be empty")
        object.__setattr__(self, "output_root", output_root)
        if purpose is BinancePublicHistoryPurpose.GAP:
            if not isinstance(self.gap_reason, str) or not self.gap_reason.strip():
                raise ValueError("gap requests require an explicit gap_reason")
            object.__setattr__(self, "gap_reason", self.gap_reason.strip())
        elif self.gap_reason is not None:
            raise ValueError("gap_reason is only valid for explicit gap requests")

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": _subject_payload(self.subject),
            "data_type": self.data_type.value,
            "request_range": self.request_range.to_dict(),
            "purpose": self.purpose.value,
            "output_root": str(self.output_root),
            "conflict_policy": self.conflict_policy.value,
            "gap_reason": self.gap_reason,
            "market": self.market.value,
        }


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryArchiveEvidence:
    """Exact evidence for one archive day/month object boundary."""

    data_type: BinancePublicHistoryDataType
    subject: BinancePublicHistorySubject
    boundary: BinanceArchiveObjectBoundary
    status: BinanceArchiveEvidenceStatus
    evidence_version: str
    evidence_reference: str
    evidence_sha256: str
    observed_at: datetime
    object_sha256: str | None = None
    actual_range: TimeRange | None = None

    def __post_init__(self) -> None:
        data_type = _coerce_enum(
            self.data_type, BinancePublicHistoryDataType, field="data type"
        )
        status = _coerce_enum(
            self.status, BinanceArchiveEvidenceStatus, field="archive evidence status"
        )
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "status", status)
        if not isinstance(self.subject, (InstrumentId, BinancePriceIndexId)):
            raise TypeError("archive evidence subject must be typed")
        if str(self.subject) not in _ALLOWED_SUBJECTS:
            raise ValueError("archive evidence subject is unsupported")
        if data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE:
            if not isinstance(self.subject, BinancePriceIndexId):
                raise ValueError("index archive evidence requires a price-index pair")
        elif not isinstance(self.subject, InstrumentId):
            raise ValueError("archive evidence requires an instrument for this family")
        if not isinstance(self.boundary, BinanceArchiveObjectBoundary):
            raise TypeError("boundary must be a BinanceArchiveObjectBoundary")
        if (
            data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
            and self.boundary.granularity is not BinanceArchiveObjectGranularity.MONTH
        ):
            raise ValueError("settled funding has no daily archive source")
        for field in ("evidence_version", "evidence_reference"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        _require_sha256(self.evidence_sha256, field="evidence_sha256")
        if self.object_sha256 is not None:
            _require_sha256(self.object_sha256, field="object_sha256")
        if (
            status is BinanceArchiveEvidenceStatus.SUPPORTED
            and self.object_sha256 is None
        ):
            raise ValueError("supported archive evidence requires object_sha256")
        if not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime")
        object.__setattr__(self, "observed_at", to_utc(self.observed_at))
        if self.actual_range is not None and not isinstance(
            self.actual_range, TimeRange
        ):
            raise TypeError("actual_range must be a TimeRange or None")

    def to_dict(self) -> dict[str, object]:
        return {
            "data_type": self.data_type.value,
            "subject": _subject_payload(self.subject),
            "boundary": self.boundary.to_dict(),
            "status": self.status.value,
            "evidence_version": self.evidence_version,
            "evidence_reference": self.evidence_reference,
            "evidence_sha256": self.evidence_sha256,
            "observed_at": format_utc(self.observed_at),
            "object_sha256": self.object_sha256,
            "actual_range": (
                self.actual_range.to_dict() if self.actual_range is not None else None
            ),
        }


def _archive_evidence_membership(
    item: BinancePublicHistoryArchiveEvidence,
) -> tuple[str, ...]:
    actual_start = (
        format_utc(item.actual_range.start) if item.actual_range is not None else ""
    )
    actual_end = (
        format_utc(item.actual_range.end) if item.actual_range is not None else ""
    )
    return (
        item.data_type.value,
        str(item.subject),
        item.boundary.granularity.value,
        item.boundary.period_start.isoformat(),
        item.status.value,
        item.evidence_version,
        item.evidence_reference,
        item.evidence_sha256,
        format_utc(item.observed_at),
        item.object_sha256 or "",
        actual_start,
        actual_end,
    )


def _approved_archive_membership(
    data_type: str,
    subject: str,
    granularity: str,
    period_start: str,
    observed_at: str,
    object_sha256: str,
    actual_start: str,
    actual_end: str,
) -> tuple[str, ...]:
    return (
        data_type,
        subject,
        granularity,
        period_start,
        BinanceArchiveEvidenceStatus.SUPPORTED.value,
        _APPROVED_ARCHIVE_EVIDENCE_VERSION,
        _APPROVED_ARCHIVE_EVIDENCE_REFERENCE,
        _APPROVED_ARCHIVE_EVIDENCE_SHA256,
        observed_at,
        object_sha256,
        actual_start,
        actual_end,
    )


def _approved_month_actual_end(data_type: str) -> str:
    return (
        "2026-07-31T16:00:00.001000Z"
        if data_type == BinancePublicHistoryDataType.SETTLED_FUNDING_RATE.value
        else "2026-08-01T00:00:00Z"
    )


_APPROVED_ARCHIVE_EVIDENCE_MEMBERSHIPS: Final = frozenset(
    {
        _approved_archive_membership(
            *values,
            "2026-07-01T00:00:00Z",
            _approved_month_actual_end(values[0]),
        )
        for values in (
            (
                "contract_kline",
                "BTCUSDT",
                "month",
                "2026-07-01",
                "2026-09-09T10:58:44.433753Z",
                "f18440bb58f0c7e1ff63cbb906132002877c4914540c8aebab920da6937cff73",
            ),
            (
                "contract_kline",
                "ETHUSDT",
                "month",
                "2026-07-01",
                "2026-09-09T10:58:53.579519Z",
                "5c98ad0cfa152fbe08df59838ead7c8bbfa433af943bbfb4d082626ebee2d83e",
            ),
            (
                "index_price_kline",
                "BTCUSDT",
                "month",
                "2026-07-01",
                "2026-09-09T10:59:07.659838Z",
                "80f701b6e752ee9a04e9069472d6eb1165c4f379ee9cfd4c7bc11eb98a4a8175",
            ),
            (
                "index_price_kline",
                "ETHUSDT",
                "month",
                "2026-07-01",
                "2026-09-09T10:59:12.258116Z",
                "dd2524b6c7fbfcc8da296da3e8068818660f590f32f6467236cd0f1585b4bc73",
            ),
            (
                "mark_price_kline",
                "BTCUSDT",
                "month",
                "2026-07-01",
                "2026-09-09T10:58:59.001048Z",
                "5bb16a0707eef96d648480ab45943f25b27425154b2ccb881c200cfd51e594ed",
            ),
            (
                "mark_price_kline",
                "ETHUSDT",
                "month",
                "2026-07-01",
                "2026-09-09T10:59:03.480234Z",
                "641ce19380be3671714cf4e358d671737af834534e6e6f1f75852ffc07ce3e7d",
            ),
            (
                "settled_funding_rate",
                "BTCUSDT",
                "month",
                "2026-07-01",
                "2026-09-09T10:59:16.668628Z",
                "e36fcc66f493d7d9ec348c852fc22e9f318c79cf7adae17398a3994ae0adc41e",
            ),
            (
                "settled_funding_rate",
                "ETHUSDT",
                "month",
                "2026-07-01",
                "2026-09-09T10:59:18.094691Z",
                "ece7f6c64d0e45fbd64929dec40bae525fe365381046e367de5ca0827bc623d3",
            ),
        )
    }
    | {
        _approved_archive_membership(
            *values,
            "2026-08-29T00:00:00Z",
            "2026-08-30T00:00:00Z",
        )
        for values in (
            (
                "contract_kline",
                "BTCUSDT",
                "day",
                "2026-08-29",
                "2026-09-09T10:58:50.895074Z",
                "41e554b2a312bfadb74e4865c5cbef8dd401586c7d973c623d0915869eb81ebc",
            ),
            (
                "contract_kline",
                "ETHUSDT",
                "day",
                "2026-08-29",
                "2026-09-09T10:58:56.525232Z",
                "6d64864dff240b52550814cb981d14325fb716d0705d4ca6f02116b321f1f91b",
            ),
            (
                "index_price_kline",
                "BTCUSDT",
                "day",
                "2026-08-29",
                "2026-09-09T10:59:10.153893Z",
                "26241ffe071d9019db8302b9e1765fd05160b9db5dd8704df6fb2cecdb79a2d1",
            ),
            (
                "index_price_kline",
                "ETHUSDT",
                "day",
                "2026-08-29",
                "2026-09-09T10:59:14.809964Z",
                "c15bec3556193e76d4a2144f57865e9610453a550177a81be966aa8eb5ab2eb7",
            ),
            (
                "mark_price_kline",
                "BTCUSDT",
                "day",
                "2026-08-29",
                "2026-09-09T10:59:01.857074Z",
                "2146dff9db1e1ddc3af33f00696f735d0076174602e659377532db817860f4ac",
            ),
            (
                "mark_price_kline",
                "ETHUSDT",
                "day",
                "2026-08-29",
                "2026-09-09T10:59:06.084103Z",
                "ba0613355d8ae1a47367d99a4c39543b5f5cd8aacb70480e5670b38d076b359a",
            ),
        )
    }
)


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryCoverage:
    archive_objects: tuple[BinancePublicHistoryArchiveEvidence, ...] = ()
    rest_windows: tuple[BinanceKlineRestCoverage, ...] = ()

    def __post_init__(self) -> None:
        archive_objects = tuple(self.archive_objects)
        rest_windows = tuple(self.rest_windows)
        if any(
            not isinstance(item, BinancePublicHistoryArchiveEvidence)
            for item in archive_objects
        ):
            raise TypeError("archive_objects must contain archive evidence")
        if any(not isinstance(item, BinanceKlineRestCoverage) for item in rest_windows):
            raise TypeError("rest_windows must contain REST coverage evidence")
        unapproved = [
            item
            for item in archive_objects
            if _archive_evidence_membership(item)
            not in _APPROVED_ARCHIVE_EVIDENCE_MEMBERSHIPS
        ]
        if unapproved:
            raise ValueError(
                "archive evidence is not an exact approved #279 report cell"
            )
        archive_keys = [
            (item.data_type, item.subject, item.boundary) for item in archive_objects
        ]
        if len(set(archive_keys)) != len(archive_keys):
            raise ValueError("archive coverage contains duplicate source cells")
        object.__setattr__(self, "archive_objects", archive_objects)
        object.__setattr__(self, "rest_windows", rest_windows)


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryAcquisitionBudget:
    timeout_seconds: float
    maximum_archive_objects: int
    maximum_rest_pages: int
    maximum_attempts_per_object: int
    maximum_attempts_per_page: int
    maximum_http_requests: int
    maximum_elapsed_seconds: float
    maximum_response_bytes: int
    maximum_download_bytes: int

    def __post_init__(self) -> None:
        for field in ("timeout_seconds", "maximum_elapsed_seconds"):
            value = getattr(self, field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"{field} must be a number")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be finite and greater than zero")
            object.__setattr__(self, field, float(value))
        for field in (
            "maximum_archive_objects",
            "maximum_rest_pages",
            "maximum_attempts_per_object",
            "maximum_attempts_per_page",
            "maximum_http_requests",
            "maximum_response_bytes",
            "maximum_download_bytes",
        ):
            value = getattr(self, field)
            if type(value) is not int:
                raise TypeError(f"{field} must be an integer")
            if value <= 0:
                raise ValueError(f"{field} must be greater than zero")

    def to_dict(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class BinancePublicHistorySourceStep:
    source_kind: BinancePublicHistorySourceKind
    reason: str
    archive_plan: BinanceArchiveObjectPlan | None = None
    archive_evidence: BinancePublicHistoryArchiveEvidence | None = None
    rest_request: BinanceRestPageRequest | None = None
    rest_coverage: BinanceKlineRestCoverage | None = None

    def __post_init__(self) -> None:
        archive = self.archive_plan is not None
        rest = self.rest_request is not None
        if archive == rest:
            raise ValueError("a source step must contain exactly one source request")
        if archive and self.source_kind not in {
            BinancePublicHistorySourceKind.ARCHIVE_DAILY,
            BinancePublicHistorySourceKind.ARCHIVE_MONTHLY,
        }:
            raise ValueError("archive step has an inconsistent source kind")
        if archive and self.archive_evidence is None:
            raise ValueError("archive steps require exact object evidence")
        if not archive and self.archive_evidence is not None:
            raise ValueError("REST steps cannot carry archive evidence")
        if rest and self.source_kind is not BinancePublicHistorySourceKind.REST:
            raise ValueError("REST step has an inconsistent source kind")
        if rest and self.rest_coverage is None:
            raise ValueError("REST steps require exact coverage evidence")


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryObligationPlan:
    request_index: int
    required_range: TimeRange
    candidates: tuple[BinancePublicHistorySourceStep, ...]
    initial_unmet_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryAcquisitionPlan:
    requests: tuple[BinancePublicHistoryAcquisitionRequest, ...]
    coverage: BinancePublicHistoryCoverage
    budget: BinancePublicHistoryAcquisitionBudget
    obligations: tuple[BinancePublicHistoryObligationPlan, ...]
    plan_id: str
    archive_objects_planned: int
    rest_page_upper_bound: int
    http_request_upper_bound: int


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryRawReference:
    data_type: BinancePublicHistoryDataType
    subject: BinancePublicHistorySubject
    source_kind: BinancePublicHistorySourceKind
    object_identity: RawObjectIdentity
    revision: RawRevisionIdentity
    artifact_path: Path
    actual_record_range: TimeRange
    record_count: int


@dataclass(frozen=True, slots=True)
class BinancePublicHistorySourceResult:
    step: BinancePublicHistorySourceStep
    status: str
    detail: str
    raw_references: tuple[BinancePublicHistoryRawReference, ...] = ()
    attempts_used: int = 0
    pages_used: int = 0
    actual_record_range: TimeRange | None = None
    rest_pages: tuple[BinanceKlineRestPageResult, ...] = ()


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryObligationResult:
    plan: BinancePublicHistoryObligationPlan
    sources: tuple[BinancePublicHistorySourceResult, ...]
    satisfied: bool
    unmet_reason: str | None


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryConflict:
    data_type: BinancePublicHistoryDataType
    subject: BinancePublicHistorySubject
    record_key: int
    left: BinancePublicHistoryRawReference
    right: BinancePublicHistoryRawReference
    detail: str


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryRequestResult:
    request: BinancePublicHistoryAcquisitionRequest
    obligations: tuple[BinancePublicHistoryObligationResult, ...]
    raw_references: tuple[BinancePublicHistoryRawReference, ...]
    satisfied_ranges: tuple[TimeRange, ...]
    unmet_ranges: tuple[TimeRange, ...]
    duplicate_overlap_records: int
    conflicts: tuple[BinancePublicHistoryConflict, ...]
    status: BinancePublicHistoryRunStatus

    @property
    def completed(self) -> bool:
        return self.status is BinancePublicHistoryRunStatus.COMPLETED


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryRunResult:
    plan_id: str
    requests: tuple[BinancePublicHistoryRequestResult, ...]
    status: BinancePublicHistoryRunStatus
    http_requests_used: int
    archive_objects_used: int
    rest_pages_used: int
    downloaded_bytes: int
    elapsed_seconds: float
    termination_reason: str

    @property
    def completed(self) -> bool:
        return self.status is BinancePublicHistoryRunStatus.COMPLETED


def _next_month(value: date) -> date:
    return (
        date(value.year + 1, 1, 1)
        if value.month == 12
        else date(value.year, value.month + 1, 1)
    )


def _midnight(value: date) -> datetime:
    return datetime.combine(value, datetime_time.min, tzinfo=UTC)


def _boundary_range(boundary: BinanceArchiveObjectBoundary) -> TimeRange:
    start = _midnight(boundary.period_start)
    end = (
        _midnight(_next_month(boundary.period_start))
        if boundary.granularity is BinanceArchiveObjectGranularity.MONTH
        else start + timedelta(days=1)
    )
    return TimeRange(start=start, end=end)


def _intersection(left: TimeRange, right: TimeRange) -> TimeRange | None:
    start = max(left.start, right.start)
    end = min(left.end, right.end)
    return TimeRange(start=start, end=end) if start < end else None


_ENDPOINTS: Final = {
    BinancePublicHistoryDataType.CONTRACT_KLINE: BinanceRestEndpoint.CONTRACT_KLINES,
    BinancePublicHistoryDataType.MARK_PRICE_KLINE: BinanceRestEndpoint.MARK_PRICE_KLINES,
    BinancePublicHistoryDataType.INDEX_PRICE_KLINE: BinanceRestEndpoint.INDEX_PRICE_KLINES,
    BinancePublicHistoryDataType.SETTLED_FUNDING_RATE: BinanceRestEndpoint.FUNDING_RATE_HISTORY,
}


def _archive_plan(
    request: BinancePublicHistoryAcquisitionRequest,
    boundary: BinanceArchiveObjectBoundary,
) -> BinanceArchiveObjectPlan:
    monthly = boundary.granularity is BinanceArchiveObjectGranularity.MONTH
    cadence = "monthly" if monthly else "daily"
    suffix = (
        boundary.period_start.strftime("%Y-%m")
        if monthly
        else boundary.period_start.isoformat()
    )
    subject = str(request.subject)
    if request.data_type is BinancePublicHistoryDataType.CONTRACT_KLINE:
        directory = "klines"
        filename = f"{subject}-1m-{suffix}.zip"
    elif request.data_type is BinancePublicHistoryDataType.MARK_PRICE_KLINE:
        directory = "markPriceKlines"
        filename = f"{subject}-1m-{suffix}.zip"
    elif request.data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE:
        directory = "indexPriceKlines"
        filename = f"{subject}-1m-{suffix}.zip"
    else:
        if not monthly:
            raise ValueError("settled funding has no daily archive source")
        directory = "fundingRate"
        filename = f"{subject}-fundingRate-{suffix}.zip"
    interval_path = "/1m" if request.data_type in _KLINE_TYPES else ""
    object_key = (
        f"data/futures/um/{cadence}/{directory}/{subject}{interval_path}/{filename}"
    )
    typed_request = BinancePublicHistoryRequest(
        subject=request.subject,
        data_type=request.data_type,
        request_range=request.request_range,
        source_kind=(
            BinancePublicHistorySourceKind.ARCHIVE_MONTHLY
            if monthly
            else BinancePublicHistorySourceKind.ARCHIVE_DAILY
        ),
        interval=(
            BinanceKlineInterval.ONE_MINUTE
            if request.data_type in _KLINE_TYPES
            else None
        ),
        archive_object_boundary=boundary,
    )
    return BinanceArchiveObjectPlan(
        request=typed_request,
        object_key=object_key,
        url=f"{_ARCHIVE_ROOT}/{object_key}",
        checksum_url=f"{_ARCHIVE_ROOT}/{object_key}.CHECKSUM",
        member_name=filename.removesuffix(".zip") + ".csv",
    )


def _archive_evidence(
    coverage: BinancePublicHistoryCoverage,
    request: BinancePublicHistoryAcquisitionRequest,
    boundary: BinanceArchiveObjectBoundary,
) -> BinancePublicHistoryArchiveEvidence | None:
    return next(
        (
            item
            for item in coverage.archive_objects
            if item.data_type is request.data_type
            and item.subject == request.subject
            and item.boundary == boundary
        ),
        None,
    )


def _rest_evidence(
    coverage: BinancePublicHistoryCoverage,
    request: BinancePublicHistoryAcquisitionRequest,
    required: TimeRange,
) -> BinanceKlineRestCoverage | None:
    endpoint = _ENDPOINTS[request.data_type]
    matches = [
        item
        for item in coverage.rest_windows
        if item.endpoint is endpoint
        and item.subject == request.subject
        and item.allowed_range.start <= required.start
        and item.allowed_range.end >= required.end
    ]
    return matches[0] if matches else None


def _rest_step(
    request: BinancePublicHistoryAcquisitionRequest,
    required: TimeRange,
    evidence: BinanceKlineRestCoverage,
    *,
    reason: str,
) -> BinancePublicHistorySourceStep:
    endpoint = _ENDPOINTS[request.data_type]
    return BinancePublicHistorySourceStep(
        source_kind=BinancePublicHistorySourceKind.REST,
        reason=reason,
        rest_request=BinanceRestPageRequest(
            endpoint=endpoint,
            subject=request.subject,
            caller_range=required,
            interval=(
                BinanceKlineInterval.ONE_MINUTE
                if request.data_type in _KLINE_TYPES
                else None
            ),
            limit=endpoint.maximum_limit,
        ),
        rest_coverage=evidence,
    )


def _archive_step(
    request: BinancePublicHistoryAcquisitionRequest,
    boundary: BinanceArchiveObjectBoundary,
    evidence: BinancePublicHistoryArchiveEvidence,
    *,
    reason: str | None = None,
) -> BinancePublicHistorySourceStep:
    plan = _archive_plan(request, boundary)
    return BinancePublicHistorySourceStep(
        source_kind=plan.request.source_kind,
        reason=reason
        or (
            f"explicit {evidence.status.value} archive evidence "
            f"{evidence.evidence_version} for {boundary.period_start.isoformat()}"
        ),
        archive_plan=plan,
        archive_evidence=evidence,
    )


def _plan_archive_obligation(
    request_index: int,
    request: BinancePublicHistoryAcquisitionRequest,
    boundary: BinanceArchiveObjectBoundary,
    coverage: BinancePublicHistoryCoverage,
    *,
    preferred: tuple[BinancePublicHistorySourceStep, ...] = (),
) -> BinancePublicHistoryObligationPlan:
    required = _intersection(request.request_range, _boundary_range(boundary))
    assert required is not None
    evidence = _archive_evidence(coverage, request, boundary)
    rest = _rest_evidence(coverage, request, required)
    candidates: list[BinancePublicHistorySourceStep] = list(preferred)
    if evidence is not None and evidence.status in {
        BinanceArchiveEvidenceStatus.SUPPORTED,
        BinanceArchiveEvidenceStatus.NOT_FOUND,
    }:
        candidates.append(_archive_step(request, boundary, evidence))
        if rest is not None:
            candidates.append(
                _rest_step(
                    request,
                    required,
                    rest,
                    reason="proven bounded REST fallback after archive unavailable/partial",
                )
            )
    elif rest is not None:
        candidates.append(
            _rest_step(
                request,
                required,
                rest,
                reason="archive boundary unavailable; exact REST window is proven",
            )
        )
    reason = None
    if not candidates:
        status = evidence.status.value if evidence is not None else "missing"
        reason = f"archive evidence is {status} and no matching REST boundary is proven"
    return BinancePublicHistoryObligationPlan(
        request_index=request_index,
        required_range=required,
        candidates=tuple(candidates),
        initial_unmet_reason=reason,
    )


def _plan_request(
    request_index: int,
    request: BinancePublicHistoryAcquisitionRequest,
    coverage: BinancePublicHistoryCoverage,
) -> tuple[BinancePublicHistoryObligationPlan, ...]:
    if request.purpose is not BinancePublicHistoryPurpose.BACKFILL:
        evidence = _rest_evidence(coverage, request, request.request_range)
        if evidence is None:
            return (
                BinancePublicHistoryObligationPlan(
                    request_index=request_index,
                    required_range=request.request_range,
                    candidates=(),
                    initial_unmet_reason="rest_boundary_unknown",
                ),
            )
        return (
            BinancePublicHistoryObligationPlan(
                request_index=request_index,
                required_range=request.request_range,
                candidates=(
                    _rest_step(
                        request,
                        request.request_range,
                        evidence,
                        reason=(
                            "explicit recent REST window"
                            if request.purpose is BinancePublicHistoryPurpose.RECENT
                            else f"explicit gap REST window: {request.gap_reason}"
                        ),
                    ),
                ),
            ),
        )

    obligations: list[BinancePublicHistoryObligationPlan] = []
    if request.data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE:
        cursor = date(
            request.request_range.start.year, request.request_range.start.month, 1
        )
        final = date(
            (request.request_range.end - timedelta(microseconds=1)).year,
            (request.request_range.end - timedelta(microseconds=1)).month,
            1,
        )
        while cursor <= final:
            boundary = BinanceArchiveObjectBoundary.month(cursor.year, cursor.month)
            obligations.append(
                _plan_archive_obligation(request_index, request, boundary, coverage)
            )
            cursor = _next_month(cursor)
        return tuple(obligations)

    cursor = request.request_range.start.date()
    final_day = (request.request_range.end - timedelta(microseconds=1)).date()
    while cursor <= final_day:
        month = BinanceArchiveObjectBoundary.month(cursor.year, cursor.month)
        next_month = _next_month(cursor)
        month_evidence = _archive_evidence(coverage, request, month)
        full_month = (
            cursor.day == 1
            and request.request_range.start <= _midnight(cursor)
            and request.request_range.end >= _midnight(next_month)
            and month_evidence is not None
            and month_evidence.status is BinanceArchiveEvidenceStatus.SUPPORTED
        )
        if full_month:
            assert month_evidence is not None
            monthly = _archive_step(
                request,
                month,
                month_evidence,
                reason=(
                    "preferred explicitly supported monthly archive; proven daily "
                    "objects remain bounded runtime fallbacks"
                ),
            )
            day = cursor
            while day < next_month:
                obligations.append(
                    _plan_archive_obligation(
                        request_index,
                        request,
                        BinanceArchiveObjectBoundary.day(day),
                        coverage,
                        preferred=(monthly,),
                    )
                )
                day += timedelta(days=1)
            cursor = next_month
        else:
            obligations.append(
                _plan_archive_obligation(
                    request_index,
                    request,
                    BinanceArchiveObjectBoundary.day(cursor),
                    coverage,
                )
            )
            cursor += timedelta(days=1)
    return tuple(obligations)


def _backfill_obligation_count(
    request: BinancePublicHistoryAcquisitionRequest,
    coverage: BinancePublicHistoryCoverage,
) -> int:
    """Count source windows without iterating over the requested calendar range."""
    if request.purpose is not BinancePublicHistoryPurpose.BACKFILL:
        return 0
    last = request.request_range.end - timedelta(microseconds=1)
    if request.data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE:
        return (
            (last.year - request.request_range.start.year) * 12
            + last.month
            - request.request_range.start.month
            + 1
        )

    count = (last.date() - request.request_range.start.date()).days + 1
    supported_month_ranges: list[TimeRange] = []
    for item in coverage.archive_objects:
        if (
            item.data_type is not request.data_type
            or item.subject != request.subject
            or item.status is not BinanceArchiveEvidenceStatus.SUPPORTED
            or item.boundary.granularity is not BinanceArchiveObjectGranularity.MONTH
        ):
            continue
        boundary_range = _boundary_range(item.boundary)
        if (
            request.request_range.start <= boundary_range.start
            and request.request_range.end >= boundary_range.end
        ):
            days = (boundary_range.end.date() - boundary_range.start.date()).days
            count -= days - 1
            supported_month_ranges.append(boundary_range)
    count += sum(
        1
        for item in coverage.archive_objects
        if item.data_type is request.data_type
        and item.subject == request.subject
        and item.status
        in {
            BinanceArchiveEvidenceStatus.SUPPORTED,
            BinanceArchiveEvidenceStatus.NOT_FOUND,
        }
        and item.boundary.granularity is BinanceArchiveObjectGranularity.DAY
        and any(
            month.start <= _boundary_range(item.boundary).start < month.end
            for month in supported_month_ranges
        )
    )
    return count


def _rest_coverage_payload(item: BinanceKlineRestCoverage) -> dict[str, object]:
    return {
        "endpoint": item.endpoint.value,
        "subject": _subject_payload(item.subject),
        "allowed_range": item.allowed_range.to_dict(),
        "evidence_version": item.evidence_version,
        "evidence_reference": item.evidence_reference,
        "evidence_sha256": item.evidence_sha256,
        "observed_at": format_utc(item.observed_at),
        "normalized_params": dict(item.normalized_params),
        "response_sha256": item.response_sha256,
        "actual_range": item.actual_range.to_dict(),
        "status": item.status.value,
    }


def _archive_plan_payload(item: BinanceArchiveObjectPlan) -> dict[str, object]:
    return {
        "request": item.request.to_dict(),
        "object_key": item.object_key,
        "url": item.url,
        "checksum_url": item.checksum_url,
        "member_name": item.member_name,
    }


def _plan_payload(
    requests: tuple[BinancePublicHistoryAcquisitionRequest, ...],
    coverage: BinancePublicHistoryCoverage,
    budget: BinancePublicHistoryAcquisitionBudget,
    obligations: tuple[BinancePublicHistoryObligationPlan, ...],
) -> dict[str, object]:
    return {
        "requests": [item.to_dict() for item in requests],
        "coverage": {
            "archive": [item.to_dict() for item in coverage.archive_objects],
            "rest": [_rest_coverage_payload(item) for item in coverage.rest_windows],
        },
        "budget": budget.to_dict(),
        "obligations": [
            {
                "request_index": item.request_index,
                "required_range": item.required_range.to_dict(),
                "initial_unmet_reason": item.initial_unmet_reason,
                "candidates": [
                    {
                        "source_kind": step.source_kind.value,
                        "reason": step.reason,
                        "archive": (
                            _archive_plan_payload(step.archive_plan)
                            if step.archive_plan is not None
                            else None
                        ),
                        "archive_evidence": (
                            step.archive_evidence.to_dict()
                            if step.archive_evidence is not None
                            else None
                        ),
                        "rest": (
                            step.rest_request.to_dict()
                            if step.rest_request is not None
                            else None
                        ),
                        "rest_coverage": (
                            _rest_coverage_payload(step.rest_coverage)
                            if step.rest_coverage is not None
                            else None
                        ),
                    }
                    for step in item.candidates
                ],
            }
            for item in obligations
        ],
    }


def _plan_id(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class _SharedBudgetExceeded(BinanceKlineRestBudgetExceeded):
    pass


@dataclass(slots=True)
class _SharedBudget:
    budget: BinancePublicHistoryAcquisitionBudget
    monotonic_clock: Callable[[], float]
    started_at: float
    http_requests: int = 0
    archive_objects: int = 0
    rest_pages: int = 0
    downloaded_bytes: int = 0
    waited_seconds: float = 0.0
    exhaustion_reason: str | None = None

    @property
    def elapsed(self) -> float:
        return max(
            max(0.0, self.monotonic_clock() - self.started_at),
            self.waited_seconds,
        )

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.budget.maximum_elapsed_seconds - self.elapsed)

    def before_http(self) -> None:
        if self.exhaustion_reason is not None:
            raise _SharedBudgetExceeded(self.exhaustion_reason)
        if self.remaining_seconds <= 0:
            self.exhaustion_reason = "maximum_elapsed_seconds exhausted"
            raise _SharedBudgetExceeded(self.exhaustion_reason)
        if self.http_requests >= self.budget.maximum_http_requests:
            self.exhaustion_reason = "maximum_http_requests exhausted"
            raise _SharedBudgetExceeded(self.exhaustion_reason)
        if self.downloaded_bytes >= self.budget.maximum_download_bytes:
            self.exhaustion_reason = "maximum_download_bytes exhausted"
            raise _SharedBudgetExceeded(self.exhaustion_reason)
        self.http_requests += 1

    def after_http(self, body: bytes) -> None:
        self.downloaded_bytes += len(body)
        if len(body) > self.budget.maximum_response_bytes:
            self.exhaustion_reason = "maximum_response_bytes exceeded"
            return
        if self.downloaded_bytes > self.budget.maximum_download_bytes:
            self.exhaustion_reason = "maximum_download_bytes exhausted"
            return
        if self.remaining_seconds <= 0:
            self.exhaustion_reason = "maximum_elapsed_seconds exhausted"


def _artifact_reference(
    store: RawStore,
    artifact: RawArtifact,
) -> BinancePublicHistoryRawReference:
    revision = artifact.revision_identity
    if revision is None:
        raise RawStoreError("completed acquisition did not expose an exact revision")
    source = artifact.manifest.object_identity.source
    return BinancePublicHistoryRawReference(
        data_type=source.data_type,
        subject=source.subject,
        source_kind=source.source_kind,
        object_identity=artifact.manifest.object_identity,
        revision=revision,
        artifact_path=artifact.path,
        actual_record_range=artifact.manifest.actual_record_range,
        record_count=artifact.manifest.record_count,
    )


def _reference_for_path(
    store: RawStore, identity: RawObjectIdentity, path: Path
) -> BinancePublicHistoryRawReference:
    for artifact in store.list_verified_revisions(identity):
        if artifact.path == path:
            return _artifact_reference(store, artifact)
    raise RawStoreError("acquisition path is not an exact verified Raw revision")


def _semantic_records(
    data_type: BinancePublicHistoryDataType, frame: pl.DataFrame
) -> dict[int, tuple[Decimal, ...]]:
    names = set(frame.columns)
    if data_type is BinancePublicHistoryDataType.CONTRACT_KLINE:
        count = "trade_count" if "trade_count" in names else "count"
        columns: tuple[str, ...] = (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            count,
            "taker_buy_volume",
            "taker_buy_quote_volume",
        )
        key = "open_time"
    elif data_type is BinancePublicHistoryDataType.MARK_PRICE_KLINE:
        columns = ("mark_open", "mark_high", "mark_low", "mark_close")
        key = "open_time"
    elif data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE:
        columns = ("index_open", "index_high", "index_low", "index_close")
        key = "open_time"
    else:
        key = "fundingTime" if "fundingTime" in names else "calc_time"
        rate = "fundingRate" if "fundingRate" in names else "last_funding_rate"
        columns = (rate,)
    return {
        int(row[key]): tuple(Decimal(str(row[column])) for column in columns)
        for row in frame.select((key, *columns)).iter_rows(named=True)
    }


class BinancePublicHistoryAcquisition:
    """Plan and run finite four-family public-history acquisitions."""

    def __init__(
        self,
        *,
        archive_http_get: ArchiveHttpGet | None = None,
        rest_http_get: BinanceKlineRestHttpGet | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        wait: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._archive_http_get = archive_http_get or _default_archive_http_get
        self._uses_default_archive_http_get = archive_http_get is None
        self._rest_http_get = rest_http_get or _default_rest_http_get
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._wait = wait or time.sleep
        self._cancelled = cancelled or (lambda: False)

    def plan(
        self,
        requests: Sequence[BinancePublicHistoryAcquisitionRequest],
        coverage: BinancePublicHistoryCoverage,
        budget: BinancePublicHistoryAcquisitionBudget,
    ) -> BinancePublicHistoryAcquisitionPlan:
        """Return a deterministic plan without network or Raw filesystem I/O."""
        if not isinstance(requests, Sequence) or isinstance(requests, (str, bytes)):
            raise TypeError("requests must be an explicit finite sequence")
        frozen_requests = tuple(requests)
        if not frozen_requests:
            raise ValueError("requests must not be empty")
        if any(
            not isinstance(item, BinancePublicHistoryAcquisitionRequest)
            for item in frozen_requests
        ):
            raise TypeError("requests must contain acquisition requests")
        if not isinstance(coverage, BinancePublicHistoryCoverage):
            raise TypeError("coverage must be a BinancePublicHistoryCoverage")
        if not isinstance(budget, BinancePublicHistoryAcquisitionBudget):
            raise TypeError("budget must be a BinancePublicHistoryAcquisitionBudget")
        source_window_count = sum(
            _backfill_obligation_count(request, coverage) for request in frozen_requests
        )
        if source_window_count > budget.maximum_archive_objects:
            raise ValueError(
                "planned backfill source windows exceed maximum_archive_objects"
            )
        obligations = tuple(
            obligation
            for index, request in enumerate(frozen_requests)
            for obligation in _plan_request(index, request, coverage)
        )
        archive_keys = {
            (obligation.request_index, step.archive_plan.object_key)
            for obligation in obligations
            for step in obligation.candidates
            if step.archive_plan is not None
        }
        archive_count = len(archive_keys)
        if archive_count > budget.maximum_archive_objects:
            raise ValueError("planned archive objects exceed maximum_archive_objects")
        rest_actions = sum(
            1
            for obligation in obligations
            for step in obligation.candidates
            if step.rest_request is not None
        )
        preferred_archive_keys = {
            (obligation.request_index, obligation.candidates[0].archive_plan.object_key)
            for obligation in obligations
            if obligation.candidates
            and obligation.candidates[0].archive_plan is not None
        }
        preferred_rest_actions = sum(
            1
            for obligation in obligations
            if obligation.candidates
            and obligation.candidates[0].rest_request is not None
        )
        minimum_http = len(preferred_archive_keys) * 2 + preferred_rest_actions
        if minimum_http > budget.maximum_http_requests:
            raise ValueError(
                "plan cannot make minimum progress within maximum_http_requests"
            )
        rest_upper = budget.maximum_rest_pages if rest_actions else 0
        http_upper = min(
            budget.maximum_http_requests,
            archive_count * 2 * budget.maximum_attempts_per_object
            + (
                budget.maximum_rest_pages * budget.maximum_attempts_per_page
                if rest_actions
                else 0
            ),
        )
        payload = _plan_payload(frozen_requests, coverage, budget, obligations)
        return BinancePublicHistoryAcquisitionPlan(
            requests=frozen_requests,
            coverage=coverage,
            budget=budget,
            obligations=obligations,
            plan_id=_plan_id(payload),
            archive_objects_planned=archive_count,
            rest_page_upper_bound=rest_upper,
            http_request_upper_bound=http_upper,
        )

    def _validate_plan(self, plan: BinancePublicHistoryAcquisitionPlan) -> None:
        if not isinstance(plan, BinancePublicHistoryAcquisitionPlan):
            raise TypeError("plan must be a BinancePublicHistoryAcquisitionPlan")
        expected = self.plan(plan.requests, plan.coverage, plan.budget)
        if plan != expected:
            raise ValueError(
                "plan does not match the controlled plan derived from its requests, "
                "evidence, and budget"
            )

    def _archive_transport(self, shared: _SharedBudget) -> ArchiveHttpGet:
        def get(url: str, timeout: float) -> ArchiveHttpResponse:
            shared.before_http()
            remaining_download = (
                shared.budget.maximum_download_bytes - shared.downloaded_bytes
            )
            response_limit = min(
                shared.budget.maximum_response_bytes, remaining_download
            )
            bounded_timeout = min(timeout, shared.remaining_seconds)
            if self._uses_default_archive_http_get:
                response = _default_archive_http_get(
                    url,
                    bounded_timeout,
                    maximum_response_bytes=response_limit,
                )
            else:
                response = self._archive_http_get(url, bounded_timeout)
            if len(response.body) > response_limit:
                response = replace(
                    response,
                    body=response.body[:response_limit],
                    complete=False,
                )
            shared.after_http(response.body)
            if not response.complete and shared.exhaustion_reason is None:
                shared.exhaustion_reason = (
                    "maximum_response_bytes exceeded"
                    if shared.budget.maximum_response_bytes <= remaining_download
                    else "maximum_download_bytes exhausted"
                )
            if shared.exhaustion_reason is not None:
                raise ArchiveHttpBudgetExceeded(
                    shared.exhaustion_reason,
                    response=response,
                )
            return response

        return get

    def _rest_transport(self, shared: _SharedBudget) -> BinanceKlineRestHttpGet:
        def get(
            url: str, timeout: float, maximum_response_bytes: int
        ) -> BinanceKlineRestHttpResponse:
            shared.before_http()
            remaining_download = (
                shared.budget.maximum_download_bytes - shared.downloaded_bytes
            )
            response_limit = min(
                maximum_response_bytes,
                shared.budget.maximum_response_bytes,
                remaining_download,
            )
            cumulative_limit_is_binding = remaining_download <= min(
                maximum_response_bytes,
                shared.budget.maximum_response_bytes,
            )
            response = self._rest_http_get(
                url,
                min(timeout, shared.remaining_seconds),
                response_limit,
            )
            if len(response.body) > response_limit:
                retained_bytes = (
                    response_limit
                    if cumulative_limit_is_binding
                    else response_limit + 1
                )
                response = replace(
                    response,
                    body=response.body[:retained_bytes],
                    complete=False,
                )
            shared.after_http(response.body)
            if cumulative_limit_is_binding and len(response.body) >= remaining_download:
                shared.exhaustion_reason = "maximum_download_bytes exhausted"
            if shared.exhaustion_reason is not None:
                response = replace(response, complete=False)
            return response

        return get

    def _shared_wait(self, shared: _SharedBudget) -> Callable[[float], None]:
        def wait(seconds: float) -> None:
            if seconds > shared.remaining_seconds:
                shared.exhaustion_reason = "retry wait exceeds remaining elapsed budget"
                raise _SharedBudgetExceeded(shared.exhaustion_reason)
            self._wait(seconds)
            shared.waited_seconds += seconds

        return wait

    def _run_archive(
        self,
        request: BinancePublicHistoryAcquisitionRequest,
        step: BinancePublicHistorySourceStep,
        store: RawStore,
        shared: _SharedBudget,
    ) -> BinancePublicHistorySourceResult:
        assert step.archive_plan is not None
        shared.archive_objects += 1
        transport = self._archive_transport(shared)
        timeout = min(shared.budget.timeout_seconds, shared.remaining_seconds)
        first_http = shared.http_requests
        status: BinanceArchiveAcquisitionStatus | None = None
        artifact_path: Path | None = None
        detail: str | None = None
        actual_record_range: TimeRange | None = None
        for attempt in range(1, shared.budget.maximum_attempts_per_object + 1):
            if request.data_type is BinancePublicHistoryDataType.CONTRACT_KLINE:
                contract_outcome = BinanceContractKlineBackfill(
                    store, http_get=transport, timeout=timeout, clock=self._clock
                )._run_controlled_plan(step.archive_plan)
                status = contract_outcome.status
                artifact_path = contract_outcome.artifact_path
                detail = contract_outcome.detail
                actual_record_range = contract_outcome.actual_record_range
            elif request.data_type is BinancePublicHistoryDataType.MARK_PRICE_KLINE:
                mark_outcome = BinanceMarkPriceKlineBackfill(
                    store, http_get=transport, timeout=timeout, clock=self._clock
                )._run_controlled_plan(step.archive_plan)
                status = mark_outcome.status
                artifact_path = mark_outcome.artifact_path
                detail = mark_outcome.detail
                actual_record_range = mark_outcome.actual_record_range
            elif request.data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE:
                index_outcome = BinanceIndexPriceKlineBackfill(
                    store, http_get=transport, timeout=timeout, clock=self._clock
                )._run_controlled_plan(step.archive_plan)
                status = index_outcome.status
                artifact_path = index_outcome.artifact_path
                detail = index_outcome.detail
                actual_record_range = index_outcome.actual_record_range
            else:
                funding_outcome = BinanceFundingRateBackfill(
                    store, http_get=transport, timeout=timeout, clock=self._clock
                )._run_controlled_plan(step.archive_plan)
                status = funding_outcome.status
                artifact_path = funding_outcome.artifact_path
                detail = funding_outcome.detail
                actual_record_range = funding_outcome.actual_record_range
            if status is not BinanceArchiveAcquisitionStatus.RETRYABLE_FAILURE:
                break
            if (
                shared.exhaustion_reason is not None
                or attempt == shared.budget.maximum_attempts_per_object
            ):
                break
            delay = float(min(2 ** (attempt - 1), 60))
            try:
                self._shared_wait(shared)(delay)
            except _SharedBudgetExceeded as error:
                status = BinanceArchiveAcquisitionStatus.BUDGET_EXHAUSTED
                detail = f"{detail or 'archive retryable failure'}; {error}"
                break
        assert status is not None
        references: tuple[BinancePublicHistoryRawReference, ...] = ()
        if artifact_path is not None:
            reference = _reference_for_path(
                store,
                RawObjectIdentity.from_request(step.archive_plan.request),
                artifact_path,
            )
            references = (reference,)
            evidence = step.archive_evidence
            assert evidence is not None
            observed_sha256 = (
                reference.revision.verified_upstream_checksum.removeprefix("sha256:")
            )
            if (
                evidence.object_sha256 is not None
                and observed_sha256 != evidence.object_sha256
            ):
                status = BinanceArchiveAcquisitionStatus.CONFLICT
                detail = (
                    "archive revision differs from the explicitly supplied object digest; "
                    "the new immutable revision was preserved"
                )
            elif (
                evidence.actual_range is not None
                and reference.actual_record_range != evidence.actual_range
            ):
                status = BinanceArchiveAcquisitionStatus.CONFLICT
                detail = (
                    "archive record range differs from the explicitly supplied evidence; "
                    "the immutable revision was preserved"
                )
        return BinancePublicHistorySourceResult(
            step=step,
            status=status.value,
            detail=detail or status.value,
            raw_references=references,
            attempts_used=shared.http_requests - first_http,
            actual_record_range=(
                references[0].actual_record_range if references else actual_record_range
            ),
        )

    def _run_rest(
        self,
        request: BinancePublicHistoryAcquisitionRequest,
        step: BinancePublicHistorySourceStep,
        store: RawStore,
        shared: _SharedBudget,
    ) -> BinancePublicHistorySourceResult:
        assert step.rest_request is not None and step.rest_coverage is not None
        remaining_pages = shared.budget.maximum_rest_pages - shared.rest_pages
        remaining_http = shared.budget.maximum_http_requests - shared.http_requests
        if remaining_pages <= 0 or remaining_http <= 0 or shared.remaining_seconds <= 0:
            shared.exhaustion_reason = (
                "shared REST page, HTTP, or elapsed budget exhausted"
            )
            return BinancePublicHistorySourceResult(
                step=step,
                status=BinanceKlineRestStatus.BUDGET_EXHAUSTED.value,
                detail=shared.exhaustion_reason,
            )
        rest_budget = BinanceKlineRestBudget(
            timeout_seconds=min(
                shared.budget.timeout_seconds, shared.remaining_seconds
            ),
            maximum_pages=min(remaining_pages, remaining_http),
            maximum_attempts_per_page=min(
                shared.budget.maximum_attempts_per_page, remaining_http
            ),
            maximum_elapsed_seconds=shared.remaining_seconds,
            maximum_response_bytes=min(
                shared.budget.maximum_response_bytes,
                max(1, shared.budget.maximum_download_bytes - shared.downloaded_bytes),
            ),
        )
        acquisition = (
            BinanceFundingRateRestAcquisition(
                store,
                http_get=self._rest_transport(shared),
                clock=self._clock,
                wait=self._shared_wait(shared),
                monotonic_clock=self._monotonic_clock,
            )
            if request.data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
            else BinanceKlineRestAcquisition(
                store,
                http_get=self._rest_transport(shared),
                clock=self._clock,
                wait=self._shared_wait(shared),
                monotonic_clock=self._monotonic_clock,
            )
        )
        result: BinanceKlineRestRunResult = acquisition.run(
            step.rest_request, step.rest_coverage, rest_budget
        )
        shared.rest_pages += result.pages_used
        if result.status is BinanceKlineRestStatus.BUDGET_EXHAUSTED:
            shared.exhaustion_reason = (
                shared.exhaustion_reason or result.termination_reason
            )
        elif (
            result.status is not BinanceKlineRestStatus.COMPLETE
            and shared.http_requests >= shared.budget.maximum_http_requests
        ):
            shared.exhaustion_reason = "maximum_http_requests exhausted"
        references: list[BinancePublicHistoryRawReference] = []
        for page in result.pages:
            if page.revision is None or page.artifact_path is None:
                continue
            artifact = store.read_revision(
                RawObjectIdentity.from_rest_page_request(page.request), page.revision
            )
            references.append(_artifact_reference(store, artifact))
        return BinancePublicHistorySourceResult(
            step=step,
            status=result.status.value,
            detail=result.termination_reason,
            raw_references=tuple(references),
            attempts_used=result.attempts_used,
            pages_used=result.pages_used,
            actual_record_range=result.actual_record_range,
            rest_pages=result.pages,
        )

    @staticmethod
    def _source_satisfied(
        request: BinancePublicHistoryAcquisitionRequest,
        required_range: TimeRange,
        result: BinancePublicHistorySourceResult,
    ) -> bool:
        satisfied = result.status in {
            BinanceArchiveAcquisitionStatus.PUBLISHED.value,
            BinanceArchiveAcquisitionStatus.EXISTING.value,
            BinanceKlineRestStatus.COMPLETE.value,
        }
        if (
            satisfied
            and request.data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
            and result.step.rest_request is not None
        ):
            actual = result.actual_record_range
            return (
                actual is not None
                and actual.start <= required_range.start
                and actual.end >= required_range.end
            )
        return satisfied

    def _compare(
        self,
        request: BinancePublicHistoryAcquisitionRequest,
        references: tuple[BinancePublicHistoryRawReference, ...],
        store: RawStore,
    ) -> tuple[int, tuple[BinancePublicHistoryConflict, ...]]:
        duplicates = 0
        conflicts: list[BinancePublicHistoryConflict] = []
        loaded = [
            (
                reference,
                _semantic_records(
                    request.data_type,
                    store.read_revision(
                        reference.object_identity, reference.revision
                    ).frame,
                ),
            )
            for reference in references
        ]
        for left_index, (left_ref, left_rows) in enumerate(loaded):
            for right_ref, right_rows in loaded[left_index + 1 :]:
                for key in sorted(left_rows.keys() & right_rows.keys()):
                    if left_rows[key] == right_rows[key]:
                        duplicates += 1
                    else:
                        conflicts.append(
                            BinancePublicHistoryConflict(
                                data_type=request.data_type,
                                subject=request.subject,
                                record_key=key,
                                left=left_ref,
                                right=right_ref,
                                detail="same semantic key has different source values",
                            )
                        )
        return duplicates, tuple(conflicts)

    @staticmethod
    def _record_overlap_local_failure(
        result: BinancePublicHistoryRequestResult,
        *,
        detail: str,
        identity: RawObjectIdentity | None,
    ) -> BinancePublicHistoryRequestResult:
        """Keep valid references while making a failed overlap audit explicit."""
        affected = False
        obligations: list[BinancePublicHistoryObligationResult] = []
        for obligation in result.obligations:
            obligation_affected = False
            sources: list[BinancePublicHistorySourceResult] = []
            for source in obligation.sources:
                source_affected = any(
                    identity is None or reference.object_identity == identity
                    for reference in source.raw_references
                )
                if source_affected:
                    affected = True
                    obligation_affected = True
                    source = replace(
                        source,
                        status=BinanceArchiveAcquisitionStatus.LOCAL_FAILURE.value,
                        detail=detail,
                    )
                sources.append(source)
            obligations.append(
                replace(
                    obligation,
                    sources=tuple(sources),
                    satisfied=(False if obligation_affected else obligation.satisfied),
                    unmet_reason=(
                        detail if obligation_affected else obligation.unmet_reason
                    ),
                )
            )
        if not affected:
            return result
        frozen_obligations = tuple(obligations)
        return replace(
            result,
            obligations=frozen_obligations,
            satisfied_ranges=tuple(
                obligation.plan.required_range
                for obligation in frozen_obligations
                if obligation.satisfied
            ),
            unmet_ranges=tuple(
                obligation.plan.required_range
                for obligation in frozen_obligations
                if not obligation.satisfied
            ),
            status=(
                BinancePublicHistoryRunStatus.PARTIAL
                if result.raw_references
                else BinancePublicHistoryRunStatus.FAILED
            ),
        )

    def run(
        self, plan: BinancePublicHistoryAcquisitionPlan
    ) -> BinancePublicHistoryRunResult:
        """Execute the exact plan with shared limits and immutable Raw outputs."""
        self._validate_plan(plan)
        shared = _SharedBudget(
            plan.budget, self._monotonic_clock, self._monotonic_clock()
        )
        stores = {
            request.output_root: RawStore(request.output_root, clock=self._clock)
            for request in plan.requests
        }
        by_request: list[list[BinancePublicHistoryObligationResult]] = [
            [] for _ in plan.requests
        ]
        archive_cache: dict[tuple[int, str], BinancePublicHistorySourceResult] = {}
        cancelled = False
        for obligation in plan.obligations:
            request = plan.requests[obligation.request_index]
            if obligation.initial_unmet_reason is not None:
                by_request[obligation.request_index].append(
                    BinancePublicHistoryObligationResult(
                        plan=obligation,
                        sources=(),
                        satisfied=False,
                        unmet_reason=obligation.initial_unmet_reason,
                    )
                )
                continue
            sources: list[BinancePublicHistorySourceResult] = []
            satisfied = False
            unmet: str | None = None
            for step_index, step in enumerate(obligation.candidates):
                if self._cancelled():
                    cancelled = True
                    unmet = "cancelled before source execution"
                    break
                if (
                    shared.exhaustion_reason is not None
                    or shared.remaining_seconds <= 0
                ):
                    shared.exhaustion_reason = (
                        shared.exhaustion_reason or "maximum_elapsed_seconds exhausted"
                    )
                    unmet = shared.exhaustion_reason
                    break
                try:
                    if step.archive_plan is not None:
                        cache_key = (
                            obligation.request_index,
                            step.archive_plan.object_key,
                        )
                        source = archive_cache.get(cache_key)
                        if source is None:
                            source = self._run_archive(
                                request, step, stores[request.output_root], shared
                            )
                            archive_cache[cache_key] = source
                    else:
                        source = self._run_rest(
                            request, step, stores[request.output_root], shared
                        )
                except _SharedBudgetExceeded as error:
                    shared.exhaustion_reason = str(error)
                    unmet = str(error)
                    break
                sources.append(source)
                if self._source_satisfied(request, obligation.required_range, source):
                    satisfied = True
                    break
                fallback_allowed = source.status in {
                    BinanceArchiveAcquisitionStatus.NOT_FOUND.value,
                    BinanceArchiveAcquisitionStatus.CHECKSUM_NOT_FOUND.value,
                    BinanceArchiveAcquisitionStatus.COVERAGE_GAP.value,
                }
                if not fallback_allowed or step_index + 1 >= len(obligation.candidates):
                    unmet = source.detail
                    break
            by_request[obligation.request_index].append(
                BinancePublicHistoryObligationResult(
                    plan=obligation,
                    sources=tuple(sources),
                    satisfied=satisfied,
                    unmet_reason=None
                    if satisfied
                    else (unmet or "source obligation was not satisfied"),
                )
            )
            if cancelled:
                break

        if cancelled or shared.exhaustion_reason is not None:
            processed = sum(len(items) for items in by_request)
            for obligation in plan.obligations[processed:]:
                by_request[obligation.request_index].append(
                    BinancePublicHistoryObligationResult(
                        plan=obligation,
                        sources=(),
                        satisfied=False,
                        unmet_reason=(
                            "cancelled" if cancelled else shared.exhaustion_reason
                        ),
                    )
                )

        request_results: list[BinancePublicHistoryRequestResult] = []
        for index, request in enumerate(plan.requests):
            obligations = tuple(by_request[index])
            references_by_revision = {
                (
                    reference.object_identity.object_id,
                    reference.revision.revision_id,
                ): reference
                for obligation in obligations
                for source in obligation.sources
                for reference in source.raw_references
            }
            references = tuple(references_by_revision.values())
            satisfied_ranges = tuple(
                item.plan.required_range for item in obligations if item.satisfied
            )
            unmet_ranges = tuple(
                item.plan.required_range for item in obligations if not item.satisfied
            )
            source_conflict = any(
                source.status == BinanceArchiveAcquisitionStatus.CONFLICT.value
                or source.status == BinanceKlineRestStatus.CONFLICT.value
                for obligation in obligations
                for source in obligation.sources
            )
            if source_conflict:
                status = BinancePublicHistoryRunStatus.CONFLICT
            elif not unmet_ranges and obligations:
                status = BinancePublicHistoryRunStatus.COMPLETED
            elif references:
                status = BinancePublicHistoryRunStatus.PARTIAL
            else:
                status = BinancePublicHistoryRunStatus.FAILED
            request_results.append(
                BinancePublicHistoryRequestResult(
                    request=request,
                    obligations=obligations,
                    raw_references=references,
                    satisfied_ranges=satisfied_ranges,
                    unmet_ranges=unmet_ranges,
                    duplicate_overlap_records=0,
                    conflicts=(),
                    status=status,
                )
            )
        comparison_groups: dict[
            tuple[Path, BinancePublicHistoryDataType, BinancePublicHistorySubject],
            list[int],
        ] = {}
        for index, item in enumerate(request_results):
            comparison_groups.setdefault(
                (
                    item.request.output_root,
                    item.request.data_type,
                    item.request.subject,
                ),
                [],
            ).append(index)
        for (root, _data_type, _subject), indices in comparison_groups.items():
            unique_references: dict[
                tuple[str, str], BinancePublicHistoryRawReference
            ] = {}
            for index in indices:
                for reference in request_results[index].raw_references:
                    unique_references[
                        (
                            reference.object_identity.object_id,
                            reference.revision.revision_id,
                        )
                    ] = reference
            touched_identities = {
                reference.object_identity.object_id: reference.object_identity
                for reference in unique_references.values()
            }
            for identity in touched_identities.values():
                try:
                    artifacts = stores[root].list_verified_revisions(identity)
                    for artifact in artifacts:
                        reference = _artifact_reference(stores[root], artifact)
                        unique_references[
                            (
                                reference.object_identity.object_id,
                                reference.revision.revision_id,
                            )
                        ] = reference
                except (RawStoreError, OSError) as error:
                    detail = f"Raw revision overlap inspection failed: {error}"
                    for index in indices:
                        request_results[index] = self._record_overlap_local_failure(
                            request_results[index],
                            detail=detail,
                            identity=identity,
                        )
            try:
                duplicates, conflicts = self._compare(
                    request_results[indices[0]].request,
                    tuple(unique_references.values()),
                    stores[root],
                )
            except (RawStoreError, OSError) as error:
                detail = f"Raw overlap comparison failed: {error}"
                for index in indices:
                    request_results[index] = self._record_overlap_local_failure(
                        request_results[index],
                        detail=detail,
                        identity=None,
                    )
                continue
            if not duplicates and not conflicts:
                continue
            for index in indices:
                item = request_results[index]
                request_results[index] = replace(
                    item,
                    duplicate_overlap_records=duplicates,
                    conflicts=conflicts,
                    status=(
                        BinancePublicHistoryRunStatus.CONFLICT
                        if conflicts
                        else item.status
                    ),
                )
        if cancelled:
            status = BinancePublicHistoryRunStatus.CANCELLED
            reason = "cancelled; completed Raw revisions were preserved"
        elif shared.exhaustion_reason is not None:
            status = BinancePublicHistoryRunStatus.BUDGET_EXHAUSTED
            reason = shared.exhaustion_reason
        elif any(
            item.status is BinancePublicHistoryRunStatus.CONFLICT
            for item in request_results
        ):
            status = BinancePublicHistoryRunStatus.CONFLICT
            reason = "source or cross-source conflicts remain unresolved"
        elif all(item.completed for item in request_results):
            status = BinancePublicHistoryRunStatus.COMPLETED
            reason = "all finite request obligations were satisfied"
        elif any(item.raw_references for item in request_results):
            status = BinancePublicHistoryRunStatus.PARTIAL
            reason = (
                "some obligations remain unmet; completed Raw revisions were preserved"
            )
        else:
            status = BinancePublicHistoryRunStatus.FAILED
            reason = "no request was fully satisfied"
        return BinancePublicHistoryRunResult(
            plan_id=plan.plan_id,
            requests=tuple(request_results),
            status=status,
            http_requests_used=shared.http_requests,
            archive_objects_used=shared.archive_objects,
            rest_pages_used=shared.rest_pages,
            downloaded_bytes=shared.downloaded_bytes,
            elapsed_seconds=shared.elapsed,
            termination_reason=reason,
        )
