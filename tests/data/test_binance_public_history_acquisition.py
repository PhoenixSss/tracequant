import hashlib
import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import tracequant.data.binance_public_archive as archive_module
import tracequant.data.binance_public_history_acquisition as history_module
from tracequant.data import (
    ArchiveHttpResponse,
    BinanceArchiveEvidenceStatus,
    BinanceArchiveObjectBoundary,
    BinanceContractKlineBackfill,
    BinanceFundingRateBackfill,
    BinanceFundingRateRestCoverage,
    BinanceIndexPriceKlineBackfill,
    BinanceKlineRestCoverage,
    BinanceKlineRestCoverageStatus,
    BinanceKlineRestHttpResponse,
    BinanceKlineRestStatus,
    BinanceMarkPriceKlineBackfill,
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
    RawObjectIdentity,
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
FUNDING_START_MS = 1_788_393_600_000
FUNDING_END_MS = 1_788_397_200_000


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


def _family_rest_coverage(
    data_type: BinancePublicHistoryDataType,
    request_range: TimeRange,
    purpose: BinancePublicHistoryPurpose,
) -> BinanceKlineRestCoverage:
    endpoint = {
        BinancePublicHistoryDataType.CONTRACT_KLINE: BinanceRestEndpoint.CONTRACT_KLINES,
        BinancePublicHistoryDataType.MARK_PRICE_KLINE: (
            BinanceRestEndpoint.MARK_PRICE_KLINES
        ),
        BinancePublicHistoryDataType.INDEX_PRICE_KLINE: (
            BinanceRestEndpoint.INDEX_PRICE_KLINES
        ),
        BinancePublicHistoryDataType.SETTLED_FUNDING_RATE: (
            BinanceRestEndpoint.FUNDING_RATE_HISTORY
        ),
    }[data_type]
    subject = (
        BinancePriceIndexId("BTCUSDT")
        if data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE
        else InstrumentId("BTCUSDT")
    )
    if data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE:
        recent = purpose is BinancePublicHistoryPurpose.RECENT
        return BinanceFundingRateRestCoverage(
            status=BinanceKlineRestCoverageStatus.SUPPORTED,
            endpoint=endpoint,
            subject=subject,
            allowed_range=request_range,
            evidence_version=EVIDENCE_VERSION,
            evidence_reference=EVIDENCE_REFERENCE,
            evidence_sha256=EVIDENCE_SHA256,
            observed_at=(
                datetime(2026, 9, 9, 18, 33, 33, 964227, tzinfo=UTC)
                if recent
                else datetime(2026, 9, 9, 18, 33, 39, 998536, tzinfo=UTC)
            ),
            normalized_params=(
                {
                    "symbol": "BTCUSDT",
                    "startTime": 1_788_374_012_658,
                    "endTime": 1_788_978_812_657,
                    "limit": 1000,
                }
                if recent
                else {
                    "symbol": "BTCUSDT",
                    "startTime": FUNDING_START_MS,
                    "endTime": FUNDING_END_MS - 1,
                    "limit": 100,
                }
            ),
            response_sha256=(
                "2bd472efeb5b2d6336fda340914d77770151589cb3a14ff419e1ed149ef41f71"
                if recent
                else "39ac0b55d6acae3de0e0be633edbfabdc37a227fa913027fe2e63c547abda401"
            ),
            actual_range=(
                TimeRange(
                    start=datetime.fromtimestamp(FUNDING_START_MS / 1000, tz=UTC),
                    end=datetime.fromtimestamp(1_788_969_600_003 / 1000, tz=UTC),
                )
                if recent
                else TimeRange(
                    start=datetime.fromtimestamp(FUNDING_START_MS / 1000, tz=UTC),
                    end=datetime.fromtimestamp((FUNDING_START_MS + 1) / 1000, tz=UTC),
                )
            ),
        )

    observed = {
        BinancePublicHistoryDataType.CONTRACT_KLINE: (
            datetime(2026, 9, 9, 18, 33, 31, 368694, tzinfo=UTC),
            "a253a934a1badc71edda32c22ab9204e8aee257109f7dc9473662b1c55a0c976",
        ),
        BinancePublicHistoryDataType.MARK_PRICE_KLINE: (
            datetime(2026, 9, 9, 18, 33, 32, 207139, tzinfo=UTC),
            "32c27367498032aa538e19bb06d90812c2a15a3766c935f2ceb9d61858f6957c",
        ),
        BinancePublicHistoryDataType.INDEX_PRICE_KLINE: (
            datetime(2026, 9, 9, 18, 33, 32, 805179, tzinfo=UTC),
            "e66727e9d38c6959847fe5efb1dd602d4aa31262c95df041a09117e7ff5a1515",
        ),
    }[data_type]
    observed_range = TimeRange(
        start=REST_START, end=datetime(2026, 9, 9, 18, 33, tzinfo=UTC)
    )
    subject_parameter = (
        "pair"
        if data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE
        else "symbol"
    )
    return BinanceKlineRestCoverage(
        status=BinanceKlineRestCoverageStatus.SUPPORTED,
        endpoint=endpoint,
        subject=subject,
        allowed_range=request_range,
        evidence_version=EVIDENCE_VERSION,
        evidence_reference=EVIDENCE_REFERENCE,
        evidence_sha256=EVIDENCE_SHA256,
        observed_at=observed[0],
        normalized_params={
            subject_parameter: "BTCUSDT",
            "interval": "1m",
            "startTime": 1_788_975_180_000,
            "endTime": 1_788_978_779_999,
            "limit": 60,
        },
        response_sha256=observed[1],
        actual_range=observed_range,
    )


def _family_rest_body(data_type: BinancePublicHistoryDataType) -> bytes:
    if data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE:
        return json.dumps(
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FUNDING_START_MS,
                    "fundingRate": "0.00010000",
                    "markPrice": "70000.00000000",
                    "rateType": "Regular",
                }
            ]
        ).encode()
    start_ms = int(REST_START.timestamp() * 1000)
    return json.dumps(
        [_contract_row(start_ms), _contract_row(start_ms + 60_000)]
    ).encode()


