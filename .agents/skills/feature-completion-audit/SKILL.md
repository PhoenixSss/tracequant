---
name: feature-completion-audit
description: Audit completion of one specified open Feature before maintainer closeout.
---

# Feature completion audit

Use a new independent session that did not participate in direct-child splitting,
key design, implementation, fixes, review verdicts or closeout. Otherwise stop:
`本会话不能提供独立 Feature Completion Audit`.

This is strictly read-only for GitHub and the implementation. It permits ref
fetch, one isolated worktree at locked `origin/main`, validation and exact ignored
local evidence only. Never edit Issues, PRs, Project, labels, Relationships,
source/tests/docs/config, submit GitHub Reviews, commit, push, merge, delete
branches, create Tasks, perform Task closeout or assess Epic completion.
A PASS is evidence only; Feature closeout remains maintainer-only.

Follow `AGENTS.md`, `.agents/policies/command-execution.md` for execution and
`.agents/policies/context-retrieval.md` for the hierarchy-aware context exception.
Use `.agents/policies/workflow-evidence.md` for evidence/validation identity and
`docs/workflows/lck/lifecycle.md` §14 for lifecycle placement, only as needed.
The package inventory is `package.json`; hashing it does not require loading
all instructions into context.

## Select the current phase

For `请独立审计 Feature #N`, take the Feature number and expected current main SHA
from the invocation. Start at Phase 1 and advance in order, reading **only the
current phase** reference. For a phase-limited request, require the locked
main/Feature/Relationship/direct-child identities and all preceding phase outputs
bound to that snapshot. Verify those facts with current read-only evidence before
proceeding; prior reports alone are insufficient. If prerequisites are missing or
cannot be verified, report insufficient evidence and stop at the requested boundary;
do not silently load or execute other phases.

| Phase | Load when |
| --- | --- |
| [1: identify and lock](references/phase-1.md) | Establishing Feature and main identity; entry is `feature-audit-snapshot` |
| [2: direct-child inventory](references/phase-2.md) | Identity locked; enumerate necessary direct work |
| [3: acceptance coverage](references/phase-3.md) | Inventory available; map every Feature criterion |
| [4: integration and safety](references/phase-4.md) | Coverage available; inspect the integrated main result |
| [5: validation and checks](references/phase-5.md) | Semantic inspection complete; validate audited main |
| [6: gaps, stability and report](references/phase-6.md) | Evidence collected; run `feature-audit-recheck` and select one fixed verdict |

Unknown, ambiguous, stale, partial, truncated or mismatched evidence fails closed;
retain its status and inspect only named gaps. No direct Git/GitHub fallback.
Completion requires a stable locked main/Feature/child set and exactly one fixed
verdict from Phase 6. Drift requires a new independent audit. No Feature writes.
