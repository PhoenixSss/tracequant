# TraceQuant repository structure

This document separates the current v1/bootstrap tree from the approved v2
target structure. The v2 section is the normative input for the v2 bootstrap;
it does not claim that the target tree, NautilusTrader integration, or any
trading capability already exists.

## Current tree

```text
src/tracequant/
  config.py
  logging.py
  core/time.py
  domain/models.py
  contracts/review.py
  data/public_history.py
  data/raw_store.py
  data/binance_contract_kline.py
tests/
  test_config.py
  test_logging.py
  core/test_time.py
  test_domain_models.py
  test_domain_acceptance.py
  fixtures/domain.py
  tools/                         repository workflow tests
apps/
  research/                      boundary README only
  runtime/                       boundary README only
  console/                       boundary README only
packages/
  contracts/                     boundary README and contract docs
  domain/                        boundary README only
  adapters/                      boundary README only
deploy/
  research/                      deployment boundary README only
  staging/                       deployment boundary README only
  live/                          deployment boundary README only
docs/                            architecture and project documentation
```

`src/tracequant/` is deliberately the current bootstrap package. Moving these
modules into a product package requires a separate behavior-preserving change
with import compatibility validation. The repository is not currently split
into independently installable application or package projects.

`tracequant.contracts` contains the current provider-neutral Review vNext value
contracts. The contracts are versioned and JSON round-trippable, while detailed
evidence remains behind explicit retrieval references.

## Current dependency direction

The direct imports in the implemented package are intentionally small:

```text
Python standard library
├── tracequant.config
├── tracequant.core.time
└── tracequant.logging ──> tracequant.config
                         └> tracequant.core.time

tracequant.domain.models ──> tracequant.core.time

tracequant.data.public_history ──> tracequant.domain
tracequant.data.raw_store ──────> tracequant.data.public_history
                             └──> tracequant.core.time
                             └──> Polars
tracequant.data.binance_contract_kline ──> tracequant.data.public_history
                                      ├──> tracequant.data.raw_store
                                      ├──> tracequant.domain
                                      └──> Polars + Python HTTP/ZIP/CSV libraries

tests ──> tracequant public APIs
tests ──> tests/fixtures/domain.py ──> tracequant.domain
```

The diagram is an import graph, not a claim that fixtures are part of the
runtime. The responsibility layers can be read as configuration and time
primitives supporting logging, while domain models depend on the UTC
primitives; test factories remain a separate test-support layer.

### Allowed boundaries

- `tracequant.config` owns explicit settings parsing. It reads process
  environment values only when `load_settings()` is called; it has no network,
  filesystem, dotenv, or global-singleton behavior.
- `tracequant.core.time` owns timezone-aware UTC conversion, parsing, and
  formatting. It depends only on the standard library.
- `tracequant.logging` owns explicit project logging setup. It may depend on
  configuration types and UTC formatting, but importing it must not configure
  handlers, create directories, or open files.
- `tracequant.domain` owns immutable, risk-independent initial market-data
  value models. It may depend on `core.time`, but not on exchanges, network
  clients, logging setup, UI code, deployment code, or test fixtures.
- `tracequant.data` owns typed source/request contracts, immutable local Raw
  Parquet/manifest persistence, durable non-completed acquisition manifests,
  and the Binance public-archive adapter. The adapter supports only USDⓈ-M
  BTCUSDT and ETHUSDT 1m contract Klines and may perform bounded HTTP,
  checksum verification, ZIP/CSV parsing, and filesystem persistence only
  after an explicit caller invocation. Importing the package performs no I/O,
  creates no directories, and starts no background work.
- `tests` may import public production APIs and test-only factories. Fixtures
  must remain deterministic, function-scoped where exposed by `conftest.py`,
  and independent of production runtime imports.

## Future product boundaries

When implemented by separately scoped Issues, the directory boundaries mean:

- `apps/research`: offline research orchestration and reproducible reports;
  never production exchange writes;
- `apps/runtime`: explicit Shadow, Demo, and Live orchestration, with live
  disabled by default and fail-closed safety gates;
- `apps/console`: operator UI and control-plane code;
- `packages/contracts`: stable cross-boundary schemas and interfaces;
- `packages/domain`: broader domain invariants that remain independent of
  exchange and transport details;
