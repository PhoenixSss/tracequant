# Candidate Refresh

Use this branch only when the maintainer explicitly asks LCK to integrate current
`main` into the existing OPEN PR for a leaf Issue already in Review. This is base
integration, not Initial Delivery, semantic Remediation, Review, or merge authority.

## Prepare

Run the elevated-first LCK entry point:

```bash
uv run --frozen python -m tools.lck refresh prepare <TASK>
```

- `ALREADY_CURRENT` is terminal success with no Refresh session or commit. Stop.
- `READY_FOR_REFRESH_COMPLETE` owns a conflict-free, uncommitted merge candidate.
- `REFRESH_CONFLICTS` owns the reported conflict inventory. Resolve only those
  integration conflicts and necessary compatibility adjustments. Unknown paths,
  unexplained semantics, or scope expansion require a Human Gate.
- Any STOP is terminal for this invocation. Do not use direct Git/GitHub commands
  as fallback.

Inspect the complete candidate and run the smallest change-relevant targeted
feedback. Do not commit, push, rebase, force push, update the branch through
GitHub, create a PR, or start Review.

## Complete

When the candidate is resolved, targeted-ready, and contains no untracked input:

```bash
uv run --frozen python -m tools.lck refresh complete <TASK> \
  --commit-message "<merge commit message>" \
  --summary "<integration summary>" \
  --risks "<risks or limitations>"
```

LCK fresh-resolves the Task/PR/head/base, validates the exact integrated tree,
creates the ordinary two-parent merge commit, fast-forwards the same remote Task
branch, reuses the same PR, and establishes the fresh-review-required boundary.
Only `READY_FOR_FRESH_REVIEW` is success.

Report the Issue and existing PR, frozen main and old/new head, exact merge
parents and validated tree, validation/check results, effects, and remaining
risks. End with:

```text
请使用 task-pr-review-runner，在全新会话中独立只读审查 Task #<TASK> 的当前 OPEN PR。
```

State that Review, Merge, Issue close, Closeout, and Feature completion were not
performed.

## Abort

If the maintainer explicitly chooses to discard an uncommitted prepared Refresh:

```bash
uv run --frozen python -m tools.lck refresh abort <TASK>
```

Only `REFRESH_ABORTED` is success. Abort must stop if a commit exists, identity
drifted, or non-owned/untracked input would be discarded.