def _archive_fixture(
    data_type: BinancePublicHistoryDataType,
    boundary: BinanceArchiveObjectBoundary,
) -> tuple[bytes, TimeRange, TimeRange]:
    start = datetime.combine(boundary.period_start, datetime.min.time(), tzinfo=UTC)
    if data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE:
        end = datetime(2026, 8, 1, tzinfo=UTC)
        rows = ["calc_time,funding_interval_hours,last_funding_rate"]
        cursor = start
        while cursor < end:
            rows.append(f"{int(cursor.timestamp() * 1000)},8,0.00010000")
            cursor += timedelta(hours=8)
        member = "BTCUSDT-fundingRate-2026-07.csv"
        actual_range = TimeRange(
            start=start, end=cursor - timedelta(hours=8) + timedelta(milliseconds=1)
        )
    else:
        end = start + timedelta(days=1)
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
        member = "BTCUSDT-1m-2026-08-29.csv"
        actual_range = TimeRange(start=start, end=end)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, "\n".join(rows) + "\n")
    return buffer.getvalue(), TimeRange(start=start, end=end), actual_range


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


def _approve_archive_evidence(
    monkeypatch: pytest.MonkeyPatch,
    *items: BinancePublicHistoryArchiveEvidence,
) -> None:
    """Bind synthetic archive bytes to exact cells for adapter-level tests only."""
    memberships = set(history_module._APPROVED_ARCHIVE_EVIDENCE_MEMBERSHIPS)
    memberships.update(
        history_module._archive_evidence_membership(item) for item in items
    )
    monkeypatch.setattr(
        history_module,
        "_APPROVED_ARCHIVE_EVIDENCE_MEMBERSHIPS",
        frozenset(memberships),
    )


def test_archive_evidence_must_match_an_exact_approved_report_cell() -> None:
    approved = BinancePublicHistoryArchiveEvidence(
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        subject=InstrumentId("BTCUSDT"),
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="issue-279-probe-run-2026-09-09T10:58:43.717234Z",
        evidence_reference="docs/research/binance-usdm-feature11-window-probes.json",
        evidence_sha256=(
            "c4080de0dff862cebd1ad3363c8048c67805316a1700c4ff9e2b1621eaeb64d6"
        ),
        observed_at=datetime(2026, 9, 9, 10, 58, 50, 895074, tzinfo=UTC),
        object_sha256=(
            "41e554b2a312bfadb74e4865c5cbef8dd401586c7d973c623d0915869eb81ebc"
        ),
        actual_range=TimeRange(
            start=datetime(2026, 8, 29, tzinfo=UTC),
            end=datetime(2026, 8, 30, tzinfo=UTC),
        ),
    )

    assert BinancePublicHistoryCoverage(archive_objects=(approved,)).archive_objects
    copied_to_unobserved_day = replace(
        approved,
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 8, 30)),
        status=BinanceArchiveEvidenceStatus.NOT_FOUND,
        object_sha256=None,
        actual_range=None,
    )
    with pytest.raises(ValueError, match="exact approved #279 report cell"):
        BinancePublicHistoryCoverage(archive_objects=(copied_to_unobserved_day,))


def test_all_approved_archive_report_cells_are_bound() -> None:
    manifest_path = Path("docs/research/binance-usdm-feature11-window-probes.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    family = {
        item.value: item
        for item in (
            BinancePublicHistoryDataType.CONTRACT_KLINE,
            BinancePublicHistoryDataType.MARK_PRICE_KLINE,
            BinancePublicHistoryDataType.INDEX_PRICE_KLINE,
            BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
        )
    }
    evidence: list[BinancePublicHistoryArchiveEvidence] = []
    for probe in manifest["archive_probes"]:
        data_type = family[probe["family"]]
        start = datetime.fromisoformat(
            probe["requested_window"].split(",", 1)[0].removeprefix("[")
        )
        actual_start = datetime.fromtimestamp(probe["first_timestamp_ms"] / 1000, UTC)
        actual_end = datetime.fromtimestamp(
            (
                probe["last_timestamp_ms"]
                + (
                    1
                    if data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
                    else 60_000
                )
            )
            / 1000,
            UTC,
        )
        evidence.append(
            BinancePublicHistoryArchiveEvidence(
                data_type=data_type,
                subject=(
                    BinancePriceIndexId(probe["subject"])
                    if data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE
                    else InstrumentId(probe["subject"])
                ),
                boundary=(
                    BinanceArchiveObjectBoundary.month(start.year, start.month)
                    if "/monthly/" in probe["object_key"]
                    else BinanceArchiveObjectBoundary.day(start.date())
                ),
                status=BinanceArchiveEvidenceStatus.SUPPORTED,
                evidence_version=manifest["artifact_binding"]["observation_set_id"],
                evidence_reference=str(manifest_path),
                evidence_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                observed_at=datetime.fromisoformat(probe["zip_request"]["observed_at"]),
                object_sha256=probe["zip_sha256"],
                actual_range=TimeRange(start=actual_start, end=actual_end),
            )
        )

    coverage = BinancePublicHistoryCoverage(archive_objects=tuple(evidence))

    assert len(coverage.archive_objects) == 14


def test_archive_adapters_do_not_expose_caller_supplied_plan_execution() -> None:
    for adapter in (
        BinanceContractKlineBackfill,
        BinanceMarkPriceKlineBackfill,
        BinanceIndexPriceKlineBackfill,
        BinanceFundingRateBackfill,
    ):
        assert not hasattr(adapter, "run_plan")


def test_history_plan_and_run_produce_traceable_mixed_source_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    _approve_archive_evidence(monkeypatch, archive_evidence)

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


