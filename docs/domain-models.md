# Initial research domain models

The public path `quant_system.core` exposes three immutable Research MVP value
objects: `InstrumentId`, `TimeRange`, and `OHLCVBar`.

`InstrumentId` trims and uppercases one through thirty-two ASCII letters or
digits. It intentionally has no venue, exchange, market-type, or quote parsing.

`TimeRange` represents the half-open UTC interval `[start, end)`. Its datetime
normalization and parsing reuse `quant_system.core.time`; aware offsets are
normalized to UTC and naive values are rejected. It provides `duration` and
`contains` with the half-open boundary semantics.

`OHLCVBar` contains only `instrument`, `start`, `end`, `open`, `high`, `low`,
`close`, and `volume`. Prices and volume are finite Python `float` values,
volume is non-negative, and the OHLC envelope is validated. Zero and negative
prices remain allowed by design for this Research MVP; positivity and monetary
precision are outside this boundary.

Each model has an explicit JSON-compatible representation. Datetime values use
the existing UTC formatter with an ISO 8601 `Z` suffix. The representation is a
stable field mapping rather than a dataclass or pickle protocol.

Shared deterministic test factories live in `tests/fixtures/domain.py` and
function-scoped pytest fixtures live in `tests/conftest.py`. Factories accept
explicit field overrides and never use current time, randomness, environment,
network, files, or shared mutable objects.

This document describes the initial internal Research MVP boundary; it is not a
long-term external serialization compatibility promise.
