---
name: task-lifecycle
description: Manage this repository's lifecycle for an already-created GitHub Task, including reading the Task, pre-implementation review, readiness, implementation, Pull Request work, final review, merge, post-merge verification, cleanup, and retrospective. Do not use for creating, planning, drafting from scratch, choosing, or splitting new Tasks, ordinary code explanation, research analysis, or work that is not tied to an existing GitHub Task.
---

# Task lifecycle

Use this Skill only to supplement the GitHub Task lifecycle in this repository.
Do not override system, developer, or user instructions, or any applicable
`AGENTS.md` or `AGENTS.override.md`. Do not treat this Skill as a replacement for
the current Issue, `.github/ISSUE_TEMPLATE/task.yml`,
`.github/pull_request_template.md`, workflows, `pyproject.toml`, or lock files.

This Skill starts only after the user specifies an existing GitHub Task. It does
not identify the next Task, split Features or Epics into new Tasks, decide that a
new Task should exist, draft a new Task from scratch, create GitHub Issues, or
choose initial Parent, Project fields, labels, or Relationships for new Tasks.

Read the current repository and GitHub sources on every run. Never rely on a
template, command, field value, or workflow cached when this Skill was created.
Execute only the lifecycle stage the user explicitly requests. A successful
stage does not authorize the next stage.

## Resolve rules by responsibility

- Treat system, developer, and current explicit user instructions as the highest
  constraints.
- Apply every `AGENTS.md` and `AGENTS.override.md` governing the files in scope.
- Use this Skill for lifecycle stages, gates, permission boundaries, and safety.
- Use the current Issue body and fields for the approved Task goal, scope,
  acceptance criteria, Parent, dependencies, labels, and Project state.
- Use `.github/ISSUE_TEMPLATE/task.yml` for Task body structure.
- Use `.github/pull_request_template.md` for Pull Request body structure.
- Use current workflows, `pyproject.toml`, and lock files for validation commands,
  tools, versions, and environment constraints.
- Use historical Tasks and Pull Requests only as process evidence, never as a
  permanent template or current fact source.

If sources conflict, are missing, or cannot all be satisfied, stop the current
write stage, identify the exact conflict, and wait for a maintainer decision. Do
not choose a source on the maintainer's behalf, overwrite a template, or weaken
acceptance criteria.

## Identify the requested stage

Classify the request as exactly one or more explicitly requested stages:

1. Existing Task intake, specification review, or authorized revision.
2. Pre-implementation read-only audit.
3. Authorized lifecycle metadata transition.
4. Branch and implementation.
5. Commit, push, and Pull Request creation.
6. Final read-only Pull Request review.
7. Merge and post-merge verification.
8. Branch cleanup.
9. Lifecycle retrospective.

Do not move automatically from audit to repair, review to merge, merge to branch
deletion, or state verification to GitHub field edits.

## Apply the permission boundary

Treat pre-implementation audits, final Pull Request reviews, pre-merge checks,
state verification, and retrospectives as read-only unless the user separately
authorizes a write stage. In a read-only stage, do not modify files, Issues,
Pull Requests, Projects, or Relationships; commit; push; submit a review; merge;
close; or automatically fix a finding.

Perform a write only when the user explicitly requests its corresponding stage
and all gates for that stage pass. Authorization for one write stage does not
authorize later stages.

## Stage 1: Intake an existing Task and review or revise its specification

- **Entry:** The user specifies an existing GitHub Task and explicitly requests
  specification intake, review, revision advice, or an authorized revision.
- **Default permission:** Read-only unless the user explicitly authorizes
  changing the specified existing Task.
- **Allowed:** Read the current Task template to check the existing Task
  structure; validate its fields, labels, Parent, dependencies, Relationships,
  and Project values; propose precise revisions; update only the specified
  existing Task when authorized.
- **Forbidden:** Identify, split, plan, draft from scratch, or create a new Task;
  implement the Task; create a branch or Pull Request; invent a replacement Issue
  structure; mark an unaudited Task Ready; or change Project, Parent, labels, or
  Relationships unless explicitly authorized.
- **Read:** Governing agent files, `.github/ISSUE_TEMPLATE/task.yml`, the parent
  Issue, linked sources, dependency Issues, Project field definitions, and label
  definitions.
