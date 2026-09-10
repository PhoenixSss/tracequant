import hashlib
import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tracequant.data import (
    ArchiveHttpResponse,
    BinanceArchiveEvidenceStatus,
    BinanceArchiveObjectBoundary,
    BinanceKlineRestCoverage,
    BinanceKlineRestCoverageStatus,
    BinanceKlineRestHttpResponse,
    BinancePriceIndexId,
    BinancePublicHistoryAcquisition,
    BinancePublicHistoryAcquisitionBudget,
    BinancePublicHistoryAcquisitionRequest,
    BinancePublicHistoryArchiveEvidence,
    BinancePublicHistoryCoverage,
    BinancePublicHistoryDataType,
    BinancePublicHistoryPurpose,
    BinancePublicHistoryRunStatus,
    BinanceRestEndpoint,
    RawStore,
)
from tracequant.domain import InstrumentId, TimeRange

NOW = datetime(2026, 9, 9, 19, 0, tzinfo=UTC)
REST_START = datetime(2026, 9, 9, 17, 33, tzinfo=UTC)
REST_END = datetime(2026, 9, 9, 17, 35, tzinfo=UTC)
EVIDENCE_VERSION = "issue-295-probe-run-2026-09-09T18:33:30.868474Z"
EVIDENCE_REFERENCE = (
    "docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json"
)
EVIDENCE_SHA256 = "30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45"


def _contract_row(open_time: int, value: str = "100.0") -> list[object]:
    return [
        open_time,
        value,
        value,
        value,
        value,
        "1.0",
        open_time + 59_999,
        "100.0",
        1,
        "0.5",
        "50.0",
        "0",
    ]


def _daily_archive(day: date) -> tuple[bytes, bytes]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    rows = [
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore"
    ]
    for minute in range(24 * 60):
        rows.append(
            ",".join(
                str(value)
                for value in _contract_row(
                    int((start + timedelta(minutes=minute)).timestamp() * 1000)
                )
            )
        )
    suffix = day.isoformat()
    member = f"BTCUSDT-1m-{suffix}.csv"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, "\n".join(rows) + "\n")
    payload = buffer.getvalue()
    checksum = (
        f"{hashlib.sha256(payload).hexdigest()}  BTCUSDT-1m-{suffix}.zip\n".encode()
    )
    return payload, checksum


def _rest_coverage() -> BinanceKlineRestCoverage:
    observed_range = TimeRange(
        start=REST_START,
        end=datetime(2026, 9, 9, 18, 33, tzinfo=UTC),
    )
    return BinanceKlineRestCoverage(
        status=BinanceKlineRestCoverageStatus.SUPPORTED,
        endpoint=BinanceRestEndpoint.CONTRACT_KLINES,
        subject=InstrumentId("BTCUSDT"),
        allowed_range=observed_range,
        evidence_version=EVIDENCE_VERSION,
        evidence_reference=EVIDENCE_REFERENCE,
        evidence_sha256=EVIDENCE_SHA256,
        observed_at=datetime(2026, 9, 9, 18, 33, 31, 368694, tzinfo=UTC),
        normalized_params={
            "symbol": "BTCUSDT",
            "interval": "1m",
            "startTime": 1_788_975_180_000,
            "endTime": 1_788_978_779_999,
            "limit": 60,
        },
        response_sha256=(
            "a253a934a1badc71edda32c22ab9204e8aee257109f7dc9473662b1c55a0c976"
        ),
        actual_range=observed_range,
    )


def _budget() -> BinancePublicHistoryAcquisitionBudget:
    return BinancePublicHistoryAcquisitionBudget(
        timeout_seconds=5,
        maximum_archive_objects=4,
        maximum_rest_pages=8,
        maximum_attempts_per_object=2,
        maximum_attempts_per_page=2,
        maximum_http_requests=20,
        maximum_elapsed_seconds=60,
        maximum_response_bytes=4_000_000,
        maximum_download_bytes=16_000_000,
    )


