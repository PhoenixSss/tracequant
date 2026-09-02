# Initial public domain models

The current Research MVP foundation exposes three immutable value models from
one public import path:

```python
from tracequant.domain import DomainValidationError, InstrumentId, OHLCVBar, TimeRange
```

These are internal Python interfaces for the current repository. They are not
an exchange schema, an order/account model, or a promise of long-term external
serialization compatibility.

## Invariants

| Model | Current contract |
| --- | --- |
| `InstrumentId` | Trims surrounding whitespace, normalizes to ASCII uppercase letters and digits, and rejects empty values or values longer than 32 characters. |
| `TimeRange` | Contains `start` and `end` as a timezone-aware UTC half-open interval `[start, end)`, with `start < end`. Aware non-UTC values are normalized to UTC; naive values are rejected. |
| `OHLCVBar` | Binds an `InstrumentId` to a `TimeRange` and finite Python `float` OHLCV values. Volume is non-negative; `high` is not below open/low/close and `low` is not above open/high/close. |

`OHLCVBar` currently allows zero and negative prices. That is an intentional
boundary decision for this initial model, not a complete venue or instrument
validation policy. The models do not represent venues, market types, quote/base
assets, tick or lot sizes, timeframes, trade counts, VWAP, funding, mark/index
prices, exchange metadata, orders, accounts, persistence, or risk decisions.

Validation failures are `ValueError` subclasses. `DomainValidationError` is
exported for callers that need to distinguish domain invariant failures from
ordinary type errors.

## Serialization

Each model has explicit `to_dict()` and `from_dict()` methods. The output uses
only JSON-compatible strings and finite floats:

- `InstrumentId.to_dict()` returns its string value;
- `TimeRange.to_dict()` returns exact `start` and `end` fields;
- `OHLCVBar.to_dict()` returns exact instrument, interval, and OHLCV fields;
- deserialization rejects missing, extra, or incorrectly typed fields;
- datetime strings use the UTC ISO 8601 form with a `Z` suffix.

The following deterministic example uses the current public API:

```python
import json
from datetime import UTC, datetime

from tracequant.domain import InstrumentId, OHLCVBar

bar = OHLCVBar(
    instrument=InstrumentId("BTCUSDT"),
    start=datetime(2024, 2, 29, 23, 45, tzinfo=UTC),
    end=datetime(2024, 3, 1, 0, 0, tzinfo=UTC),
    open=100.0,
    high=110.0,
    low=90.0,
    close=105.0,
    volume=12.5,
)
payload = bar.to_dict()
json.dumps(payload, allow_nan=False)
assert OHLCVBar.from_dict(payload) == bar
```

Serialization is a validated interchange helper for the current package. It
does not define a versioned database schema, a canonical exchange-data schema,
or a persistence strategy.

## UTC boundary

`tracequant.core.time` is the shared boundary for `ensure_aware`, `to_utc`,
`parse_utc`, and `format_utc`. Domain constructors normalize aware values and
reject naive values; callers remain responsible for validating external event
time semantics before constructing a model. No domain model consults the
machine's local timezone.

## Binance public-history boundary

`tracequant.data` adds the first venue-specific data contract without changing
the generic domain models. `BinancePublicHistoryRequest` reuses
`InstrumentId` and `TimeRange`, and admits only the four currently scoped
Binance USDⓈ-M instruments and data families:

- 1m contract, mark-price, and index-price Klines;
- settled funding-rate records, which have no Kline interval.

`BinanceArchiveObjectBoundary` identifies an upstream UTC calendar day or
month. It is serialized separately from the caller's `[start, end)`
`request_range`; an archive object boundary is not a claim about the request's
actual coverage. Daily archive sources are limited to the three Kline
families, while monthly archive and REST sources also represent settled
funding.

The request and its `source_identity` have deterministic JSON-compatible
serialization. The module contains only value validation and serialization;
URL resolution, HTTP, archive parsing, persistence, and completeness checks
remain outside this contract.

## Test-factory boundary

One-off values stay in the owning test. Deterministic factories reused across
domain tests live in `tests/fixtures/domain.py`:

- `make_instrument(value="BTCUSDT")`;
- `make_time_range(start=..., end=...)`;
- `make_bar(instrument=..., start=..., end=..., open=..., high=..., low=...,
  close=..., volume=...)`.

The default range is the fixed UTC interval
`2024-02-29T23:45:00Z` to `2024-03-01T00:00:00Z`, and default prices/volume are
fixed constants. `tests/conftest.py` exposes these callables as function-scoped
`instrument_factory`, `time_range_factory`, and `bar_factory` fixtures.

Fixtures import the production models so tests can construct valid values, but
production modules never import fixtures. The factories are not a data source,
runtime dependency, exchange simulator, or substitute for future market-data
fixtures.