@pytest.mark.parametrize(
    ("coverage_status", "source_status"),
    [
        (
            BinanceKlineRestCoverageStatus.UNKNOWN,
            BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
        ),
        (
            BinanceKlineRestCoverageStatus.UNSUPPORTED,
            BinanceKlineRestStatus.UNSUPPORTED,
        ),
    ],
)
def test_plan_retains_matching_negative_rest_evidence_without_io(
    tmp_path: Path,
    coverage_status: BinanceKlineRestCoverageStatus,
    source_status: BinanceKlineRestStatus,
) -> None:
    calls = 0

    def forbidden_rest(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError((url, timeout, maximum_response_bytes))

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / coverage_status.value,
    )
    coverage = replace(_rest_coverage(), status=coverage_status)
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=forbidden_rest, clock=lambda: NOW
    )

    plan = acquisition.plan(
        (request,), BinancePublicHistoryCoverage(rest_windows=(coverage,)), _budget()
    )
    result = acquisition.run(plan)

    assert plan.obligations[0].initial_unmet_reason is None
    assert plan.obligations[0].candidates[0].rest_coverage is coverage
    assert result.status is BinancePublicHistoryRunStatus.FAILED
    source = result.requests[0].obligations[0].sources[0]
    assert source.status == source_status.value
    assert source.step.rest_coverage is coverage
    assert calls == 0


def test_supported_rest_evidence_for_another_subject_stays_unknown(
    tmp_path: Path,
) -> None:
    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / "mismatched-rest-subject",
    )
    coverage = replace(_rest_coverage(), subject=InstrumentId("ETHUSDT"))

    plan = BinancePublicHistoryAcquisition().plan(
        (request,), BinancePublicHistoryCoverage(rest_windows=(coverage,)), _budget()
    )

    assert plan.obligations[0].candidates == ()
    assert plan.obligations[0].initial_unmet_reason == "rest_boundary_unknown"


def test_plan_keeps_four_family_subjects_and_archive_cadence_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    _approve_archive_evidence(monkeypatch, *evidence)

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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    _approve_archive_evidence(monkeypatch, unavailable)
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


def test_missing_monthly_archive_uses_all_proven_daily_fallbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    month_start = date(2026, 2, 1)
    month_end = date(2026, 3, 1)
    request_range = TimeRange(
        start=datetime(2026, 2, 1, tzinfo=UTC),
        end=datetime(2026, 3, 1, tzinfo=UTC),
    )
    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=request_range,
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "monthly-daily-fallback",
    )
    monthly = BinancePublicHistoryArchiveEvidence(
        data_type=request.data_type,
        subject=request.subject,
        boundary=BinanceArchiveObjectBoundary.month(2026, 2),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="monthly-fallback-fixture",
        evidence_reference="tests/data/monthly-fallback-fixture",
        evidence_sha256="a" * 64,
        observed_at=NOW,
        object_sha256="b" * 64,
        actual_range=request_range,
    )
    payloads: dict[date, tuple[bytes, bytes]] = {}
    daily_evidence: list[BinancePublicHistoryArchiveEvidence] = []
    cursor = month_start
    while cursor < month_end:
        payload, checksum = _daily_archive(cursor)
        payloads[cursor] = (payload, checksum)
        daily_evidence.append(
            BinancePublicHistoryArchiveEvidence(
                data_type=request.data_type,
                subject=request.subject,
                boundary=BinanceArchiveObjectBoundary.day(cursor),
                status=BinanceArchiveEvidenceStatus.SUPPORTED,
                evidence_version="monthly-fallback-fixture",
                evidence_reference="tests/data/monthly-fallback-fixture",
                evidence_sha256="a" * 64,
                observed_at=NOW,
                object_sha256=hashlib.sha256(payload).hexdigest(),
                actual_range=TimeRange(
                    start=datetime.combine(cursor, datetime.min.time(), tzinfo=UTC),
                    end=datetime.combine(
                        cursor + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                    ),
                ),
            )
        )
        cursor += timedelta(days=1)
    _approve_archive_evidence(monkeypatch, monthly, *daily_evidence)
    calls: list[str] = []

    def archive_get(url: str, timeout: float) -> ArchiveHttpResponse:
        calls.append(url)
        if "/monthly/" in url:
            return ArchiveHttpResponse(status=404, body=b"missing", headers={})
        filename = url.removesuffix(".CHECKSUM").rsplit("/", 1)[-1]
        day = date.fromisoformat(
            filename.removeprefix("BTCUSDT-1m-").removesuffix(".zip")
        )
        payload, checksum = payloads[day]
        return ArchiveHttpResponse(
            status=200,
            body=checksum if url.endswith(".CHECKSUM") else payload,
            headers={},
        )

    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=archive_get,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(
            archive_objects=(monthly, *daily_evidence),
        ),
        replace(
            _budget(),
            maximum_archive_objects=29,
            maximum_http_requests=60,
        ),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.COMPLETED
    assert plan.archive_objects_planned == 29
    assert result.archive_objects_used == 29
    assert len(result.requests[0].raw_references) == 28
    assert len(calls) == 57
    assert sum("/monthly/" in url for url in calls) == 1
    assert all(
        [source.status for source in obligation.sources]
        == ["checksum_not_found", "published"]
        for obligation in result.requests[0].obligations
    )


