---
name: task-closeout
description: Close out one maintainer-specified Task after maintainer manual Squash Merge through LCK live-state recovery. Never merge, manually close an Issue, repair code, clean unrelated branches, or assess Feature completion.
---

# Task closeout

Use this Skill only after the maintainer states that a specific PR was
manually Squash Merged and requests closeout for its exact Task. The statement
authorizes verification; it is not proof of merge. This Skill never merges.

## Standard invocation

~~~text
PR #<PR编号> 已由我人工 Squash Merge。

请使用 task-closeout，完成
[Task] <当前完整标题> #<Task编号> 的合并后核验与分支清理。
~~~

Task and PR numbers are primary keys; the current Issue title is canonical.
The LCK resolver reacquires the PR from current Git/GitHub facts. Do not pass
an old PR, branch, SHA, or snapshot as lifecycle authority.

## Default context

It does not default to re-reading the complete business Issue hierarchy
(Parent/Epic bodies), business comments, or full implementation context. Read
additional business context only when an explicit anomaly trigger requires it,
such as an unresolved linkage, closure, or merge-identity conflict.

## Policies and lifecycle owner

Read the applicable AGENTS.md,
.agents/policies/command-execution.md, and
.agents/policies/workflow-evidence.md. Shared semantics are owned by
docs/development/issue-workflow.md (§11 and §13).

The lifecycle entry point is:

~~~bash
uv run --frozen python tools/agent_workflow/lck.py closeout <TASK>
~~~

Before the maintainer merge, the deterministic merge gate is:

~~~bash
uv run --frozen python tools/agent_workflow/lck.py merge preflight <TASK>
~~~

Merge Preflight verifies the unique current PR, head/base, required checks,
current Review PASS, blockers, and mergeability. It returns
READY_FOR_HUMAN_MERGE; it has no automatic merge path. The maintainer must
perform the manual Squash Merge.

LCK is the only Task lifecycle control authority. The historical Evidence
snapshot and closeout interfaces are audit material only; they are not a
substitute for LCK live-state resolution, phase eligibility, or write
authority. The historical eligible-under-capability-limited-policy state
remains a reported limitation, not permission to broaden cleanup.

## LCK closeout contract

LCK resolves the current state on every invocation and fail-closes on
ambiguous identity, multiple merged PRs, open PR conflicts, remote divergence,
unknown merge identity, or unsafe worktree ownership. It does not require a
previous Kernel process or authoritative snapshot lineage.

The result separates:

~~~text
Business Delivery: COMPLETE | NOT_COMPLETE
Cleanup: COMPLETE | PENDING
~~~

A verified merged PR makes Business Delivery COMPLETE. Cleanup may remain
PENDING after an interrupted or failed idempotent effect and can be retried
with the same LCK command. A GitHub-deleted remote branch is recognized as a
normal post-merge state.

The bounded effects may synchronize main by fast-forward, converge Project
Status and lifecycle labels only when authoritative Issue closure is present,
and clean only the verified Task branch after head/tree/worktree proof. LCK
never manually closes the Issue, uses force push, resets, deletes unrelated
branches, or assesses Feature completion.

### Result-consumption boundary

The final compact `lck-agent-view` is the normal reporting authority for the
Closeout invocation. `receipt_reference` is a pointer, not an instruction to
read. When the Agent View reports terminal success with Business Delivery
`COMPLETE`, Cleanup `COMPLETE`, and `next_action` directing the Agent to stop,
report from that compact result and stop. Do not follow `receipt_reference` or
open the successful Audit Receipt merely to restate merge identity, exact
branch/worktree actions, or make the report more complete.

Expand the Audit Receipt only when the current result has a concrete need for
diagnosis or audit: STOP, Cleanup `PENDING`, partial/unknown state, an effect
anomaly, an Agent View that is insufficient to determine the current outcome,
or an explicit maintainer request for audit details such as merge identity or
exact cleanup proof. Read only the evidence needed for that question; the
Receipt remains audit evidence and is not a second lifecycle result.

## Recovery and reporting

The same closeout command is the cleanup-only recovery entry when only the
exact verified Task refs remain. Reacquire live facts; do not reconstruct state
from an old report. Stop and surface facts when the unique safe action cannot
be proved.

For a normal terminal success, report the Task, Business Delivery, Cleanup,
the bounded effect summary and limitations already present in the Agent View,
plus the actions not performed. Exact merge/branch/worktree audit details are
not required in the normal success report; provide them only when an allowed
Audit Receipt trigger above applies. Explicitly state that no merge, manual
Issue close, repair commit, unrelated cleanup, or Feature completion occurred.
