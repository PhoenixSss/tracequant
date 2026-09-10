# Binance USDⓈ-M 1m Kline REST acquisition

`BinanceKlineRestAcquisition.run(request, coverage, budget)` is the bounded
public entry point for contract, mark-price, and index-price 1m Klines. It
accepts only BTCUSDT and ETHUSDT. Contract and mark-price requests use an
`InstrumentId`/`symbol`; index-price requests use a
`BinancePriceIndexId`/`pair`.

The entry point accepts only complete, minute-aligned UTC half-open ranges. The
range must end before the current unclosed bar. It converts the end boundary to
Binance's inclusive `endTime` once, advances subsequent pages from the last
`open_time + 60000`, and never treats a short or empty page as proof that an
unmet range is complete.

## Required coverage and budget

`BinanceKlineRestCoverage` is source evidence, not a request to probe or extend
availability. A supported coverage value must match the request's endpoint and
typed subject, contain the request range, and bind the frozen #295 conclusion:

- evidence version:
  `issue-295-probe-run-2026-09-09T18:33:30.868474Z`;
- reference:
  `docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json`;
- reference SHA-256:
  `30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45`;
- maximum admitted UTC range:
  `[2026-09-09T17:33:00Z, 2026-09-09T18:33:00Z)`.

Missing, unknown, mismatched, unrecognized, or out-of-range coverage returns
`rest_boundary_unknown` (or `unsupported`) before HTTP access. The current date
does not extend this frozen range. A synthetic test response proves code
behavior only and is not valid availability evidence.

`BinanceKlineRestBudget` requires finite positive values for per-attempt
timeout, maximum pages, maximum attempts per page, total elapsed time, and
maximum response bytes. Timeout/connection failures, HTTP 429, and retryable
5xx responses use bounded backoff. A valid `Retry-After` is charged to the
remaining elapsed-time budget. Other 4xx responses, malformed JSON, Binance
error objects, and schema violations are terminal for that page.

```python
from datetime import UTC, datetime
from pathlib import Path

from tracequant.data import (
    BinanceKlineInterval,
    BinanceKlineRestAcquisition,
    BinanceKlineRestBudget,
    BinanceKlineRestCoverage,
    BinanceKlineRestCoverageStatus,
    BinanceRestEndpoint,
    BinanceRestPageRequest,
    RawStore,
)
from tracequant.domain import InstrumentId, TimeRange

requested = TimeRange(
    start=datetime(2026, 9, 9, 17, 33, tzinfo=UTC),
    end=datetime(2026, 9, 9, 17, 38, tzinfo=UTC),
)
request = BinanceRestPageRequest(
    endpoint=BinanceRestEndpoint.CONTRACT_KLINES,
    subject=InstrumentId("BTCUSDT"),
    caller_range=requested,
    interval=BinanceKlineInterval.ONE_MINUTE,
    limit=2,
)
coverage = BinanceKlineRestCoverage(
    status=BinanceKlineRestCoverageStatus.SUPPORTED,
    endpoint=request.endpoint,
    subject=request.subject,
    allowed_range=TimeRange(
        start=datetime(2026, 9, 9, 17, 33, tzinfo=UTC),
        end=datetime(2026, 9, 9, 18, 33, tzinfo=UTC),
    ),
    evidence_version="issue-295-probe-run-2026-09-09T18:33:30.868474Z",
    evidence_reference=(
        "docs/research/binance-usdm-feature11-rest-window-follow-up-probes.json"
    ),
    evidence_sha256=(
        "30682f83b887514ef2a0e20ec21203cff70529ac1518fb538277ae0881920b45"
    ),
    observed_at=datetime(2026, 9, 9, 18, 33, 40, tzinfo=UTC),
)
budget = BinanceKlineRestBudget(
    timeout_seconds=10,
    maximum_pages=10,
    maximum_attempts_per_page=3,
    maximum_elapsed_seconds=60,
    maximum_response_bytes=16 * 1024 * 1024,
)

result = BinanceKlineRestAcquisition(RawStore(Path("data"))).run(
    request, coverage, budget
)
if not result.complete:
    raise RuntimeError(
        f"{result.status}: {result.termination_reason}; unmet={result.unmet_range}"
    )
```

## Results and Raw evidence

The run result reports every page and attempt, the terminal reason, exact Raw
revision identity/path, actual record range, unmet range, and consumed
page/attempt/elapsed budgets. A non-empty valid response page is independently
published. Earlier valid pages remain readable if a later page fails, but the
run remains incomplete. Each successfully parsed page also reports this run's
`observed_at`; an idempotent repeat therefore preserves the first persisted
provenance while still making the new observation traceable in its current
result.

Each REST revision stores `data.parquet`, `manifest.json`, and the exact
`response.json` bytes. It uses `response_sha256` revision evidence and
`BinanceRestPageProvenance`; it does not fabricate a ZIP, `.CHECKSUM`, or CSV
member. Repeating the same page request and response digest is idempotent. New
bytes create a separate revision, and exact revision reads keep older content
available. A later observation alone does not overwrite the first persisted
provenance for identical content.

Contract Klines expose their actual volume, quote-volume, trade-count, and
taker fields. Mark-price and index-price Klines retain positions 5, 7, and
8–11 as `ignore_*` values; they are not published as volume or trade counts.
Empty/invalid/HTTP-failure responses are retained as acquisition evidence and
never become a zero-filled or falsely complete Parquet page. A transport can
return `BinanceKlineRestHttpResponse(..., complete=False)` when it received an
HTTP status and only a body prefix; the prefix bytes, digest, and status remain
failure evidence and are never published as completed Raw.

`BinanceRestPageAcquisition` is the public reusable execution boundary behind
the Kline facade. A dataset adapter supplies request/coverage checks, a parser
returning `BinanceRestPageParsed`, and a Raw schema identifier; the shared
executor owns URL construction, bounded HTTP/retry/wait behavior, elapsed and
page budgets, failure evidence, result assembly, and immutable page
publication. L05 can therefore provide a funding adapter and reuse this path
without copying the state machine. This Task does not itself implement funding
parsing, a CLI, archive fallback, source refresh, gap repair, scheduling, or
another provider.
