---
name: task-delivery-runner
description: Implement one specified leaf Issue, or remediate it when the maintainer explicitly names a failed Review ID.
---

# Leaf delivery runner

The specified Issue number is the key; its current body and canonical type label
select the business contract/profile. LCK alone resolves live workspace, branch,
PR, SHA, checks and lifecycle identity. The Agent owns semantic implementation.
Never directly stage the final candidate, commit, push, create/update PRs or mutate
Project state. Never supply cached branch/SHA/PR/refspec authority or use direct
Git/GitHub fallback after STOP. Unknown, stale or ambiguous facts fail closed.
Merge is maintainer-only; this Skill never reviews, merges, closes Issues,
performs closeout, deletes branches or assesses Feature completion.

Follow `AGENTS.md`, `.agents/policies/command-execution.md` for launcher,
execution routes/waits/failures, `.agents/policies/context-retrieval.md` for scope,
and `.agents/policies/workflow-evidence.md` for validation/evidence consumption.
Read only necessary lifecycle sections referenced by the selected branch; do not
reload policies already available in this session. `package.json` binds the full
instruction inventory without requiring every branch to be loaded into context.

## Select exactly one branch

- `实现 Issue #N`: read [Initial Delivery](references/initial-delivery.md).
  Entry: `uv run --frozen python -m tools.lck delivery prepare <TASK>`.
  Completion: `uv run --frozen python -m tools.lck delivery complete <TASK>`
  with semantic metadata, ending at `READY_FOR_REVIEW` and the Human boundary.
- Only an explicit maintainer remediation request naming the failed `review_id`:
  read [Remediation](references/remediation.md). Entry: `remediation prepare`.
  End at `READY_FOR_NEW_REVIEW` after repair or `NO_IMPLEMENTATION_CHANGE` when
  unchanged. A generic implementation request never authorizes repair after FAIL.

A deterministic STOP ends the invocation. A missing/unparseable tool result permits
at most one identical bounded retry under the command-execution policy. Semantic
scope/architecture ambiguity requires a Human Gate; never guess or force progress.