- `packages/adapters`: exchange, storage, database, filesystem, transport, and
  vendor integrations. Venue-specific semantics terminate here;
- `deploy/research`, `deploy/staging`, and `deploy/live`: explicit,
  environment-specific deployment assets.

None of these future scaffold directories currently provides additional data
ingestion, factors, backtesting, model training, order execution, account
state, risk decisions, or multi-exchange support. The narrow archive path in
`tracequant.data` is the only implemented ingestion capability.

## Import and side-effect rules

Modules must not perform I/O, read environment variables, create directories,
or cache global singletons during import. Configuration, logging setup, file
creation, and future network/exchange operations must be explicit calls owned by
the appropriate application or adapter boundary. Secrets must remain outside
source, tests, documentation, and logs; domain models must remain independent
of secret and transport concerns.

Physical repository extraction is not the default. Consider it only when
independent release cadence, a security boundary, dependency isolation, or
independent Console deployment creates sustained operational value.

## Approved v2 target structure

The v2 bootstrap must create only the tracked skeleton described here. As part
of that skeleton it establishes the declared Python baseline, the exact
official NautilusTrader wheel dependency, the uv source-build policy, the
committed lock, and the minimal integration seam needed to prove package and
import ownership. It may resolve and install the locked wheel into a generated,
disposable project environment solely for bootstrap acceptance. It must not
copy v1 modules into that skeleton, initialize persistent runtime data,
implement a trading workflow, or imply that Offline, Shadow, Demo, or Live
operation is available. Empty directories are represented by a short
`README.md` only when the bootstrap needs to preserve a boundary in Git.

### Classification vocabulary

Every v2 path has exactly one storage classification:

- **Tracked**: source, tests, safe configuration, documentation, or project
  metadata committed to Git.
- **Generated/ignored**: disposable developer output below the checkout. It is
  covered by `.gitignore`, is never an input of record, and may be deleted and
  recreated.
- **External/versioned**: persistent or sensitive state outside the checkout.
  Its identity and retention are explicit; `.gitignore` is not its safety
  boundary.

No path may change classification at runtime. In particular, failure to
configure an external root must fail closed rather than fall back to a
similarly named directory in the repository.

### Tracked top-level layout

These are all proposed top-level entries in the v2 checkout. A bootstrap may
omit an entry marked conditional until the Issue that needs it, but it may not
invent another top-level product-code root.

| Top-level path | One purpose | Owner | Classification |
| --- | --- | --- | --- |
| `.github/` | Repository-native CI, Issue, and dependency-maintenance metadata | Maintainers | Tracked |
| `config/` | Non-secret schemas, safe defaults, and reviewed examples; never host state or enabled-Live credentials/configuration | TraceQuant configuration owner | Tracked |
| `docs/` | Current product architecture, operator guidance, decisions, and versioned research conclusions | Maintainers and the capability owner named by each document | Tracked |
| `src/` | Python source-container whose only importable top-level package is `tracequant` | TraceQuant engineering | Tracked |
| `tests/` | Product tests mirroring `src/tracequant`; test support is not a production import source | TraceQuant engineering | Tracked |
| `.env.example` | Names and safe placeholders for supported environment variables; never values or automatic dotenv loading | TraceQuant configuration owner | Tracked |
| `.gitignore` | Exhaustive checkout-local generated-path exclusions and secret-shaped local files | Maintainers | Tracked |
| `.python-version` | Exact initial v2 interpreter selection: CPython `3.13` | Maintainers | Tracked |
| `LICENSE` | TraceQuant source license | Maintainers | Tracked |
| `README.md` | Project entry point, safety status, and links to current documentation | Maintainers | Tracked |
| `pyproject.toml` | Build metadata, the `tracequant` package declaration, `requires-python = ">=3.13,<3.14"`, dependency groups, tool configuration, and any explicitly scoped console entry points | Maintainers | Tracked |
| `uv.toml` | uv installation policy, including the NautilusTrader wheel-only constraint; never a machine-specific or LCK cache location | Maintainers | Tracked |
| `uv.lock` | Reproducible Python dependency resolution | Maintainers | Tracked |

