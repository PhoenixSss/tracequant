import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tracequant.data import (
    BinanceKlineInterval,
    BinanceKlineRestAcquisition,
    BinanceKlineRestBudget,
    BinanceKlineRestCoverage,
    BinanceKlineRestCoverageStatus,
    BinanceKlineRestHttpGet,
    BinanceKlineRestHttpResponse,
    BinanceKlineRestStatus,
    BinancePriceIndexId,
    BinanceRestEndpoint,
    BinanceRestPageProvenance,
    BinanceRestPageRequest,
    RawArtifactIncompleteError,
    RawArtifactNotFoundError,
    RawArtifactValidationError,
    RawObjectIdentity,
    RawStore,
)
from tracequant.domain import InstrumentId, TimeRange

START = datetime(2026, 9, 9, 17, 33, tzinfo=UTC)
NOW = datetime(2026, 9, 9, 19, 0, tzinfo=UTC)
EVIDENCE_VERSION = "issue-295-probe-run-2026-09-09T18:33:30.868474Z"
EVIDENCE_REFERENCE = (
    "docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json"
)
EVIDENCE_SHA256 = "30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45"


def _subject(
    endpoint: BinanceRestEndpoint, value: str = "BTCUSDT"
) -> InstrumentId | BinancePriceIndexId:
    if endpoint is BinanceRestEndpoint.INDEX_PRICE_KLINES:
        return BinancePriceIndexId(value)
    return InstrumentId(value)


def _request(
    endpoint: BinanceRestEndpoint = BinanceRestEndpoint.CONTRACT_KLINES,
    *,
    subject_value: str = "BTCUSDT",
    start: datetime = START,
    minutes: int = 3,
    limit: int = 2,
) -> BinanceRestPageRequest:
    return BinanceRestPageRequest(
        endpoint=endpoint,
        subject=_subject(endpoint, subject_value),
        caller_range=TimeRange(start=start, end=start + timedelta(minutes=minutes)),
        interval=BinanceKlineInterval.ONE_MINUTE,
        limit=limit,
    )


def _coverage(
    request: BinanceRestPageRequest,
    *,
    status: BinanceKlineRestCoverageStatus = BinanceKlineRestCoverageStatus.SUPPORTED,
    allowed_range: TimeRange | None = None,
) -> BinanceKlineRestCoverage:
    return BinanceKlineRestCoverage(
        status=status,
        endpoint=request.endpoint,
        subject=request.subject,
        allowed_range=allowed_range
        or TimeRange(
            start=START,
            end=datetime(2026, 9, 9, 18, 33, tzinfo=UTC),
        ),
        evidence_version=EVIDENCE_VERSION,
        evidence_reference=EVIDENCE_REFERENCE,
        evidence_sha256=EVIDENCE_SHA256,
        observed_at=datetime(2026, 9, 9, 18, 33, 40, tzinfo=UTC),
    )


def _budget(
    *,
    pages: int = 8,
    attempts: int = 3,
    elapsed: float = 30.0,
    response_bytes: int = 1_000_000,
) -> BinanceKlineRestBudget:
    return BinanceKlineRestBudget(
        timeout_seconds=5.0,
        maximum_pages=pages,
        maximum_attempts_per_page=attempts,
        maximum_elapsed_seconds=elapsed,
        maximum_response_bytes=response_bytes,
    )


def _row(
    open_time: int,
    endpoint: BinanceRestEndpoint,
    *,
    variant: int = 0,
) -> list[object]:
    prices = [
        f"{100 + variant}.00000000",
        f"{102 + variant}.00000000",
        f"{99 + variant}.00000000",
        f"{101 + variant}.00000000",
    ]
    if endpoint is BinanceRestEndpoint.CONTRACT_KLINES:
        return [
            open_time,
            *prices,
            "12.50000000",
            open_time + 59_999,
            "1262.50000000",
            7,
            "6.00000000",
            "606.00000000",
            "0",
        ]
    return [
        open_time,
        *prices,
        "0",
        open_time + 59_999,
        "0",
        60,
        "0",
        "0",
        "0",
    ]


