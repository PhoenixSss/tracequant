---
name: task-delivery
description: Deliver a maintainer-specified, already-created GitHub Task from identity and specification gates through implementation, validation, commit, push, Pull Request creation, successful configured Required Checks and applicable check runs, and a fixed handoff for independent review. Do not use to choose, plan, split, draft, or create Tasks; review Feature completion; perform an independent Pull Request review; merge; close Issues; verify post-merge state; or clean branches.
---

# Task delivery

Use this Skill for the complete pre-merge delivery of one maintainer-specified,
already-created GitHub Task.

Do not override system, developer, or current explicit user instructions, or any
applicable `AGENTS.md` or `AGENTS.override.md`. Do not treat this Skill as a
replacement for the current Issue, `.github/ISSUE_TEMPLATE/task.yml`,
`.github/pull_request_template.md`, workflows, `pyproject.toml`, or lock files.

A normal invocation authorizes the bounded sequence in this Skill from Task
identity verification through a Pull Request that is ready for a separate,
independent review. It does not authorize merge or post-merge work.

After this Skill creates or recovers the PR and passes its own readiness
self-check, the next step is a new session using `task-pr-review`. This Skill
must not automatically call, simulate, or replace that independent review.

## Standard invocation

Prefer a complete current Task title plus Issue number:

```text
请按 task-delivery 完整处理
[Task] <当前完整标题> #<Task编号>，
直到 PR 准备好接受独立审查。
```

The Issue number is the primary key. The current GitHub Issue title is the
canonical title and is an additional human-safety check.

## Scope boundary

This Skill starts only after the user identifies an existing GitHub Task.

It does not:

- identify the next Task;
- split a Feature or Epic;
- decide that a new Task should exist;
- plan, draft, choose, or create a new Task;
- choose initial Parent, Project fields, labels, or Relationships for a new Task;
- assess, recommend, or perform Feature completion;
- perform an independent Pull Request review;
- merge a Pull Request;
- close an Issue;
- synchronize post-merge `main`;
- perform post-merge validation;
- delete local or remote branches;
- run a full lifecycle retrospective by default.

Parent state and sub-issue counts may be read and reported only as raw facts.
Never infer Feature completion from `n / n closed`.

## Resolve rules by responsibility

- Treat system, developer, and current explicit user instructions as the highest
  constraints.
- Apply every `AGENTS.md` and `AGENTS.override.md` governing files in scope.
- Use this Skill for delivery phases, gates, permission boundaries, recovery,
  and safety.
- Use the current GitHub Task body and comments for the approved goal, scope,
  acceptance criteria, Parent, dependencies, labels, fields, and exclusions.
- Use `.github/ISSUE_TEMPLATE/task.yml` to check the existing Task structure.
- Use `.github/pull_request_template.md` for the Pull Request body.
- Use current workflows, `pyproject.toml`, and lock files for validation commands,
  tools, versions, and environment constraints.
- Use `.agents/policies/command-execution.md` as the single normative command
  routing policy and the optional ignored local execution profile only as a
  machine-specific routing preference.
- Use `.agents/policies/task-workflow-telemetry.md` only for an explicitly
  active, local measurement run. Telemetry is not a gate or correctness source.
- Use historical Tasks and Pull Requests only as process evidence, never as a
  current fact source.

Read current repository and GitHub facts on every run. Do not reuse a cached
template, command, field value, Project option, branch, title, or workflow.

If sources conflict, required facts are missing, or all rules cannot be
satisfied, stop before the related write, identify the exact conflict, and wait
for a maintainer decision. Do not weaken acceptance criteria or choose a source
on the maintainer's behalf.

## Permission model

A request to complete `task-delivery` authorizes only the following actions for
the exact identified Task, when each gate passes:

1. read-only identity, specification, repository, and GitHub audits;
2. the exact Task lifecycle metadata transitions needed for `Ready`,
   `In Progress`, `Review`, or a real `Blocked` state;
3. creation or safe reuse of the exact Task branch;
4. repository changes strictly within approved Task scope;
5. current CI-equivalent local validation;
6. explicit-path staging, one scoped commit, and ordinary non-force push;
7. creation or reuse of one non-Draft Pull Request for the Task;
8. waiting for and reading configured Required Checks and applicable check runs;
9. a self-check and fixed independent-review handoff.

