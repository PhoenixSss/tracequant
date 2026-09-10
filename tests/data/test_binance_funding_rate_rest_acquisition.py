import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from tracequant.data import (
    BinanceFundingRateRestAcquisition,
    BinanceFundingRateRestBudget,
    BinanceFundingRateRestCoverage,
    BinanceFundingRateRestCoverageStatus,
    BinanceFundingRateRestStatus,
    BinanceKlineRestHttpResponse,
    BinanceRestEndpoint,
    BinanceRestPageRequest,
    RawObjectIdentity,
    RawStore,
)
from tracequant.domain import InstrumentId, TimeRange

EVIDENCE_VERSION = "issue-295-probe-run-2026-09-09T18:33:30.868474Z"
EVIDENCE_REFERENCE = (
    "docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json"
)
EVIDENCE_SHA256 = "30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45"
RECENT_START_MS = 1_788_374_012_658
RECENT_END_MS = 1_788_978_812_658
FIRST_FUNDING_MS = 1_788_393_600_000
OBSERVED_LAST_MS = 1_788_969_600_002
NOW = datetime(2026, 9, 9, 19, 0, tzinfo=UTC)

CELL_EVIDENCE = {
    "BTCUSDT": (
        datetime(2026, 9, 9, 18, 33, 33, 964227, tzinfo=UTC),
        "2bd472efeb5b2d6336fda340914d77770151589cb3a14ff419e1ed149ef41f71",
    ),
    "ETHUSDT": (
        datetime(2026, 9, 9, 18, 33, 35, 109386, tzinfo=UTC),
        "368852dd6686fce1fac1d940f7153dc5099acc8125c7ae8bac4d4b0ece24cace",
    ),
}
GAP_CELL_EVIDENCE = {
    "BTCUSDT": (
        datetime(2026, 9, 9, 18, 33, 39, 998536, tzinfo=UTC),
        "39ac0b55d6acae3de0e0be633edbfabdc37a227fa913027fe2e63c547abda401",
    ),
    "ETHUSDT": (
        datetime(2026, 9, 9, 18, 33, 40, 679377, tzinfo=UTC),
        "b862855ea7296ca550dc3aac55c34e2e907cb993aa5749efaedd4409b4cf1ac1",
    ),
}


def _at(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1_000, tz=UTC)


def _request(
    symbol: str = "BTCUSDT",
    *,
    start_ms: int = FIRST_FUNDING_MS,
    end_ms: int = FIRST_FUNDING_MS + 28 * 60 * 60 * 1_000 + 1,
    limit: int = 2,
) -> BinanceRestPageRequest:
    return BinanceRestPageRequest(
        endpoint=BinanceRestEndpoint.FUNDING_RATE_HISTORY,
        subject=InstrumentId(symbol),
        caller_range=TimeRange(start=_at(start_ms), end=_at(end_ms)),
        interval=None,
        limit=limit,
    )


def _coverage(
    request: BinanceRestPageRequest,
    *,
    status: BinanceFundingRateRestCoverageStatus = (
        BinanceFundingRateRestCoverageStatus.SUPPORTED
    ),
    allowed_range: TimeRange | None = None,
) -> BinanceFundingRateRestCoverage:
    symbol = str(request.subject)
    observed_at, response_sha256 = CELL_EVIDENCE.get(symbol, CELL_EVIDENCE["BTCUSDT"])
    return BinanceFundingRateRestCoverage(
        status=status,
        endpoint=BinanceRestEndpoint.FUNDING_RATE_HISTORY,
        subject=request.subject,
        allowed_range=allowed_range
        or TimeRange(start=_at(RECENT_START_MS), end=_at(RECENT_END_MS)),
        evidence_version=EVIDENCE_VERSION,
        evidence_reference=EVIDENCE_REFERENCE,
        evidence_sha256=EVIDENCE_SHA256,
        observed_at=observed_at,
        normalized_params={
            "symbol": symbol,
            "startTime": RECENT_START_MS,
            "endTime": RECENT_END_MS - 1,
            "limit": 1000,
        },
        response_sha256=response_sha256,
        actual_range=TimeRange(
            start=_at(FIRST_FUNDING_MS), end=_at(OBSERVED_LAST_MS + 1)
        ),
    )


