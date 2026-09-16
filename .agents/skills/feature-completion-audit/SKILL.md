---
name: feature-completion-audit
description: Independently audit whether a maintainer-specified open Feature is complete on current main before manual Feature closeout.
---

# Feature completion audit

Use this Skill in a new session for one existing open Feature. The session must not
have participated in its child splitting, key design decisions, implementation,
fixes, review verdicts, or closeout. Otherwise stop with:

```text
本会话不能提供独立 Feature Completion Audit
```

This is one continuous read-only audit. A request may stop after a named phase, but
must verify the preceding phase facts. A passing verdict is evidence for the
maintainer; it never authorizes Feature closeout.

The Feature number and maintainer-supplied expected current-main SHA identify the
request; the current GitHub title is canonical.

## Authority and interface

Read applicable `AGENTS.md` and the relevant sections of
`.agents/policies/command-execution.md`, `.agents/policies/workflow-evidence.md`, and
`docs/workflows/lck/lifecycle.md` §14. This hierarchy-aware audit may read the target
Feature, its direct children, and the current-main evidence required below. Do not
load unrelated history, roadmap material, sibling Features, or broad docs by default.

Audit the locked `origin/main` and current GitHub Feature facts with:

```bash
uv run --frozen python -m tools.lck.feature_audit feature-audit-snapshot \
  --feature <FEATURE> --expected-main-sha <SHA>
uv run --frozen python -m tools.lck.validation_runner run \
  --phase feature-audit --include-skill-validators --require-skill-validator
uv run --frozen python -m tools.lck.feature_audit feature-audit-recheck \
  --snapshot-id <SNAPSHOT_ID>
```

Run validation in the isolated worktree fixed at the audited main SHA. Preserve
`partial`, `unknown`, `fail`, truncation, schema mismatch, and drift; inspect only the
named evidence gap or failed command. Historical child reports may locate evidence
but do not prove completion.

The audit is strictly read-only for repository content and all GitHub state. It may
fetch refs, use the one isolated audited-main worktree, run validation, and write
bounded ignored evidence. It never implements or fixes code, creates Tasks, edits or
closes Issues, changes Project/labels/relationships, submits reviews, commits,
pushes, merges, deletes branches, performs Task closeout, or assesses Epic completion.

## Audit phases

### 1. Identify and lock

Generate the snapshot and verify the open `type:feature`, canonical identity and
body, Parent, blockers/dependencies/relationships, actual `origin/main`, and direct
children. Lock the audited main SHA, Feature content/relationship digest,
direct-child set/digest, and snapshot ID. Fail closed when identity or lock facts are
unavailable or contradictory.

### 2. Inventory direct children

For each direct child, record identity/type/state, Parent, lifecycle status,
blockers/relationships, necessity, merged or approved no-code outcome, and its
current-main implementation/test/docs/decision evidence. Do not infer completion
from closure counts; flag reopened, orphaned, blocked, or ambiguously parented work.

### 3. Map acceptance coverage

Map every Feature criterion to exactly one of `Satisfied`, `Not satisfied`,
`Not applicable by approved decision`, or `Insufficient evidence`, citing current-main
or explicit approved-decision evidence. Do not reinterpret ambiguity; name the
maintainer clarification required.

### 4. Review integration and safety

Judge the current-main result end to end: goals, cross-child interfaces,
compatibility/config/docs/operations, dead or unconnected work, integrated tests,
and applicable credential, permission, time/data, financial, live-default, or
repository-history risks. Identify required work missing from the child hierarchy.

### 5. Validate and check remote state

Run the Feature validation command against the locked worktree and inspect actual
remote-main checks and required-check configuration. A real failure is a completion
gap. Missing or ambiguous evidence without a confirmed defect remains insufficient.

### 6. Classify gaps and recheck stability

Classify findings as Blocking, High, Medium, Low, or Nit. When useful, propose the
smallest new Task boundary without creating it. Run the recheck command; any audited
main, material Feature, relationship, or direct-child-set change invalidates the
conclusion and requires a new independent audit.

## Fixed verdict and report

Any unresolved Blocking/High/Medium finding prevents a passing verdict. Output
exactly one:

```text
Feature 已完成，可以由维护者人工收尾
```

Use only when all necessary children and criteria are complete, current-main
integration/tests/docs and validation/checks pass, no blocker remains, and the
Feature/relationships/child set/main are stable.

```text
Feature 尚未完成，需要补充或修复 Task
```

Use for a confirmed material gap, incomplete necessary child or criterion, missing
required current-main work, validation failure, or blocker.

```text
证据不足，暂不能判定 Feature 完成
```

Use when no confirmed defect can be concluded but required facts, evidence,
stability, or a maintainer decision are insufficient or contradictory.

Report Feature identity/URL/Parent, audited main SHA, actual Skill/Runner identity,
child summary, acceptance matrix, integration/safety, findings, validation/checks,
blockers/conflicts, proposed Task boundaries, limitations, actions not performed,
and the fixed verdict. Include `Audited main SHA: <actual SHA>`. After any new merge,
Feature clarification, blocker resolution, validation repair, main/child-set change,
or reopen, discard the old verdict and rerun in a new independent session. Remove
the temporary worktree only by its exact path.