There is no top-level `apps/`, `packages/`, `scripts/`, `runtime/`, `data/`,
`artifacts/`, or `vendor/` product root in v2. Operational Python entry points
are declared in `pyproject.toml` only when a scoped capability needs them and
resolve to a function in the owning boundary; they do not justify a generic
entrypoint or orchestration framework. Non-Python deployment material is added
only by a later scoped Issue; it does not justify a second Python source tree.

### `src/tracequant` layout and ownership

All importable, self-developed production Python belongs below the single
`tracequant` namespace. The bootstrap creates only the dependency boundary
needed to prove that separation:

```text
src/tracequant/
  __init__.py
  integrations/
    __init__.py
    nautilus/
      __init__.py              external distribution identity and import seam
```

Later scoped Issues may add only the following product boundaries when their
capability is actually implemented. They are not bootstrap scaffolding and do
not authorize framework-like abstractions:

```text
src/tracequant/
  source_data/                 raw provenance, acquisition, checksum, coverage
  research/                    views, features, labels, models, artifacts
  integrations/
    nautilus/
      strategies/             concrete Nautilus Strategy/Actor implementations
      configuration/          Nautilus backtest/node/venue/risk configuration
  operations/                  admission, external observation, alerts, release
```

| Boundary | Owns | Must not own |
| --- | --- | --- |
| `tracequant.source_data` | Source URL/version, immutable raw bytes, checksums, acquisition policy, gap/coverage QA, and missing-data acquisition | A Binance exchange adapter, canonical trading-data schema, Nautilus catalog implementation, or trading-domain types |
| `tracequant.research` | Read-only research views, feature/label semantics, causal lineage, training/evaluation, and model artifacts | Mutable trading state, Order/Position/Portfolio types, a trading engine, or writes back into the Nautilus catalog |
| `tracequant.integrations.nautilus` | Direct use of the pinned public Nautilus APIs, source-to-Nautilus conversion, concrete Nautilus-native Strategy/Actor implementations, model loading/inference inside those implementations, and Nautilus configuration | A generic Strategy Adapter, model-to-runtime middleware, project Order/Position DTOs, a copied upstream package tree, or parallel trading state machines |
| `tracequant.operations` | Demo/Live admission, external black-box observation, alerts, redaction, release/rollback policy, and later deployment assets | Trading-state correction, a reconciliation engine, credential values, or a second account/order truth |
| Dedicated tests below `tests/` | Public-API compatibility, research/runtime parity, backtest and Demo acceptance, restart/reconciliation observation, and architecture enforcement | Production runtime code, shadow ledgers, or corrective trading behavior |
| NautilusTrader in environment `site-packages` | Trading data/instrument types, `ParquetDataCatalog`, runtime Bar aggregation and indicators, Strategy/Actor lifecycle, orders, positions, account/portfolio state, core pre-trade risk, fills/accounting/reports, Binance adapter, cache, restart, and reconciliation | TraceQuant research semantics, source provenance, model artifacts, project-specific policy thresholds, environment admission, or operator approval |

`tracequant.integrations.nautilus` is a boundary, not a vendor fork. It should
contain concrete, small Nautilus-native implementations and configuration, not
a generic adapter/port framework or directories named after NautilusTrader's
internal packages. Upstream modules, generated bindings, examples, fixtures,
and tests stay in the installed distribution in the environment's
`site-packages`; none may appear under `src/`, `tests/`, or `vendor/`.

### Dependency and order-authority direction

The allowed product flow is:

```text
source_data -> tracequant.integrations.nautilus data conversion
                                      |
                                      v
                 installed nautilus_trader typed objects / ParquetDataCatalog
                                      |
                                      v
                           read-only research views
                                      |
                                      v
                           model/feature artifacts
                                      |
                                      v
tracequant.integrations.nautilus.strategies -> Nautilus backtest / Demo / Live

tracequant.operations -----------------------> observe, gate, alert, release
```

Production strategy/model behavior is implemented directly as a thin public
Nautilus `Strategy` or `Actor` below
`tracequant.integrations.nautilus.strategies`. Model loading and inference stay
with the concrete strategy that consumes them; there is no generic
model-to-runtime middleware, parallel strategy runtime, or project-owned Order,
Position, Portfolio, or Account DTO layer. Orders are created and submitted
only through Nautilus `OrderFactory` and Strategy APIs, never through a
TraceQuant exchange client.

