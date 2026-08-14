---
name: feature-completion-audit
description: Independently and strictly read-only audit one maintainer-specified open GitHub Feature against a locked current main. Inventory direct children, map acceptance criteria to current-main evidence, review integration and safety, validate, recheck stability, and output one fixed verdict. Do not implement, create Tasks, edit GitHub state, close the Feature, merge, perform Task closeout, or assess Epic completion.
---

# Feature completion audit

Use this Skill for one existing open Feature before the maintainer manually
closes it or sets Project Status to `Done`.

Run in a new session that did not participate in direct-child splitting, key
design decisions, implementation, fixes, review verdicts, or closeout.
Otherwise stop with:

```text
本会话不能提供独立 Feature Completion Audit
```

A passing audit is evidence only and never authorizes Feature closeout.

## Standard invocation

```text
请使用 feature-completion-audit，独立只读审计
[Feature] <当前完整标题> #<Feature编号>。

Expected main SHA: <当前 main SHA>
```

The Feature number is the primary key; the current Issue title is canonical.
A request may limit execution to one named Phase. Verify prior Phase facts and
stop at the requested boundary.

## Context acquisition (hierarchy-aware exception)

This audit is a hierarchy-aware flow, not a leaf-Issue-first default: its
purpose requires the target Feature body, the relevant direct-child Issue
hierarchy, completion state, and implementation / validation evidence. Read
what the audit phases below require. Do not default to historical comments,
unrelated docs/ADRs, the roadmap, sibling Feature history, or general
workflow reports.

## Policies and audit interface

Read applicable `AGENTS.md` / `AGENTS.override.md` and:

```text
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
```

Audit the exact locked `origin/main` implementation and current Feature facts.
Use the repository-defined operations:

```text
feature-audit-snapshot
workflow_validation.py run --phase feature-audit --include-skill-validators --require-skill-validator
feature-audit-recheck
```

Run validation against a worktree fixed at the audited main SHA. Record the
actual Audit Skill, Evidence/Validation Runner, profile/schema, audited main,
Feature snapshot, direct-child-set digest, and content hashes used. Historical
Task/PR/delivery/review/closeout reports may locate evidence but are not
completion proof.

For `partial`, `unknown`, `fail`, truncation, schema mismatch, or drift, inspect
only the named facts or failed commands and preserve the original status.

## Permission boundary

The audit is strictly read-only for Feature, child Issues, PRs, Project, labels,
Relationships, reviews/threads, repository, branches, and code. It may fetch
refs, use one isolated worktree at the audited main SHA, run validation, and
write exact ignored local evidence artifacts.

It does not authorize Feature or child edits/closure, Task creation, Project or
label changes, body-checkbox edits, source/test/doc/config changes, commits,
pushes, merge, GitHub Review submission, branch deletion, Task closeout, or Epic
completion assessment. Gap recommendations are proposals only.

## Phase 1: identify and lock

Generate the Feature snapshot. Verify repository, open `type:feature`, canonical
title, complete body/comments/fields, Parent, blockers, dependencies,
Relationships, actual `origin/main`, and the direct-child collection.

Lock and report:

```text
Audited branch: origin/main
Audited main SHA
Feature canonical identity
Feature content / Relationship digest
Direct-child set and digest
Snapshot ID
```

Distinguish direct children from indirect descendants and related Issues. Use
the evidence-insufficient path when identity, child-set, or main facts are
unavailable or contradictory.

## Phase 2: direct-child inventory

For every direct child, record:

- Issue number, title, type, state, labels, Parent, Project Status, blockers, and
  Relationships;
- whether it is necessary to Feature completion;
- merged/closing PR, merge commit, checks, and correct linkage;
- Task closeout/lifecycle facts when applicable;
- approved no-PR/no-code exception and its current-main evidence;
- current-main implementation, tests, docs, ADR, or approved-decision evidence.