- **Check:** Confirm the specified Task has one primary goal that one independent
  Pull Request can complete, preserves required template sections, has explicit
  Parent, Project fields, labels, dependencies, and Relationships, and does not
  require the implementer to guess key architecture decisions. Recommend splitting
  size L. Use `type:task`; do not add a mutually exclusive `type:docs` merely
  because the work is documentation-only.
- **Exit:** The existing Task specification is complete and auditable, or exact
  revision needs are reported. It remains `codex:needs-spec` until Stage 2 passes
  and an authorized Stage 3 Ready transition is requested.
- **Report:** Existing Task identity, sources, current fields, validation
  findings, proposed revisions, unresolved decisions, and the next gate.

Reading `.github/ISSUE_TEMPLATE/task.yml` in this Skill means checking the
structure and required sections of an existing Task. It does not authorize
planning or creating a new Task.

### Parent, dependencies, and Relationships

Keep these concepts distinct:

- Parent/Sub-issue expresses hierarchy.
- The Issue body may list the complete logical dependency and evidence chain.
- GitHub `Blocked by` or blocking Relationships express formal blocking state.

Normally keep only the nearest direct unresolved blocker as a formal
Relationship. Do not create a formal blocker merely because a completed Task is
evidence or appears in the body. Never infer Relationships from chat text.
Use `codex:blocked` only for a real, unresolved blocker, not incomplete wording or
a precautionary pause.

## Stage 2: Perform the pre-implementation read-only audit

- **Entry:** The user requests readiness review before implementation.
- **Default permission:** Read-only.
- **Allowed:** Inspect Git, repository files, the complete Task and comments,
  Parent, dependencies, formal Relationships, labels, and Project fields.
- **Forbidden:** Modify files or GitHub state, repair the specification, create a
  branch, commit, push, or implement.
- **Read:** All governing agent files; current Issue and comments; Parent and
  blockers; linked docs and ADRs; current templates, workflows,
  `pyproject.toml`, and lock files.
- **Check:** Confirm repository identity, root, branch, full untracked status,
  clean scope, synchronized `main` and `origin/main`, approved goal and
  acceptance criteria, correct Parent and Project fields, consistent labels, and
  no unresolved formal or factual blocker.
- **Exit:** Report pass or fail. A pass permits a separately requested Ready
  transition. Implementation may begin only after the Task actually has
  `codex:ready`, is unblocked, and Stage 4 is separately requested. The audit
  performs neither transition nor implementation.
- **Report:** Findings by severity, exact evidence, readiness conclusion, and
  unresolved gates.

## Stage 3: Apply an authorized lifecycle metadata transition

- **Entry:** The user explicitly requests a specific Codex-label or Project
  Status transition, and current repository and GitHub facts support that
  transition. Transitioning to `Ready` additionally requires Stage 2 to have
  passed.
- **Default permission:** Limited GitHub metadata write for the exact authorized
  transition only.
- **Allowed:** Apply only the approved lifecycle transition, replace conflicting
  lifecycle labels when required, and re-read the result.
- **Forbidden:** Change unrelated fields; infer or broaden the requested
  transition; implement; create a branch; use `codex:blocked` without a real
  unresolved blocker; or set `Done` without verified merge and post-merge
  completion.
- **Read:** Current Issue and comments, relevant audit or lifecycle evidence,
  label definitions, current labels, Project field definitions, available
  Project Status options, and the current Project item values.
- **Check:** Confirm the requested transition against current facts:
  - For `Ready`, require a passed Stage 2 audit and complete specification.
  - For `In Progress`, require implementation to be starting or active.
  - For `Review`, require a Pull Request to exist or final review to be starting.
  - For `Blocked`, require a real unresolved blocker.
  - When leaving `Blocked`, restore the state and Codex label supported by the
    actual lifecycle stage rather than mechanically restoring `Ready`.
  - For `Done`, first verify merge and post-merge completion and verify whether
    repository automation already applied the transition. Perform a manual
    transition only when the user explicitly authorizes it.
  Replace conflicting lifecycle labels rather than accumulating them.
- **Exit:** GitHub reports the exact requested label and Project Status
  consistently, or the mismatch is reported without unrelated changes.
- **Report:** Previous and resulting values, evidence supporting the transition,
  mutation performed, verification, and the next gate.

### Codex labels and Project Status

Keep Codex lifecycle labels separate from the Project `Status` field:

- `codex:*` labels express Codex specification gates, implementation permission,
  or blocker state.
- Project `Status` expresses the Task's current project workflow phase.

