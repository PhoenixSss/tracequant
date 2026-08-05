---
name: task-delivery-runner
description: Deliver one maintainer-specified existing GitHub Task from readiness through implementation, Runner validation, commit, push, PR creation, checks, and handoff for independent review; or repair an existing Task PR from an independent review handoff and return it for a new independent review. Do not choose Tasks, review independently, merge, close Issues, clean unrelated branches, or assess Feature completion.
---

# Task delivery runner

Use this Skill for one existing Task explicitly named by the maintainer. It
supports initial delivery and independent-review remediation. A successful run
ends with one non-Draft PR ready for a new-session
`task-pr-review-runner`.

## Standard invocation

```text
请按 task-delivery-runner 完整处理
[Task] <当前完整标题> #<Task编号>，
直到 PR 准备好接受独立审查。
```

For remediation after an independent review:

```text
请按 task-delivery-runner 修复
[Task] <当前完整标题> #<Task编号>
对应 PR #<PR编号> 的独立审查问题，
并继续处理，直到 PR 再次准备好接受新的独立审查。

Review remediation handoff:

<粘贴 task-pr-review-runner 输出的 remediation handoff>
```

The Issue number is the primary key; the current Issue title is canonical.
A request may limit execution to one named Phase. Verify prior Phase facts and
stop at the requested boundary.

## Policies and Runner interface

Read applicable `AGENTS.md` / `AGENTS.override.md` and:

```text
.agents/policies/workflow-evidence.md
```

Use the current repository Runner interfaces:

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

During implementation, use a matching targeted Validation profile only when
needed:

```text
targeted
targeted:tools-tests
targeted:workflow-tests
```

`workflow-delivery` is the final CI-equivalent validation for the committed
candidate head. The Evidence Runner is the source for workflow facts covered by
its snapshot. For `partial`, `unknown`, `fail`, truncation, schema mismatch, or
drift, inspect only the named facts or failed commands and preserve the original
status.

Evidence artifacts must record the actual Skill, Runner, profile/schema, target
repository, base/head, and content hashes used in the run.

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
  artifact before deciding whether to repair.
- `blocked`: a Runner precondition failed (e.g. unclean worktree, wrong branch,
  wrong cwd, identity mismatch). Fix the precondition; do not retry with
  different arguments.
- `partial` / `unknown`: bounded diagnostics in the artifact — inspect only the
  named gates.

Never fall back to an equivalent direct command chain after a Runner result.
Never retry a Runner command with modified arguments to work around a failure.

## Permission boundary

After its prerequisite gates pass, this Skill may:

- read Task, repository, Git, GitHub, Project, and Relationship facts;
- move the exact Task through `Ready`, `In Progress`, and `Review`;
- create or reuse the exact Task branch;
- edit only approved Task scope;
- run targeted and final Runner validation;
- stage explicit paths, create scoped commits, and push normally;
- create or reuse one matching non-Draft PR;
- repair confirmed in-scope findings from an independent Review and update the exact existing Task PR;
- wait for and read checks;
- produce the independent-review handoff.

It does not authorize Task-scope changes, unrelated writes, force push,
`--admin`, protection bypass, destructive Git cleanup, GitHub Review
submission, merge, Issue close, post-merge work, branch deletion, or Feature
completion assessment. Proposed Task-specification changes require separate
maintainer authorization.

## Lifecycle state

Keep lifecycle labels separate from Project `Status`:

| State | Codex label | Project Status |
| --- | --- | --- |
| specification incomplete | `codex:needs-spec` | `Inbox` / `Specifying` |
| ready | `codex:ready` | `Ready` |
| implementation active | `codex:ready` | `In Progress` |
| PR or review active | `codex:ready` | `Review` |
| blocked | `codex:blocked` | `Blocked` |
| verified post-merge | `codex:ready` | `Done` |

Implementation requires an open Task, `Ready` or `In Progress`,
`codex:ready`, no `codex:blocked`, and this invocation. Stop before a state
write if actual Project options differ.

## Phase 1: identity and readiness

Generate the `delivery` snapshot. Verify repository/origin, workspace,
refs/worktrees, synchronized main identity, Task type/title/body/comments,
Parent/dependencies/Relationships, labels, Project fields, blockers, templates,
workflows, validation sources, and affected architecture.

Independently confirm that goal, scope, acceptance criteria, exceptions, and
out-of-scope work are implementable without guessing. The Issue title is
canonical when a derived Project title lags. Apply and re-read lifecycle
transitions only after readiness passes.