def test_history_plan_and_run_produce_traceable_mixed_source_result(
    tmp_path: Path,
) -> None:
    archive_payload, checksum_payload = _daily_archive(date(2026, 8, 29))
    archive_calls: list[str] = []
    rest_calls: list[str] = []

    def archive_get(url: str, timeout: float) -> ArchiveHttpResponse:
        assert 0 < timeout <= 5
        archive_calls.append(url)
        return ArchiveHttpResponse(
            status=200,
            body=checksum_payload if url.endswith(".CHECKSUM") else archive_payload,
            headers={"content-type": "application/octet-stream"},
        )

    def rest_get(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        assert 0 < timeout <= 5
        assert maximum_response_bytes <= 4_000_000
        rest_calls.append(url)
        start_ms = int(REST_START.timestamp() * 1000)
        body = json.dumps(
            [_contract_row(start_ms), _contract_row(start_ms + 60_000)]
        ).encode()
        return BinanceKlineRestHttpResponse(status=200, body=body, headers={})

    root = tmp_path / "history"
    requests = (
        BinancePublicHistoryAcquisitionRequest(
            subject=InstrumentId("BTCUSDT"),
            data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
            request_range=TimeRange(
                start=datetime(2026, 8, 29, tzinfo=UTC),
                end=datetime(2026, 8, 30, tzinfo=UTC),
            ),
            purpose=BinancePublicHistoryPurpose.BACKFILL,
            output_root=root,
        ),
        BinancePublicHistoryAcquisitionRequest(
            subject=InstrumentId("BTCUSDT"),
            data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
            request_range=TimeRange(start=REST_START, end=REST_END),
            purpose=BinancePublicHistoryPurpose.RECENT,
            output_root=root,
        ),
    )
    archive_evidence = BinancePublicHistoryArchiveEvidence(
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        subject=InstrumentId("BTCUSDT"),
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="issue-279-probe-run-2026-09-09T10:58:43.717234Z",
        evidence_reference=("docs/research/binance-usdm-feature11-window-probes.json"),
        evidence_sha256="c" * 64,
        observed_at=datetime(2026, 9, 9, 10, 59, tzinfo=UTC),
        object_sha256=hashlib.sha256(archive_payload).hexdigest(),
        actual_range=requests[0].request_range,
    )
    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=archive_get,
        rest_http_get=rest_get,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
    )

    plan = acquisition.plan(
        requests,
        BinancePublicHistoryCoverage(
            archive_objects=(archive_evidence,), rest_windows=(_rest_coverage(),)
        ),
        _budget(),
    )

    assert not root.exists()
    assert plan.archive_objects_planned == 1
    assert all(
        obligation.initial_unmet_reason is None for obligation in plan.obligations
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.COMPLETED
    assert result.completed
    assert result.http_requests_used == 3
    assert result.archive_objects_used == 1
    assert result.rest_pages_used == 1
    assert len(result.requests[0].raw_references) == 1
    assert len(result.requests[1].raw_references) == 1
    assert result.requests[0].raw_references[0].source_kind.value == "archive_daily"
    assert result.requests[1].raw_references[0].source_kind.value == "rest"
    assert all(
        reference.revision.revision_id
        for item in result.requests
        for reference in item.raw_references
    )
    store = RawStore(root)
    for item in result.requests:
        for reference in item.raw_references:
            assert (
                store.read_revision(reference.object_identity, reference.revision).path
                == reference.artifact_path
            )

    repeated = acquisition.run(plan)
    assert repeated.completed
    assert [
        reference.revision.revision_id
        for item in repeated.requests
        for reference in item.raw_references
    ] == [
        reference.revision.revision_id
        for item in result.requests
        for reference in item.raw_references
    ]
    assert len(archive_calls) == 4
    assert len(rest_calls) == 2


def test_plan_keeps_unknown_ranges_unmet_without_io_or_directory_creation(
    tmp_path: Path,
) -> None:
    calls = 0

    def forbidden_archive(url: str, timeout: float) -> ArchiveHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError((url, timeout))

    acquisition = BinancePublicHistoryAcquisition(archive_http_get=forbidden_archive)
    root = tmp_path / "unknown"
    request = BinancePublicHistoryAcquisitionRequest(
        subject=BinancePriceIndexId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.INDEX_PRICE_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.GAP,
        gap_reason="explicit missing 1m bars",
        output_root=root,
    )

    plan = acquisition.plan((request,), BinancePublicHistoryCoverage(), _budget())

    assert not root.exists()
    assert plan.obligations[0].initial_unmet_reason == "rest_boundary_unknown"
    result = acquisition.run(plan)
    assert result.status is BinancePublicHistoryRunStatus.FAILED
    assert result.requests[0].unmet_ranges == (request.request_range,)
    assert calls == 0
    assert not root.exists()


def test_plan_keeps_four_family_subjects_and_archive_cadence_distinct(
    tmp_path: Path,
) -> None:
    day_range = TimeRange(
        start=datetime(2026, 8, 29, tzinfo=UTC),
        end=datetime(2026, 8, 30, tzinfo=UTC),
    )
    month_range = TimeRange(
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 8, 1, tzinfo=UTC),
    )
    subjects = (
        (
            BinancePublicHistoryDataType.CONTRACT_KLINE,
            InstrumentId("BTCUSDT"),
            day_range,
            BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
            "/klines/",
        ),
        (
            BinancePublicHistoryDataType.MARK_PRICE_KLINE,
            InstrumentId("ETHUSDT"),
            day_range,
            BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
            "/markPriceKlines/",
        ),
        (
            BinancePublicHistoryDataType.INDEX_PRICE_KLINE,
            BinancePriceIndexId("BTCUSDT"),
            day_range,
            BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
            "/indexPriceKlines/",
        ),
        (
            BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
            InstrumentId("ETHUSDT"),
            month_range,
            BinanceArchiveObjectBoundary.month(2026, 7),
            "/fundingRate/",
        ),
    )
    requests = tuple(
        BinancePublicHistoryAcquisitionRequest(
            subject=subject,
            data_type=data_type,
            request_range=request_range,
            purpose=BinancePublicHistoryPurpose.BACKFILL,
            output_root=tmp_path / "four-family",
        )
        for data_type, subject, request_range, _boundary, _directory in subjects
    )
    evidence = tuple(
        BinancePublicHistoryArchiveEvidence(
            data_type=data_type,
            subject=subject,
            boundary=boundary,
            status=BinanceArchiveEvidenceStatus.SUPPORTED,
            evidence_version="fixed-window-evidence",
            evidence_reference="docs/research/fixed.json",
            evidence_sha256="a" * 64,
            observed_at=NOW,
            object_sha256="b" * 64,
            actual_range=request_range,
        )
        for data_type, subject, request_range, boundary, _directory in subjects
    )

    plan = BinancePublicHistoryAcquisition().plan(
        requests,
        BinancePublicHistoryCoverage(archive_objects=evidence),
        _budget(),
    )

    assert plan.archive_objects_planned == 4
    for obligation, expected in zip(plan.obligations, subjects, strict=True):
        archive_plan = obligation.candidates[0].archive_plan
        assert archive_plan is not None
        assert expected[4] in archive_plan.url
        assert archive_plan.request.subject == expected[1]
    funding_plan = plan.obligations[-1].candidates[0].archive_plan
    assert funding_plan is not None
    assert "/monthly/" in funding_plan.url
    assert "/daily/" not in funding_plan.url