For an existing Task, first read its current Project `Status` field and available
options. While the repository Project Status options are exactly `Inbox`,
`Specifying`, `Ready`, `In Progress`, `Review`, `Blocked`, and `Done`, use this
mapping:

| Lifecycle state | Codex label | Project Status |
| --- | --- | --- |
| Task recorded, specification not started | `codex:needs-spec` | `Inbox` |
| Specification is being completed | `codex:needs-spec` | `Specifying` |
| Pre-implementation review passed | `codex:ready` | `Ready` |
| Implementation branch or work is active | `codex:ready` | `In Progress` |
| Pull Request exists or final review is active | `codex:ready` | `Review` |
| A real unresolved blocker exists | `codex:blocked` | `Blocked` |
| PR is merged and post-merge verification passed | Follow current label policy | `Done` |

When pre-implementation review passes, recommend exactly:

```text
Remove codex:needs-spec
Add codex:ready
Project Status: Specifying -> Ready
```

If the existing Task is still in `Inbox` when review passes, recommend:

```text
Remove codex:needs-spec
Add codex:ready
Project Status: Inbox -> Ready
```

When implementation starts, recommend:

```text
Project Status: Ready -> In Progress
```

When a Pull Request is created or final review starts, recommend:

```text
Project Status: In Progress -> Review
```

When a real unresolved blocker appears, recommend:

```text
Remove codex:ready or codex:needs-spec
Add codex:blocked
Project Status: current status -> Blocked
```

After a blocker is resolved, do not mechanically restore `Ready`. Restore
`Specifying`, `Ready`, `In Progress`, or `Review` according to the lifecycle stage
and current facts, and restore the corresponding Codex label.

After merge and post-merge verification, explicitly check:

```text
Project Status: Review -> Done
```

Issue `OPEN` or `CLOSED` state and Project `Status` are independent facts.
`Closes #<Task>` may close the Issue automatically, while Project Status may or
may not update automatically. Read and report both facts separately after merge.

Changing Codex labels or Project Status is a write operation. Read-only stages
may only report the exact recommended transition. Do not change labels, Project
fields, Parent, or Relationships without explicit authorization.

If the actual Project Status options differ from the seven options above, report
the actual options, identify the mismatch, stop related state writes, and wait
for maintainer direction. Do not guess, create, or rename Project Status options.

This mapping applies only to existing Tasks. Do not use it to choose the next
Task, split a Feature, create a GitHub Issue, choose initial fields for a new
Task, or replace planning outside this Skill.

## Stage 4: Create a branch and implement

- **Entry:** The Task is open, has `codex:ready`, is unblocked and sufficiently
  specified, the user requests implementation, and the base is current.
- **Default permission:** Repository writes within Task scope.
- **Allowed:** Create or use the Task branch, make the smallest scoped changes,
  and run current repository validation.
- **Forbidden:** Unrelated refactors; scope expansion; destructive cleanup;
  GitHub metadata writes except a separately and explicitly requested Stage 3
  transition performed as a distinct substep; commits, pushes, or Pull Requests
  unless separately requested.
- **Read:** Complete Issue and comments, Parent and dependencies, linked docs and
  ADRs, governing agent files, current implementation docs, workflows,
  `pyproject.toml`, and lock files.
- **Check:** Before writing, run repository-root, branch, status including all
  untracked files, recent-log, fetch, and main synchronization checks. If
  unrelated changes exist, stop without cleaning. If already on the expected
  branch, verify it is based on current main. If on another feature branch, stop
  rather than switch, merge, rebase, or clean automatically.
- **Exit:** Only in-scope files changed, acceptance criteria addressed, relevant
  validation passed or limitations recorded, and status explicitly inspected.
- **Report:** Files changed, commands and results, acceptance coverage,
  limitations, out-of-scope work, and the next gate.

### Git and validation safety

- Never use `git add .`, `git clean`, `git reset --hard`, force push, or
  `--admin` to bypass protection.
- Keep commit and Pull Request file scope identical to the Task scope.
- Remember that ordinary diffs omit untracked files; inspect them explicitly.
- Before staging, inspect status and every tracked and untracked change. Stage
  explicit paths only. After staging, inspect `git diff --cached` and status.
- After committing, inspect `git show --stat` and
  `git diff HEAD^ HEAD --check`, plus the committed file list.
- Always require `git diff --check`.
- Read current workflows, `pyproject.toml`, and lock files before choosing
  validation commands. Run CI-equivalent commands; documentation-only Tasks are
  not exempt from required checks.
