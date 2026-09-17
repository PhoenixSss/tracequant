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

The routed LCK entry points are `delivery prepare` / `delivery complete`,
`remediation prepare` / `remediation no-change` / `remediation complete`, and
`refresh prepare` / `refresh complete` / `refresh abort`.
Their exact commands in the selected reference use the stable
`uv run --frozen python -m tools.lck` front door.

## Shared guardrails

Read applicable `AGENTS.md`. Use these canonical owners only when their decisions
apply to the selected branch:

- `.agents/policies/command-execution.md` for execution route, launcher preflight,
  failure classification, and 30-second waits;
- `.agents/policies/context-retrieval.md` for scoped context acquisition;
- `.agents/policies/workflow-evidence.md` for validation and evidence consumption;
- `docs/workflows/lck/lifecycle.md` and, for Remediation,
  `docs/workflows/lck/review-and-remediation.md` for shared lifecycle semantics.

Before the first LCK command, verify `command -v uv`, `uv --version`, and
`uv run --frozen python --version`. A launcher failure is an environment failure,
not a lifecycle verdict.

LCK alone owns branch selection, staging, commit, push, PR identity/effects, and
Project lifecycle writes. Never replace an LCK STOP with direct Git/GitHub commands,
an archived snapshot, guessed identity, force push, or a broader-permission retry of
a real command failure. Unknown, stale, divergent, or ambiguous authority fails
closed.

A successful branch stops at its documented result and Human boundary. This Skill
never starts Independent Review, merges, closes an Issue, performs Closeout, or
assesses Feature completion.
