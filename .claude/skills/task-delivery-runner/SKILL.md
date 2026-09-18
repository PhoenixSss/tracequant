---
name: task-delivery-runner
description: Deliver a ready leaf Issue, remediate an explicitly identified failed Review, or refresh an existing Review candidate onto current main when the maintainer explicitly requests it.
---

# Leaf delivery runner

Use this Skill for one existing leaf Issue explicitly named by the maintainer. The
Issue number is the mechanical key; the current GitHub title and body are canonical.

## Select one workflow

Choose exactly one branch and read only its linked instructions:

- For a new implementation or ordinary Delivery request, read
  [references/initial-delivery.md](references/initial-delivery.md).
- For Remediation, require an explicit maintainer request and the failed LCK
  `review_id`, then read [references/remediation.md](references/remediation.md).
- For Candidate Refresh, require an explicit maintainer request for an existing
  leaf Issue in Review, then read
  [references/candidate-refresh.md](references/candidate-refresh.md).

Do not infer Remediation from an open PR, failing checks, comments, or an old review.
If neither branch can be selected safely, stop at a Human Gate.

The LCK entry points are `delivery prepare` / `delivery complete`,
`remediation prepare` / `remediation no-change` / `remediation complete`, and
the one-shot `refresh <TASK>` operation.
Their exact commands in the selected reference use the stable
`uv run --frozen python -m tools.lck` front door.

## Shared guardrails

Read applicable `AGENTS.md`. Use these canonical owners only when their decisions
apply to the selected branch:

- `.agents/policies/command-execution.md` for launcher preflight and failure
  classification;
- `.agents/policies/context-retrieval.md` for scoped context acquisition;
- `.agents/policies/workflow-evidence.md` for validation and evidence consumption;
- `docs/workflows/lck/lifecycle.md` and, for Remediation,
  `docs/workflows/lck/review-and-remediation.md` for shared lifecycle semantics.

Before the first LCK command, verify `command -v uv`, `uv --version`, and
`uv run --frozen python --version`. A launcher failure is an environment failure,
not a lifecycle verdict.

LCK alone owns branch selection, staging, commit, push, PR identity/effects, and
Project lifecycle writes. Never replace an LCK STOP with direct Git/GitHub commands,
an archived snapshot, guessed identity, direct Git fallback, or a broader-permission
retry of a real command failure. Candidate Refresh is the sole narrow exception for
an LCK-owned exact `--force-with-lease`; Agents never run it directly. Unknown,
stale, divergent, or ambiguous authority fails closed.

A successful branch stops at its documented result and Human boundary. This Skill
never starts Independent Review, merges, closes an Issue, performs Closeout, or
assesses Feature completion.
# Candidate Refresh

Use this branch only when the maintainer explicitly asks to refresh an existing
leaf Issue whose current non-Draft OPEN PR is already in Review and current main
has advanced after a blocker or dependency was merged.

## Run the atomic refresh

Use the one-shot refresh LCK entry point:

```bash
uv run --frozen python -m tools.lck refresh <TASK>
```

LCK fresh-resolves the Task, native blockers, Task branch, current main and the
existing PR; takes the shared Task-local operation lock; rebases the exact clean
Task head onto frozen current main; validates the rebased tree; and updates the
same remote branch with an exact old-head lease.

Proceed only on one of these terminal results:

- `ALREADY_CURRENT`: current main is already contained in the Task head; no write
  occurred and no new Review is required because this invocation changed nothing.
- `READY_FOR_FRESH_REVIEW`: the exact validated rebased head was pushed to the
  existing PR and a fresh-review-required boundary was recorded.

Any conflict, validation failure, main/PR/head drift, or lease failure is a STOP.
LCK must restore the original local head when the remote was not updated and must
not leave a rebase or Refresh session behind. Do not resolve conflicts, retry with
direct Git, run an unbounded force push, or convert the STOP into Remediation.

On success, report old head, frozen main, new head, validated tree, exact-lease
effect, validation, receipt, and limitations. Stop and ask the maintainer to start
a fresh independent Review. This branch never starts Review, merges, closes the
Issue, or performs Closeout.
# Initial Delivery

Use this branch when the maintainer asks to implement or deliver a leaf Issue and
has not explicitly requested Remediation with a failed Review ID.

## Prepare

Run the LCK entry point:

```bash
uv run --frozen python -m tools.lck delivery prepare <TASK>
```

Proceed only on `READY_FOR_DELIVERY`. Consume the returned canonical Issue profile,
Task Contract, workspace, and live facts; do not query or invent parallel mechanical
authority. LCK has prepared the profile-owned workspace and verified Project Status
`In Progress`. Any STOP is terminal for this invocation.

