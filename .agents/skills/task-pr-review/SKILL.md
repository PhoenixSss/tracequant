---
name: task-pr-review
description: Independently and strictly read-only review one maintainer-specified GitHub Task PR in a fresh session. Lock trusted base/head/diff, inspect full code and evidence, run current validation, classify findings, and output one fixed verdict. Do not fix, write GitHub state, submit a review, merge, close Issues, perform closeout, or assess Feature completion.
---

# Task PR review

Use this Skill only for one existing Task PR. Run it in a new session that must not have
participated in its specification interpretation, design, implementation, test
fixes, commit, push, or PR creation. Otherwise stop with:

```text
本会话不能提供独立审查
```

A delivery handoff locates the object but is not correctness evidence.

## Standard invocation

```text
请使用 task-pr-review，独立只读审查
[Task] <当前完整标题> #<Task编号>
对应的 PR #<PR编号>。

Expected base SHA: <base SHA>
Expected head SHA: <head SHA>
```

The Task number and PR number are primary keys; current Issue title is canonical.
A PR-only request may resolve exactly one same-repository open Task from closing
linkage, but the documented production form remains complete identity plus SHAs.

## Rules, trust, and tools

Read applicable `AGENTS.md` / `AGENTS.override.md` and trusted versions of:

```text
.agents/skills/task-pr-review/SKILL.md
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
.agents/policies/task-workflow-telemetry.md
```

Use current Task/PR, templates, workflows, `pyproject.toml`, and lock files.
Historical reports are supporting evidence only. Run current checks through
`tools/agent_workflow/workflow_validation.py` from the trusted control plane.

This review is strictly read-only for code, Git history, GitHub, Project,
reviews, threads, labels, Relationships, and lifecycle state. It may fetch refs,
use one detached temporary review worktree, run validation, and write only exact
ignored local evidence/validation/telemetry artifacts.

It never fixes files, edits Issue/PR/Project state, submits Approve or Request
Changes, resolves threads, commits, pushes, merges, closes Issues, deletes Task
branches, performs closeout, or assesses Feature completion.

## Trusted control plane

Do not use reviewed rules to prove themselves.

Lock the actual PR base SHA before formal review. If the PR changes an applicable
agent file, workflow Skill, Evidence/Validation/Telemetry tool, or shared policy,
obtain `trusted_runner.py` from the PR base or execute it in a detached base
worktree. Use it to run Evidence and Validation from that same base. PR-head
versions are review objects only.

Record:

```text
trusted base SHA
runner source SHA and content digest
reviewed head SHA
effective diff digest
trusted governance files
```

If base control plane and head review object cannot be isolated, stop without a
passing verdict. When the base predates this Skill, use maintainer-provided
independent read-only instructions; the introduced Skill cannot authorize its
own first review.

## Phase 1: identify and lock

Generate one current `pr-review-snapshot` using the trusted control plane. Verify:

- repository, Task, and PR exist in the same repository;
- Task is open, `type:task`, `codex:ready`, not `codex:blocked`, and Project
  Status is `Review` for ordinary review;
- canonical title matches supplied title;
- PR is open, non-Draft, targets the expected base, and has exactly the intended
  Task closing linkage;
- actual base/head SHAs match supplied values when provided;
- complete changed-file and commit inventories are available;
- checks, reviews, unresolved threads, mergeability, and Required-Checks
  configuration are distinguished correctly.

A derived ProjectV2 title is not canonical. Stop on material identity,
repository, linkage, state, base, or head mismatch.

Lock and report:

```text
actual base branch and SHA
actual head branch and SHA
merge-base/effective diff baseline
effective diff digest
complete file and commit inventory
```

## Phase 2: read full evidence

The snapshot does not replace content review. Independently read:

- complete Task body and comments, Parent, dependencies, fields, and
  Relationships;
- complete PR body, full effective diff, every changed file in context, and all
  commits;
- tests, documentation, configuration, public interfaces, and relevant unchanged
  code around the diff;
- current check runs, reviews, review comments, unresolved threads, and requested
  changes;
- current workflows, project tooling, and applicable safety rules.

Do not adopt delivery claims, comments, test names, or green checks as proof
without inspecting what they cover.

