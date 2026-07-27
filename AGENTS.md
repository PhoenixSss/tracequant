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

When the user specifies an already-created GitHub Task and requests the complete
pre-merge workflow through a Pull Request that is ready for independent review,
first read `.agents/skills/task-delivery/SKILL.md`.

When the user requests an independent read-only review of a Task Pull Request in
a new session, first read `.agents/skills/task-pr-review/SKILL.md`.

When the user states that a Pull Request was manually merged and requests
post-merge verification, state convergence, validation, or Task-branch cleanup,
first read `.agents/skills/task-closeout/SKILL.md`.

When the user requests an independent read-only completion audit of a specified
open GitHub Feature before maintainer closeout, first read
`.agents/skills/feature-completion-audit/SKILL.md`.

The three Task workflow Skills start only after the user identifies an existing
Task or Task Pull Request. They do not identify, split, plan, draft, choose, or
create new Tasks. They do not assess, recommend, or perform Feature completion.
No Task workflow Skill may merge a Pull Request.

Feature completion is audited only through a separately invoked, independent,
strictly read-only `feature-completion-audit` session. That Skill may recommend
completion gaps but does not create Tasks, close the Feature, set Project `Done`,
or assess Epic completion. Feature closeout remains a maintainer manual gate.

The independent PR Review Skill must run in a session that did not participate
in implementation or modification of the PR. It is strictly read-only, does not
submit a GitHub Review, does not fix findings, does not change Issue/PR/Project
state, and does not merge.

When the user provides both a Task number and title, treat the Issue number as
the primary key and the current GitHub Issue title as the canonical title. Stop
before writes when the supplied title and numbered Issue clearly identify
different work.

A final merge decision requires an independent read-only Pull Request review in
a separate session through `task-pr-review`, followed by maintainer manual merge.

Before a repository workflow Skill listed above executes a command, it must read
`.agents/policies/command-execution.md` and may read the optional local
`.agents/execution-profile.local.toml`. The local profile is ignored by Git and
must not be committed. It may select an execution context only after the active
Skill authorizes the command; it never expands lifecycle or GitHub permissions.

The repository does not run Task workflow Token telemetry. Token-consumption
analysis is performed outside this repository from Codex rollout logs plus
maintainer-supplied Task metadata. Raw rollout logs and generated external Token
reports must not be committed. Whether external analysis is available or
successful never changes workflow permissions, gates, validation, findings,
verdicts, Merge authorization, or Feature completion evidence.

Deterministic workflow fact collection and compact validation are governed by
`.agents/policies/workflow-evidence.md`. Repository workflow Skills use
`tools/agent_workflow/workflow_evidence.py` and
`tools/agent_workflow/workflow_validation.py` to replace repeated mechanical
command chains, while retaining all semantic review, safety, and lifecycle
judgment. Local evidence and validation artifacts remain in exact ignored
`.agents/evidence.local/` and `.agents/validation.local/` directories. A PR that
changes governance or these tools must be reviewed using the trusted PR base
control plane through `tools/agent_workflow/trusted_runner.py` or an equivalent
trusted detached worktree.

This root `AGENTS.md` remains the repository-level rule source. Repository Skills
supplement these rules and do not override system, developer, user, or more
specific scoped instructions.

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