## Implement

Implement the smallest complete change satisfying the returned Issue body. Expand
beyond the leaf only when the context-retrieval policy supplies a concrete trigger.
For `type:task`, preserve the structured Critical Outcome and its bounded pytest
verification target. Other leaf profiles use their own typed contract and must not
receive a fabricated Critical Outcome.

Run the smallest change-relevant targeted validation while developing. Once that
feedback passes, known contract gaps are closed, and no failure or unresolved
diagnostic concern remains, inspect the complete workspace diff and proceed directly
to Delivery Complete. Do not pre-run an equivalent repository-wide suite merely for
reassurance; broaden validation only after a concrete failure/finding or an explicit
maintainer request. LCK Delivery Complete owns the authoritative Critical Outcome,
locked pytest/Ruff/format/mypy plan, and exact validated tree.

Remove or repair unrelated, generated, secret-bearing, or prohibited changes before
completion. Do not stage or commit the candidate yourself.

## Complete

Run the delivery-complete operation with concise semantic metadata:

```bash
uv run --frozen python -m tools.lck delivery complete <TASK> \
  --commit-message "<scoped commit message>" \
  --summary "<implementation summary>" \
  --risks "<risks or limitations>"
```

Do not supply branch, remote, SHA, base SHA, PR number, or refspec. LCK reacquires
live authority, validates and commits the exact candidate, synchronizes the remote,
ensures the OPEN PR, observes checks, moves Project Status to `Review`, and verifies
the final local/remote/PR head.

Only `READY_FOR_REVIEW` is success. Report the canonical Issue and PR URLs, changed
files and behavior, Critical Outcome and formal validation results, bounded LCK
effects, non-blocking checks observation, lifecycle state, and remaining risks. End
with this fresh-session prompt:

```text
请使用 task-pr-review-runner，在全新会话中独立只读审查 Task #<TASK> 的当前 OPEN PR。
```

State that Independent Review, Merge, Issue close, post-merge Closeout, branch
deletion, and Feature completion were not performed.
# Explicit Remediation

Use this branch only when the maintainer explicitly requests repair after the latest
completed LCK Independent Review failed and supplies its `review_id`. The failed
Review supplies semantic findings only; LCK reacquires all current Task, PR, branch,
base, and head authority.

## Prepare

```bash
uv run --frozen python -m tools.lck remediation prepare <TASK> \
  --review-id <FAILED_REVIEW_ID>
```

Proceed only on `READY_FOR_REMEDIATION`. Use the returned findings and current
workspace. Do not pass expected PR/head/base identity or recover from archived
mechanical evidence. If an intentionally different clone lacks the local completed
Review record, a maintainer-provided `--findings-file` may carry those completed
semantic findings; it never supplies lifecycle authority.

## Repair

Confirm each finding against the current implementation, make the smallest complete
repair, and add meaningful regression coverage. Run only change-relevant targeted
development validation. A requirement whose evidence can exist only for the new
candidate head or in a fresh provider/Independent Review remains a pending Review
acceptance item; do not fabricate it or block creation of the repaired head.

If genuine scope, architecture, or product ambiguity remains, stop at a Human Gate.
Do not stage, commit, push, replace the PR, change lifecycle state, or begin Review.

## Finish the prepared session

When inspection proves no implementation/config/docs/test change is required, close
the unchanged session through the read-only-source-repository operation:

```bash
uv run --frozen python -m tools.lck remediation no-change <TASK> \
  --review-id <FAILED_REVIEW_ID> \
  --summary "<why no implementation change is required>"
```

Proceed only on `NO_IMPLEMENTATION_CHANGE`; do not manufacture a no-op commit.

When an actual repair is targeted-ready, run the remediation-complete operation:

```bash
uv run --frozen python -m tools.lck remediation complete <TASK> \
  --review-id <FAILED_REVIEW_ID> \
  --commit-message "<scoped repair commit message>" \
  --summary "<repair summary>" \
  --risks "<risks or limitations>"
```

LCK must reuse the existing OPEN PR, run the profile gates and formal validation,
commit/push the exact repaired tree, and verify the new candidate head. It never
creates a replacement PR.

Only `READY_FOR_NEW_REVIEW` is repair success. Report repaired findings, the new
head, validation/check observations, limitations, and deferred Review-acceptance
items. Stop and ask the maintainer to start a fresh `task-pr-review-runner` session;
the earlier Review result is invalid for the new head.