This invocation does not authorize:

- editing the Task specification;
- changing Parent, Priority, Size, Phase, Target, or Relationships;
- unrelated Issue, Project, label, or Pull Request writes;
- submitting a GitHub review;
- merge, Issue close, post-merge work, or branch deletion;
- force push, `--admin`, protection bypass, `git reset --hard`, or `git clean`.

If the Task specification needs revision, report the exact change and stop
unless the user separately authorizes editing that existing Task.

## Identify the Task safely

Before any write:

1. parse the requested Issue number;
2. read that Issue from the current repository;
3. verify that it exists and represents a Task;
4. read its current GitHub Issue title;
5. treat that Issue title as the canonical title;
6. if the user supplied a title, compare it with the canonical title;
7. verify the Issue is open for active delivery;
8. verify repository identity, Parent, labels, Project item, fields, comments,
   and formal Relationships.

Normalize only superficial title differences such as:

- leading or trailing whitespace;
- repeated internal whitespace;
- ordinary case differences;
- common full-width versus half-width punctuation;
- Markdown escaping.

Stop before writes when title and number clearly identify different work.
Report both identities:

```text
Requested:
#<number> <supplied title>

GitHub:
#<number> <canonical title>
```

A derived ProjectV2 `Title` may lag behind the Issue title. Use Issue
`content.title` as authoritative, report the mismatch, and do not attempt a
DraftIssue title update for an Issue-backed item.

When only a number is supplied, read and report the canonical title before
continuing. The documented production format remains full title plus number.

Re-check Task identity before creating a Pull Request and before producing the
independent-review handoff. If a title change signals a material scope expansion,
stop and re-audit the Task body.

## Codex labels and Project Status

Keep Codex lifecycle labels separate from Project `Status`:

- `codex:*` labels express specification-gate or blocker facts.
- Project `Status` expresses the current workflow phase.

When actual Project options are exactly `Inbox`, `Specifying`, `Ready`,
`In Progress`, `Review`, `Blocked`, and `Done`, use:

| Lifecycle fact | Codex label | Project Status |
| --- | --- | --- |
| Task recorded, specification not started | `codex:needs-spec` | `Inbox` |
| Specification is being completed | `codex:needs-spec` | `Specifying` |
| Pre-implementation gate passed | `codex:ready` | `Ready` |
| Implementation is active | `codex:ready` | `In Progress` |
| Pull Request or review is active | `codex:ready` | `Review` |
| A real unresolved blocker exists | `codex:blocked` | `Blocked` |
| Verified merge and closeout completed | `codex:ready` | `Done` |

`codex:ready` means that the Task passed the Codex pre-implementation
specification gate. It persists through `Ready`, `In Progress`, `Review`, and
`Done` as an audit marker. The label alone does not mean that the Task is
currently waiting for implementation and never authorizes implementation by
itself.

Implementation requires all of:

- Issue state is `OPEN`;
- Project Status is `Ready` or `In Progress`;
- `codex:ready` is present;
- `codex:blocked` is absent;
- the current invocation authorizes implementation.

Any query for implementable Tasks must at least restrict `is:open` and must
verify Project Status before writing. Never use `label:codex:ready` alone as an
implementation selector.

If a completed Task is reopened, do not resume implementation solely because
`codex:ready` remains. Reassess whether the Task belongs in `Specifying`,
`In Progress`, or `Review`.

If actual Project Status options differ from the seven values above, report the
actual options and stop related state writes. Do not create, rename, or guess
Project options.

Issue `OPEN/CLOSED` and Project `Status` are independent facts. Never infer one
from the other.

## Phase 1: Read and establish the baseline

Read:

- repository root and remote identity;
- all governing agent files;
- current branch, full status including untracked files, refs, recent log, and
  worktrees;
- current Task body, comments, fields, labels, Parent, dependencies, and formal
  Relationships;
- current Task template and Pull Request template;
- current workflows, `pyproject.toml`, and lock files;
- `.agents/policies/command-execution.md` and the optional ignored local
  execution profile;
- existing branches, commits, and Pull Requests related to the Task.

Check:

- local repository is the expected repository;
- `main` and `origin/main` are synchronized before creating new work;
- workspace, index, and untracked state are clean unless safely resuming verified
  work for this exact Task;
