# Configuration boundary

The bootstrap has no runtime configuration schema and no enabled trading mode.
Later capabilities may add reviewed, non-secret schemas and examples here.

Persistent state must never fall back into the checkout. When introduced, typed
configuration must require absolute external `raw_root`, `catalog_root`,
`cache_root`, `run_root`, `evidence_root`, `audit_root`, and `environment_root`
values. Catalog and cache access must also require an explicit environment,
mode, schema identity, and the exact Nautilus runtime identity
`2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d`. Missing, relative,
unknown, or mismatched values must fail closed.
