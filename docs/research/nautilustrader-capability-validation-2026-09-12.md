# NautilusTrader capability validation baseline

- **Evidence date:** 2026-09-12
- **Status:** Completed foundation-selection evidence
- **Conclusion:** `NAUTILUS_PRIMARY`, `LIVE_NOT_APPROVED`
- **Decision:** [ADR-0001](../architecture/adr-0001-nautilustrader-primary-runtime.md)
- **Prerequisite:**
  [`V1_RETIREMENT_COMPLETE`](https://github.com/PhoenixSss/tracequant/issues/314)

## Purpose and claim boundary

This is the canonical, portable repository record of the completed external
foundation-selection and NautilusTrader capability validation. It preserves
the conclusion without requiring the external audit workspaces to remain
available and without making the completed selection investigation a future
v2 prerequisite.

The evidence supports selecting a framework baseline for v2 development and
backtesting. It does **not** prove that TraceQuant has integrated
NautilusTrader, that Binance Demo behavior works for TraceQuant, or that Live
trading is safe or approved.

| Claim | Evidence state on 2026-09-12 |
| --- | --- |
| Fixed NautilusTrader wheel, selected Python tests, and official synthetic backtest execute | `RUNTIME_VERIFIED` |
| The fixed source contains the inspected trading-domain and Binance USD-M capability surfaces | `SOURCE_VERIFIED` |
| TraceQuant has integrated the selected runtime | `NOT_VERIFIED`; not implemented by this documentation item |
| Binance Demo order, restart, and reconciliation behavior | `NOT_VERIFIED`; later acceptance gate |
| Live readiness | Not approved; `LIVE_NOT_APPROVED` |

## Evidence vocabulary

The source audit used these levels. They must remain distinct in later design
and acceptance work:

| Canonical label | Meaning |
| --- | --- |
| `RUNTIME_VERIFIED` | An existing official test, example, or tool was run and its result observed. |
| `SOURCE_VERIFIED` | The behavior or interface exists in the fixed source or official test source. It was not necessarily executed. |
| `DOC_VERIFIED` (documentation-verified) | Fixed-version official documentation states the behavior. It is not runtime proof. |
| `ISSUE_EVIDENCE` | An official upstream Issue records a report and status. It is not a local reproduction. |
| `NOT_VERIFIED` | The environment, credentials, or allowed audit scope could not verify the claim. |
| `UNSUPPORTED` | Fixed source explicitly rejects the capability or the required path is absent with corroborating evidence. |

No lower level is promoted to `RUNTIME_VERIFIED`, and framework evidence is
never promoted to TraceQuant integration, Demo, or Live evidence.

## Fixed subject identities

| Subject | Official source | Audited identity | Notes |
| --- | --- | --- | --- |
| NautilusTrader | `https://github.com/nautechsystems/nautilus_trader.git` | tag `v2.0.0rc4`; commit `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`; 2026-09-02 | Detached fixed checkout; Python `2.0.0rc4`; Rust crates `0.63.0` |
| LEAN Engine | `https://github.com/QuantConnect/Lean.git` | commit `6eb389012d73c364547d61546ff822fc8432dee2`; describe `18084`; 2026-09-11 | Comparison subject; runtime reported `v2.5.0.0` |
| LEAN Binance plugin | `https://github.com/QuantConnect/Lean.Brokerages.Binance.git` | commit `9696fc025972314d73008f879d595df5c67bca28`; describe `17864`; 2026-06-19 | Comparison subject; resolved `QuantConnect.Brokerages 2.5.18042` |

The three source checkouts were clean after their audit commands. They and
their environments remain external evidence inputs and are not repository
dependencies or committed artifacts.

## Runtime-verified results

The fixed NautilusTrader wheel reported version `2.0.0rc4` and core commit
prefix `a04002511106` under Python 3.12.14. The selected official test total was:

```text
318 passed, 6 skipped, 0 failed
```

That total consists of:

| Official test surface | Result |
| --- | ---: |
| `python/tests/unit/model/test_funding.py` | 11 passed |
| `python/tests/acceptance_tests` | 33 passed, 6 skipped |
| Selected backtest engine/config/node/model, execution, risk, portfolio, serialization, Binance factory, and migration units | 274 passed |

The acceptance suite used its supported `TEST_DATA_ROOT_PATH` setting to point
at the fixed checkout. Earlier missing-test-data failures were therefore
classified as environment setup, not framework defects; no candidate source
was changed.

The official `examples/backtest/synthetic_data_pnl_test.py` example exited
successfully with 12 bars, two market orders, one position, per-contract fees,
margin, and realized PnL of `$70.00`.

These results prove that the selected build and core synthetic backtest path
were executable in the audit environment. They are **not** Binance Demo,
exchange-protocol, restart/reconciliation, strategy-profitability, or Live
proof.

## Capability evidence and limits

Fixed-source review found NautilusTrader-owned surfaces for typed trading data
and instruments, `ParquetDataCatalog`, runtime bar aggregation and common
indicators, Strategy/Actor lifecycle, orders, positions, portfolio/accounting,
core risk, backtest fills/fees/funding/reports, Binance USD-M data and
execution, cache persistence, and reconciliation. In particular, the Binance
source exposed GTX post-only, reduce-only, one-way/hedge position semantics,
per-symbol leverage and cross/isolated margin configuration, mark/index/funding
data, native amend paths, and dedicated reconciliation behavior.

This is primarily `SOURCE_VERIFIED`, with fixed documentation supplying
`DOC_VERIFIED` support. The selected runtime executions above cover only their
stated official Python test and synthetic example surfaces.

The architectural ownership derived from this evidence is canonical in
[ADR-0001](../architecture/adr-0001-nautilustrader-primary-runtime.md). In
summary, NautilusTrader owns trading-domain types and mutable state; TraceQuant
owns source provenance, read-only research semantics, features and labels,
models and strategy intent, policy values, acceptance, operations, and the Live
gate.

## Known gaps and unverified claims

The completed audit did not verify:

- NautilusTrader Rust Binance adapter tests, because the audit host lacked the
  Rust toolchain;
- any Binance Demo or private API path, because no Demo credentials were used
  and no order was submitted;
- warm or cold restart with venue orders or positions;
- long-running disconnect/reconnect behavior or a Demo soak;
- real funding, commission, balance, and position reconciliation;
- RTX 5090 model training or inference coexistence; or
- TraceQuant-specific data, Strategy, risk-policy, or runtime integration.

The fixed rc4 Python `ParquetDataCatalog` binding did not directly expose the
Rust backend's FundingRate write/query surface. Historical mark/index/funding
coverage and research lineage remain TraceQuant integration work, not evidence
that a second trading-data platform is needed.

Upstream Issue evidence included a then-open warm-restart NETTING
reconciliation report, nautechsystems/nautilus_trader#4736, and a then-open
Futures OCO/bracket capability gap, #4043. `ISSUE_EVIDENCE` does not claim local
reproduction, but the restart report remains a Live blocker until a selected
version and TraceQuant Demo acceptance evidence resolve it.

## Required later gates

The completed selection tests are not repeated as a prerequisite for beginning
v2 design. New evidence must instead be produced at the boundary it concerns:

1. **Integration:** pin and integrate the dependency through a scoped Issue;
   verify TraceQuant data, Strategy, risk, accounting, and reproducibility
   contracts without creating a second trading domain.
2. **Demo execution:** first use official Binance Futures data/exec testers,
   then verify market/limit/cancel/reduce-only/stop, partial and complete fills,
   selected position and margin modes, and strict symbol allowlists.
3. **Restart and reconciliation:** exercise warm/cold restart with open orders,
   open positions, partial fills, disconnect/reconnect, and persistent cache;
   unexplained venue/local state must fail closed.
4. **Accounting:** compare fees, funding, balances, orders, fills, and positions
   with venue observations across relevant boundaries.
5. **Soak and operations:** pass an initial 24–72 hour engineering soak, then a
   longer Demo observation period driven by anomaly rate and change frequency;
   validate alerts, runbooks, secret controls, rollback, and kill behavior.
6. **Live decision:** re-check the target release's production recommendation,
   resolve or accept all blockers explicitly, and require human approval before
   any separately scoped tiny-capital canary.

Until all applicable gates pass, missing or contradictory evidence preserves
`LIVE_NOT_APPROVED` and the system remains Offline or Demo-only.

## External report register

The four completed reports remain outside the repository. Their filenames and
SHA-256 digests preserve exact source identity without committing audit source
checkouts, virtual environments, dependency caches, or machine-specific paths.

| External report | SHA-256 |
| --- | --- |
| `TraceQuant 开源技术栈与自研边界深度研究.md` | `5fc90e347c8115301c673f4f98dde6070555332062f40fb5c55451b72ee9b88a` |
| `TraceQuant Trading Runtime Read-Only Review.md` | `93f607c0adef9f6f27e96f4a835c4e258902ce546c62d433bc4d0a8d74d26179` |
| `TraceQuant Nautilus 技术栈与自研边界复审.md` | `954edd7b2f7439b261225d0c46266e68fa6bba7ce33ab8feacf50779b8dea89e` |
| `TraceQuant 分阶段推进计划.md` | `6710bb0505d19971ea46959cf0644cef97720a8a348326e826929d841dd76001` |

This register identifies the completed inputs; those external files are not
runtime dependencies and do not have to be reacquired or rerun for ordinary v2
design work.
