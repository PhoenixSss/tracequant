"""Typed page contracts for Binance USDⓈ-M public REST history.

The contracts in this module describe one bounded response page.  They do not
perform HTTP, pagination, retry, rate-limit handling, parsing, or persistence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self

from tracequant.core.time import format_utc, parse_utc, to_utc
from tracequant.data.public_history import (
    BinanceKlineInterval,
    BinanceMarket,
    BinancePriceIndexId,
    BinancePublicHistoryDataType,
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceIdentity,
    BinancePublicHistorySourceKind,
    BinancePublicHistorySubject,
    BinancePublicHistorySubjectKind,
    PublicHistoryContractError,
)
from tracequant.domain import InstrumentId, TimeRange

__all__ = [
    "BinanceRestEndpoint",
    "BinanceRestPageIdentity",
    "BinanceRestPageProvenance",
    "BinanceRestPageRequest",
    "BinanceRestRequestBounds",
    "next_binance_funding_cursor",
    "next_binance_kline_cursor",
]

_BINANCE_VENUE: Final = "binance"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_KLINE_INTERVAL_MILLISECONDS: Final = {
    BinanceKlineInterval.ONE_MINUTE: 60_000,
}
_ALLOWED_RESPONSE_HEADERS: Final = frozenset(
    {
        "content-type",
        "date",
        "retry-after",
        "x-mbx-used-weight",
        "x-mbx-used-weight-1m",
    }
)
_MAX_HEADER_VALUE_LENGTH: Final = 1024


class BinanceRestEndpoint(StrEnum):
    """Controlled Binance USDⓈ-M REST endpoint identities."""

    CONTRACT_KLINES = "/fapi/v1/klines"
    MARK_PRICE_KLINES = "/fapi/v1/markPriceKlines"
    INDEX_PRICE_KLINES = "/fapi/v1/indexPriceKlines"
    FUNDING_RATE_HISTORY = "/fapi/v1/fundingRate"

    @property
    def data_type(self) -> BinancePublicHistoryDataType:
        """Return the public-history dataset served by this endpoint."""
        return _ENDPOINT_DATA_TYPES[self]

    @property
    def maximum_limit(self) -> int:
        """Return the endpoint's admitted page-size ceiling."""
        if self is BinanceRestEndpoint.FUNDING_RATE_HISTORY:
            return 1_000
        return 1_500


_ENDPOINT_DATA_TYPES: Final = {
    BinanceRestEndpoint.CONTRACT_KLINES: BinancePublicHistoryDataType.CONTRACT_KLINE,
    BinanceRestEndpoint.MARK_PRICE_KLINES: (
        BinancePublicHistoryDataType.MARK_PRICE_KLINE
    ),
    BinanceRestEndpoint.INDEX_PRICE_KLINES: (
        BinancePublicHistoryDataType.INDEX_PRICE_KLINE
    ),
    BinanceRestEndpoint.FUNDING_RATE_HISTORY: (
        BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
    ),
}


def _coerce_endpoint(value: BinanceRestEndpoint | str) -> BinanceRestEndpoint:
    if isinstance(value, BinanceRestEndpoint):
        return value
    if not isinstance(value, str):
        raise TypeError("endpoint must be a supported BinanceRestEndpoint")
    try:
        return BinanceRestEndpoint(value)
    except ValueError as error:
        raise PublicHistoryContractError(
            f"unsupported Binance REST endpoint {value!r}"
        ) from error


def _coerce_market(value: BinanceMarket | str) -> BinanceMarket:
    if isinstance(value, BinanceMarket):
        return value
    if not isinstance(value, str):
        raise TypeError("market must be a supported BinanceMarket")
    try:
        return BinanceMarket(value)
    except ValueError as error:
        raise PublicHistoryContractError(
            f"unsupported Binance REST market {value!r}"
        ) from error


def _require_exact_fields(
    value: object, *, expected: frozenset[str], model: str
) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{model} serialized value must be a JSON object")
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"extra fields: {', '.join(sorted(extra))}")
        raise PublicHistoryContractError(
            f"invalid {model} fields ({'; '.join(details)})"
        )
    return value