- Do not claim CI verification from non-equivalent commands. Interpret exit codes
  by command semantics; for example, `rg` exit code 1 can mean no matches.

## Stage 5: Commit, push, and create a Pull Request

- **Entry:** The implementation is complete, scoped, validated, and the user
  explicitly requests the applicable commit, push, or Pull Request action.
- **Default permission:** Only the specifically requested Git/GitHub writes.
- **Allowed:** Stage explicit in-scope paths, verify the staged diff, commit, push
  the Task branch, and create one Pull Request when each action is requested.
- **Forbidden:** Bulk staging, unrelated files, force push, duplicate Pull
  Requests, Issue/Project edits except a separately and explicitly requested
  Stage 3 transition performed as a distinct substep, merge, or claims about
  post-merge state.
- **Read:** Current Issue, current `.github/pull_request_template.md`, Git status
  and diffs, current workflows and check requirements, and existing Pull Requests
  for the branch.
- **Check:** Use the repository's current PR template without replacing it with an
  invented `Summary / Validation / Scope` structure. Include `Closes #<Task>`.
  Do not pre-check post-merge items such as PR merged, Issue closed, or main
  synchronized. After creation, re-read the title, body, changed files, and
  Checks; ensure there is no duplicate Pull Request.
- **Exit:** Requested Git operations are verified and the Pull Request, if
  requested, accurately represents only the Task.
- **Report:** Commit and branch state, Pull Request URL and scope, local validation,
  current Checks, unperformed actions, and the next gate.

## Stage 6: Perform final read-only Pull Request review

- **Entry:** A Pull Request exists and the user requests an independent final
  review.
- **Default permission:** Strictly read-only and independent.
- **Allowed:** Read the Issue, diff, commits, discussions, reviews, and Checks;
  reproduce validation without writing.
- **Forbidden:** Modify files or GitHub state, submit a review, fix findings,
  push, merge, or close.
- **Read:** Current Issue and comments, governing rules, PR template and body,
  complete diff and file list, workflows, CI results, review threads, and linked
  sources.
- **Check:** Scope, correctness, acceptance criteria, safety, test adequacy,
  documentation, required checks, unresolved requested changes, and merge gates.
  Classify findings as Blocking, High, Medium, Low, or Nit.
- **Exit:** Conclude exactly `通过，可以合并`, `有条件通过`, or
  `不通过，需要修复`. Do not pass for merge with unresolved Blocking, High, or
  Medium findings, requested changes, or failed required checks.
- **Report:** Findings ordered by severity with evidence, check status, residual
  risks, and one permitted conclusion.

Keep these gates separate:

```text
local validation passed
!= CI passed
!= independent Pull Request review passed
!= merge is authorized
```

## Stage 7: Merge and verify post-merge state

- **Entry:** The user explicitly requests merge; independent review passed;
  required checks succeeded; no blocking findings or requested changes remain.
- **Default permission:** Merge write followed by read-only verification. Any
  Codex-label or Project Status write requires a separately and explicitly
  requested Stage 3 transition performed as a distinct substep.
- **Allowed:** Perform the repository-approved merge, then synchronize and verify
  state as explicitly requested. Apply a separately authorized Stage 3
  transition only after the required lifecycle evidence exists.
- **Forbidden:** `--admin`, bypassing protection or CI, merging with failed gates,
  Codex-label or Project Status writes without a separately authorized Stage 3
  substep, manually closing a Task expected to close through
  `Closes #<Task>`, closing the parent Feature from sub-issue count alone, or
  deleting branches.
- **Read:** PR mergeability, reviews, required checks, Issue linkage, current
  merge policy, Parent, Project fields, local Git state, and key files.
- **Check:** Default to squash merge. After merge, switch to local main and use
  fast-forward-only synchronization. Verify PR is MERGED, Task is CLOSED, Parent
  remains correct, Project Status is correct, local main equals origin/main, the
  worktree is clean, and key files contain the merged result. Treat Issue CLOSED
  and Project Status as independent states.
- **Exit:** Merge and every requested post-merge check are confirmed, or each
  discrepancy is reported without automatic repair.
- **Report:** Merge method and result, PR/Issue/Project/Parent state, main commit,
  worktree and key-file verification, discrepancies, and the cleanup gate.

After squash merge, do not rely only on commit ancestry to decide whether branch
content reached main. Compare the final tree or verify the merged Pull Request.
An `n / n closed` sub-issue display means only that all currently created child
Tasks are closed; recommend closing the parent Feature only when the Feature's own
completion criteria are satisfied.

