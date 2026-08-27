# Initial domain models

TraceQuant's Research MVP currently exposes the following small internal domain
models through the formal public import path:

```python
from tracequant.domain import InstrumentId, OHLCVBar, TimeRange
```

`InstrumentId` trims surrounding whitespace, normalizes ASCII letters to upper
case, and accepts only ASCII letters and digits. It is a single normalized
instrument identifier; venue, exchange, market type, and symbol decomposition
are intentionally outside its boundary.

`TimeRange` is an immutable UTC half-open interval, `[start, end)`. Inputs must
be timezone-aware and are normalized with the existing `tracequant.core.time`
UTC utilities. Naive datetimes are rejected, and `start` must be earlier than
`end`.

`OHLCVBar` contains an `InstrumentId`, a UTC half-open interval, and Python
`float` OHLCV values. Values must be finite, volume must be non-negative, and
the high/low values must enclose open and close. Prices of zero and negative
values are currently allowed; no positive-price market assumption is added yet.

All three models are immutable, comparable value objects with explicit
`to_dict()` / `from_dict()` APIs. Their output is JSON-compatible and uses the
repository's UTC ISO 8601 format. This is the initial internal Research MVP
model set, not a long-term externally versioned serialization protocol.

Shared tests use deterministic factories in `tests/fixtures/domain.py` only
when multiple test modules need the same construction. Fixtures default to
function scope, expose important values, and do not use current time, random
data, environment variables, network access, or filesystem access. Single-test
data stays in its test module.

The models do not define orders, trades, accounts, positions, strategies,
factors, backtest events, exchange adapters, storage schemas, timeframes,
calendars, precision systems, API DTOs, or a fixture platform.