def _body_for_url(url: str, *, variant: int = 0) -> bytes:
    split = urlsplit(url)
    params = parse_qs(split.query)
    start = int(params["startTime"][0])
    end_exclusive = int(params["endTime"][0]) + 1
    limit = int(params["limit"][0])
    endpoint = BinanceRestEndpoint(split.path)
    count = min(limit, (end_exclusive - start) // 60_000)
    return json.dumps(
        [
            _row(start + index * 60_000, endpoint, variant=variant)
            for index in range(count)
        ],
        separators=(",", ":"),
    ).encode()


class _DynamicTransport:
    def __init__(self, *, variant: int = 0) -> None:
        self.variant = variant
        self.calls: list[tuple[str, float, int]] = []

    def __call__(
        self, url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        self.calls.append((url, timeout, maximum_response_bytes))
        return BinanceKlineRestHttpResponse(
            status=200,
            body=_body_for_url(url, variant=self.variant),
            headers={"Content-Type": "application/json", "X-MBX-USED-WEIGHT-1M": "1"},
        )


class _QueueTransport:
    def __init__(
        self,
        responses: list[BinanceKlineRestHttpResponse | BaseException],
    ) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(
        self, url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _acquisition(
    root: Path,
    transport: BinanceKlineRestHttpGet,
    *,
    wait: Callable[[float], None] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
) -> tuple[RawStore, BinanceKlineRestAcquisition]:
    store = RawStore(root, clock=lambda: NOW)
    return store, BinanceKlineRestAcquisition(
        store,
        http_get=transport,
        clock=lambda: NOW,
        wait=wait,
        monotonic_clock=monotonic_clock,
    )


def test_kline_rest_run_publishes_verified_page_raw(tmp_path: Path) -> None:
    endpoints = (
        BinanceRestEndpoint.CONTRACT_KLINES,
        BinanceRestEndpoint.MARK_PRICE_KLINES,
        BinanceRestEndpoint.INDEX_PRICE_KLINES,
    )
    for endpoint in endpoints:
        for subject_value in ("BTCUSDT", "ETHUSDT"):
            case_root = tmp_path / endpoint.name / subject_value
            transport = _DynamicTransport()
            store, acquisition = _acquisition(case_root, transport)
            request = _request(
                endpoint,
                subject_value=subject_value,
                minutes=3,
                limit=2,
            )

            result = acquisition.run(request, _coverage(request), _budget())

            assert result.status is BinanceKlineRestStatus.COMPLETE
            assert result.complete
            assert result.unmet_range is None
            assert result.actual_record_range == request.caller_range
            assert result.pages_used == 2
            assert result.attempts_used == 2
            assert [page.record_count for page in result.pages] == [2, 1]
            assert [page.short_page for page in result.pages] == [False, True]
            assert len(transport.calls) == 2
            first_query = parse_qs(urlsplit(transport.calls[0][0]).query)
            expected_subject_param = (
                "pair"
                if endpoint is BinanceRestEndpoint.INDEX_PRICE_KLINES
                else "symbol"
            )
            assert first_query[expected_subject_param] == [subject_value]
            assert first_query["interval"] == ["1m"]
            assert first_query["endTime"] == ["1788975359999"]

            for page in result.pages:
                assert page.revision is not None
                artifact = store.read_exact_revision(
                    page.revision.logical_identity, page.revision
                )
                exact_body = _body_for_url(
                    next(
                        call[0]
                        for call in transport.calls
                        if parse_qs(urlsplit(call[0]).query)["startTime"][0]
                        == str(page.request.page_boundary.start_time_ms)  # type: ignore[union-attr]
                    )
                )
                assert artifact.response_path.read_bytes() == exact_body
                assert not artifact.archive_path.exists()
                assert not artifact.checksum_response_path.exists()
                assert page.response_sha256 == hashlib.sha256(exact_body).hexdigest()
                assert artifact.manifest.manifest_schema_version == 5
                assert isinstance(
                    artifact.manifest.rest_provenance, BinanceRestPageProvenance
                )

            first_revision = result.pages[0].revision
            assert first_revision is not None
            first_artifact = store.read_exact_revision(
                first_revision.logical_identity, first_revision
            )
            provenance = first_artifact.manifest.rest_provenance
            assert isinstance(provenance, BinanceRestPageProvenance)
            assert (
                provenance.request.normalized_params
                == result.pages[0].request.normalized_params
            )
            assert provenance.response_sha256 == result.pages[0].response_sha256
            if endpoint is BinanceRestEndpoint.CONTRACT_KLINES:
                assert first_artifact.frame["volume"].to_list() == [
                    "12.50000000",
                    "12.50000000",
                ]
                assert first_artifact.frame["trade_count"].to_list() == [7, 7]
            else:
                assert "volume" not in first_artifact.frame.columns
                assert "trade_count" not in first_artifact.frame.columns
                assert first_artifact.frame["ignore_8"].to_list() == [60, 60]

            repeated = acquisition.run(request, _coverage(request), _budget())
            assert repeated.status is BinanceKlineRestStatus.COMPLETE
            assert [page.revision for page in repeated.pages] == [
                page.revision for page in result.pages
            ]
            persisted = store.read_exact_revision(
                first_revision.logical_identity, first_revision
            )
            assert persisted.manifest == first_artifact.manifest


@pytest.mark.parametrize(
    ("coverage_factory", "expected_status", "detail"),
    [
        (lambda request: None, BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN, "missing"),
        (
            lambda request: _coverage(
                request, status=BinanceKlineRestCoverageStatus.UNKNOWN
            ),
            BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
            "unknown",
        ),
        (
            lambda request: replace(
                _coverage(request), endpoint=BinanceRestEndpoint.MARK_PRICE_KLINES
            ),
            BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
            "does not match",
        ),
        (
            lambda request: replace(_coverage(request), evidence_sha256="0" * 64),
            BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
            "unrecognized",
        ),
        (
            lambda request: _coverage(
                request,
                allowed_range=TimeRange(
                    start=START + timedelta(minutes=1),
                    end=START + timedelta(minutes=4),
                ),
            ),
            BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
            "outside",
        ),
    ],
)
def test_coverage_is_fail_closed_before_http(
    tmp_path: Path,
    coverage_factory: Callable[
        [BinanceRestPageRequest], BinanceKlineRestCoverage | None
    ],
    expected_status: BinanceKlineRestStatus,
    detail: str,
) -> None:
    transport = _DynamicTransport()
    _, acquisition = _acquisition(tmp_path, transport)
    request = _request(minutes=3)

    result = acquisition.run(request, coverage_factory(request), _budget())

    assert result.status is expected_status
    assert detail in result.termination_reason
    assert result.unmet_range == request.caller_range
    assert transport.calls == []


def test_unaligned_unclosed_and_unsupported_requests_fail_before_http(
    tmp_path: Path,
) -> None:
    transport = _DynamicTransport()
    _, acquisition = _acquisition(tmp_path, transport)
    unaligned = _request(start=START + timedelta(milliseconds=1), minutes=1)
    unclosed = _request(start=NOW.replace(minute=0), minutes=1)
    usdc = _request(subject_value="BTCUSDC", minutes=1)

    assert acquisition.run(unaligned, _coverage(unaligned), _budget()).status is (
        BinanceKlineRestStatus.INVALID_REQUEST
    )
    assert acquisition.run(unclosed, _coverage(unclosed), _budget()).status is (
        BinanceKlineRestStatus.INVALID_REQUEST
    )
    assert acquisition.run(usdc, _coverage(usdc), _budget()).status is (
        BinanceKlineRestStatus.UNSUPPORTED
    )
    assert transport.calls == []


@pytest.mark.parametrize(
    ("body_factory", "expected_status", "detail"),
    [
        (lambda request: b"[]", BinanceKlineRestStatus.LEGAL_EMPTY, "empty"),
        (lambda request: b"{}", BinanceKlineRestStatus.INVALID_RESPONSE, "array"),
        (lambda request: b"not-json", BinanceKlineRestStatus.INVALID_RESPONSE, "JSON"),
        (
            lambda request: json.dumps(
                [_row(1_788_975_180_000, request.endpoint)[:-1]]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "12 array elements",
        ),
        (
            lambda request: json.dumps(
                [
                    [
                        *_row(1_788_975_180_000, request.endpoint)[:1],
                        "NaN",
                        *_row(1_788_975_180_000, request.endpoint)[2:],
                    ]
                ]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "finite numeric",
        ),
        (
            lambda request: json.dumps(
                [
                    _row(1_788_975_180_000, request.endpoint),
                    _row(1_788_975_180_000, request.endpoint),
                ]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "duplicate",
        ),
        (
            lambda request: json.dumps(
                [
                    _row(1_788_975_180_000, request.endpoint),
                    _row(1_788_975_180_000, request.endpoint, variant=1),
                ]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "conflicting duplicate",
        ),
        (
            lambda request: json.dumps(
                [
                    _row(1_788_975_240_000, request.endpoint),
                    _row(1_788_975_180_000, request.endpoint),
                ]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "out of order",
        ),
        (
            lambda request: json.dumps(
                [
                    _row(1_788_975_180_000, request.endpoint),
                    _row(1_788_975_300_000, request.endpoint),
                ]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "missing minute",
        ),
        (
            lambda request: json.dumps(
                [_row(1_788_975_120_000, request.endpoint)]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "outside",
        ),
        (
            lambda request: json.dumps(
                [
                    _row(1_788_975_180_000, request.endpoint),
                    _row(1_788_975_240_000, request.endpoint),
                    _row(1_788_975_300_000, request.endpoint),
                ]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "page limit",
        ),
        (
            lambda request: json.dumps(
                [
                    [
                        *_row(1_788_975_180_000, request.endpoint)[:6],
                        1_788_975_239_998,
                        *_row(1_788_975_180_000, request.endpoint)[7:],
                    ]
                ]
            ).encode(),
            BinanceKlineRestStatus.INVALID_RESPONSE,
            "complete minute",
        ),
    ],
)
def test_invalid_and_empty_pages_are_quarantined_without_raw_publication(
    tmp_path: Path,
    body_factory: Callable[[BinanceRestPageRequest], bytes],
    expected_status: BinanceKlineRestStatus,
    detail: str,
) -> None:
    request = _request(minutes=3, limit=2)
    body = body_factory(request)
    transport = _QueueTransport(
        [BinanceKlineRestHttpResponse(200, body, {"Content-Type": "application/json"})]
    )
    store, acquisition = _acquisition(tmp_path, transport)

    result = acquisition.run(request, _coverage(request), _budget())

    assert result.status is expected_status
    assert detail in result.termination_reason
    identity = RawObjectIdentity.from_rest_page_request(request)
    assert store.list_verified_revisions(identity) == ()
    records = store.list_acquisition_manifests(identity)
    assert len(records) == 1
    assert records[0].source_body_sha256 == hashlib.sha256(body).hexdigest()
    evidence_path = store.acquisition_path_for(identity) / records[0].record_id
    assert (evidence_path / "response.json").read_bytes() == body
    assert not (evidence_path / "source.zip").exists()


def test_short_page_is_preserved_but_following_empty_page_leaves_unmet_range(
    tmp_path: Path,
) -> None:
    request = _request(minutes=3, limit=2)
    first_body = json.dumps([_row(1_788_975_180_000, request.endpoint)]).encode()
    transport = _QueueTransport(
        [
            BinanceKlineRestHttpResponse(200, first_body, {}),
            BinanceKlineRestHttpResponse(200, b"[]", {}),
        ]
    )
    store, acquisition = _acquisition(tmp_path, transport)

    result = acquisition.run(request, _coverage(request), _budget())

    assert result.status is BinanceKlineRestStatus.LEGAL_EMPTY
    assert len(result.pages) == 2
    assert result.pages[0].short_page
    assert result.pages[0].artifact_path is not None
    assert result.actual_record_range == TimeRange(
        start=START, end=START + timedelta(minutes=1)
    )
    assert result.unmet_range == TimeRange(
        start=START + timedelta(minutes=1),
        end=START + timedelta(minutes=3),
    )
    first_revision = result.pages[0].revision
    assert first_revision is not None
    assert (
        store.read_exact_revision(
            first_revision.logical_identity, first_revision
        ).response_path.read_bytes()
        == first_body
    )


@pytest.mark.parametrize("variant", [0, 1])
def test_cross_page_duplicate_or_conflict_is_explicit_and_keeps_first_page(
    tmp_path: Path, variant: int
) -> None:
    request = _request(minutes=2, limit=2)
    first_body = json.dumps([_row(1_788_975_180_000, request.endpoint)]).encode()
    repeated_body = json.dumps(
        [_row(1_788_975_180_000, request.endpoint, variant=variant)]
    ).encode()
    transport = _QueueTransport(
        [
            BinanceKlineRestHttpResponse(200, first_body, {}),
            BinanceKlineRestHttpResponse(200, repeated_body, {}),
        ]
    )
    store, acquisition = _acquisition(tmp_path, transport)

    result = acquisition.run(request, _coverage(request), _budget())

    assert result.status is BinanceKlineRestStatus.INVALID_RESPONSE
    assert "cross-page" in result.termination_reason
    expected = "duplicate" if variant == 0 else "conflicting duplicate"
    assert expected in result.termination_reason
    first_revision = result.pages[0].revision
    assert first_revision is not None
    assert len(store.list_verified_revisions(first_revision.logical_identity)) == 1


@pytest.mark.parametrize(
    ("first", "expected_wait"),
    [
        (TimeoutError("timed out"), 1.0),
        (
            BinanceKlineRestHttpResponse(429, b'{"code":-1003}', {"Retry-After": "2"}),
            2.0,
        ),
        (BinanceKlineRestHttpResponse(503, b"busy", {}), 1.0),
    ],
)
def test_retryable_failures_back_off_with_the_same_page_identity(
    tmp_path: Path,
    first: BinanceKlineRestHttpResponse | BaseException,
    expected_wait: float,
) -> None:
    request = _request(minutes=1, limit=1)
    url = BinanceKlineRestAcquisition._request_url(request)
    success = BinanceKlineRestHttpResponse(200, _body_for_url(url), {})
    transport = _QueueTransport([first, success])
    now = [0.0]
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    _, acquisition = _acquisition(
        tmp_path,
        transport,
        wait=wait,
        monotonic_clock=lambda: now[0],
    )

    result = acquisition.run(request, _coverage(request), _budget())

    assert result.status is BinanceKlineRestStatus.COMPLETE
    assert transport.calls == [url, url]
    assert waits == [expected_wait]
    assert result.attempts_used == 2
    assert result.pages[0].attempts[0].page_attempt_number == 1
    assert result.pages[0].attempts[1].page_attempt_number == 2


def test_retry_after_and_page_limits_terminate_without_claiming_completion(
    tmp_path: Path,
) -> None:
    request = _request(minutes=3, limit=1)
    retry_after = _QueueTransport(
        [BinanceKlineRestHttpResponse(429, b"busy", {"Retry-After": "10"})]
    )
    _, retry_acquisition = _acquisition(
        tmp_path / "retry",
        retry_after,
        wait=lambda unused: None,
        monotonic_clock=lambda: 0.0,
    )
    retry_result = retry_acquisition.run(
        request, _coverage(request), _budget(elapsed=5)
    )

    dynamic = _DynamicTransport()
    _, page_acquisition = _acquisition(tmp_path / "page", dynamic)
    page_result = page_acquisition.run(request, _coverage(request), _budget(pages=1))

    assert retry_result.status is BinanceKlineRestStatus.BUDGET_EXHAUSTED
    assert retry_result.attempts_used == 1
    assert page_result.status is BinanceKlineRestStatus.BUDGET_EXHAUSTED
    assert len(page_result.pages) == 1
    assert page_result.pages[0].artifact_path is not None
    assert page_result.unmet_range == TimeRange(
        start=START + timedelta(minutes=1),
        end=START + timedelta(minutes=3),
    )


def test_per_page_attempt_limit_is_not_reset(tmp_path: Path) -> None:
    request = _request(minutes=1, limit=1)
    transport = _QueueTransport(
        [
            TimeoutError("first"),
            BinanceKlineRestHttpResponse(503, b"second", {}),
        ]
    )
    waits: list[float] = []
    _, acquisition = _acquisition(
        tmp_path,
        transport,
        wait=waits.append,
        monotonic_clock=lambda: 0.0,
    )

    result = acquisition.run(
        request, _coverage(request), _budget(attempts=2, elapsed=10)
    )

    assert result.status is BinanceKlineRestStatus.RETRY_EXHAUSTED
    assert result.attempts_used == 2
    assert len(transport.calls) == 2
    assert waits == [1.0]


def test_http_attempt_that_exceeds_total_elapsed_budget_is_not_published(
    tmp_path: Path,
) -> None:
    request = _request(minutes=1, limit=1)
    now = [0.0]

    def slow_transport(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        now[0] += 6.0
        return BinanceKlineRestHttpResponse(200, _body_for_url(url), {})

    store, acquisition = _acquisition(
        tmp_path,
        slow_transport,
        monotonic_clock=lambda: now[0],
    )

    result = acquisition.run(request, _coverage(request), _budget(elapsed=5.0))

    assert result.status is BinanceKlineRestStatus.BUDGET_EXHAUSTED
    identity = RawObjectIdentity.from_rest_page_request(request)
    assert store.list_verified_revisions(identity) == ()
    assert len(store.list_acquisition_manifests(identity)) == 1


def test_nonretryable_http_and_response_size_limit_are_observable(
    tmp_path: Path,
) -> None:
    request = _request(minutes=1, limit=1)
    bad_request = _QueueTransport(
        [BinanceKlineRestHttpResponse(400, b'{"code":-1100}', {})]
    )
    _, bad_acquisition = _acquisition(tmp_path / "bad", bad_request)
    bad_result = bad_acquisition.run(request, _coverage(request), _budget())

    oversized = _QueueTransport([BinanceKlineRestHttpResponse(200, b"1234567", {})])
    store, oversized_acquisition = _acquisition(tmp_path / "large", oversized)
    large_result = oversized_acquisition.run(
        request, _coverage(request), _budget(response_bytes=5)
    )

    assert bad_result.status is BinanceKlineRestStatus.INVALID_RESPONSE
    assert bad_result.attempts_used == 1
    assert large_result.status is BinanceKlineRestStatus.INVALID_RESPONSE
    identity = RawObjectIdentity.from_rest_page_request(request)
    record = store.list_acquisition_manifests(identity)[0]
    evidence_path = store.acquisition_path_for(identity) / record.record_id
    assert (evidence_path / "response.json").read_bytes() == b"123456"


def test_response_digest_creates_new_revision_without_overwriting_old_content(
    tmp_path: Path,
) -> None:
    request = _request(minutes=1, limit=1)
    store, first_acquisition = _acquisition(tmp_path, _DynamicTransport(variant=0))
    first = first_acquisition.run(request, _coverage(request), _budget())
    _, second_acquisition = _acquisition(tmp_path, _DynamicTransport(variant=5))
    second = second_acquisition.run(request, _coverage(request), _budget())

    first_revision = first.pages[0].revision
    second_revision = second.pages[0].revision
    assert first_revision is not None
    assert second_revision is not None
    assert first_revision.revision_id != second_revision.revision_id
    revisions = store.list_verified_revisions(first_revision.logical_identity)
    assert len(revisions) == 2
    assert (
        store.read_exact_revision(
            first_revision.logical_identity, first_revision
        ).frame["close"][0]
        == "101.00000000"
    )
    assert (
        store.read_exact_revision(
            second_revision.logical_identity, second_revision
        ).frame["close"][0]
        == "106.00000000"
    )


@pytest.mark.parametrize("mutation", ["missing", "corrupt"])
def test_rest_reader_rejects_missing_or_corrupt_response_evidence(
    tmp_path: Path, mutation: str
) -> None:
    request = _request(minutes=1, limit=1)
    store, acquisition = _acquisition(tmp_path, _DynamicTransport())
    result = acquisition.run(request, _coverage(request), _budget())
    revision = result.pages[0].revision
    assert revision is not None
    artifact = store.read_exact_revision(revision.logical_identity, revision)
    if mutation == "missing":
        artifact.response_path.unlink()
        expected_error: type[Exception] = RawArtifactIncompleteError
    else:
        artifact.response_path.write_bytes(
            artifact.response_path.read_bytes() + b"corrupt"
        )
        expected_error = RawArtifactValidationError

    with pytest.raises(expected_error):
        store.read_exact_revision(revision.logical_identity, revision)


def test_interrupted_raw_publish_does_not_expose_partial_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(minutes=1, limit=1)
    store, acquisition = _acquisition(tmp_path, _DynamicTransport())

    def interrupt(
        unused_store: RawStore, unused_source: Path, unused_destination: Path
    ) -> None:
        raise OSError("simulated interrupted publish")

    monkeypatch.setattr(RawStore, "_publish_directory", interrupt)
    result = acquisition.run(request, _coverage(request), _budget())

    assert result.status is BinanceKlineRestStatus.LOCAL_FAILURE
    identity = RawObjectIdentity.from_rest_page_request(request)
    assert store.list_verified_revisions(identity) == ()
    with pytest.raises(RawArtifactNotFoundError):
        store.read(identity)


def test_acquisition_construction_does_not_touch_filesystem(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    store = RawStore(root)

    BinanceKlineRestAcquisition(store, http_get=_DynamicTransport())

    assert not root.exists()