def test_cross_source_conflict_preserves_both_exact_revisions_and_stays_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    _approve_archive_evidence(monkeypatch, archive_evidence)
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


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("object_key", "data/futures/um/daily/klines/ETHUSDT/1m/other.zip"),
        ("url", "https://example.invalid/other.zip"),
        ("checksum_url", "https://example.invalid/other.zip.CHECKSUM"),
        ("member_name", "other.csv"),
    ],
)
def test_run_rejects_tampered_archive_locator_or_framing_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: str,
) -> None:
    calls = 0

    def forbidden_archive(url: str, timeout: float) -> ArchiveHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError((url, timeout))

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2026, 8, 29, tzinfo=UTC),
            end=datetime(2026, 8, 30, tzinfo=UTC),
        ),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "tampered",
    )
    evidence = BinancePublicHistoryArchiveEvidence(
        data_type=request.data_type,
        subject=request.subject,
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="exact-day",
        evidence_reference="docs/research/exact-day.json",
        evidence_sha256="a" * 64,
        observed_at=NOW,
        object_sha256="b" * 64,
        actual_range=request.request_range,
    )
    _approve_archive_evidence(monkeypatch, evidence)
    acquisition = BinancePublicHistoryAcquisition(archive_http_get=forbidden_archive)
    plan = acquisition.plan(
        (request,), BinancePublicHistoryCoverage(archive_objects=(evidence,)), _budget()
    )
    step = plan.obligations[0].candidates[0]
    assert step.archive_plan is not None
    if field == "object_key":
        archive_plan = replace(step.archive_plan, object_key=replacement)
    elif field == "url":
        archive_plan = replace(step.archive_plan, url=replacement)
    elif field == "checksum_url":
        archive_plan = replace(step.archive_plan, checksum_url=replacement)
    else:
        assert field == "member_name"
        archive_plan = replace(step.archive_plan, member_name=replacement)
    obligation = replace(
        plan.obligations[0], candidates=(replace(step, archive_plan=archive_plan),)
    )

    with pytest.raises(ValueError, match="controlled plan"):
        acquisition.run(replace(plan, obligations=(obligation,)))

    assert calls == 0
    assert not request.output_root.exists()


def test_run_rejects_tampered_step_coverage_before_io(tmp_path: Path) -> None:
    calls = 0

    def forbidden_rest(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError((url, timeout, maximum_response_bytes))

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / "tampered-rest",
    )
    acquisition = BinancePublicHistoryAcquisition(rest_http_get=forbidden_rest)
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        _budget(),
    )
    step = plan.obligations[0].candidates[0]
    assert step.rest_coverage is not None
    obligation = replace(
        plan.obligations[0],
        candidates=(
            replace(
                step,
                rest_coverage=replace(
                    step.rest_coverage, evidence_version="different-evidence"
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="controlled plan"):
        acquisition.run(replace(plan, obligations=(obligation,)))

    assert calls == 0
    assert not request.output_root.exists()


def test_large_backfill_is_rejected_before_source_windows_are_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2000, 1, 1, tzinfo=UTC),
            end=datetime(9999, 1, 1, tzinfo=UTC),
        ),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "bounded-plan",
    )

    def forbidden_expansion(*args: object, **kwargs: object) -> object:
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(history_module, "_plan_request", forbidden_expansion)

    with pytest.raises(ValueError, match="source windows"):
        BinancePublicHistoryAcquisition().plan(
            (request,),
            BinancePublicHistoryCoverage(),
            replace(_budget(), maximum_archive_objects=1),
        )


def test_changed_upstream_rerun_compares_all_preserved_revisions(
    tmp_path: Path,
) -> None:
    calls = 0

    def changing_rest(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        value = "100.0" if calls == 1 else "999.0"
        start_ms = int(REST_START.timestamp() * 1000)
        return BinanceKlineRestHttpResponse(
            status=200,
            body=json.dumps(
                [_contract_row(start_ms, value), _contract_row(start_ms + 60_000)]
            ).encode(),
            headers={},
        )

    root = tmp_path / "same-source-conflict"
    recent = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=root,
    )
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=changing_rest,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
    )
    plan = acquisition.plan(
        (recent,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        _budget(),
    )

    first = acquisition.run(plan)
    result = acquisition.run(plan)

    assert first.status is BinancePublicHistoryRunStatus.COMPLETED
    assert result.status is BinancePublicHistoryRunStatus.CONFLICT
    assert len(result.requests[0].conflicts) == 1
    conflict = result.requests[0].conflicts[0]
    assert conflict.left.source_kind is conflict.right.source_kind
    assert conflict.left.revision != conflict.right.revision
    assert conflict.left.artifact_path.exists()
    assert conflict.right.artifact_path.exists()


@pytest.mark.parametrize(
    "data_type",
    [
        BinancePublicHistoryDataType.CONTRACT_KLINE,
        BinancePublicHistoryDataType.MARK_PRICE_KLINE,
        BinancePublicHistoryDataType.INDEX_PRICE_KLINE,
        BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
    ],
)
def test_all_four_families_execute_backfill_through_real_archive_adapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data_type: BinancePublicHistoryDataType,
) -> None:
    funding = data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
    boundary = (
        BinanceArchiveObjectBoundary.month(2026, 7)
        if funding
        else BinanceArchiveObjectBoundary.day(date(2026, 8, 29))
    )
    archive_payload, request_range, actual_range = _archive_fixture(data_type, boundary)
    subject = (
        BinancePriceIndexId("BTCUSDT")
        if data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE
        else InstrumentId("BTCUSDT")
    )
    request = BinancePublicHistoryAcquisitionRequest(
        subject=subject,
        data_type=data_type,
        request_range=request_range,
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / data_type.value,
    )
    evidence = BinancePublicHistoryArchiveEvidence(
        data_type=data_type,
        subject=subject,
        boundary=boundary,
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="four-family-archive-fixture",
        evidence_reference="tests/data/four-family-archive-fixture",
        evidence_sha256="a" * 64,
        observed_at=NOW,
        object_sha256=hashlib.sha256(archive_payload).hexdigest(),
        actual_range=actual_range,
    )

    def archive_get(url: str, timeout: float) -> ArchiveHttpResponse:
        filename = url.removesuffix(".CHECKSUM").rsplit("/", 1)[-1]
        checksum = f"{hashlib.sha256(archive_payload).hexdigest()}  {filename}\n"
        return ArchiveHttpResponse(
            status=200,
            body=checksum.encode() if url.endswith(".CHECKSUM") else archive_payload,
            headers={},
        )

    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=archive_get, clock=lambda: NOW
    )
    _approve_archive_evidence(monkeypatch, evidence)
    plan = acquisition.plan(
        (request,), BinancePublicHistoryCoverage(archive_objects=(evidence,)), _budget()
    )

    result = acquisition.run(plan)

    assert result.completed
    reference = result.requests[0].raw_references[0]
    assert reference.data_type is data_type
    assert reference.actual_record_range == actual_range
    assert (
        RawStore(request.output_root)
        .read_revision(reference.object_identity, reference.revision)
        .path
        == reference.artifact_path
    )


