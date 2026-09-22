# TraceQuant v2 repository structure

This document is the current ownership contract for the TraceQuant v2 product
bootstrap and its repository engineering tooling. The product runtime pins
NautilusTrader, owns source provenance, implements the stage 1 BTCUSDT 1h
catalog path and the accepted stage 2 BTC/ETH Nautilus-homologous dataset path,
and runs one Nautilus-native offline MA-cross backtest plus read-only Polars
research views over that stage 2 catalog. Stage 3 includes the accepted catalog
binding, finite causal feature/label contract, both fixed Strategies,
deterministic LightGBM artifacts, the fixed expanding-window and accounting-only
sensitivity evaluation, and the finite synchronous OOS rebuild which projects
that evidence into a strict compact acceptance record. Stage 4 adds the narrow
offline Binance Demo configuration and admission boundary described below; it
does not provide a network client, an execution connection, a connected Demo
runtime, or Live mode. LCK is an approved repository capability outside that
product runtime.

## Tracked layout

Every tracked top-level path has one purpose and owner:

| Path | Purpose | Owner |
| --- | --- | --- |
| `.github/` | Repository-native CI and Issue metadata | Maintainers |
| `.agents/` | Canonical Agent procedures and LCK execution/evidence policies | LCK maintainers |
| `.claude/` | Claude provider Skills mirrored from canonical Agent procedures | LCK maintainers |
| `.codex/` | Thin Codex execution-policy adapter | LCK maintainers |
| `config/` | Safe configuration boundary documentation | TraceQuant configuration owner |
| `docs/` | Current architecture, dependency policy, evidence, and release identity | Maintainers and named capability owners |
| `src/` | The single production Python source root | TraceQuant engineering |
| `tests/` | Product, architecture, and isolated repository-tooling tests | TraceQuant engineering and LCK maintainers |
| `tools/lck/` | Repository-only Local Control Kernel implementation and configuration | LCK maintainers |
| `AGENTS.md` | Agent entry routing and repository ownership boundaries | Maintainers |
| `CLAUDE.md` | Claude provider adapter to `AGENTS.md` and `.claude/skills/` | Maintainers |
| `.gitattributes` | Cross-platform tracked-text normalization | Maintainers |
| `.env.example` | Safe statement of the currently empty environment surface | TraceQuant configuration owner |
| `.gitignore` | Closed set of disposable checkout-local output and secret-shaped local files | Maintainers |
| `.python-version` | Exact CPython 3.13 interpreter selection | Maintainers |
| `LICENSE` | TraceQuant source license | Maintainers |
| `README.md` | Bootstrap entry point and safety status | Maintainers |
| `pyproject.toml` | Package, Python, dependency, and quality-tool metadata | Maintainers |
| `uv.toml` | Wheel-only installation policy for NautilusTrader | Maintainers |
| `uv.lock` | Reproducible dependency resolution | Maintainers |

There is no top-level `apps/`, `packages/`, `scripts/`, `deploy/`,
`runtime/`, `data/`, `artifacts/`, or `vendor/` product root. A later scoped
capability may add only a path already assigned below or amend this architecture
through an explicit decision.

`tools/` is not a product source root. Its only approved tracked subtree is
`tools/lck/`. LCK tests are confined to `tests/tools/lck/`, its normative
workflow documents to `docs/workflows/lck/`, and its user guides to
`docs/guides/lck/`. Product implementation and product-specific configuration
must not be placed in those LCK-owned paths.

The initial bootstrap text that excluded `.agents/`, `.claude/`, `.codex/`,
`tools/`, `tests/tools/`, and `docs/workflows/` as a class is superseded. The
exact LCK-owned roots listed above are part of the approved tracked layout;
unrelated trees under those broad names are not implicitly authorized.

## Repository-tooling ownership

