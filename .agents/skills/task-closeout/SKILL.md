---
name: task-closeout
description: Close out one maintainer-specified GitHub Task after the maintainer has manually merged its Pull Request. Verify Task and PR identity, merge facts, Issue closure, synchronized main, post-merge validation, required checks, final Project and Codex-label state, and safely remove only the exact Task branches. Do not use to merge, manually close an Issue, implement fixes, review Feature completion, or clean unrelated branches.
---

# Task closeout

Use this Skill only after a maintainer states that a specific Pull Request was
manually merged and requests post-merge verification and cleanup for the exact
associated Task.

Do not override system, developer, or current explicit user instructions, or any
applicable `AGENTS.md` or `AGENTS.override.md`. Read current repository and
GitHub facts on every run.

This Skill never merges a Pull Request.

## Standard invocation

Prefer complete Task title, Task number, and Pull Request number:

```text
PR #<PR编号> 已由我人工 Squash Merge。

请使用 task-closeout，完成
[Task] <当前完整标题> #<Task编号>
及 PR #<PR编号> 的合并后核验与分支清理。
```

The user's statement is an entry request, not proof. Verify the actual merge and
all related facts independently.

## Scope boundary

This Skill may:

- verify exact Task and Pull Request identity;
- verify the Pull Request is actually merged;
- verify merge method, head SHA, merge commit, and closing linkage;
- verify the Task Issue closed automatically;
- synchronize local `main` with `origin/main` using fast-forward only;
- run current post-merge CI-equivalent validation;
- read required checks for the merge commit on remote `main`;
- verify or precisely converge the current Task's Project Status to `Done`;
- verify or precisely converge Codex lifecycle labels to final `codex:ready`;
- delete only the exact verified remote and local Task branch after all gates;
- report Parent and sub-issue information only as raw facts.

It does not:

- merge a Pull Request;
- manually close an Issue;
- modify implementation files;
- create a repair commit or Pull Request;
- assess, recommend, or perform Feature completion;
- modify Parent, Priority, Size, Phase, Target, or Relationships;
- delete unrelated branches;
- perform destructive cleanup;
- run a full lifecycle retrospective by default.

Never infer Feature completion from `n / n closed`.

## Resolve rules by responsibility

- Treat system, developer, and current explicit user instructions as the highest
  constraints.
- Apply every governing `AGENTS.md` and `AGENTS.override.md`.
- Use this Skill for closeout gates, precise state convergence, synchronization,
  validation, cleanup, recovery, and safety.
- Use current GitHub Issue and Pull Request facts as authoritative for identity,
  linkage, merge, and Issue state.
- Use current Project definitions for fields and actual Status options.
- Use current workflows, `pyproject.toml`, and lock files for validation.
- Use `.agents/policies/command-execution.md` as the single normative command
  routing policy and the optional ignored local execution profile only as a
  machine-specific routing preference.
- Use `.agents/policies/task-workflow-telemetry.md` only for an explicitly
  active local run. Telemetry never changes closeout gates or branch authority.
- Use the Pull Request head ref and current Git refs to resolve branch identity.
- Use historical records only as evidence, never as a current fact source.

If required sources conflict, a destructive step is uncertain, or rules cannot
all be satisfied, stop before the related write and report the exact conflict.

## Permission model

A valid `task-closeout` invocation authorizes only these exact writes after their
gates pass:

- fast-forward-only synchronization of local `main`;
- precise final lifecycle metadata convergence for the identified Task:
  - Project Status to `Done` when automation did not do so;
  - removal of conflicting `codex:needs-spec` or `codex:blocked`;
  - preservation or addition of `codex:ready`;
- ordinary deletion of the exact verified remote Task branch;
- safe deletion of the exact verified local Task branch, including the narrowly
  gated squash-merge `git branch -D` fallback defined below.

It does not authorize:

- merge;
- manual Issue close;
- code or documentation edits;
- commit, push of code, or repair PR creation;
- unrelated metadata changes;
- Parent or Feature changes;
- force push, `--admin`, protection bypass, reset, clean, or broad branch
  deletion.

