# AGENTS.md

## Project purpose

TraceQuant is an auditable research-to-live quantitative trading system for cryptocurrency perpetual futures.

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

When the user explicitly invokes `task-delivery-runner`, first read
`.agents/skills/task-delivery-runner/SKILL.md`. When the user explicitly invokes
`task-pr-review-runner`, first read
`.agents/skills/task-pr-review-runner/SKILL.md`.

Historical `.agents/skills/task-delivery/SKILL.md` and
`.agents/skills/task-pr-review/SKILL.md` are retained only as explicit benchmark
baselines. Never select, combine, or fall back to them unless the maintainer
names that exact Skill.

When the user states that a Pull Request was manually merged and requests
post-merge verification, state convergence, validation, or Task-branch cleanup,
first read `.agents/skills/task-closeout/SKILL.md`.

When the user requests an independent read-only completion audit of a specified
open GitHub Feature before maintainer closeout, first read
`.agents/skills/feature-completion-audit/SKILL.md`.

Workflow Skills start only after the user identifies an existing Task, Task PR,
or Feature. They do not identify, split, plan, draft, choose, or create new
Tasks. No workflow Skill may merge a Pull Request.

Independent PR Review must run in a fresh session that did not participate in
implementation or remediation of the reviewed head. It is strictly read-only,
does not submit a GitHub Review, does not fix findings, does not change
Issue/PR/Project state, and does not merge. A non-passing Review may emit a
bounded remediation handoff for `task-delivery-runner`; any new commit requires
a new independent Review session.

When the user provides both a number and title, treat the number as the primary
key and the current GitHub title as canonical. Stop before writes when they
clearly identify different work.

A final merge decision requires a passing `task-pr-review-runner` result for the
current PR head, followed by maintainer manual merge.

Before a repository workflow Skill executes a command, it must read
`.agents/policies/command-execution.md` and may read the optional ignored
`.agents/execution-profile.local.toml`. The local profile selects an execution
context only after the active Skill authorizes a command; it never expands
workflow permissions.

The repository does not run Task workflow Token telemetry. Token analysis is
performed outside this repository from Codex rollout logs and maintainer-supplied
Task metadata. Raw rollout logs and external Token reports must not be committed.
External analysis never changes permissions, gates, findings, verdicts, Merge
authorization, or completion evidence.

Deterministic workflow facts and compact validation are governed by
`.agents/policies/workflow-evidence.md`. Runner Skills use the current repository
front doors:

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
tools/agent_workflow/wsl2_validation_runner.py
```

The executed Skill, Runner, profile/schema, repository head, and content hashes
are recorded for reproducibility. Workflow object identities remain locked as
required: Task base, PR base/head/effective diff, audited main, and merge SHA.
There is no requirement to load a Skill or Runner from `main`, a PR base, or
another commit.

Local evidence and validation artifacts remain in exact ignored
`.agents/evidence.local/` and `.agents/validation.local/` directories.

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