@pytest.mark.parametrize(
    ("data_type", "purpose"),
    [
        (data_type, purpose)
        for data_type in (
            BinancePublicHistoryDataType.CONTRACT_KLINE,
            BinancePublicHistoryDataType.MARK_PRICE_KLINE,
            BinancePublicHistoryDataType.INDEX_PRICE_KLINE,
            BinancePublicHistoryDataType.SETTLED_FUNDING_RATE,
        )
        for purpose in (
            BinancePublicHistoryPurpose.RECENT,
            BinancePublicHistoryPurpose.GAP,
        )
    ],
)
def test_all_four_families_execute_recent_and_explicit_gap_through_rest(
    tmp_path: Path,
    data_type: BinancePublicHistoryDataType,
    purpose: BinancePublicHistoryPurpose,
) -> None:
    funding = data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
    request_range = (
        TimeRange(
            start=datetime.fromtimestamp(FUNDING_START_MS / 1000, tz=UTC),
            end=datetime.fromtimestamp(FUNDING_END_MS / 1000, tz=UTC),
        )
        if funding
        else TimeRange(start=REST_START, end=REST_END)
    )
    subject = (
        BinancePriceIndexId("BTCUSDT")
        if data_type is BinancePublicHistoryDataType.INDEX_PRICE_KLINE
        else InstrumentId("BTCUSDT")
    )
    request = BinancePublicHistoryAcquisitionRequest(
        subject=subject,
        data_type=data_type,
        request_range=request_range,
        purpose=purpose,
        gap_reason=(
            "explicit family gap"
            if purpose is BinancePublicHistoryPurpose.GAP
            else None
        ),
        output_root=tmp_path / f"{data_type.value}-{purpose.value}",
    )

    def rest_get(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        return BinanceKlineRestHttpResponse(
            status=200, body=_family_rest_body(data_type), headers={}
        )

    coverage = _family_rest_coverage(data_type, request_range, purpose)
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=rest_get,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,), BinancePublicHistoryCoverage(rest_windows=(coverage,)), _budget()
    )

    result = acquisition.run(plan)

    if funding:
        assert result.status is BinancePublicHistoryRunStatus.PARTIAL
        assert not result.completed
        assert result.requests[0].satisfied_ranges == ()
        assert result.requests[0].unmet_ranges == (request_range,)
    else:
        assert result.completed
    source = result.requests[0].obligations[0].sources[0]
    assert source.status == BinanceKlineRestStatus.COMPLETE.value
    assert len(source.rest_pages) == 1
    assert source.rest_pages[0].revision == source.raw_references[0].revision
    assert source.rest_pages[0].observed_at is not None
    assert purpose.value in source.step.reason


def test_partial_multi_page_rest_failure_keeps_every_page_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_rest_step = history_module._rest_step

    def one_record_pages(
        request: BinancePublicHistoryAcquisitionRequest,
        required: TimeRange,
        evidence: BinanceKlineRestCoverage,
        *,
        reason: str,
    ) -> history_module.BinancePublicHistorySourceStep:
        step = original_rest_step(request, required, evidence, reason=reason)
        assert step.rest_request is not None
        return replace(step, rest_request=replace(step.rest_request, limit=1))

    monkeypatch.setattr(history_module, "_rest_step", one_record_pages)
    calls = 0

    def second_page_is_malformed(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return BinanceKlineRestHttpResponse(
                status=200,
                body=json.dumps(
                    [_contract_row(int(REST_START.timestamp() * 1000))]
                ).encode(),
                headers={},
            )
        return BinanceKlineRestHttpResponse(
            status=200, body=b"{malformed-json", headers={}
        )

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / "partial-pages",
    )
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=second_page_is_malformed,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        _budget(),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.PARTIAL
    source = result.requests[0].obligations[0].sources[0]
    assert source.status == BinanceKlineRestStatus.INVALID_RESPONSE.value
    assert source.pages_used == 2
    assert len(source.rest_pages) == 2
    assert [page.status for page in source.rest_pages] == [
        BinanceKlineRestStatus.COMPLETE,
        BinanceKlineRestStatus.INVALID_RESPONSE,
    ]
    assert source.rest_pages[0].revision == source.raw_references[0].revision
    assert source.rest_pages[1].revision is None
    assert (
        source.rest_pages[1].response_sha256
        == hashlib.sha256(b"{malformed-json").hexdigest()
    )
    assert source.rest_pages[1].attempts[0].outcome == "received"
    assert "JSON" in source.rest_pages[1].detail


def test_shared_http_exhaustion_keeps_completed_rest_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_rest_step = history_module._rest_step

    def one_record_pages(
        request: BinancePublicHistoryAcquisitionRequest,
        required: TimeRange,
        evidence: BinanceKlineRestCoverage,
        *,
        reason: str,
    ) -> history_module.BinancePublicHistorySourceStep:
        step = original_rest_step(request, required, evidence, reason=reason)
        assert step.rest_request is not None
        return replace(step, rest_request=replace(step.rest_request, limit=1))

    monkeypatch.setattr(history_module, "_rest_step", one_record_pages)
    calls = 0

    def first_page_then_unavailable(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return BinanceKlineRestHttpResponse(
                status=200,
                body=json.dumps(
                    [_contract_row(int(REST_START.timestamp() * 1000))]
                ).encode(),
                headers={},
            )
        return BinanceKlineRestHttpResponse(status=503, body=b"busy", headers={})

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / "shared-http-pages",
    )
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=first_page_then_unavailable,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        replace(_budget(), maximum_http_requests=2),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.BUDGET_EXHAUSTED
    assert result.http_requests_used == 2
    assert calls == 2
    source = result.requests[0].obligations[0].sources[0]
    assert source.status == BinanceKlineRestStatus.BUDGET_EXHAUSTED.value
    assert len(source.rest_pages) == 2
    assert source.rest_pages[0].status is BinanceKlineRestStatus.COMPLETE
    assert source.rest_pages[0].revision == source.raw_references[0].revision
    assert source.rest_pages[1].status is BinanceKlineRestStatus.BUDGET_EXHAUSTED
    assert source.rest_pages[1].attempts[-1].outcome == "budget_exhausted"