## Phase 3: semantic review

Review at least:

- exact Task scope and out-of-scope boundaries;
- every acceptance criterion and documented exception;
- correctness, edge cases, error handling, typing, compatibility, and public
  behavior;
- test quality, negative paths, regression coverage, and whether tests could
  pass while required behavior is broken;
- documentation and operational guidance affected by the change;
- secrets, credentials, UTC/data correctness, financial safety, and live-trading
  defaults where applicable;
- dependency and architecture decisions;
- tracked/untracked/generated-file scope and prohibited local artifacts;
- governance bootstrap safety when governance is changed.

Do not fix findings. A repair requires a separately authorized implementation
session and a new independent review of the resulting head/effective diff.

## Phase 4: validation

Run the shared Validation runner from the trusted base control plane against the
reviewed head context. Run all current applicable checks and Skill validators;
never reuse delivery results. A GitHub plan-limit `403` is not a successful
Required-Checks query. No configured Required Check is also not a fictional
failure when complete fallback evidence consistently shows none.

Any real validation failure, pending/incomplete applicable check, stale result,
or unavailable required evidence prevents an unconditional passing verdict.

## Phase 5: stability recheck

After semantic review and validation, run `pr-review-recheck` from the same
trusted control plane. Recollect current Task/PR facts and compare identity,
base/head, effective diff digest, files/commits, checks, reviews, and threads.

Any new commit, head/base/effective-diff change invalidates the review. Start a
new independent review session for the new object. A check/thread-only change
must be evaluated under current gates before verdict.

## Severity

Use exactly:

- **Blocking**: unsafe to merge; core correctness, permissions, credentials,
  funds/data safety, or repository history at risk;
- **High**: major correctness, safety, scope, or lifecycle defect;
- **Medium**: clear pre-merge defect, rule/test/documentation gap, or conflict
  that should be fixed before merge;
- **Low**: non-blocking maintainability, clarity, or minor residual risk;
- **Nit**: wording, formatting, or tiny consistency issue.

Order findings by severity and cite exact files/lines, Task clauses, GitHub state,
or validation evidence. Any unresolved Blocking, High, or Medium finding
prevents a passing verdict.

## Fixed verdicts

Output exactly one:

```text
通过，可以人工合并
```

Only when acceptance and scope are satisfied, validation and all applicable
check runs are successful and complete, no requested changes or unresolved
blocking threads remain, no Blocking/High/Medium finding remains, and
base/head/diff stayed stable. A documented plan-limit endpoint failure alone does
not block this verdict when approved fallback evidence is complete, consistent,
and non-contradictory.

```text
有条件通过，不得合并
```

When no confirmed Blocking/High/Medium code defect exists, but an objective gate
is pending, unavailable, ambiguous, contradictory, unstable, or not yet
merge-ready.

```text
不通过，需要修复
```

When any Blocking/High/Medium finding remains, acceptance/scope is wrong,
critical validation fails, or permissions, safety, identity, or trusted-control
boundaries are violated.

## Report contract

On a clean success path, use the compact shared report with:

```text
Task and PR canonical identity / URLs
Trusted base and runner source
Reviewed base/head SHA and effective diff digest
Changed-file and commit summary
Acceptance coverage summary
Findings by severity
Local validation and remote checks
Reviews / unresolved threads / mergeability
Limitations and actions not performed
One fixed verdict
Reviewed head SHA: <actual head SHA>
```

Use a detailed report for findings, failed/pending evidence, drift, fallback,
conflict, or maintainer decision. Do not copy complete Task/PR bodies, complete
diff, or complete successful validation logs.

## Temporary worktree and recovery

A temporary worktree must be unique, detached at the exact locked commit, never
modify reviewed files, and be removed by exact path without `git clean`. Re-run
from current facts after any interrupted review, repaired PR, changed SHA,
resolved blocker, or stale evidence. Never inherit an old verdict.

## Telemetry

If a maintainer-started run is active, perform one lightweight status check and
append one aggregate `task-pr-review` summary using facts already produced.
Record independent review run, Evidence/Validation calls, report/handoff size,
findings, fallbacks, retries, drift, and invalidation when known. Telemetry never
influences findings or verdict and never authorizes a write.