def test_archive_404_uses_only_the_planned_proven_rest_fallback(
    tmp_path: Path,
) -> None:
    archive_calls: list[str] = []
    rest_calls: list[str] = []

    def missing_archive(url: str, timeout: float) -> ArchiveHttpResponse:
        archive_calls.append(url)
        return ArchiveHttpResponse(status=404, body=b"missing", headers={})

    def rest_get(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        rest_calls.append(url)
        start_ms = int(REST_START.timestamp() * 1000)
        return BinanceKlineRestHttpResponse(
            status=200,
            body=json.dumps(
                [_contract_row(start_ms), _contract_row(start_ms + 60_000)]
            ).encode(),
            headers={},
        )

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "fallback",
    )
    unavailable = BinancePublicHistoryArchiveEvidence(
        data_type=request.data_type,
        subject=request.subject,
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 9, 9)),
        status=BinanceArchiveEvidenceStatus.NOT_FOUND,
        evidence_version="observed-missing-object",
        evidence_reference="docs/research/missing.json",
        evidence_sha256="d" * 64,
        observed_at=NOW,
    )
    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=missing_archive,
        rest_http_get=rest_get,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(
            archive_objects=(unavailable,), rest_windows=(_rest_coverage(),)
        ),
        _budget(),
    )

    result = acquisition.run(plan)

    assert result.completed
    obligation = result.requests[0].obligations[0]
    assert [source.status for source in obligation.sources] == [
        "checksum_not_found",
        "complete",
    ]
    assert len(archive_calls) == 1
    assert len(rest_calls) == 1
    assert obligation.sources[1].raw_references[0].source_kind.value == "rest"


