---
name: task-pr-review-runner
description: Independently and strictly read-only review one maintainer-specified Task PR in a fresh session. Lock base/head/effective diff, inspect the complete change, run Review Runners, classify findings, output one fixed verdict, and when the verdict is not passing emit a bounded remediation handoff for task-delivery-runner. Never fix, write GitHub state, submit a review, merge, close Issues, perform closeout, or assess Feature completion.
---

# Task PR review runner

Use this Skill for one existing Task PR in a new session that did not
participate in specification interpretation, implementation, fixes, commit,
push, or PR creation. Otherwise stop with:

```text
本会话不能提供独立审查
```

A Delivery handoff locates the object but is not correctness evidence.

## Standard invocation

```text
请使用 task-pr-review-runner，独立只读审查
[Task] <当前完整标题> #<Task编号>
对应的 PR #<PR编号>。

Expected base SHA: <base SHA>
Expected head SHA: <head SHA>
```

Task and PR numbers are primary keys; the current Issue title is canonical.
A request may limit execution to one named Phase. Verify prior Phase facts and
stop at the requested boundary.

## Policies and Runner interface

Read applicable `AGENTS.md` / `AGENTS.override.md` and:

```text
.agents/policies/workflow-evidence.md
```

Use the current repository Runner interfaces in this order:

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py review \
  --task <TASK> \
  --pr <PR> \
  --expected-base-sha <LOCKED_BASE_SHA> \
  --expected-head-sha <LOCKED_HEAD_SHA>

tools/agent_workflow/wsl2_validation_runner.py workflow-review \
  --base-sha <LOCKED_BASE_SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py \
  recheck --snapshot-id <LOCKED_SNAPSHOT_ID>
```

The initial snapshot defines the reviewed identity. `workflow-review` is the
independent CI-equivalent validation for the locked head. `recheck` verifies
stability before verdict.

For `partial`, `unknown`, `fail`, truncation, schema mismatch, or drift, inspect
only the named facts or failed commands. Semantic review may read any code or
context needed to judge correctness.

Record the actual Review Skill, Runner, profile/schema, repository, reviewed
base/head/diff, and content hashes in the evidence artifacts. When the PR
changes Skills, Runners, Rules, or workflow governance, review those changes,
their tests, permissions, and failure behavior explicitly; Runner success alone
is not proof of correctness.

## Execution model

Claude Code executes commands directly in the user's shell environment — there
is no sandbox isolation layer. Git, `gh`, Python, subprocess, network, and
filesystem access all work natively. The Codex Guardian sandbox/elevated routing
model does not apply.

Runner commands are deterministic Python CLI tools invoked from the repository
root on the WSL2 Linux filesystem. They are never wrapped in `python`,
`bash -c`, `sh -c`, `uv run`, command substitution, pipelines, redirection, or
a generic shell string. Each Runner call is a single Bash tool invocation.

The Bash tool itself may prompt for user approval on first use. To suppress
these prompts for the documented Runner invocations, pre-authorize in
`.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(tools/agent_workflow/wsl2_github_evidence_runner.py *)",
      "Bash(tools/agent_workflow/wsl2_validation_runner.py *)"
    ]
  }
}
```

Runner commands can fail, but not because of sandbox restrictions. Read the
Runner's own output to classify:

- `pass`: all commands succeeded, inspect the compact digest.
- `fail`: one or more commands returned non-zero. Read the named failure
  artifact before deciding how to proceed.
- `blocked`: a Runner precondition failed (e.g. unclean worktree, wrong branch,
  wrong cwd, identity mismatch). Fix the precondition; do not retry with
  different arguments.
- `partial` / `unknown`: bounded diagnostics in the artifact — inspect only the
  named gates.

Never fall back to an equivalent direct command chain after a Runner result.
Never retry a Runner command with modified arguments to work around a failure.

## Permission boundary

Review is strictly read-only for code, Git history, GitHub, Project, reviews,
threads, labels, Relationships, and lifecycle state. It may fetch refs, use one
isolated worktree for the locked reviewed head, run validation, and write exact
ignored local evidence artifacts.

It does not authorize file fixes, Issue/PR/Project edits, GitHub Review
submission, thread resolution, commits, pushes, merge, Issue close, branch
deletion, closeout, or Feature completion assessment.

## Phase 1: identify and lock

Generate `review`. Verify same-repository Task/PR, Task type/state, exact closing
linkage, PR open and non-Draft, expected base/head, complete files/commits,
checks, reviews, threads, mergeability, and Required-Checks classification.

Lock and report:

```text
Reviewed base SHA
Reviewed head SHA
Merge base / effective-diff baseline
Effective diff digest
Complete file and commit inventory
Snapshot ID
```

Stop on material repository, Task, linkage, state, base, or head mismatch.

## Phase 2: read complete evidence

Independently read the complete Task specification/comments/Relationships, PR
body and effective diff, every changed file in context, commits,
tests/docs/config/public interfaces, relevant unchanged code, current
reviews/threads/checks, workflows, tooling, and safety rules.

Do not inherit Delivery conclusions or accept comments, test names, or green
checks without inspecting coverage.

## Phase 3: semantic review

Evaluate scope and acceptance, correctness and edge cases, error handling,
typing/compatibility/public behavior, negative/regression tests, documentation,
credentials/UTC/data/financial/live-mode safety, dependency and architecture
decisions, generated/prohibited artifacts, and workflow-governance safety.

Do not fix findings. Repairs require a separately authorized implementation
session and a new independent review of the resulting head and effective diff.

## Phase 4: validation

Run `workflow-review`. Do not reuse Delivery validation. A real validation
failure, an incomplete applicable check, stale evidence, or unavailable required
evidence prevents an unconditional pass.

A recognized plan-limit `403` is not a successful Required-Checks query.
Approved fallback check evidence may support a passing verdict only when it is
complete, consistent, and non-contradictory.

## Phase 5: stability recheck

Run `recheck` against the initial snapshot. Recollect identity, base/head,
effective diff, files/commits, checks, reviews, and threads. Any new commit or
base/head/effective-diff change invalidates the review and requires a new
independent session. Evaluate check/thread-only changes under current gates.

## Findings and verdicts

Use exactly: Blocking, High, Medium, Low, and Nit. Cite precise files/lines, Task
clauses, state, or validation evidence. Any unresolved Blocking/High/Medium
finding prevents pass.

Output exactly one:

```text
通过，可以人工合并
```

Only when scope/acceptance, validation, checks, reviews/threads, and stability
pass with no unresolved Blocking/High/Medium finding.

```text
有条件通过，不得合并
```

When no confirmed Blocking/High/Medium code defect exists but an objective gate
is pending, unavailable, ambiguous, contradictory, unstable, or not merge-ready.

```text
不通过，需要修复
```

When a Blocking/High/Medium finding remains, scope/acceptance is wrong,
validation fails, or identity, permission, or safety boundaries fail.

## Remediation handoff

For `有条件通过，不得合并` or `不通过，需要修复`, emit one compact handoff after
the verdict:

```text
Remediation handoff

