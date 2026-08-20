# TraceQuant

An auditable research-to-live quantitative trading system for cryptocurrency perpetual futures.

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

TraceQuant is a modular monorepo. The current bootstrap package remains in `src/tracequant/` while product boundaries are established before implementation is moved into them.

- `apps/research/`: research-facing orchestration.
- `apps/runtime/`: Shadow, Demo, and Live runtime entry points.
- `apps/console/`: future operator UI/control-plane boundary.
- `packages/contracts/`: stable cross-boundary schemas and interfaces.
- `packages/domain/`: core domain models and invariants.
- `packages/adapters/`: exchange, storage, database, filesystem, and vendor integrations.
- `deploy/research|staging|live/`: environment-specific deployment assets.
- `src/tracequant/`: currently implemented bootstrap Python package.
- `tests/`: unit, integration, and regression tests.
- `docs/`: architecture, research reports, risk rules, ADRs, and workflow documentation.
- `.github/`: Issue templates, Pull Request template, and CI workflows.

See [Repository structure](docs/architecture/repository-structure.md) for boundary rules and future extraction criteria.

## Documentation

- [Technical baseline](docs/architecture/technical-baseline.md): current approved technology choices and architecture boundaries.
- [Project roadmap](docs/planning/project-roadmap.md): current four-stage plan, Issue navigation, dependencies, and implementation entry point.
- [Planning baseline v1.0](docs/planning/quant-system-planning-baseline-v1.0.md): historical planning snapshot retained for context and decision history.
- [Deep research report](docs/research/deep-research-report.md): historical broad research on markets, strategies, data, backtesting, and operations.
- [Deep research report 2](docs/research/deep-research-report-2.md): historical follow-up research used to refine the technical direction.
- [Technical roadmap research](docs/research/technical-roadmap-research.md): historical comparative research behind the selected implementation route.
- [WSL2 Codex environment](docs/workflows/wsl2-codex-environment/README.md): reproducible WSL2 setup, diagnostics, approval boundaries, rollback, and troubleshooting.
- [WSL2 GitHub evidence runner](docs/workflows/wsl2-github-evidence-runner/README.md): fixed read-only Task/PR snapshots, drift rechecks, least-privilege Rules, and Git/GitHub approval boundaries.

## Current status

The project is in its initial planning and repository setup stage. No live-trading capability has been implemented.

## Development environment

Python 3.13 and [uv](https://docs.astral.sh/uv/) are required. The repository `.python-version` pins the project environment to Python 3.13, matching CI and the supported TraceQuant runtime baseline. Create or update the local environment with:

```console
uv sync --locked --dev
```

Verify that the project package is importable with:

```console
uv run python -c "import tracequant; print(tracequant.__name__)"
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

For the supported VS Code Remote WSL + Codex environment, including pinned uv, GitHub CLI authentication, proxy behavior, diagnostics, and rollback, see [the WSL2 environment guide](docs/workflows/wsl2-codex-environment/README.md).

## Continuous integration

Pull requests targeting `main` and pushes to `main` automatically run CI. The workflow runs pytest, Ruff lint, Ruff format checking, and mypy. Use the equivalent local commands in the existing Development environment section above.

## UTC time handling

Internal datetimes must be timezone-aware and use UTC as the standard timezone. Naive datetimes are explicitly rejected. Time utilities are provided by `tracequant.core.time`.

## Initial domain models

The Research MVP exposes three immutable, validated models from
`tracequant.domain`: `InstrumentId`, `TimeRange`, and `OHLCVBar`. They provide
strict JSON-compatible `to_dict` / `from_dict` representations and reuse the
project UTC utilities. `TimeRange` and bars use half-open `[start, end)` UTC
intervals. Bar prices are finite Python floats and may currently be zero or
negative for research data; volume must be finite and non-negative.

Shared test fixtures use function scope in `tests/conftest.py`. Deterministic,
field-overridable factories live in `tests/fixtures/domain.py`; they use fixed
UTC timestamps and never read the clock, random state, environment, network, or
filesystem. Test-specific data should remain in its test module, and shared
fixtures should be added only after at least two modules need them.

## Configuration

Application configuration is loaded explicitly with `tracequant.config.load_settings`.
Importing the module does not read environment variables, parse `.env` files, create
directories, or cache a global settings singleton.

Supported environment variables:

- `TRACEQUANT_ENV`: required; one of `development`, `test`, or `production`.
- `TRACEQUANT_LOG_LEVEL`: optional; one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`; defaults to `INFO`.
- `TRACEQUANT_LOG_FORMAT`: optional; one of `text` or `json`; defaults to `json`. Logging currently supports `json`.
- `TRACEQUANT_LOG_DIR`: optional; empty or unset disables file logging.

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
from tracequant.config import Environment, Settings

settings = Settings(environment=Environment.TEST)
```

`SecretValue` provides redacted `repr` and `str` output for future sensitive fields.
It is a display-safety boundary only, not encryption, a secrets manager, or a system
keyring. Current configuration scope does not include exchange credentials, account
settings, databases, trading modes, structured logging setup, or automatic log
directory creation.

## Structured logging

Applications configure logging explicitly with `tracequant.logging.configure_logging(settings)`.
Importing the logging module does not configure handlers, create directories, or open
files. Modules should continue to use `logging.getLogger(__name__)`.

JSON log records are single-line UTF-8 objects with stable `timestamp`, `level`,
`logger`, and `message` fields. Timestamps are timezone-aware UTC ISO 8601 strings.
When `settings.log_dir` is set, the exact directory is created and logs are appended
to `tracequant.jsonl`; when it is empty or unset, only console logging is enabled.

Known sensitive keys are redacted case-insensitively in structured fields and
exception output: `password`, `secret`, `token`, `api_key`, `apikey`,
`authorization`, and `cookie`. This boundary does not guarantee detection of secrets
that callers manually concatenate into free-text messages, so callers must not place
raw credentials in log messages.

## Agent workflow evidence and validation

Repository workflow Skills use the current fixed Runner entries for deterministic
metadata and validation summaries:

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
tools/agent_workflow/wsl2_validation_runner.py
```

Local outputs are stored only in Git-ignored directories:

```text
.agents/evidence.local/
.agents/validation.local/
```

See `.agents/policies/workflow-evidence.md`. These tools do not replace semantic
review, independent PR review, manual Merge, or Feature closeout.

- [WSL2 GitHub evidence runner](docs/workflows/wsl2-github-evidence-runner/README.md): fixed read-only profiles, evidence snapshots, Git/GitHub approval boundaries, and live material capture.
- [Agent workflow Skills](docs/workflows/agent-skills.md): current Codex/Claude Skills, source-of-truth ownership, and validation entry points.
- [Validation Runner](docs/workflows/wsl2-validation-runner/README.md): fixed validation profiles, compact artifacts, exact argv, and process cleanup.
- [Task Skill runner migration](docs/workflows/task-skill-runner-migration/README.md): historical migration record only; not an operational guide.

## License

TraceQuant is publicly accessible but is not currently distributed under an open-source license. All rights are reserved. See `LICENSE`.
