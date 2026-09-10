"""Bounded Binance USDⓈ-M settled-funding REST acquisition.

The adapter consumes one frozen, per-window source observation.  It reuses the
shared REST executor for HTTP, retry, budget, failure evidence, and immutable
page publication without treating sparse funding events as a continuous grid.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final

import polars as pl

from tracequant.data.binance_kline_rest import (
    BinanceKlineRestAttemptResult,
    BinanceKlineRestBudget,
    BinanceKlineRestCoverage,
    BinanceKlineRestCoverageStatus,
    BinanceKlineRestHttpGet,
    BinanceKlineRestPageResult,
    BinanceKlineRestRunResult,
    BinanceKlineRestStatus,
    BinanceRestPageAcquisition,
    BinanceRestPageParsed,
    BinanceRestPageParseError,
)
from tracequant.data.public_history_rest import (
    BinanceRestEndpoint,
    BinanceRestPageRequest,
)
from tracequant.data.raw_store import RawStore
from tracequant.domain import InstrumentId, TimeRange

__all__ = [
    "BinanceFundingRateRestAcquisition",
    "BinanceFundingRateRestAttemptResult",
    "BinanceFundingRateRestBudget",
    "BinanceFundingRateRestCoverage",
    "BinanceFundingRateRestCoverageStatus",
    "BinanceFundingRateRestPageResult",
    "BinanceFundingRateRestRunResult",
    "BinanceFundingRateRestStatus",
]

BinanceFundingRateRestAttemptResult = BinanceKlineRestAttemptResult
BinanceFundingRateRestBudget = BinanceKlineRestBudget
BinanceFundingRateRestCoverageStatus = BinanceKlineRestCoverageStatus
BinanceFundingRateRestPageResult = BinanceKlineRestPageResult
BinanceFundingRateRestRunResult = BinanceKlineRestRunResult
BinanceFundingRateRestStatus = BinanceKlineRestStatus

_APPROVED_COVERAGE_VERSION: Final = "issue-295-probe-run-2026-09-09T18:33:30.868474Z"
_APPROVED_COVERAGE_REFERENCE: Final = (
    "docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json"
)
_APPROVED_COVERAGE_SHA256: Final = (
    "30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45"
)
_RAW_SCHEMA_IDENTIFIER: Final = "binance.um.settled-funding-rate.rest-json.v1"
_ALLOWED_INSTRUMENTS: Final = frozenset({"BTCUSDT", "ETHUSDT"})
_CORE_FIELDS: Final = frozenset({"symbol", "fundingTime", "fundingRate"})
_KNOWN_FIELDS: Final = _CORE_FIELDS | {"markPrice", "rateType"}
_CONFIRMED_RATE_TYPE: Final = "Regular"
_CRITICAL_UNKNOWN_TOKENS: Final = (
    "funding",
    "rate",
    "settle",
    "predict",
    "forecast",
)


@dataclass(frozen=True, slots=True)
class BinanceFundingRateRestCoverage(BinanceKlineRestCoverage):
    """Finite funding endpoint/instrument/window source evidence."""


@dataclass(frozen=True, slots=True)
class _FundingObservation:
    subject: str
    requested_range: TimeRange
    normalized_params: Mapping[str, str | int]
    observed_at: datetime
    response_sha256: str
    actual_range: TimeRange


def _utc_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000, tz=UTC)


_RECENT_START_MS: Final = 1_788_374_012_658
_RECENT_END_MS: Final = 1_788_978_812_658
_GAP_START_MS: Final = 1_788_393_600_000
_GAP_END_MS: Final = 1_788_397_200_000
_RECENT_ACTUAL_END_MS: Final = 1_788_969_600_003

_APPROVED_OBSERVATIONS: Final = (
    _FundingObservation(
        subject="BTCUSDT",
        requested_range=TimeRange(
            start=_utc_ms(_RECENT_START_MS), end=_utc_ms(_RECENT_END_MS)
        ),
        normalized_params={
            "symbol": "BTCUSDT",
            "startTime": _RECENT_START_MS,
            "endTime": _RECENT_END_MS - 1,
            "limit": 1000,
        },
        observed_at=datetime(2026, 9, 9, 18, 33, 33, 964227, tzinfo=UTC),
        response_sha256=(
            "2bd472efeb5b2d6336fda340914d77770151589cb3a14ff419e1ed149ef41f71"
        ),
        actual_range=TimeRange(
            start=_utc_ms(_GAP_START_MS), end=_utc_ms(_RECENT_ACTUAL_END_MS)
        ),
    ),
    _FundingObservation(
        subject="ETHUSDT",
        requested_range=TimeRange(
            start=_utc_ms(_RECENT_START_MS), end=_utc_ms(_RECENT_END_MS)
        ),
        normalized_params={
            "symbol": "ETHUSDT",
            "startTime": _RECENT_START_MS,
            "endTime": _RECENT_END_MS - 1,
            "limit": 1000,
        },
        observed_at=datetime(2026, 9, 9, 18, 33, 35, 109386, tzinfo=UTC),
        response_sha256=(
            "368852dd6686fce1fac1d940f7153dc5099acc8125c7ae8bac4d4b0ece24cace"
        ),
        actual_range=TimeRange(
            start=_utc_ms(_GAP_START_MS), end=_utc_ms(_RECENT_ACTUAL_END_MS)
        ),
    ),
    _FundingObservation(
        subject="BTCUSDT",
        requested_range=TimeRange(
            start=_utc_ms(_GAP_START_MS), end=_utc_ms(_GAP_END_MS)
        ),
        normalized_params={
            "symbol": "BTCUSDT",
            "startTime": _GAP_START_MS,
            "endTime": _GAP_END_MS - 1,
            "limit": 100,
        },
        observed_at=datetime(2026, 9, 9, 18, 33, 39, 998536, tzinfo=UTC),
        response_sha256=(
            "39ac0b55d6acae3de0e0be633edbfabdc37a227fa913027fe2e63c547abda401"
        ),
        actual_range=TimeRange(
            start=_utc_ms(_GAP_START_MS), end=_utc_ms(_GAP_START_MS + 1)
        ),
    ),
    _FundingObservation(
        subject="ETHUSDT",
        requested_range=TimeRange(
            start=_utc_ms(_GAP_START_MS), end=_utc_ms(_GAP_END_MS)
        ),
        normalized_params={
            "symbol": "ETHUSDT",
            "startTime": _GAP_START_MS,
            "endTime": _GAP_END_MS - 1,
            "limit": 100,
        },
        observed_at=datetime(2026, 9, 9, 18, 33, 40, 679377, tzinfo=UTC),
        response_sha256=(
            "b862855ea7296ca550dc3aac55c34e2e907cb993aa5749efaedd4409b4cf1ac1"
        ),
        actual_range=TimeRange(
            start=_utc_ms(_GAP_START_MS), end=_utc_ms(_GAP_START_MS + 1)
        ),
    ),
)


def _require_int64(value: object, *, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise BinanceRestPageParseError(
            f"{field} must be a non-negative signed 64-bit integer"
        )
    return value


def _require_numeric_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise BinanceRestPageParseError(f"{field} must be a finite numeric string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise BinanceRestPageParseError(
            f"{field} must be a finite numeric string"
        ) from error
    if not number.is_finite():
        raise BinanceRestPageParseError(f"{field} must be a finite numeric string")
    return value


def _strict_json_array(body: bytes) -> list[object]:
    try:
        payload = json.loads(
            body.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {value}")
            ),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise BinanceRestPageParseError(
            "response body is not strict UTF-8 JSON"
        ) from error
    if not isinstance(payload, list):
        raise BinanceRestPageParseError(
            "response JSON must be an array, not an error object"
        )
    return payload


def _is_critical_unknown(name: str) -> bool:
    lowered = name.casefold()
    return any(token in lowered for token in _CRITICAL_UNKNOWN_TOKENS)


class _FundingRateRestPageAdapter:
    def request_failure(
        self, request: BinanceRestPageRequest, now: datetime
    ) -> tuple[BinanceKlineRestStatus, str] | None:
        del now
        if request.endpoint is not BinanceRestEndpoint.FUNDING_RATE_HISTORY:
            return (
                BinanceKlineRestStatus.UNSUPPORTED,
                "only settled funding-rate history is supported",
            )
        if not isinstance(request.subject, InstrumentId):
            return (
                BinanceKlineRestStatus.UNSUPPORTED,
                "settled funding history requires a typed instrument/symbol",
            )
        if str(request.subject) not in _ALLOWED_INSTRUMENTS:
            return (
                BinanceKlineRestStatus.UNSUPPORTED,
                "only BTCUSDT and ETHUSDT are supported",
            )
        if request.interval is not None:
            return (
                BinanceKlineRestStatus.INVALID_REQUEST,
                "settled funding history does not use a Kline interval",
            )
        if request.page_boundary != request.caller_bounds:
            return (
                BinanceKlineRestStatus.INVALID_REQUEST,
                "public run must start at the complete normalized caller range",
            )
        return None

    def coverage_failure(
        self, request: BinanceRestPageRequest, coverage: object | None
    ) -> tuple[BinanceKlineRestStatus, str] | None:
        if coverage is None:
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "REST coverage evidence is missing",
            )
        if not isinstance(coverage, BinanceFundingRateRestCoverage):
            raise TypeError("coverage must be a BinanceFundingRateRestCoverage or None")
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
                "coverage endpoint or typed instrument does not match the request",
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

        matched: _FundingObservation | None = None
        for observation in _APPROVED_OBSERVATIONS:
            if observation.subject != str(coverage.subject):
                continue
            if (
                dict(coverage.normalized_params) == dict(observation.normalized_params)
                and coverage.observed_at == observation.observed_at
                and coverage.response_sha256 == observation.response_sha256
                and coverage.actual_range == observation.actual_range
            ):
                matched = observation
                break
        if matched is None:
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "per-window funding observation is missing or unrecognized",
            )
        if (
            coverage.allowed_range.start < matched.requested_range.start
            or coverage.allowed_range.end > matched.requested_range.end
        ):
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "coverage range extends beyond its bound funding observation",
            )
        if (
            request.caller_range.start < coverage.allowed_range.start
            or request.caller_range.end > coverage.allowed_range.end
        ):
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "request range is outside the supplied coverage range",
            )
        return None

    def parse_page(
        self,
        request: BinanceRestPageRequest,
        body: bytes,
        prior_records: Mapping[int, tuple[object, ...]],
    ) -> BinanceRestPageParsed:
        payload = _strict_json_array(body)
        assert request.page_boundary is not None
        if not payload:
            empty_frame = pl.DataFrame(
                schema={
                    "symbol": pl.String,
                    "fundingTime": pl.Int64,
                    "fundingRate": pl.String,
                    "markPrice": pl.String,
                    "markPricePresent": pl.Boolean,
                    "rateType": pl.String,
                    "rateTypePresent": pl.Boolean,
                    "unknownFields": pl.List(pl.String),
                }
            )
            return BinanceRestPageParsed(
                frame=empty_frame,
                actual_record_range=None,
                records={},
                next_cursor_ms=request.page_boundary.end_time_ms + 1,
                terminal=True,
            )
        if len(payload) > request.limit:
            raise BinanceRestPageParseError(
                "response contains more records than the page limit"
            )

        rows: list[tuple[object, ...]] = []
        records: dict[int, tuple[object, ...]] = {}
        previous_time: int | None = None
        for row_number, raw_record in enumerate(payload):
            if not isinstance(raw_record, dict):
                raise BinanceRestPageParseError(
                    f"record {row_number} must be a JSON object"
                )
            missing = sorted(_CORE_FIELDS - raw_record.keys())
            if missing:
                raise BinanceRestPageParseError(
                    f"record {row_number} is missing required fields: {', '.join(missing)}"
                )
            symbol = raw_record["symbol"]
            if not isinstance(symbol, str) or symbol != str(request.subject):
                raise BinanceRestPageParseError(
                    f"record {row_number} symbol does not match the requested instrument"
                )
            funding_time = _require_int64(
                raw_record["fundingTime"], field=f"record {row_number} fundingTime"
            )
            funding_rate = _require_numeric_string(
                raw_record["fundingRate"], field=f"record {row_number} fundingRate"
            )

            mark_price_present = "markPrice" in raw_record
            raw_mark_price = raw_record.get("markPrice")
            mark_price = (
                None
                if raw_mark_price is None
                else _require_numeric_string(
                    raw_mark_price, field=f"record {row_number} markPrice"
                )
            )
            rate_type_present = "rateType" in raw_record
            raw_rate_type = raw_record.get("rateType")
            if raw_rate_type is None:
                rate_type = None
            elif not isinstance(raw_rate_type, str):
                raise BinanceRestPageParseError(
                    f"record {row_number} rateType must be a string or null"
                )
            elif raw_rate_type != _CONFIRMED_RATE_TYPE:
                raise BinanceRestPageParseError(
                    f"record {row_number} rateType has unconfirmed settled semantics"
                )
            else:
                rate_type = raw_rate_type

            unknown_fields = sorted(set(raw_record) - _KNOWN_FIELDS)
            critical_unknowns = [
                name for name in unknown_fields if _is_critical_unknown(name)
            ]
            if critical_unknowns:
                raise BinanceRestPageParseError(
                    "record "
                    f"{row_number} contains unconfirmed funding-semantic fields: "
                    f"{', '.join(critical_unknowns)}"
                )

            frozen_record = (
                json.dumps(
                    raw_record,
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            prior = prior_records.get(funding_time)
            if prior is not None:
                detail = (
                    "conflicting duplicate" if prior != frozen_record else "duplicate"
                )
                raise BinanceRestPageParseError(
                    f"record {row_number} has a cross-page {detail} fundingTime"
                )
            duplicate = records.get(funding_time)
            if duplicate is not None:
                detail = (
                    "conflicting duplicate"
                    if duplicate != frozen_record
                    else "duplicate"
                )
                raise BinanceRestPageParseError(
                    f"record {row_number} has a {detail} fundingTime"
                )
            if previous_time is not None and funding_time < previous_time:
                raise BinanceRestPageParseError(
                    f"record {row_number} fundingTime is out of order"
                )
            if not (
                request.page_boundary.start_time_ms
                <= funding_time
                <= request.page_boundary.end_time_ms
            ):
                raise BinanceRestPageParseError(
                    f"record {row_number} fundingTime is outside the page boundary"
                )
            previous_time = funding_time
            records[funding_time] = frozen_record
            rows.append(
                (
                    symbol,
                    funding_time,
                    funding_rate,
                    mark_price,
                    mark_price_present,
                    rate_type,
                    rate_type_present,
                    unknown_fields,
                )
            )

        try:
            frame = pl.DataFrame(
                rows,
                schema={
                    "symbol": pl.String,
                    "fundingTime": pl.Int64,
                    "fundingRate": pl.String,
                    "markPrice": pl.String,
                    "markPricePresent": pl.Boolean,
                    "rateType": pl.String,
                    "rateTypePresent": pl.Boolean,
                    "unknownFields": pl.List(pl.String),
                },
                orient="row",
            )
        except Exception as error:
            raise BinanceRestPageParseError(
                "response fields cannot be represented by the funding Raw schema"
            ) from error
        first_time = int(frame["fundingTime"][0])
        last_time = int(frame["fundingTime"][-1])
        return BinanceRestPageParsed(
            frame=frame,
            actual_record_range=TimeRange(
                start=_utc_ms(first_time), end=_utc_ms(last_time + 1)
            ),
            records=records,
            next_cursor_ms=last_time + 1,
            terminal=frame.height < request.limit,
        )

    def raw_schema_identifier(self, request: BinanceRestPageRequest) -> str:
        del request
        return _RAW_SCHEMA_IDENTIFIER


class BinanceFundingRateRestAcquisition(BinanceRestPageAcquisition):
    """Acquire settled funding pages through the shared bounded REST executor."""

    def __init__(
        self,
        store: RawStore,
        *,
        http_get: BinanceKlineRestHttpGet | None = None,
        clock: Callable[[], datetime] | None = None,
        wait: Callable[[float], None] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(
            store,
            adapter=_FundingRateRestPageAdapter(),
            http_get=http_get,
            clock=clock,
            wait=wait,
            monotonic_clock=monotonic_clock,
        )