Nautilus `RiskEngine` owns core pre-trade validation, throttles, notional
limits, and trading state. TraceQuant supplies reviewed thresholds and only
small, confirmed-missing strategy/portfolio policies such as daily loss, total
exposure, or stale-data stops. Such a policy may reject or reduce a proposed
action before it reaches Nautilus but must not become a generic second
RiskEngine. Neither the project policy nor Nautilus may turn the other's veto
into approval. Missing/unknown policy state, stale data, local/exchange
disagreement, or unreconciled order state produces no opening order.

Live is absent or disabled by default in every tracked example. Adding a Live
entry point requires a separate Issue, explicit configuration, the admission
evidence required by
[ADR-0001](adr-0001-nautilustrader-primary-runtime.md#live-admission-remains-closed),
and human approval. Unknown configuration or runtime identity fails closed.

## External and local generated roots

### External persistent-root contract

Persistent data is never stored below the repository. Deployments must provide
absolute roots through typed configuration; the names below are logical fields,
not permission to read environment variables on import.

| Logical root | Required version/identity partition | Contents and owner | Classification |
| --- | --- | --- | --- |
| `raw_root` | `raw-contract/<contract_version>/<source>/<dataset_identity>/<revision>/` | Immutable response bytes/Parquet, checksums, provenance, and completion manifests; TraceQuant data owner | External/versioned |
| `catalog_root` | `nautilus/<package_version>+<upstream_release_or_commit>/<catalog_schema_id>/<environment>/` | Nautilus-compatible catalog generated from verified raw revisions; Nautilus runtime owns catalog semantics, TraceQuant records conversion provenance | External/versioned |
| `cache_root` | `nautilus/<package_version>+<upstream_release_or_commit>/<cache_schema_id>/<environment>/<instance_id>/` | Nautilus cache/restart/reconciliation state; Nautilus runtime owner | External/versioned |
| `run_root` | `<environment>/<code_version>/nautilus/<package_version>+<upstream_release_or_commit>/<environment_lock_digest>/<mode>/<run_id>/` | Logs, reports, temporary run artifacts, and an immutable run manifest; TraceQuant operations owner | External/versioned |
| `evidence_root` | `<environment>/<evidence_schema_version>/<subject_identity>/<revision>/` | Research, backtest, Demo, and acceptance evidence with digests; producing capability owner | External/versioned |
| `audit_root` | `<environment>/<audit_schema_version>/<account_or_system_identity>/<date_partition>/` | Append-only operational decisions/events with retention and access control; TraceQuant operations owner | External/versioned |
| `environment_root` | `<environment_lock_digest>/` | Reproducible environment exports, wheel/source caches if retained, SBOMs, and license material; dependency owner | External/versioned |

`<environment>` is one of explicitly configured `research`, `shadow`, `demo`,
or (only after approval) `live`. It is never inferred from a directory's
existence. `run_id`, source revision, schema versions, runtime identity, and
digests are immutable identifiers, not mutable aliases such as `latest`.
Secrets live in a secret manager or injected process boundary, never in any of
these roots' manifests.

The exact Nautilus runtime identity is the normalized package version plus the
reviewed upstream release identity or exact source commit, using the canonical
form defined by the NautilusTrader import and update policy. Catalog, cache, and
run creation records that identity and the applicable schema or
environment-lock identifier in both the path and a manifest. On open,
TraceQuant compares configured identity, manifest identity, and imported
NautilusTrader identity. A missing value or mismatch is a hard error: it must
not open, migrate, copy, or silently reuse the state. An upgrade gets a new
partition and an explicit, separately validated migration or rebuild. This
prevents persistent runtime data from crossing NautilusTrader version
identities silently. The complete acquisition, upgrade, promotion, and rollback
rules are defined by the
[NautilusTrader import and update policy](../guides/nautilustrader-import-and-update-policy.md).

Raw data is versioned by its TraceQuant source contract rather than by a
Nautilus version so that upstream evidence remains immutable and runtime
independent. Every catalog conversion binds exact raw manifest/content digests,
conversion code version, target catalog schema, and Nautilus runtime identity.
Derived data never mutates or replaces its raw input.

### Generated/ignored checkout paths

The v2 `.gitignore` owns this closed category of disposable local outputs:

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

Local convenience state must remain small and reproducible. The repository
must not add ignored `data/`, `catalog/`, `cache/`, `runs/`, `artifacts/`,
`evidence/`, `audit/`, `models/`, or `logs/` fallbacks: those names could hide
misconfigured persistent state inside the checkout.

Only metadata that is useful without its external payload may be committed:
configuration schemas/examples, data or model interface schemas, small
redacted research conclusions, and manifests that contain stable logical IDs
and cryptographic digests. Machine-specific absolute paths, credentials,
account/position/order events, logs, raw/derived datasets, catalogs, caches,
model binaries, checkpoints, databases, run payloads, and operational evidence
remain external. A committed manifest must not make an unavailable payload
look present or verified.

## Explicit exclusions from v2

LCK is a delivery mechanism for the current repository, not part of the v2
product or its bootstrap input. The v2 tree must not contain or depend at
runtime, build time, test time, or documentation-generation time on:

- `.agents/`, `.claude/`, or `.workflow.local/` LCK policies, Skills, receipts,
  state, or validation artifacts;
- `tools/agent_workflow/`, LCK runners/helpers, or equivalent copied wrappers;
- `tests/tools/` or any LCK/workflow fixture and test;
- `docs/workflows/` or LCK operator/adoption documentation; or
- an `lck`, workflow-runner, or agent package/module declared as a project or
  development dependency.

The current repository keeps its existing LCK assets until an authorized
transition removes them. They are not transferred into the closed v2
top-level layout above. This Issue changes documentation only.

## Bootstrap mechanical acceptance contract

The v2 bootstrap Task has complete structural input when it implements the
following checks against its candidate tree. Tool choice is left to that Task,
but each assertion must be an automated test or CI/static check rather than a
review convention:

1. Package discovery exposes exactly the top-level production package
   `tracequant`; no `src/nautilus_trader`, `src/nautilus`, second source root,
   vendored upstream tree, or importable production Python outside
   `src/tracequant` exists.
2. Python outside `src/tracequant` is test code below `tests/` only. Tests may
   import `tracequant` public/test-support APIs; production code never imports
   `tests`.
3. `pyproject.toml` pins the exact approved official `nautilus-trader` wheel,
   the new v2 `uv.toml` rejects a NautilusTrader source build and contains no
   v1/LCK cache location, and `uv.lock` resolves the distribution from standard
   PyPI without a Git, path, editable, workspace, or source override. A clean,
   disposable environment can sync the committed lock and verify the installed
   distribution and module origin without an upstream source checkout.
4. Only `tracequant.integrations.nautilus` directly imports
   `nautilus_trader`; dedicated acceptance tests may also import the public
   symbols they verify. Source-data, research, and operations code cannot
   directly import that distribution. Any concrete catalog conversion,
   Nautilus-native strategy, or runtime configuration that needs the public
   upstream API belongs inside the integration boundary.
5. Tree and import checks reject a separate `tracequant.strategies`,
   `tracequant.risk`, or generic orchestration/adapter/port framework, along
   with project-owned trading-domain DTOs, exchange clients, RiskEngine,
   portfolio/account ledger, backtest engine, or reconciliation state machine.
6. The bootstrap contains no strategy, order, or exchange behavior. When a
   later scoped Issue adds a concrete strategy, static and behavior checks must
   confine it to the Nautilus integration boundary, require public Nautilus
   Strategy/OrderFactory APIs, reject exchange-client construction and risk
   bypasses, and prove rejected, unknown, stale, divergent, or unreconciled
   state cannot open a position.
7. Tree-policy checks reject all LCK paths/dependencies listed above, a copied
   NautilusTrader source/test layout, secret files, and repository-local
   persistent-root fallbacks.
8. Configuration tests require absolute external roots, explicit environment
   and mode, and exact Nautilus package/source/schema identity for catalog and
   cache opens. Missing/mismatched identity fails closed and never selects a
   legacy or `latest` directory.
9. Import-safety tests prove package imports perform no I/O, environment reads,
   directory creation, client construction, background startup, or singleton
   caching. Live remains unavailable or explicitly disabled without all later
   admission gates.

Bootstrap completion establishes only the repository skeleton, a verified clean
installation of the exact wheel-only NautilusTrader dependency and lock, the
minimal integration seam, and these guards. It does not authorize
persistent-data migration, strategy or backtest implementation, exchange
connectivity, order submission, Demo, or Live trading.
