# TraceQuant Domain

Core quantitative-trading domain models, invariants, and risk-independent business rules belong here as they are introduced.

Domain code must not depend on exchange/network clients, UI code, or deployment implementations.

## Initial public models

The Research MVP currently exposes only these immutable models:

```python
from tracequant.domain import InstrumentId, OHLCVBar, TimeRange
```

- `InstrumentId` strips surrounding whitespace, normalizes to uppercase, and
  accepts 1–32 ASCII letters or digits. It does not encode an exchange, market
  type, base/quote split, tick size, or lot size.
- `TimeRange` represents a UTC half-open interval `[start, end)`. Direct model
  construction rejects naive and non-UTC datetimes. Deserialization uses the
  existing UTC parser, so explicit ISO 8601 offsets are normalized to UTC.
- `OHLCVBar` carries one instrument and interval plus Python `float` OHLCV
  values. Values must be finite, volume must be non-negative, and OHLC bounds
  must be consistent. Zero and negative prices remain valid for research data.

Each model provides an explicit JSON-compatible `to_dict` / `from_dict`
round-trip. These are initial internal public models for the Research MVP, not a
versioned external wire protocol.

## Test fixtures and factories

Reusable deterministic factories live in `tests/fixtures/domain.py`. Shared
pytest fixtures belong in `tests/conftest.py` only after at least two test
modules use them; they retain pytest's default function scope. Factories accept
explicit field overrides and use fixed UTC timestamps. Tests must keep local
one-off data in their own module and must not use current time, randomness,
environment variables, network access, filesystem state, shared mutable
objects, or a general-purpose mega-fixture.
