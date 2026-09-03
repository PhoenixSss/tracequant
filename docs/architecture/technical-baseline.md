# TraceQuant technical baseline

- **Version:** 1.2
- **Date:** 2026-09-03
- **Repository:** `PhoenixSss/tracequant`
- **Status:** current implementation facts plus explicitly deferred boundaries

This document separates what is implemented on the current `main` line from
the architecture that later Issues may introduce. The current code and tests,
`pyproject.toml`, `uv.lock`, `.env.example`, and CI workflow are the authority
for implemented behavior. The [project roadmap](../planning/project-roadmap.md)
and research reports describe plans or historical reasoning; they do not turn
planned systems into available capabilities.

## 1. Current implementation

The repository currently implements one dependency-light bootstrap package:

```text
Python standard library
├── tracequant.config
├── tracequant.core.time
└── tracequant.logging ──> config + core.time

tracequant.domain.models ──> tracequant.core.time

tracequant.data.public_history ──> tracequant.domain
tracequant.data.raw_store ──────> public_history + core.time + Polars
tracequant.data.binance_contract_kline ──> public_history + raw_store
                                      └──> domain + Polars + stdlib transport/archive
```

The public foundation consists of:

1. explicit settings loading for `TRACEQUANT_ENV`, log level, log format, and
   an optional log directory;
2. explicit structured JSON logging with known-key redaction;
3. timezone-aware UTC conversion, parsing, and formatting;
4. immutable initial domain models for instrument identifiers, UTC ranges, and
   OHLCV bars, including explicit JSON-compatible serialization;
5. typed Binance public-history request, source-identity, and archive-boundary
   contracts;
6. an immutable filesystem Raw store that publishes and revalidates Parquet
   plus a provenance/checksum manifest;
7. an explicit Binance USDⓈ-M public-archive backfill adapter for BTCUSDT and
   ETHUSDT 1m contract Klines, including bounded HTTP, upstream checksum
   verification, ZIP/CSV validation, and complete 12-field Raw parsing;
8. deterministic test-only factories for the domain models.

Polars is the sole runtime third-party dependency in `pyproject.toml` and is
used for Raw frames and Parquet I/O. Development dependencies are pytest,
Ruff, mypy, and PyYAML. The current package does not use an exchange SDK,
database, backtest engine, model library, or execution framework.

## 2. Engineering boundaries

The full current tree and import rules are in
[Repository structure](repository-structure.md). The important boundaries are:

- configuration is explicit and immutable; importing it does not read env vars,
  load `.env`, create directories, or cache a singleton;
- logging is explicit; importing it does not configure handlers, create
  directories, or open files;
- UTC conversion is centralized in `tracequant.core.time`;
- domain models depend on UTC utilities but not on network, exchange, UI,
  deployment, logging setup, or test fixtures;
- data contracts remain separate from transport and persistence concerns;
  `BinanceContractKlineBackfill` and `RawStore` perform network and filesystem
  work only when explicitly called, and module imports remain side-effect free;
- tests may use `tests/fixtures`, but fixtures are not production runtime
  dependencies;
- future exchange, storage, transport, and vendor behavior must terminate at
  adapter boundaries rather than leak into domain or research logic.

No module may obtain credentials, perform network I/O, create runtime state, or
activate live trading as an import side effect. Secrets are not documented as
values and must not be printed, logged, committed, or included in fixtures.

## 3. Configuration and environment

`load_settings()` accepts explicit arguments and optionally a mapping for
deterministic tests. Its precedence is:

```text
explicit arguments > process environment (or supplied mapping) > defaults
```

The current fields are:

