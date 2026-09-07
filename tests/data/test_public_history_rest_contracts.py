import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tracequant.data import (
    BinanceKlineInterval,
    BinancePriceIndexId,
    BinanceRestEndpoint,
    BinanceRestPageIdentity,
    BinanceRestPageProvenance,
    BinanceRestPageRequest,
    BinanceRestRequestBounds,
    PublicHistoryContractError,
    RawObjectIdentity,
    RawRevisionEvidenceKind,
    RawRevisionIdentity,
    next_binance_funding_cursor,
    next_binance_kline_cursor,
)
from tracequant.domain import InstrumentId, TimeRange


def _caller_range() -> TimeRange:
    return TimeRange(
        start=datetime(2024, 3, 1, tzinfo=UTC),
        end=datetime(2024, 3, 1, 0, 10, tzinfo=UTC),
    )


def _contract_page(*, page_start_ms: int | None = None) -> BinanceRestPageRequest:
    caller_range = _caller_range()
    caller_bounds = BinanceRestRequestBounds.from_caller_range(caller_range)
    return BinanceRestPageRequest(
        endpoint=BinanceRestEndpoint.CONTRACT_KLINES,
        subject=InstrumentId("BTCUSDT"),
        caller_range=caller_range,
        interval=BinanceKlineInterval.ONE_MINUTE,
        limit=3,
        page_boundary=BinanceRestRequestBounds(
            start_time_ms=(
                caller_bounds.start_time_ms if page_start_ms is None else page_start_ms
            ),
            end_time_ms=caller_bounds.end_time_ms,
        ),
    )


def test_rest_page_identity_and_provenance_are_stable_and_page_specific() -> None:
    first = _contract_page()
    same_in_local_timezone = BinanceRestPageRequest(
        endpoint=BinanceRestEndpoint.CONTRACT_KLINES,
        subject=InstrumentId(" btcusdt "),
        caller_range=TimeRange(
            start=datetime(2024, 3, 1, 8, tzinfo=timezone(timedelta(hours=8))),
            end=datetime(2024, 3, 1, 8, 10, tzinfo=timezone(timedelta(hours=8))),
        ),
        interval=BinanceKlineInterval.ONE_MINUTE,
        limit=3,
    )
    second_page = first.next_page(first.identity.page_boundary.start_time_ms)

    assert first.identity == same_in_local_timezone.identity
    assert first.identity.to_json() == same_in_local_timezone.identity.to_json()
    assert first.identity.logical_id == same_in_local_timezone.identity.logical_id
    assert second_page.identity.logical_id != first.identity.logical_id
    assert first.normalized_params == {
        "endTime": 1_709_251_799_999,
        "interval": "1m",
        "limit": 3,
        "startTime": 1_709_251_200_000,
        "symbol": "BTCUSDT",
    }
    assert "pair" not in first.normalized_params

    empty_body = b"[]"
    empty = BinanceRestPageProvenance.from_response(
        request=first,
        response_body=empty_body,
        observed_at=datetime(2024, 3, 1, 8, 11, tzinfo=timezone(timedelta(hours=8))),
        http_status=200,
        response_headers={"Content-Type": "application/json", "X-MBX-USED-WEIGHT": "1"},
        record_count=0,
    )
    replacement = BinanceRestPageProvenance.from_response(
        request=first,
        response_body=b"[[1709251200000]]",
        observed_at=datetime(2024, 3, 1, 0, 12, tzinfo=UTC),
        http_status=200,
        response_headers={"content-type": "application/json"},
        record_count=1,
    )

    assert empty.response_sha256 == hashlib.sha256(empty_body).hexdigest()
    assert empty.observed_at == datetime(2024, 3, 1, 0, 11, tzinfo=UTC)
    assert empty.server_time is None
    assert empty.is_successful_empty
    assert empty.to_dict()["request"] == first.to_dict()
    assert BinanceRestPageProvenance.from_dict(empty.to_dict()) == empty

    first_revision = RawRevisionIdentity.from_rest_page_provenance(empty)
    replacement_revision = RawRevisionIdentity.from_rest_page_provenance(replacement)
    second_page_identity = RawObjectIdentity.from_rest_page_request(second_page)
    assert first_revision.logical_identity == RawObjectIdentity.from_rest_page_request(
        first
    )
    assert first_revision.evidence_kind is RawRevisionEvidenceKind.RESPONSE_SHA256
    assert first_revision.verified_response_sha256 == empty.response_sha256
    assert first_revision.revision_id != replacement_revision.revision_id
    assert first_revision.logical_object_id == replacement_revision.logical_object_id
    assert second_page_identity.object_id != first_revision.logical_object_id
    assert RawObjectIdentity.from_dict(first_revision.logical_identity.to_dict()) == (
        first_revision.logical_identity
    )