For an Issue-backed ProjectV2 item, treat Issue `content.title` as the real title.
A derived ProjectV2 Title may lag. Record and re-check a mismatch; do not use a
DraftIssue update method or automatically rewrite the Issue.

## Stage 8: Clean up branches

- **Entry:** The user separately requests cleanup and Stage 7 verification has
  established that the Pull Request is merged and content is on main.
- **Default permission:** Limited branch deletion requested by the user.
- **Allowed:** Delete only the exact verified local or remote Task branch.
- **Forbidden:** Automatic cleanup after merge, deletion before verification,
  broad deletion, destructive worktree cleanup, or deleting any branch with
  unique unmerged work.
- **Read:** PR merge status, final file tree, branch refs, worktree status, and
  main/origin synchronization.
- **Check:** Resolve exact branch names, confirm no unique required content and no
  active worktree dependency, then verify deletion results.
- **Exit:** Only the authorized branch is removed and main remains synchronized
  with a clean worktree.
- **Report:** Evidence used, exact branches removed or retained, final refs and
  status, and recoverability limitations.

## Stage 9: Review the lifecycle

- **Entry:** The user requests a retrospective or a real Task trial supplies new
  evidence.
- **Default permission:** Read-only.
- **Allowed:** Compare observed behavior with this Skill and current repository
  rules; recommend a separate revision Task.
- **Forbidden:** Modify this Skill opportunistically during another business Task,
  change GitHub state, or claim universal coverage from limited examples.
- **Read:** The real Task and PR record, current rules/templates/workflows, command
  results, and this Skill.
- **Check:** Record whether triggering and stage detection were correct, whether a
  long prompt was still needed, conflicts with AGENTS/templates/CI, and whether
  elevated fallback behaved correctly.
- **Exit:** Findings and a bounded v1.x revision proposal are documented; the
  current business Task follows its already approved specification.
- **Report:** Evidence, successful behavior, gaps, risks, and proposed independent
  follow-up Task.

## Handle sandbox and elevated fallback

When a normal command fails:

1. Determine whether the failure resembles sandbox permission, credential
   isolation, or login-session isolation.
2. When the user allows it and the environment supports it, retry the same command
   with elevated permission.
3. Only after the elevated retry fails, diagnose a real credential, environment,
   or code problem.

If sandboxed `gh` returns 401, do not immediately run `gh auth login`. First run
`gh auth status` and the original read-only query elevated. Ask a maintainer to
reauthenticate only if elevated execution also confirms an invalid token. Apply
the same reasoning to access-denied or login-session errors from `uv` or
`python`. Never use elevation for `--admin`, bypasses, skipped review, or skipped
CI. Report the normal failure and elevated retry as separate results.

## Use a consistent stage report

Keep reports concise and include:

- Current stage.
- Fact sources read.
- Entry gates and their results.
- Actions performed.
- Validation results, including command and exit code when relevant.
- Git and GitHub state.
- Actions deliberately not performed.
- The next gate, without executing it.

## Final checklist

- Confirm the user authorized only the executed stage or stages.
- Confirm current governing files, Issue data, templates, and CI configuration
  were read rather than recalled.
- Confirm read-only stages made no writes and did not auto-fix findings.
- Confirm Task scope, Parent, dependencies, Relationships, labels, and Project
  fields are explicit and consistent.
- Confirm tracked and untracked files, staged scope, and committed scope were
  inspected at their applicable gates.
- Confirm current CI-equivalent validation and `git diff --check` results are
  accurately reported.
- Confirm PR template use, `Closes #<Task>`, independent review, required checks,
  and merge authorization remain separate gates.
- Confirm post-merge Issue, Project, Parent, main, worktree, and key-file state was
  verified before any cleanup.
- Confirm no credentials, protection bypass, destructive cleanup, automatic live
  trading, or unrelated change was introduced.

## Version 1 limitations

This v1 is based on the repository and completed lifecycle evidence available at
creation time; it is not proven for every future Task. Use configuration
management, structured logging, and initial domain-model Tasks as real trials.
For each trial, record trigger accuracy, stage accuracy, need for extra prompting,
rule or CI conflicts, and elevated fallback behavior. Complete the approved
business Task under its current rules, then create a separate Task for any Skill
revision; do not fold a v1.1 change into unrelated work.
