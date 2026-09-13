# TraceQuant v2 technical baseline

The current product implementation is a clean, non-production bootstrap. The
runtime surface contains only the `tracequant` namespace and a minimal explicit
identity seam for the installed NautilusTrader distribution. Repository-only LCK
engineering tooling is approved outside this runtime and does not expand the
product surface described here.

## Environment

- CPython: `3.13` (`>=3.13,<3.14`)
- uv: `0.12.1`
- build backend: `uv_build>=0.12.1,<0.13.0`
- runtime dependency: exact official PyPI distribution
  `nautilus-trader==2.0.0rc4`
- upstream release: `v2.0.0rc4`, commit
  `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`
- source-build policy: `uv.toml` rejects builds of `nautilus-trader`

`uv.lock`, `pyproject.toml`, `uv.toml`, `.python-version`, and CI are the
mechanical authority for the installed environment. There is no Git, path,
editable, workspace, private-index, nightly, fork, or source-checkout override
for NautilusTrader.

## Implemented surface

`tracequant.integrations.nautilus` owns explicit distribution/import identity
queries, the stage 1 BTCUSDT 1h historical Bar path selected as `USE_NAUTILUS`,
and the stage 1 Nautilus-native MA-cross offline backtest. The catalog path
requests native Nautilus bars through the locked Binance public data client,
records source provenance, and writes/reads `ParquetDataCatalog`. The backtest
reads that catalog, runs one long-only SMA crossover Strategy, and writes
order, fill, position, account, and summary reports. `tracequant.source_data`
owns the frozen window, checksum, and provenance identity; it does not import
`nautilus_trader`.

Importing TraceQuant performs no I/O, environment read, directory creation,
client construction, background startup, or global singleton initialization.
Identity queries, catalog writes, backtests, and Nautilus requests perform
their work only when called.

The current product surface is `OFFLINE_BACKTEST_ONLY` and `LIVE_NOT_APPROVED`.
Public historical data requests do not use account credentials or a Binance
execution client. The backtest does not connect to an exchange.

## Quality baseline

The clean installation gate is:

```bash
uv sync --locked --dev --no-build-package nautilus-trader --no-cache
```

The canonical checks are:

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tools/lck tests
```

CI runs the clean no-cache wheel-only sync before these checks. The acceptance
suite verifies distribution version and origin, lock provenance, package/tree
ownership, ignored-path policy, and the absence of unimplemented or retired
code boundaries.

## Change control

Dependencies, source boundaries, persistent storage, configuration, and trading
capabilities require separately scoped changes. An unavailable official wheel,
unsupported platform, missing external identity, ambiguous ownership, or failed
guard stops the change rather than enabling a source build or local fallback.

Live remains unavailable until its later architecture, Demo, reconciliation,
accounting, soak, security, release, and explicit human-approval gates exist and
pass. No current file is a Live enablement mechanism.