Do not infer completion from child closure counts. A closed child without merged
or current-main evidence is not automatically satisfied. Report reopened,
orphaned, blocked, or ambiguously parented work.

## Phase 3: acceptance coverage

Map every Feature acceptance criterion to one status:

```text
Satisfied
Not satisfied
Not applicable by approved decision
Insufficient evidence
```

Cite current-main source, tests, docs, ADR, configuration, runtime/public
behavior, merged PR, or an explicit approved decision. Child closure and prior
reports are insufficient by themselves.

Do not reinterpret ambiguous or contradictory criteria. Record the maintainer
clarification required.

## Phase 4: integration and safety

Review the current-main result as a whole:

- primary Feature goals and user/system value;
- cross-Task interfaces and end-to-end behavior;
- compatibility, configuration, documentation, and operations;
- dead, isolated, placeholder, or unconnected implementation;
- integrated tests, not only isolated unit tests;
- credentials, permissions, UTC/data correctness, financial safety, live-mode
  defaults, and repository-history safety where applicable;
- required work not represented by a direct child.

Inspect enough current code and evidence to establish Feature-level completion;
a full historical re-review is not required by default.

## Phase 5: validation and remote checks

Run the Feature audit Validation profile against the locked audited-main
worktree. Read actual remote-main checks and Required-Checks configuration.
Preserve no-configured, plan-limit `403`, pending, failed, stale, cancelled,
skipped, and unavailable states.

A real validation failure is a completion gap. Missing or ambiguous evidence
without a confirmed defect uses the evidence-insufficient verdict.

## Phase 6: gaps and stability

Classify each completion gap by severity and, when useful, propose the smallest
candidate Task boundary without creating or editing a Task.

Run `feature-audit-recheck`. Recollect Feature identity/content,
Relationships, direct-child set, child lifecycle evidence, audited main, and
checks. Any audited-main, material Feature, Relationship, or direct-child-set
change invalidates the stable conclusion and requires a new independent audit.

## Findings and verdicts

Use exactly Blocking, High, Medium, Low, and Nit. Cite exact Feature clauses,
child Issues, current-main files, validation, or GitHub state. Any unresolved
Blocking/High/Medium finding prevents a passing verdict.

Output exactly one:

```text
Feature 已完成，可以由维护者人工收尾
```

Only when all necessary direct work is complete, every criterion is
`Satisfied` or approved `Not applicable`, current-main integration/tests/docs
are complete, validation/checks pass, no blocker or Blocking/High/Medium finding
remains, and the audited main, Feature, Relationships, and direct-child set are
stable.

```text
Feature 尚未完成，需要补充或修复 Task
```

When a confirmed Blocking/High/Medium gap remains, necessary child work is
incomplete, a criterion is not satisfied, current main lacks required
implementation/integration/tests/docs, validation fails, or a blocker remains.

```text
证据不足，暂不能判定 Feature 完成
```

When no confirmed defect can be concluded but criteria, children, GitHub facts,
current-main evidence, stability, or a maintainer decision are insufficient or
contradictory.

## Report and closeout gate

On a clean path, report Feature identity/URL/Parent, audited main SHA, actual
Skill/Runner identity, direct-child summary, acceptance matrix, integration and
safety summary, findings, validation/checks, blockers and state conflicts,
gap-to-Task recommendations, limitations, actions not performed, one fixed
verdict, and:

```text
Audited main SHA: <actual SHA>
```

Use a detailed report for gaps, failed/pending evidence, drift, fallback,
conflict, or maintainer decision.

After a passing audit, the maintainer must re-verify current `origin/main`,
Feature title/body, direct-child set, blockers, and checks immediately before
manual closeout. This Skill performs none of those writes and never assesses
Epic completion.

Remove any temporary worktree by exact path without destructive broad cleanup.
Re-run in a new independent session after any new merged Task, clarified
Feature, resolved blocker, repaired validation, main change, child-set change,
or reopened Feature. Never inherit an earlier verdict.
