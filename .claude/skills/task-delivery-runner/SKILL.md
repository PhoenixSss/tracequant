---
name: task-delivery-runner
description: Deliver one maintainer-specified existing GitHub Task through LCK, including explicitly requested remediation after a failed Independent Review. LCK owns workspace preparation, live identity, Critical Outcome, formal validation, commit, push, PR reuse/create as phase-appropriate, checks, and lifecycle boundaries. Review, merge, closeout, and Feature completion remain separate.
---

# Task delivery runner

Use this Skill for one existing Task explicitly named by the maintainer.

Initial Delivery is controlled by **LCK**. Codex / Claude owns semantic work;
LCK owns deterministic lifecycle mechanics. A successful initial Delivery ends
at `READY_FOR_REVIEW` and a Human boundary. It never starts Independent Review.

Review remediation is also LCK-controlled after cutover, but starts only from an
explicit Human request that identifies a failed LCK `review_id`.

## Standard invocation

```text
请按 task-delivery-runner 完整处理
[Task] <当前完整标题> #<Task编号>，
直到 PR 准备好接受独立审查。
```

For remediation after an Independent Review FAIL:

```text
请按 task-delivery-runner 对 Task #<Task编号> 显式执行 remediation。
Failed Review ID: <REVIEW_ID>
```

The failed `review_id` locates semantic findings only. LCK independently reacquires
the current Task / PR / base / head / branch state; mechanical facts from the Review
record are not write authorization. The originating workspace-local Review audit record
is the default findings source. When intentionally switching clone or Agent runtime and
that ignored local record is unavailable, the maintainer MAY provide the completed
Review findings as an explicit semantic-only handoff:

```bash
uv run --frozen python tools/agent_workflow/lck.py remediation prepare <TASK> \
  --review-id <FAILED_REVIEW_ID> --findings-file <COMPLETED_REVIEW_FINDINGS>
```

`--findings-file` never supplies PR/head/base/branch/checks authority and MUST NOT be
used to synthesize or rewrite findings merely to obtain admission. Existing Codex/local
record behavior remains the primary path.

The Issue number is the primary key; the current Issue title is canonical.

Before the first LCK command, verify the project launcher:

```bash
command -v uv
uv --version
uv run --frozen python --version
```

If this preflight fails, report an environment/launcher failure. It is not a
Delivery or Review verdict, and must not be converted into `STOP_REQUIRED`.

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

## Long-operation wait and progress contract

For known heavyweight operations — `delivery complete`, `review prepare`, and
formal workflow validation — use a fixed 30-second wait window for the first
wait and every subsequent still-running poll (for example,
`write_stdin`/`yield_time_ms=30000`). Do not use adaptive 1-second, 10-second,
20-second, or 30-second polling. If the process exits earlier, return its
output immediately; 30 seconds is the maximum wait window, not a minimum
runtime.