def test_rest_page_exhaustion_propagates_to_shared_run_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_rest_step = history_module._rest_step

    def one_record_pages(
        request: BinancePublicHistoryAcquisitionRequest,
        required: TimeRange,
        evidence: BinanceKlineRestCoverage,
        *,
        reason: str,
    ) -> history_module.BinancePublicHistorySourceStep:
        step = original_rest_step(request, required, evidence, reason=reason)
        assert step.rest_request is not None
        return replace(step, rest_request=replace(step.rest_request, limit=1))

    monkeypatch.setattr(history_module, "_rest_step", one_record_pages)

    def one_page(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        return BinanceKlineRestHttpResponse(
            status=200,
            body=json.dumps(
                [_contract_row(int(REST_START.timestamp() * 1000))]
            ).encode(),
            headers={},
        )

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / "rest-page-budget",
    )
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=one_page,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        replace(_budget(), maximum_rest_pages=1),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.BUDGET_EXHAUSTED
    assert result.rest_pages_used == 1
    source = result.requests[0].obligations[0].sources[0]
    assert source.status == BinanceKlineRestStatus.BUDGET_EXHAUSTED.value
    assert source.raw_references[0].revision == source.rest_pages[0].revision
    assert source.detail == "page or total elapsed-time budget exhausted"


def test_plan_selection_is_bounded_by_month_edges_partial_and_exact_rest_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = InstrumentId("BTCUSDT")
    july = BinanceArchiveObjectBoundary.month(2026, 7)
    full_month = TimeRange(
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 8, 1, tzinfo=UTC),
    )
    monthly = BinancePublicHistoryArchiveEvidence(
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        subject=subject,
        boundary=july,
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="closed-month",
        evidence_reference="docs/research/closed-month.json",
        evidence_sha256="a" * 64,
        observed_at=NOW,
        object_sha256="b" * 64,
        actual_range=full_month,
    )
    acquisition = BinancePublicHistoryAcquisition()
    _approve_archive_evidence(monkeypatch, monthly)
    full_request = BinancePublicHistoryAcquisitionRequest(
        subject=subject,
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=full_month,
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "full-month",
    )
    full_plan = acquisition.plan(
        (full_request,),
        BinancePublicHistoryCoverage(archive_objects=(monthly,)),
        _budget(),
    )
    assert len(full_plan.obligations) == 31
    assert all(
        obligation.candidates[0].source_kind.value == "archive_monthly"
        for obligation in full_plan.obligations
    )
    assert full_plan.archive_objects_planned == 1

    edge_request = replace(
        full_request,
        request_range=TimeRange(
            start=datetime(2026, 7, 15, tzinfo=UTC),
            end=datetime(2026, 8, 2, tzinfo=UTC),
        ),
        output_root=tmp_path / "month-edges",
    )
    edge_daily = BinancePublicHistoryArchiveEvidence(
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        subject=subject,
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 7, 15)),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="closed-day-edge",
        evidence_reference="docs/research/closed-day-edge.json",
        evidence_sha256="d" * 64,
        observed_at=NOW,
        object_sha256="e" * 64,
    )
    _approve_archive_evidence(monkeypatch, edge_daily)
    edge_plan = acquisition.plan(
        (edge_request,),
        BinancePublicHistoryCoverage(archive_objects=(monthly, edge_daily)),
        replace(_budget(), maximum_archive_objects=20),
    )
    assert len(edge_plan.obligations) == 18
    assert edge_plan.obligations[0].candidates[0].source_kind.value == "archive_daily"
    assert all(
        step.source_kind.value != "archive_monthly"
        for obligation in edge_plan.obligations
        for step in obligation.candidates
    )

    partial = BinancePublicHistoryArchiveEvidence(
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        subject=subject,
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 9, 9)),
        status=BinanceArchiveEvidenceStatus.PARTIAL,
        evidence_version="partial-day",
        evidence_reference="docs/research/partial-day.json",
        evidence_sha256="c" * 64,
        observed_at=NOW,
    )
    _approve_archive_evidence(monkeypatch, partial)
    recent_range = TimeRange(start=REST_START, end=REST_END)
    backfill = replace(
        full_request,
        request_range=recent_range,
        output_root=tmp_path / "partial",
    )
    partial_plan = acquisition.plan(
        (backfill,),
        BinancePublicHistoryCoverage(
            archive_objects=(partial,), rest_windows=(_rest_coverage(),)
        ),
        _budget(),
    )
    assert [
        step.source_kind.value for step in partial_plan.obligations[0].candidates
    ] == ["rest"]

    mark_request = replace(
        backfill,
        data_type=BinancePublicHistoryDataType.MARK_PRICE_KLINE,
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / "wrong-rest-cell",
    )
    wrong_cell_plan = acquisition.plan(
        (mark_request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        _budget(),
    )
    assert wrong_cell_plan.obligations[0].candidates == ()
    assert wrong_cell_plan.obligations[0].initial_unmet_reason == (
        "rest_boundary_unknown"
    )


@pytest.mark.parametrize("failure", ["checksum", "schema"])
def test_archive_integrity_failure_does_not_use_planned_rest_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    day = date(2026, 9, 9)
    archive_payload, checksum_payload = _daily_archive(day)
    if failure == "checksum":
        checksum_payload = f"{'0' * 64}  BTCUSDT-1m-{day.isoformat()}.zip\n".encode()
    else:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                f"BTCUSDT-1m-{day.isoformat()}.csv", "open_time\nnot-an-integer\n"
            )
        archive_payload = buffer.getvalue()
        checksum_payload = (
            f"{hashlib.sha256(archive_payload).hexdigest()}  "
            f"BTCUSDT-1m-{day.isoformat()}.zip\n"
        ).encode()

    def archive_get(url: str, timeout: float) -> ArchiveHttpResponse:
        return ArchiveHttpResponse(
            status=200,
            body=checksum_payload if url.endswith(".CHECKSUM") else archive_payload,
            headers={},
        )

    rest_calls = 0

    def forbidden_fallback(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal rest_calls
        rest_calls += 1
        raise AssertionError((url, timeout, maximum_response_bytes))

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / failure,
    )
    evidence = BinancePublicHistoryArchiveEvidence(
        data_type=request.data_type,
        subject=request.subject,
        boundary=BinanceArchiveObjectBoundary.day(day),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="integrity-fixture",
        evidence_reference="tests/data/integrity-fixture",
        evidence_sha256="d" * 64,
        observed_at=NOW,
        object_sha256=hashlib.sha256(archive_payload).hexdigest(),
    )
    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=archive_get,
        rest_http_get=forbidden_fallback,
        clock=lambda: NOW,
    )
    _approve_archive_evidence(monkeypatch, evidence)
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(
            archive_objects=(evidence,), rest_windows=(_rest_coverage(),)
        ),
        _budget(),
    )

    result = acquisition.run(plan)

    source = result.requests[0].obligations[0].sources
    assert result.status is BinancePublicHistoryRunStatus.FAILED
    assert [item.status for item in source] == ["invalid_content"]
    assert rest_calls == 0


