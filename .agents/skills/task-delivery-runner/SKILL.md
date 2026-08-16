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

Read applicable `AGENTS.md` / `AGENTS.override.md` and
`.agents/policies/command-execution.md`, `.agents/policies/workflow-evidence.md`.

Shared lifecycle semantics are owned by `docs/development/issue-workflow.md`
(§3 lifecycle metadata, §4 readiness, §6 Delivery, §10 remediation). Read the
minimal needed section for the current phase; do not duplicate lifecycle prose
in this Skill.

Use the current repository Runner interfaces:

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py delivery \
  --entry-point <ENTRY_POINT> \
  --task <TASK> \
  --expected-main-sha <LOCKED_MAIN_SHA> \
  [--branch <BRANCH> --expected-base-sha <BASE> \
   [--expected-head-sha <HEAD>] | --pr <PR>]

tools/agent_workflow/wsl2_validation_runner.py workflow-delivery \
  --base-sha <LOCKED_TASK_BASE_SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py delivery-readiness \
  --task <TASK> \
  --pr <PR> \
  --expected-base-sha <LOCKED_TASK_BASE_SHA> \
  --expected-head-sha <CURRENT_HEAD_SHA>
```

| Entry point | Invoked at | Params beyond `--task`, `--expected-main-sha` |
|---|---|---|
| `delivery-start` | Phase 1 before any write | — |
| `implementation` | Phase 2 before branch/implementation writes | `--branch --expected-base-sha` |
| `final-validation` | Phase 3 before commit + `workflow-delivery` | `--branch --expected-base-sha --expected-head-sha` |
| `pr-readiness` | Phase 4 before PR creation/push | `--branch --expected-base-sha --expected-head-sha` |
| `review-remediation` | Remediation before any repair edit | `--pr --expected-base-sha --expected-head-sha --review-handoff-id` |

During implementation, use a matching targeted Validation profile only when
needed: `targeted`, `targeted:tools-tests`, or `targeted:workflow-tests`.

`workflow-delivery` is the final CI-equivalent validation for the committed
candidate head. The Evidence Runner is the source for workflow facts covered by
its snapshot. For `partial`, `unknown`, `fail`, truncation, schema mismatch, or
drift, inspect only the named facts or failed commands and preserve the original
status.

Evidence artifacts must record the Skill, Runner, profile/schema, target
repository, base/head, and content hashes used in the run.

## Permission boundary

After its prerequisite gates pass, this Skill may: read Task, repository, Git,
GitHub, Project, and Relationship facts; move the exact Task through `Ready`,
`In Progress`, and `Review`; create or reuse the exact Task branch; edit only
approved Task scope; run targeted and final Runner validation; stage explicit
paths, create scoped commits, and push normally; create or reuse one matching
non-Draft PR; repair confirmed in-scope independent review findings and update
the existing Task PR; wait for and read checks; produce the independent-review
handoff.

It does not authorize: Task-scope changes, unrelated writes, force push,
`--admin`, protection bypass, destructive Git cleanup, GitHub Review
submission, merge, Issue close, post-merge work, branch deletion, or Feature
completion assessment. Proposed Task-specification changes require separate
maintainer authorization.

## Lifecycle state

The label / Project `Status` mapping and readiness conditions are defined in
`docs/development/issue-workflow.md` §3–§4; do not re-derive the table here.
Implementation requires an open Task, `Ready` or `In Progress`, `codex:ready`,
no `codex:blocked`, and this invocation. Stop before a state write if actual
Project options differ.

`codex:ready` and `codex:needs-spec` are mutually exclusive lifecycle labels.
Their coexistence is a lifecycle conflict that fails Preflight; do not proceed.

## Preflight gate (terminal admission gate, required first step)

Every invocation MUST run the `delivery` Preflight first, before any Git,
GitHub, or code write. Preflight is a **terminal admission gate**, not a
remediation phase — no recovery path through a non-pass result.

Proceed only when ALL hold: `status = pass`, `disposition.workflow_may_continue
= true`, `disposition.write_actions_allowed = true`, identity matches this
Task/repository/entry-point/branch/base/head/PR.

Any valid non-pass (`fail`, `partial`, `unknown`, `blocked`, lifecycle
conflict, identity conflict, incompatible entry state,
maintainer-decision-required) is a **final admission decision**: stop
immediately, read only the compact digest and failing gate, report failure
and required maintainer action, state no writes were performed. Do NOT modify
code/config/GitHub state, auto-repair, enter implementation/validation/PR/
readiness/review-remediation, route to a remediation loop, or re-invoke the
same profile.

Distinguish: **no valid result** (Runner crash, transport break, unparseable
schema, no artifact) → one identical bounded retry. **Valid non-pass** →
final; no auto-remediation or retry.

### Worktree compatibility

`worktree_state_compatible` is per-entry-point, not universal:

| Entry point | Dirty worktree | Handling |
|---|---|---|
| `delivery-start` | Allowed | Full flow takes custody of existing changes |
| `implementation` | Forbidden at branch admission | Branch creation/reuse must begin from a clean worktree; development may then create Task-owned changes |
| `final-validation` | Forbidden | Must bind clean committed head |
| `pr-readiness` | Forbidden | Local, remote, PR head must be stable |
| `review-remediation` | Forbidden (fail-closed) | Must start from determinate reviewed head |

When dirty allowed, Preflight records staged/unstaged/untracked files but
never stages, commits, stashes, discards, resets, or edits. Dirty with
unrelated, generated, secret-bearing, or prohibited files → fail.

### Invocation lifecycle

- First action: run Preflight with the appropriate entry point.
- Full flow/phase-specific/new-session: execute Preflight once.
- Same-invocation later phases: check local preconditions + drift.
- Phase-specific calls stop at the requested boundary.
- No handoff/artifact: generate one minimal read-only snapshot.
- Valid handoff/artifact (same Task/branch/base/head): reuse; regenerate only
  when missing, expired, contradictory, or insufficient.

## Phase 1: identity and readiness

Generate the `delivery` snapshot. The snapshot provides deterministic
identity facts: repository/origin, workspace, refs/worktrees, synchronized
main identity, implementation-bearing leaf type/title/state, Parent identity,
dependencies/Relationships metadata, labels, Project fields, blocker state,
and PR head/base/checks when applicable. Verifying these mechanical facts
does not require reading the full text of any source into the model context.

The current Task body is the business specification: read it and confirm
that goal, scope, acceptance criteria, exceptions, and out-of-scope work are
implementable without guessing. Do not default to reading comments, complete
Parent/Epic bodies, dependency bodies, templates, workflows, validation
sources, or linked docs/ADRs. Expand those only when a trigger applies: the
Task body explicitly references them, the specification is missing or
ambiguous, a conflict must be located, a dependency's state/contract affects
implementation, a safety/architecture constraint applies, or verification
requires them. Read the minimum relevant source/section, evaluate
sufficiency, and expand further only if still insufficient.

The Issue title is canonical when a derived Project title lags. Apply and
re-read lifecycle transitions only after readiness passes.

## Phase 2: branch and implementation

Start from clean `main` unless facts prove a valid recovery point. Create or
reuse one exact Task branch after verifying identity, history, scope, and
ownership. For a missing branch, an `implementation` preflight PASS with
`branch creation = pass` is the normal workflow authorization to create the
new branch using only the canonical `task/<Task number>-<slug>` form:

```text
git switch -c task/<Task number>-<slug> <expected-base-sha>
```

Legacy numeric branch forms may be reused only when an existing branch is
already proven to belong to the current Task; they are never used for new
branch creation.

Immediately verify the new branch, HEAD, base, and clean state. For an existing
branch, switch to it only after the Runner proves identity/base/ownership and
reuse it idempotently. Never reset, overwrite, or reuse a branch whose identity
is ambiguous; a dirty worktree is fail-closed when branch creation is required.

Implement the smallest correct change — follow scoped rules, preserve safety,
add required tests/docs, inspect tracked and untracked scope. Do not weaken
tests to obtain a pass.

## Phase 3: commit, final validation, and push

Use targeted profiles during development. Before committing, map acceptance
criteria to implementation/tests, inspect the complete diff, exclude secrets,
generated files, unrelated changes, and ignored evidence artifacts. Stage
explicit paths only; do not use `git add .`. Create a scoped commit, run
`workflow-delivery` against the clean committed head. On failure, inspect
bounded evidence, repair with another scoped commit, and rerun. Push only the
head that passed final validation. Re-read branch and remote-head identity.

## Phase 4: PR and readiness

### 4a. PR resolve or create

Use `tools/agent_workflow/pr_resolve.py` as the single PR resolve/create path.
The helper enforces in code (not in Skill prose): single structured query with
`--limit 2`, exit-code check, non-empty stdout, JSON parse; 0 matches → create
with exit/stdout/URL checks; 1 match → reuse; >1 → fail-closed; identity
verification with all required fields, no retry with modified fields; mismatch
on number/URL/state/draft/base-head branch/base-head SHA → fail-closed. Never
suppresses stderr, parses empty stdout as JSON, retries with modified fields,
or falls back to text-mode.

The PR must contain `Closes #<Task>`, describe implementation, validation,
risks, and limitations, and contain only approved files and commits. Set
Project Status to `Review` only after the PR exists.