def test_cross_source_conflict_preserves_both_exact_revisions_and_stays_incomplete(
    tmp_path: Path,
) -> None:
    day = date(2026, 9, 9)
    archive_payload, checksum_payload = _daily_archive(day)

    def archive_get(url: str, timeout: float) -> ArchiveHttpResponse:
        return ArchiveHttpResponse(
            status=200,
            body=checksum_payload if url.endswith(".CHECKSUM") else archive_payload,
            headers={},
        )

    def conflicting_rest(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        start_ms = int(REST_START.timestamp() * 1000)
        return BinanceKlineRestHttpResponse(
            status=200,
            body=json.dumps(
                [
                    _contract_row(start_ms, "999.0"),
                    _contract_row(start_ms + 60_000),
                ]
            ).encode(),
            headers={},
        )

    root = tmp_path / "conflict"
    backfill = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2026, 9, 9, tzinfo=UTC),
            end=datetime(2026, 9, 10, tzinfo=UTC),
        ),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=root,
    )
    gap = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.GAP,
        gap_reason="compare an explicitly missing range",
        output_root=root,
    )
    archive_evidence = BinancePublicHistoryArchiveEvidence(
        data_type=backfill.data_type,
        subject=backfill.subject,
        boundary=BinanceArchiveObjectBoundary.day(day),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="exact-day",
        evidence_reference="docs/research/exact-day.json",
        evidence_sha256="e" * 64,
        observed_at=NOW,
        object_sha256=hashlib.sha256(archive_payload).hexdigest(),
        actual_range=backfill.request_range,
    )
    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=archive_get,
        rest_http_get=conflicting_rest,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
    )
    plan = acquisition.plan(
        (backfill, gap),
        BinancePublicHistoryCoverage(
            archive_objects=(archive_evidence,), rest_windows=(_rest_coverage(),)
        ),
        _budget(),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.CONFLICT
    assert not result.completed
    assert len(result.requests[0].conflicts) == 1
    conflict = result.requests[0].conflicts[0]
    assert conflict.record_key == int(REST_START.timestamp() * 1000)
    assert conflict.left.revision != conflict.right.revision
    assert conflict.left.artifact_path.exists()
    assert conflict.right.artifact_path.exists()
    assert result.requests[0].duplicate_overlap_records == 1


def test_rest_retries_cannot_reset_the_shared_http_budget(tmp_path: Path) -> None:
    calls = 0

    def unavailable_rest(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        return BinanceKlineRestHttpResponse(status=503, body=b"busy", headers={})

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / "budget",
    )
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=unavailable_rest,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
    )
    budget = replace(_budget(), maximum_http_requests=1)
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        budget,
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.BUDGET_EXHAUSTED
    assert result.http_requests_used == 1
    assert calls == 1
    assert result.requests[0].raw_references == ()
