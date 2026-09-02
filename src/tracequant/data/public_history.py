"""Immutable contracts for Binance USDⓈ-M public historical data.

This module describes source intent only.  It does not resolve archive URLs,
call REST endpoints, read files, or parse exchange data.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final, Self

from tracequant.domain import DomainValidationError, InstrumentId, TimeRange

__all__ = [
    "BinanceArchiveObjectBoundary",
    "BinanceArchiveObjectGranularity",
    "BinanceKlineInterval",
    "BinanceMarket",
    "BinancePublicHistoryDataType",
    "BinancePublicHistoryRequest",
    "BinancePublicHistorySourceIdentity",
    "BinancePublicHistorySourceKind",
    "PublicHistoryContractError",
]

_BINANCE_VENUE: Final = "binance"
_SUPPORTED_INSTRUMENT_VALUES: Final = frozenset(
    {"BTCUSDT", "ETHUSDT", "BTCUSDC", "ETHUSDC"}
)


class PublicHistoryContractError(DomainValidationError):
    """Raised when a public-history request violates its typed contract."""


class BinanceMarket(StrEnum):
    """The Binance market covered by this first contract."""

    USD_M = "um"


class BinancePublicHistoryDataType(StrEnum):
    """Distinct raw data families supported by the first public-history slice."""

    CONTRACT_KLINE = "contract_kline"
    MARK_PRICE_KLINE = "mark_price_kline"
    INDEX_PRICE_KLINE = "index_price_kline"
    SETTLED_FUNDING_RATE = "settled_funding_rate"


class BinancePublicHistorySourceKind(StrEnum):
    """The source boundary used to obtain a public-history object."""

    ARCHIVE_DAILY = "archive_daily"
    ARCHIVE_MONTHLY = "archive_monthly"
    REST = "rest"


class BinanceKlineInterval(StrEnum):
    """Kline intervals admitted by the first implementation contract."""

    ONE_MINUTE = "1m"


class BinanceArchiveObjectGranularity(StrEnum):
    """Calendar granularity of an upstream archive object."""

    DAY = "day"
    MONTH = "month"


def _require_exact_fields(
    value: object, *, expected: frozenset[str], model: str
) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{model} serialized value must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{model} serialized field names must be strings")

    actual = set(value)
    missing = expected - actual
    extra = actual - expected
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


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _coerce_enum[EnumT: StrEnum](
    value: object, enum_type: type[EnumT], *, field: str
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a supported string value")
    try:
        return enum_type(value)
    except ValueError as error:
        raise PublicHistoryContractError(
            f"{field} has unsupported value {value!r}"
        ) from error


@dataclass(frozen=True, slots=True)
class BinanceArchiveObjectBoundary:
    """UTC calendar boundary for one daily or monthly archive object.

    ``period_start`` is a calendar date, not a caller request timestamp.  A
    daily object names one UTC calendar day; a monthly object must start on the
    first day of its UTC calendar month.
    """

    granularity: BinanceArchiveObjectGranularity
    period_start: date

    def __post_init__(self) -> None:
        granularity = _coerce_enum(
            self.granularity,
            BinanceArchiveObjectGranularity,
            field="archive object granularity",
        )
        if type(self.period_start) is not date:
            raise TypeError("archive object period_start must be a date")
        if (
            granularity is BinanceArchiveObjectGranularity.MONTH
            and self.period_start.day != 1
        ):
            raise PublicHistoryContractError(
                "monthly archive object period_start must be the first day of a month"
            )
        object.__setattr__(self, "granularity", granularity)

    @classmethod
    def day(cls, period_start: date) -> Self:
        """Create a boundary for one UTC daily archive object."""
        return cls(BinanceArchiveObjectGranularity.DAY, period_start)

    @classmethod
    def month(cls, year: int, month: int) -> Self:
        """Create a boundary for one UTC monthly archive object."""
        return cls(
            BinanceArchiveObjectGranularity.MONTH,
            date(year=year, month=month, day=1),
        )

    @classmethod
    def daily(cls, period_start: date) -> Self:
        """Alias for :meth:`day` using the source terminology."""
        return cls.day(period_start)

    @classmethod
    def monthly(cls, year: int, month: int) -> Self:
        """Alias for :meth:`month` using the source terminology."""
        return cls.month(year, month)

    @property
    def kind(self) -> BinanceArchiveObjectGranularity:
        """Return the calendar granularity without exposing a second field."""
        return self.granularity

    def to_dict(self) -> dict[str, str]:
        """Return a stable JSON-compatible object boundary."""
        return {
            "granularity": self.granularity.value,
            "period_start": self.period_start.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Create a boundary from exact serialized fields."""
        fields = _require_exact_fields(
            value,
            expected=frozenset({"granularity", "period_start"}),
            model="BinanceArchiveObjectBoundary",
        )
        period_start_text = _require_string(
            fields["period_start"], field="period_start"
        )
        try:
            period_start = date.fromisoformat(period_start_text)
        except ValueError as error:
            raise PublicHistoryContractError(
                "period_start must be an ISO 8601 calendar date"
            ) from error
        return cls(
            granularity=_coerce_enum(
                fields["granularity"],
                BinanceArchiveObjectGranularity,
                field="archive object granularity",
            ),
            period_start=period_start,
        )


