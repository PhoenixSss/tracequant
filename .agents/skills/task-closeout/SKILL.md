---
name: task-closeout
description: Close out a specified leaf Issue after the maintainer reports its PR manually Squash Merged.
---

# Task closeout

The maintainer statement authorizes verification, not proof of merge. This Skill
never merges, manually closes Issues, repairs code, cleans unrelated branches or
assesses Feature completion. LCK alone owns lifecycle writes and live-state recovery;
unknown, ambiguous, stale or conflicting identity fails closed. No direct Git/GitHub
fallback or old PR/branch/SHA/snapshot as authority.

Follow `AGENTS.md`, `.agents/policies/command-execution.md` for launcher/routes/
failures, `.agents/policies/context-retrieval.md` for anomaly-driven context and
`.agents/policies/workflow-evidence.md` for compact result consumption. Consult
`docs/workflows/lck/lifecycle.md` §§11,13 only for a lifecycle question; do not
reload policies already available. `package.json` binds instruction identity.

## Close out and report

Use the exact Issue number from the request; LCK resolves the current PR:

```bash
uv run --frozen python -m tools.lck closeout <TASK>
```

The prior `merge preflight` belongs to the separate pre-merge Review flow, not a
second Closeout workflow. This entry is only for a manually merged PR.

Report the final compact `lck-agent-view`:

```text
Business Delivery: COMPLETE | NOT_COMPLETE
Cleanup: COMPLETE | PENDING
```

When Business Delivery and Cleanup are `COMPLETE` and `next_action` says stop,
report Task, bounded effects and limitations, then stop. `receipt_reference` is
an on-demand audit pointer, never a default read instruction.

For STOP, Cleanup `PENDING`, partial/unknown state, an effect anomaly, insufficient
Agent View or an explicit maintainer audit request, expand only the needed Receipt
evidence. Preserve partial/unknown limitations. A verified merge can mean Business
Delivery `COMPLETE` while cleanup is `PENDING`; recover only through the same LCK
closeout command with fresh facts. A remote branch already deleted by GitHub is
normal. If a unique safe action cannot be proved, stop and surface the conflict.

LCK may fast-forward main, converge metadata only with authoritative Issue closure,
and remove only the proved exact Task refs. The Receipt remains audit evidence,
not lifecycle authority. State that no merge, manual Issue close, repair commit,
unrelated cleanup or Feature completion occurred.
