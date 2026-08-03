---
name: task-closeout
description: Close out one maintainer-specified Task after maintainer manual merge. Use the fixed read-only Evidence Runner and fixed closeout Validation profile, synchronize main, converge exact final metadata, and delete only verified Task branches. Never merge, manually close an Issue, repair code, clean unrelated branches, or assess Feature completion.
---

# Task closeout

Use this Skill only after the maintainer states that a specific PR was manually
merged and requests post-merge verification and cleanup for its exact Task. The
statement is an entry request, not proof. This Skill never merges a PR.

## Standard invocation

```text
PR #<PR编号> 已由我人工 Squash Merge。

请使用 task-closeout，完成
[Task] <当前完整标题> #<Task编号>
及 PR #<PR编号> 的合并后核验与分支清理。
```

Task and PR numbers are primary keys; current Issue title is canonical.

## Rules and fixed runners

Read applicable agent rules and:

```text
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
```

Normal Closeout uses only these fixed read/validation front doors:

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

The Evidence Runner is read-only. It does not synchronize main, mutate metadata,
or delete branches. The `workflow-closeout` profile requires clean synchronized
`main`, runs current post-merge CI-equivalent checks and required Skill
validators, and produces a compact digest.

Do not also run the raw Evidence/Validation tools, direct full validation chain,
or complete legacy GitHub/Git read sequence. Expand only a named
partial/unknown/fail/truncation/conflict with bounded read-only inspection.

When GitHub returns a recognized Required Checks plan-limit `403`, preserve
`required_checks_configuration = unknown` and the Evidence status as `partial`.
The runner may additionally report a separate
`cleanup_eligibility.status = eligible-under-capability-limited-policy`. That
derived field is only a branch-cleanup input; it is not a complete Evidence pass
and never authorizes Merge, push, Issue, Project, label, review, or other writes.

## Permission boundary

After all exact gates pass, this invocation may fetch current refs, verify
Task/PR/merge/linkage/state/check/branch facts, fast-forward local `main` to
`origin/main` only, run fixed post-merge validation, converge only this Task's
Project Status to `Done` and lifecycle label to `codex:ready`, and delete only
the exact verified remote/local Task branch.

It never authorizes merge, manual Issue close, repair, commit/push, hierarchy or
Relationship changes, unrelated branch/worktree cleanup, Feature completion,
force push, `--admin`, protection bypass, `git reset --hard`, or `git clean`.

## Entry and identity gates

Generate one current fixed `closeout-readonly` snapshot. Independently verify
repository/Task/PR/title, exact closing linkage, actual `MERGED` state, expected
head and merge SHA, merge method, automatic Issue closure, Project/label facts,
workspace/worktrees, exact branches, and checks.

If the Task did not auto-close through the intended PR, stop. Do not manually
close it. The plan records branch/head/merge/main/check facts and remains
read-only; it is not deletion permission.

## Phase 1: synchronize merged result

Start clean. Fetch current refs, switch to local `main`, and fast-forward only.
Stop on divergence, changes, worktree conflict, reset/force/bypass requirement.
Verify local `main == origin/main`, merge reachability, merged tree/scope, and no
tracked/staged local execution artifacts. For Squash Merge, use merge facts,
linkage, reviewed head, changed-file scope, and tree comparison rather than
inventing ancestry.

## Phase 2: post-merge validation and checks

Run only `workflow-closeout` after main synchronization. Do not reuse Delivery
or Review results. Read remote-main check runs and Required-Checks
classification. Preserve none-configured, plan-limit `403`, pending, failed,
cancelled, skipped, stale, and unavailable states. A real failure or unresolved
required gate stops metadata convergence and cleanup.

## Phase 3: final Task metadata

Expected final state:

```text
Issue: CLOSED by correct PR linkage
Project Status: Done
Codex label: codex:ready
codex:blocked: absent
```

Verify existing automation first. Apply only missing exact final Project/lifecycle
convergence and re-read it. Do not change any other field or infer Feature
completion.

## Phase 4: exact branch cleanup

