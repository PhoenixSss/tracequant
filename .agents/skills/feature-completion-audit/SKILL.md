---
name: feature-completion-audit
description: Independently and strictly read-only audit one maintainer-specified open GitHub Feature against a locked current main before maintainer closeout. Inventory direct children, map acceptance criteria to current-main evidence, review integration and safety, validate, recheck stability, and output one fixed verdict. Do not implement, create Tasks, edit GitHub state, close the Feature, merge, perform Task closeout, or assess Epic completion.
---

# Feature completion audit

Use this Skill only for one existing open Feature before the maintainer manually
closes it or sets Project Status to `Done`.

Run in a new session that did not participate in the Feature's direct-child
splitting, key design decisions, implementation, fixes, review verdicts, or
closeout. Otherwise stop with:

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

The Feature number is the primary key; current Issue title is canonical.

## Rules, trust, and tools

Read applicable `AGENTS.md` / `AGENTS.override.md` and trusted versions of:

```text
.agents/skills/feature-completion-audit/SKILL.md
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
.agents/policies/task-workflow-telemetry.md
```

Use current Feature/child/PR facts and locked `origin/main` implementation,
workflows, `pyproject.toml`, lock files, documentation, ADRs, and tests. Run
current checks through `tools/agent_workflow/workflow_validation.py` from the
trusted audited-main control plane.
Historical Task/PR/delivery/review/closeout reports may locate evidence but are
not completion proof.

Use trusted-control-plane operations:

```text
feature-audit-snapshot
workflow_validation.py run --phase feature-audit --include-skill-validators --require-skill-validator
feature-audit-recheck
```

If audited `main` changes governance or tooling relative to the current working
context, run Evidence and Validation from the locked audited-main commit through
`trusted_runner.py` or a detached trusted worktree. Record audited main, runner
source SHA/content digest, and child-set/snapshot fingerprints.

## Permission boundary

This audit is strictly read-only for Feature, Task, Issue, PR, Project, label,
Relationship, review/thread, repository, branch, and code state. It may fetch
refs, create and remove one exact detached temporary audit worktree, run
validation, and write exact ignored local evidence/validation/telemetry files.

It never:

- edits or closes the Feature or a child Issue;
- creates, edits, reopens, or closes a Task;
- changes Project, labels, Parent, Relationships, comments, reviews, or threads;
- checks boxes in Issue bodies;
- modifies source, tests, docs, configuration, or governance;
- commits, pushes, merges, submits a GitHub Review, or deletes business branches;
- performs Task closeout or Epic completion assessment.

Gap recommendations are non-writing proposals only.

## Phase 1: identify and lock the audit baseline

Generate a current Feature snapshot and verify:

- repository and Feature exist;
- Feature is open and `type:feature`;
- canonical title matches supplied title;
- complete Feature body/comments, fields, Parent, blockers, dependencies, and
  Relationships were read;
- actual `origin/main` matches expected main SHA when supplied;
- direct sub-issue collection is available and can be distinguished from
  indirect descendants or merely related Issues.

Lock:

```text
Audited branch: origin/main
Audited main SHA: <exact SHA>
Feature canonical identity
Direct-child set and digest
Feature content / Relationship facts
```

A ProjectV2 derived title is not canonical. If required identity, child, or main
facts are unavailable or contradictory, use the evidence-insufficient path.

## Phase 2: direct-child inventory

For every direct child, independently classify and record:

- Issue number, canonical title, type, state, labels, Parent, Project Status,
  blockers, and Relationships;
- whether it is necessary to Feature completion;
- closing or merged PR, merge commit, checks, and correct linkage;
- Task closeout/lifecycle facts when applicable;
- approved no-PR/no-code exception and its current-main evidence, if any;
- current-main implementation, tests, docs, ADR, or approved-decision evidence.

Do not infer completion from `n / n closed`. A closed child without merged/current
main evidence is not automatically satisfied. A related or indirect Issue is not
a direct child. Reopened, orphaned, blocked, or ambiguously parented work must be
reported.

The snapshot may normalize inventory metadata, but the Agent must inspect the
relevant child Issue/PR and current-main evidence needed for semantic judgment.

## Phase 3: acceptance coverage

Parse every Feature acceptance criterion and create a coverage matrix with one
of:

```text
Satisfied
Not satisfied
Not applicable by approved decision
Insufficient evidence
```