LCK is an on-demand repository engineering tool, not part of the installed
`tracequant` distribution. `tools.lck` must not import `tracequant` or
`nautilus_trader`; product modules must not import `tools.lck`. Provider-neutral
Review value contracts live in `tools/lck/review_models.py`, not in the product
namespace. PyYAML is a development/tooling dependency and is not a TraceQuant
runtime dependency.

The stable entry is `uv run --frozen python -m tools.lck`. Detailed lifecycle
semantics live under `docs/workflows/lck/`; this architecture document owns only
the product/tooling boundary.

## Python and dependency ownership

The complete production package is:

```text
src/tracequant/
  __init__.py
  source_data/
    __init__.py
    stage1_btcusdt.py
    stage2_btceth.py
  research/
    __init__.py
    source_schema.py
    stage3_artifacts.py
    stage3_features.py
    views.py
  integrations/
    __init__.py
    nautilus/
      __init__.py
      stage1_btcusdt.py
      stage1_backtest.py
      stage2_artifact.py
      stage2_btceth.py
      stage2_source_artifact.py
      stage3_evaluation.py
      stage3_model.py
      stage3_momentum.py
      stage3_oos.py
      stage4_demo.py
      stage4_demo_data.py
      stage4_demo_evidence.py
      stage4_demo_execution.py
      strategies/
        __init__.py
        stage1_ma_cross.py
        stage3_model.py
        stage3_momentum.py
```

All self-developed production Python belongs below the single `tracequant`
namespace. The installed `nautilus_trader` namespace belongs to the official
`nautilus-trader==2.0.0rc4` distribution in environment `site-packages`.
Upstream source, tests, examples, bindings, and package-shaped copies never
belong in this repository.

`tracequant.integrations.nautilus` is the only production boundary permitted to
import `nautilus_trader`. Identity queries remain explicit and side-effect free.
Stage 1 catalog ingest, the stage 2 BTC/ETH public-data bar catalog, and the
offline MA-cross backtest live in use-case modules beside that seam; they are
not a generic adapter, trading domain, or import-time runtime wrapper.

`integrations/nautilus/strategies/` holds the stage 1 MA-cross Strategy, the
fixed-parameter Stage 3 traditional momentum Strategy, and the thin Stage 3
LightGBM signal Strategy. The adjacent `integrations/nautilus/stage3_momentum.py`
and `integrations/nautilus/stage3_model.py` modules own their accepted-catalog
offline base-run entries and immutable fact outputs. The finite
`integrations/nautilus/stage3_evaluation.py` use case composes those boundaries
into the fixed expanding-window matrix and accounting-only replay; it is not a
generic workflow engine or strategy adapter. The model Strategy reuses
the momentum capability's Nautilus order, reversal, fee, funding, account, and
terminal-state path; it adds no model gateway or parallel trading state.
`integrations/nautilus/stage3_oos.py` is the finite product use case which
sequentially invokes those existing Stage 3 capabilities. It accepts only
explicit locked identity and absolute external roots, creates no scheduler or
resume layer, and writes only the compact tracked acceptance projection in the
checkout; models and full run evidence remain external.
`integrations/nautilus/stage4_demo.py` and its finite
`stage4_demo_evidence.py` companion own the bounded offline Binance USD-M
Futures Demo boundary: fixed runtime/configuration and credential admission,
pure normal-order and exact reduce-only quantity contracts, fixed monotonic
deadlines, the secret-free frozen configuration digest, and the two frozen
Stage 4 evidence schemas with fail-closed pure validation. They may construct
the approved Nautilus execution-client configuration only from an admitted
attempt, but do not create a network client, connect to Demo or Live, submit
orders, fetch market or account state, aggregate or publish acceptance, or
orchestrate execution. `stage4_demo_data.py` is the one bounded exception for
public Demo market data: it composes the official rc4 `DataTester` with one
Binance USD-M Demo data client, observes only the locked instrument through the
public cache, and writes one EvidenceV1 record to a fresh external partition.
It creates no execution client and does not read credentials.
`stage4_demo_execution.py` is the matching bounded order-enabled exception. It
builds two independently admitted logical attempts from the official rc4
`ExecTester`: one market-canary/exact-reduce-only-close attempt and one fresh
canary followed by a single passive post-only accept/cancel attempt. Its plan is
Demo-only, sequential, capped at one active or inflight order, and permits a
failure-only exact cleanup phase only after terminal-order and zero-order proof.
Because rc4 registers the built-in tester before `LiveNode` starts, the entry
freezes quantity and expected passive price from a qualified attempt-local public
input before constructing the order-enabled node, then requires the runtime cache
to prove the same constraints and exact submitted action identity. Runtime price
drift, a non-reduce-only or inexact close, and any passive terminal status other
than `CANCELED` are conflicting observations and cannot produce PASS.
It adapts only Nautilus-owned public cache/account facts into one fresh external
EvidenceV1 partition per attempt; it does not expose tester-only execution
privilege to ordinary Strategy code or introduce a reusable orchestrator.
`tracequant.research` owns
read-only Polars views, time splits, the finite Stage 3 causal feature/label
contract, and the LightGBM training/artifact/loading boundary over the stage 2
Nautilus catalog. The model boundary writes only caller-selected absolute
external empty partitions, uses LightGBM's native text format, and does not own
strategy, order, position, account, or execution state. That catalog is the
accepted `binance-usdm-btceth-202001-202608-r1` dataset; its tracked identity lives in
`docs/product/stage2-btceth-dataset-acceptance.json`, and the stage 3
requirements that consumers bind to are in
`docs/product/stage-3-strategy-and-model-requirements.md`. A successful
ordinary catalog identity check does not by itself prove that a catalog is that
accepted dataset: sidecar declarations only bind identity, so the formal loader
also binds the bar/mark/funding rows it actually reads to the accepted coverage
grid, accepting omissions only where that coverage records an explained gap.

