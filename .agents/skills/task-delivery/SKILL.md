---
name: task-delivery
description: Deliver one maintainer-specified existing GitHub Task from readiness through implementation, validation, commit, push, PR creation, successful checks, and a compact handoff for an independent review. Do not choose or create Tasks, review the PR independently, merge, close Issues, perform post-merge cleanup, or assess Feature completion.
---

# Task delivery

Use this Skill for the complete pre-merge delivery of one existing Task explicitly
identified by the maintainer. A normal invocation authorizes only the bounded
sequence in this Skill through a non-Draft PR ready for a new-session
`task-pr-review`.

## Standard invocation

```text
请按 task-delivery 完整处理
[Task] <当前完整标题> #<Task编号>，
直到 PR 准备好接受独立审查。
```

The Issue number is the primary key; the current GitHub Issue title is canonical.

## Rules and tools

Before commands, read the applicable `AGENTS.md` / `AGENTS.override.md` and:

```text
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
```

Use current Issue, templates, workflows, `pyproject.toml`, and lock files as
current facts. Historical Tasks and PRs are process evidence only.

Use the shared tools for mechanical reads and validation:

```text
python tools/agent_workflow/workflow_evidence.py delivery-preflight ...
python tools/agent_workflow/workflow_validation.py run --phase delivery --base-sha <Task base SHA> ...
python tools/agent_workflow/workflow_evidence.py delivery-readiness ...
```

Do not repeat the full legacy Git/GitHub query chain after a valid snapshot.
Read detailed facts manually only when a gate is `unknown`, `fail`, truncated,
conflicting, or needed for semantic work. Tool failure uses the safe read-only
fallback defined by the shared policy; no gate may be skipped.

## Permission boundary

When every preceding gate passes, this invocation may:

1. read Task, repository, Git, GitHub, Project, and Relationship facts;
2. apply the exact Task lifecycle transitions required for `Ready`,
   `In Progress`, and `Review`;
3. create or safely reuse the exact Task branch;
4. edit only files within approved Task scope;
5. run current validation;
6. explicitly stage approved paths, make one scoped commit, and ordinary push;
7. create or reuse one matching non-Draft PR;
8. wait for and read checks;
9. produce a compact independent-review handoff.

It never authorizes:

- changing Task goal, body, Parent, Priority, Size, Phase, Target, or
  Relationships;
- unrelated GitHub or repository writes;
- force push, `--admin`, protection bypass, `git reset --hard`, or `git clean`;
- GitHub Review submission;
- merge, Issue close, post-merge validation, or branch deletion;
- Feature completion assessment.

If the Task specification requires revision, report the exact proposed revision
and stop unless the maintainer separately authorizes that existing-Issue edit.

## Lifecycle state

Keep Codex labels and Project `Status` separate. With the repository's expected
options, use:

| Fact | Codex label | Project Status |
| --- | --- | --- |
| specification incomplete | `codex:needs-spec` | `Inbox` / `Specifying` |
| readiness gate passed | `codex:ready` | `Ready` |
| implementation active | `codex:ready` | `In Progress` |
| PR or review active | `codex:ready` | `Review` |
| real unresolved blocker | `codex:blocked` | `Blocked` |
| verified post-merge completion | `codex:ready` | `Done` |

Implementation requires an open Issue, `Ready` or `In Progress`,
`codex:ready`, no `codex:blocked`, and this invocation. `codex:ready` alone is
not an implementation selector. If actual Project options differ, stop before a
state write.

## Phase 1: identity and readiness

Generate one current preflight snapshot. At minimum verify:

- repository and remote identity;
- current branch, full tracked/untracked status, refs, worktrees, and
  `origin/main`;
- Task exists, is open, has `type:task`, canonical title matches, and complete
  body/comments were read;
- Parent, dependencies, labels, Project fields, and formal Relationships;
- no unresolved blocker or specification conflict;
- current templates, workflows, validation sources, and affected architecture.

A derived ProjectV2 `Title` may lag. Use Issue `content.title`; do not attempt to
update an Issue-backed item's derived title.