Task: #<Task>
PR: #<PR>
Reviewed head SHA: <SHA>
Verdict: <verdict>

Required remediation:
- [F1][Blocking|High|Medium] <defect, precise evidence, expected behavior>

Objective gates:
- <pending, unavailable, ambiguous, contradictory, or unstable gate>

Maintainer decision required:
- <scope, specification, public behavior, or architecture decision>
```

Rules:

- include only findings that caused the non-passing verdict;
- include objective gates that require recheck or waiting;
- include decisions that cannot be resolved without maintainer authorization;
- exclude Low and Nit findings unless the maintainer explicitly made them
  required;
- state the defect and expected behavior, but do not design the implementation;
- preserve finding IDs so the next Review can map repairs to the original
  evidence.

End with this exact remediation prompt populated with current identities:

```text
请按 task-delivery-runner 修复
[Task] <当前完整标题> #<Task编号>
对应 PR #<PR编号> 的独立审查问题，
并继续处理，直到 PR 再次准备好接受新的独立审查。

Review remediation handoff:

<上述 remediation handoff>
```

A passing verdict does not emit a remediation handoff.

## Report and recovery

On clean success, report canonical Task/PR URLs, reviewed base/head/diff digest,
changed files/commits, acceptance coverage, findings, validation/checks,
reviews/threads/mergeability, limitations, actions not performed, one fixed
verdict, and:

```text
Reviewed head SHA: <actual SHA>
```

Use a detailed report for any finding, fallback, `partial`/`unknown`, failure,
drift, conflict, or maintainer decision. For a conditional or failing verdict,
include the bounded remediation handoff and exact `task-delivery-runner` prompt.
Remove any temporary worktree by its exact path without destructive broad
cleanup. Never inherit an earlier verdict.
