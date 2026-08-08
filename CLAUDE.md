# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

TraceQuant is an auditable research-to-live quantitative trading system for cryptocurrency perpetual futures (Binance USDⓈ-M, starting with BTCUSDT/ETHUSDT). Currently in **Research MVP** stage — no live trading capability exists yet.

The `AGENTS.md` file at the repo root is the primary behavior rule source: it defines the issue-driven workflow, context acquisition (leaf-Issue-first, trigger-based expansion), implementation constraints, financial safety rules, and data correctness requirements. This file supplements it with Claude Code–specific tooling, commands, permissions, and environment context; it does not duplicate AGENTS.md rules.

### Codex 与 Claude Code 共存说明

本项目同时保留 Codex（`.agents/`、`.codex/`）和 Claude Code（`.claude/`）的专用配置，这是有意的设计。在 Claude Code 中工作时：

- **Skills**: 使用 `Skill` 工具调用 `.claude/skills/` 下的 Skill，不要手动 `Read` `.agents/skills/` 下的 Skill 文件。两套 Skill 各自适配对应工具的执行模型。
- **Legacy skills**: `.agents/skills/task-delivery/` 和 `.agents/skills/task-pr-review/` 是历史基准，没有 `.claude/` 对应版本。除非维护者明确指定，否则不要使用它们。
- **权限**: Claude Code 权限由 `.claude/settings.json` 控制。`.codex/rules/` 是 Codex 专用，Claude Code 忽略。
- **标签**: `codex:ready`、`codex:blocked`、`codex:needs-spec` 等 GitHub Issue 标签不受工具环境影响，照常适用。

## Commands

```bash
# Install dependencies (requires Python 3.13 and uv)
uv sync --locked --dev

# Verify the package is importable
uv run python -c "import tracequant; print(tracequant.__name__)"

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_config.py

# Run a single test function
uv run pytest tests/test_config.py::test_load_settings_defaults

# Lint
uv run ruff check .

# Format check
uv run ruff format --check .

# Strict type checking
uv run mypy src tests
```

CI (`.github/workflows/ci.yml`) runs `pytest`, `ruff check`, `ruff format --check`, and `mypy src tests` on every PR and push to `main`. Use `uv lock --check` to validate the lock file is up to date.

## Architecture

### Repository and package layout

TraceQuant is a modular monorepo with explicit product boundaries:

```
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
  config.py
  logging.py
  core/time.py
```

`src/tracequant/` is the current bootstrap Python package. Existing shared utilities remain there until a behavior-preserving Task has a concrete reason to move them behind a product/package boundary; do not move code merely to satisfy directory aesthetics. See `docs/architecture/repository-structure.md`.

The package currently has **zero runtime dependencies** (only dev dependencies: pytest, ruff, mypy). The canonical Python namespace is `tracequant`.

### Configuration system (`tracequant.config`)

Settings are loaded explicitly via `load_settings()`. Importing the config module does **not** read env vars, parse `.env` files, create directories, or cache a global singleton. Loading priority: explicit arguments > `TRACEQUANT_*` environment variables > defaults. Required env var: `TRACEQUANT_ENV` (one of `development`, `test`, `production`). `SecretValue` provides redacted `repr`/`str` for display safety — it is not encryption.

### Logging (`tracequant.logging`)

JSON log records with stable `timestamp`, `level`, `logger`, `message` fields. Timestamps are timezone-aware UTC ISO 8601. Configured explicitly via `configure_logging(settings)` — importing the module does not configure handlers. Known sensitive keys (`password`, `secret`, `token`, `api_key`, `apikey`, `authorization`, `cookie`) are recursively redacted. File logging only when `settings.log_dir` is set.

### UTC time handling (`tracequant.core.time`)

All internal datetimes must be timezone-aware UTC. Naive datetimes are explicitly rejected. Re-exports `datetime.UTC` for convenience. Key functions: `ensure_aware()`, `to_utc()`, `is_utc()`, `parse_utc()`, `format_utc()`.

### Agent workflow tools (`tools/agent_workflow/`)

Deterministic runner scripts for workflow evidence and validation:

- `wsl2_github_evidence_runner.py` — captures read-only GitHub/Task/PR snapshots with content hashes
- `wsl2_validation_runner.py` — compact validation profiles with exact argv and process cleanup
- `workflow_common.py` — shared helpers: `CommandRunner`, JSON utilities, SHA hashing, path redaction, secret redaction
- `workflow_evidence.py` / `workflow_validation.py` — evidence and validation domain logic

Local outputs go only to Git-ignored `.agents/evidence.local/` and `.agents/validation.local/`.

### Claude Code skills (`.claude/skills/`)

Claude Code 专用的四个 Skill，与 `.agents/skills/` 中 Codex 的对应 Skill 并存：

| Skill | Purpose |
|---|---|
| `task-delivery-runner` | Implement a Task from readiness through PR creation |
| `task-pr-review-runner` | Read-only independent PR review, emits remediation handoff or passing verdict |
| `task-closeout` | Post-merge verification, state convergence, and Task-branch cleanup |
| `feature-completion-audit` | Read-only audit of an open Feature against current main |

Skills start only after the user identifies an existing Task, PR, or Feature. None may merge a PR.

### Permissions (`.claude/settings.json`)

Claude Code 权限控制（与 Codex 的 `.codex/rules/` 并存）。当前 allow-list：两个 runner 脚本，以及只读 git 命令（`status`, `log`, `diff`, `rev-parse`, `merge-base`, `check-ignore`, `branch --show-current`, `branch -vv`, `ls-remote`）。

## Key design constraints

The repository-wide hard constraints — separation of concerns, exchange code
behind adapters, no import-time side effects, live trading disabled by
default (fail closed), financial time-series validation (chronological
walk-forward with purging and embargo), raw data immutability, and separate
gross/net reporting — are defined in `AGENTS.md` and are binding here.
Claude Code additionally enforces **strict typing**: `mypy --strict` passes
on `src` and `tests` (Python 3.13), per the Commands section below.

## Technical baseline (future architecture)

The approved architecture (see `docs/architecture/technical-baseline.md`) prescribes this data pipeline once Research MVP features are implemented:

```
Binance public data → Immutable Raw Parquet + manifest → Self-built Canonical schema
→ Polars transforms + DuckDB queries → Features, labels, research datasets
→ Vectorized research backtest → LightGBM (primary) / XGBoost (challenger)
→ NautilusTrader event-driven verification → Shadow → Binance Demo → Small-capital Live
```

NautilusTrader is the chosen event-driven backtest and execution base. Polars/DuckDB are the primary data processing engines (pandas only for compatibility/display). MLflow for experiment tracking. PostgreSQL, Prometheus, Grafana, and Valkey are planned for later phases but must not be introduced before their corresponding Epics.

Prohibited without an approved architecture Issue: microservices, Kubernetes, distributed message queues, multi-exchange abstractions, HFT market making, reinforcement learning, automatic live-trading activation.

## Issue-driven development

All work is tracked in GitHub Issues (the `PhoenixSss/tracequant` repo). The
issue-driven workflow, leaf-Issue-first context acquisition, expansion
triggers, and comments / Parent / Epic policies are defined in `AGENTS.md`
and apply unchanged to Claude Code sessions. Current work scope is read from
the active GitHub Issue and Project state, not from this file.