def test_index_page_uses_pair_while_tradable_families_use_symbol() -> None:
    index_page = BinanceRestPageRequest(
        endpoint=BinanceRestEndpoint.INDEX_PRICE_KLINES,
        subject=BinancePriceIndexId("BTCUSDT"),
        caller_range=_caller_range(),
        interval=BinanceKlineInterval.ONE_MINUTE,
        limit=100,
    )

    assert index_page.normalized_params["pair"] == "BTCUSDT"
    assert "symbol" not in index_page.normalized_params
    assert index_page.identity.to_dict()["subject_kind"] == "price_index_pair"
    assert BinanceRestPageRequest.from_dict(index_page.to_dict()) == index_page
    assert BinanceRestPageIdentity.from_dict(index_page.identity.to_dict()) == (
        index_page.identity
    )

    with pytest.raises(PublicHistoryContractError, match="price-index pair"):
        BinanceRestPageRequest(
            endpoint=BinanceRestEndpoint.INDEX_PRICE_KLINES,
            subject=InstrumentId("BTCUSDT"),
            caller_range=_caller_range(),
            interval=BinanceKlineInterval.ONE_MINUTE,
            limit=100,
        )


def test_half_open_range_has_one_inclusive_upstream_conversion() -> None:
    bounds = BinanceRestRequestBounds.from_caller_range(_caller_range())

    assert bounds == BinanceRestRequestBounds(
        start_time_ms=1_709_251_200_000,
        end_time_ms=1_709_251_799_999,
    )
    assert BinanceRestRequestBounds.from_dict(bounds.to_dict()) == bounds

    with pytest.raises(PublicHistoryContractError, match="millisecond precision"):
        BinanceRestRequestBounds.from_caller_range(
            TimeRange(
                start=datetime(2024, 3, 1, 0, 0, 0, 1, tzinfo=UTC),
                end=datetime(2024, 3, 1, 0, 1, tzinfo=UTC),
            )
        )


def test_empty_http_page_is_distinct_from_http_error_and_transport_failure() -> None:
    request = _contract_page()
    empty = BinanceRestPageProvenance.from_response(
        request=request,
        response_body=b"[]",
        observed_at=datetime(2024, 3, 1, 0, 11, tzinfo=UTC),
        http_status=200,
        response_headers={},
        record_count=0,
    )
    not_found = BinanceRestPageProvenance.from_response(
        request=request,
        response_body=b'{"code":-1}',
        observed_at=datetime(2024, 3, 1, 0, 11, tzinfo=UTC),
        http_status=404,
        response_headers={},
        record_count=None,
    )

    assert empty.is_successful_empty
    assert not not_found.is_successful_response
    assert empty != not_found
    with pytest.raises(TypeError, match="response_body must be bytes"):
        BinanceRestPageProvenance.from_response(
            request=request,
            response_body=None,  # type: ignore[arg-type]
            observed_at=datetime(2024, 3, 1, 0, 11, tzinfo=UTC),
            http_status=200,
            response_headers={},
        )


def test_response_provenance_is_utc_and_header_allow_listed() -> None:
    request = _contract_page()

    with pytest.raises(PublicHistoryContractError, match="timezone-aware"):
        BinanceRestPageProvenance.from_response(
            request=request,
            response_body=b"[]",
            observed_at=datetime(2024, 3, 1),
            http_status=200,
            response_headers={},
        )
    with pytest.raises(PublicHistoryContractError, match="not allow-listed"):
        BinanceRestPageProvenance.from_response(
            request=request,
            response_body=b"[]",
            observed_at=datetime(2024, 3, 1, tzinfo=UTC),
            http_status=200,
            response_headers={"Authorization": "secret"},
        )


def test_kline_and_funding_cursor_progression_are_typed_without_a_loop() -> None:
    assert (
        next_binance_kline_cursor(1_709_251_200_000, BinanceKlineInterval.ONE_MINUTE)
        == 1_709_251_260_000
    )
    assert next_binance_funding_cursor(1_709_251_200_000) == 1_709_251_200_001

    funding_page = BinanceRestPageRequest(
        endpoint=BinanceRestEndpoint.FUNDING_RATE_HISTORY,
        subject=InstrumentId("BTCUSDT"),
        caller_range=_caller_range(),
        interval=None,
        limit=100,
    )
    next_page = funding_page.next_page(1_709_251_200_000)

    assert next_page.identity.page_boundary.start_time_ms == 1_709_251_200_001
    assert "interval" not in funding_page.normalized_params


@pytest.mark.parametrize(
    "endpoint",
    [
        BinanceRestEndpoint.CONTRACT_KLINES,
        BinanceRestEndpoint.MARK_PRICE_KLINES,
        BinanceRestEndpoint.INDEX_PRICE_KLINES,
        BinanceRestEndpoint.FUNDING_RATE_HISTORY,
    ],
)
def test_rest_endpoint_identity_is_controlled(endpoint: BinanceRestEndpoint) -> None:
    assert endpoint.value.startswith("/fapi/v1/")

    with pytest.raises(PublicHistoryContractError, match="unsupported Binance REST"):
        BinanceRestPageRequest(
            endpoint="https://example.invalid/arbitrary",  # type: ignore[arg-type]
            subject=InstrumentId("BTCUSDT"),
            caller_range=_caller_range(),
            interval=BinanceKlineInterval.ONE_MINUTE,
            limit=100,
        )
