---
name: feature-completion-audit
description: Independently and strictly read-only audit one maintainer-specified open GitHub Feature against the current main branch before maintainer closeout. Verify Feature identity, direct child Issues, merged PR and Task closeout evidence, acceptance criteria, current-main integration, validation, gaps, and audit stability, then output one fixed verdict. Do not use to implement or create Tasks, edit GitHub state, close the Feature, merge, submit reviews, perform Task closeout, or assess Epic completion.
---

# Feature completion audit

Use this Skill only for an independent completion audit of one existing,
maintainer-specified GitHub Feature before the maintainer manually closes it or
sets its Project Status to `Done`.

Run it in a new Codex session that did not participate in the Feature's direct
child work through requirements splitting, key design decisions, implementation,
fixes, `task-delivery`, `task-pr-review` verdicts, or `task-closeout`. If the
current session participated in any of that work, stop and report:

```text
本会话不能提供独立 Feature Completion Audit
```

Do not override system, developer, current explicit user instructions, or any
applicable `AGENTS.md` or `AGENTS.override.md`. Read current repository and
GitHub facts on every run. Historical Task, Pull Request, delivery, review, or
closeout reports may locate evidence but are not completion proof.

This Skill is strictly read-only for Feature, Task, Issue, Pull Request, Project,
label, Relationship, repository, branch, and review state. A passing verdict is
an audit result only; it never authorizes Feature closeout.

## Standard invocation

Prefer the complete current Feature title, Issue number, and expected current
`main` SHA:

```text
请使用 feature-completion-audit，独立只读审计
[Feature] <当前完整标题> #<Feature编号>。

Expected main SHA: <当前 main SHA>
```

The Issue number is the primary Feature key. The current GitHub Issue title is
the canonical title and is an additional human-safety check.

If only a Feature number is supplied, read the Feature and echo its canonical
title before continuing. Stop when a supplied title and number materially
identify different work.

## Scope boundary

This Skill may:

- read current repository, Git, GitHub, Project, Issue, Pull Request, check-run,
  review, comment, and Relationship facts;
- fetch current remote refs;
- inspect the Feature body, comments, fields, Parent, acceptance criteria, and
  direct sub-issues;
- inspect direct child Issue lifecycle and merged Pull Request evidence;
- inspect current `main` source, tests, documentation, ADRs, configuration, and
  public behavior;
- use a detached checkout or a uniquely named temporary audit worktree at the
  locked `origin/main` SHA;
- run current CI-equivalent and Feature-applicable validation;
- create local temporary validation output;
- produce candidate gap-to-Task recommendations and an audit report.

It does not:

- edit Feature, Task, Issue, Pull Request, Project, label, Parent, Relationship,
  comment, review, or thread state;
- create, edit, reopen, or close a Task;
- edit or close the Feature;
- set Project Status to `Done`;
- check acceptance boxes in an Issue body;
- modify source, tests, documentation, configuration, or governance files;
- commit or push;
- submit a GitHub Review, Approve, or Request Changes;
- merge;
- run `task-delivery`, `task-pr-review`, or `task-closeout` as a write workflow;
- delete business branches;
- assess Epic completion;
- add token telemetry, token optimization, or evidence-runner behavior.

Never use force push, `--admin`, branch-protection bypass, `git reset --hard`,
`git clean`, broad cleanup, or another destructive operation.

## Resolve rules by responsibility

Follow these sources in order:

```text
system / developer / current explicit user instructions
-> applicable AGENTS.md / AGENTS.override.md
-> feature-completion-audit permission and audit rules
-> trusted .agents/policies/command-execution.md
-> optional ignored local execution profile for routing only
-> current GitHub Feature body, comments, fields, and Relationships
-> current origin/main repository state and current validation sources
-> current direct child Issues and their Pull Request / closeout facts
-> historical Tasks and Pull Requests only as supporting evidence
```

Use the current Feature for approved scope and acceptance criteria. Use current
`origin/main` for implemented behavior. Use current GitHub facts for Issue,
Project, Relationship, Pull Request, merge, check, and lifecycle state.

