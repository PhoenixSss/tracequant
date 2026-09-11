import hashlib
import io
import json
import threading
import time
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

from tracequant.data import (
    ArchiveHttpResponse,
    BinanceArchiveAcquisitionStatus,
    BinanceArchiveObjectPlan,
    BinanceArchiveParseResult,
    BinanceContractKlineBackfill,
    BinanceKlineInterval,
    BinanceKlineRestAcquisition,
    BinanceKlineRestBudget,
    BinanceKlineRestCoverage,
    BinanceKlineRestCoverageStatus,
    BinanceKlineRestHttpResponse,
    BinanceKlineRestStatus,
    BinancePublicArchiveAcquisition,
    BinancePublicHistoryExecutionContext,
    BinancePublicHistoryExecutionLimits,
    BinancePublicHistoryExecutionStopReason,
    BinancePublicHistoryHttpAllowance,
    BinanceRestEndpoint,
    BinanceRestPageRequest,
    RawObjectIdentity,
    RawStore,
    plan_binance_contract_kline_archives,
)
from tracequant.data import binance_public_archive as archive_module
from tracequant.domain import InstrumentId, TimeRange

_REST_START = datetime(2026, 9, 9, 17, 33, tzinfo=UTC)
_REST_END = _REST_START + timedelta(minutes=1)
_NOW = datetime(2026, 9, 9, 19, tzinfo=UTC)


def _archive(member_name: str, start_open_time: int) -> bytes:
    header = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore\n"
    )
    rows = "".join(
        f"{start_open_time + offset * 60_000},61000.0,61020.0,60990.0,"
        f"61010.0,12.5,{start_open_time + offset * 60_000 + 59_999},"
        "762500.0,42,6.0,366000.0,0\n"
        for offset in range(24 * 60)
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, (header + rows).encode())
    return output.getvalue()


def _rest_request() -> BinanceRestPageRequest:
    return BinanceRestPageRequest(
        endpoint=BinanceRestEndpoint.CONTRACT_KLINES,
        subject=InstrumentId("BTCUSDT"),
        caller_range=TimeRange(start=_REST_START, end=_REST_END),
        interval=BinanceKlineInterval.ONE_MINUTE,
        limit=1,
    )


def _rest_coverage(request: BinanceRestPageRequest) -> BinanceKlineRestCoverage:
    approved_start = datetime(2026, 9, 9, 17, 33, tzinfo=UTC)
    approved_end = datetime(2026, 9, 9, 18, 33, tzinfo=UTC)
    return BinanceKlineRestCoverage(
        status=BinanceKlineRestCoverageStatus.SUPPORTED,
        endpoint=request.endpoint,
        subject=request.subject,
        allowed_range=TimeRange(start=approved_start, end=approved_end),
        evidence_version="issue-295-probe-run-2026-09-09T18:33:30.868474Z",
        evidence_reference=(
            "docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json"
        ),
        evidence_sha256=(
            "30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45"
        ),
        observed_at=datetime(2026, 9, 9, 18, 33, 31, 368694, tzinfo=UTC),
        normalized_params={
            "symbol": "BTCUSDT",
            "interval": "1m",
            "startTime": int(approved_start.timestamp() * 1_000),
            "endTime": int(approved_end.timestamp() * 1_000) - 1,
            "limit": 60,
        },
        response_sha256=(
            "a253a934a1badc71edda32c22ab9204e8aee257109f7dc9473662b1c55a0c976"
        ),
        actual_range=TimeRange(start=approved_start, end=approved_end),
    )


def _rest_body() -> bytes:
    start_ms = int(_REST_START.timestamp() * 1_000)
    return json.dumps(
        [
            [
                start_ms,
                "100.00000000",
                "102.00000000",
                "99.00000000",
                "101.00000000",
                "12.50000000",
                start_ms + 59_999,
                "1262.50000000",
                7,
                "6.00000000",
                "606.00000000",
                "0",
            ]
        ],
        separators=(",", ":"),
    ).encode()