- no unrelated branch or worktree is active;
- the Task has one primary goal suitable for one Pull Request;
- scope, acceptance criteria, exclusions, Parent, dependencies, fields, labels,
  and Relationships are explicit and consistent;
- no real unresolved blocker exists.

Do not clean, reset, rebase, merge, or switch away from unrelated work.

## Phase 2: Audit readiness and apply the Ready gate

The readiness audit is read-only until it passes.

Require:

- complete and auditable Task specification;
- no key architecture, product, security, or risk decision left to the
  implementer;
- correct Parent and Project fields;
- consistent lifecycle labels;
- no unresolved formal or factual blocker;
- approved file and behavior scope;
- current validation expectations.

When the audit passes and the Task is `codex:needs-spec` in `Inbox` or
`Specifying`, the full-delivery invocation authorizes this exact transition:

```text
Remove codex:needs-spec
Add codex:ready
Project Status: Inbox or Specifying -> Ready
```

Re-read and verify the result before repository writes.

When a real blocker exists, the invocation authorizes only:

```text
Remove conflicting codex lifecycle label
Add codex:blocked
Project Status: current status -> Blocked
```

Then stop and report the blocker.

When leaving `Blocked`, restore the lifecycle label and state supported by
current facts rather than mechanically restoring `Ready`.

## Phase 3: Create or recover the Task branch

Implementation may start only after the Task actually has `codex:ready`, is
open, is unblocked, and the repository baseline is safe.

Before writing:

- fetch current remote state;
- verify `main == origin/main`;
- verify full clean status unless safely resuming this exact Task;
- derive one exact branch name associated with the Task;
- inspect existing local and remote refs and existing Pull Requests.

When starting new work:

1. create the exact Task branch from synchronized `main`;
2. verify its base;
3. transition Project Status `Ready -> In Progress`;
4. re-read the Project item.

When resuming:

- verify branch, commits, changed files, and any Pull Request all belong to the
  same Task;
- verify no unapproved files or unexplained history;
- infer the actual phase from facts;
- apply only the exact missing lifecycle transition.

Stop rather than reusing a branch or Pull Request whose identity is uncertain.

## Phase 4: Implement the approved Task

Make the smallest correct change that satisfies the Task.

- Modify only approved files and behaviors.
- Do not perform unrelated refactors.
- Do not expand scope silently.
- Do not add dependencies unless the Task authorizes them.
- Preserve public interfaces unless the Task explicitly changes them.
- Follow all repository financial-safety, data-correctness, and architecture
  rules.
- Update documentation when behavior or interfaces change.
- Inspect tracked and untracked files throughout the work.

If implementation requires an out-of-scope file, architecture change, new
dependency, weakened acceptance criterion, or maintainer decision, stop before
making that change.

## Phase 5: Validate

Read current workflows, `pyproject.toml`, and lock files before choosing
commands. Run the current CI-equivalent validation, including documentation-only
Tasks when required by CI.

Always require:

- relevant tests;
- repository lint and formatting checks;
- repository type checks;
- applicable documentation or Skill validators;
- `git diff --check`;
- explicit inspection of all tracked and untracked changes.

Do not:

- claim CI equivalence from a subset of commands;
- weaken, delete, or skip tests merely to pass;
- hide a failure behind a non-equivalent check;
- misread command-specific exit codes.

A validation failure pauses delivery. Do not continue to commit or PR creation
unless the failure is resolved within approved scope and all checks subsequently
pass.

## Phase 6: Stage, commit, and push

Before staging:

- inspect status including all untracked files;
- inspect every changed file;
- confirm changed-file scope matches the Task.

Stage explicit paths only. Never use `git add .`.

After staging:

- inspect `git diff --cached`;
- inspect staged file names and status;
- confirm no unapproved content.

Create one scoped commit unless current repository conventions require otherwise.
After committing, inspect:

- commit subject and body;
- `git show --stat`;
- committed file list;
- `git diff HEAD^ HEAD --check`;
- branch and worktree status.

Push only the exact Task branch with ordinary non-force push.

Never use force push, `--admin`, destructive cleanup, or a protection bypass.

## Phase 7: Create or recover the Pull Request

Read the current Pull Request template at execution time.

Before creating a PR:

