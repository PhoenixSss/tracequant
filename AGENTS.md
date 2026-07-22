# AGENTS.md

## Project purpose

This repository implements a research-first cryptocurrency quantitative trading system.

Correctness, reproducibility, risk control, and auditability have higher priority than performance, abstraction, or feature count.

## Issue-driven workflow

All requirements and implementation tasks are tracked in GitHub Issues.

Before changing code:

1. Read the complete assigned GitHub Issue, including comments.
2. Confirm that the Issue has the `codex:ready` label.
3. Read its parent Issue, blocking Issues, linked documentation, and ADRs.
4. Do not implement an Issue that is blocked or insufficiently specified.
5. Treat the Issue scope, acceptance criteria, and out-of-scope section as binding.
6. One implementation Issue should normally produce one Pull Request.

GitHub Issues are the source of truth for planned work.
Repository documentation is the source of truth for current implemented behavior.

When handling a GitHub Task specification, implementation, Pull Request, review,
merge, post-merge verification, or branch cleanup, first read
`.agents/skills/task-lifecycle/SKILL.md`. This root `AGENTS.md` remains the
repository-level rule source. The Skill only supplements the Task lifecycle and
does not override system, developer, user, or more specific scoped instructions.

## Implementation rules

- Implement only the assigned Issue.
- Do not perform unrelated refactors.
- Prefer the smallest correct change.
- Do not silently expand scope.
- Do not add production dependencies without a documented reason.
- Preserve public interfaces unless the Issue explicitly changes them.
- Keep exchange-specific code behind adapters.
- Keep strategy logic independent from network and exchange clients.
- Keep risk decisions independent from strategy decisions.
- A strategy must never submit an exchange order directly.

## Financial safety

- Never enable live trading by default.
- Never submit live orders from tests.
- Demo, shadow, and live modes must be clearly separated.
- Live mode must require explicit configuration and fail closed.
- Never print, log, commit, or expose credentials.
- Never implement withdrawal functionality.
- The risk module has final authority to reject or reduce an order.
- When local and exchange state disagree, stop opening new positions.
- Unknown order state must be reconciled before retrying.
- Order submission must be idempotent.

## Data correctness

- Use timezone-aware UTC timestamps.
- Do not use naive datetime values in domain or storage code.
- Raw data must remain immutable.
- Missing values must not be silently converted to zero.
- Detect duplicate, missing, and out-of-order market data.
- Feature calculations must not access future observations.
- Fit preprocessing steps using training data only.
- Never tune parameters or thresholds using the final test set.

## Backtesting

- Report both gross and net performance.
- Include fees, funding, slippage, turnover, and fill assumptions.
- Never assume every limit order is completely filled.
- Distinguish signal time, order time, fill time, and return measurement time.
- Use chronological validation rather than random train/test splitting.
- Record the data range, parameters, code version, and data fingerprint.

## Verification

After implementation:

1. Run the most relevant tests.
2. Run repository lint and type-check commands.
3. Update documentation when behavior or interfaces change.
4. Report files changed, commands executed, results, limitations, and out-of-scope work.

Do not delete, skip, or weaken tests merely to make them pass.

## Prohibited premature complexity

Do not introduce the following without an approved architecture Issue:

- microservices;
- Kubernetes;
- distributed message queues;
- multi-exchange abstractions;
- high-frequency market making;
- reinforcement learning;
- automatic live-trading activation.