Documentation is usage guidance, not a substitute for normative rules or current
implementation evidence. If required facts conflict, are missing, or cannot all
be satisfied, report the precise conflict and use the evidence-insufficient
verdict rather than choosing a source on the maintainer's behalf.

## Permission model

A normal invocation authorizes only:

1. current read-only Feature, repository, Git, GitHub, and Project audits;
2. fetching refs and reading the current remote state;
3. creation and exact removal of one temporary detached audit worktree when
   needed;
4. current read-only validation;
5. an audit report and non-writing gap-to-Task recommendations.

It does not authorize any repository or GitHub lifecycle mutation. The optional
local execution profile may select execution context only after this Skill has
authorized the exact command. Elevation never grants a write permission.

## Phase 1: Read rules and execution environment

Before executing commands, read:

- current merged root `AGENTS.md`;
- every applicable `AGENTS.override.md`;
- `.agents/skills/feature-completion-audit/SKILL.md`;
- `.agents/policies/command-execution.md`;
- optional ignored `.agents/execution-profile.local.toml`;
- current `.github/workflows/ci.yml`;
- current Issue and Pull Request templates;
- current `pyproject.toml` and lock files.

Use the shared command-execution policy for all command routing. Keep these
concepts separate:

```text
lifecycle authorization
!= command execution routing
!= operating-system elevation
```

The local profile cannot change audit scope, Feature identity, trusted facts,
findings, severity, acceptance coverage, audited SHA, verdict, or GitHub write
permissions.

## Phase 2: Identify the Feature

Before the formal audit:

1. parse the Feature Issue number;
2. read the Issue from the current repository;
3. verify it exists;
4. verify it is `OPEN` for an ordinary pre-close completion audit;
5. verify it has `type:feature`;
6. read and record the current canonical Issue title;
7. compare any supplied title with the canonical title;
8. read the complete Feature body and comments;
9. read Project item and fields, labels, Parent, blockers, dependencies, and
   other formal Relationships;
10. read the complete direct sub-issue collection.

Normalize only superficial title differences: leading or trailing whitespace,
repeated whitespace, ordinary case differences, common full-width/half-width
punctuation, and Markdown escaping.

Stop when:

- title and number materially disagree;
- the Issue does not exist;
- the Issue is not a Feature;
- the Feature is already closed for an ordinary pre-close audit;
- repository identity is uncertain;
- a required current fact cannot be read reliably.

A ProjectV2 derived `Title` may lag behind the Issue title. Use Issue
`content.title` as authoritative and do not attempt a DraftIssue title update.

A retrospective audit of an already closed Feature requires a separate explicit
request and is outside the default invocation.

## Phase 3: Lock the current implementation baseline

At audit start:

1. fetch current remote refs;
2. identify `origin/main` and its current SHA;
3. record the actual `Audited main SHA`;
4. compare it with any supplied Expected main SHA;
5. record the Feature title, body, comments relevant to completion, state,
   Relationships, and direct child Issue set;
6. record the current remote check runs for the audited `main` SHA;
7. inspect current `main` through detached checkout, blobs, or a unique temporary
   worktree without modifying another working branch.

If Expected main SHA differs from actual `origin/main`, stop and report that the
handoff is stale.

The audit is bound to the actual `origin/main` SHA, not merely a local branch
name. Do not audit an unpushed local commit or a stale local `main` as the
Feature completion baseline.

## Phase 4: Build the direct child Issue inventory

Determine direct children only from current formal sub-issue / Parent
relationships. Do not infer direct children from labels, title similarity,
Project ordering, search results, linked mentions, or historical convention.

For every direct child Issue, read at least:

- Issue number, canonical title, type, state, and URL;
- confirmed Parent;
- labels and Project Status;
- comments relevant to completion, exceptions, or blockers;
- formal blocker, dependency, and other Relationships;
- closing or implementing Pull Request when applicable;
- Pull Request state, base, head, merge commit, merge time, and closing linkage;
- actual applicable check runs;
- automatic Issue closure evidence or an explicitly approved no-PR / no-code
  completion decision;
