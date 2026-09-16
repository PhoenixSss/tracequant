---
name: task-pr-review-runner
description: Independently review the current open PR for a maintainer-specified leaf Issue in a fresh session.
---

# Task PR review runner

Use this Skill only in a fresh session that did not implement or remediate the head
being reviewed. The Task number is the only mechanical key supplied to LCK; a PR
number mentioned by the maintainer is intent, not authority.

Read applicable `AGENTS.md` and `.agents/policies/command-execution.md`. Read only
the Review portions needed from `.agents/policies/workflow-evidence.md`,
`docs/workflows/lck/review-and-remediation.md`, and
`docs/workflows/lck/lifecycle.md`. Those owners define execution routes, 30-second
waits, validation/evidence consumption, freshness, and shared lifecycle semantics.

Before the first LCK command, verify `command -v uv`, `uv --version`, and
`uv run --frozen python --version`. A launcher failure is not a Review verdict.

## Prepare the exact review target

```bash
uv run --frozen python -m tools.lck review prepare <TASK>
```

Proceed only on `READY_FOR_SEMANTIC_REVIEW`. Use the returned `review_id`, current
Task Contract, locked target, effective diff, checks/formal-validation evidence,
`structured_review_instructions`, and sealed `review_root`. Do not replace these
facts with Delivery handoffs, archived evidence, expected SHAs, or direct GitHub
selection.

Review Prepare has already run formal validation for the exact head. Consume that
evidence; do not rerun pytest, Ruff, mypy, lock checks, Skill validators, or an
equivalent suite in the sealed clone. The source repository and the standalone
review clone are implementation-read-only except for the ignored operation evidence
owned by LCK.

## Inspect, reason, judge, and report

Follow all applicable `structured_review_instructions`. Read the complete effective
diff, test source, and necessary related code. Map every acceptance criterion to
evidence and inspect correctness, failure behavior, integration, public/config/docs
effects, test semantics, and applicable security or workflow boundaries. Expand to
comments, hierarchy, or history only for an explicit reference or concrete ambiguity.

Continue the full review after finding a blocker and perform the required residual
sweep. Findings use Blocking, High, Medium, Low, and Nit; any unresolved
Blocking/High/Medium defect or unmet requirement produces FAIL. If LCK evidence is
insufficient, report the specific validation/evidence gap instead of manufacturing
substitute authority. Provider or fresh-review-only proof is a Review-acceptance
evidence gap, not a retroactive Delivery requirement.

Independent Review never modifies implementation. It does not submit a GitHub
Review, repair code, write lifecycle state, merge, close the Issue, start Remediation,
perform Closeout, or assess Feature completion.

## Complete against fresh live state

For PASS:

```bash
uv run --frozen python -m tools.lck review complete <TASK> \
  --review-id <REVIEW_ID> \
  --verdict PASS
```

For FAIL, write complete blocking findings to an ignored or temporary file outside
the sealed clone, then run:

```bash
uv run --frozen python -m tools.lck review complete <TASK> \
  --review-id <REVIEW_ID> \
  --verdict FAIL \
  --findings-file <FINDINGS_FILE>
```

Review Complete reacquires live applicability. `REVIEW_STALE_HEAD`,
`REVIEW_STALE_BASE`, `REVIEW_STALE_TASK`, or `REVIEW_STALE_DIFF` rejects the verdict;
report the stale result and require a new fresh Review. A valid FAIL returns
`STOP_REQUIRED`; report `不通过，需要修复`, all findings, and the failed `review_id`,
then stop without starting Remediation.

Only after a valid PASS returns `READY_FOR_MERGE_PREFLIGHT`, run:

```bash
uv run --frozen python -m tools.lck merge preflight <TASK>
```

This fresh read-only gate verifies the accepted Review against the current PR,
head/base, required checks, blockers, and mergeability. Only
`READY_FOR_HUMAN_MERGE` permits the report `通过，可以人工合并`. Include reviewed
identity/diff, acceptance coverage, validation/check state, applicability, preflight,
findings, and limitations, then stop at the maintainer manual Squash Merge boundary.