def _budget(
    *, pages: int = 8, attempts: int = 3, elapsed: float = 30.0
) -> BinanceFundingRateRestBudget:
    return BinanceFundingRateRestBudget(
        timeout_seconds=5,
        maximum_pages=pages,
        maximum_attempts_per_page=attempts,
        maximum_elapsed_seconds=elapsed,
        maximum_response_bytes=1_000_000,
    )


def _gap_coverage(request: BinanceRestPageRequest) -> BinanceFundingRateRestCoverage:
    symbol = str(request.subject)
    observed_at, response_sha256 = GAP_CELL_EVIDENCE[symbol]
    gap_end_ms = FIRST_FUNDING_MS + 60 * 60 * 1_000
    return BinanceFundingRateRestCoverage(
        status=BinanceFundingRateRestCoverageStatus.SUPPORTED,
        endpoint=request.endpoint,
        subject=request.subject,
        allowed_range=TimeRange(start=_at(FIRST_FUNDING_MS), end=_at(gap_end_ms)),
        evidence_version=EVIDENCE_VERSION,
        evidence_reference=EVIDENCE_REFERENCE,
        evidence_sha256=EVIDENCE_SHA256,
        observed_at=observed_at,
        normalized_params={
            "symbol": symbol,
            "startTime": FIRST_FUNDING_MS,
            "endTime": gap_end_ms - 1,
            "limit": 100,
        },
        response_sha256=response_sha256,
        actual_range=TimeRange(
            start=_at(FIRST_FUNDING_MS), end=_at(FIRST_FUNDING_MS + 1)
        ),
    )


def _records(symbol: str, *, variant: int = 0) -> list[dict[str, object]]:
    times = [
        FIRST_FUNDING_MS,
        FIRST_FUNDING_MS + 8 * 60 * 60 * 1_000,
        FIRST_FUNDING_MS + 16 * 60 * 60 * 1_000,
        FIRST_FUNDING_MS + 20 * 60 * 60 * 1_000,
        FIRST_FUNDING_MS + 28 * 60 * 60 * 1_000,
    ]
    rates = (
        ["0.00010000", "-0.00020000", "0", "0.00030000", "-0.00040000"]
        if symbol == "BTCUSDT"
        else ["-0.00001000", "0", "0.00002000", "-0.00003000", "0.00004000"]
    )
    if variant:
        rates[0] = "0.00090000"
    return [
        {
            "symbol": symbol,
            "fundingTime": times[0],
            "fundingRate": rates[0],
            "markPrice": "70000.00000000",
            "rateType": "Regular",
        },
        {
            "symbol": symbol,
            "fundingTime": times[1],
            "fundingRate": rates[1],
            "markPrice": None,
        },
        {
            "symbol": symbol,
            "fundingTime": times[2],
            "fundingRate": rates[2],
            "rateType": None,
        },
        {
            "symbol": symbol,
            "fundingTime": times[3],
            "fundingRate": rates[3],
            "markPrice": "71000.00000000",
            "rateType": "Regular",
            "sourceNote": {"revision": 1},
        },
        {
            "symbol": symbol,
            "fundingTime": times[4],
            "fundingRate": rates[4],
            "markPrice": "72000.00000000",
            "rateType": "Regular",
        },
    ]