| Field | Values and default |
| --- | --- |
| `TRACEQUANT_ENV` | Required: `development`, `test`, or `production`. |
| `TRACEQUANT_LOG_LEVEL` | Optional: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`; default `INFO`. |
| `TRACEQUANT_LOG_FORMAT` | Optional: `text` or `json`; parser default `json`. Current logger accepts only `json`. |
| `TRACEQUANT_LOG_DIR` | Optional path; empty or unset means no file handler. The path is created only during explicit logging setup. |

`.env.example` documents these names but is not parsed automatically. The
current config surface deliberately does not include exchange credentials,
account settings, database URLs, trading mode, strategy parameters, or automatic
dotenv loading. `SecretValue` protects ordinary `repr` and `str` output; it is
not encryption, key storage, or a secret manager.

## 4. Logging and observability boundary

`configure_logging(settings)` installs project-owned handlers explicitly. The
current output is one-line UTF-8 JSON with stable `timestamp`, `level`,
`logger`, and `message` fields, plus optional `extra` and exception fields.
Timestamps are aware UTC ISO 8601 strings. Console output uses stderr. A
non-empty `log_dir` creates that exact directory and appends to
`tracequant.jsonl`; an empty value leaves only the console handler.

Known sensitive mapping keys are redacted case-insensitively:
`password`, `secret`, `token`, `api_key`, `apikey`, `authorization`, and
`cookie`. `SecretValue` and nested structured values are also handled. The
redactor cannot guarantee detection of a secret manually concatenated into free
text, so callers must keep credentials out of messages. There is currently no
metrics, dashboard, alerting, audit store, or log rotation subsystem.

## 5. UTC and domain boundary

`ensure_aware`, `to_utc`, `parse_utc`, and `format_utc` reject naive datetimes.
Aware values with a non-zero offset are converted to UTC, and formatted values
use `Z`. The initial domain models are documented in
[domain-models.md](domain-models.md). They intentionally stop at validated
single-instrument OHLCV values and do not define venue metadata, canonical
market-data schemas, orders, accounts, persistence, or risk policy.

Finite Python `float` values are required for OHLCV fields. Non-finite values
are rejected, volume cannot be negative, and zero or negative prices are still
allowed. Explicit `to_dict`/`from_dict` methods support deterministic JSON
round trips but are not an external compatibility or database contract.

## 6. Current quality baseline

Python `3.13` and uv `0.12.1` are fixed by the repository environment. The
canonical development and CI commands are maintained in the root
[README](../../README.md) and are:

```text
uv sync --locked --dev
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tests
```

The lock check is a CI integrity check; the other commands are the same local
quality commands. Do not create a competing quality command set in an
architecture document.

## 7. Explicitly deferred system architecture

The following are future boundaries, not current implementations. A future
Issue must supply its own contracts, data fingerprints, tests, and safety gates
before any of them can be described as available.

### Data and research

The intended future flow is:

```text
public exchange data
  -> immutable raw data
  -> canonical normalization and quality checks
  -> features, labels, and research datasets
  -> vectorized research and event-driven verification
