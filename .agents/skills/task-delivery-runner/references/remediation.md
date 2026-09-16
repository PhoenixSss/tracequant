# Explicit Remediation

Use only the maintainer-named failed `review_id`. Consult
`docs/workflows/lck/review-and-remediation.md` §§8–9 for shared semantics when
needed; LCK reacquires mechanical identity independently of the findings.

```bash
uv run --frozen python -m tools.lck remediation prepare <TASK> \
  --review-id <FAILED_REVIEW_ID>
```

Proceed only on `READY_FOR_REMEDIATION`. The workspace-local failed Review record
supplies semantic findings. Only when the maintainer intentionally changes clone
or runtime and that record is unavailable may the completed findings be supplied
with `--findings-file <COMPLETED_REVIEW_FINDINGS>`. Never synthesize findings to gain
admission or accept old PR/head/base/checks as write authority.

Confirm findings against the current implementation, make the smallest complete
repair and add regression coverage. Classify each requirement at its evidence
boundary: repairable code/config/docs/test defects must be fixed now; evidence
that can only exist after the repaired head or in a separate provider/fresh Review
remains a deferred **Review-acceptance evidence gap**. It is unsatisfied but is not
a Human Gate or prerequisite for completion. Never fabricate future evidence.
Genuine scope/architecture/product ambiguity still requires a Human Gate.

Use targeted development feedback and inspect the complete candidate diff before
completion, under `.agents/policies/workflow-evidence.md`. Do not run precautionary
full validation before LCK.

If no repair is required, do not manufacture a commit. Close the prepared session:

```bash
uv run --frozen python -m tools.lck remediation no-change <TASK> \
  --review-id <FAILED_REVIEW_ID> \
  --summary "<why no implementation change is required>"
```

`NO_IMPLEMENTATION_CHANGE` leaves the head unchanged and releases the prepared
session, without claiming deferred acceptance. Never manually delete session state.
Otherwise complete the actual repair:

```bash
uv run --frozen python -m tools.lck remediation complete <TASK> \
  --review-id <FAILED_REVIEW_ID> \
  --skill-path <CALLER_SKILL_PATH> \
  --commit-message "<scoped repair commit message>" \
  --summary "<repair summary>" \
  --risks "<risks or limitations>"
```

LCK validates and commits the repair, reuses the existing OPEN PR and verifies
local/remote/PR head alignment; it never creates a replacement PR. On
`READY_FOR_NEW_REVIEW`, report repaired findings, new head, validation/checks,
limitations and deferred acceptance items. The new head invalidates the old
semantic verdict. Stop and give the fresh-session Review prompt from Initial
Delivery reporting (without loading that branch):

```text
请使用 task-pr-review-runner，在全新独立会话中只读审查 Task #<TASK> 的当前 OPEN PR。
```

Never automatically start Review. Independent Review, Merge, Issue close,
post-merge closeout, branch deletion and Feature completion were not performed.