## Identify Task and Pull Request safely

Before any write:

1. parse Task number and Pull Request number;
2. read the Task from the current repository;
3. verify it is a Task and read its current canonical Issue title;
4. compare any supplied title with the canonical title;
5. read the Pull Request and verify repository, state, base, head ref, head SHA,
   merge commit, body, and closing linkage;
6. verify the Pull Request corresponds to the requested Task;
7. verify the Task's Parent, Project item, fields, labels, and Relationships.

The Issue number is the primary Task key. The current GitHub Issue title is the
canonical title. Normalize only superficial whitespace, case, punctuation, and
Markdown-escaping differences.

Stop before writes when:

- Task title and number materially disagree;
- Task or PR does not exist;
- the Issue is not a Task;
- Task and PR belong to different repositories;
- PR closing linkage identifies a different Task;
- branch identity cannot be resolved safely.

A derived ProjectV2 `Title` may lag behind the Issue title. Use Issue
`content.title` as authoritative, report a mismatch, and do not attempt a
DraftIssue title update for an Issue-backed Project item.

## Entry gates

Require all of:

- the user explicitly requests closeout;
- the Pull Request is actually `MERGED`;
- the merge commit and merge time are available;
- the Task and Pull Request identity relationship is proven;
- the local repository is the expected repository;
- the local worktree, index, and untracked state are clean before switching or
  synchronizing;
- no unrelated worktree or branch ownership conflict exists.

If the Pull Request is not merged, stop. Do not attempt to merge it.

The approved process expects Squash Merge. If another merge method was used:

- report a process deviation;
- do not rewrite history;
- continue read-only identity, merge, Issue, Project, and `main` verification;
- pause metadata mutation and branch deletion until the maintainer explicitly
  confirms that closeout should continue under the observed merge method.

## Phase 1: Verify merge and Issue closure

Read and record:

- PR state;
- actual merge method when determinable;
- base branch;
- head ref and head SHA;
- merge commit SHA and merge time;
- PR body and `Closes #<Task>` linkage;
- Task Issue state;
- current Project Status;
- current Codex lifecycle labels;
- Parent and sub-issue count as raw facts.

The Task Issue is expected to close automatically through `Closes #<Task>`.

If the PR is merged but the Task remains open:

- stop;
- report the missing automatic closure or incorrect linkage;
- do not manually close the Issue;
- do not conceal the inconsistency with a Project or label write.

Issue `CLOSED` and Project Status `Done` are independent facts. Read both. Never
infer one from the other.

Do not assess or recommend Parent Feature completion.

## Phase 2: Synchronize `main`

Before synchronization:

- verify full clean status including untracked files;
- verify current branch and worktrees;
- fetch current remote refs;
- verify the expected remote and base branch.

Switch to local `main` only when safe.

Synchronize only with a fast-forward operation. Do not merge, rebase, reset, or
clean.

After synchronization require:

```text
local main == origin/main == verified merge commit
```

If local and remote `main` have diverged, the merge commit is not the remote
tip, or safe fast-forward is impossible, stop and report. Do not repair history.

## Phase 3: Verify the merged result

On synchronized `main`:

- inspect the merge commit and changed files;
- verify the merged tree contains the reviewed Pull Request result;
- confirm no unexpected file scope appears in the merged result;
- read current workflows, `pyproject.toml`, and lock files;
- run current CI-equivalent post-merge validation;
- run applicable documentation or Skill validators;
- require `git diff --check`;
- verify final worktree, index, and untracked state remain clean;
- wait for and read required checks for the merge commit on remote `main`.

Do not rely only on commit ancestry after Squash Merge. Verify the merged Pull
Request and compare final trees when needed.

A local validation failure or failed required check stops closeout before
metadata convergence or branch deletion. Do not create a repair commit.

If Project automation already set `Done` before validation, report the actual
state but do not downgrade it automatically when validation fails.

## Phase 4: Verify or converge final Task metadata

After merge, automatic Issue closure, synchronized `main`, post-merge local
validation, and required remote checks all pass, require:

