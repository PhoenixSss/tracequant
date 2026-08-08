# AGENTS.md

## Project purpose

TraceQuant is an auditable research-to-live quantitative trading system for cryptocurrency perpetual futures.

Correctness, reproducibility, risk control, and auditability have higher priority than performance, abstraction, or feature count.

## Issue-driven workflow

All requirements and implementation tasks are tracked in GitHub Issues.

Before changing code:

1. Read the current assigned GitHub Issue body — it is the primary
   specification for the work item.
2. Confirm that the Issue has the `codex:ready` label.
3. Read the applicable repository instructions in this file (and
   `CLAUDE.md` when working in Claude Code).
4. Read only the code, tests, and referenced sources needed to implement the
   Objective / Requirements / Acceptance Criteria.
5. Do not implement an Issue that is blocked or insufficiently specified.
6. Treat the Issue scope, acceptance criteria, and out-of-scope section as binding.
7. One implementation Issue should normally produce one Pull Request.

GitHub Issues are the source of truth for planned work.
Repository documentation is the source of truth for current implemented behavior.

### Default context (leaf-Issue-first)

The current leaf Issue body is the primary source of the current work item
specification. A normal implementation Task starts with:

- the current leaf Issue body;
- applicable repository instructions (this file; the Claude-specific
  supplement in `CLAUDE.md` when running Claude Code);
- the code and tests relevant to the Task;
- the minimum input required by an explicitly invoked workflow Skill;
- the Git / GitHub object identities that must be verified at the current
  stage (base/head SHAs, PR identity, branch identity).

The leaf Issue body must not override platform constraints, maintainer
constraints, security boundaries, repository hard invariants, active
ADR / durable architecture decisions, or safety requirements. A Task must not
be required to restate its Parent Feature, Epic, repository architecture, or
standard workflow.

### Default exclusions

Unless an expansion trigger below applies, a normal Task start must not
default to reading the full text of:

- Issue comments (all history);
- the complete Parent Feature body;
- the complete Parent Epic body;
- sibling or descendant Issues;
- complete blocking / related Issue bodies;
- all linked documentation;
- all ADRs;
- repository roadmap;
- historical workflow reports, Skills, or benchmark / experiment archives;
- Delivery / Review / Closeout history sessions;
- architecture documentation unrelated to the current change.

A link's existence does not by itself require reading the target's full text.

### Context expansion triggers

Expand another source only when the current Task needs it:

- **Explicit reference**: the leaf Issue explicitly requires a Parent
  requirement, dependency, document, ADR, report, benchmark, specification,
  or code location — read only the part needed to complete that requirement.
- **Missing or ambiguous specification**: the leaf Issue cannot safely
  determine expected behavior, scope boundary, acceptance semantics,
  compatibility requirement, or dependency contract — expand upstream by
  requirement precedence. Never guess to fill a missing requirement.
- **Conflict**: the leaf Issue, a Parent, an ADR, a repository invariant, or
  the current implementation conflict — read enough to locate the conflict
  source. Fail closed / Human Gate when it cannot be safely resolved.
- **Hard dependency**: only when a dependency's state or contract affects
  whether the current Task can be implemented, verified, or merged — prefer
  native metadata and the necessary section over the dependency's full
  history.
- **Safety / architecture**: when the change may affect live-trading safety,
  credentials, order/risk authority, data integrity, time-series leakage,
  public compatibility, an architecture boundary, or irreversible state/data
  mutation — load the applicable durable repository rule / ADR /
  documentation. Never skip safety constraints to reduce context.
- **Verification**: when Acceptance Criteria point at a test fixture,
  benchmark, report, protocol, or frozen evidence — read only what that
  verification requires.

### Progressive retrieval

Expand context only when necessary:

1. Read the minimum relevant source or section.
2. Evaluate whether the information is sufficient.
3. Expand further only when still insufficient.

Never expand one reference into a full document, then its Parent, then its
comments, then recursively up the hierarchy. Unbounded recursive expansion
is forbidden. When an extra source is read, be able to state why it is
needed, what it is, and which requirement / ambiguity / risk it resolves.
Ordinary code reads during implementation do not require a formal evidence
artifact.

### Comments policy

Issue comments are discussion / decision history, not default startup
context. Read comments only when:

- the current Issue body explicitly references a historical decision;
- the current specification has ambiguity the body alone cannot resolve;
- provenance of a requirement change must be confirmed;
- the maintainer explicitly asks;
- the current body conflicts with another active source and the change origin
  must be located.

Never load all comments just because the Issue has comments. Historical
comments never silently override the current Issue body.

### Parent / Epic policy

Parent Feature and Epic bodies are upstream scope/outcome sources, not full
execution inputs. A normal Task does not default to reading them completely.
When the leaf Issue already defines Objective, Requirements, Acceptance
Criteria, Scope Boundary, and required References, execute directly. Expand
to a Parent only when the Task cannot safely determine scope, behavior, or a
durable constraint — and read only what resolves that question, not the
complete Parent specification. A single Parent constraint never requires the
Parent full body plus the Epic full body plus all siblings.

### Deterministic facts vs model context

Verifying a mechanical fact with a deterministic tool is not the same as the
model consuming the full original text. Readiness, review, and closeout may
verify Issue state, labels, Parent identity, blocker state, Project Status,
PR head, and checks with deterministic queries (Runner snapshots). Doing so
does not require injecting the complete Parent body, comments, or history
into the agent context. The current leaf Issue body is the only default
full-text business source.

### feature-completion-audit exception

`feature-completion-audit` is a hierarchy-aware audit: its purpose requires
the target Feature, the relevant child Issue hierarchy, completion state, and
implementation / validation evidence. The leaf-Issue-first default does not
apply to it. It must still limit acquisition to the hierarchy, state, and
evidence the audit needs — not historical comments, unrelated docs / ADRs,
the roadmap, sibling Feature history, or general workflow reports.

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
- Modules must not perform I/O, read env vars, create directories, or cache
  global singletons on import.

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
- Use chronological walk-forward validation with purging and embargo rather than random train/test splitting.
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
