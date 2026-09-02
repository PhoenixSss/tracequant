import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from tracequant.data import (
    BinanceArchiveObjectBoundary,
    BinanceArchiveObjectGranularity,
    BinanceKlineInterval,
    BinanceMarket,
    BinancePublicHistoryDataType,
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceIdentity,
    BinancePublicHistorySourceKind,
    PublicHistoryContractError,
)
from tracequant.domain import InstrumentId, TimeRange


def _request(
    *,
    data_type: BinancePublicHistoryDataType = (
        BinancePublicHistoryDataType.CONTRACT_KLINE
    ),
    source_kind: BinancePublicHistorySourceKind = (
        BinancePublicHistorySourceKind.ARCHIVE_DAILY
    ),
    interval: BinanceKlineInterval | None = BinanceKlineInterval.ONE_MINUTE,
    archive_object_boundary: BinanceArchiveObjectBoundary | None = None,
) -> BinancePublicHistoryRequest:
    if (
        archive_object_boundary is None
        and source_kind is not BinancePublicHistorySourceKind.REST
    ):
        archive_object_boundary = BinanceArchiveObjectBoundary.day(date(2024, 2, 29))
    return BinancePublicHistoryRequest(
        instrument=InstrumentId("BTCUSDT"),
        data_type=data_type,
        request_range=TimeRange(
            start=datetime(2024, 2, 29, 23, 45, tzinfo=UTC),
            end=datetime(2024, 3, 1, 0, 15, tzinfo=UTC),
        ),
        source_kind=source_kind,
        interval=interval,
        archive_object_boundary=archive_object_boundary,
    )


def test_public_history_request_has_stable_utc_source_identity() -> None:
    first = BinancePublicHistoryRequest(
        instrument=InstrumentId(" btcusdt "),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2024, 3, 1, 7, 45, tzinfo=timezone(timedelta(hours=8))),
            end=datetime(2024, 3, 1, 8, 15, tzinfo=timezone(timedelta(hours=8))),
        ),
        source_kind=BinancePublicHistorySourceKind.ARCHIVE_DAILY,
        interval=BinanceKlineInterval.ONE_MINUTE,
        archive_object_boundary=BinanceArchiveObjectBoundary.day(date(2024, 2, 29)),
    )
    second = BinancePublicHistoryRequest(
        instrument=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2024, 2, 29, 23, 45, tzinfo=UTC),
            end=datetime(2024, 3, 1, 0, 15, tzinfo=UTC),
        ),
        source_kind=BinancePublicHistorySourceKind.ARCHIVE_DAILY,
        interval=BinanceKlineInterval.ONE_MINUTE,
        archive_object_boundary=BinanceArchiveObjectBoundary.daily(date(2024, 2, 29)),
    )

    assert first == second
    assert first.source_identity == second.source_identity
    assert first.to_json() == second.to_json()
    payload = first.to_dict()
    assert json.loads(first.to_json()) == payload
    assert json.dumps(payload, allow_nan=False) == json.dumps(
        second.to_dict(), allow_nan=False
    )
    assert payload["request_range"] != payload["archive_object_boundary"]
    assert BinancePublicHistoryRequest.from_dict(payload) == first
    assert (
        BinancePublicHistorySourceIdentity.from_dict(first.source_identity.to_dict())
        == first.source_identity
    )


@pytest.mark.parametrize("instrument", ["BTCUSDT", "ETHUSDT", "BTCUSDC", "ETHUSDC"])
def test_first_binance_instrument_set_is_expressible(instrument: str) -> None:
    request = BinancePublicHistoryRequest(
        instrument=InstrumentId(instrument),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 1, 1, 0, 1, tzinfo=UTC),
        ),
        source_kind=BinancePublicHistorySourceKind.REST,
        interval=BinanceKlineInterval.ONE_MINUTE,
    )

    assert request.instrument == InstrumentId(instrument)
    assert request.archive_object_boundary is None


@pytest.mark.parametrize(
    "data_type",
    [
        BinancePublicHistoryDataType.CONTRACT_KLINE,
        BinancePublicHistoryDataType.MARK_PRICE_KLINE,
        BinancePublicHistoryDataType.INDEX_PRICE_KLINE,
    ],
)
def test_each_kline_family_is_typed_as_one_minute(
    data_type: BinancePublicHistoryDataType,
) -> None:
    request = _request(data_type=data_type)

    assert request.interval is BinanceKlineInterval.ONE_MINUTE
    assert request.data_type is data_type
    assert request.source_identity.to_dict()["data_type"] == data_type.value


def test_settled_funding_is_not_a_kline() -> None:
    request = _request(
        data_type=BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
        source_kind=BinancePublicHistorySourceKind.ARCHIVE_MONTHLY,
        interval=None,
        archive_object_boundary=BinanceArchiveObjectBoundary.month(2024, 2),
    )

    assert request.interval is None
    assert request.data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
    assert request.source_kind is BinancePublicHistorySourceKind.ARCHIVE_MONTHLY


def test_monthly_archive_boundary_is_explicit_and_utc_calendar_based() -> None:
    boundary = BinanceArchiveObjectBoundary.monthly(2024, 2)

    assert boundary.granularity is BinanceArchiveObjectGranularity.MONTH
    assert boundary.period_start == date(2024, 2, 1)
    assert boundary.to_dict() == {
        "granularity": "month",
        "period_start": "2024-02-01",
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"interval": "5m"},
            "supported Kline interval must be 1m",
        ),
        (
            {"interval": None},
            "supported Kline interval must be 1m",
        ),
        (
            {
                "data_type": BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
                "interval": BinanceKlineInterval.ONE_MINUTE,
                "source_kind": BinancePublicHistorySourceKind.ARCHIVE_MONTHLY,
                "archive_object_boundary": BinanceArchiveObjectBoundary.month(2024, 2),
            },
            "does not use a Kline interval",
        ),
        (
            {
                "data_type": BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
                "source_kind": BinancePublicHistorySourceKind.ARCHIVE_DAILY,
                "interval": None,
                "archive_object_boundary": BinanceArchiveObjectBoundary.day(
                    date(2024, 2, 29)
                ),
            },
            "has no daily archive source",
        ),
        (
            {
                "source_kind": BinancePublicHistorySourceKind.REST,
                "archive_object_boundary": BinanceArchiveObjectBoundary.day(
                    date(2024, 2, 29)
                ),
            },
            "REST source must not include",
        ),
        (
            {
                "source_kind": BinancePublicHistorySourceKind.ARCHIVE_DAILY,
                "archive_object_boundary": BinanceArchiveObjectBoundary.month(2024, 2),
            },
            "daily archive source requires a day",
        ),
        (
            {"instrument": InstrumentId("SOLUSDT")},
            "outside the supported",
        ),
        (
            {"market": "spot"},
            "market has unsupported value",
        ),
    ],
)
def test_unsupported_public_history_combinations_fail(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(PublicHistoryContractError, match=message):
        replace(_request(), **kwargs)  # type: ignore[arg-type]


def test_request_uses_the_existing_instrument_and_time_range_models() -> None:
    request = _request()

    assert isinstance(request.instrument, InstrumentId)
    assert isinstance(request.request_range, TimeRange)
    assert request.market is BinanceMarket.USD_M
