# Initial public domain models

`tracequant.domain` is the public import boundary for the Research MVP's initial
internal domain models:

```python
from tracequant.domain import InstrumentId, OHLCVBar, TimeRange
```

- `InstrumentId` normalizes a trimmed symbol to uppercase ASCII letters and digits.
- `TimeRange` represents a timezone-aware UTC half-open interval `[start, end)`.
- `OHLCVBar` carries one instrument's finite OHLCV values over the same interval.

The models are immutable value objects. Their explicit `to_dict` and `from_dict`
methods use stable JSON-compatible strings, mappings, floats, and the project's UTC
ISO 8601 format. Aware datetimes with another offset are normalized to UTC through
`tracequant.core.time`; naive datetimes are rejected.

Prices may currently be zero or negative. Volume must be non-negative, `high` must
not be below any OHLC price, and `low` must not be above any OHLC price. These are
conservative data-shape invariants, not exchange tick, lot, or monetary precision
rules.

These models are an internal shared API for the Research MVP, not a promise of
long-term external serialization compatibility. They deliberately do not model a
venue, market type, timeframe, trades, orders, accounts, exchange metadata, storage,
or a versioned schema.

Shared tests use deterministic, fixed-UTC factories in `tests/fixtures/domain.py`.
Factories expose explicit field overrides and return a fresh object on every call.
Only fixtures used by multiple test modules belong in `tests/conftest.py`; values
specific to one test stay in that test module.
