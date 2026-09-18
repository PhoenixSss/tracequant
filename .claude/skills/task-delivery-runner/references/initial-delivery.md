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
