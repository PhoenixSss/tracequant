# Configuration boundary

Tracked, non-secret example schemas may live here. The stage 2 BTC/ETH bar
dataset example is `stage2_btceth.example.toml`; the read-only Stage 3
feature/label input example is `stage3_features.example.toml`.

The immutable publication locator for the accepted Stage 2 r1 catalog is
`datasets/binance-usdm-btceth-202001-202608-r1.lock.json`. It binds a fixed
GitHub Release tag and asset, archive size/hash, every extracted file, and all
accepted dataset identities. It is independent of the historical acceptance
record and never uses a `latest` alias or a machine-local path.

The disaster-recovery-only source escrow locator is
`datasets/binance-usdm-btceth-202001-202608-r1.sources.lock.json`. It binds a
separate fixed Release containing exactly the accepted 818 Binance ZIP objects
and their 818 official checksum files. It is not a second catalog or a supported
research/backtest input; normal consumers continue to materialize the catalog
lock above.

Persistent state must never fall back into the checkout. Typed configuration
must require absolute paths outside the repository. The stage 2 bar dataset
requires existing absolute `raw_root` and `catalog_path` values. Stage 3 feature
loading requires explicit `catalog_path`, `evidence_root`, and `run_root` values
plus the complete accepted Stage 2 identity; its config path is supplied only
through `TRACEQUANT_STAGE3_CONFIG`. Later capabilities may also require
`catalog_root`, `cache_root`, `audit_root`, and `environment_root`. Catalog and cache access
must also require an explicit environment, mode, schema identity, and the exact
Nautilus runtime identity
`2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d`. Missing, relative,
unknown, or mismatched values must fail closed.

Secrets live outside Git. They never appear in examples, manifests, or tests.