def _normalize_components(
    *,
    market: BinanceMarket | str,
    instrument: InstrumentId,
    data_type: BinancePublicHistoryDataType | str,
    interval: BinanceKlineInterval | str | None,
    source_kind: BinancePublicHistorySourceKind | str,
    archive_object_boundary: BinanceArchiveObjectBoundary | None,
) -> tuple[
    BinanceMarket,
    BinancePublicHistoryDataType,
    BinanceKlineInterval | None,
    BinancePublicHistorySourceKind,
    BinanceArchiveObjectBoundary | None,
]:
    normalized_market = _coerce_enum(market, BinanceMarket, field="market")
    if not isinstance(instrument, InstrumentId):
        raise TypeError("instrument must be an InstrumentId")
    if str(instrument) not in _SUPPORTED_INSTRUMENT_VALUES:
        raise PublicHistoryContractError(
            f"instrument {instrument!s} is outside the supported Binance USDⓈ-M set"
        )

    normalized_data_type = _coerce_enum(
        data_type, BinancePublicHistoryDataType, field="data type"
    )
    normalized_interval: BinanceKlineInterval | None
    if interval is None:
        normalized_interval = None
    else:
        try:
            normalized_interval = _coerce_enum(
                interval, BinanceKlineInterval, field="interval"
            )
        except PublicHistoryContractError as error:
            raise PublicHistoryContractError(
                "supported Kline interval must be 1m"
            ) from error
    normalized_source_kind = _coerce_enum(
        source_kind, BinancePublicHistorySourceKind, field="source kind"
    )

    if archive_object_boundary is not None and not isinstance(
        archive_object_boundary, BinanceArchiveObjectBoundary
    ):
        raise TypeError(
            "archive_object_boundary must be a BinanceArchiveObjectBoundary"
        )

    is_funding = (
        normalized_data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
    )
    if is_funding:
        if normalized_interval is not None:
            raise PublicHistoryContractError(
                "settled funding rate does not use a Kline interval"
            )
    elif normalized_interval is not BinanceKlineInterval.ONE_MINUTE:
        raise PublicHistoryContractError("supported Kline interval must be 1m")

    if normalized_source_kind is BinancePublicHistorySourceKind.ARCHIVE_DAILY:
        if is_funding:
            raise PublicHistoryContractError(
                "settled funding rate has no daily archive source"
            )
        if archive_object_boundary is None:
            raise PublicHistoryContractError(
                "daily archive source requires a day object boundary"
            )
        if (
            archive_object_boundary.granularity
            is not BinanceArchiveObjectGranularity.DAY
        ):
            raise PublicHistoryContractError(
                "daily archive source requires a day object boundary"
            )
    elif normalized_source_kind is BinancePublicHistorySourceKind.ARCHIVE_MONTHLY:
        if archive_object_boundary is None:
            raise PublicHistoryContractError(
                "monthly archive source requires a month object boundary"
            )
        if (
            archive_object_boundary.granularity
            is not BinanceArchiveObjectGranularity.MONTH
        ):
            raise PublicHistoryContractError(
                "monthly archive source requires a month object boundary"
            )
    elif archive_object_boundary is not None:
        raise PublicHistoryContractError(
            "REST source must not include an archive object boundary"
        )

    return (
        normalized_market,
        normalized_data_type,
        normalized_interval,
        normalized_source_kind,
        archive_object_boundary,
    )


@dataclass(frozen=True, slots=True)
class BinancePublicHistorySourceIdentity:
    """Stable source identity independent of the caller's request range."""

    instrument: InstrumentId
    data_type: BinancePublicHistoryDataType
    source_kind: BinancePublicHistorySourceKind
    interval: BinanceKlineInterval | None
    archive_object_boundary: BinanceArchiveObjectBoundary | None
    market: BinanceMarket = BinanceMarket.USD_M

    def __post_init__(self) -> None:
        (
            market,
            data_type,
            interval,
            source_kind,
            archive_object_boundary,
        ) = _normalize_components(
            market=self.market,
            instrument=self.instrument,
            data_type=self.data_type,
            interval=self.interval,
            source_kind=self.source_kind,
            archive_object_boundary=self.archive_object_boundary,
        )
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "interval", interval)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "archive_object_boundary", archive_object_boundary)

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON-compatible source identity fields."""
        return {
            "venue": _BINANCE_VENUE,
            "market": self.market.value,
            "instrument": self.instrument.to_dict(),
            "data_type": self.data_type.value,
            "interval": self.interval.value if self.interval is not None else None,
            "source_kind": self.source_kind.value,
            "archive_object_boundary": (
                self.archive_object_boundary.to_dict()
                if self.archive_object_boundary is not None
                else None
            ),
        }

    def to_json(self) -> str:
        """Return canonical JSON for logs, manifests, and comparisons."""
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Create a source identity from exact serialized fields."""
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "venue",
                    "market",
                    "instrument",
                    "data_type",
                    "interval",
                    "source_kind",
                    "archive_object_boundary",
                }
            ),
            model="BinancePublicHistorySourceIdentity",
        )
        venue = _require_string(fields["venue"], field="venue")
        if venue != _BINANCE_VENUE:
            raise PublicHistoryContractError(f"venue must be {_BINANCE_VENUE!r}")
        boundary_value = fields["archive_object_boundary"]
        boundary = (
            None
            if boundary_value is None
            else BinanceArchiveObjectBoundary.from_dict(boundary_value)
        )
        interval_value = fields["interval"]
        interval = (
            None
            if interval_value is None
            else _coerce_enum(interval_value, BinanceKlineInterval, field="interval")
        )
        return cls(
            instrument=InstrumentId.from_dict(fields["instrument"]),
            data_type=_coerce_enum(
                fields["data_type"],
                BinancePublicHistoryDataType,
                field="data type",
            ),
            source_kind=_coerce_enum(
                fields["source_kind"],
                BinancePublicHistorySourceKind,
                field="source kind",
            ),
            interval=interval,
            archive_object_boundary=boundary,
            market=_coerce_enum(fields["market"], BinanceMarket, field="market"),
        )


