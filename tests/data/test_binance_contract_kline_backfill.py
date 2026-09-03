import hashlib
import http.client
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch

from tracequant.data import (
    ArchiveHttpResponse,
    BinanceArchiveCoverageGapPlan,
    BinanceArchiveObjectPlan,
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
ONE_MINUTE_MS = 60_000


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


def _archive_plans(
    instrument: InstrumentId, request_range: TimeRange
) -> tuple[BinanceArchiveObjectPlan, ...]:
    plans = plan_binance_contract_kline_archives(instrument, request_range)
    assert all(isinstance(plan, BinanceArchiveObjectPlan) for plan in plans)
    return tuple(plan for plan in plans if isinstance(plan, BinanceArchiveObjectPlan))


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


def _full_daily_archive(
    plan: BinanceArchiveObjectPlan,
    start_open_time: int,
    *,
    first_close: str = "61010.0",
    second_close: str = "61010.0",
    header: bool = True,
) -> bytes:
    rows = [_row(start_open_time + offset * ONE_MINUTE_MS) for offset in range(24 * 60)]
    rows[0] = _row(start_open_time, close=first_close)
    rows[1] = _row(start_open_time + ONE_MINUTE_MS, close=second_close)
    return _archive(plan.member_name, rows, header=header)


def _responses(url: str, archive: bytes) -> dict[str, ArchiveHttpResponse]:
    filename = url.rsplit("/", 1)[-1]
    checksum = hashlib.sha256(archive).hexdigest()
    return {
        url: ArchiveHttpResponse(200, archive, {"Content-Type": "application/zip"}),
        f"{url}.CHECKSUM": ArchiveHttpResponse(
            200,
            f"{checksum}  {filename}\n".encode(),
            {"Content-Type": "text/plain"},
        ),
    }


def test_planner_uses_monthly_objects_for_complete_months_and_daily_edges() -> None:
    plans = _archive_plans(
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


def test_planner_uses_only_daily_objects_proven_at_publication_boundary() -> None:
    plans = plan_binance_contract_kline_archives(
        InstrumentId("BTCUSDT"),
        _range("2026-08-01T00:00:00", "2026-09-01T00:00:00"),
    )

    assert len(plans) == 31
    assert all(isinstance(plan, BinanceArchiveObjectPlan) for plan in plans[:29])
    assert all(
        plan.request.source_kind.value == "archive_daily"
        for plan in plans[:29]
        if isinstance(plan, BinanceArchiveObjectPlan)
    )
    assert all(isinstance(plan, BinanceArchiveCoverageGapPlan) for plan in plans[29:])


def test_planner_uses_daily_lower_boundary_and_marks_earlier_days_as_gaps() -> None:
    plans = plan_binance_contract_kline_archives(
        InstrumentId("BTCUSDT"),
        _range("2019-12-01T00:00:00", "2020-01-01T00:00:00"),
    )

    assert len(plans) == 31
    assert all(isinstance(plan, BinanceArchiveCoverageGapPlan) for plan in plans[:30])
    assert isinstance(plans[-1], BinanceArchiveObjectPlan)
    assert plans[-1].request.source_kind.value == "archive_daily"
    assert plans[-1].object_key.endswith("BTCUSDT-1m-2019-12-31.zip")


def test_backfill_preserves_available_daily_object_beside_lower_boundary_gap(
    tmp_path: Path,
) -> None:
    request_range = _range("2019-12-30T23:59:00", "2019-12-31T00:01:00")
    plans = plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)
    assert isinstance(plans[0], BinanceArchiveCoverageGapPlan)
    assert isinstance(plans[1], BinanceArchiveObjectPlan)
    archive = _full_daily_archive(plans[1], 1577750400000)
    transport = FixtureHttp(_responses(plans[1].url, archive))

    result = BinanceContractKlineBackfill(RawStore(tmp_path), http_get=transport).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert [item.status for item in result.objects] == [
        BinanceContractKlineStatus.COVERAGE_GAP,
        BinanceContractKlineStatus.PUBLISHED,
    ]
    assert [url for url, _ in transport.calls] == [
        plans[1].checksum_url,
        plans[1].url,
    ]


def test_backfill_reports_unproven_dates_without_accessing_archive_urls(
    tmp_path: Path,
) -> None:
    transport = FixtureHttp({})

    result = BinanceContractKlineBackfill(RawStore(tmp_path), http_get=transport).run(
        InstrumentId("ETHUSDT"),
        _range("2026-08-30T00:00:00", "2026-09-01T00:00:00"),
    )

    assert result.completed is False
    assert [item.status for item in result.objects] == [
        BinanceContractKlineStatus.COVERAGE_GAP,
        BinanceContractKlineStatus.COVERAGE_GAP,
    ]
    assert transport.calls == []


def test_planner_rejects_instrument_outside_the_adapter_supported_set() -> None:
    try:
        plan_binance_contract_kline_archives(
            InstrumentId("BTCUSDC"),
            _range("2024-01-04T00:00:00", "2024-01-05T00:00:00"),
        )
    except ValueError as error:
        assert str(error) == (
            "instrument BTCUSDC has no frozen contract-Kline archive coverage"
        )
    else:
        raise AssertionError("BTCUSDC must remain outside this adapter's scope")


def test_backfill_contract_klines_publishes_verified_raw_artifact(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:02:00")
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
    archive = _full_daily_archive(
        plan,
        1709164800000,
        second_close="61011.0",
    )
    responses = _responses(plan.url, archive)
    transport = FixtureHttp(responses)
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
    assert artifact.frame["close"].head(2).to_list() == ["61010.0", "61011.0"]
    assert artifact.frame["count"].head(2).to_list() == [42, 42]
    assert artifact.manifest.upstream_checksum == (
        f"sha256:{hashlib.sha256(archive).hexdigest()}"
    )
    assert artifact.manifest.upstream_revision == plan.url
    provenance = artifact.manifest.provenance
    assert provenance is not None
    assert provenance.object_key == plan.object_key
    assert provenance.source_url == plan.url
    assert provenance.source_http_status == 200
    assert dict(provenance.source_http_headers) == {"Content-Type": "application/zip"}
    assert provenance.checksum_url == plan.checksum_url
    assert provenance.checksum_http_status == 200
    assert dict(provenance.checksum_http_headers) == {"Content-Type": "text/plain"}
    assert (
        provenance.checksum_response_sha256
        == hashlib.sha256(responses[plan.checksum_url].body).hexdigest()
    )
    assert provenance.archive_sha256 == hashlib.sha256(archive).hexdigest()
    assert provenance.csv_member == plan.member_name
    assert provenance.validation_evidence == (
        "checksum_response_verified",
        "archive_sha256_matches_checksum",
        "zip_member_structure_verified",
        "csv_schema_and_rows_verified",
        "source_object_coverage_verified",
    )
    assert provenance.acquired_at.tzinfo is not None
    assert all(timeout == 2.5 for _, timeout in transport.calls)


def test_checksum_mismatch_does_not_publish_completed_artifact(tmp_path: Path) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
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


def test_missing_checksum_is_distinct_from_missing_archive(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
    archive = _full_daily_archive(plan, 1709164800000)
    responses = _responses(plan.url, archive)
    responses.pop(plan.checksum_url)

    result = BinanceContractKlineBackfill(
        RawStore(tmp_path), http_get=FixtureHttp(responses)
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.CHECKSUM_NOT_FOUND
    assert result.objects[0].detail == plan.checksum_url
    assert (
        not RawStore(tmp_path)
        .path_for(RawObjectIdentity.from_request(plan.request))
        .exists()
    )


def test_missing_archive_remains_not_found_after_checksum_is_verified(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
    archive = _full_daily_archive(plan, 1709164800000)
    responses = _responses(plan.url, archive)
    responses.pop(plan.url)

    result = BinanceContractKlineBackfill(
        RawStore(tmp_path), http_get=FixtureHttp(responses)
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.NOT_FOUND
    assert result.objects[0].detail == plan.url


def test_truncated_daily_archive_is_not_published_as_completed(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:02:00")
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
    archive = _archive(plan.member_name, [_row(1709164800000)])
    store = RawStore(tmp_path)

    result = BinanceContractKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan.url, archive))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.COVERAGE_GAP
    assert result.objects[0].detail == (
        "archive rows do not cover the complete source object boundary"
    )
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


def test_truncated_monthly_archive_is_not_published_as_completed(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-01T00:00:00", "2024-03-01T00:00:00")
    plan = _archive_plans(InstrumentId("ETHUSDT"), request_range)[0]
    archive = _archive(plan.member_name, [_row(1706745600000)])
    store = RawStore(tmp_path)

    result = BinanceContractKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan.url, archive))
    ).run(InstrumentId("ETHUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.COVERAGE_GAP
    assert result.objects[0].detail == (
        "archive rows do not cover the complete source object boundary"
    )
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


def test_invalid_archive_member_and_row_shape_are_rejected(tmp_path: Path) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _archive_plans(InstrumentId("ETHUSDT"), request_range)[0]
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
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
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
    plans = _archive_plans(InstrumentId("BTCUSDT"), request_range)
    first_archive = _full_daily_archive(plans[0], 1709164800000)
    transport = FixtureHttp(_responses(plans[0].url, first_archive))
    store = RawStore(tmp_path)

    result = BinanceContractKlineBackfill(store, http_get=transport).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed is False
    assert [item.status for item in result.objects] == [
        BinanceContractKlineStatus.PUBLISHED,
        BinanceContractKlineStatus.CHECKSUM_NOT_FOUND,
    ]
    assert store.read_request(plans[0].request).frame.height == 24 * 60


def test_cross_object_incomplete_read_keeps_earlier_published_result(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T23:59:00", "2024-03-01T00:01:00")
    plans = _archive_plans(InstrumentId("BTCUSDT"), request_range)
    first_archive = _full_daily_archive(plans[0], 1709164800000)
    responses = _responses(plans[0].url, first_archive)

    def transport(url: str, timeout: float) -> ArchiveHttpResponse:
        del timeout
        if url == plans[1].checksum_url:
            raise http.client.IncompleteRead(b"truncated")
        return responses.get(url, ArchiveHttpResponse(404, b"missing", {}))

    store = RawStore(tmp_path)
    result = BinanceContractKlineBackfill(store, http_get=transport).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed is False
    assert [item.status for item in result.objects] == [
        BinanceContractKlineStatus.PUBLISHED,
        BinanceContractKlineStatus.RETRYABLE_FAILURE,
    ]
    assert store.read_request(plans[0].request).frame.height == 24 * 60


def test_unsupported_zip_compression_is_reported_as_invalid_content(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _archive_plans(InstrumentId("ETHUSDT"), request_range)[0]
    archive = _archive(plan.member_name, [_row(1709164800000)])

    def unsupported_read(self: zipfile.ZipFile, member: zipfile.ZipInfo) -> bytes:
        del self, member
        raise NotImplementedError("unsupported compression method")

    monkeypatch.setattr(zipfile.ZipFile, "read", unsupported_read)
    result = BinanceContractKlineBackfill(
        RawStore(tmp_path), http_get=FixtureHttp(_responses(plan.url, archive))
    ).run(InstrumentId("ETHUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.INVALID_CONTENT


def test_verified_existing_object_is_reconciled_with_upstream(tmp_path: Path) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
    archive = _full_daily_archive(plan, 1709164800000, header=False)
    store = RawStore(tmp_path)
    first_http = FixtureHttp(_responses(plan.url, archive))
    backfill = BinanceContractKlineBackfill(store, http_get=first_http)
    assert backfill.run(InstrumentId("BTCUSDT"), request_range).completed

    current_http = FixtureHttp(_responses(plan.url, archive))
    second = BinanceContractKlineBackfill(store, http_get=current_http).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert second.completed
    assert second.objects[0].status is BinanceContractKlineStatus.EXISTING
    assert [url for url, _ in current_http.calls] == [plan.checksum_url, plan.url]


def test_upstream_revision_conflicts_without_replacing_existing_artifact(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
    original = _full_daily_archive(plan, 1709164800000)
    revised = _full_daily_archive(plan, 1709164800000, first_close="62000.0")
    store = RawStore(tmp_path)
    first = BinanceContractKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan.url, original))
    ).run(InstrumentId("BTCUSDT"), request_range)
    original_artifact = store.read_request(plan.request)
    original_checksum = original_artifact.manifest.project_sha256

    second = BinanceContractKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan.url, revised))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert first.completed
    assert second.completed is False
    assert second.objects[0].status is BinanceContractKlineStatus.CONFLICT
    assert second.objects[0].artifact_path is None
    preserved = store.read_request(plan.request)
    assert preserved.manifest.project_sha256 == original_checksum
    assert preserved.frame["close"].head(2).to_list() == ["61010.0", "61010.0"]


def test_incomplete_existing_artifact_is_reported_as_local_failure(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _archive_plans(InstrumentId("BTCUSDT"), request_range)[0]
    archive = _full_daily_archive(plan, 1709164800000)
    store = RawStore(tmp_path)
    incomplete_path = store.path_for(RawObjectIdentity.from_request(plan.request))
    incomplete_path.mkdir(parents=True)
    sentinel = incomplete_path / "interrupted-write"
    sentinel.write_text("preserve", encoding="utf-8")

    result = BinanceContractKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan.url, archive))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceContractKlineStatus.LOCAL_FAILURE
    assert result.objects[0].artifact_path is None
    assert result.objects[0].detail == (
        "Raw artifact requires both data.parquet and manifest.json"
    )
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert {path.name for path in incomplete_path.iterdir()} == {"interrupted-write"}


def test_existing_object_is_reconciled_when_request_widens(tmp_path: Path) -> None:
    narrow_range = _range("2024-02-29T00:00:00", "2024-02-29T00:02:00")
    narrow_plan = _archive_plans(InstrumentId("BTCUSDT"), narrow_range)[0]
    archive = _full_daily_archive(narrow_plan, 1709164800000)
    store = RawStore(tmp_path)
    first_http = FixtureHttp(_responses(narrow_plan.url, archive))

    first = BinanceContractKlineBackfill(store, http_get=first_http).run(
        InstrumentId("BTCUSDT"), narrow_range
    )

    assert first.completed is True

    wider_range = _range("2024-02-29T00:00:00", "2024-02-29T00:03:00")
    current_http = FixtureHttp({})
    second = BinanceContractKlineBackfill(store, http_get=current_http).run(
        InstrumentId("BTCUSDT"), wider_range
    )

    assert second.completed is False
    assert second.objects[0].status is BinanceContractKlineStatus.CHECKSUM_NOT_FOUND
    assert second.objects[0].artifact_path is None
    assert [url for url, _ in current_http.calls] == [narrow_plan.checksum_url]
    assert store.read_request(narrow_plan.request).frame.height == 24 * 60