class _FundingTransport:
    def __init__(self, *, variant: int = 0) -> None:
        self.variant = variant
        self.calls: list[str] = []
        self.bodies: dict[str, bytes] = {}

    def __call__(
        self, url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del timeout, maximum_response_bytes
        self.calls.append(url)
        parsed = urlsplit(url)
        assert parsed.path == BinanceRestEndpoint.FUNDING_RATE_HISTORY.value
        params = parse_qs(parsed.query)
        assert "interval" not in params
        symbol = params["symbol"][0]
        start_ms = int(params["startTime"][0])
        end_ms = int(params["endTime"][0])
        limit = int(params["limit"][0])
        selected = [
            record
            for record in _records(symbol, variant=self.variant)
            if start_ms <= cast(int, record["fundingTime"]) <= end_ms
        ][:limit]
        body = json.dumps(selected, separators=(",", ":")).encode()
        self.bodies[url] = body
        return BinanceKlineRestHttpResponse(
            status=200,
            body=body,
            headers={"Content-Type": "application/json", "X-MBX-USED-WEIGHT-1M": "1"},
        )


def test_funding_rest_run_publishes_verified_page_raw(tmp_path: Path) -> None:
    store = RawStore(tmp_path, clock=lambda: NOW)

    for symbol in ("BTCUSDT", "ETHUSDT"):
        transport = _FundingTransport()
        acquisition = BinanceFundingRateRestAcquisition(
            store,
            http_get=transport,
            clock=lambda: NOW,
            monotonic_clock=lambda: 0.0,
        )
        request = _request(symbol)
        result = acquisition.run(request, _coverage(request), _budget())

        assert result.status is BinanceFundingRateRestStatus.COMPLETE
        assert result.complete
        assert result.unmet_range is None
        assert result.actual_record_range == request.caller_range
        assert [page.record_count for page in result.pages] == [2, 2, 1]
        assert [page.short_page for page in result.pages] == [False, False, True]
        assert len(transport.calls) == 3
        starts = [
            int(parse_qs(urlsplit(url).query)["startTime"][0])
            for url in transport.calls
        ]
        source_times = [cast(int, record["fundingTime"]) for record in _records(symbol)]
        assert starts == [source_times[0], source_times[1] + 1, source_times[3] + 1]
        assert all(
            int(parse_qs(urlsplit(url).query)["endTime"][0]) == source_times[-1]
            for url in transport.calls
        )

        frames = []
        raw_bodies = []
        first_revisions = []
        for page, url in zip(result.pages, transport.calls, strict=True):
            revision = page.revision
            assert revision is not None
            first_revisions.append(revision)
            artifact = store.read_exact_revision(revision.logical_identity, revision)
            frames.append(artifact.frame)
            raw_bodies.append(artifact.response_path.read_bytes())
            assert artifact.response_path.read_bytes() == transport.bodies[url]
            assert artifact.manifest.raw_schema_identifier == (
                "binance.um.settled-funding-rate.rest-json.v1"
            )
            assert artifact.manifest.rest_provenance is not None
            assert artifact.manifest.rest_provenance.request.normalized_params == (
                page.request.normalized_params
            )

        assert raw_bodies
        assert [value for frame in frames for value in frame["fundingRate"]] == [
            record["fundingRate"] for record in _records(symbol)
        ]
        assert frames[0]["markPricePresent"].to_list() == [True, True]
        assert frames[0]["markPrice"].to_list() == ["70000.00000000", None]
        assert frames[1]["markPricePresent"].to_list() == [False, True]
        assert frames[1]["rateTypePresent"].to_list() == [True, True]
        assert frames[1]["rateType"].to_list() == [None, "Regular"]
        assert frames[1]["unknownFields"].to_list() == [[], ["sourceNote"]]

        repeated = acquisition.run(request, _coverage(request), _budget())
        assert repeated.status is BinanceFundingRateRestStatus.COMPLETE
        assert [page.revision for page in repeated.pages] == first_revisions
        for revision in first_revisions:
            assert store.read_exact_revision(revision.logical_identity, revision)


def test_funding_rest_new_digest_keeps_old_revision(tmp_path: Path) -> None:
    store = RawStore(tmp_path, clock=lambda: NOW)
    request = _request(end_ms=FIRST_FUNDING_MS + 1, limit=100)
    original = BinanceFundingRateRestAcquisition(
        store,
        http_get=_FundingTransport(),
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    ).run(request, _coverage(request), _budget())
    replacement = BinanceFundingRateRestAcquisition(
        store,
        http_get=_FundingTransport(variant=1),
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    ).run(request, _coverage(request), _budget())

    first_revision = original.pages[0].revision
    second_revision = replacement.pages[0].revision
    assert first_revision is not None
    assert second_revision is not None
    assert first_revision != second_revision
    assert store.read_exact_revision(
        first_revision.logical_identity, first_revision
    ).frame["fundingRate"].to_list() == ["0.00010000"]
    assert store.read_exact_revision(
        second_revision.logical_identity, second_revision
    ).frame["fundingRate"].to_list() == ["0.00090000"]


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
def test_funding_gap_observation_returns_point_range_not_calendar_coverage(
    tmp_path: Path, symbol: str
) -> None:
    request = _request(
        symbol,
        start_ms=FIRST_FUNDING_MS,
        end_ms=FIRST_FUNDING_MS + 60 * 60 * 1_000,
        limit=100,
    )
    result = BinanceFundingRateRestAcquisition(
        RawStore(tmp_path),
        http_get=_FundingTransport(),
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    ).run(request, _gap_coverage(request), _budget())

    assert result.status is BinanceFundingRateRestStatus.COMPLETE
    assert result.pages[0].record_count == 1
    assert result.actual_record_range == TimeRange(
        start=_at(FIRST_FUNDING_MS), end=_at(FIRST_FUNDING_MS + 1)
    )
    assert result.actual_record_range != result.requested_range


@pytest.mark.parametrize(
    ("mutate", "expected_status", "detail"),
    [
        (
            lambda coverage: replace(
                coverage, status=BinanceFundingRateRestCoverageStatus.UNKNOWN
            ),
            BinanceFundingRateRestStatus.REST_BOUNDARY_UNKNOWN,
            "unknown",
        ),
        (
            lambda coverage: replace(
                coverage, status=BinanceFundingRateRestCoverageStatus.UNSUPPORTED
            ),
            BinanceFundingRateRestStatus.UNSUPPORTED,
            "unsupported",
        ),
        (
            lambda coverage: replace(coverage, evidence_sha256="0" * 64),
            BinanceFundingRateRestStatus.REST_BOUNDARY_UNKNOWN,
            "binding",
        ),
        (
            lambda coverage: replace(
                coverage,
                normalized_params={**coverage.normalized_params, "limit": 999},
            ),
            BinanceFundingRateRestStatus.REST_BOUNDARY_UNKNOWN,
            "observation",
        ),
        (
            lambda coverage: replace(
                coverage,
                allowed_range=TimeRange(
                    start=_at(RECENT_START_MS - 1), end=_at(RECENT_END_MS)
                ),
            ),
            BinanceFundingRateRestStatus.REST_BOUNDARY_UNKNOWN,
            "extends",
        ),
    ],
)
def test_funding_coverage_fails_closed_before_http(
    tmp_path: Path,
    mutate: object,
    expected_status: BinanceFundingRateRestStatus,
    detail: str,
) -> None:
    request = _request()
    calls = 0

    def transport(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del url, timeout, maximum_response_bytes
        nonlocal calls
        calls += 1
        return BinanceKlineRestHttpResponse(200, b"[]", {})

    assert callable(mutate)
    coverage = mutate(_coverage(request))
    result = BinanceFundingRateRestAcquisition(
        RawStore(tmp_path), http_get=transport, clock=lambda: NOW
    ).run(request, coverage, _budget())

    assert result.status is expected_status
    assert detail in result.termination_reason
    assert result.pages == ()
    assert calls == 0


def test_missing_coverage_and_unsupported_instrument_do_not_access_http(
    tmp_path: Path,
) -> None:
    calls = 0

    def transport(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del url, timeout, maximum_response_bytes
        nonlocal calls
        calls += 1
        return BinanceKlineRestHttpResponse(200, b"[]", {})

    acquisition = BinanceFundingRateRestAcquisition(
        RawStore(tmp_path), http_get=transport, clock=lambda: NOW
    )
    request = _request()
    missing = acquisition.run(request, None, _budget())
    unsupported_request = _request("BTCUSDC")
    unsupported = acquisition.run(
        unsupported_request, _coverage(unsupported_request), _budget()
    )

    assert missing.status is BinanceFundingRateRestStatus.REST_BOUNDARY_UNKNOWN
    assert unsupported.status is BinanceFundingRateRestStatus.UNSUPPORTED
    assert calls == 0


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"code": -1}, "must be an array"),
        ([{"symbol": "BTCUSDT"}], "missing required fields"),
        (
            [
                {
                    "symbol": "ETHUSDT",
                    "fundingTime": FIRST_FUNDING_MS,
                    "fundingRate": "0.1",
                }
            ],
            "symbol does not match",
        ),
        (
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FIRST_FUNDING_MS,
                    "fundingRate": "NaN",
                }
            ],
            "finite numeric string",
        ),
        (
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FIRST_FUNDING_MS,
                    "fundingRate": "0.1",
                    "markPrice": "Infinity",
                }
            ],
            "finite numeric string",
        ),
        (
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FIRST_FUNDING_MS,
                    "fundingRate": "0.1",
                    "rateType": "Predicted",
                }
            ],
            "unconfirmed settled semantics",
        ),
        (
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FIRST_FUNDING_MS,
                    "fundingRate": "0.1",
                    "predictedFundingRate": "0.2",
                }
            ],
            "unconfirmed funding-semantic fields",
        ),
        (
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FIRST_FUNDING_MS + 1,
                    "fundingRate": "0.1",
                },
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FIRST_FUNDING_MS,
                    "fundingRate": "0.2",
                },
            ],
            "out of order",
        ),
        (
            [
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FIRST_FUNDING_MS,
                    "fundingRate": "0.1",
                },
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": FIRST_FUNDING_MS,
                    "fundingRate": "0.1",
                },
            ],
            "duplicate fundingTime",
        ),
    ],
)
def test_malformed_or_unsettled_funding_response_is_not_published(
    tmp_path: Path, payload: object, detail: str
) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()

    def transport(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del url, timeout, maximum_response_bytes
        return BinanceKlineRestHttpResponse(200, body, {})

    request = _request(end_ms=FIRST_FUNDING_MS + 2, limit=10)
    result = BinanceFundingRateRestAcquisition(
        RawStore(tmp_path),
        http_get=transport,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    ).run(request, _coverage(request), _budget())

    assert result.status is BinanceFundingRateRestStatus.INVALID_RESPONSE
    assert detail in result.termination_reason
    assert result.pages[0].revision is None
    assert not list(tmp_path.rglob("data.parquet"))


def test_empty_response_records_evidence_without_zero_row_raw(tmp_path: Path) -> None:
    def transport(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del url, timeout, maximum_response_bytes
        return BinanceKlineRestHttpResponse(200, b"[]", {})

    request = _request(end_ms=FIRST_FUNDING_MS + 1, limit=100)
    result = BinanceFundingRateRestAcquisition(
        RawStore(tmp_path),
        http_get=transport,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    ).run(request, _coverage(request), _budget())

    assert result.status is BinanceFundingRateRestStatus.COMPLETE
    assert result.pages[0].status is BinanceFundingRateRestStatus.LEGAL_EMPTY
    assert result.pages[0].response_sha256 is not None
    assert result.pages[0].revision is None
    assert result.actual_record_range is None
    assert not list(tmp_path.rglob("data.parquet"))
    assert list(tmp_path.rglob("response.json"))


def test_shared_retry_budget_and_failure_evidence_apply_to_funding(
    tmp_path: Path,
) -> None:
    valid_body = json.dumps([_records("BTCUSDT")[0]], separators=(",", ":")).encode()
    responses: list[BinanceKlineRestHttpResponse | BaseException] = [
        BinanceKlineRestHttpResponse(429, b"limited", {"Retry-After": "1"}),
        TimeoutError("timed out"),
        BinanceKlineRestHttpResponse(200, valid_body, {}),
    ]
    calls: list[str] = []
    waits: list[float] = []

    def transport(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del timeout, maximum_response_bytes
        calls.append(url)
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    store = RawStore(tmp_path)
    request = _request(end_ms=FIRST_FUNDING_MS + 1, limit=100)
    result = BinanceFundingRateRestAcquisition(
        store,
        http_get=transport,
        clock=lambda: NOW,
        wait=waits.append,
        monotonic_clock=lambda: 0.0,
    ).run(request, _coverage(request), _budget(attempts=3))

    assert result.status is BinanceFundingRateRestStatus.COMPLETE
    assert len(set(calls)) == 1
    assert waits == [1, 2.0]
    assert [attempt.outcome for attempt in result.pages[0].attempts] == [
        "retryable_http",
        "transport_error",
        "received",
    ]
    identity = RawObjectIdentity.from_rest_page_request(request)
    assert len(store.list_acquisition_manifests(identity)) == 2


def test_cross_page_conflict_keeps_the_valid_first_page(tmp_path: Path) -> None:
    records = _records("BTCUSDT")
    bodies = [
        json.dumps(records[:2], separators=(",", ":")).encode(),
        json.dumps([records[1]], separators=(",", ":")).encode(),
    ]

    def transport(
        url: str, timeout: float, maximum_response_bytes: int
    ) -> BinanceKlineRestHttpResponse:
        del url, timeout, maximum_response_bytes
        return BinanceKlineRestHttpResponse(200, bodies.pop(0), {})

    store = RawStore(tmp_path)
    request = _request()
    result = BinanceFundingRateRestAcquisition(
        store,
        http_get=transport,
        clock=lambda: NOW,
        monotonic_clock=lambda: 0.0,
    ).run(request, _coverage(request), _budget())

    assert result.status is BinanceFundingRateRestStatus.INVALID_RESPONSE
    assert "cross-page duplicate" in result.termination_reason
    assert result.unmet_range is not None
    first_revision = result.pages[0].revision
    assert first_revision is not None
    assert store.read_exact_revision(first_revision.logical_identity, first_revision)
    assert result.pages[1].revision is None