def _require_int(value: object, *, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    return value


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _datetime_to_milliseconds(value: datetime, *, field: str) -> int:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    try:
        normalized = to_utc(value)
    except ValueError as error:
        raise PublicHistoryContractError(f"{field} must be timezone-aware") from error
    if normalized.microsecond % 1_000:
        raise PublicHistoryContractError(
            f"{field} must have exact millisecond precision"
        )
    delta = normalized - _EPOCH
    milliseconds = (
        delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000
    )
    if milliseconds < 0:
        raise PublicHistoryContractError(f"{field} must not precede Unix epoch")
    return milliseconds


def _milliseconds_to_datetime(value: int) -> datetime:
    if type(value) is not int or value < 0:
        raise PublicHistoryContractError(
            "request boundary milliseconds must be non-negative integers"
        )
    return _EPOCH + timedelta(milliseconds=value)


@dataclass(frozen=True, slots=True)
class BinanceRestRequestBounds:
    """One Binance inclusive ``startTime``/``endTime`` request boundary."""

    start_time_ms: int
    end_time_ms: int

    def __post_init__(self) -> None:
        for field in ("start_time_ms", "end_time_ms"):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise PublicHistoryContractError(
                    f"{field} must be a non-negative integer"
                )
        if self.start_time_ms > self.end_time_ms:
            raise PublicHistoryContractError(
                "REST request start_time_ms must not exceed end_time_ms"
            )

    @classmethod
    def from_caller_range(cls, caller_range: TimeRange) -> Self:
        """Convert UTC ``[start, end)`` once to Binance inclusive bounds."""
        if not isinstance(caller_range, TimeRange):
            raise TypeError("caller_range must be a TimeRange")
        start_time_ms = _datetime_to_milliseconds(
            caller_range.start, field="caller range start"
        )
        end_exclusive_ms = _datetime_to_milliseconds(
            caller_range.end, field="caller range end"
        )
        return cls(
            start_time_ms=start_time_ms,
            end_time_ms=end_exclusive_ms - 1,
        )

    def to_dict(self) -> dict[str, int]:
        return {"startTime": self.start_time_ms, "endTime": self.end_time_ms}

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset({"startTime", "endTime"}),
            model="BinanceRestRequestBounds",
        )
        return cls(
            start_time_ms=_require_int(fields["startTime"], field="startTime"),
            end_time_ms=_require_int(fields["endTime"], field="endTime"),
        )


def next_binance_kline_cursor(
    last_open_time_ms: int, interval: BinanceKlineInterval
) -> int:
    """Return the first open time after the last Kline in a response page."""
    if type(last_open_time_ms) is not int or last_open_time_ms < 0:
        raise PublicHistoryContractError(
            "last_open_time_ms must be a non-negative integer"
        )
    if not isinstance(interval, BinanceKlineInterval):
        try:
            interval = BinanceKlineInterval(interval)
        except (TypeError, ValueError) as error:
            raise PublicHistoryContractError("unsupported Kline interval") from error
    return last_open_time_ms + _KLINE_INTERVAL_MILLISECONDS[interval]


def next_binance_funding_cursor(last_funding_time_ms: int) -> int:
    """Return a cursor strictly beyond the last funding timestamp."""
    if type(last_funding_time_ms) is not int or last_funding_time_ms < 0:
        raise PublicHistoryContractError(
            "last_funding_time_ms must be a non-negative integer"
        )
    return last_funding_time_ms + 1


def _subject_kind(
    subject: BinancePublicHistorySubject,
) -> BinancePublicHistorySubjectKind:
    if isinstance(subject, InstrumentId):
        return BinancePublicHistorySubjectKind.INSTRUMENT
    if isinstance(subject, BinancePriceIndexId):
        return BinancePublicHistorySubjectKind.PRICE_INDEX_PAIR
    raise TypeError("subject must be an InstrumentId or BinancePriceIndexId")


def _subject_param(
    subject: BinancePublicHistorySubject,
) -> tuple[str, str]:
    if isinstance(subject, InstrumentId):
        return "symbol", str(subject)
    if isinstance(subject, BinancePriceIndexId):
        return "pair", str(subject)
    raise TypeError("subject must be an InstrumentId or BinancePriceIndexId")


