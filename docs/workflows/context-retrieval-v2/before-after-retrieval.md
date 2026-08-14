# Agent Context / Retrieval v2 — Before / After baseline

Task: [#87](https://github.com/PhoenixSss/tracequant/issues/87)
Scope: default context acquisition of the repository workflow (AGENTS.md,
CLAUDE.md, Delivery / Review / Closeout Skills, feature-completion-audit).

This document is the compact versioned comparison evidence required by
Requirement 11 and the Task-specific Verification of #87. The **BEFORE**
section is frozen at Task base SHA `c032e65f2399d7d59b4afa844e85dacfb4135129`
from the unmodified repository. The **AFTER** section is filled in after the
implementation, using the same measurement definition.

Measurement definition: for each scenario, record the sources that the current
workflow instructions require the agent to read by default (full text into
model context), the deterministic tool queries the workflow performs, and the
sources that are only read after an explicit trigger. Context bytes / chars and
Token counts are not reliably obtainable: this repository explicitly excludes
Task workflow Token telemetry (`AGENTS.md`, "Token telemetry"), so they are not
fabricated here.

---

## BEFORE (base SHA c032e65, unmodified repository)

### Scenario A — Ordinary leaf Task (Delivery default path)

Current default retrieval plan, as required by the unmodified instructions:

| # | Source | Required by | Full text? |
|---|---|---|---|
| 1 | Assigned Task Issue body | AGENTS.md "Issue-driven workflow" step 1 | Yes |
| 2 | All Issue comments | AGENTS.md step 1 "including comments" | Yes (all) |
| 3 | Parent Feature body | AGENTS.md step 3 "Read its parent Issue" | Yes (complete) |
| 4 | Blocking / related Issue bodies | AGENTS.md step 3 "blocking Issues" | Yes (complete) |
| 5 | Linked documentation | AGENTS.md step 3 "linked documentation" | Yes (all linked) |
| 6 | ADRs | AGENTS.md step 3 "and ADRs" | Yes (all) |
| 7 | Templates, workflows, validation sources, affected architecture | task-delivery-runner Phase 1 | Yes (eager list) |
| 8 | Skill instruction + policies | task-delivery-runner | SKILL.md + workflow-evidence.md |

Deterministic queries performed by the Evidence Runner `delivery` snapshot
(`workflow_evidence.py`): `gh issue view <task> --json
number,title,body,comments,state,labels,projectItems,url,closedAt,
closedByPullRequestsReferences` (fetches the full body and every comment body),
relationships GraphQL (parent/blocking/sub-issue metadata), issue closure
snapshot, git snapshot (status/log/rev-parse/merge-base/branches/worktrees),
and, when a PR exists, PR view, review threads, required checks, and diff
digest.

Default full-text reads: 1 Task body + **all** comments + Parent body + all
blocking bodies + all linked docs + all ADRs + templates/workflows/validation
sources/architecture docs. Manual expansion count: 0 (everything is eager).

### Scenario B — Parent / dependency expansion

The current rules contain no trigger. AGENTS.md step 3 makes the full Parent
body, every blocking body, all linked docs, and all ADRs unconditional reads
for every Task. A leaf body that already defines Objective / Requirements /
Acceptance Criteria still drags in the complete Parent Feature, the complete
Parent Epic, and every sibling / related body whenever one constraint is
mentioned. There is no "read the minimum relevant section" step and no
sufficiency evaluation.

### Scenario C — Independent Review (default path)

task-pr-review-runner Phase 2 requires: "Independently read the **complete**
Task specification/**comments**/Relationships, PR body and effective diff,
every changed file in context, commits, tests/docs/config/public interfaces,
relevant unchanged code, current reviews/threads/checks, **workflows, tooling**,
and safety rules."

Default full-text reads: Task body + **all** comments + Relationships +
PR body + effective diff + all changed files + commits + tests/docs/config +
unchanged related code + reviews/threads/checks + workflows + tooling + safety
rules. The fresh-session, read-only, independent judgment properties are
already present and must be preserved.

### Scenario D — Closeout (default path)

task-closeout is already the closest to the target: its default context is
identity facts (Task/PR identity, closing linkage, actual `MERGED` state,
reviewed head, merge SHA/method/time, Issue closure, Project/label facts,
workspace/worktrees, exact branches, checks). The Runner's `closeout-readonly`
snapshot fetches the Task body and comments for content hashing, but the Skill
does not instruct re-reading the full business specification. Gap: the minimal
business-context default is implicit rather than explicit, and the Skill does
not state that a full hierarchy re-read is only an exception path.

### Metric summary (BEFORE)

| Metric | A Delivery leaf | B Parent/dep | C Review | D Closeout |
|---|---|---|---|---|
| Default Issue bodies (full text) | Task + Parent + blocking(s) | Task + full Parent + full Epic | Task + PR body | Task (identity/hash only) |
| Default comments (full text) | all | all | all ("complete comments") | none |
| Parent / Epic bodies | full | full | — | none |
| Dependency bodies | full (blocking) | full | — | none |
| Docs / ADR | all linked + all ADR | all linked + all ADR | workflows + tooling + docs | none |
| Skill / instruction sources | SKILL.md + 2 policies | same | SKILL.md + policies | SKILL.md + policies |
| Deterministic tool queries | issue view (body+comments), relationships, closure, git, (PR view/threads/checks/diff) | same | review snapshot, validation, recheck | closeout-readonly, validation, recheck |
| Manual expansion count | 0 | 0 | 0 | 0 |
| Triggered expansion sources | none defined | none defined | none defined | none defined |
| Context chars / bytes | not reliably obtainable (no telemetry) | — | — | — |
| Token data | not reliably obtainable (no telemetry) | — | — | — |

The `gh issue view`/PR queries themselves are legitimate deterministic fact
verification; the eager part is that the workflow instructions additionally
require the model to consume the full text of comments, Parent, blocking,
docs, and ADRs by default, and that the Delivery/Review Skills name these
full-text sources in their Phase 1 / Phase 2 mandatory lists.

---

## AFTER (implementation head of #87, same measurement definition)

Changed policy sources for this measurement: `AGENTS.md` (leaf-Issue-first
default context, default exclusions, six expansion triggers, progressive
retrieval, comments policy, Parent/Epic policy, deterministic-facts
separation, feature-completion-audit exception), `CLAUDE.md` (ownership
cleanup per Requirement 7), and the eight Delivery / Review / Closeout /
feature-completion-audit Skills under `.agents/skills/` and `.claude/skills/`.

### Scenario A — Ordinary leaf Task (Delivery default path)

Default full-text reads: **the current Task body only**. Comments, Parent /
Epic bodies, blocking bodies, linked docs/ADRs, templates, workflows,
validation sources, and architecture docs are not default input (AGENTS.md
"Default exclusions", Delivery Skill Phase 1). The Runner `delivery` snapshot
continues to provide deterministic identity facts (state, labels, Project
fields, Parent identity, relationships metadata, blocker state, PR head/checks)
without full-text consumption of those sources.

### Scenario B — Parent / dependency expansion

Leaf-Issue-first: a self-sufficient leaf body executes directly. When a
Parent-level constraint is missing, the agent detects the insufficiency,
expands the minimum relevant Parent context, evaluates sufficiency, and stops
— no unbounded hierarchy expansion (AGENTS.md "Parent / Epic policy" +
"Progressive retrieval").

### Scenario C — Independent Review (default path)

Default full-text reads: current Task body + PR body + effective diff + every
changed file + commits + relevant unchanged code + reviews/threads/checks +
review-relevant constraints. Comments and Parent/Epic/hierarchy sources are
expanded only when review scope, risk, or ambiguity requires it (Review Skill
Phase 2). Fresh-session, read-only, independent judgment, and
no-inheriting-Delivery-verdict are unchanged.

### Scenario D — Closeout (default path)

Explicit minimal-context default: Task/PR identity, reviewed head, merge
identity, CI/review status, Issue/Project lifecycle state, main/branch state,
post-merge validation (Closeout Skill "Default context"). Full business
hierarchy re-read only on an explicit anomaly trigger.

### Metric summary (AFTER)

| Metric | A Delivery leaf | B Parent/dep | C Review | D Closeout |
|---|---|---|---|---|
| Default Issue bodies (full text) | 1 (current Task body) | 1 (current Task body) | Task body + PR body | Task (identity/hash only) |
| Default comments (full text) | none | none | none | none |
| Parent / Epic bodies | none (on demand, minimum section) | minimum necessary section only | none (on scope/risk trigger) | none |
| Dependency bodies | metadata + on-demand section | metadata + on-demand | none (on trigger) | none |
| Docs / ADR | on demand (explicit reference / safety / verification trigger) | on demand | on trigger | none |
| Skill / instruction sources | SKILL.md + policies (unchanged) | same | same | same |
| Deterministic tool queries | Runner snapshot sets (unchanged) | same | same | same |
| Manual expansion count | 0 in default path; trigger-based on demand | 1 (bounded) | 0 in default path | 0 in default path |
| Triggered expansion sources | 6 triggers defined | parent-constraint trigger | scope/risk/ambiguity trigger | anomaly trigger |
| Context chars / bytes | not reliably obtainable (no telemetry) | — | — | — |
| Token data | not reliably obtainable (no telemetry) | — | — | — |

### Before → after comparison

| Metric | Before | After | Interpretation |
|---|---|---|---|
| Default full-text Issue bodies | Task + Parent + blocking(s) (AGENTS.md step 1/3) | 1 (current leaf Task body) | Parent/blocking bodies removed from default; identity via Runner metadata |
| Default full-text comments | all — mandated by AGENTS.md step 1, Delivery Phase 1, Review Phase 2 | none | comments default-off; read only under one of five defined triggers |
| Default Parent / Epic bodies | complete (AGENTS.md step 3) | on demand, minimum relevant section | Parent/Epic moved from default to triggered |
| Default dependency bodies | complete (blocking Issues, AGENTS.md step 3) | metadata + on-demand section | hard-dependency trigger only |
| Default docs / ADR | all linked + all ADR (AGENTS.md step 3) | on demand | explicit-reference / safety / verification triggers |
| Default templates/workflows/validation sources/architecture (Delivery) | eager Phase 1 list | not default; trigger-based | Delivery Skill Phase 1 rewritten |
| Default workflows/tooling (Review) | Phase 2 mandatory list | review-relevant constraints only | Review Skill Phase 2 rewritten |
| Skill / instruction sources | SKILL.md + policies | unchanged | no governance reduction |
| Deterministic tool queries | Runner snapshot sets | unchanged | mechanical verification fully preserved |
| Expansion triggers | none defined | six (explicit reference, missing/ambiguous spec, conflict, hard dependency, safety/architecture, verification) | bounded progressive retrieval replaces eager loading |
| Bounded expansion / no unbounded recursion | not defined | explicit rule | "Progressive retrieval" forbids unbounded recursion |
| Manual expansion count (default path) | 0 (all eager) | 0 (default), trigger-based on demand | fewer default reads; expansion still possible |
| Context chars / bytes | not reliably obtainable | not reliably obtainable | no fabricated Token data |
| Token data | not reliably obtainable | not reliably obtainable | repository excludes Task Token telemetry |

Eliminated default full-text reads: all Issue comments; complete Parent /
Epic bodies; blocking / related Issue bodies; all linked docs and ADRs;
templates / workflows / validation sources / affected architecture docs
(Delivery eager list); workflows / tooling (Review mandatory list).

Retained deterministic queries (unchanged): Runner `delivery`, `review`,
`closeout-readonly`, `recheck` snapshots — issue view (content hashing and
identity), relationships metadata, closure, git snapshot, PR view, review
threads, required checks, diff digest — plus the Validation Runner profiles.
These remain the single mechanical path and never inject full source text
into model context by themselves.

Default → triggered transitions: comments; Parent/Epic full bodies; dependency
bodies; linked docs/ADRs; templates/workflows/architecture docs; review-time
workflow/tooling reads.

Safety, financial, data-integrity, and architecture hard rules in AGENTS.md
(Implementation rules, Financial safety, Data correctness, Backtesting,
Verification, Prohibited premature complexity) are unchanged; the
safety/architecture expansion trigger requires loading the applicable durable
rule and fails closed when it cannot be confirmed.
