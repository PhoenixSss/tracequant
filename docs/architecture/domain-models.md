# Initial public domain models

TraceQuant's Research MVP exposes its first internal public domain models from:

```python
from tracequant.domain import InstrumentId, OHLCVBar, TimeRange
```

`InstrumentId` normalizes a single-market symbol to at most 32 trimmed ASCII uppercase
letters and digits. `TimeRange` represents a timezone-aware UTC half-open interval
`[start, end)`. `OHLCVBar` binds an instrument and interval to finite `float` OHLCV
values, a non-negative volume, and consistent high/low bounds. Aware non-UTC inputs
follow the existing `tracequant.core.time` contract and are normalized to UTC; naive
datetimes are rejected.

Each immutable model provides explicit `to_dict` and `from_dict` methods. Their output
contains only stable public values, uses the project's UTC ISO 8601 format, and can be
handled directly by Python's standard `json` module. These are internal Research MVP
interfaces, not a promise of long-term external serialization compatibility.

Prices are intentionally allowed to be zero or negative at this boundary. These models
do not model venues, market types, base/quote parsing, tick or lot sizes, timeframes,
trade counts, VWAP, exchange metadata, orders, accounts, or persistence schemas.

Tests keep one-off values in their owning test module. Deterministic constructors that
are genuinely reused across modules live in `tests/fixtures/domain.py`; `conftest.py`
only exposes those small factories as function-scoped pytest fixtures. Callers should
override business-significant symbols, times, and prices explicitly rather than hide
them behind a larger fixture platform.
