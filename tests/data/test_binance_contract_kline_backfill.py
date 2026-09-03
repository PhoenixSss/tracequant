import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from tracequant.data import (
    ArchiveHttpResponse,
    BinanceContractKlineBackfill,
    BinanceContractKlineStatus,
    RawObjectIdentity,
    RawStore,
    plan_binance_contract_kline_archives,
)
from tracequant.domain import InstrumentId, TimeRange

HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore\n"
)


class FixtureHttp:
    def __init__(self, responses: dict[str, ArchiveHttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> ArchiveHttpResponse:
        self.calls.append((url, timeout))
        return self.responses.get(url, ArchiveHttpResponse(404, b"missing", {}))


def _range(start: str, end: str) -> TimeRange:
    return TimeRange(
        start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        end=datetime.fromisoformat(end).replace(tzinfo=UTC),
    )


def _archive(member: str, rows: list[str], *, header: bool = True) -> bytes:
    payload = ((HEADER if header else "") + "".join(rows)).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, payload)
    return output.getvalue()


def _row(open_time: int, close: str = "61010.0") -> str:
    return (
        f"{open_time},61000.0,61020.0,60990.0,{close},12.5,"
        f"{open_time + 59_999},762500.0,42,6.0,366000.0,0\n"
    )


def _responses(url: str, archive: bytes) -> dict[str, ArchiveHttpResponse]:
    filename = url.rsplit("/", 1)[-1]
    checksum = hashlib.sha256(archive).hexdigest()
    return {
        url: ArchiveHttpResponse(200, archive, {"Content-Type": "application/zip"}),
        f"{url}.CHECKSUM": ArchiveHttpResponse(
            200, f"{checksum}  {filename}\n".encode(), {}
        ),
    }


def test_planner_uses_monthly_objects_for_complete_months_and_daily_edges() -> None:
    plans = plan_binance_contract_kline_archives(
        InstrumentId("ETHUSDT"),
        _range("2024-01-31T23:30:00", "2024-03-01T00:30:00"),
    )

    assert [plan.request.source_kind.value for plan in plans] == [
        "archive_daily",
        "archive_monthly",
        "archive_daily",
    ]
    boundaries = [plan.request.archive_object_boundary for plan in plans]
    assert all(boundary is not None for boundary in boundaries)
    assert [
        boundary.period_start.isoformat()
        for boundary in boundaries
        if boundary is not None
    ] == [
        "2024-01-31",
        "2024-02-01",
        "2024-03-01",
    ]
    assert plans[1].object_key == (
        "data/futures/um/monthly/klines/ETHUSDT/1m/ETHUSDT-1m-2024-02.zip"
    )
    assert all(
        plan.request.request_range == plans[0].request.request_range for plan in plans
    )


def test_planner_does_not_infer_monthly_availability_after_research_cutoff() -> None:
    plans = plan_binance_contract_kline_archives(
        InstrumentId("BTCUSDT"),
        _range("2026-08-01T00:00:00", "2026-09-01T00:00:00"),
    )

    assert len(plans) == 31
    assert {plan.request.source_kind.value for plan in plans} == {"archive_daily"}


def test_backfill_contract_klines_publishes_verified_raw_artifact(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:02:00")
    plan = plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)[
        0
    ]
    archive = _archive(
        plan.member_name,
        [_row(1709164800000), _row(1709164860000, close="61011.0")],
    )
    transport = FixtureHttp(_responses(plan.url, archive))
    store = RawStore(tmp_path)

    result = BinanceContractKlineBackfill(store, http_get=transport, timeout=2.5).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed is True
    assert [item.status for item in result.objects] == [
        BinanceContractKlineStatus.PUBLISHED
    ]
    artifact = store.read_request(plan.request)
    assert artifact.frame.columns == [
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
    ]
    assert artifact.frame["close"].to_list() == ["61010.0", "61011.0"]
    assert artifact.frame["count"].to_list() == [42, 42]
    assert artifact.manifest.upstream_checksum == (
        f"sha256:{hashlib.sha256(archive).hexdigest()}"
    )
    assert artifact.manifest.upstream_revision == plan.url
    assert all(timeout == 2.5 for _, timeout in transport.calls)


def test_checksum_mismatch_does_not_publish_completed_artifact(tmp_path: Path) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)[
        0
    ]
    archive = _archive(plan.member_name, [_row(1709164800000)])
    responses = _responses(plan.url, archive)
    responses[plan.checksum_url] = ArchiveHttpResponse(
        200, f"{'0' * 64}  {plan.url.rsplit('/', 1)[-1]}\n".encode(), {}
    )
    store = RawStore(tmp_path)

    result = BinanceContractKlineBackfill(store, http_get=FixtureHttp(responses)).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.INVALID_CONTENT
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