def test_existing_local_corruption_fails_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 8, 29)
    archive_payload, checksum_payload = _daily_archive(day)

    def archive_get(url: str, timeout: float) -> ArchiveHttpResponse:
        return ArchiveHttpResponse(
            status=200,
            body=checksum_payload if url.endswith(".CHECKSUM") else archive_payload,
            headers={},
        )

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2026, 8, 29, tzinfo=UTC),
            end=datetime(2026, 8, 30, tzinfo=UTC),
        ),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "local-corruption",
    )
    evidence = BinancePublicHistoryArchiveEvidence(
        data_type=request.data_type,
        subject=request.subject,
        boundary=BinanceArchiveObjectBoundary.day(day),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="local-corruption-fixture",
        evidence_reference="tests/data/local-corruption-fixture",
        evidence_sha256="e" * 64,
        observed_at=NOW,
        object_sha256=hashlib.sha256(archive_payload).hexdigest(),
        actual_range=request.request_range,
    )
    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=archive_get, clock=lambda: NOW
    )
    _approve_archive_evidence(monkeypatch, evidence)
    plan = acquisition.plan(
        (request,), BinancePublicHistoryCoverage(archive_objects=(evidence,)), _budget()
    )
    first = acquisition.run(plan)
    artifact_path = first.requests[0].raw_references[0].artifact_path
    data_path = artifact_path / "data.parquet"
    data_path.write_bytes(b"locally-corrupted")

    repeated = acquisition.run(plan)

    assert repeated.status is BinancePublicHistoryRunStatus.FAILED
    assert repeated.requests[0].obligations[0].sources[0].status == "local_failure"
    assert data_path.read_bytes() == b"locally-corrupted"


@pytest.mark.parametrize(
    ("budget_field", "expected_reason"),
    [
        ("maximum_response_bytes", "maximum_response_bytes exceeded"),
        ("maximum_download_bytes", "maximum_download_bytes exhausted"),
    ],
)
def test_shared_byte_exhaustion_preserves_response_evidence_without_retry(
    tmp_path: Path, budget_field: str, expected_reason: str
) -> None:
    calls = 0

    def oversized(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        return BinanceKlineRestHttpResponse(status=503, body=b"123456", headers={})

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / budget_field,
    )
    budget_values = {
        "maximum_response_bytes": 5,
        "maximum_download_bytes": 5,
        "maximum_attempts_per_page": 3,
    }
    if budget_field == "maximum_response_bytes":
        budget_values["maximum_download_bytes"] = 100
    else:
        budget_values["maximum_response_bytes"] = 100
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=oversized,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        replace(_budget(), **budget_values),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.BUDGET_EXHAUSTED
    assert result.termination_reason == expected_reason
    assert result.downloaded_bytes == 6
    assert calls == 1
    page = result.requests[0].obligations[0].sources[0].rest_pages[0]
    assert page.attempts[0].outcome == "response_too_large"
    assert page.attempts[0].http_status == 503
    assert page.attempts[0].response_sha256 == hashlib.sha256(b"123456").hexdigest()


def test_archive_byte_exhaustion_preserves_received_checksum_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def oversized_checksum(url: str, timeout: float) -> ArchiveHttpResponse:
        nonlocal calls
        calls += 1
        return ArchiveHttpResponse(status=200, body=b"123456", headers={})

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2026, 8, 29, tzinfo=UTC),
            end=datetime(2026, 8, 30, tzinfo=UTC),
        ),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "archive-byte-budget",
    )
    evidence = BinancePublicHistoryArchiveEvidence(
        data_type=request.data_type,
        subject=request.subject,
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="archive-budget-fixture",
        evidence_reference="tests/data/archive-budget-fixture",
        evidence_sha256="a" * 64,
        observed_at=NOW,
        object_sha256="b" * 64,
        actual_range=request.request_range,
    )
    _approve_archive_evidence(monkeypatch, evidence)
    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=oversized_checksum,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(archive_objects=(evidence,)),
        replace(
            _budget(),
            maximum_response_bytes=5,
            maximum_download_bytes=100,
        ),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.BUDGET_EXHAUSTED
    assert result.downloaded_bytes == 5
    assert calls == 1
    source = result.requests[0].obligations[0].sources[0]
    assert source.status == "budget_exhausted"
    identity = source.step.archive_plan
    assert identity is not None
    manifests = RawStore(request.output_root).list_acquisition_manifests(
        RawObjectIdentity.from_request(identity.request)
    )
    assert len(manifests) == 1
    assert manifests[0].checksum_http_status == 200
    assert manifests[0].checksum_response_sha256 == hashlib.sha256(b"12345").hexdigest()


