---
name: task-pr-review-runner
description: Independently review the current PR for a specified leaf Issue in a fresh session.
---

# Independent PR Review

Use a fresh session that did not implement or remediate the reviewed head.
Independent Review never modifies implementation, submits a GitHub Review,
merges, closes Issues, performs Closeout, starts remediation or assesses Feature
completion. Only Inspect / Reason / Judge / Report is authorized.

Follow `AGENTS.md`, `.agents/policies/command-execution.md` for launcher/routes/
waits/failures, `.agents/policies/context-retrieval.md` for context and
`.agents/policies/workflow-evidence.md` for evidence consumption. Shared Review
semantics belong to `docs/workflows/lck/review-and-remediation.md` (§§3–6 as needed).
Do not reload already available policies. `package.json` binds instruction identity.

## Prepare and inspect

Resolve the leaf number from maintainer intent; it is the only target key passed
to LCK. Delivery handoffs and historical PR/base/head/checks are not authority.

```bash
uv run --frozen python -m tools.lck review prepare <TASK> \
  --skill-path <CALLER_SKILL_PATH>
```

Proceed only on `READY_FOR_SEMANTIC_REVIEW`. Use the returned `review_id`, Task
Contract, current target, validation/check evidence, `review_root` and canonical
`structured_review_instructions`. The standalone clone is sealed and
implementation-read-only; source tracked files and Git metadata are read-only.
Only operation-owned ignored LCK evidence may be written outside the clone.
No direct Git/GitHub fallback for STOP, stale, unknown or ambiguous facts.

Inspect the complete effective diff and necessary related code; build an
independent AC coverage matrix. Complete every applicable Structured Review v2
obligation and the adversarial residual sweep even after finding a blocker.
Judge test source/coverage, correctness, failure behavior, docs/config/interfaces
and applicable workflow/security boundaries. Do not execute tests, static checks,
Skill validators or another formal suite in the sealed `review_root`: Prepare
already persisted authoritative validation. Report specific evidence gaps instead.
Delivery conclusions, old verdicts and remediation rationale are not evidence.

Use Blocking, High, Medium, Low, Nit. Blocking/High/Medium defects or unmet Task
requirements produce FAIL. Separate future-head/provider evidence as a
**Review-acceptance evidence gap**; never demand fabricated receipts or make it a
prerequisite for the earlier Delivery/Remediation that creates the reviewed head.

## Complete and report

For PASS:

```bash
uv run --frozen python -m tools.lck review complete <TASK> \
  --review-id <REVIEW_ID> --verdict PASS
```

For FAIL, write complete findings to ignored or temporary storage outside the
sealed clone, then:

```bash
uv run --frozen python -m tools.lck review complete <TASK> \
  --review-id <REVIEW_ID> --verdict FAIL --findings-file <FINDINGS_FILE>
```

LCK checks fresh applicability. `REVIEW_STALE_HEAD`, `REVIEW_STALE_BASE`,
`REVIEW_STALE_TASK` or `REVIEW_STALE_DIFF` means the verdict was not accepted for
the current target; report the need for fresh Review Prepare. Do not reuse it.
FAIL returns `STOP_REQUIRED`: report `不通过，需要修复`, findings/evidence and the
failed `review_id`. Stop; no merge preflight, automatic Delivery prompt or remediation.
PASS also requires current checks to pass; FAIL only observes checks.

Only `READY_FOR_MERGE_PREFLIGHT` permits:

```bash
uv run --frozen python -m tools.lck merge preflight <TASK>
```

Only `READY_FOR_HUMAN_MERGE` permits reporting `通过，可以人工合并`. Include reviewed
head/base/diff, fresh applicability, preflight, AC coverage, validation/checks and
limitations. Stop for maintainer manual Squash Merge; the Agent never merges.
