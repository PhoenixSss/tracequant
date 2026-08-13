---
name: task-closeout
description: Close out one maintainer-specified Task after the maintainer manually merged its PR. Verify merge and auto-close facts, synchronize main, run post-merge validation, converge final Task metadata when authorized, and delete only the exact verified Task branches. Never merge, manually close an Issue, repair code, clean unrelated branches, or assess Feature completion.
---

# Task closeout

Use this Skill only after the maintainer states that a specific PR was manually
merged and requests post-merge verification and cleanup for the exact associated
Task. The statement is an entry request, not proof.

This Skill never merges a PR.

## Standard invocation

```text
PR #<PR编号> 已由我人工 Squash Merge。

请使用 task-closeout，完成
[Task] <当前完整标题> #<Task编号>
及 PR #<PR编号> 的合并后核验与分支清理。
```

Task and PR numbers are primary keys; current Issue title is canonical.

## Rules and tools

Read applicable `AGENTS.md` / `AGENTS.override.md` and:

```text
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
```

Use current Task/PR, refs, worktrees, workflows, `pyproject.toml`, and lock files.
Use:

```text
python tools/agent_workflow/workflow_evidence.py closeout-plan ...
python tools/agent_workflow/workflow_validation.py run --phase closeout ...  # include Skill validators when merged scope changed governance
python tools/agent_workflow/workflow_evidence.py closeout-final ...
```

The Evidence tool is read-only. It verifies and locks facts but does not perform
metadata writes, `main` synchronization, or branch deletion. Do not repeat the
full legacy read sequence after a valid plan unless a gate is unknown, failed,
truncated, conflicting, or requires semantic inspection.

## Permission boundary

After every exact gate passes, this invocation may:

- fetch current refs;
- verify Task/PR identity, merge, closing linkage, Issue closure, Project state,
  labels, checks, and branch identity;
- fast-forward local `main` to `origin/main` only;
- run current post-merge validation;
- precisely converge this Task's Project Status to `Done` and lifecycle label to
  `codex:ready` when current facts require and allow it;
- delete only the exact verified remote and local Task branch.

It never authorizes:

- merge;
- manual Issue close;
- source/test/document/config repair;
- commit or push;
- Parent, Priority, Size, Phase, Target, or Relationship changes;
- unrelated branch or worktree cleanup;
- Feature completion advice or action;
- force push, `--admin`, protection bypass, `git reset --hard`, or `git clean`.

## Entry and identity gates

Generate a current `closeout-plan` and independently verify:

- repository, Task, and PR identity;
- supplied and canonical Task titles match;
- PR closing linkage identifies the exact Task;
- PR is actually `MERGED` and not merely closed;
- actual head SHA and merge commit match supplied values when provided;
- merge method is Squash, or a non-Squash method has explicit maintainer approval
  before continuing;
- Task closed automatically through the correct linkage;
- Project and label facts are read independently from Issue state;
- local workspace and worktrees permit safe synchronization and exact cleanup.

If the Task did not close automatically, stop. Do not manually close it. A
ProjectV2 derived title may lag and is not canonical.

The plan must record exact branch, head SHA, merge SHA, `origin/main`, check
state, local/remote branch existence, and `apply_authorized=false`. It is not
permission to delete.

## Phase 1: synchronize and verify merged result

Start from a clean workspace. Fetch current refs, switch to local `main`, and
fast-forward only. Stop on divergence, local changes, worktree conflict, or any
operation requiring reset, force, or bypass.

After synchronization verify:

```text
local main == origin/main
verified merge commit is reachable from origin/main
merged files and tree match the reviewed/merged Task scope
no local execution/evidence/validation artifacts are tracked or staged
```

For Squash Merge, ancestry will not necessarily contain the old Task tip; use
merge facts, closing linkage, reviewed head, changed-file scope, and tree
comparison rather than inventing ancestry.

## Phase 2: post-merge validation and checks

Run the shared Validation runner against synchronized `main`. This is a new
post-merge observation; do not reuse delivery or review results. Governance
changes require applicable Skill validators.

Read remote-main check runs and Required-Checks configuration. Preserve the
meaning of no configured Required Checks, plan-limit `403`, pending, failed,
cancelled, skipped, stale, and unavailable checks. A real failure or unresolved
required gate stops closeout before metadata convergence or cleanup.

## Phase 3: final Task metadata

Read Issue state, Project Status, and Codex labels independently. The expected
final state is:

```text
Issue: CLOSED by correct PR linkage
Project Status: Done
Codex label: codex:ready
codex:blocked: absent
```

When automation already produced the correct state, verify and do nothing.
When only the exact final Project/lifecycle convergence is missing, this Skill
may apply that exact write and re-read it. Do not change any other field or infer
Feature completion from Parent/sub-issue counts.

## Phase 4: exact branch cleanup

Resolve the exact Task branch from verified PR facts. Before any deletion verify:

- branch belongs to the reviewed Task/PR and repository;
- expected head SHA matches the verified PR head;
- no other worktree uses it;
- merged result, Issue closure, synchronized main, validation, checks, and final
  metadata are all complete;
- deletion affects no external, unrelated, default, protected, or ambiguous
  branch.

Delete the exact remote branch only after all gates and verify absence.

For the exact local branch, try safe `git branch -d` first. A precise `-D` is
allowed only for the same locked branch when:

- the PR was verified as Squash Merge;
- remote branch deletion/absence is verified;
- local branch tip equals reviewed PR head;
- `git diff --quiet main <exact-branch>` proves no remaining tree difference;
- no worktree uses it;
- all other closeout gates already passed.

Never use wildcard, broad, guessed, or unrelated branch cleanup.

## Final stability verification

Run `closeout-final` against the stored plan after synchronization, metadata, and
cleanup. Recollect merge, Issue, Project, main, checks, validation facts, and
exact branch state. Any material drift, new check failure, reopened Issue,
changed merge fact, or unexpected branch state stops a success conclusion.

## Mandatory pauses

Pause before the related write when identity, linkage, merge, Issue auto-close,
merge method, workspace, worktree, main synchronization, tree comparison,
validation, check, Project option, label, branch ownership, or exact deletion
facts are uncertain or conflicting. Also pause when a repair commit, manual Issue
close, merge, force operation, bypass, destructive cleanup, or maintainer design
decision would be required.

Report completed safe steps, exact failing gate, current refs/state, and the one
next decision.

## Recovery and idempotency

Every run reads current facts. Verify completed actions rather than repeating
them. Resume from the first unverified gate when metadata is already correct,
`main` is already synchronized, validation is partly complete, or exact branches
are already absent. Never recreate a deleted branch.


## Compact closeout report

On clean success include:

```text
Task / PR canonical identity and URLs
PR base/head, reviewed head, merge method, merge SHA
Issue, Project, and Codex-label final state
local main / origin-main state
post-merge validation and remote checks
exact remote/local branch actions and whether -D was used
Parent/sub-issue facts without Feature judgment
limitations and actions deliberately not performed
```

Use a detailed report for any failure, fallback, drift, conflict, or maintainer
decision. Explicitly state that no merge, manual Issue close, repair commit,
unrelated cleanup, or Feature completion action occurred.
