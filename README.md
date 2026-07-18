# Quant System

A research-first quantitative trading system for cryptocurrency perpetual futures.

## Initial scope

- Exchange: Binance
- Markets: USD-margined perpetual futures
- Initial instruments: BTC and ETH USDT/USDC perpetual contracts
- Decision horizon: approximately 15 minutes to 4 hours
- Research focus:
  - multi-factor signals;
  - machine-learning filters;
  - cost-aware backtesting;
  - maker-preferred execution;
  - strict risk control and auditability.

## Development principles

- Correctness before performance.
- Research before live trading.
- Gross and net results must be reported separately.
- Fees, funding, slippage, missed fills, and execution delay must be modeled.
- Strategy, risk, execution, and exchange connectivity must remain separated.
- Live trading must be disabled by default.
- All implementation work is tracked through GitHub Issues and Pull Requests.

## Repository structure

- `docs/`: architecture, research reports, risk rules, and ADRs.
- `src/`: application source code.
- `tests/`: unit, integration, and regression tests.
- `.github/`: Issue templates, Pull Request templates, and CI workflows.

## Current status

The project is in its initial planning and repository setup stage. No live-trading capability has been implemented.