## Phase 2: branch and implementation

Start from clean synchronized `main` unless current facts prove a valid recovery
point. Create or reuse one exact Task branch after verifying its identity,
history, scope, and ownership.

Implement the smallest correct change. Follow scoped rules, preserve safety, add
required tests/docs, inspect tracked and untracked scope, and do not weaken tests
to obtain a pass. Source inspection and development commands remain available
for implementation work.

## Phase 3: commit, final validation, and push

Use targeted profiles during development. Before committing, map acceptance
criteria to implementation/tests, inspect the complete diff and untracked files,
and exclude secrets, generated files, unrelated changes, and ignored evidence
artifacts.

Stage explicit paths only; do not use `git add .`. Create a scoped commit, then
run `workflow-delivery` against the clean committed head. On failure, inspect
bounded evidence, repair with another scoped commit, and rerun. Push only the
head that passed final validation. Re-read branch and remote-head identity.

## Phase 4: PR and readiness

Create or recover exactly one non-Draft PR targeting the approved base. The PR
must identify Task/branch/head, contain `Closes #<Task>`, describe
implementation, validation, risks, and limitations, and contain only approved
files and commits.

Set Project Status to `Review` only after the PR exists or review begins. Wait
for applicable checks. Distinguish no configured Required Checks, a recognized
plan-limit `403`, and actual pending, failed, stale, cancelled, skipped, or
unavailable checks.

Generate `delivery-readiness`. Verify Task/PR identity, base/head, effective
diff, files/commits, linkage, checks, reviews, threads, lifecycle, and scope.
Read the full PR diff and relevant context for semantic self-review.

Stop on a new commit, drift, validation/check failure, blocking thread, state
conflict, or unresolved Blocking/High/Medium self-finding.

## Review remediation

Use this mode when a `task-pr-review-runner` verdict requires changes or an
objective gate must be re-evaluated for an existing open Task PR.

The remediation handoff must identify:

- Task and PR;
- reviewed head SHA;
- Review verdict;
- required Blocking, High, or Medium findings;
- unresolved objective gates;
- maintainer decisions, if any.

Re-read current Task, PR, branch, head, effective diff, checks, reviews, and
threads. Verify that the PR is open, belongs to the expected Task branch, and
matches the reviewed head. If the head changed and the change is not already
explained by current repository facts, stop for clarification.

Classify every handoff item before editing:

- confirmed in-scope implementation, test, documentation, or configuration
  finding: repair it;
- pending or unavailable objective gate: recheck or wait without inventing a
  code change;
- requested change to Task scope, acceptance criteria, public behavior, or an
  approved architecture decision: stop for maintainer authorization;
- Low or Nit finding: leave unchanged unless the maintainer explicitly requests
  it.

Implement the smallest complete repair and add regression coverage where
applicable. Preserve Task scope, safety boundaries, and unrelated behavior.

Create scoped repair commits. Run final `workflow-delivery` validation against
the clean committed head, push the validated head, wait for applicable checks,
and regenerate `delivery-readiness`. Update the PR description or validation
summary when the repair materially changes them.

The previous Review verdict applies only to its reviewed head and becomes stale
after any new commit. This Skill does not submit or resolve a GitHub Review,
merge, close the Task, or perform closeout.

Stop when the updated PR is ready for a new independent review. Report:

- handoff items addressed and how;
- items not addressed and why;
- old reviewed head and new head;
- repair commits and changed files;
- regression tests and final validation;
- checks, reviews, threads, and remaining limitations;
- the exact new-session `task-pr-review-runner` prompt.

## Recovery and handoff

Resume from the first unverified gate using current facts. Verify completed
writes instead of repeating them. For remediation, treat the supplied handoff as
an index to independently verified findings and gates, not as permission to
change Task scope. A Runner result does not replace semantic judgment.

This Skill never performs independent review. On a clean path, including after
remediation, report canonical Task/PR URLs, branch, base/head, changed-file
summary, final validation/check summary, lifecycle state, thread count,
limitations, and:

```text
Ready for independent review
```

End with the exact new-session `task-pr-review-runner` prompt and expected
base/head SHAs. Use a detailed report for any finding, fallback,
`partial`/`unknown`, failure, drift, conflict, or maintainer decision. Always
state that Review, Merge, Issue close, post-merge work, branch deletion, and
Feature completion were not performed.
