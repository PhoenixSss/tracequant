# ADR-0001: NautilusTrader is the primary trading runtime for v2

- **Status:** Accepted
- **Decision date:** 2026-09-12
- **Decision:** `NAUTILUS_PRIMARY`
- **Live status:** `LIVE_NOT_APPROVED`
- **Validated baseline:** NautilusTrader `v2.0.0rc4`, commit
  `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`
- **Evidence:** [NautilusTrader capability validation baseline](../research/nautilustrader-capability-validation-2026-09-12.md)

## Context

The TraceQuant v1 retirement gate reached
[`V1_RETIREMENT_COMPLETE`](../research/tracequant-v1-retirement-gate-2026-09-12.md).
The completed foundation-selection work compared trading runtimes and then
reviewed the selected NautilusTrader version through fixed-source inspection,
official tests, and an official synthetic backtest. Issue #316 registers that
already completed work; it does not reopen framework selection.

TraceQuant needs one owner for trading-domain types and mutable trading state.
Research libraries may be composed around that owner, but they must not create
a competing Order, Position, Portfolio, Risk, Execution, or Reconciliation
domain.

## Decision

NautilusTrader v2 is the primary trading runtime for TraceQuant v2. The first
development and backtest baseline is pinned to `v2.0.0rc4` at commit
`a0400251110653b6d8ae6a9b5b89c4543fa85a2d`.

The decision state is:

```text
NAUTILUS_PRIMARY
VERSION_PINNED=v2.0.0rc4/a0400251110653b6d8ae6a9b5b89c4543fa85a2d
DEVELOPMENT_AND_BACKTEST_ALLOWED
BINANCE_DEMO_OBSERVATION_REQUIRED
LIVE_NOT_APPROVED
```

This is an architecture selection, not a claim that NautilusTrader is already
integrated into this repository. The current implementation baseline remains
authoritative for installed and callable TraceQuant capabilities. Adding the
dependency, integrating it, or crossing an Offline, Shadow, Demo, or Live
boundary requires separately scoped Issues and validation.

## Ownership boundary

| Capability | Primary owner | TraceQuant responsibility |
| --- | --- | --- |
| Trading data types and instrument semantics | NautilusTrader | Universe allowlists, source provenance, and read-only research mappings |
| Trading-history catalog and runtime aggregation | NautilusTrader | Raw-source acquisition, checksums, coverage QA, and research-derived views |
| Common indicator primitives | NautilusTrader | Feature definitions, parameters, causality, and research/runtime parity |
| Strategy and Actor lifecycle | NautilusTrader | Alpha, model loading and inference, and strategy configuration |
| Orders, positions, account, and portfolio | NautilusTrader | Strategy intent and acceptance assertions; no shadow trading ledger |
| Core pre-trade risk | NautilusTrader | Thresholds and only verified missing policies such as daily loss or stale-data stops |
| Backtest fills, fees, funding, accounting, and reports | NautilusTrader | Scenarios, assumptions, comparisons, and acceptance criteria |
| Binance market data and execution | NautilusTrader adapter | Configuration, credentials boundary, symbol allowlist, and black-box acceptance |
| Cache, restart, and reconciliation | NautilusTrader | Persistence configuration, acceptance, alerting, and Live admission |
| Deployment and monitoring | TraceQuant operations | Environment isolation, least privilege, rollback, SLOs, alerts, and runbooks |

Polars, Jupyter, scikit-learn, GBDT libraries, PyTorch, DuckDB, MLflow,
Optuna, Redis/PostgreSQL, and observability tools remain companion components
used only when a scoped requirement justifies them. None becomes a second
trading-domain owner.

## Consequences

- Do not build a TraceQuant trading engine, generic backtester, exchange order
  state machine, Binance execution adapter, Portfolio/Account ledger, generic
  RiskEngine, or reconciliation engine alongside NautilusTrader.
- Keep raw source evidence, read-only research tables, and Nautilus trading
  data as distinct layers. A file format does not own trading semantics.
- Implement production strategies through NautilusTrader Strategy/Actor and
  order APIs. A model output is research or strategy intent, not an order fact.
- Prefer a reproducible upstream fix when a NautilusTrader defect is confirmed;
  do not maintain a parallel shadow state machine as a workaround.
- Do not repeat the completed framework-selection exercise as a prerequisite
  for v2 design. Re-evaluate a version only for a scoped upgrade or a failed
  acceptance gate.

## Live admission remains closed

The pinned release candidate has framework capability evidence but no
TraceQuant Binance Demo or Live proof. Live remains prohibited until separate
work verifies at least:

1. the target version's production recommendation or an explicit risk
   acceptance if it is still a release candidate;
2. Binance Demo market, limit, cancel, reduce-only, stop, partial-fill, and
   position/margin-mode behavior;
3. warm and cold restart with open orders and positions, plus reconciliation
   with no unexplained venue/local drift;
4. funding, fees, balances, and positions against venue observations;
5. disconnect/reconnect behavior, fail-closed controls, alerts, and runbooks;
6. an engineering soak followed by a longer Demo observation period; and
7. explicit human approval for any later tiny-capital canary.

Failure or missing evidence at any gate keeps the system in Demo. No date or
completion of earlier development automatically authorizes Live trading.