During these operations, structured progress may appear on stderr. It is
bounded, non-authoritative observability only: use it to identify the current
operation and stage, never as evidence for eligibility, freshness, verdicts,
retry, Merge, Closeout, or any lifecycle result. The final stdout JSON remains
the only machine-parseable lifecycle result.

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
uv run --frozen python tools/agent_workflow/lck.py delivery prepare <TASK>
```

Proceed only when LCK returns a resolved Delivery context. LCK reacquires live
Git/GitHub facts, verifies Task identity/readiness/blockers, and creates,
selects, or restores the one correct Task workspace.

Do not pass branch, expected SHA, base SHA, PR number, remote, or refspec.

### 2. Semantic implementation

Read the current Task body and implement the smallest complete change that
satisfies Objective, Requirements, Critical Outcome, Acceptance Criteria, and
explicit scope boundaries.

#### Development validation boundary

Normal implementation validation is development feedback, not Delivery
authorization. Before LCK Delivery Complete, choose the smallest check that
matches the changed surface: a named targeted profile, a focused pytest node,
or a scoped static check. Rerun the relevant target after a change when useful.

For a normal Task, do not proactively run a full `current-ci-equivalent`, full
`workflow-delivery`, or equivalent full-repository validation before LCK
Delivery Complete merely as a precaution or to "confirm" the candidate. The
formal full Delivery validation is intentionally reserved for LCK Delivery
Complete.

A broader or full validation run is allowed only when concrete evidence requires
diagnostic expansion, such as a targeted failure indicating a cross-module
regression, an unresolved concern tied to observed behavior, or an explicit
maintainer request. Task importance, broad scope, or a global-infrastructure
classification alone is not a diagnostic trigger. Record and treat any broader
run as diagnostic information only: it does not replace LCK's Critical Outcome
verifier or formal validation, authorize commit/push/PR effects, or advance
lifecycle state.

During implementation, use a matching targeted Validation profile when useful:

```bash
tools/agent_workflow/wsl2_validation_runner.py targeted:tools-tests
tools/agent_workflow/wsl2_validation_runner.py targeted:workflow-tests
```

Targeted validation is development feedback only. Do not treat it as final
Delivery authorization and do not weaken tests to obtain a pass. Any full
validation performed for diagnosis remains non-authoritative; LCK Delivery
Complete must still run its own Critical Outcome and formal Delivery validation
and bind the exact validated tree before commit.

Once change-relevant validation passes, known Task Contract gaps are closed,
and no unresolved failure, finding, or concrete diagnostic concern remains,
the candidate is **targeted-ready**. Proceed to LCK Delivery Complete. Do not
insert precautionary repository-wide pytest/static validation, a second broad
self-review, or status-only checking between targeted-ready and LCK merely to
confirm the candidate. A newly observed failure/finding or an explicit
maintainer request may reopen diagnostic work; that work remains
non-authoritative.

Before completion, inspect the complete workspace diff semantically. Remove or
repair unrelated, generated, secret-bearing, or prohibited changes. The
workspace presented to LCK is the candidate Task tree.

### 3. LCK Delivery Complete

After semantic implementation is complete, invoke LCK with semantic metadata:

```bash
uv run --frozen python tools/agent_workflow/lck.py delivery complete <TASK> \
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
→ observe current checks without making CI completion a Delivery gate
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
ambiguous PR identity, stale lifecycle facts, or failed postconditions are
terminal for that invocation. Pending, failed, missing, or otherwise unresolved
PR checks may be reported as a non-authoritative observation, but do not veto
Initial Delivery. Required-check success remains a Review Complete / Merge
Preflight gate. LCK does not rebase, force push, guess an identity, or route
itself into repair.

### 4. Initial Delivery reporting

On `READY_FOR_REVIEW`, report:

- canonical Task and PR URLs returned by current facts;
- semantic changed-file summary;
- Critical Outcome result;
- formal Delivery validation result;
- LCK effect receipts at summary level;
- non-blocking checks observation and preserved limitations;
- current lifecycle state;
- remaining semantic risks or limitations;
- exact fresh-session `task-pr-review-runner` prompt.

Always state that Independent Review, Merge, Issue close, post-merge closeout,
branch deletion, and Feature completion were not performed.

## Review remediation

This section applies only after the latest completed LCK Independent Review returned
`FAIL` / `STOP_REQUIRED` and the maintainer explicitly requests repair. A successful
remediation forces a fresh Review before any later remediation can start.

The Agent / Skill MUST NOT accept a bounded mechanical handoff, expected SHA, base SHA,
PR number, checks snapshot, validation snapshot, or old Evidence Runner snapshot as
Remediation authority. The failed Review record supplies semantic findings; LCK resolves
all actionable mechanical identity from current live Git / GitHub state.

### 1. LCK Remediation Prepare

```bash
uv run --frozen python tools/agent_workflow/lck.py remediation prepare <TASK> \
  --review-id <FAILED_REVIEW_ID>
```

Proceed only on `READY_FOR_REMEDIATION`. With the normal local-record path, LCK
verifies the failed Review audit record and reads its findings. For an explicit
cross-workspace/runtime handoff where that ignored audit record does not exist,
`--findings-file` supplies only the already-completed semantic findings. In both cases LCK
reacquires the current OPEN non-Draft PR/head/base and Task branch and selects/restores
the current implementation workspace.

Do not pass expected head/base/PR identity. If current live identity is ambiguous,
diverged, missing, or unsafe, STOP; do not use archived evidence snapshots or
legacy command paths as a fallback.

### 2. Semantic repair