def _rest_budget() -> BinanceKlineRestBudget:
    return BinanceKlineRestBudget(
        timeout_seconds=5,
        maximum_pages=2,
        maximum_attempts_per_page=2,
        maximum_elapsed_seconds=30,
        maximum_response_bytes=1_000_000,
    )


def _context(
    *, total_bytes: int, http_attempts: int = 10
) -> BinancePublicHistoryExecutionContext:
    return BinancePublicHistoryExecutionContext(
        BinancePublicHistoryExecutionLimits(
            deadline_monotonic=100,
            maximum_http_attempts=http_attempts,
            maximum_total_response_bytes=total_bytes,
            maximum_response_bytes=total_bytes,
            maximum_archive_objects=2,
            maximum_rest_pages=3,
            maximum_attempts_per_request=2,
        ),
        monotonic_clock=lambda: 0,
        sleeper=lambda unused_seconds: None,
    )


def test_shared_execution_context_bounds_archive_and_rest_attempts(
    tmp_path: Path,
) -> None:
    archive_range = TimeRange(
        start=datetime(2024, 2, 29, tzinfo=UTC),
        end=datetime(2024, 2, 29, 0, 1, tzinfo=UTC),
    )
    plan = cast(
        BinanceArchiveObjectPlan,
        plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), archive_range)[0],
    )
    archive = _archive(plan.member_name, 1_709_164_800_000)
    checksum = (
        f"{hashlib.sha256(archive).hexdigest()}  {plan.url.rsplit('/', 1)[-1]}\n"
    ).encode()
    archive_calls: list[str] = []

    def archive_get(url: str, timeout: float) -> ArchiveHttpResponse:
        assert 0 < timeout <= 5
        archive_calls.append(url)
        body = checksum if url == plan.checksum_url else archive
        return ArchiveHttpResponse(200, body, {})

    rest_body = _rest_body()
    rest_calls: list[str] = []

    def rest_get(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        assert 0 < timeout <= 5
        assert maximum_response_bytes == len(rest_body)
        rest_calls.append(url)
        return BinanceKlineRestHttpResponse(200, rest_body, {}, complete=True)

    context = _context(
        total_bytes=len(checksum) + len(archive) + len(rest_body),
        http_attempts=3,
    )
    store = RawStore(tmp_path, clock=lambda: _NOW)

    archive_result = BinanceContractKlineBackfill(
        store, http_get=archive_get, timeout=5, clock=lambda: _NOW
    ).run(InstrumentId("BTCUSDT"), archive_range, context)
    request = _rest_request()
    rest_acquisition = BinanceKlineRestAcquisition(
        store,
        http_get=rest_get,
        clock=lambda: _NOW,
        monotonic_clock=lambda: 0,
    )
    rest_result = rest_acquisition.run(
        request, _rest_coverage(request), _rest_budget(), context
    )
    exhausted = rest_acquisition.run(
        request, _rest_coverage(request), _rest_budget(), context
    )

    assert archive_result.completed
    assert rest_result.complete
    assert len(rest_result.pages) == 1
    assert exhausted.status is BinanceKlineRestStatus.BUDGET_EXHAUSTED
    assert exhausted.pages[0].attempts == ()
    assert archive_calls == [plan.checksum_url, plan.url]
    assert len(rest_calls) == 1
    snapshot = context.snapshot()
    assert snapshot.http_attempts == 3
    assert snapshot.response_bytes == len(checksum) + len(archive) + len(rest_body)
    assert snapshot.archive_objects == 1
    assert snapshot.rest_pages == 2
    assert snapshot.stop_reason is (
        BinancePublicHistoryExecutionStopReason.HTTP_ATTEMPTS_EXHAUSTED
    )
    assert [attempt.attempts for attempt in snapshot.request_attempts] == [1, 1, 1]


def test_shared_context_serializes_total_byte_allowances() -> None:
    second_started = threading.Event()

    def cancelled() -> bool:
        if threading.current_thread().name == "second-attempt":
            second_started.set()
        return False

    context = BinancePublicHistoryExecutionContext(
        replace(_context(total_bytes=10, http_attempts=2).limits),
        cancelled=cancelled,
        monotonic_clock=lambda: 0,
    )
    first = context.begin_http_attempt("archive:first", timeout_seconds=5)
    second: list[BinancePublicHistoryHttpAllowance] = []

    def begin_second() -> None:
        second.append(context.begin_http_attempt("rest:second", timeout_seconds=5))

    thread = threading.Thread(target=begin_second, name="second-attempt", daemon=True)
    thread.start()
    assert second_started.wait(1)
    context.record_response_bytes(first, 4)
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(second) == 1
    assert second[0].maximum_response_bytes == 6
    context.record_response_bytes(second[0], 6)
    snapshot = context.snapshot()
    assert snapshot.http_attempts == 2
    assert snapshot.response_bytes == 10


def test_no_io_conclusion_and_pre_request_exhaustion_do_not_charge_http(
    tmp_path: Path,
) -> None:
    request = _rest_request()
    context = _context(total_bytes=100, http_attempts=1)

    def forbidden_rest(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        raise AssertionError((url, timeout, maximum_response_bytes))

    result = BinanceKlineRestAcquisition(
        RawStore(tmp_path),
        http_get=forbidden_rest,
        clock=lambda: _NOW,
        monotonic_clock=lambda: 0,
    ).run(request, None, _rest_budget(), context)

    assert result.status is BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN
    assert context.snapshot().http_attempts == 0
    assert context.snapshot().rest_pages == 0


def test_archive_http_exhaustion_preserves_received_checksum(
    tmp_path: Path,
) -> None:
    request_range = TimeRange(
        start=datetime(2024, 2, 29, tzinfo=UTC),
        end=datetime(2024, 2, 29, 0, 1, tzinfo=UTC),
    )
    plan = cast(
        BinanceArchiveObjectPlan,
        plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)[0],
    )
    checksum = f"{'0' * 64}  {plan.url.rsplit('/', 1)[-1]}\n".encode()
    calls: list[str] = []

    def checksum_only(url: str, timeout: float) -> ArchiveHttpResponse:
        del timeout
        calls.append(url)
        return ArchiveHttpResponse(200, checksum, {})

    context = _context(total_bytes=1_000, http_attempts=1)
    store = RawStore(tmp_path, clock=lambda: _NOW)
    result = BinanceContractKlineBackfill(
        store, http_get=checksum_only, clock=lambda: _NOW
    ).run(InstrumentId("BTCUSDT"), request_range, context)

    assert result.objects[0].status is BinanceArchiveAcquisitionStatus.BUDGET_EXHAUSTED
    assert calls == [plan.checksum_url]
    assert context.snapshot().http_attempts == 1
    assert context.snapshot().response_bytes == len(checksum)
    manifests = store.list_acquisition_manifests(
        RawObjectIdentity.from_request(plan.request)
    )
    assert len(manifests) == 1
    assert manifests[0].checksum_http_status == 200
    assert manifests[0].checksum_response_sha256 == hashlib.sha256(checksum).hexdigest()


def test_rest_retry_wait_cancellation_is_terminal_and_keeps_attempt(
    tmp_path: Path,
) -> None:
    request = _rest_request()
    cancellation = threading.Event()
    calls = 0

    def unavailable(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del url, timeout, maximum_response_bytes
        nonlocal calls
        calls += 1
        return BinanceKlineRestHttpResponse(503, b"retry", {})

    context = BinancePublicHistoryExecutionContext(
        BinancePublicHistoryExecutionLimits(
            deadline_monotonic=100,
            maximum_http_attempts=3,
            maximum_total_response_bytes=100,
            maximum_response_bytes=100,
            maximum_archive_objects=1,
            maximum_rest_pages=1,
            maximum_attempts_per_request=3,
        ),
        cancelled=cancellation.is_set,
        monotonic_clock=lambda: 0,
        sleeper=lambda unused_seconds: cancellation.set(),
    )
    result = BinanceKlineRestAcquisition(
        RawStore(tmp_path),
        http_get=unavailable,
        clock=lambda: _NOW,
        monotonic_clock=lambda: 0,
    ).run(request, _rest_coverage(request), _rest_budget(), context)

    assert result.status is BinanceKlineRestStatus.CANCELLED
    assert calls == 1
    assert len(result.pages) == 1
    assert [attempt.outcome for attempt in result.pages[0].attempts] == [
        "retryable_http"
    ]
    assert result.pages[0].attempts[0].waited_seconds == 0
    assert result.elapsed_seconds == 0
    snapshot = context.snapshot()
    assert snapshot.http_attempts == 1
    assert snapshot.response_bytes == len(b"retry")
    assert snapshot.stop_reason is BinancePublicHistoryExecutionStopReason.CANCELLED


@pytest.mark.parametrize("stop", ["cancelled", "deadline"])
def test_final_rest_response_stops_before_publication_and_keeps_evidence(
    tmp_path: Path,
    stop: str,
) -> None:
    request = _rest_request()
    cancellation = threading.Event()
    monotonic = 0.0
    body = _rest_body()

    def final_response(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del url, timeout, maximum_response_bytes
        nonlocal monotonic
        if stop == "cancelled":
            cancellation.set()
        else:
            monotonic = 101
        return BinanceKlineRestHttpResponse(200, body, {})

    context = BinancePublicHistoryExecutionContext(
        BinancePublicHistoryExecutionLimits(
            deadline_monotonic=100,
            maximum_http_attempts=1,
            maximum_total_response_bytes=len(body),
            maximum_response_bytes=len(body),
            maximum_archive_objects=1,
            maximum_rest_pages=1,
            maximum_attempts_per_request=1,
        ),
        cancelled=cancellation.is_set,
        monotonic_clock=lambda: monotonic,
    )
    store = RawStore(tmp_path, clock=lambda: _NOW)
    result = BinanceKlineRestAcquisition(
        store,
        http_get=final_response,
        clock=lambda: _NOW,
        monotonic_clock=lambda: monotonic,
    ).run(request, _rest_coverage(request), _rest_budget(), context)

    expected = (
        BinanceKlineRestStatus.CANCELLED
        if stop == "cancelled"
        else BinanceKlineRestStatus.BUDGET_EXHAUSTED
    )
    assert result.status is expected
    assert result.pages[0].response_sha256 == hashlib.sha256(body).hexdigest()
    assert (
        store.list_verified_revisions(RawObjectIdentity.from_rest_page_request(request))
        == ()
    )
    manifest = store.list_acquisition_manifests(
        RawObjectIdentity.from_rest_page_request(request)
    )[0]
    assert manifest.source_body_sha256 == hashlib.sha256(body).hexdigest()


def test_inflight_default_rest_cancellation_terminates_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _rest_request()
    cancellation = threading.Event()
    reached_request = threading.Event()

    class BlockingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            reached_request.set()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"[")
            self.wfile.flush()
            time.sleep(5)

        def log_message(self, unused_format: str, *unused_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def cancel_inflight() -> None:
        if reached_request.wait(2):
            cancellation.set()

    cancellation_thread = threading.Thread(target=cancel_inflight, daemon=True)
    cancellation_thread.start()
    monkeypatch.setattr(
        "tracequant.data.binance_kline_rest._BASE_URL",
        f"http://127.0.0.1:{server.server_port}",
    )
    context = BinancePublicHistoryExecutionContext(
        BinancePublicHistoryExecutionLimits(
            deadline_monotonic=time.monotonic() + 5,
            maximum_http_attempts=1,
            maximum_total_response_bytes=100,
            maximum_response_bytes=100,
            maximum_archive_objects=1,
            maximum_rest_pages=1,
            maximum_attempts_per_request=1,
        ),
        cancelled=cancellation.is_set,
    )
    started = time.monotonic()
    try:
        result = BinanceKlineRestAcquisition(
            RawStore(tmp_path), clock=lambda: _NOW
        ).run(request, _rest_coverage(request), _rest_budget(), context)
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
        cancellation_thread.join()

    assert result.status is BinanceKlineRestStatus.CANCELLED
    assert reached_request.is_set()
    assert context.snapshot().http_attempts == 1
    assert elapsed < 2


def test_inflight_default_archive_cancellation_terminates_worker_and_maps_result(
    tmp_path: Path,
) -> None:
    cancellation = threading.Event()
    reached_request = threading.Event()

    class BlockingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            reached_request.set()
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"x")
            self.wfile.flush()
            time.sleep(5)

        def log_message(self, unused_format: str, *unused_args: object) -> None:
            return None

    class UnusedAdapter:
        raw_schema_identifier = "unused"

        def parse_member(
            self, plan: BinanceArchiveObjectPlan, payload: bytes
        ) -> BinanceArchiveParseResult:
            raise AssertionError((plan, payload))

        def validate_complete_object_coverage(
            self, plan: BinanceArchiveObjectPlan, actual_range: TimeRange
        ) -> None:
            raise AssertionError((plan, actual_range))

        def validate_required_coverage(
            self, plan: BinanceArchiveObjectPlan, actual_range: TimeRange
        ) -> None:
            raise AssertionError((plan, actual_range))

    request_range = TimeRange(
        start=datetime(2024, 2, 29, tzinfo=UTC),
        end=datetime(2024, 2, 29, 0, 1, tzinfo=UTC),
    )
    planned = cast(
        BinanceArchiveObjectPlan,
        plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)[0],
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def cancel_inflight() -> None:
        if reached_request.wait(2):
            cancellation.set()

    cancellation_thread = threading.Thread(target=cancel_inflight, daemon=True)
    cancellation_thread.start()
    local_url = f"http://127.0.0.1:{server.server_port}/object"
    plan = replace(planned, url=local_url, checksum_url=f"{local_url}.CHECKSUM")
    context = BinancePublicHistoryExecutionContext(
        BinancePublicHistoryExecutionLimits(
            deadline_monotonic=time.monotonic() + 5,
            maximum_http_attempts=1,
            maximum_total_response_bytes=100,
            maximum_response_bytes=100,
            maximum_archive_objects=1,
            maximum_rest_pages=1,
            maximum_attempts_per_request=1,
        ),
        cancelled=cancellation.is_set,
    )
    started = time.monotonic()
    try:
        result = BinancePublicArchiveAcquisition(
            RawStore(tmp_path), clock=lambda: _NOW
        ).acquire(plan, UnusedAdapter(), context)
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
        cancellation_thread.join()

    assert result.status is BinanceArchiveAcquisitionStatus.CANCELLED
    assert reached_request.is_set()
    assert context.snapshot().http_attempts == 1
    assert elapsed < 2


@pytest.mark.parametrize("declared_length", [False, True])
@pytest.mark.parametrize(
    ("payload", "expected_body", "complete"),
    [
        (b"1234", b"1234", True),
        (b"12345", b"12345", True),
        (b"123456", b"123456", False),
    ],
)
def test_archive_reader_distinguishes_exact_eof_from_overflow(
    monkeypatch: pytest.MonkeyPatch,
    declared_length: bool,
    payload: bytes,
    expected_body: bytes,
    complete: bool,
) -> None:
    class Response:
        status = 200

        def __init__(self) -> None:
            self.headers = (
                {"Content-Length": str(len(payload))} if declared_length else {}
            )
            self.offset = 0

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *unused_args: object) -> None:
            return None

        def read(self, amount: int) -> bytes:
            chunk = payload[self.offset : self.offset + amount]
            self.offset += len(chunk)
            return chunk

        def read1(self, amount: int) -> bytes:
            return self.read(min(amount, 2))

    monkeypatch.setattr(
        "tracequant.data.binance_public_archive.urllib.request.urlopen",
        lambda request, timeout: Response(),
    )

    response = archive_module._urllib_http_get(
        "https://example.invalid/archive.zip",
        5,
        maximum_response_bytes=5,
    )

    assert response.body == expected_body
    assert response.complete is complete