@dataclass(frozen=True, slots=True)
class BinancePublicHistoryRequest:
    """A typed Binance USDⓈ-M public-history acquisition request.

    ``request_range`` is the caller's UTC half-open range.  An archive
    ``archive_object_boundary`` is the upstream day/month object boundary;
    keeping them as separate fields prevents an archive filename from being
    mistaken for the requested or observed data range.
    """

    instrument: InstrumentId
    data_type: BinancePublicHistoryDataType
    request_range: TimeRange
    source_kind: BinancePublicHistorySourceKind
    interval: BinanceKlineInterval | None = None
    archive_object_boundary: BinanceArchiveObjectBoundary | None = None
    market: BinanceMarket = BinanceMarket.USD_M

    def __post_init__(self) -> None:
        if not isinstance(self.request_range, TimeRange):
            raise TypeError("request_range must be a TimeRange")
        (
            market,
            data_type,
            interval,
            source_kind,
            archive_object_boundary,
        ) = _normalize_components(
            market=self.market,
            instrument=self.instrument,
            data_type=self.data_type,
            interval=self.interval,
            source_kind=self.source_kind,
            archive_object_boundary=self.archive_object_boundary,
        )
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "interval", interval)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "archive_object_boundary", archive_object_boundary)

    @property
    def time_range(self) -> TimeRange:
        """Alias exposing the request's existing domain range terminology."""
        return self.request_range

    @property
    def source_object_boundary(self) -> BinanceArchiveObjectBoundary | None:
        """Return the archive boundary, distinct from ``request_range``."""
        return self.archive_object_boundary

    @property
    def source_identity(self) -> BinancePublicHistorySourceIdentity:
        """Return the stable source identity for this request."""
        return BinancePublicHistorySourceIdentity(
            instrument=self.instrument,
            data_type=self.data_type,
            source_kind=self.source_kind,
            interval=self.interval,
            archive_object_boundary=self.archive_object_boundary,
            market=self.market,
        )

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON-compatible request and source fields."""
        return {
            **self.source_identity.to_dict(),
            "request_range": self.request_range.to_dict(),
        }

    def to_json(self) -> str:
        """Return canonical JSON for the complete request identity."""
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Create a request from exact serialized fields."""
        fields = _require_exact_fields(
            value,
            expected=frozenset(
                {
                    "venue",
                    "market",
                    "instrument",
                    "data_type",
                    "interval",
                    "source_kind",
                    "archive_object_boundary",
                    "request_range",
                }
            ),
            model="BinancePublicHistoryRequest",
        )
        venue = _require_string(fields["venue"], field="venue")
        if venue != _BINANCE_VENUE:
            raise PublicHistoryContractError(f"venue must be {_BINANCE_VENUE!r}")
        boundary_value = fields["archive_object_boundary"]
        boundary = (
            None
            if boundary_value is None
            else BinanceArchiveObjectBoundary.from_dict(boundary_value)
        )
        interval_value = fields["interval"]
        interval = (
            None
            if interval_value is None
            else _coerce_enum(interval_value, BinanceKlineInterval, field="interval")
        )
        return cls(
            instrument=InstrumentId.from_dict(fields["instrument"]),
            data_type=_coerce_enum(
                fields["data_type"],
                BinancePublicHistoryDataType,
                field="data type",
            ),
            request_range=TimeRange.from_dict(fields["request_range"]),
            source_kind=_coerce_enum(
                fields["source_kind"],
                BinancePublicHistorySourceKind,
                field="source kind",
            ),
            interval=interval,
            archive_object_boundary=boundary,
            market=_coerce_enum(fields["market"], BinanceMarket, field="market"),
        )