```

Only the first, narrow part of this flow currently exists: callers can retrieve
approved Binance USDⓈ-M BTCUSDT/ETHUSDT 1m contract-Kline archives and publish
immutable Raw Parquet objects with manifests. There is no general Binance or
private API client, REST recent/gap synchronization, canonical schema,
missing/duplicate/out-of-order policy, feature pipeline, label pipeline, or
future-data-leakage check. The preserved Binance 12-field wire schema is Raw
source data and must not be treated as a canonical schema.

Archive planning is bounded by the per-instrument daily and monthly coverage
frozen in the approved Research contract. Dates outside those observed bounds
produce explicit coverage-gap results and are not requested as speculative
archive URLs.

### Backtesting, models, and experiments

There is no backtester, strategy, factor library, model training, or experiment
tracking in the current repository. Future research must preserve chronological
walk-forward validation, purging, embargo, point-in-time features, explicit
fees/funding/slippage/fill assumptions, gross and net performance, and code/data
fingerprints. A future event-driven engine may validate execution realism, but
no such engine is installed or callable today.

### Execution and risk

There is no Shadow, Demo, or Live runtime; no order/account/position ledger; no
exchange adapter; and no risk authority. Future runtime work must keep strategy,
risk, execution, and exchange connectivity separate. The risk layer must have
final authority to reject or reduce orders, fail closed on disagreement with
exchange state, reconcile unknown order state before retrying, and keep live
trading explicitly disabled by default. These are safety requirements for
future work, not evidence of current trading capability.

### Storage, deployment, and observability

Polars and local Parquet/manifest storage are current, limited dependencies and
capabilities of the Raw path. PostgreSQL, Redis/Valkey, DuckDB, Prometheus,
Grafana, Alertmanager, NautilusTrader, MLflow, Kubernetes, and multi-exchange
support are not current dependencies or services. Introducing one requires a
scoped Issue that documents purpose, alternatives, license/maintenance
considerations, version constraints, safety impact, and reproducible
validation. Future `apps/`, `packages/`, and `deploy/` directories remain
boundaries until such an Issue is implemented and reviewed.

## 8. Research and trading scope limits

The current public-data capability is limited to explicitly requested Binance
USDⓈ-M BTCUSDT/ETHUSDT 1m contract-Kline archives and local immutable Raw
artifacts. None of the following is currently available: private Binance API
access, REST recent/gap synchronization, multi-timeframe aggregation, factors,
models, backtests, Demo orders, Live orders, private API credentials, database
state, or multi-exchange production execution.

Historical research, planning documents, and workflow documentation must retain
their stated roles. Workflow controls such as LCK and the Validation Runner
govern repository delivery and review; they are not business modules and must
not be presented as trading functionality.

## 9. Placeholder and stub audit

The tracked repository was searched for `TODO`, `FIXME`, `NotImplementedError`,
`pass`, `placeholder`, and `stub` (excluding `.git` and ignored local
workflow artifacts), with matches classified by context:

- `src/tracequant/` contains no matching unfinished-work marker. The current
  production package has no undocumented code placeholder.
- `tests/tools/` contains intentional `Stub*` test doubles, literal
  `validation stub` input strings, and no-op `pass` branches used to exercise
  workflow failure/cleanup behavior. They are test scaffolding, not runtime
  capability claims.
- `tools/agent_workflow/` and `tools/wsl2_codex_diagnostic.py` contain
  `pass` in exception cleanup/no-op branches and use `pass` as a serialized
  status value. These are workflow-tool implementation details, not business
  module stubs.
- The active workflow Skills in `.agents/skills/` and `.claude/skills/` use
  `pass`/`PASS` for lifecycle outcomes and review protocol language; the
  feature-audit Skill also uses `placeholder` when describing a finding to
  inspect. These are workflow instructions and review vocabulary, not product
  implementation stubs.
- `AGENTS.md`, `.agents/policies/`, and `docs/development/` use `pass`/`PASS`
  as governance, validation, and lifecycle terminology. These matches state
  protocol outcomes or constraints and do not identify unfinished runtime
  work.
- `.github/ISSUE_TEMPLATE/*.yml` uses `placeholder` as GitHub form-field UI
  metadata. It is not application code.
- `docs/workflows/task-workflow-architecture-audit.md` uses `placeholder` for
  unavailable historical Task #86 evidence and `pass`/`PASS` for audit status
  and protocol outcomes. It is a workflow audit record, not product code.
- Other `docs/workflows/` reports, evidence records, publication registers,
  and templates use `pass`/`PASS` as validation or protocol status values and
  `placeholder`/`FILL_ME` as explicitly historical or template values. They
  are not current implementation evidence and must not be read as product
  functionality.

No current production data, research, execution, or risk capability is being
hidden behind one of these matches. A future real code gap must receive its own
scoped Issue rather than being made to look complete by documentation.

## 10. Change control

Any future change that adds runtime dependencies, moves bootstrap modules,
introduces network or persistence I/O, changes UTC or serialization semantics,
adds exchange/order/risk authority, or crosses Research/Shadow/Demo/Live
boundaries requires a dedicated Issue and appropriate tests/documentation.
Do not make a planned technology choice appear implemented by editing this
baseline alone. Keep future claims explicitly marked until the corresponding
code and validation exist.
