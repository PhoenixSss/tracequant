# TraceQuant repository structure

This document describes the current repository and the boundaries that future
implementation work must respect. Directory names under `apps/`, `packages/`,
and `deploy/` are architectural scaffolding; their presence does not mean that
the corresponding product capability exists.

## Current tree

```text
src/tracequant/
  config.py
  logging.py
  core/time.py
  domain/models.py
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
  contracts/                     boundary README only
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

## Current dependency direction

The direct imports in the implemented package are intentionally small:

```text
Python standard library
├── tracequant.config
├── tracequant.core.time
└── tracequant.logging ──> tracequant.config
                         └> tracequant.core.time

tracequant.domain.models ──> tracequant.core.time

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

None of these future boundaries currently provides data ingestion, factors,
backtesting, model training, order execution, account state, risk decisions,
or multi-exchange support.

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
