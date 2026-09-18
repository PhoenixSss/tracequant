---
name: task-delivery-runner
description: Deliver a ready leaf Issue, remediate an explicitly identified failed Review, or refresh an existing Review candidate onto current main when the maintainer explicitly requests it.
---

# Claude adapter

Follow `.agents/skills/task-delivery-runner/SKILL.md` as the canonical
procedure, including its one-branch-only supporting reference. Claude-specific
permissions do not change LCK lifecycle authority or fail-closed behavior.
This includes the explicit one-shot `refresh <TASK>` Candidate Refresh branch;
it has no prepare/complete/abort session surfaces.
