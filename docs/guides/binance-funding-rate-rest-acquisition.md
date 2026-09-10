# Binance USDⓈ-M settled-funding REST acquisition

`BinanceFundingRateRestAcquisition.run(request, coverage, budget)` is the
bounded public entry point for settled funding-rate pages. It accepts only the
USDⓈ-M `/fapi/v1/fundingRate` endpoint with a typed BTCUSDT or ETHUSDT
`InstrumentId`; funding requests use `symbol` and set `interval=None`.

The caller supplies a timezone-aware UTC half-open `[start, end)` range.
`BinanceRestPageRequest` converts it once to inclusive `startTime` and
`endTime=end-1`, enforces the endpoint's `limit <= 1000`, and retains the same
caller range on every page. Pagination advances from the actual last
`fundingTime + 1ms`; it never assumes a permanent eight-hour schedule or
invents events between observed timestamps.

## Frozen coverage and finite budget

`BinanceFundingRateRestCoverage` is source evidence, not permission to probe a
new date. A supported value must match the endpoint, typed instrument, request
range, and one exact funding recent/gap observation from the #279 follow-up
Research in #295. The binding contains the normalized source parameters,
response digest, observation time and actual point range, plus:

- evidence version:
  `issue-295-probe-run-2026-09-09T18:33:30.868474Z`;
- reference:
  `docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json`;
- reference SHA-256:
  `30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45`.

Only the two frozen observations per instrument are recognized: the seven-day
recent request `[2026-09-02T18:33:32.658Z,
2026-09-09T18:33:32.658Z)` and the one-hour positive gap observation
`[2026-09-03T00:00:00Z, 2026-09-03T01:00:00Z)`. Coverage may narrow one of
those windows but may not extend it. Missing, unknown, wrong-subject,
unrecognized, or out-of-window evidence fails before HTTP access. Current time
never expands a frozen observation.

The funding-named budget/status/result types are aliases of the shared REST
executor types. Timeout, response-size, page/attempt, total elapsed-time,
backoff and `Retry-After` limits therefore have exactly the same semantics as
the Kline REST path and are not reset between funding pages.

```python
from datetime import UTC, datetime
from pathlib import Path

from tracequant.data import (
    BinanceFundingRateRestAcquisition,
    BinanceFundingRateRestBudget,
    BinanceFundingRateRestCoverage,
    BinanceFundingRateRestCoverageStatus,
    BinanceRestEndpoint,
    BinanceRestPageRequest,
    RawStore,
)
from tracequant.domain import InstrumentId, TimeRange

requested = TimeRange(
    start=datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
    end=datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
)
request = BinanceRestPageRequest(
    endpoint=BinanceRestEndpoint.FUNDING_RATE_HISTORY,
    subject=InstrumentId("BTCUSDT"),
    caller_range=requested,
    interval=None,
    limit=100,
)
coverage = BinanceFundingRateRestCoverage(
    status=BinanceFundingRateRestCoverageStatus.SUPPORTED,
    endpoint=request.endpoint,
    subject=request.subject,
    allowed_range=requested,
    evidence_version="issue-295-probe-run-2026-09-09T18:33:30.868474Z",
    evidence_reference=(
        "docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json"
    ),
    evidence_sha256=(
        "30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45"
    ),
    observed_at=datetime(2026, 9, 9, 18, 33, 39, 998536, tzinfo=UTC),
    normalized_params={
        "symbol": "BTCUSDT",
        "startTime": 1788393600000,
        "endTime": 1788397199999,
        "limit": 100,
    },
    response_sha256=(
        "39ac0b55d6acae3de0e0be633edbfabdc37a227fa913027fe2e63c547abda401"
    ),
    actual_range=TimeRange(
        start=datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        end=datetime(2026, 9, 3, 0, 0, 0, 1000, tzinfo=UTC),
    ),
)
budget = BinanceFundingRateRestBudget(
    timeout_seconds=10,
    maximum_pages=4,
    maximum_attempts_per_page=2,
    maximum_elapsed_seconds=30,
    maximum_response_bytes=16 * 1024 * 1024,
)

result = BinanceFundingRateRestAcquisition(RawStore(Path("data"))).run(
    request, coverage, budget
)
```

## Settled schema, Raw revisions, and coverage limits

Every response must be a strict JSON array of objects. `symbol`, `fundingTime`
and `fundingRate` are required: the symbol must match the request, time must be
a boundary-contained int64 millisecond value, and the rate remains its original
finite numeric string, including negative values and zero. Records must be
strictly time-increasing across and within pages; duplicates, conflicts,
out-of-order values, boundary violations, or a non-advancing cursor stop the
run without sorting, deduplicating, or dropping rows.

The Raw schema is
`binance.um.settled-funding-rate.rest-json.v1`. It retains the source names
`symbol`, `fundingTime`, `fundingRate`, `markPrice`, and `rateType`.
`markPricePresent` and `rateTypePresent` distinguish a missing optional field
from an explicit JSON null. The only currently confirmed non-null `rateType`
is `Regular`; an unconfirmed value or new funding-semantic field is rejected
instead of being published as settled data. Benign unknown fields are named in
`unknownFields`, while their exact names and values remain in `response.json`.
Archive-only `calc_time`, `funding_interval_hours`, and
`last_funding_rate` are never synthesized.

Each valid non-empty page is independently and atomically published with
`data.parquet`, `manifest.json`, and its exact `response.json`. Identical
request bytes are idempotent; a new response digest creates a new immutable
revision and leaves older revisions readable. A later-page or local-storage
failure does not erase pages already published, but the whole run remains
incomplete.

A short or empty response may terminate pagination under the confirmed
ascending bounded endpoint semantics. An empty response is stored only as
acquisition evidence and never as a zero-row Raw artifact. `COMPLETE` means
that the bounded response sequence terminated normally; it does not prove a
continuous calendar range, the absence of unreported exchange events, the
earliest available history, or that an external gap-repair objective is
satisfied. Callers must compare returned point events and `actual_record_range`
with their own coverage requirement rather than treating the request envelope
as continuous event coverage.
