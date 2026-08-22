---
name: task-delivery-runner
description: Deliver one maintainer-specified existing GitHub Task through LCK initial Delivery, or repair an existing Task PR from an independent review handoff. Initial Delivery uses LCK for workspace preparation, Critical Outcome, formal validation, commit, push, PR resolution/creation, checks, and Project Status. Independent review, merge, closeout, and Feature completion remain outside this Skill.
---

# Task delivery runner

Use this Skill for one existing Task explicitly named by the maintainer.

Initial Delivery is controlled by **LCK**. Codex / Claude owns semantic work;
LCK owns deterministic lifecycle mechanics. A successful initial Delivery ends
at `READY_FOR_REVIEW` and a Human boundary. It never starts Independent Review.

Review remediation remains the separate procedure at the end of this Skill
until its LCK cutover is implemented by the Review / Remediation migration Task.

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

## Policies and shared semantics

Read applicable `AGENTS.md` and
`.agents/policies/command-execution.md`, `.agents/policies/workflow-evidence.md`.
Shared lifecycle semantics are owned by `docs/development/issue-workflow.md`.
Read only the sections needed by the current invocation.

The current Task body is the business specification. Do not default to reading comments, complete Parent/Epic bodies, dependency bodies, templates, workflows, validation sources, or linked docs/ADRs. Expand only when the current Task explicitly references them, the specification is missing or ambiguous, a dependency affects implementation, a safety/architecture constraint applies, or verification requires it.
Verifying these mechanical facts does not require reading the full text of any source into the model context. Read the minimum relevant source/section, evaluate sufficiency, and expand further only if still insufficient.

It must contain a valid `Critical Outcome` contract with:

```text
Caller: ...
Capability: ...
Observable result: ...
Verification test: tests/.../test_*.py::test_*
```

The verification target is data, not an arbitrary command. LCK executes only
the bounded pytest verifier defined by the repository runtime.

## Initial Delivery authority boundary

For **initial Delivery**, the Agent / Skill MAY:

- read the current Task and required scoped context;
- understand, design, implement, diagnose, and explain the change;
- edit files within approved Task scope;
- run targeted validation while developing;
- provide semantic completion metadata such as commit message, implementation
  summary, risks, and limitations.

For **initial Delivery**, the Agent / Skill MUST NOT directly:

- choose or create the Task branch;
- stage or commit the final candidate;
- choose a remote/refspec or push;
- choose a PR number or create/reuse/update the PR mechanically;
- change Project lifecycle state;
- invent or pass branch/SHA/base/PR identity as workflow authority;
- start Independent Review, merge, close the Issue, or perform closeout.

Those mechanics belong to LCK. If an LCK command returns STOP, do not fall back
to direct Git/GitHub commands. Report the STOP reason and wait for the required
maintainer or implementation action.

## Initial Delivery procedure

### 1. LCK Delivery Prepare

The first lifecycle action is:

```bash
python tools/agent_workflow/lck.py delivery prepare <TASK>
```

Proceed only when LCK returns a resolved Delivery context. LCK reacquires live
Git/GitHub facts, verifies Task identity/readiness/blockers, and creates,
selects, or restores the one correct Task workspace.

Do not pass branch, expected SHA, base SHA, PR number, remote, or refspec.

### 2. Semantic implementation

Read the current Task body and implement the smallest complete change that
satisfies Objective, Requirements, Critical Outcome, Acceptance Criteria, and
explicit scope boundaries.

During implementation, use a matching targeted Validation profile when useful:

```bash
tools/agent_workflow/wsl2_validation_runner.py targeted:tools-tests
tools/agent_workflow/wsl2_validation_runner.py targeted:workflow-tests
```

Targeted validation is development feedback only. Do not treat it as final
Delivery authorization and do not weaken tests to obtain a pass.

Before completion, inspect the complete workspace diff semantically. Remove or
repair unrelated, generated, secret-bearing, or prohibited changes. The
workspace presented to LCK is the candidate Task tree.

### 3. LCK Delivery Complete

After semantic implementation is complete, invoke LCK with semantic metadata:

```bash
python tools/agent_workflow/lck.py delivery complete <TASK> \
  --commit-message "<scoped commit message>" \
  --summary "<implementation summary>" \
  --risks "<risks or limitations>"
```

Do not supply branch, remote, SHA, base SHA, PR number, or refspec.

Within one bounded invocation, LCK performs the deterministic sequence:

```text
reacquire live Task/Git/GitHub facts
→ validate Delivery Complete eligibility
→ parse current Task Critical Outcome
→ stage current candidate tree
→ run Critical Outcome verifier
→ run formal Delivery validation
→ prove validated staged tree is unchanged
→ commit_current_tree
→ ensure_remote_branch
→ ensure_open_pr
→ wait for applicable checks
→ set Project Status Review
→ reacquire live facts
→ prove local HEAD == remote HEAD == PR head
→ READY_FOR_REVIEW
→ STOP at Human boundary
```

If the invocation resumes after an earlier partial side effect, LCK does not
trust a previous receipt as authority. It reacquires current facts and reruns
the applicable Critical Outcome / formal validation gates before continuing.

`Critical Outcome FAIL`, formal validation failure, remote divergence,
ambiguous PR identity, failed/unknown checks, stale lifecycle facts, or failed
postconditions are terminal for that invocation. LCK does not rebase, force
push, guess an identity, or route itself into repair.

### 4. Initial Delivery reporting

On `READY_FOR_REVIEW`, report:

- canonical Task and PR URLs returned by current facts;
- semantic changed-file summary;
- Critical Outcome result;
- formal Delivery validation result;
- LCK effect receipts at summary level;
- checks result and preserved limitations;
- current lifecycle state;
- remaining semantic risks or limitations;
- exact fresh-session `task-pr-review-runner` prompt.

Always state that Independent Review, Merge, Issue close, post-merge closeout,
branch deletion, and Feature completion were not performed.

## Review remediation

This section applies only after a non-passing `task-pr-review-runner` handoff.
Its lifecycle-control migration is intentionally outside the initial Delivery
cutover.

The remediation handoff must identify Task and PR, reviewed head SHA, verdict,
required Blocking/High/Medium findings, objective gates, and maintainer
questions if any. `review-remediation` requires the bounded handoff. Missing or contradictory handoff identity is a semantic admission failure: stop before Runner or repair writes.
Do not use the generic snapshot fallback to manufacture remediation authority.

Run the current deterministic remediation Preflight before editing:

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py delivery \
  --entry-point review-remediation \
  --task <TASK> \
  --expected-main-sha <CURRENT_MAIN_SHA> \
  --pr <PR> \
  --expected-base-sha <REVIEWED_BASE_SHA> \
  --expected-head-sha <REVIEWED_HEAD_SHA>
```

Proceed only on a valid passing result. `partial`, `unknown`, lifecycle
conflict, identity conflict, or other valid non-pass is terminal; do not repair
it automatically.

Classify every handoff item before editing: confirmed in-scope implementation,
test, documentation, or configuration finding → repair; scope/AC/public
behavior/architecture change → Human Gate; Low/Nit → leave unchanged unless
explicitly requested.

Implement the smallest complete repair and add regression coverage. Existing
remediation mechanics continue to use the current Evidence / Validation Runner
contract for this phase: create scoped repair commits, run `workflow-delivery`
against the clean committed head, push only the validated head, reuse the
existing Task PR, wait for checks, and regenerate `delivery-readiness`.

The previous Review verdict applies only to its reviewed head. Any new commit
requires a fresh independent review. Remediation never submits a GitHub Review,
merges, closes the Task, performs closeout, or starts the new Review itself.

Report the handoff items addressed, new head, validation/check result, remaining
limitations, and the exact fresh-session review prompt.

## Failure discipline

Distinguish deterministic STOP from tool failure:

- valid LCK / Runner STOP → stop; no alternate write route;
- missing/unparseable tool result → at most one identical bounded retry;
- semantic Task ambiguity or requested scope expansion → Human Gate;
- remote divergence or identity ambiguity → stop; never force push or guess;
- `partial` / `unknown` evidence remains `partial` / `unknown` in reporting.

The Skill is a semantic procedure. It is not the lifecycle state machine.