### 4b. Checks

Wait for applicable checks. Distinguish no configured Required Checks, a
recognized plan-limit `403`, and pending, failed, stale, cancelled, skipped,
or unavailable checks.

### 4c. Semantic self-review artifact

Before `delivery-readiness`, produce a structured self-review artifact using
the schema from `tools/agent_workflow/self_review.py`. The artifact must: lock
Task number, business base SHA, current head SHA, effective diff SHA-256, and
PR number at generation time; re-confirm head has not changed before
finalizing; map every acceptance criterion to `verified` |
`partially_verified` | `not_verified` with implementation and validation
evidence; group changed files into review areas derived from the actual diff;
record per area: files, status, key behaviour changes, mapped criteria,
mechanical validation results, findings, and remaining risk; enforce that
`overall: "verified"` requires every area and criterion `verified` with
evidence entries; never accept a keyword grep or file-exists check as semantic
review; never claim provenance or canonical-state clearance without a
corresponding mechanical validator result.

Write to `.agents/evidence.local/self-reviews/` (Git-ignored, not committed).
The artifact is bound to current head and diff; any new commit makes it stale.

### 4d. Delivery readiness

Generate `delivery-readiness`. Verify Task/PR identity, base/head, effective
diff, files/commits, linkage, checks, reviews, threads, lifecycle, and scope.
Stop on a new commit, drift, validation/check failure, blocking thread, state
conflict, or unresolved Blocking/High/Medium self-finding.