Resolve the exact branch from verified PR facts. Before deletion verify branch
ownership, expected head, no worktree use, merged result, Issue closure,
synchronized main, validation/checks, final metadata, and no unrelated/default/
protected/ambiguous target. Treat PR-declared closing linkage, the Issue's
latest effective closure cause, and the PR merge identity as separate facts:
unknown or partial closure evidence blocks cleanup, conflicts block cleanup, and
only complete Issue-side proof that the locked merged PR closed the Task may
satisfy the closure portion of cleanup eligibility.

If Required Checks configuration is unavailable only because of a recognized
GitHub plan-limit `403`, branch cleanup may continue only when the snapshot's
independent `cleanup_eligibility.status` is exactly
`eligible-under-capability-limited-policy`, the preserved Required Checks gate is
still `unknown`, actual observed check runs include at least one quality gate and
are all successful terminal states, the final recheck is stable, local
`main == origin/main == merge SHA`, the exact remote and local branch tips equal
the reviewed PR head, the PR head tree equals the merge tree, the target branch
is not used by any worktree, and the cleanup plan contains no other branch.
Any authentication, scope, permission, rate-limit, network, schema, service, or
unknown Required Checks failure keeps cleanup blocked. Missing Issue-side PR
state, `merged`, or `mergedAt` metadata must be reported as unknown/partial
closure evidence, not as an explicit not-linked relationship.

Delete only the exact remote branch and verify absence. For local cleanup, try
`git branch -d` first. Exact `-D` is allowed only after verified Squash Merge,
remote absence, local tip equals reviewed head, tree equality with main, no
worktree use, and all other gates pass. Never use wildcard or broad cleanup.

## Cleanup-only recovery

For a Task whose lifecycle is already complete and whose only deferred action is
exact task-branch cleanup, the maintainer may request a bounded `cleanup-only`
resume by providing Task, PR, PR base SHA, reviewed head SHA, merge SHA, and exact
branch name. This path may read current Task/PR/Project/label/check/thread/SHA/ref
facts, run `workflow-closeout`, run final `recheck`, compute the same
capability-limited cleanup eligibility, delete only the exact remote branch, and
delete only the exact local branch.

`cleanup-only` must not merge, close an Issue, edit Project Status, edit labels,
create commits, push code, repair business files, reset a Task to `In Progress`
or `Review`, delete any other branch, perform Feature completion, or rewrite a
partial Evidence result to pass. Identity drift or missing proof stops at a
checkpoint.

## Final stability recheck

Run the fixed `recheck` against the stored closeout plan after synchronization,
metadata, and cleanup. Any merge/state/main/check/branch drift stops success.

## Failure expansion, pauses, and recovery

- `partial` / `unknown`: inspect only named gates and preserve uncertainty.
- `fail` / drift: stop before the dependent write or deletion.
- Runner unavailable or schema/version mismatch: report incompatibility; do not
  reactivate the entire legacy path.

Pause when identity, linkage, merge, auto-close, method, workspace, main/tree,
validation, checks, Project/label, branch ownership, or deletion facts conflict,
or when repair, manual close, merge, force, bypass, destructive cleanup, or a
maintainer decision is required.

Resume from current facts and first unverified gate. Verify completed actions
rather than repeating them; never recreate a deleted branch.

## Compact closeout report

On clean success include Task/PR URLs and identity, PR base/head/reviewed head,
merge method/SHA, Issue/Project/label state, local/origin main, fixed post-merge
validation and checks, exact branch actions and `-D` use, Parent/sub-issue facts
without Feature judgment, limitations, and actions not performed.

When cleanup succeeds under the capability-limited policy, report
`closeout = completed-with-capability-limitation`, `evidence = stable / partial`,
`required_checks_configuration = unknown`, and
`cleanup = completed-under-capability-limited-policy`. Do not call it an
unqualified clean success. Other terminal closeout states include `completed`,
`partial-cleanup-deferred`, `blocked`, and `invalidated-by-drift`.

Use a detailed report for any fallback, partial/unknown, failure, drift, conflict,
or maintainer decision. Explicitly state no merge, manual Issue close, repair
commit, unrelated cleanup, or Feature completion action occurred.