def test_invalid_archive_member_and_row_shape_are_rejected(tmp_path: Path) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = plan_binance_contract_kline_archives(InstrumentId("ETHUSDT"), request_range)[
        0
    ]
    malformed = _archive("unexpected.csv", ["1,2,3\n"])

    result = BinanceContractKlineBackfill(
        RawStore(tmp_path), http_get=FixtureHttp(_responses(plan.url, malformed))
    ).run(InstrumentId("ETHUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.INVALID_CONTENT


def test_missing_minute_is_reported_as_coverage_gap_and_not_published(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:03:00")
    plan = plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)[
        0
    ]
    archive = _archive(
        plan.member_name,
        [_row(1709164800000), _row(1709164920000)],
    )
    store = RawStore(tmp_path)

    result = BinanceContractKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan.url, archive))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.COVERAGE_GAP
    assert result.objects[0].detail == "archive rows contain a missing 1m timestamp"
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


def test_cross_object_partial_failure_keeps_published_object_and_is_not_complete(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T23:59:00", "2024-03-01T00:01:00")
    plans = plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)
    first_archive = _archive(plans[0].member_name, [_row(1709251140000)])
    transport = FixtureHttp(_responses(plans[0].url, first_archive))
    store = RawStore(tmp_path)

    result = BinanceContractKlineBackfill(store, http_get=transport).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed is False
    assert [item.status for item in result.objects] == [
        BinanceContractKlineStatus.PUBLISHED,
        BinanceContractKlineStatus.NOT_FOUND,
    ]
    assert store.read_request(plans[0].request).frame.height == 1


def test_verified_existing_object_is_skipped_without_network(tmp_path: Path) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)[
        0
    ]
    archive = _archive(plan.member_name, [_row(1709164800000)], header=False)
    store = RawStore(tmp_path)
    first_http = FixtureHttp(_responses(plan.url, archive))
    backfill = BinanceContractKlineBackfill(store, http_get=first_http)
    assert backfill.run(InstrumentId("BTCUSDT"), request_range).completed

    no_network = FixtureHttp({})
    second = BinanceContractKlineBackfill(store, http_get=no_network).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert second.completed
    assert second.objects[0].status is BinanceContractKlineStatus.EXISTING
    assert no_network.calls == []


def test_existing_object_must_cover_the_current_wider_request(tmp_path: Path) -> None:
    narrow_range = _range("2024-02-29T00:00:00", "2024-02-29T00:02:00")
    narrow_plan = plan_binance_contract_kline_archives(
        InstrumentId("BTCUSDT"), narrow_range
    )[0]
    archive = _archive(
        narrow_plan.member_name,
        [_row(1709164800000), _row(1709164860000)],
    )
    store = RawStore(tmp_path)
    first_http = FixtureHttp(_responses(narrow_plan.url, archive))

    first = BinanceContractKlineBackfill(store, http_get=first_http).run(
        InstrumentId("BTCUSDT"), narrow_range
    )

    assert first.completed is True

    wider_range = _range("2024-02-29T00:00:00", "2024-02-29T00:03:00")
    no_network = FixtureHttp({})
    second = BinanceContractKlineBackfill(store, http_get=no_network).run(
        InstrumentId("BTCUSDT"), wider_range
    )

    assert second.completed is False
    assert second.objects[0].status is BinanceContractKlineStatus.COVERAGE_GAP
    assert second.objects[0].artifact_path is not None
    assert second.objects[0].detail == (
        "archive rows do not cover the caller range within this source object"
    )
    assert no_network.calls == []
