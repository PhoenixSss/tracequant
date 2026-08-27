# TraceQuant Domain

Core quantitative-trading domain models, invariants, and risk-independent business rules belong here as they are introduced.

Domain code must not depend on exchange/network clients, UI code, or deployment implementations.

## Research MVP public models

The initial internal public domain API is:

```python
from tracequant.domain import InstrumentId, OHLCVBar, TimeRange
```

- `InstrumentId` normalizes a trimmed instrument string to uppercase ASCII
  letters and digits.
- `TimeRange` represents a timezone-aware UTC half-open interval `[start, end)`.
- `OHLCVBar` applies the same interval rules and validates finite `float` OHLCV
  values, non-negative volume, and high/low relationships.

The models are immutable and expose explicit JSON-compatible serialization.
Datetime values use the repository UTC ISO 8601 format with a trailing `Z`.
Prices may currently be zero or negative; tick size, lot size, and monetary
precision are deliberately outside this model boundary.

Shared tests use deterministic, function-scoped fixtures from `tests/conftest.py`
and explicit factories from `tests/fixtures/domain.py`. Tests should override any
symbol, price, or time that matters to their assertion and keep test-specific data
in the test module.

These are Research MVP internal public models, not a long-term external API or
versioned serialization protocol. They do not model venues, market types,
timeframes, orders, accounts, exchange metadata, calendars, or persistence.