def test_default_archive_transport_caps_the_stream_while_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_limits: list[int] = []

    class Response:
        status = 200
        headers = {"Content-Length": "6"}

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            read_limits.append(limit)
            return b"123456"[:limit]

    monkeypatch.setattr(
        "tracequant.data.binance_public_archive.urllib.request.urlopen",
        lambda request, timeout: Response(),
    )

    response = archive_module._default_http_get(
        "https://example.invalid/archive.zip",
        5.0,
        maximum_response_bytes=5,
    )

    assert read_limits == [5]
    assert response.body == b"12345"
    assert not response.complete


def test_archive_cumulative_budget_caps_each_production_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, checksum = _daily_archive(date(2026, 8, 29))
    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2026, 8, 29, tzinfo=UTC),
            end=datetime(2026, 8, 30, tzinfo=UTC),
        ),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "archive-cumulative-budget",
    )
    evidence = BinancePublicHistoryArchiveEvidence(
        data_type=request.data_type,
        subject=request.subject,
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="archive-cumulative-budget-fixture",
        evidence_reference="tests/data/archive-cumulative-budget-fixture",
        evidence_sha256="a" * 64,
        observed_at=NOW,
        object_sha256=hashlib.sha256(payload).hexdigest(),
        actual_range=request.request_range,
    )
    maximum_download_bytes = len(checksum) + 5
    response_limits: list[int] = []

    def bounded_default(
        url: str, timeout: float, *, maximum_response_bytes: int | None = None
    ) -> ArchiveHttpResponse:
        assert maximum_response_bytes is not None
        response_limits.append(maximum_response_bytes)
        body = checksum if url.endswith(".CHECKSUM") else payload
        return ArchiveHttpResponse(
            status=200,
            body=body[:maximum_response_bytes],
            headers={},
            complete=len(body) <= maximum_response_bytes,
        )

    monkeypatch.setattr(history_module, "_default_archive_http_get", bounded_default)
    _approve_archive_evidence(monkeypatch, evidence)
    acquisition = BinancePublicHistoryAcquisition(
        clock=lambda: NOW, monotonic_clock=lambda: 0.0
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(archive_objects=(evidence,)),
        replace(
            _budget(),
            maximum_response_bytes=maximum_download_bytes,
            maximum_download_bytes=maximum_download_bytes,
        ),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.BUDGET_EXHAUSTED
    assert result.downloaded_bytes == maximum_download_bytes
    assert response_limits == [maximum_download_bytes, 5]
    assert result.requests[0].raw_references == ()


def test_archive_final_response_cannot_publish_after_elapsed_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock_value = 0.0

    def late_checksum(url: str, timeout: float) -> ArchiveHttpResponse:
        nonlocal clock_value
        clock_value = 6.0
        return ArchiveHttpResponse(status=200, body=b"late", headers={})

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(
            start=datetime(2026, 8, 29, tzinfo=UTC),
            end=datetime(2026, 8, 30, tzinfo=UTC),
        ),
        purpose=BinancePublicHistoryPurpose.BACKFILL,
        output_root=tmp_path / "archive-elapsed-budget",
    )
    evidence = BinancePublicHistoryArchiveEvidence(
        data_type=request.data_type,
        subject=request.subject,
        boundary=BinanceArchiveObjectBoundary.day(date(2026, 8, 29)),
        status=BinanceArchiveEvidenceStatus.SUPPORTED,
        evidence_version="archive-elapsed-budget-fixture",
        evidence_reference="tests/data/archive-elapsed-budget-fixture",
        evidence_sha256="a" * 64,
        observed_at=NOW,
        object_sha256="b" * 64,
        actual_range=request.request_range,
    )
    _approve_archive_evidence(monkeypatch, evidence)
    acquisition = BinancePublicHistoryAcquisition(
        archive_http_get=late_checksum,
        clock=lambda: NOW,
        monotonic_clock=lambda: clock_value,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(archive_objects=(evidence,)),
        replace(_budget(), maximum_elapsed_seconds=5),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.BUDGET_EXHAUSTED
    assert result.termination_reason == "maximum_elapsed_seconds exhausted"
    assert result.requests[0].raw_references == ()


def test_rest_timeout_exhaustion_preserves_bounded_page_attempts(
    tmp_path: Path,
) -> None:
    calls = 0

    def timeout(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        raise TimeoutError("bounded fixture timeout")

    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.RECENT,
        output_root=tmp_path / "timeout",
    )
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=timeout,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        replace(_budget(), maximum_attempts_per_page=2),
    )

    result = acquisition.run(plan)

    assert result.status is BinancePublicHistoryRunStatus.FAILED
    source = result.requests[0].obligations[0].sources[0]
    assert source.status == BinanceKlineRestStatus.RETRY_EXHAUSTED.value
    assert calls == 2
    assert len(source.rest_pages) == 1
    assert [attempt.outcome for attempt in source.rest_pages[0].attempts] == [
        "transport_error",
        "transport_error",
    ]
    assert source.rest_pages[0].revision is None


def test_rest_empty_remains_distinct_from_completed_coverage(tmp_path: Path) -> None:
    request = BinancePublicHistoryAcquisitionRequest(
        subject=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        request_range=TimeRange(start=REST_START, end=REST_END),
        purpose=BinancePublicHistoryPurpose.GAP,
        gap_reason="verify explicit empty semantics",
        output_root=tmp_path / "empty",
    )
    acquisition = BinancePublicHistoryAcquisition(
        rest_http_get=lambda _url, _timeout, _maximum: BinanceKlineRestHttpResponse(
            status=200, body=b"[]", headers={}
        ),
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    )
    plan = acquisition.plan(
        (request,),
        BinancePublicHistoryCoverage(rest_windows=(_rest_coverage(),)),
        _budget(),
    )

    result = acquisition.run(plan)

    source = result.requests[0].obligations[0].sources[0]
    assert result.status is BinancePublicHistoryRunStatus.FAILED
    assert source.status == BinanceKlineRestStatus.LEGAL_EMPTY.value
    assert source.rest_pages[0].status is BinanceKlineRestStatus.LEGAL_EMPTY
    assert result.requests[0].satisfied_ranges == ()
    assert result.requests[0].unmet_ranges == (request.request_range,)