@dataclass(frozen=True, slots=True)
class BinanceRestPageIdentity:
    """Stable logical identity for exactly one REST response page request."""

    endpoint: BinanceRestEndpoint
    subject: BinancePublicHistorySubject
    page_boundary: BinanceRestRequestBounds
    limit: int
    interval: BinanceKlineInterval | None
    market: BinanceMarket = BinanceMarket.USD_M

    def __post_init__(self) -> None:
        endpoint = _coerce_endpoint(self.endpoint)
        market = _coerce_market(self.market)
        if not isinstance(self.page_boundary, BinanceRestRequestBounds):
            raise TypeError("page_boundary must be a BinanceRestRequestBounds")
        if type(self.limit) is not int or not 1 <= self.limit <= endpoint.maximum_limit:
            raise PublicHistoryContractError(
                f"limit must be between 1 and {endpoint.maximum_limit} for {endpoint.value}"
            )
        validation_range = TimeRange(
            start=_milliseconds_to_datetime(self.page_boundary.start_time_ms),
            end=_milliseconds_to_datetime(self.page_boundary.end_time_ms + 1),
        )
        validated = BinancePublicHistoryRequest(
            subject=self.subject,
            data_type=endpoint.data_type,
            request_range=validation_range,
            source_kind=BinancePublicHistorySourceKind.REST,
            interval=self.interval,
            market=market,
        )
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "subject", validated.subject)
        object.__setattr__(self, "interval", validated.interval)
        object.__setattr__(self, "market", market)

    @property
    def data_type(self) -> BinancePublicHistoryDataType:
        return self.endpoint.data_type

    @property
    def subject_kind(self) -> BinancePublicHistorySubjectKind:
        return _subject_kind(self.subject)

    @property
    def normalized_params(self) -> Mapping[str, str | int]:
        """Return immutable, order-independent, JSON-compatible request facts."""
        subject_name, subject_value = _subject_param(self.subject)
        params: dict[str, str | int] = {
            subject_name: subject_value,
            **self.page_boundary.to_dict(),
            "limit": self.limit,
        }
        if self.interval is not None:
            params["interval"] = self.interval.value
        return MappingProxyType(dict(sorted(params.items())))

    def to_dict(self) -> dict[str, object]:
        return {
            "venue": _BINANCE_VENUE,
            "market": self.market.value,
            "endpoint": self.endpoint.value,
            "data_type": self.data_type.value,
            "subject_kind": self.subject_kind.value,
            "normalized_params": dict(self.normalized_params),
            "page_boundary": self.page_boundary.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def logical_id(self) -> str:
        """Return the deterministic page logical-object identifier."""
        return hashlib.sha256(self.to_json().encode("ascii")).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "venue",
                    "market",
                    "endpoint",
                    "data_type",
                    "subject_kind",
                    "normalized_params",
                    "page_boundary",
                }
            ),
            model="BinanceRestPageIdentity",
        )
        if _require_string(fields["venue"], field="venue") != _BINANCE_VENUE:
            raise PublicHistoryContractError(f"venue must be {_BINANCE_VENUE!r}")
        endpoint = _coerce_endpoint(
            _require_string(fields["endpoint"], field="endpoint")
        )
        if fields["data_type"] != endpoint.data_type.value:
            raise PublicHistoryContractError(
                "REST endpoint and data_type identity do not match"
            )
        params = fields["normalized_params"]
        if not isinstance(params, dict) or any(
            not isinstance(key, str) for key in params
        ):
            raise TypeError("normalized_params must be a JSON object")
        subject_kind = _require_string(fields["subject_kind"], field="subject_kind")
        if subject_kind == BinancePublicHistorySubjectKind.INSTRUMENT.value:
            subject_key = "symbol"
            subject: BinancePublicHistorySubject = InstrumentId.from_dict(
                params.get(subject_key)
            )
        elif subject_kind == BinancePublicHistorySubjectKind.PRICE_INDEX_PAIR.value:
            subject_key = "pair"
            subject = BinancePriceIndexId.from_dict(params.get(subject_key))
        else:
            raise PublicHistoryContractError(
                f"unsupported REST subject kind {subject_kind!r}"
            )
        boundary = BinanceRestRequestBounds.from_dict(fields["page_boundary"])
        interval_value = params.get("interval")
        interval = (
            None
            if interval_value is None
            else BinanceKlineInterval(_require_string(interval_value, field="interval"))
        )
        identity = cls(
            endpoint=endpoint,
            subject=subject,
            page_boundary=boundary,
            limit=_require_int(params.get("limit"), field="limit"),
            interval=interval,
            market=_coerce_market(_require_string(fields["market"], field="market")),
        )
        if dict(identity.normalized_params) != params:
            raise PublicHistoryContractError(
                "normalized_params contain unsupported or inconsistent request facts"
            )
        return identity


