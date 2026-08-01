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

## Domain models

The initial Research MVP public domain import path is `quant_system.domain`.
It exposes only `InstrumentId`, `TimeRange`, `OHLCVBar`, and
`DomainValidationError`.

`InstrumentId` trims input, normalizes it to uppercase, and accepts only ASCII
letters and digits up to 32 characters. It intentionally does not encode venue,
exchange, market type, tick size, lot size, or base/quote parsing.

`TimeRange` represents UTC half-open intervals as `[start, end)`. Its datetimes
must be timezone-aware UTC values, `start` must be earlier than `end`, and
serialization uses stable UTC ISO 8601 strings.

`OHLCVBar` stores an `InstrumentId`, UTC `start` and `end`, and float `open`,
`high`, `low`, `close`, and `volume` values. Values must be finite, volume must
be non-negative, and OHLC relationships are validated. Prices may currently be
zero or negative so research data is not rejected prematurely; downstream
modules must decide whether stricter price constraints are appropriate.

All three models are immutable dataclasses with explicit `to_dict` and
`from_dict` APIs. The serialized dictionaries are JSON-compatible internal
Research MVP payloads, not a long-term external compatibility contract, and do
not rely on pickle or dataclass implementation details.

Shared test construction lives in `tests/fixtures/domain.py`; pytest fixtures in
`tests/conftest.py` are reserved for values reused by multiple test modules.
Factories are deterministic, allow explicit field overrides, use fixed UTC
times, and must not read current time, randomness, environment variables,
network, or files.

## Configuration

Application configuration is loaded explicitly with `quant_system.config.load_settings`.
Importing the module does not read environment variables, parse `.env` files, create
directories, or cache a global settings singleton.

Supported environment variables:

- `QUANT_SYSTEM_ENV`: required; one of `development`, `test`, or `production`.
- `QUANT_SYSTEM_LOG_LEVEL`: optional; one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`; defaults to `INFO`.
- `QUANT_SYSTEM_LOG_FORMAT`: optional; one of `text` or `json`; defaults to `json`. Logging currently supports `json`.
- `QUANT_SYSTEM_LOG_DIR`: optional; empty or unset disables file logging.

Loading priority is:

```text
explicit load_settings arguments
> process environment variables
> defaults
```

Use `.env.example` as the committed variable list and copy values into your shell,
IDE, or env-file tooling when needed. Local `.env` and `.env.*` files remain ignored
by Git and are not read automatically by this project.

Tests can construct isolated settings directly:

```python
from quant_system.config import Environment, Settings

settings = Settings(environment=Environment.TEST)
```

`SecretValue` provides redacted `repr` and `str` output for future sensitive fields.
It is a display-safety boundary only, not encryption, a secrets manager, or a system
keyring. Current configuration scope does not include exchange credentials, account
settings, databases, trading modes, structured logging setup, or automatic log
directory creation.

## Structured logging

Applications configure logging explicitly with `quant_system.logging.configure_logging(settings)`.
Importing the logging module does not configure handlers, create directories, or open
files. Modules should continue to use `logging.getLogger(__name__)`.

JSON log records are single-line UTF-8 objects with stable `timestamp`, `level`,
`logger`, and `message` fields. Timestamps are timezone-aware UTC ISO 8601 strings.
When `settings.log_dir` is set, the exact directory is created and logs are appended
to `quant-system.jsonl`; when it is empty or unset, only console logging is enabled.

Known sensitive keys are redacted case-insensitively in structured fields and
exception output: `password`, `secret`, `token`, `api_key`, `apikey`,
`authorization`, and `cookie`. This boundary does not guarantee detection of secrets
that callers manually concatenate into free-text messages, so callers must not place
raw credentials in log messages.

## Agent workflow evidence and validation

Repository workflow Skills use compact local tooling for deterministic metadata
and validation summaries:

```powershell
python -X utf8 tools/agent_workflow/workflow_evidence.py --help
python -X utf8 tools/agent_workflow/workflow_validation.py --help
python -X utf8 tools/agent_workflow/trusted_runner.py --help
```

Local outputs are stored only in Git-ignored directories:

```text
.agents/evidence.local/
.agents/validation.local/
```

See `docs/workflows/workflow-evidence.md`. These tools do not replace semantic
review, independent PR review, manual Merge, or Feature closeout.