- final Task lifecycle label and Project Status when the child is a Task;
- evidence that the required result exists in the audited current `main`.

Classify every Issue encountered as exactly one of:

```text
direct child
indirect descendant
related issue
historical evidence
unrelated issue
```

For a direct child Task, verify its Task, Pull Request, merge, checks, automatic
closure, and closeout state. Local historical branch cleanup is not by itself a
Feature acceptance criterion unless the Feature or repository rules explicitly
make it one.

For a direct child Feature, do not silently flatten its descendants into this
Feature's direct child Task list. Verify its current completion state and
available completion evidence, and inspect descendants only when needed to
resolve this Feature's acceptance criteria.

An approved no-PR / no-code exception must be an explicit current maintainer
decision, ADR, or Feature/child Issue comment that explains why no repository
change was required. A closed Issue alone is not an exception.

Create a finding when, among other cases:

- a necessary direct child is open, reopened, or blocked;
- a closed Task lacks a merged Pull Request and lacks an approved exception;
- a Pull Request did not merge or its closing linkage is inconsistent;
- an Issue appears to have been closed manually despite required unmerged work;
- Issue, Project, label, Parent, or Relationship states materially conflict;
- the child result is absent from current `main`;
- necessary work is duplicated, omitted, orphaned, or attached to the wrong
  Parent;
- the Feature requires work that has neither a child Issue nor current-main
  implementation evidence.

A count such as `n / n closed` is an inventory fact only. It is never sufficient
completion evidence.

## Phase 5: Map Feature acceptance criteria

Extract every Feature acceptance criterion and produce a coverage matrix using
only these statuses:

```text
Satisfied
Partially satisfied
Not satisfied
Not applicable by approved decision
Evidence unavailable
```

Each row must cite concrete current evidence, such as:

- source or configuration in audited `main`;
- current tests;
- current documentation;
- an ADR or explicit approved design decision;
- current merged child Task / Pull Request facts;
- current GitHub state;
- current validation results.

Do not use any of these as standalone proof of satisfaction:

- a child Task title;
- a Pull Request body claim;
- a `task-delivery` handoff;
- an implementer self-check;
- a checked box in a child Task;
- a prior chat verdict without current evidence;
- `n / n closed`.

If the Feature body lacks complete, testable acceptance criteria, or a criterion
requires a maintainer scope decision, use `Evidence unavailable` or the
corresponding partial/not-satisfied state and do not manufacture a requirement.

## Phase 6: Review Feature-level integration

Audit the Feature-level result in current `main`, not every historical Pull
Request line by line.

Check at least:

- whether child results combine correctly in current `main`;
- whether the Feature's externally observable behavior is complete;
- whether configuration, interfaces, data flow, documentation, and examples are
  connected end to end;
- whether work exists only as an isolated component, dead path, unwired path, or
  disabled path;
- boundary conditions, error handling, recovery, idempotency, and compatibility;
- public interfaces and user or operator instructions;
- required migration, configuration, examples, and operational documentation;
- cross-module contracts and contradictory assumptions between child Tasks;
- regressions or scope leakage visible at Feature level;
- whether required work was mislabeled as optional follow-up or future
  optimization.

Do not routinely re-review every historical Pull Request. Inspect historical
diffs or commits only when current `main` cannot establish an acceptance
criterion, integration is suspicious, current behavior conflicts with historical
claims, or an approved decision must be verified.

For this quantitative-trading repository, apply relevant safety checks and mark
truly inapplicable areas explicitly. Check as applicable:

- live trading remains disabled by default and fails closed;
- credentials are not exposed;
- strategy and risk authority boundaries remain intact;
- order submission and unknown-order recovery are idempotent and safe;
- timestamps and data handling are UTC-correct and loss-aware;
- features do not use future observations;
- preprocessing and model selection preserve train/validation/test separation;
- backtests include appropriate fees, funding, slippage, turnover, and fill
  assumptions;