@dataclass(frozen=True, slots=True)
class BinanceRestPageRequest:
    """Caller range and exact page boundary for one future REST call."""

    endpoint: BinanceRestEndpoint
    subject: BinancePublicHistorySubject
    caller_range: TimeRange
    limit: int
    interval: BinanceKlineInterval | None
    page_boundary: BinanceRestRequestBounds | None = None
    market: BinanceMarket = BinanceMarket.USD_M

    def __post_init__(self) -> None:
        if not isinstance(self.caller_range, TimeRange):
            raise TypeError("caller_range must be a TimeRange")
        caller_bounds = BinanceRestRequestBounds.from_caller_range(self.caller_range)
        boundary = self.page_boundary or caller_bounds
        if not isinstance(boundary, BinanceRestRequestBounds):
            raise TypeError("page_boundary must be a BinanceRestRequestBounds or None")
        if (
            boundary.start_time_ms < caller_bounds.start_time_ms
            or boundary.end_time_ms > caller_bounds.end_time_ms
        ):
            raise PublicHistoryContractError(
                "page boundary must remain inside normalized caller bounds"
            )
        identity = BinanceRestPageIdentity(
            endpoint=self.endpoint,
            subject=self.subject,
            page_boundary=boundary,
            limit=self.limit,
            interval=self.interval,
            market=self.market,
        )
        object.__setattr__(self, "endpoint", identity.endpoint)
        object.__setattr__(self, "subject", identity.subject)
        object.__setattr__(self, "interval", identity.interval)
        object.__setattr__(self, "page_boundary", boundary)
        object.__setattr__(self, "market", identity.market)

    @property
    def caller_bounds(self) -> BinanceRestRequestBounds:
        return BinanceRestRequestBounds.from_caller_range(self.caller_range)

    @property
    def identity(self) -> BinanceRestPageIdentity:
        assert self.page_boundary is not None
        return BinanceRestPageIdentity(
            endpoint=self.endpoint,
            subject=self.subject,
            page_boundary=self.page_boundary,
            limit=self.limit,
            interval=self.interval,
            market=self.market,
        )

    @property
    def source_identity(self) -> BinancePublicHistorySourceIdentity:
        return BinancePublicHistorySourceIdentity(
            subject=self.subject,
            data_type=self.endpoint.data_type,
            source_kind=BinancePublicHistorySourceKind.REST,
            interval=self.interval,
            market=self.market,
        )

    @property
    def normalized_params(self) -> Mapping[str, str | int]:
        return self.identity.normalized_params

    def with_cursor(self, cursor_ms: int) -> Self:
        """Return the next page request while preserving its caller boundary."""
        if type(cursor_ms) is not int or cursor_ms < 0:
            raise PublicHistoryContractError("cursor_ms must be a non-negative integer")
        assert self.page_boundary is not None
        return replace(
            self,
            page_boundary=BinanceRestRequestBounds(
                start_time_ms=cursor_ms,
                end_time_ms=self.page_boundary.end_time_ms,
            ),
        )

    def next_page(self, last_result_time_ms: int) -> Self:
        """Advance according to the endpoint's typed cursor progression rule."""
        if self.endpoint is BinanceRestEndpoint.FUNDING_RATE_HISTORY:
            cursor = next_binance_funding_cursor(last_result_time_ms)
        else:
            assert self.interval is not None
            cursor = next_binance_kline_cursor(last_result_time_ms, self.interval)
        return self.with_cursor(cursor)

    def to_dict(self) -> dict[str, object]:
        return {
            "caller_range": self.caller_range.to_dict(),
            "page_identity": self.identity.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset({"caller_range", "page_identity"}),
            model="BinanceRestPageRequest",
        )
        caller_range = TimeRange.from_dict(fields["caller_range"])
        identity = BinanceRestPageIdentity.from_dict(fields["page_identity"])
        request = cls(
            endpoint=identity.endpoint,
            subject=identity.subject,
            caller_range=caller_range,
            limit=identity.limit,
            interval=identity.interval,
            page_boundary=identity.page_boundary,
            market=identity.market,
        )
        if request.identity != identity:
            raise PublicHistoryContractError(
                "serialized REST page request identity is inconsistent"
            )
        return request


