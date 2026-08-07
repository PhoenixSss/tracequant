# TraceQuant repository structure

## Decision

TraceQuant remains a **modular monorepo**. The repository establishes product and dependency boundaries before considering physical repository extraction.

```text
apps/
  research/
  runtime/
  console/
packages/
  contracts/
  domain/
  adapters/
deploy/
  research/
  staging/
  live/
src/tracequant/
tests/
docs/
```

## Bootstrap package

`src/tracequant/` is the current bootstrap Python package. It contains the already implemented shared configuration, logging, and UTC utilities. This migration deliberately does **not** force those existing modules into an artificial product package merely to satisfy directory aesthetics.

As Research, Runtime, Contracts, Domain, and Adapter implementations are added, new code must be placed behind the corresponding boundary. Moving an existing bootstrap module into a product package requires a behavior-preserving Task with import and compatibility validation.

## Boundary rules

- `apps/research`: offline research orchestration; no production exchange writes.
- `apps/runtime`: Shadow/Demo/Live orchestration; live disabled by default and fail closed.
- `apps/console`: operator UI/control-plane boundary; independently deployable only when justified.
- `packages/contracts`: stable cross-boundary schemas and interfaces.
- `packages/domain`: core domain models and invariants; no exchange/network/UI/deployment dependencies.
- `packages/adapters`: exchange, storage, database, filesystem, transport, and vendor integrations.
- `deploy/research|staging|live`: environment-specific deployment assets with explicit separation.

## Repository extraction

Do not split repositories by default. Physical extraction is considered only when independent release cadence, security boundary, dependency isolation, or independent Console deployment creates sustained operational value.