- the Feature does not introduce unapproved premature complexity.

## Phase 7: Validate audited `main`

Read current workflows, `pyproject.toml`, and lock files before choosing commands.
Run or independently verify:

- Feature-specific tests and validators;
- current full CI-equivalent tests;
- lint;
- formatting check;
- type check;
- required documentation or end-to-end validation;
- `git diff --check` for the audited tree or relevant comparison;
- complete tracked and untracked status of the original and temporary audit
  worktrees;
- actual remote check runs for the audited `main` SHA.

Use the locked `origin/main` SHA. Do not change code to make validation pass.
Do not delete, weaken, skip, or reinterpret tests merely to obtain a passing
result.

If a command fails from environment isolation, route it under the shared command
policy. If validation reveals a real code, test, documentation, configuration,
or CI failure, report it without repair and do not output a passing verdict.

A GitHub plan-limited endpoint is a service fact, not isolation evidence and not
proof that an undisclosed gate exists. Report available facts without bypassing
the service limitation.

## Phase 8: Identify completion gaps

For each completion gap, report:

- the affected Feature clause;
- current evidence;
- missing behavior, integration, test, document, or lifecycle state;
- severity;
- whether the minimum remedy is a new Task, reopening a Task, or fixing an
  existing Task through its normal workflow;
- a candidate Task title and minimal acceptance boundary when useful;
- whether the gap blocks Feature completion.

This Skill may recommend candidate work only. It must not create an Issue,
change Parent or Relationships, edit the Feature, or invoke implementation.

Do not convert personal preference, unrelated cleanup, speculative architecture,
or unapproved expansion into a completion blocker.

## Phase 9: Re-check stability

Before the final verdict, re-read:

- current `origin/main` SHA;
- Feature title, body, state, relevant comments, Project state, and labels;
- direct child Issue set and each material child state;
- blocker, dependency, Parent, and other formal Relationships;
- relevant Pull Requests and actual check runs;
- original repository and temporary worktree status.

The audit is invalidated by:

- any `origin/main` SHA change;
- a material Feature title, body, acceptance-criteria, or scope change;
- addition, removal, or re-parenting of a direct child Issue;
- a necessary child Issue being reopened or newly blocked;
- a new unresolved blocker or contradictory Relationship;
- a material check-run or validation state deterioration;
- another fact change that can affect Feature completion.

When invalidated, do not output a passing verdict. Report:

```text
Feature audit invalidated by repository or GitHub change.
Restart the audit in a new session.
```

A new `main` SHA requires a new Codex session and a complete new audit. Old
findings may be clues only; old coverage and verdicts are not inherited.

## Severity

Use exactly these severities:

- **Blocking**: the Feature cannot safely be declared complete, or there is a
  funds, credentials, core-data, repository-history, or permission-boundary
  risk;
- **High**: a primary Feature goal, critical integration, major acceptance
  criterion, or core safety property is incomplete;
- **Medium**: a clear completion gap, necessary test/documentation/compatibility
  omission, or lifecycle inconsistency means merged Tasks are insufficient to
  close the Feature;
- **Low**: non-blocking maintainability, clarity, or minor residual risk;
- **Nit**: wording, formatting, or tiny consistency issue.

Order findings by severity and cite exact Feature clauses, child Issues, current
files, validation evidence, or GitHub state. Any unresolved Blocking, High, or
Medium finding prevents a passing verdict.

## Verdicts

Output exactly one of these verdicts.

### Completed

```text
Feature 已完成，可以由维护者人工收尾
```

Use only when:

- Feature identity and scope are clear;
- all necessary direct child work is complete;
- every acceptance criterion is `Satisfied` or
  `Not applicable by approved decision`;
- current `main` integration, behavior, tests, and documentation are complete;
- all applicable validation and current-main check runs pass;
- no unresolved blocker exists;
- no unresolved Blocking, High, or Medium finding exists;
- audited main SHA, Feature content, Relationships, and direct child set remain
  stable.

