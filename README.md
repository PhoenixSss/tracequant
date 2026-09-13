# TraceQuant

TraceQuant v2 is an auditable quantitative research-to-live project for
cryptocurrency perpetual futures. The current repository is deliberately only
a clean bootstrap: it pins the approved NautilusTrader runtime, establishes
package ownership, and provides mechanical guards for that boundary.

There is no strategy, data ingestion, backtest workflow, exchange connectivity,
order submission, Demo mode, or Live mode in this tree. Live trading is not
approved and cannot be enabled by configuration.

## Bootstrap environment

TraceQuant supports CPython 3.13 and uv 0.12.1. Create the exact development
environment from an official `nautilus-trader==2.0.0rc4` binary wheel:

```bash
uv sync --locked --dev --no-build-package nautilus-trader --no-cache
```

A missing compatible wheel is a hard failure; do not relax the Python target or
allow a source build. Run the repository checks with:

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tests
```

## Ownership and safety

All TraceQuant production Python is under `src/tracequant`. Direct imports of
the external `nautilus_trader` package are confined to
`tracequant.integrations.nautilus`; the package is installed into the generated
environment and is never vendored into this repository.

Persistent raw data, catalogs, caches, runs, evidence, audit data, model
artifacts, and credentials must use explicit external roots. The bootstrap does
not implement those roots or silently fall back to paths inside the checkout.
See the [repository structure](docs/architecture/repository-structure.md),
[runtime decision](docs/architecture/adr-0001-nautilustrader-primary-runtime.md),
and [dependency policy](docs/guides/nautilustrader-import-and-update-policy.md).

## v1 recovery

The final v1 tree remains recoverable from the immutable annotated tag and
GitHub Release
[`tracequant-v1-archive-2026-09-12`](https://github.com/PhoenixSss/tracequant/releases/tag/tracequant-v1-archive-2026-09-12).
Its peeled commit is `27a9fdd877533f933cde4818eba9c186d286c529` and its
tree is `8406076ce81a2476005e3c938abd4c5a088373c2`. The v2 bootstrap is a
normal descendant and does not modify that recovery identity.
