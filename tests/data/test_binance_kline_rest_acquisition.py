import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import polars as pl
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
    BinanceRestPageAcquisition,
    BinanceRestPageParsed,
    BinanceRestPageProvenance,
    BinanceRestPageRequest,
    RawArtifactIncompleteError,
    RawArtifactNotFoundError,
    RawArtifactValidationError,
    RawObjectIdentity,
    RawRevisionEvidenceKind,
    RawRevisionIdentity,
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
RECENT_END = datetime(2026, 9, 9, 18, 33, tzinfo=UTC)
CELL_EVIDENCE = {
    (BinanceRestEndpoint.CONTRACT_KLINES, "BTCUSDT"): (
        datetime(2026, 9, 9, 18, 33, 31, 368694, tzinfo=UTC),
        "a253a934a1badc71edda32c22ab9204e8aee257109f7dc9473662b1c55a0c976",
    ),
    (BinanceRestEndpoint.CONTRACT_KLINES, "ETHUSDT"): (
        datetime(2026, 9, 9, 18, 33, 31, 676945, tzinfo=UTC),
        "82d2a49a6751df1137c1293e969dfc2f991e0b20c296edda0e52b69d9aef7cd7",
    ),
    (BinanceRestEndpoint.MARK_PRICE_KLINES, "BTCUSDT"): (
        datetime(2026, 9, 9, 18, 33, 32, 207139, tzinfo=UTC),
        "32c27367498032aa538e19bb06d90812c2a15a3766c935f2ceb9d61858f6957c",
    ),
    (BinanceRestEndpoint.MARK_PRICE_KLINES, "ETHUSDT"): (
        datetime(2026, 9, 9, 18, 33, 32, 440732, tzinfo=UTC),
        "c7163b4f125e983fb4ede8d707a70ff8e80a39462714732a020503a1f7e15ea6",
    ),
    (BinanceRestEndpoint.INDEX_PRICE_KLINES, "BTCUSDT"): (
        datetime(2026, 9, 9, 18, 33, 32, 805179, tzinfo=UTC),
        "e66727e9d38c6959847fe5efb1dd602d4aa31262c95df041a09117e7ff5a1515",
    ),
    (BinanceRestEndpoint.INDEX_PRICE_KLINES, "ETHUSDT"): (
        datetime(2026, 9, 9, 18, 33, 33, 608919, tzinfo=UTC),
        "e87aad187eae9bf664b39f7acb468114962dc25d7daad35509d8b67e07004531",
    ),
}


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
    observed_at, response_sha256 = CELL_EVIDENCE.get(
        (request.endpoint, str(request.subject)),
        CELL_EVIDENCE[(request.endpoint, "BTCUSDT")],
    )
    subject_parameter = (
        "pair"
        if request.endpoint is BinanceRestEndpoint.INDEX_PRICE_KLINES
        else "symbol"
    )
    actual_range = TimeRange(start=START, end=RECENT_END)
    return BinanceKlineRestCoverage(
        status=status,
        endpoint=request.endpoint,
        subject=request.subject,
        allowed_range=allowed_range or actual_range,
        evidence_version=EVIDENCE_VERSION,
        evidence_reference=EVIDENCE_REFERENCE,
        evidence_sha256=EVIDENCE_SHA256,
        observed_at=observed_at,
        normalized_params={
            subject_parameter: str(request.subject),
            "interval": "1m",
            "startTime": int(START.timestamp() * 1_000),
            "endTime": int(RECENT_END.timestamp() * 1_000) - 1,
            "limit": 60,
        },
        response_sha256=response_sha256,
        actual_range=actual_range,
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


class _FundingPageAdapter:
    """Small L05-shaped adapter proving reuse without implementing L05 itself."""

    def request_failure(
        self, request: BinanceRestPageRequest, now: datetime
    ) -> tuple[BinanceKlineRestStatus, str] | None:
        assert request.endpoint is BinanceRestEndpoint.FUNDING_RATE_HISTORY
        return None

    def coverage_failure(
        self, request: BinanceRestPageRequest, coverage: object | None
    ) -> tuple[BinanceKlineRestStatus, str] | None:
        if coverage != "verified-funding-coverage":
            return (
                BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
                "funding coverage is not verified",
            )
        return None

    def parse_page(
        self,
        request: BinanceRestPageRequest,
        body: bytes,
        prior_records: Mapping[int, tuple[object, ...]],
    ) -> BinanceRestPageParsed:
        payload = json.loads(body)
        assert isinstance(payload, list)
        if not payload:
            assert request.page_boundary is not None
            return BinanceRestPageParsed(
                frame=pl.DataFrame(
                    schema={"funding_time": pl.Int64, "funding_rate": pl.String}
                ),
                actual_record_range=None,
                records={},
                next_cursor_ms=request.page_boundary.end_time_ms + 1,
                terminal=True,
            )
        rows: list[tuple[object, ...]] = []
        records: dict[int, tuple[object, ...]] = {}
        for raw in payload:
            assert isinstance(raw, dict)
            funding_time = raw["fundingTime"]
            assert isinstance(funding_time, int)
            row = (funding_time, raw["fundingRate"])
            assert funding_time not in prior_records
            rows.append(row)
            records[funding_time] = row
        first_funding_time = rows[0][0]
        last_funding_time = rows[-1][0]
        assert isinstance(first_funding_time, int)
        assert isinstance(last_funding_time, int)
        return BinanceRestPageParsed(
            frame=pl.DataFrame(
                rows, schema=["funding_time", "funding_rate"], orient="row"
            ),
            actual_record_range=TimeRange(
                start=datetime.fromtimestamp(first_funding_time / 1_000, tz=UTC),
                end=datetime.fromtimestamp((last_funding_time + 1) / 1_000, tz=UTC),
            ),
            records=records,
            next_cursor_ms=last_funding_time + 1,
        )

    def raw_schema_identifier(self, request: BinanceRestPageRequest) -> str:
        return "test.binance.funding-rate.rest-json.v1"


def _acquisition(
    root: Path,
    transport: BinanceKlineRestHttpGet,
    *,
    wait: Callable[[float], None] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[RawStore, BinanceKlineRestAcquisition]:
    store = RawStore(root, clock=lambda: NOW)
    return store, BinanceKlineRestAcquisition(
        store,
        http_get=transport,
        clock=clock or (lambda: NOW),
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
                expected_rows = [tuple(row) for row in json.loads(exact_body)]
                assert artifact.frame.rows() == expected_rows
                expected_columns = {
                    BinanceRestEndpoint.CONTRACT_KLINES: [
                        "open_time",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "close_time",
                        "quote_volume",
                        "trade_count",
                        "taker_buy_volume",
                        "taker_buy_quote_volume",
                        "ignore",
                    ],
                    BinanceRestEndpoint.MARK_PRICE_KLINES: [
                        "open_time",
                        "mark_open",
                        "mark_high",
                        "mark_low",
                        "mark_close",
                        "placeholder_volume",
                        "close_time",
                        "placeholder_quote_volume",
                        "placeholder_count",
                        "placeholder_taker_buy_volume",
                        "placeholder_taker_buy_quote_volume",
                        "placeholder_ignore",
                    ],
                    BinanceRestEndpoint.INDEX_PRICE_KLINES: [
                        "open_time",
                        "index_open",
                        "index_high",
                        "index_low",
                        "index_close",
                        "placeholder_volume",
                        "close_time",
                        "placeholder_quote_volume",
                        "placeholder_count",
                        "placeholder_taker_buy_volume",
                        "placeholder_taker_buy_quote_volume",
                        "placeholder_ignore",
                    ],
                }[endpoint]
                assert artifact.frame.columns == expected_columns

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
                assert first_artifact.frame["placeholder_count"].to_list() == [60, 60]

            repeated = acquisition.run(request, _coverage(request), _budget())
            assert repeated.status is BinanceKlineRestStatus.COMPLETE
            assert [page.revision for page in repeated.pages] == [
                page.revision for page in result.pages
            ]
            persisted = store.read_exact_revision(
                first_revision.logical_identity, first_revision
            )
            assert persisted.manifest == first_artifact.manifest


def test_shared_rest_page_executor_accepts_funding_adapter(tmp_path: Path) -> None:
    start_ms = int(START.timestamp() * 1_000)
    request = BinanceRestPageRequest(
        endpoint=BinanceRestEndpoint.FUNDING_RATE_HISTORY,
        subject=InstrumentId("BTCUSDT"),
        caller_range=TimeRange(
            start=START,
            end=START + timedelta(days=7),
        ),
        interval=None,
        limit=2,
    )

    def funding_body(url: str) -> bytes:
        start = int(parse_qs(urlsplit(url).query)["startTime"][0])
        if start > start_ms:
            return b"[]"
        return json.dumps(
            [
                {"fundingTime": start, "fundingRate": "0.00010000"},
                {
                    "fundingTime": start + 8 * 60 * 60 * 1_000,
                    "fundingRate": "0.00020000",
                },
            ],
            separators=(",", ":"),
        ).encode()

    calls = 0

    def transport(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return BinanceKlineRestHttpResponse(503, b"busy", {})
        return BinanceKlineRestHttpResponse(200, funding_body(url), {})

    store = RawStore(tmp_path, clock=lambda: NOW)
    acquisition = BinanceRestPageAcquisition(
        store,
        adapter=_FundingPageAdapter(),
        http_get=transport,
        clock=lambda: NOW,
        wait=lambda unused: None,
        monotonic_clock=lambda: 0.0,
    )

    result = acquisition.run(request, "verified-funding-coverage", _budget())

    assert result.status is BinanceKlineRestStatus.LEGAL_EMPTY
    assert result.attempts_used == 3
    assert result.unmet_range == TimeRange(
        start=START + timedelta(hours=8, milliseconds=1),
        end=request.caller_range.end,
    )
    assert len(result.pages) == 2
    assert [len(page.attempts) for page in result.pages] == [2, 1]
    assert result.pages[1].status is BinanceKlineRestStatus.LEGAL_EMPTY
    assert result.pages[1].revision is None
    revision = result.pages[0].revision
    assert revision is not None
    artifact = store.read_exact_revision(revision.logical_identity, revision)
    assert artifact.manifest.raw_schema_identifier == (
        "test.binance.funding-rate.rest-json.v1"
    )
    assert artifact.frame.rows() == [
        (start_ms, "0.00010000"),
        (start_ms + 8 * 60 * 60 * 1_000, "0.00020000"),
    ]


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
        (
            lambda request: _coverage(
                request,
                allowed_range=TimeRange(
                    start=START + timedelta(microseconds=1),
                    end=START + timedelta(minutes=4),
                ),
            ),
            BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
            "outside",
        ),
        (
            lambda request: _coverage(
                request,
                allowed_range=TimeRange(
                    start=START,
                    end=datetime(2026, 9, 9, 18, 33, tzinfo=UTC)
                    + timedelta(microseconds=1),
                ),
            ),
            BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN,
            "extends",
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


def test_coverage_requires_exact_per_window_observation_evidence(
    tmp_path: Path,
) -> None:
    request = _request(minutes=3)
    valid = _coverage(request)
    changed_params = dict(valid.normalized_params)
    changed_params["limit"] = 5
    invalid_coverages = (
        replace(valid, observed_at=valid.observed_at + timedelta(seconds=1)),
        replace(valid, normalized_params=changed_params),
        replace(valid, response_sha256="0" * 64),
        replace(
            valid,
            actual_range=TimeRange(
                start=START,
                end=START + timedelta(minutes=5),
            ),
        ),
    )

    for index, coverage in enumerate(invalid_coverages):
        transport = _DynamicTransport()
        _, acquisition = _acquisition(tmp_path / str(index), transport)

        result = acquisition.run(request, coverage, _budget())

        assert result.status is BinanceKlineRestStatus.REST_BOUNDARY_UNKNOWN
        assert "per-window" in result.termination_reason
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


@pytest.mark.parametrize("exhaust_budget", [False, True])
def test_retry_termination_keeps_recorded_attempts(
    tmp_path: Path, exhaust_budget: bool
) -> None:
    request = _request(minutes=1, limit=1)
    transport = _QueueTransport([BinanceKlineRestHttpResponse(503, b"busy", {})])
    now = [0.0]

    def wait(seconds: float) -> None:
        if exhaust_budget:
            now[0] += seconds
            return
        raise RuntimeError("wait interrupted")

    _, acquisition = _acquisition(
        tmp_path,
        transport,
        wait=wait,
        monotonic_clock=lambda: now[0],
    )

    result = acquisition.run(
        request,
        _coverage(request),
        _budget(attempts=2, elapsed=1.0),
    )

    expected = (
        BinanceKlineRestStatus.BUDGET_EXHAUSTED
        if exhaust_budget
        else BinanceKlineRestStatus.LOCAL_FAILURE
    )
    assert result.status is expected
    assert result.attempts_used == 1
    assert len(result.pages) == 1
    assert len(result.pages[0].attempts) == 1
    assert result.pages[0].attempts[0].http_status == 503
    assert (
        result.pages[0].attempts[0].response_sha256
        == hashlib.sha256(b"busy").hexdigest()
    )


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


@pytest.mark.parametrize("blocked_stage", ["open", "body"])
def test_default_http_transport_terminates_the_entire_worker_at_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked_stage: str
) -> None:
    request = _request(minutes=1, limit=1)
    reached_block = threading.Event()

    class BlockingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            reached_block.set()
            if blocked_stage == "open":
                time.sleep(5.0)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[")
            self.wfile.flush()
            time.sleep(5.0)

        def log_message(self, unused_format: str, *unused_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    monkeypatch.setattr(
        "tracequant.data.binance_kline_rest._BASE_URL",
        f"http://127.0.0.1:{server.server_port}",
    )
    store = RawStore(tmp_path, clock=lambda: NOW)
    acquisition = BinanceKlineRestAcquisition(store, clock=lambda: NOW)

    started = time.monotonic()
    try:
        result = acquisition.run(
            request,
            _coverage(request),
            replace(
                _budget(attempts=1),
                timeout_seconds=2.0,
                maximum_elapsed_seconds=2.0,
            ),
        )
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()

    assert result.status is BinanceKlineRestStatus.BUDGET_EXHAUSTED
    assert result.attempts_used == 1
    assert reached_block.is_set()
    assert elapsed < 3.0
    identity = RawObjectIdentity.from_rest_page_request(request)
    assert store.list_verified_revisions(identity) == ()
    record = store.list_acquisition_manifests(identity)[0]
    if blocked_stage == "body":
        digest = hashlib.sha256(b"[").hexdigest()
        assert result.pages[0].attempts[0].outcome == "incomplete_response"
        assert result.pages[0].attempts[0].http_status == 200
        assert result.pages[0].attempts[0].response_sha256 == digest
        assert record.source_http_status == 200
        assert record.source_body_sha256 == digest
        evidence_path = store.acquisition_path_for(identity) / record.record_id
        assert (evidence_path / "response.json").read_bytes() == b"["
    else:
        assert record.source_http_status is None
        assert record.source_body_sha256 is None


def test_default_http_transport_rejects_truncated_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(minutes=1, limit=1)
    body = json.dumps(
        [_row(request.caller_bounds.start_time_ms, request.endpoint)],
        separators=(",", ":"),
    ).encode()

    class TruncatedHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) + 10))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, unused_format: str, *unused_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), TruncatedHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    monkeypatch.setattr(
        "tracequant.data.binance_kline_rest._BASE_URL",
        f"http://127.0.0.1:{server.server_port}",
    )
    store = RawStore(tmp_path, clock=lambda: NOW)
    acquisition = BinanceKlineRestAcquisition(store, clock=lambda: NOW)

    try:
        result = acquisition.run(
            request,
            _coverage(request),
            replace(
                _budget(attempts=1),
                timeout_seconds=2.0,
                maximum_elapsed_seconds=5.0,
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()

    digest = hashlib.sha256(body).hexdigest()
    assert result.status is BinanceKlineRestStatus.RETRY_EXHAUSTED
    assert result.pages[0].attempts[0].outcome == "incomplete_response"
    assert result.pages[0].attempts[0].http_status == 200
    assert result.pages[0].attempts[0].response_sha256 == digest
    identity = RawObjectIdentity.from_rest_page_request(request)
    assert store.list_verified_revisions(identity) == ()
    record = store.list_acquisition_manifests(identity)[0]
    assert record.source_http_status == 200
    assert record.source_body_sha256 == digest
    evidence_path = store.acquisition_path_for(identity) / record.record_id
    assert (evidence_path / "response.json").read_bytes() == body


def test_deep_json_parser_failure_is_quarantined_with_received_bytes(
    tmp_path: Path,
) -> None:
    request = _request(minutes=1, limit=1)
    body = b"[" * 16_000 + b"]" * 16_000
    store, acquisition = _acquisition(
        tmp_path, _QueueTransport([BinanceKlineRestHttpResponse(200, body, {})])
    )

    result = acquisition.run(request, _coverage(request), _budget())

    assert result.status is BinanceKlineRestStatus.INVALID_RESPONSE
    assert "strict UTF-8 JSON" in result.termination_reason
    identity = RawObjectIdentity.from_rest_page_request(request)
    record = store.list_acquisition_manifests(identity)[0]
    evidence_path = store.acquisition_path_for(identity) / record.record_id
    assert record.source_body_sha256 == hashlib.sha256(body).hexdigest()
    assert (evidence_path / "response.json").read_bytes() == body
    assert store.list_verified_revisions(identity) == ()


def test_oversized_json_integer_is_an_invalid_response_with_evidence(
    tmp_path: Path,
) -> None:
    request = _request(minutes=1, limit=1)
    row = _row(request.caller_bounds.start_time_ms, request.endpoint)
    row[8] = 2**63
    body = json.dumps([row], separators=(",", ":")).encode()
    store, acquisition = _acquisition(
        tmp_path, _QueueTransport([BinanceKlineRestHttpResponse(200, body, {})])
    )

    result = acquisition.run(request, _coverage(request), _budget())

    assert result.status is BinanceKlineRestStatus.INVALID_RESPONSE
    assert "signed 64-bit" in result.termination_reason
    assert result.pages[0].response_sha256 == hashlib.sha256(body).hexdigest()
    identity = RawObjectIdentity.from_rest_page_request(request)
    record = store.list_acquisition_manifests(identity)[0]
    evidence_path = store.acquisition_path_for(identity) / record.record_id
    assert (evidence_path / "response.json").read_bytes() == body
    assert store.list_verified_revisions(identity) == ()


@pytest.mark.parametrize(
    ("transport_result", "expected_outcome", "expected_digest"),
    [
        (TimeoutError("timed out"), "transport_error", None),
        (
            BinanceKlineRestHttpResponse(503, b"busy", {}),
            "retryable_http",
            hashlib.sha256(b"busy").hexdigest(),
        ),
    ],
)
def test_evidence_write_failure_keeps_in_memory_attempt_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport_result: BinanceKlineRestHttpResponse | BaseException,
    expected_outcome: str,
    expected_digest: str | None,
) -> None:
    request = _request(minutes=1, limit=1)
    store, acquisition = _acquisition(tmp_path, _QueueTransport([transport_result]))

    def fail_evidence_write(
        unused_store: RawStore, *unused_args: object, **unused_kwargs: object
    ) -> None:
        raise OSError("simulated evidence disk failure")

    monkeypatch.setattr(RawStore, "write_acquisition_manifest", fail_evidence_write)

    result = acquisition.run(request, _coverage(request), _budget())

    assert result.status is BinanceKlineRestStatus.LOCAL_FAILURE
    assert result.attempts_used == 1
    assert len(result.pages) == 1
    assert len(result.pages[0].attempts) == 1
    attempt = result.pages[0].attempts[0]
    assert attempt.outcome == expected_outcome
    assert attempt.response_sha256 == expected_digest
    assert "evidence" in result.pages[0].detail


def test_long_retry_after_delta_exhausts_budget_without_retrying_early(
    tmp_path: Path,
) -> None:
    request = _request(minutes=1, limit=1)
    transport = _QueueTransport(
        [BinanceKlineRestHttpResponse(429, b"busy", {"Retry-After": "99999999999"})]
    )
    waits: list[float] = []
    _, acquisition = _acquisition(
        tmp_path,
        transport,
        wait=waits.append,
        monotonic_clock=lambda: 0.0,
    )

    result = acquisition.run(
        request, _coverage(request), _budget(attempts=2, elapsed=5.0)
    )

    assert result.status is BinanceKlineRestStatus.BUDGET_EXHAUSTED
    assert result.attempts_used == 1
    assert len(transport.calls) == 1
    assert waits == []


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


@pytest.mark.parametrize("status", [200, 503])
def test_partial_http_response_retries_same_page_and_preserves_received_bytes(
    tmp_path: Path, status: int
) -> None:
    request = _request(minutes=1, limit=1)
    partial = b'[[1788975180000,"100.0"'
    url = BinanceKlineRestAcquisition._request_url(request)
    success = BinanceKlineRestHttpResponse(200, _body_for_url(url), {})
    waits: list[float] = []
    transport = _QueueTransport(
        [
            BinanceKlineRestHttpResponse(
                status, partial, {"Retry-After": "1"}, complete=False
            ),
            success,
        ]
    )
    store, acquisition = _acquisition(
        tmp_path,
        transport,
        wait=waits.append,
        monotonic_clock=lambda: 0.0,
    )

    result = acquisition.run(request, _coverage(request), _budget())

    digest = hashlib.sha256(partial).hexdigest()
    assert result.status is BinanceKlineRestStatus.COMPLETE
    assert result.attempts_used == 2
    assert result.pages[0].attempts[0].page_attempt_number == 1
    assert result.pages[0].attempts[1].page_attempt_number == 2
    assert result.pages[0].attempts[0].outcome == "incomplete_response"
    assert result.pages[0].attempts[0].http_status == status
    assert result.pages[0].attempts[0].response_sha256 == digest
    assert transport.calls == [url, url]
    assert waits == [1]
    identity = RawObjectIdentity.from_rest_page_request(request)
    record = next(
        manifest
        for manifest in store.list_acquisition_manifests(identity)
        if manifest.source_body_sha256 == digest
    )
    evidence_path = store.acquisition_path_for(identity) / record.record_id
    assert record.status == BinanceKlineRestStatus.RETRYABLE_FAILURE.value
    assert record.source_http_status == status
    assert record.source_body_sha256 == digest
    assert (evidence_path / "response.json").read_bytes() == partial
    assert len(store.list_verified_revisions(identity)) == 1


@pytest.mark.parametrize("status", [302, 400])
def test_partial_nonretryable_http_response_is_terminal(
    tmp_path: Path, status: int
) -> None:
    request = _request(minutes=1, limit=1)
    partial = b'{"code":-1100'
    transport = _QueueTransport(
        [BinanceKlineRestHttpResponse(status, partial, {}, complete=False)]
    )
    waits: list[float] = []
    store, acquisition = _acquisition(
        tmp_path,
        transport,
        wait=waits.append,
        monotonic_clock=lambda: 0.0,
    )

    result = acquisition.run(request, _coverage(request), _budget(attempts=3))

    digest = hashlib.sha256(partial).hexdigest()
    assert result.status is BinanceKlineRestStatus.INVALID_RESPONSE
    assert result.attempts_used == 1
    assert len(transport.calls) == 1
    assert waits == []
    assert result.pages[0].attempts[0].outcome == "incomplete_response"
    assert result.pages[0].attempts[0].http_status == status
    assert result.pages[0].response_sha256 == digest
    identity = RawObjectIdentity.from_rest_page_request(request)
    record = store.list_acquisition_manifests(identity)[0]
    assert record.status == BinanceKlineRestStatus.INVALID_RESPONSE.value
    assert record.source_http_status == status
    assert record.source_body_sha256 == digest


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


def test_overlapping_caller_range_reuses_revision_and_reports_new_observation(
    tmp_path: Path,
) -> None:
    first_observation = datetime(2026, 9, 9, 19, 1, tzinfo=UTC)
    second_observation = datetime(2026, 9, 9, 19, 2, tzinfo=UTC)
    first_request = _request(minutes=3, limit=1)
    store, first_acquisition = _acquisition(
        tmp_path,
        _DynamicTransport(),
        clock=lambda: first_observation,
    )
    first = first_acquisition.run(first_request, _coverage(first_request), _budget())
    overlapping_request = _request(
        start=START + timedelta(minutes=1), minutes=2, limit=1
    )
    _, second_acquisition = _acquisition(
        tmp_path,
        _DynamicTransport(),
        clock=lambda: second_observation,
    )

    second = second_acquisition.run(
        overlapping_request, _coverage(overlapping_request), _budget()
    )

    assert first.status is BinanceKlineRestStatus.COMPLETE
    assert second.status is BinanceKlineRestStatus.COMPLETE
    original_revision = first.pages[1].revision
    assert original_revision is not None
    assert second.pages[0].revision == original_revision
    assert second.pages[0].detail == "existing immutable revision"
    assert second.pages[0].observed_at == second_observation
    persisted = store.read_exact_revision(
        original_revision.logical_identity, original_revision
    )
    provenance = persisted.manifest.rest_provenance
    assert provenance is not None
    assert provenance.observed_at == first_observation
    assert provenance.request.caller_range == first_request.caller_range


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


def test_rest_reader_rejects_completed_manifest_with_failed_http_status(
    tmp_path: Path,
) -> None:
    request = _request(minutes=1, limit=1)
    store, acquisition = _acquisition(tmp_path, _DynamicTransport())
    result = acquisition.run(request, _coverage(request), _budget())
    revision = result.pages[0].revision
    assert revision is not None
    artifact = store.read_exact_revision(revision.logical_identity, revision)
    manifest_path = artifact.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance"]["http_status"] = 500
    manifest_path.write_text(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(RawArtifactValidationError):
        store.read_exact_revision(revision.logical_identity, revision)


def test_rest_reader_rejects_archive_evidence_kind_for_rest_revision(
    tmp_path: Path,
) -> None:
    request = _request(minutes=1, limit=1)
    store, acquisition = _acquisition(tmp_path, _DynamicTransport())
    result = acquisition.run(request, _coverage(request), _budget())
    revision = result.pages[0].revision
    assert revision is not None
    artifact = store.read_exact_revision(revision.logical_identity, revision)
    manifest = artifact.manifest.to_dict()
    archive_revision = RawRevisionIdentity(
        logical_identity=revision.logical_identity,
        evidence_kind=RawRevisionEvidenceKind.UPSTREAM_CHECKSUM,
        verified_upstream_checksum=revision.verified_upstream_checksum,
    )
    manifest["revision_evidence_kind"] = "upstream_checksum"
    manifest["revision_id"] = archive_revision.revision_id
    archive_path = store.revision_path_for(
        archive_revision.logical_identity, archive_revision
    )
    artifact.path.rename(archive_path)
    (archive_path / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(
        RawArtifactValidationError,
        match="REST manifest revision evidence must be response_sha256",
    ):
        store.read_exact_revision(archive_revision.logical_identity, archive_revision)


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