- re-check Task number and canonical title;
- confirm the branch has no existing conflicting PR;
- confirm committed scope and validation;
- confirm the Task remains open and in the expected Project.

Create one non-Draft Pull Request that:

- targets the repository's approved base branch;
- uses the current PR template;
- describes only the Task scope;
- includes `Closes #<Task>`;
- does not pre-check post-merge facts;
- omits optional CLI arguments whose values are empty.

After creation, re-read:

- PR number, title, body, URL, state, draft state, base, head, and head SHA;
- changed files and commits;
- Issue closing linkage;
- checks and mergeability;
- existing reviews and unresolved threads.

Transition Project Status to `Review` only after the PR exists. Keep
`codex:ready`.

If a matching PR already exists, verify it represents the same Task and branch
before resuming. Never create a duplicate PR.

## Phase 8: Wait for checks and perform the readiness self-check

Read and report both:

- the branch protection Required Checks configuration and its current status;
- all applicable check runs produced by current workflows and their conclusions.

If no Required Checks are configured, do not invent a required gate.

Wait until both are true:

- all configured Required Checks, if any, have reached a successful terminal
  state;
- all applicable check runs have reached a successful terminal state.

Do not report readiness while any applicable CI check run is failed, cancelled,
skipped unexpectedly, stale, pending, or in progress.

A Pull Request is ready for independent review only when:

- local CI-equivalent validation passed;
- configured Required Checks, if any, and all applicable GitHub check runs
  passed;
- PR is open and not Draft;
- base, head, head SHA, commits, and changed files are stable and expected;
- `Closes #<Task>` is present and correct;
- no requested changes or unresolved review thread blocks review;
- no Blocking, High, or unresolved Medium self-check finding remains;
- Task is open, `codex:ready`, and Project Status is `Review`.

Perform a thorough implementation self-check covering:

- Task scope and acceptance criteria;
- correctness and safety;
- tests and documentation;
- dependency and lock-file changes;
- workflow and template compliance;
- changed-file and commit scope;
- residual risks and known limitations.

Call this a `self-check`, `readiness check`, or `pre-review check`. Do not call it
an independent review and do not submit a GitHub review.

Keep these gates separate:

```text
local validation passed
!= CI passed
!= implementation self-check passed
!= independent Pull Request review passed
!= merge is authorized
```

The independent review must be performed in a separate session with
`task-pr-review`. That review re-reads and verifies the handoff facts; it does
not accept this Skill's self-check or handoff as correctness evidence. Any
change to PR head, base, or effective diff invalidates a previous independent
review conclusion and requires a new Codex session to review the new effective
diff from the beginning. After this Skill pushes a fix that creates a new head
SHA, the previous review session must not continue to a new verdict. Old review
findings may guide the fix, but old verdicts and completed review steps are not
inherited.

## Fixed independent-review handoff

Stop after producing a handoff containing at least:

```text
Task number
Task canonical title
Task URL
PR number
PR title
PR URL
Task branch
Base SHA
Head SHA
Changed files
Local validation commands and results
Required Checks configuration and status
Actual check runs and conclusions
Project Status
Codex lifecycle label
Unresolved review threads
Known limitations or residual risks
Ready for independent review: yes or no
```

Also include the exact next prompt:

```text
请使用 task-pr-review，独立只读审查
[Task] <当前完整标题> #<Task编号>
对应的 PR #<PR编号>。

Expected base SHA: <base SHA>
Expected head SHA: <head SHA>
```

The next review must occur in a separate session under `task-pr-review` and an
independent read-only process. Do not continue to merge, closeout, branch
cleanup, or represent the self-check as an independent review. If another commit
is pushed after this handoff, generate a new handoff with the new base/head SHAs
and start a new independent review session.

## Mandatory pause conditions

Pause immediately when any of these applies:

- Task title and number materially disagree;
- Issue does not exist, is not a Task, or is not open;
- specification is incomplete or needs an unauthorized edit;
- Project options or lifecycle labels conflict with current rules;
- Parent, dependencies, or Relationships are ambiguous or inconsistent;
- a real blocker exists;
- worktree, index, untracked files, branch, or worktree ownership is unsafe;
- `main` and `origin/main` are not safely synchronized;
- implementation needs out-of-scope files or decisions;
- validation, configured Required Checks, or applicable CI check runs fail;
- a branch, commit, or PR cannot be proven to belong to this Task;
- PR head, base, or effective diff changes unexpectedly;
- a Blocking, High, or unresolved Medium self-check finding exists;
- the process would require force push, `--admin`, bypass, reset, clean, merge,
  Issue close, or branch deletion;