Later scoped Issues may add these remaining product boundaries only when
implementing the corresponding capability, in that Issue's scope and against
its accepted requirements:

```text
src/tracequant/
  research/                    later model/evaluation capabilities beyond the first artifact
  integrations/nautilus/
    configuration/             concrete Nautilus runtime configuration
    strategies/                additional strategies beyond the implemented stage 1/3 Strategies
  operations/                  admission, observation, alerts, and release
```

These remaining capabilities are not implemented today. Listing them here
assigns ownership only; it creates no scaffolding. `strategies/` already exists
for the stage 1 MA-cross, Stage 3 traditional momentum, and Stage 3 LightGBM
signal Strategies, while `configuration/` and `operations/` are absent entirely.

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
.agents/execution-profile.local.toml
.agents/evidence.local/
.agents/validation.local/
.workflow.local/
```

The three LCK output roots are bounded, ignored repository-tooling state. They
are not product persistence, do not satisfy an `evidence_root` or `run_root`, and
never replace live Git/GitHub authority.

The repository deliberately does not ignore local `data/`, `catalog/`,
`cache/`, `runs/`, `artifacts/`, `evidence/`, `audit/`, `models/`, or `logs/`
fallbacks. Such names could hide a missing external-root configuration.

## Mechanical guards

The acceptance suite enforces the approved product-and-LCK top-level layout, the
single production namespace and source root, the exact wheel-only dependency
and PyPI lock source, the Nautilus import boundary, the isolated LCK tooling allowlist,
the absence of retired workflow and v1 business paths, the generated-path
allowlist, and import-time safety. The
Critical Outcome test additionally proves that TraceQuant resolves from this
checkout while NautilusTrader resolves from the external environment.

Any later source/configuration expansion must extend tests at the boundary it
introduces. Persistent-root creation must add absolute-path, environment, mode,
schema, and runtime-identity failure tests. Strategy work must add public API,
risk-veto, stale/unknown state, reconciliation, and no-exchange-client tests.
Until then, the product-runtime skeleton prevents those unimplemented product
capabilities from being implied by empty scaffolding; it does not prohibit the
approved repository-only LCK capability.