## Review remediation

Use this mode when a `task-pr-review-runner` verdict requires changes or an
objective gate must be re-evaluated for an existing open Task PR.

The remediation handoff must identify: Task and PR; reviewed head SHA; Review
verdict; required Blocking, High, or Medium findings; unresolved objective
gates; maintainer decisions, if any; and the canonical structured handoff
artifact under `.agents/evidence.local/review-handoffs/<evidence_id>.json`.
Pass the exact producer-emitted `<evidence_id>` as `--review-handoff-id`; the
Runner loads only that content-addressed artifact and never selects evidence by
an arbitrary directory scan.

Run the `review-remediation` preflight with the expected base/head before any
repair edit. A valid artifact is the conclusion carrier even when the PR has
zero submitted GitHub Reviews. The preflight must fail closed for missing,
malformed, stale, ambiguous, identity-mismatched, or maintainer-decision-
required artifacts.

Implementation admission also verifies any existing active PR for an existing
Task branch before allowing writes: exactly one current-Task PR, canonical
branch, expected base, and non-conflicting identity are required. Ambiguous,
cross-Task, or mismatched active PRs fail closed before implementation.

Re-read current Task, PR, branch, head, effective diff, checks, reviews, and
threads. Verify that the PR is open, belongs to the expected Task branch, and
matches the reviewed head. If the head changed and the change is not already
explained by current repository facts, stop for clarification.

Classify every handoff item before editing: confirmed in-scope
implementation/test/documentation/configuration finding → repair; pending or
unavailable objective gate → recheck or wait; scope/acceptance-criteria/
public-behavior/architecture change → stop for maintainer authorization;
Low or Nit → leave unchanged unless maintainer explicitly requests it.

Implement the smallest complete repair, add regression coverage, preserve Task
scope, safety boundaries, and unrelated behavior. Create scoped repair commits,
run final `workflow-delivery` against the clean committed head, push the
validated head, wait for checks, regenerate `delivery-readiness`. Update PR
description/validation summary when materially changed.

The previous Review verdict applies only to its reviewed head; any new commit
makes it stale. This Skill does not submit/resolve a GitHub Review, merge,
close the Task, or perform closeout. Stop when the updated PR is ready for a
new independent review. Report: handoff items addressed and how; items not
addressed and why; old reviewed head and new head; repair commits and changed
files; regression tests and final validation; checks, reviews, threads,
remaining limitations; exact new-session `task-pr-review-runner` prompt.

## Recovery and handoff

Recovery rules apply only after Invocation Preflight has passed and never
authorize remediation of a Preflight result. A valid non-pass Preflight is a
terminal disposition, not a recoverable state.

Resume from the first unverified gate by checking local preconditions plus
drift from the Preflight snapshot for the target entry point. Verify completed
writes instead of repeating them. For remediation, treat the supplied handoff
as an index to independently verified findings and gates, not as permission to
change Task scope. Stop on lifecycle conflict, identity drift, or entry-point
state invalidation.

This Skill never performs independent review. On a clean path, including after
remediation, report: canonical Task/PR URLs, branch, base/head, changed-file
summary; final validation/check summary, lifecycle state, thread count,
limitations; self-review artifact path, overall verdict, summary of each area
and acceptance criterion with evidence status; every `partial`/`unknown` from
`delivery-readiness` preserved with original reason (never upgraded to `pass`
by omission); mechanical validation conclusions (separate from semantic
self-review); unverified or partially verified content with explicit gaps;
and:

```text
Ready for independent review
```

**Reporting contract:** Every conclusion must trace to a self-review evidence
entry or mechanical validator result. Only `Verified` may be stated as fact.
`Partially verified` must state covered scope and gaps. `Not verified` must
not be rephrased as a pass. Do not expand a grep, file-exists check, or
partial validator result into "all complete." When `delivery-readiness` is
`partial`, retain the `partial`/`unknown` reasons.

End with the exact new-session `task-pr-review-runner` prompt and expected
base/head SHAs. Use a detailed report for any finding, fallback,
`partial`/`unknown`, failure, drift, conflict, or maintainer decision. Always
state that Review, Merge, Issue close, post-merge work, branch deletion, and
Feature completion were not performed.
