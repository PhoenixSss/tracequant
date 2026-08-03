---
name: task-delivery
description: Deliver one maintainer-specified existing GitHub Task from readiness through implementation, fixed-runner validation, commit, push, PR creation, checks, and a compact handoff for independent review. Do not choose Tasks, review independently, merge, close Issues, clean branches, or assess Feature completion.
---

# Task delivery

Use this Skill for one existing Task explicitly identified by the maintainer. A
normal invocation authorizes only the bounded pre-merge sequence through one
non-Draft PR ready for a new-session `task-pr-review`.

## Standard invocation

```text
请按 task-delivery 完整处理
[Task] <当前完整标题> #<Task编号>，
直到 PR 准备好接受独立审查。
```

The Issue number is primary; the current GitHub Issue title is canonical.

## Rules and fixed runners

Before commands, read applicable `AGENTS.md` / `AGENTS.override.md` and:

```text
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
```

Normal Delivery uses only these fixed front doors for mechanical workflow facts
and final validation:

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py delivery \
  --task <TASK> \
  --expected-main-sha <LOCKED_MAIN_SHA>

tools/agent_workflow/wsl2_validation_runner.py workflow-delivery \
  --base-sha <LOCKED_TASK_BASE_SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py delivery-readiness \
  --task <TASK> \
  --pr <PR> \
  --expected-base-sha <LOCKED_TASK_BASE_SHA> \
  --expected-head-sha <CURRENT_HEAD_SHA>
```

During implementation, use a named targeted Validation profile only when it
matches the work:

```text
targeted
targeted:tools-tests
targeted:workflow-tests
```

The final `workflow-delivery` profile is the authoritative Delivery validation
observation. It runs current applicable CI-equivalent checks and required Skill
validators through the fixed front door. Do not also run the replaced raw
workflow validator or a second full direct `uv` validation chain.

Do not repeat the legacy direct GitHub/Git fact-query chain after a valid
snapshot. A `partial`, `unknown`, `fail`, schema/version mismatch, truncation, or
drift is not permission to run the entire old chain. Expand only the exact
missing or conflicting fact with bounded read-only inspection, report the
fallback, and preserve the original gate status.

## Permission boundary

After every preceding gate passes, this invocation may:

1. read Task, repository, Git, GitHub, Project, and Relationship facts;
2. apply the exact lifecycle transitions to `Ready`, `In Progress`, and `Review`;
3. create or safely reuse the exact Task branch;
4. edit only approved Task scope;
5. run targeted and final fixed-runner validation;
6. stage explicit approved paths, make scoped commits, and ordinary push;
7. create or reuse one matching non-Draft PR;
8. wait for and read checks;
9. produce a compact independent-review handoff.

It never authorizes changing Task scope or hierarchy, unrelated writes, force
push, `--admin`, protection bypass, `git reset --hard`, `git clean`, GitHub
Review submission, merge, Issue close, post-merge work, branch deletion, or
Feature completion assessment.

If the Task specification requires revision, report the exact proposed change
and stop unless the maintainer separately authorizes the Issue edit.

## Lifecycle state

Keep Codex labels and Project `Status` separate:

| Fact | Codex label | Project Status |
| --- | --- | --- |
| specification incomplete | `codex:needs-spec` | `Inbox` / `Specifying` |
| readiness passed | `codex:ready` | `Ready` |
| implementation active | `codex:ready` | `In Progress` |
| PR or review active | `codex:ready` | `Review` |
| real blocker | `codex:blocked` | `Blocked` |
| verified post-merge completion | `codex:ready` | `Done` |

Implementation requires an open Task, `Ready` or `In Progress`, `codex:ready`,
no `codex:blocked`, and this invocation. Stop before a state write if actual
Project options differ.

## Phase 1: identity and readiness

Generate one current fixed `delivery` snapshot. Verify repository/origin,
current branch and workspace, refs/worktrees, synchronized main identity, Task
state/type/title/body/comments, Parent/dependencies/Relationships, labels,
Project fields, blockers, templates, workflows, validation sources, and affected
architecture.

The Agent—not the digest—decides whether goal, scope, acceptance criteria,
exceptions, and out-of-scope work are implementable without guessing. A derived
ProjectV2 title may lag; Issue `content.title` is canonical.

Only after readiness passes, apply and re-read the exact state transitions.

## Phase 2: branch and implementation

Start from clean synchronized `main` unless current recovery facts prove a valid
later point. Create or reuse one exact Task branch after verifying identity,
history, scope, and ownership.

Implement the smallest correct change. Follow scoped rules, preserve safety,
add required tests/docs, inspect tracked and untracked scope, and never weaken
tests merely to pass. Necessary source inspection and development commands are
not replaced by the workflow runners.

## Phase 3: validation, commit, and push

Use matching targeted profiles during development. Before final validation,
perform semantic acceptance mapping, inspect the full diff and untracked files,
check generated files/secrets/scope, and ensure ignored evidence/validation
artifacts are not staged.

When this Task changes trusted Runner, profile, Rules, Evidence, Validation, or
Skill files, the fixed runner intentionally cannot validate an uncommitted
trusted-file change. Run explicit development checks, commit the scoped trusted
change, then run `workflow-delivery`. If it fails, repair with another scoped
commit and rerun; never treat pre-commit checks as the final phase observation.

Stage explicit paths only; never use `git add .`. Push normally without force and
re-read branch/head identity.

## Phase 4: PR and readiness

Create or recover exactly one non-Draft PR targeting the approved base. It must
identify Task/branch/head, contain `Closes #<Task>`, describe implementation,
validation, risks and limitations, and contain only approved files/commits.

Set Project Status to `Review` only after the PR exists or review starts. Wait
for applicable checks. Distinguish configured Required Checks, none configured,
plan-limit `403`, and actual check runs; never convert pending, failed, stale,
cancelled, or unknown into success.

Generate one fixed `delivery-readiness` snapshot. Re-check identity, base/head,
effective diff, commits/files, linkage, checks, reviews, threads, lifecycle, and
scope. Read the full PR diff and relevant context for semantic self-review.

Pause on a new commit, drift, failed validation/check, blocking thread, state
conflict, or unresolved Blocking/High/Medium self-finding.

## Failure expansion and recovery

- `pass`: consume the compact digest and continue semantic work.
- `partial` / `unknown`: inspect only the named unknown gates; never claim full
  readiness.
- `fail` / drift: stop before the dependent write and inspect bounded diagnostics.
- Runner unavailable, version mismatch, or schema mismatch: report the control
  plane incompatibility. Do not silently reactivate the complete legacy path.

Resume from the first unverified gate using current facts. Do not repeat a
correct metadata write, recreate artifacts, or run both old and new paths.

## Independent-review handoff

This Skill must not perform or simulate independent review. On a clean success
path output only the compact canonical identity, URLs, branch, base/head,
changed-file summary, fixed validation/check summary, lifecycle state, thread
count, limitations, and `Ready for independent review`.

End with the exact new-session `task-pr-review` prompt including expected
base/head SHAs. Do not copy complete bodies, diff, successful logs, or the full
Delivery report.

## Mandatory pauses and final report

Stop before a related write when identity, specification, scope, branch,
workspace, validation, checks, threads, credentials, permissions, or Runner
facts are uncertain or conflicting, or when an out-of-scope change, force,
bypass, destructive cleanup, merge, Issue close, or maintainer design decision
would be required.

Use a compact report only for a clean success. Any fallback, partial/unknown,
failure, drift, conflict, or decision uses a detailed report. Always state that
independent Review, Merge, Issue close, post-merge work, branch deletion, and
Feature completion were not performed.
