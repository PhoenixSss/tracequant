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

## Documentation

- [Technical baseline](docs/architecture/technical-baseline.md): current approved technology choices and architecture boundaries.
- [Project roadmap](docs/planning/project-roadmap.md): current four-stage plan, Issue navigation, dependencies, and implementation entry point.
- [Planning baseline v1.0](docs/planning/quant-system-planning-baseline-v1.0.md): historical planning snapshot retained for context and decision history.
- [Deep research report](docs/research/deep-research-report.md): historical broad research on markets, strategies, data, backtesting, and operations.
- [Deep research report 2](docs/research/deep-research-report-2.md): historical follow-up research used to refine the technical direction.
- [Technical roadmap research](docs/research/technical-roadmap-research.md): historical comparative research behind the selected implementation route.

## Current status

The project is in its initial planning and repository setup stage. No live-trading capability has been implemented.

## Development environment

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are required. Create or update the local environment with:

```console
uv sync --dev
```

Verify that the project package is importable with:

```console
uv run python -c "import quant_system; print(quant_system.__name__)"
```

Run the test suite with:

```console
uv run pytest
```

Run lint and formatting checks with:

```console
uv run ruff check .
uv run ruff format --check .
```

Run strict type checking with:

```console
uv run mypy src tests
```

## Continuous integration

Pull requests targeting `main` and pushes to `main` automatically run CI. The workflow runs pytest, Ruff lint, Ruff format checking, and mypy. Use the equivalent local commands in the existing Development environment section above.

## UTC time handling

Internal datetimes must be timezone-aware and use UTC as the standard timezone. Naive datetimes are explicitly rejected. Time utilities are provided by `quant_system.core.time`.