```text
Issue state: CLOSED
Project Status: Done
Codex lifecycle label: codex:ready
```

`codex:ready` is retained after Done as the persistent record that the Task
passed the pre-implementation specification gate. It is not a standalone
implementation authorization.

If Project automation did not set `Done`, this invocation authorizes changing
only the identified Task's Status to the actual `Done` option.

If final Codex labels conflict, this invocation authorizes only:

- remove `codex:needs-spec`;
- remove `codex:blocked`;
- keep or add `codex:ready`.

Re-read and verify every metadata write.

Do not modify:

- Issue open/closed state;
- Parent;
- Priority;
- Size;
- Phase;
- Target;
- Relationships;
- any other Issue or Project item.

If actual Project Status options differ from `Inbox`, `Specifying`, `Ready`,
`In Progress`, `Review`, `Blocked`, and `Done`, report the actual options and
stop state writes.

## Phase 5: Resolve exact branch identity

Resolve the Task branch from the merged Pull Request's exact head ref and current
Git refs. Do not infer a branch from title text alone.

Record:

- exact local Task branch, if present;
- exact remote Task branch or tracking ref, if present;
- head SHA;
- worktree ownership;
- branch-to-`main` tree comparison.

If the PR head originated from a fork or a remote not controlled by this
repository, do not attempt to delete an external branch. Report it instead.

Before any deletion require:

- PR is merged;
- Task is closed;
- Project and Codex labels are in final state;
- current branch is `main`;
- `main == origin/main == verified merge commit`;
- worktree, index, and untracked state are clean;
- exact branch name is certain;
- no other worktree uses the Task branch;
- no unique required content exists;
- `git diff --quiet main <exact-branch>` succeeds for every branch or tracking
  ref that still exists.

If any tree diff is nonzero or uncertain, do not delete.

## Phase 6: Delete the exact remote Task branch

When the repository-owned remote Task branch still exists and all cleanup gates
pass, delete only that exact branch with an ordinary non-force remote deletion.

Do not:

- use patterns or wildcard refs;
- delete multiple branches;
- force push;
- delete `main`;
- delete a branch not proven to be the merged Task branch.

Re-fetch and verify the remote branch no longer exists.

If GitHub or a maintainer already deleted it, treat that step as complete.

## Phase 7: Delete the exact local Task branch

When the exact local Task branch exists and all cleanup gates pass:

1. attempt:

   ```text
   git branch -d <exact-task-branch>
   ```

2. if it succeeds, verify the ref is gone;

3. if it fails, determine the exact reason;

4. only when the refusal is solely that Squash Merge did not place the original
   Task commit in `main` ancestry, re-run every cleanup gate and confirm:
   - PR is merged;
   - current branch is `main`;
   - `main == origin/main`;
   - worktree, index, and untracked state are clean;
   - branch is unused by any worktree;
   - remote branch is deleted or absent;
   - `git diff --quiet main <exact-task-branch>` succeeds;
   - branch identity remains exact;

5. then this invocation authorizes only:

   ```text
   git branch -D <exact-task-branch>
   ```

6. verify the local ref is gone and final repository state is clean.

Never extend this fallback to:

- a nonzero or uncertain tree diff;
- an uncertain branch name;
- multiple branches;
- a branch used by another worktree;
- an unclean workspace;
- an unmerged or differently linked Pull Request;
- `main` or any unrelated branch.

## Recovery and idempotency

Derive closeout progress from current facts.

Support safe re-entry when:

- PR is already merged;
- Task is already closed;
- Project is already `Done`;
- final Codex labels are already correct;
- local `main` is already synchronized;
- local or remote validation was already partly completed;
- remote Task branch is already absent;
- local Task branch is already absent.

Completed actions are verified rather than repeated.

If an earlier run stopped after a safe partial action, resume from the first
unverified gate. Never recreate a deleted branch or repeat a metadata mutation
that is already correct.

## Mandatory pause conditions

Pause before the related write when:

