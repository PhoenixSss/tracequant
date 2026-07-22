---
name: task-lifecycle
description: Manage this repository's GitHub Task lifecycle when creating or revising a Task specification, performing a read-only readiness audit, changing Ready state, implementing on a branch, committing or pushing, creating or reviewing a Pull Request, squash-merging, verifying post-merge state, cleaning up branches, or reviewing the lifecycle process. Do not use for ordinary code explanation, research analysis, or work that is not tied to a GitHub Task.
---

# Task lifecycle

Use this Skill only to supplement the GitHub Task lifecycle in this repository.
Do not override system, developer, or user instructions, or any applicable
`AGENTS.md` or `AGENTS.override.md`. Do not treat this Skill as a replacement for
the current Issue, `.github/ISSUE_TEMPLATE/task.yml`,
`.github/pull_request_template.md`, workflows, `pyproject.toml`, or lock files.

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

1. Task specification creation or revision.
2. Pre-implementation read-only audit.
3. Ready-state transition.
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

## Stage 1: Create or revise a Task specification

- **Entry:** The user explicitly requests Task creation or specification changes.
- **Default permission:** Write only the requested GitHub Task metadata and body;
  otherwise remain read-only.
- **Allowed:** Read the current Task template; draft or update one Task; validate
  its fields, labels, Parent, dependencies, and Project values.
- **Forbidden:** Implement the Task, create a branch or Pull Request, invent a
  replacement Issue structure, or mark an unaudited Task Ready.
- **Read:** Governing agent files, `.github/ISSUE_TEMPLATE/task.yml`, the parent
  Issue, linked sources, dependency Issues, Project field definitions, and label
  definitions.
- **Check:** Preserve every required template section. Give the Task one primary
  goal that one independent Pull Request can complete. Recommend splitting size
  L. Explicitly verify Parent, Project fields, and labels. Start a new Task with
  `codex:needs-spec`. Use `type:task`; do not add a mutually exclusive
  `type:docs` merely because the work is documentation-only.
- **Exit:** The specification is complete and auditable, but remains
  `codex:needs-spec` until Stage 2 passes and Stage 3 is requested.
- **Report:** Task identity, sources, proposed fields, validation findings,
  unresolved decisions, and the next gate.

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
  transition or implementation stage; it performs neither.
- **Report:** Findings by severity, exact evidence, readiness conclusion, and
  unresolved gates.

## Stage 3: Change Ready state

- **Entry:** Stage 2 passed and the user explicitly requests a label or Project
  state change.
- **Default permission:** Limited GitHub metadata write.
- **Allowed:** Apply only the approved state transition and re-read the result.
- **Forbidden:** Change unrelated fields, implement, create a branch, or use
  `codex:blocked` without a real unresolved blocker.
- **Read:** Current Issue, audit result, label definitions, and Project field
  options.
- **Check:** Ensure specification completeness and replace conflicting lifecycle
  labels rather than accumulating them. Apply `codex:ready` only after audit.
- **Exit:** GitHub reports the requested label and Project state consistently.
- **Report:** Previous and resulting values, mutation performed, verification,
  and the next gate.

## Stage 4: Create a branch and implement

- **Entry:** The Task is open, has `codex:ready`, is unblocked and sufficiently
  specified, the user requests implementation, and the base is current.
- **Default permission:** Repository writes within Task scope.
- **Allowed:** Create or use the Task branch, make the smallest scoped changes,
  and run current repository validation.
- **Forbidden:** Unrelated refactors; scope expansion; destructive cleanup;
  GitHub metadata writes; commits, pushes, or Pull Requests unless separately
  requested.
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
  Requests, Issue/Project edits, merge, or claims about post-merge state.
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
- **Default permission:** Merge write followed by read-only verification.
- **Allowed:** Perform the repository-approved merge, then synchronize and verify
  state as explicitly requested.
- **Forbidden:** `--admin`, bypassing protection or CI, merging with failed gates,
  manually closing a Task expected to close through `Closes #<Task>`, closing the
  parent Feature from sub-issue count alone, or deleting branches.
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
