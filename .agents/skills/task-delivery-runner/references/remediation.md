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

When an actual repair is targeted-ready, run the elevated-first operation:

```bash
uv run --frozen python -m tools.lck remediation complete <TASK> \
  --review-id <FAILED_REVIEW_ID> \
  --commit-message "<scoped repair commit message>" \
  --summary "<repair summary>" \
  --risks "<risks or limitations>"
```

Follow the command-execution policy's fixed 30-second wait. LCK must reuse the
existing OPEN PR, run the profile gates and formal validation, commit/push the
exact repaired tree, and verify the new candidate head. It never creates a
replacement PR.

Only `READY_FOR_NEW_REVIEW` is repair success. Report repaired findings, the new
head, validation/check observations, limitations, and deferred Review-acceptance
items. Stop and ask the maintainer to start a fresh `task-pr-review-runner` session;
the earlier Review result is invalid for the new head.