def _normalize_response_headers(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("response_headers must be a mapping")
    normalized: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise TypeError("response header names and values must be strings")
        name = raw_name.strip().lower()
        if name not in _ALLOWED_RESPONSE_HEADERS:
            raise PublicHistoryContractError(
                f"response header {raw_name!r} is not allow-listed"
            )
        if name in normalized:
            raise PublicHistoryContractError(
                f"duplicate normalized response header {name!r}"
            )
        header_value = raw_value.strip()
        if len(header_value) > _MAX_HEADER_VALUE_LENGTH:
            raise PublicHistoryContractError(
                f"response header {name!r} exceeds bounded value length"
            )
        normalized[name] = header_value
    return MappingProxyType(dict(sorted(normalized.items())))


def _require_sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PublicHistoryContractError(
            "response_sha256 must be a lowercase SHA-256 digest"
        )
    return value


@dataclass(frozen=True, slots=True)
class BinanceRestPageProvenance:
    """Immutable response evidence for one completed HTTP page observation.

    A transport failure has no HTTP response and therefore cannot be
    constructed as this observation.  A 200 response with ``record_count=0``
    is a successful empty page and remains distinct from an HTTP error.
    """

    request: BinanceRestPageRequest
    response_sha256: str
    observed_at: datetime
    http_status: int
    response_headers: Mapping[str, str]
    server_time: datetime | None = None
    record_count: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, BinanceRestPageRequest):
            raise TypeError("request must be a BinanceRestPageRequest")
        object.__setattr__(
            self, "response_sha256", _require_sha256(self.response_sha256)
        )
        if not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime")
        try:
            observed_at = to_utc(self.observed_at)
        except ValueError as error:
            raise PublicHistoryContractError(
                "observed_at must be timezone-aware"
            ) from error
        object.__setattr__(self, "observed_at", observed_at)
        if self.server_time is not None:
            if not isinstance(self.server_time, datetime):
                raise TypeError("server_time must be a datetime or None")
            try:
                server_time = to_utc(self.server_time)
            except ValueError as error:
                raise PublicHistoryContractError(
                    "server_time must be timezone-aware when supplied"
                ) from error
            object.__setattr__(self, "server_time", server_time)
        if type(self.http_status) is not int or not 100 <= self.http_status <= 599:
            raise PublicHistoryContractError("http_status must be an HTTP status code")
        object.__setattr__(
            self, "response_headers", _normalize_response_headers(self.response_headers)
        )
        if self.record_count is not None and (
            type(self.record_count) is not int or self.record_count < 0
        ):
            raise PublicHistoryContractError(
                "record_count must be a non-negative integer or None"
            )

    @classmethod
    def from_response(
        cls,
        *,
        request: BinanceRestPageRequest,
        response_body: bytes,
        observed_at: datetime,
        http_status: int,
        response_headers: Mapping[str, str],
        server_time: datetime | None = None,
        record_count: int | None = None,
    ) -> Self:
        """Bind exact response bytes to their page request and observation."""
        if not isinstance(response_body, bytes):
            raise TypeError("response_body must be bytes")
        return cls(
            request=request,
            response_sha256=hashlib.sha256(response_body).hexdigest(),
            observed_at=observed_at,
            http_status=http_status,
            response_headers=response_headers,
            server_time=server_time,
            record_count=record_count,
        )

    @property
    def page_identity(self) -> BinanceRestPageIdentity:
        return self.request.identity

    @property
    def is_successful_response(self) -> bool:
        return 200 <= self.http_status < 300

    @property
    def is_successful_empty(self) -> bool:
        return self.is_successful_response and self.record_count == 0

    @property
    def revision_checksum(self) -> str:
        """Return the Raw revision-compatible verified response checksum."""
        return f"sha256:{self.response_sha256}"

    def to_dict(self) -> dict[str, object]:
        return {
            "request": self.request.to_dict(),
            "response_sha256": self.response_sha256,
            "observed_at": format_utc(self.observed_at),
            "server_time": (
                format_utc(self.server_time) if self.server_time is not None else None
            ),
            "http_status": self.http_status,
            "response_headers": dict(self.response_headers),
            "record_count": self.record_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "request",
                    "response_sha256",
                    "observed_at",
                    "server_time",
                    "http_status",
                    "response_headers",
                    "record_count",
                }
            ),
            model="BinanceRestPageProvenance",
        )
        server_time_value = fields["server_time"]
        record_count_value = fields["record_count"]
        return cls(
            request=BinanceRestPageRequest.from_dict(fields["request"]),
            response_sha256=_require_sha256(fields["response_sha256"]),
            observed_at=parse_utc(
                _require_string(fields["observed_at"], field="observed_at")
            ),
            server_time=(
                None
                if server_time_value is None
                else parse_utc(_require_string(server_time_value, field="server_time"))
            ),
            http_status=_require_int(fields["http_status"], field="http_status"),
            response_headers=_normalize_response_headers(fields["response_headers"]),
            record_count=(
                None
                if record_count_value is None
                else _require_int(record_count_value, field="record_count")
            ),
        )
