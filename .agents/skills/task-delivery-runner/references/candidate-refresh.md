# Candidate Refresh

Use this branch only when the maintainer explicitly asks to refresh an existing
leaf Issue whose current non-Draft OPEN PR is already in Review and current main
has advanced after a blocker or dependency was merged.

## Run the atomic refresh

Use the elevated-first LCK entry point:

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
