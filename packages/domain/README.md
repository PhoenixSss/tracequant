# TraceQuant Domain

Core quantitative-trading domain models, invariants, and risk-independent business rules belong here as they are introduced.

Domain code must not depend on exchange/network clients, UI code, or deployment implementations.

The Research MVP's initial Python domain boundary is exposed as
`tracequant.domain` from the current bootstrap package. Moving it into a
separately built package requires a dedicated compatibility-preserving Task.