Read the returned findings, confirm them against the current implementation, implement
the smallest complete repair, and add regression coverage. Classify each finding by the
boundary at which its evidence can truthfully exist:

- implementation/config/docs/test defects that can be repaired on the current workspace
  are Remediation work and must be addressed before completion;
- a requirement whose evidence can only be produced **after the repaired candidate head
  exists**, or by a **separate provider / fresh Independent Review invocation**, is a
  deferred Review-acceptance item. It remains unsatisfied, but it is **not** a Human Gate
  and **not** a prerequisite for `remediation complete`;
- genuine scope/architecture/product ambiguity still stops at Human Gate rather than being
  silently expanded.

Do not fabricate deferred evidence and do not ask the maintainer to provide future-head
provider/cross-provider receipts before the repaired head exists. If actual repair changes
are ready, continue to LCK Remediation Complete and report the deferred acceptance item as
pending for the next fresh Review. If **no repair change exists** and the only remaining
item is external/future Review evidence, do not manufacture a commit merely to advance the
lifecycle. Close the prepared Remediation session through the formal no-change terminal
operation below, then obtain the external evidence for the unchanged head.

The Agent may edit and run targeted development validation. It MUST NOT directly stage
the final tree, commit, push, create/replace the PR, mutate lifecycle state, or start a
new Review.

### 3. LCK Remediation No Change

When the formal Remediation role was entered but semantic inspection confirms that no
implementation/config/docs/test repair is required, close that prepared session without
changing the candidate:

```bash
uv run --frozen python tools/agent_workflow/lck.py remediation no-change <TASK> \
  --review-id <FAILED_REVIEW_ID> \
  --summary "<why no implementation change is required>"
```

Proceed only on `NO_IMPLEMENTATION_CHANGE`. LCK reacquires the current OPEN PR/head/base,
requires the selected Task workspace to be clean and still exactly match the prepared
Remediation target, writes a formal no-change receipt under `.workflow.local/lck/`, and
releases the prepared Remediation session. It does **not** commit, push, create a new head,
set `fresh-review-required`, or claim that deferred provider/cross-runtime acceptance is
satisfied. A retry for the same unchanged target is idempotent and replays the prior
receipt.

This operation is also the deterministic recovery path for an older prepared session that
was left open because the Skill correctly refused to manufacture a no-op commit. Do not
manually delete or edit the session marker.

### 4. LCK Remediation Complete

After an actual repair is ready:

```bash
uv run --frozen python tools/agent_workflow/lck.py remediation complete <TASK> \
  --review-id <FAILED_REVIEW_ID> \
  --commit-message "<scoped repair commit message>" \
  --summary "<repair summary>" \
  --risks "<risks or limitations>"
```

LCK reuses the Task-2 Delivery effects for Critical Outcome, formal validation, exact
validated-tree commit, remote synchronization, non-blocking check observation, and final
local/remote/PR head verification. Unlike Initial Delivery, Remediation must reuse existing OPEN PR
state; it never creates a replacement PR.

`remediation complete` gates the repaired implementation and the mechanics needed to create
a stable new candidate head. It MUST NOT require provider-attributed implementation receipts,
fresh cross-provider Review receipts, or other acceptance evidence that can only be
truthfully produced by a separate session after that new head exists. Those requirements
remain pending and MUST still block the later Independent Review PASS when the Task requires
them. `READY_FOR_NEW_REVIEW` means only that the repaired head is ready to be reviewed.

A successful result is:

```text
READY_FOR_NEW_REVIEW
→ STOP
```

The new head invalidates the earlier semantic Review result. Remediation MUST NOT start
Independent Review automatically. Report the repaired findings, new head,
validation/check results, limitations, and tell the maintainer to start a new fresh
`task-pr-review-runner` invocation.

## Failure discipline

Distinguish deterministic STOP from tool failure:

- valid LCK / Runner STOP → stop; no alternate write route;
- missing/unparseable tool result → at most one identical bounded retry;
- semantic Task ambiguity or requested scope expansion → Human Gate;
- remote divergence or identity ambiguity → stop; never force push or guess;
- `partial` / `unknown` evidence remains `partial` / `unknown` in reporting.

The Skill is a semantic procedure. It is not the lifecycle state machine.
