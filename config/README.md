# Configuration boundary

Tracked, non-secret example schemas may live here. The stage 2 BTC/ETH bar
dataset example is `stage2_btceth.example.toml`.

Persistent state must never fall back into the checkout. Typed configuration
must require absolute paths outside the repository. The stage 2 bar dataset
requires existing absolute `raw_root` and `catalog_path` values. Later
capabilities may also require `catalog_root`, `cache_root`, `run_root`,
`evidence_root`, `audit_root`, and `environment_root`. Catalog and cache access
must also require an explicit environment, mode, schema identity, and the exact
Nautilus runtime identity
`2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d`. Missing, relative,
unknown, or mismatched values must fail closed.

Secrets live outside Git. They never appear in examples, manifests, or tests.