- Task title and number materially disagree;
- Task, PR, closing linkage, repository, or branch identity is uncertain;
- PR is not merged;
- Task did not close automatically;
- non-Squash merge lacks maintainer confirmation to continue;
- local workspace is not clean;
- worktrees conflict;
- `main` cannot fast-forward or does not equal `origin/main`;
- merge commit or merged tree cannot be verified;
- local post-merge validation fails;
- required checks fail or remain unresolved;
- Project options differ from expected values;
- tree comparison is nonzero or uncertain;
- branch is used by another worktree;
- deletion would affect an unrelated or external branch;
- the process would require merge, manual Issue close, repair commit, force push,
  `--admin`, bypass, reset, clean, or broad deletion.

Report completed steps, exact evidence, current safe state, and the next
maintainer decision or recovery gate.


## Optional workflow telemetry

Read `.agents/policies/task-workflow-telemetry.md` and perform one lightweight
local status check for this Task. With no explicit active run, do no further
telemetry work.

When active, append one `task-closeout` summary using only facts and counts
already produced by merge verification, synchronization, validation, metadata
convergence, and exact branch cleanup. Record retries and the squash-specific
exact `-D` fallback when they occur. Do not add GitHub queries, Git commands, or
validation only for measurement.

When the summary identity includes `workflow_main_sha`, copy the immutable value
from the active Telemetry run manifest. Do not derive it from current `main`,
`origin/main`, the PR base or head SHA, or the Squash merge commit. Maintainer
manual Merge remains a closeout prerequisite but does not require a separate
Telemetry event.

Telemetry cannot authorize merge, Issue close, metadata writes, main updates, or
branch deletion and cannot weaken any exact branch gate. If the ignored local
append fails, report `telemetry incomplete`; closeout behavior remains governed
only by this Skill.

## Command execution routing

Before executing a command, read `.agents/policies/command-execution.md` and
check the optional ignored `.agents/execution-profile.local.toml`.

Apply every closeout identity, merge, synchronization, validation, metadata, and
branch-cleanup gate before route selection. A local profile may choose only the
execution context of an exact command already authorized by this Skill. It
cannot authorize merge, manual Issue close, repair work, metadata beyond the
precise final convergence allowed here, or branch deletion before all exact
branch safety gates pass.

Preserve executable, argv, working directory, repository, Task/PR identity,
closeout phase, and intent across retries. The profile cannot weaken tree-diff,
worktree, remote-branch, `-d`, or squash-specific exact `-D` gates. This Skill
never executes `gh auth login`.

Report routing events required by the shared policy. Never elevate a forbidden
operation.

## Closeout report

Include:

- Task number and canonical title;
- Pull Request number, title, base, head, head SHA, merge method, and merge
  commit;
- Issue state;
- Project Status;
- Codex lifecycle labels;
- Parent and sub-issue facts without Feature completion judgment;
- local and remote `main` state;
- post-merge validation commands, exit codes, and results;
- material execution-routing decisions and elevated attempts required by the
  shared command policy;
- telemetry run ID and completion state only when an explicit run is active;
- required checks on remote `main`;
- exact remote and local branch actions;
- whether `-D` was required and every gate that justified it;
- final refs and clean status;
- process deviations, limitations, and actions deliberately not performed.

## Final checklist

- Task number and canonical title match.
- Pull Request corresponds to the Task and is actually merged.
- Merge method and merge commit were verified.
- The Task closed automatically through correct linkage.
- Issue state and Project Status were read independently.
- Parent and sub-issue facts were not converted into Feature completion advice.
- Local `main` fast-forwarded safely and equals `origin/main`.
- Merged tree and expected file scope were verified.
- Current CI-equivalent post-merge validation passed.
- Required checks for remote `main` passed.
- Final Project Status is `Done`.
- Final Codex lifecycle label is `codex:ready`.
- Only the exact Task branches were deleted.
- Any local `-D` use satisfied every squash-specific safety gate.
- Final worktree, index, untracked state, refs, and `main` are clean and correct.
- Command routes followed the shared policy and no local profile weakened any
  closeout or exact-branch safety gate.
- No merge, manual Issue close, repair commit, force push, `--admin`, reset,
  clean, bypass, Feature completion action, or unrelated change occurred.