This verdict does not close the Feature or set Project Status to `Done`.

### Incomplete

```text
Feature 尚未完成，需要补充或修复 Task
```

Use when any Blocking, High, or Medium finding remains, a necessary child is
incomplete, an acceptance criterion is not satisfied, current `main` lacks
required implementation/integration/tests/documentation, validation has a real
failure, or a blocker remains.

### Insufficient evidence

```text
证据不足，暂不能判定 Feature 完成
```

Use when no confirmed completion defect can be concluded but the Feature body or
criteria are incomplete or contradictory, direct children cannot be established
reliably, necessary repository/GitHub facts are unavailable, the baseline is
unstable, a maintainer decision is required, or other key evidence is missing.

Do not disguise insufficient evidence as either completion or a confirmed defect.

## Maintainer manual closeout gate

After a passing audit, the maintainer must independently verify immediately
before closeout:

- current `origin/main` equals the report's `Audited main SHA`;
- Feature title, body, and direct child set have not materially changed;
- no necessary child was reopened or blocked;
- current validation/check evidence has not deteriorated;
- the Feature remains in the intended pre-close state.

The maintainer then decides whether to close the Feature Issue, set Project
Status to `Done`, or perform other separately approved Feature metadata changes.
This Skill performs none of those actions.

## Report contract

Produce a report containing at least:

1. Audit Object:
   - Feature number, canonical title, and URL;
   - Feature Parent;
   - audited branch and `Audited main SHA`;
2. trusted rules, repository files, and GitHub sources read;
3. direct child Issue inventory:
   - classification;
   - Issue type and state;
   - closing or merged Pull Request / approved exception;
   - Project Status and lifecycle facts;
   - current-main completion evidence;
4. Feature acceptance-criteria coverage matrix;
5. findings grouped by Blocking, High, Medium, Low, and Nit;
6. Feature-level scope, integration, correctness, compatibility, and safety;
7. current-main local validation and remote check runs;
8. blockers, dependencies, open/reopened/orphaned work, and state conflicts;
9. gap-to-Task recommendations;
10. residual risks and known limitations;
11. actions deliberately not performed;
12. one fixed verdict.

End with:

```text
Audited main SHA: <actual main SHA>
```

A successful report may be concise, but it must not omit the direct child
inventory, acceptance matrix, findings, validation, verdict, or SHA.

## Command execution routing

Before executing a command, read `.agents/policies/command-execution.md` and
check the optional ignored `.agents/execution-profile.local.toml`.

First apply this Skill's strict read-only authorization and prohibitions. Only
then use the shared policy to select `sandbox-first`, `elevated-first`, or
`adaptive`. Route selection cannot authorize a GitHub mutation, repository
change, Feature closeout, Task creation, or another forbidden operation.

Preserve executable, argv, working directory, repository, Feature identity,
audited main SHA, audit phase, authorization source, and intent across retries.
This Skill never executes `gh auth login`.

Report routing events required by the shared policy. Never elevate a command
forbidden by this Skill.

## Temporary audit worktree

If a temporary worktree is needed:

- use a unique, recognizable temporary path;
- create it detached at the locked audited `origin/main` SHA;
- do not occupy or modify a Task branch worktree;
- do not edit reviewed files;
- remove only the exact temporary worktree created by this audit;
- do not use `git clean`;
- verify the original repository status and refs were not unintentionally
  changed before reporting.

## Recovery and re-audit

Every run re-reads current facts. Re-run in a new session when:

- a prior audit stopped for insufficient evidence;
- a new Task was completed and merged;
- the Feature body was clarified by an approved maintainer edit;
- a blocker was resolved;
- a validation failure was repaired through an independent Task;
- `main` SHA changed;
- the direct child Issue set changed;
- a Feature was reopened after an incorrect close.

Do not inherit an old verdict or completed audit steps to a new SHA. Even for the
same SHA, re-check current Feature, child, Relationship, Project, and check-run
facts because GitHub state can change.