- another actor merges the PR during delivery.

Report the exact gate, evidence, actions completed, and safe recovery point.

## Recovery and idempotency

Derive progress from current facts. Do not assume a run starts from Phase 1.

Support safe recovery when:

- Task is already `Ready`, `In Progress`, or `Review`;
- exact Task branch already exists;
- verified in-scope changes are present;
- commit exists but is not pushed;
- branch is pushed but PR does not exist;
- matching PR already exists;
- CI is pending or already complete;
- Project Status is already at the correct phase.

For every existing artifact, verify Task identity, branch, scope, history, and
current state before reuse. Completed steps are verified rather than repeated.

## Git and validation safety

- Never use `git add .`.
- Never use `git clean`.
- Never use `git reset --hard`.
- Never force push.
- Never use `--admin` or bypass protection.
- Never merge.
- Inspect full untracked status; ordinary diffs omit untracked files.
- Keep worktree, staged, committed, and PR file scope aligned with Task scope.
- Interpret command exit codes according to command semantics.


## Optional workflow telemetry

Read `.agents/policies/task-workflow-telemetry.md`, then perform one lightweight
local `telemetry.py status` check for this Task. If there is no explicit active
run, do no further telemetry work and add no telemetry report fields.

When a run is active, use only facts and counts already produced by delivery.
Append one `task-delivery` phase summary at completion or interruption. Do not
add GitHub queries, repository reads, validation commands, or retries only for
measurement. Do not store raw prompts, source contents, command output, or
credentials.

Telemetry does not authorize implementation, metadata writes, commit, push, PR
creation, or readiness. A telemetry write failure is reported as `telemetry
incomplete`; delivery continues or stops only according to this Skill's existing
gates. Do not create or edit the local telemetry configuration automatically.

## Command execution routing

Before executing a command, read `.agents/policies/command-execution.md` and
check the optional ignored `.agents/execution-profile.local.toml`.

First apply this Skill's lifecycle authorization and prohibitions. Only then use
the shared policy to select `sandbox-first`, `elevated-first`, or `adaptive`.
A route changes execution context only; it never authorizes implementation,
metadata writes, commit, push, Pull Request creation, or another lifecycle step.

Keep every retry identical in executable, argv, working directory, repository,
Task identity, lifecycle stage, and intent. Do not execute `gh auth login`; use
the shared credential procedure and wait for a maintainer decision when elevated
execution also confirms invalid credentials.

Report routing events required by the shared policy. Never elevate a command
forbidden by this Skill.

## Delivery report

Keep reports concise and include:

- current phase and recovered starting point;
- fact sources read;
- Task identity and canonical title;
- entry gates;
- lifecycle transitions;
- files changed;
- validation commands, exit codes, and results;
- material execution-routing decisions and elevated attempts required by the
  shared command policy;
- telemetry run ID and completion state only when an explicit run is active;
- commit, branch, and Pull Request state;
- Required Checks configuration/status and actual check runs/conclusions;
- self-check findings;
- actions deliberately not performed;
- fixed independent-review handoff;
- next human gate.

## Final checklist

- Current Task number and canonical title were verified.
- Current governing files, Issue, templates, workflows, and validation sources
  were read rather than recalled.
- The Task was existing and maintainer-specified.
- Specification and blocker gates passed before implementation.
- Exact lifecycle metadata transitions were verified after writes.
- Parent and sub-issue facts were not converted into Feature completion advice.
- All tracked and untracked files were inspected.
- Changed, staged, committed, pushed, and PR scope remained approved.
- Current CI-equivalent validation, configured Required Checks if any, and all
  applicable check runs passed.
- The PR used the current template and contains `Closes #<Task>`.
- The self-check was not represented as an independent review.
- No merge, Issue close, post-merge work, or branch deletion occurred.
- Command routes followed the shared policy and no local profile expanded this
  Skill's permissions.
- No force push, `--admin`, reset, clean, bypass, credential exposure, or
  unrelated change occurred.