The Agent—not the snapshot—must decide that goal, scope, acceptance criteria,
exceptions, and out-of-scope work are implementable without guessing a key
architecture decision.

If readiness passes, verify or apply only the exact transition to `Ready`, then
to `In Progress` when work starts. Re-read each write result.

## Phase 2: branch and implementation

Start from clean, synchronized `main` unless verified recovery facts justify a
later point. Create or reuse one exact Task branch. Existing artifacts are reused
only after identity, history, scope, and ownership are verified.

Implement the smallest correct change:

- follow every scoped agent rule;
- satisfy all acceptance criteria;
- do not silently expand scope or refactor unrelated code;
- do not add a production dependency without documented Task justification;
- preserve financial, credential, UTC, data, and live-trading safety rules;
- add or update tests and documentation required by changed behavior;
- never weaken or delete tests merely to pass.

Inspect complete tracked and untracked scope throughout implementation.

## Phase 3: validation, commit, and push

Run the shared validation runner against current Task-branch facts and the locked
Task base SHA. Governance changes detected in `base...HEAD` require all applicable
Skill validators. A compact success summary is
not permission to omit a command; a failure requires bounded diagnostics and a
real fix or pause.

Before commit:

- perform semantic self-check against every acceptance criterion;
- inspect full diff, untracked files, generated files, secrets, and scope;
- resolve all Blocking, High, and Medium self-check findings;
- ensure local ignored execution, evidence, and validation files are
  not staged.

Stage explicit paths only; never use `git add .`. Make one scoped commit unless a
recovered history already proves a valid Task-only commit. Push normally without
force. Re-read branch and commit identity.

## Phase 4: PR and readiness

Create or recover exactly one non-Draft PR using the current template. The PR
must:

- target the approved base branch;
- identify the exact Task branch and current head;
- contain `Closes #<Task>`;
- accurately summarize implementation, validation, risks, and limitations;
- contain only approved files and commits.

Set Project Status to `Review` only after the PR exists or review is actually
starting. Wait for all applicable check runs to complete. Distinguish configured
Required Checks, no configured Required Checks, GitHub plan-limit `403`, and
ordinary check runs; never convert pending, failed, stale, cancelled, or unknown
checks into success.

Generate `delivery-readiness` after the PR and checks exist. Re-check Task/PR
identity, base/head SHA, effective diff, commits, files, closing linkage, checks,
reviews, threads, lifecycle state, and current scope. Read the full PR diff and
relevant file context for the final semantic self-check.

Pause if a new commit, base/head/diff change, failed validation, blocking thread,
state conflict, or unresolved Blocking/High/Medium finding appears.

## Independent-review handoff

The next step is a new session using `task-pr-review`; this Skill must not perform
or simulate that review.

On a clean success path, output a compact handoff containing:

```text
Task number / canonical title / URL
PR number / title / URL
Task branch
Base branch and base SHA
Head SHA
Changed-file summary
Local validation summary
Required-Checks configuration and actual check-run summary
Project Status and Codex label
Unresolved-thread count
Known limitations
Ready for independent review
```

End with the exact standard next-session prompt, including expected base/head
SHAs. Do not copy the Task/PR body, complete diff, complete validation output, or
the complete delivery report.

## Mandatory pauses

Stop before the related write when identity, repository, title, Parent,
dependency, Relationship, specification, scope, branch ownership, workspace,
validation, check, review-thread, credential, or permission facts are uncertain
or conflicting. Also stop when the next action would require an out-of-scope
file, Task revision, unrelated refactor, merge, Issue close, force operation,
protection bypass, destructive cleanup, or maintainer design decision.

Report completed safe steps, exact failing gate, current state, and the single
next decision.

## Recovery

Every run reads current facts. Resume from the first unverified gate when a valid
Task branch, scoped changes, commit, push, PR, or completed checks already exist.
Never repeat a correct metadata write or recreate a deleted artifact merely to
follow phase numbering.


## Final report

Use the compact success contract from the shared policy. Use a detailed report
for any finding, failure, fallback, drift, conflict, or maintainer decision.
Always state actions deliberately not performed, especially independent review,
Merge, Issue close, post-merge work, and branch deletion.
