---
name: task-closeout
description: Close out one maintainer-specified Task after maintainer manual merge. Verify the exact merge, synchronize main, run post-merge validation, converge final Task metadata, and delete only the verified Task branch. Never merge, manually close an Issue, repair code, clean unrelated branches, or assess Feature completion.
---

# Task closeout

Use this Skill only after the maintainer states that a specific PR was manually
merged and requests closeout for its exact Task. The statement authorizes
verification; it is not proof of merge. This Skill never merges a PR.

## Standard invocation

```text
PR #<PR编号> 已由我人工 Squash Merge。

请使用 task-closeout，完成
[Task] <当前完整标题> #<Task编号>
及 PR #<PR编号> 的合并后核验与分支清理。
```

Task and PR numbers are primary keys; the current Issue title is canonical.
A request may limit execution to one named Phase or to the documented
cleanup-only path.

## Policies and Runner interface

Read applicable agent rules and:

```text
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
```

Shared lifecycle semantics are owned by `docs/development/issue-workflow.md`
(§11 manual Squash Merge boundary, §13 Closeout). Read the minimal needed
section for the current phase; do not duplicate closeout-semantic prose in this
Skill.

Use the current repository Runner interfaces:

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py closeout-readonly \
  --task <TASK> \
  --pr <PR> \
  --expected-head-sha <REVIEWED_PR_HEAD_SHA> \
  --expected-merge-sha <MERGE_SHA>

tools/agent_workflow/wsl2_validation_runner.py workflow-closeout \
  --base-sha <PR_BASE_SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py \
  recheck --snapshot-id <CLOSEOUT_PLAN_SNAPSHOT_ID>
```

The first snapshot is the read-only closeout plan. `workflow-closeout` validates
the synchronized merged result. `recheck` verifies stability after metadata and
cleanup operations.

For `partial`, `unknown`, `fail`, truncation, schema mismatch, or drift, inspect
only the named facts or failed commands and preserve the original status.

A recognized Required-Checks plan-limit `403` keeps
`required_checks_configuration = unknown` and Evidence status `partial`.
Branch cleanup may still use the separate
`cleanup_eligibility.status = eligible-under-capability-limited-policy`; that
field authorizes only exact branch cleanup under the conditions below.

## Default context

Normal closeout verifies: Task/PR identity, reviewed head, merge identity,
CI/review status, Issue/Project lifecycle state, local/origin main, branch
state, and post-merge validation requirements. It does not default to
re-reading the complete business Issue hierarchy (Parent/Epic bodies),
business comments, or full implementation context. Those are read only when
an explicit anomaly trigger requires it — for example a linkage, closure, or
merge-identity conflict that the deterministic facts cannot resolve.

## Permission boundary

After all prerequisite gates pass, this Skill may fetch refs, fast-forward local
`main` to `origin/main`, run post-merge validation, set this Task's Project
Status to `Done`, restore lifecycle label `codex:ready`, remove
`codex:blocked`, and delete only the exact verified Task branch.

It does not authorize merge, manual Issue close, repair commits, code push,
hierarchy/Relationship changes, unrelated cleanup, Feature completion, force
push, `--admin`, protection bypass, destructive reset, or `git clean`.

## Entry gates

Generate `closeout-readonly`. Verify repository, Task/PR/title, exact closing
linkage, actual `MERGED` state, reviewed head, merge SHA/method/time, automatic
Issue closure, Project/label facts, workspace/worktrees, exact branches, and
checks.

If the intended PR did not automatically close the Task, stop. Do not close the
Issue manually.

## Phase 1: synchronize the merged result

Start from a clean workspace. Fetch refs, switch to local `main`, and
fast-forward only. Stop on divergence, changes, worktree conflict, or any need
for reset, force, or bypass.

Verify:

```text
local main == origin/main
merge SHA is reachable
merged tree and scope match the reviewed change
no tracked or staged execution artifacts exist
```

For Squash Merge, use merge facts, linkage, reviewed head, changed-file scope,
and tree comparison rather than ancestry assumptions.

## Phase 2: post-merge validation

Run `workflow-closeout` after synchronization. Read remote-main checks and
Required-Checks classification. Preserve none-configured, plan-limit `403`,
pending, failed, cancelled, skipped, stale, and unavailable states. A real
failure or unresolved required gate blocks metadata convergence and cleanup.

## Phase 3: final Task metadata

The expected final state is:

```text
Issue: CLOSED by the verified PR
Project Status: Done
Codex label: codex:ready
codex:blocked: absent
```

Verify existing automation first. Apply only missing exact Project/lifecycle
convergence and re-read it. Do not change any other field or infer Feature
completion.

## Phase 4: exact branch cleanup

Resolve the exact branch from verified PR facts. Before deletion confirm branch
ownership, expected head, no worktree use, merged result, Issue closure,
synchronized main, validation/checks, final metadata, and an unambiguous,
non-default, non-protected target.

Complete Issue-side proof that the locked merged PR closed the Task is required.
Unknown, partial, or conflicting closure evidence blocks cleanup.

When Required-Checks configuration is unknown only because of a recognized
plan-limit `403`, cleanup may proceed only when all of the following hold:

- `cleanup_eligibility.status` is exactly
  `eligible-under-capability-limited-policy`;
- observed check runs include at least one quality gate and all are successful
  terminal states;
- the final recheck is stable;
- `local main == origin/main == merge SHA`;
- local Task branch tip equals the reviewed PR head;
- if the remote Task branch still exists, its tip equals the reviewed PR head;
- if GitHub already deleted the remote branch, the Evidence Runner records
  `remote_branch_state = ALREADY_DELETED` only after the same PR/head/merge,
  effective-diff, squash-tree, and synchronized-main identity proof;
- PR-head tree equals merge tree;
- no worktree uses the branch;
- the cleanup plan contains no other branch.

Authentication, scope, permission, rate-limit, network, schema, service, or
other unknown failures keep cleanup blocked.

Delete only the exact remote branch when it still exists and verify absence. A
remote ref already absent after the recorded identity proof is not a failed
gate. For local cleanup, use `git branch -d` first. Exact `-D` is allowed only
after verified Squash Merge, remote presence/absence proof, local tip equal to
reviewed head, tree equality with main, no worktree use, and all other gates
pass.

## Cleanup-only recovery

When lifecycle closeout is complete and only the exact Task branch remains, the
maintainer may request `cleanup-only` with Task, PR, PR base SHA, reviewed head
SHA, merge SHA, and exact branch name.

Re-run the same identity, validation, recheck, and cleanup gates. This path may
delete only the exact Task branch; it may not merge, close an Issue, edit
metadata, commit, push, repair files, change lifecycle state, delete another
branch, or assess Feature completion.

## Stability and report

Run `recheck` after synchronization, metadata convergence, and cleanup. Any
merge, state, main, check, or branch drift blocks success.

On clean success, report Task/PR URLs, PR base/head/reviewed head, merge
method/SHA, Issue/Project/label state, local/origin main, post-merge validation
and checks, exact branch actions, Parent/sub-issue facts without Feature
judgment, limitations, and actions not performed.

When cleanup uses the capability-limited policy, report:

```text
closeout = completed-with-capability-limitation
evidence = stable / partial
required_checks_configuration = unknown
cleanup = completed-under-capability-limited-policy
```

Other terminal states are `completed`, `partial-cleanup-deferred`, `blocked`,
and `invalidated-by-drift`. Use a detailed report for any fallback,
`partial`/`unknown`, failure, drift, conflict, or maintainer decision. Explicitly
state that no merge, manual Issue close, repair commit, unrelated cleanup, or
Feature completion action occurred.
