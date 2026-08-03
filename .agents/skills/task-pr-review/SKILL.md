---
name: task-pr-review
description: Independently and strictly read-only review one maintainer-specified Task PR in a fresh session using trusted-base fixed Evidence and Validation runners. Lock base/head/diff, inspect full code, classify findings, and output one fixed verdict. Never fix, write GitHub state, submit a review, merge, close Issues, perform closeout, or assess Feature completion.
---

# Task PR review

Use this Skill only for one existing Task PR in a new session that did not
participate in specification interpretation, implementation, test fixes, commit,
push, or PR creation. Otherwise stop with:

```text
本会话不能提供独立审查
```

A Delivery handoff locates the object but is not correctness evidence.

## Standard invocation

```text
请使用 task-pr-review，独立只读审查
[Task] <当前完整标题> #<Task编号>
对应的 PR #<PR编号>。

Expected base SHA: <base SHA>
Expected head SHA: <head SHA>
```

Task and PR numbers are primary keys; current Issue title is canonical.

## Rules, trust, and fixed runners

Read applicable agent rules and trusted base versions of this Skill and:

```text
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
```

A reviewed change must not control its own review. Obtain the bootstrap
`trusted_runner.py` from the locked PR base or a detached trusted-base worktree.
Then use the trusted-base fixed front doors in this exact ordering:

```bash
tools/agent_workflow/trusted_runner.py \
  --tool evidence-runner \
  --trusted-sha <LOCKED_BASE_SHA> -- \
  review \
  --task <TASK> \
  --pr <PR> \
  --expected-base-sha <LOCKED_BASE_SHA> \
  --expected-head-sha <LOCKED_HEAD_SHA>

tools/agent_workflow/trusted_runner.py \
  --tool validation-runner \
  --trusted-sha <LOCKED_BASE_SHA> -- \
  workflow-review \
  --base-sha <LOCKED_BASE_SHA>

tools/agent_workflow/trusted_runner.py \
  --tool evidence-runner \
  --trusted-sha <LOCKED_BASE_SHA> -- \
  recheck --snapshot-id <LOCKED_SNAPSHOT_ID>
```

The bootstrap itself must be trusted. If the current worktree copy differs from
the locked base, invoke the base copy from a detached trusted worktree; do not
let PR-head governance establish trust in itself.

The fixed Evidence Runner replaces the legacy Git/GitHub query chain. The fixed
Validation Runner replaces the raw validator and direct full `uv` command chain.
Do not run both. Detailed read-only queries are allowed only for a named
partial/unknown/fail/truncation/conflict or semantic code review need.

### Task #85 migration bootstrap

The Task #85 PR that first introduces trusted front-door extraction is reviewed
with the predecessor base control plane because its base cannot know the new
front-door tool choices. This one-time bootstrap is not a permanent fallback.
The independent review must verify this migration, and all later Tasks use the
commands above after Task #85 is merged.

## Permission boundary

Review is strictly read-only for code, Git history, GitHub, Project, reviews,
threads, labels, Relationships, and lifecycle state. It may fetch refs when the
trusted predecessor control plane requires it, use one detached temporary
worktree, run validation, and write only exact ignored local evidence/validation
artifacts.

It never fixes files, edits Issue/PR/Project state, submits Approve or Request
Changes, resolves threads, commits, pushes, merges, closes Issues, deletes
branches, performs closeout, or assesses Feature completion.

## Phase 1: identify and lock

Generate one trusted fixed `review` snapshot. Verify same-repository Task/PR,
Task open/type/labels/Project state, title, exact closing linkage, PR open and
non-Draft, expected base/head, complete files/commits, checks, reviews, threads,
mergeability, and Required-Checks classification.

Lock and report actual base/head, merge-base/effective diff baseline, effective
diff digest, and complete file/commit inventory. Stop on material identity,
repository, linkage, state, base, or head mismatch.

## Phase 2: read full evidence

The snapshot does not replace semantic review. Independently read the complete
Task specification/comments/Relationships, PR body and effective diff, every
changed file in context, commits, tests/docs/config/public interfaces, relevant
unchanged code, current reviews/threads/checks, workflows, tooling, and safety
rules.

Do not inherit Delivery conclusions or accept comments, test names, or green
checks without inspecting coverage.

## Phase 3: semantic review

Review exact scope and acceptance, correctness and edge cases, error handling,
typing/compatibility/public behavior, negative/regression tests, documentation,
credentials/UTC/data/financial/live-trading safety, dependency/architecture
decisions, generated or prohibited artifacts, and governance bootstrap safety.

Do not fix findings. Repairs require a separately authorized implementation
session and a new independent review of the resulting head/effective diff.

## Phase 4: trusted validation

Run only the trusted `workflow-review` fixed Validation profile. Never reuse
Delivery validation. A plan-limit `403` is not a successful Required-Checks
query; complete approved fallback check evidence may support a passing verdict
when consistent and non-contradictory.

A real validation failure, pending/incomplete applicable check, stale result, or
unavailable required evidence prevents unconditional pass. On failure, inspect
the bounded failed-command evidence; do not rerun a second full validation path.

## Phase 5: stability recheck

Run the trusted fixed `recheck` against the exact initial snapshot. Recollect
identity, base/head, effective diff, files/commits, checks, reviews, and threads.
Any new commit or base/head/effective-diff change invalidates the review and
requires a new independent session. Evaluate check/thread-only changes under
current gates before verdict.

## Severity and fixed verdicts

Use exactly Blocking, High, Medium, Low, and Nit. Every finding cites precise
files/lines, Task clauses, state, or validation evidence. Any unresolved
Blocking/High/Medium prevents pass.

Output exactly one:

```text
通过，可以人工合并
```

Only when scope/acceptance, trusted validation, checks, reviews/threads, and
stability all pass with no Blocking/High/Medium finding. A documented plan-limit
endpoint failure alone does not block when approved fallback evidence is
complete and consistent.

```text
有条件通过，不得合并
```

When no confirmed Blocking/High/Medium code defect exists but an objective gate
is pending, unavailable, ambiguous, contradictory, unstable, or not merge-ready.

```text
不通过，需要修复
```

When a Blocking/High/Medium finding remains, scope/acceptance is wrong, critical
validation fails, or permissions, safety, identity, or trust boundaries fail.

## Failure expansion, report, and recovery

A fixed Runner `partial`/`unknown` expands only the named fact. A `fail` or drift
stops the dependent verdict. Runner unavailable, schema/version mismatch, or
trusted-bundle failure is a control-plane gate failure; do not silently restore
the complete legacy chain.

On clean success report canonical Task/PR URLs, trusted base and bootstrap,
reviewed base/head/diff digest, changed files/commits, acceptance coverage,
findings, trusted validation/checks, reviews/threads/mergeability, limitations,
actions not performed, one fixed verdict, and `Reviewed head SHA`.

Use a detailed report for any finding, fallback, partial/unknown, failure, drift,
conflict, or maintainer decision. Temporary worktrees must be unique, detached,
read-only, and removed by exact path without `git clean`. Never inherit an old
verdict.