For each criterion cite current-main source, tests, docs, ADR, configuration,
runtime/public behavior, merged PR, or explicit approved decision. Child closure
or a prior report alone is not sufficient.

Do not silently reinterpret ambiguous or contradictory criteria. Use
`Insufficient evidence` and identify the maintainer clarification needed.

## Phase 4: Feature-level integration and safety

Review the current-main result as a whole, not only historical PRs:

- primary Feature goals and user/system value;
- cross-Task interfaces and end-to-end behavior;
- compatibility, configuration, documentation, and operations;
- dead, isolated, placeholder, or unconnected implementation;
- tests that cover integrated behavior rather than only isolated units;
- credentials, permissions, UTC/data correctness, financial safety, live-mode
  defaults, and repository-history safety where applicable;
- missing work not represented by a direct child.

Do not re-review every historical line by default, but inspect enough current
code and evidence to establish Feature-level completion.

## Phase 5: validation and remote checks

Run the shared Validation runner against the locked audited-main worktree. Run
all current applicable checks and Skill validators. Read actual remote-main check
runs and Required-Checks configuration with the same explicit handling of no
configured checks, plan-limit `403`, pending, failed, stale, cancelled, skipped,
and unavailable states.

A real validation failure is a completion gap. Missing or ambiguous evidence
without a confirmed defect uses the evidence-insufficient verdict.

## Phase 6: gaps and stability recheck

Classify each completion gap by severity and propose the smallest candidate
Task boundary when useful. Do not create or edit that Task.

Run `feature-audit-recheck` from the same trusted audited-main control plane.
Recollect Feature identity/content facts, Relationships, direct-child set,
child lifecycle evidence, audited main, and current checks.

Any audited-main, material Feature, Relationship, or direct-child-set change
invalidates the stable completion conclusion. Re-audit the new object in a new
session.

## Severity

Use exactly:

- **Blocking**: Feature cannot safely be declared complete; credentials, funds,
  core data, permissions, or repository history are at risk;
- **High**: primary goal, critical integration, major acceptance criterion, or
  core safety property is incomplete;
- **Medium**: clear completion, test, documentation, compatibility, or lifecycle
  gap prevents closeout;
- **Low**: non-blocking maintainability, clarity, or residual risk;
- **Nit**: wording, formatting, or tiny consistency issue.

Order findings by severity and cite exact Feature clauses, child Issues,
current-main files, validation, or GitHub state. Any unresolved
Blocking/High/Medium finding prevents a passing verdict.

## Fixed verdicts

Output exactly one:

```text
Feature 已完成，可以由维护者人工收尾
```

Only when all necessary direct work is complete, every criterion is `Satisfied`
or approved `Not applicable`, current-main integration/tests/docs are complete,
validation/checks pass, no blocker or Blocking/High/Medium finding remains, and
the audited main/Feature/Relationships/direct-child set stayed stable.

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

## Report contract

On a clean path, use a compact report containing:

```text
Feature canonical identity / URL / Parent
Audited branch and main SHA
Trusted runner source
Direct-child inventory summary
Acceptance coverage matrix
Feature integration / safety summary
Findings by severity
Local validation and remote checks
Blockers, orphaned/reopened work, and state conflicts
Gap-to-Task recommendations
Limitations and actions not performed
One fixed verdict
Audited main SHA: <actual SHA>
```

The direct-child inventory, acceptance matrix, findings, validation, verdict,
and SHA may be concise but cannot be omitted. Use a detailed report for gaps,
failed/pending evidence, drift, fallback, conflict, or maintainer decision.

## Maintainer closeout gate

After a passing audit, the maintainer must re-verify current `origin/main`,
Feature title/body, direct-child set, blockers, and checks immediately before
manually closing the Feature or setting Project `Done`. This Skill performs none
of those writes and never assesses Epic completion.

## Temporary worktree, recovery, and telemetry

A temporary audit worktree must be unique, detached at the locked main SHA,
read-only for audited files, and removed by exact path without `git clean`.

Re-run in a new independent session after any new merged Task, clarified Feature,
resolved blocker, repaired validation, changed main, child-set change, or
reopened Feature. Never inherit an old verdict.

If a maintainer-started Task workflow run explicitly includes Feature audit,
perform one lightweight status check and append one aggregate
`feature-completion-audit` summary using facts already produced. Record Evidence
and Validation operations, report size, fallbacks, retries, findings, and drift
when known. Telemetry is not Feature evidence and never affects verdict.
