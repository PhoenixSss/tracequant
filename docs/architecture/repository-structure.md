# TraceQuant v2 repository structure

This document is the current ownership contract for the clean TraceQuant v2
bootstrap. The bootstrap proves source and dependency separation; it does not
provide a strategy, data pipeline, backtester, exchange connection, or trading
mode.

## Tracked layout

Every tracked top-level path has one purpose and owner:

| Path | Purpose | Owner |
| --- | --- | --- |
| `.github/` | Repository-native CI and Issue metadata | Maintainers |
| `config/` | Safe configuration boundary documentation | TraceQuant configuration owner |
| `docs/` | Current architecture, dependency policy, evidence, and release identity | Maintainers and named capability owners |
| `src/` | The single production Python source root | TraceQuant engineering |
| `tests/` | Product and architecture acceptance tests | TraceQuant engineering |
| `.env.example` | Safe statement of the currently empty environment surface | TraceQuant configuration owner |
| `.gitignore` | Closed set of disposable checkout-local output and secret-shaped local files | Maintainers |
| `.python-version` | Exact CPython 3.13 interpreter selection | Maintainers |
| `LICENSE` | TraceQuant source license | Maintainers |
| `README.md` | Bootstrap entry point and safety status | Maintainers |
| `pyproject.toml` | Package, Python, dependency, and quality-tool metadata | Maintainers |
| `uv.toml` | Wheel-only installation policy for NautilusTrader | Maintainers |
| `uv.lock` | Reproducible dependency resolution | Maintainers |

There is no top-level `apps/`, `packages/`, `scripts/`, `tools/`, `deploy/`,
`runtime/`, `data/`, `artifacts/`, or `vendor/` product root. A later scoped
capability may add only a path already assigned below or amend this architecture
through an explicit decision.

## Python and dependency ownership

The complete production package is initially:

```text
src/tracequant/
  __init__.py
  integrations/
    __init__.py
    nautilus/
      __init__.py
```

All self-developed production Python belongs below the single `tracequant`
namespace. The installed `nautilus_trader` namespace belongs to the official
`nautilus-trader==2.0.0rc4` distribution in environment `site-packages`.
Upstream source, tests, examples, bindings, and package-shaped copies never
belong in this repository.

`tracequant.integrations.nautilus` is the only production boundary permitted to
import `nautilus_trader`. Its bootstrap implementation exposes only explicit
distribution/import identity queries. Those calls prove separation without
creating a generic adapter, trading domain, runtime wrapper, or import-time side
effect.

Later scoped Issues may add these product boundaries only when implementing the
corresponding capability:

```text
src/tracequant/
  source_data/                 immutable source provenance and acquisition
  research/                    read-only views, features, labels, and models
  integrations/nautilus/
    strategies/                concrete Nautilus Strategy/Actor code
    configuration/             concrete Nautilus runtime configuration
  operations/                  admission, observation, alerts, and release
```

NautilusTrader owns trading types, instruments, orders, positions, portfolio and
account state, core pre-trade risk, execution, fills/accounting, catalogs,
runtime cache, restart, and reconciliation. TraceQuant owns source provenance,
causal research semantics, models and strategy intent, project-specific policy
thresholds, environment admission, acceptance evidence, and operator approval.
The project must not introduce parallel mutable trading DTOs, a second risk or
execution engine, a generic strategy adapter, or an exchange client used by a
strategy.

## Import and safety rules

Imports must not perform I/O, read environment variables, create directories,
construct clients, start background work, or cache global singletons. Any future
configuration, logging, file creation, model loading, or network action must be
an explicit call at its owning boundary.

Live is absent and cannot be enabled by this bootstrap. A future opening order
must use public Nautilus Strategy/OrderFactory APIs and must survive both
Nautilus core risk and any confirmed-missing TraceQuant policy. Unknown policy
state, stale data, local/venue disagreement, or unreconciled order state cannot
be converted into approval.

## External persistent roots

Persistent or sensitive state is external/versioned, never ignored inside the
checkout. Future typed configuration must require absolute paths and immutable
identity partitions:

| Logical root | Required identity partition | Contents |
| --- | --- | --- |
| `raw_root` | `raw-contract/<contract>/<source>/<dataset>/<revision>/` | Immutable source bytes, checksums, and provenance |
| `catalog_root` | `nautilus/<runtime>/<schema>/<environment>/` | Nautilus-compatible catalog |
| `cache_root` | `nautilus/<runtime>/<schema>/<environment>/<instance>/` | Runtime cache and recovery state |
| `run_root` | `<environment>/<code>/nautilus/<runtime>/<lock>/<mode>/<run>/` | Logs, reports, and run manifest |
| `evidence_root` | `<environment>/<schema>/<subject>/<revision>/` | Research and acceptance evidence |
| `audit_root` | `<environment>/<schema>/<system>/<date>/` | Append-only operational decisions |
| `environment_root` | `<environment-lock-digest>/` | Environment exports, SBOM, and retained dependency evidence |

The initial immutable runtime identity is
`2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d`. Catalog, cache, and run
paths and manifests must repeat it. Missing or mismatched configured, imported,
path, schema, or manifest identity is a hard error and must not select `latest`,
reuse a legacy root, mutate an existing partition, or fall back into the
checkout. Raw inputs remain independent and immutable.

Secrets live in a secret manager or injected process boundary. They never
appear in examples, manifests, logs, tests, data roots, or Git.

## Generated checkout paths

The closed generated/ignored category is:

```text
.venv/
.cache/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
build/
dist/
*.egg-info/
__pycache__/
.env
.env.*.local
*.local.toml
```

The repository deliberately does not ignore local `data/`, `catalog/`,
`cache/`, `runs/`, `artifacts/`, `evidence/`, `audit/`, `models/`, or `logs/`
fallbacks. Such names could hide a missing external-root configuration.

## Mechanical guards

The bootstrap acceptance suite enforces the closed top-level tree, the single
production namespace and source root, the exact wheel-only dependency and PyPI
lock source, the Nautilus import boundary, the absence of retired workflow and
v1 business paths, the generated-path allowlist, and import-time safety. The
Critical Outcome test additionally proves that TraceQuant resolves from this
checkout while NautilusTrader resolves from the external environment.

Any later source/configuration expansion must extend tests at the boundary it
introduces. Persistent-root creation must add absolute-path, environment, mode,
schema, and runtime-identity failure tests. Strategy work must add public API,
risk-veto, stale/unknown state, reconciliation, and no-exchange-client tests.
Until then, the closed skeleton prevents those unimplemented capabilities from
being implied by empty scaffolding.
