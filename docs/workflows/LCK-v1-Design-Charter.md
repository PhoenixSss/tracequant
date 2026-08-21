# LCK v1 Design Charter

> **Status:** Draft Design Baseline
> **Architecture name:** Local Control Kernel Workflow v1
> **Short name:** LCK v1
> **Purpose:** Define the target control architecture, lifecycle responsibilities, design constraints, and migration guardrails for the next-generation local Codex / Claude Agentic SDLC workflow.

---

## 1. Charter Purpose

This document freezes the design baseline for **LCK v1 (Local Control Kernel Workflow v1)**.

LCK v1 is not a new agent platform and not a parallel workflow runtime. It is the target evolution of the repository's existing Runner-based workflow architecture.

Its purpose is to move deterministic lifecycle control out of Skills and semantic agents, while preserving the current interactive Codex / Claude workflow.

The target architecture is:

> **Human keeps intent and merge authority.
> Codex / Claude keep semantic work.
> Skills become semantic procedures and entry instructions.
> LCK owns deterministic lifecycle control.
> Git and GitHub remain the authoritative current-state systems.**

LCK v1 MUST evolve from the current Runner infrastructure. It MUST NOT introduce a second long-lived workflow platform beside the existing system.

---

## 2. Core Architectural Decision

The primary architectural change is:

> **Lifecycle control moves out of Skill interpretation and into a deterministic local control layer.**

The current workflow already contains strong deterministic infrastructure through Evidence Runner, Validation Runner, Git/GitHub helpers, and related workflow utilities.

LCK v1 treats these components as the technical foundation of the future control plane.

The intended evolution is conceptually:

```text
Current

Human
  ↓
Codex / Claude
  ↓
Skill                       ← lifecycle controller
  ├─ calls Runner
  ├─ interprets Runner facts
  ├─ decides phase actions
  ├─ performs Git/GitHub writes
  └─ advances lifecycle
       ↓
Runner                      ← deterministic assistant
```

Target:

```text
Human
  ↓
Codex / Claude
  ↓
Skill                       ← semantic procedure / entry instructions
  ↓
LCK                         ← lifecycle controller
  ├─ resolves live facts
  ├─ evaluates deterministic gates
  ├─ runs formal validation
  ├─ executes bounded Git/GitHub effects
  ├─ resolves lifecycle state
  └─ returns deterministic results
       ↓
Git / GitHub                ← authoritative current state
```

The implementation MUST prefer restructuring and reusing current Runner code over creating a new parallel framework.

---

## 3. LCK v1 Goals

LCK v1 MUST achieve the following goals.

### 3.1 Preserve interactive Codex / Claude usage

Codex and Claude windows remain the primary human interaction surface.

A normal Task may still begin with a simple instruction such as:

```text
请严格遵循 LCK v1 实现 Issue #123
```

The user MUST NOT be forced to replace interactive work with a black-box background workflow.

### 3.2 Separate semantic work from deterministic mechanics

Codex / Claude remain responsible for semantic work:

```text
Understand
Design
Implement
Diagnose
Review
Explain
```

LCK becomes responsible for deterministic mechanics:

```text
repository identity
branch identity
workspace preparation
HEAD / SHA resolution
remote identity
PR identity
checks state
phase eligibility
formal validation
commit execution
push execution
PR creation/reuse/update
merge preflight
closeout mechanics
recovery classification
cleanup
```

### 3.3 Remove lifecycle authority from Skills

Skills remain useful, but their responsibility changes.

A Skill MAY:

- explain the semantic role of the current phase;
- tell the Agent which LCK entrypoint to invoke;
- define semantic expectations;
- define output structure;
- define what semantic work is allowed or prohibited.

A Skill MUST NOT:

- implement a Git/GitHub state machine;
- determine actionable branch / SHA / PR identity;
- decide whether direct Git/GitHub writes are permitted;
- perform lifecycle state transitions through interpreted procedural logic;
- act as the primary recovery engine.

### 3.4 Make workflow correctness independent of conversation continuity

Workflow correctness MUST NOT depend on:

- the Agent remembering an earlier SHA;
- a Codex session remaining open;
- a Claude session remaining open;
- an earlier conversation retaining phase facts;
- the same model continuing the Task;
- a handoff snapshot remaining fresh.

A new invocation MUST be able to reconstruct the current workflow state from authoritative current facts.

### 3.5 Reduce control-plane complexity

LCK v1 is successful only if it improves correctness while reducing or containing control complexity.

The final architecture MUST NOT become:

```text
Current Workflow
+ LCK
+ compatibility runtime
+ snapshot lineage
+ another state engine
```

The target is a net simplification.

---

## 4. Non-Goals

LCK v1 MUST NOT become a general agent platform.

The following are explicit non-goals unless future evidence justifies them through a separate architecture decision:

- long-running workflow daemon;
- background workflow server;
- workflow database;
- event-sourced lifecycle store;
- durable provenance lineage system;
- cross-phase authoritative snapshot cache;
- a second bounded verified fact handoff system;
- generic drift framework;
- generic dynamic permission engine;
- generic workflow DSL;
- DAG compiler;
- import graph;
- workflow lockfile system;
- local clone of GitHub Agentic Workflows Safe Outputs MCP Gateway;
- plugin architecture for workflow control;
- arbitrary shell execution API;
- automatic Review → Remediation loop;
- automatic merge;
- background retry loop;
- durable fact cache introduced only for token or latency optimization;
- distributed control infrastructure built for hypothetical future needs.

The default answer to such mechanisms in LCK v1 is **NO**.

---

## 5. Full Lifecycle Model

LCK v1 uses the following full Task lifecycle:

```text
Task Contract
      │
      ▼
Prepare
      │
      ▼
Delivery
      │
      ▼
HUMAN STOP
      │
      ▼
Independent Review
   │          │
 FAIL        PASS
   │          │
   ▼          ▼
HUMAN STOP   Merge Preflight
   │          │
explicit     ▼
remediation HUMAN SQUASH MERGE
   │          │
   ▼          ▼
Remediation Closeout
   │
   └────────────→ Independent Review again
```

Recovery is not a normal linear phase. It is a cross-cutting state resolution capability available at any invocation boundary.

---

## 6. Full-Lifecycle Responsibility Model

The following responsibility model is the design baseline for LCK v1.

| Lifecycle area | Human | Codex / Claude | Skill | LCK / Runner | Git / GitHub |
|---|---|---|---|---|---|
| **Task Contract** | Defines / approves business intent and resolves real ambiguity | May analyze Task, identify conflicts, propose clarification | Provides Task semantic rules | Validates contract requirements required by workflow | Stores Issue body and lifecycle metadata |
| **Prepare** | Starts the Task | Reads Task and semantic context | Instructs Agent how to enter Delivery | **Resolves live facts, checks eligibility, prepares or resumes correct workspace** | Authoritative repository / Issue / PR state |
| **Delivery** | Provides semantic steering when needed | **Understand / Design / Implement / Diagnose** | Provides Delivery semantic procedure | **Formal validation, lifecycle gates, commit, remote synchronization, PR resolution/creation/update, delivery result** | Stores commit / branch / PR / checks |
| **Independent Review** | Starts a separate Review invocation | **Fresh semantic reviewer; Inspect / Reason / Judge / Report** | Provides Review semantic procedure | **Resolves current review target, prepares clean read-only context, runs deterministic validation, checks verdict applicability** | Authoritative PR head/base/checks/review state |
| **Review FAIL** | **Decides remediation / redesign / abandon** | MUST NOT automatically continue into repair | MUST NOT automatically transition workflow | **Returns STOP_REQUIRED** | Stores findings / review state |
| **Remediation** | Explicitly starts repair | **Understands findings, modifies implementation, diagnoses failures** | Provides remediation semantic procedure | **Re-resolves live state, validates, commits, synchronizes remote, updates existing PR** | Stores new PR head and checks |
| **Review PASS** | Decides whether to merge | No merge authority | No merge authority | Performs deterministic merge preflight only | Authoritative mergeability and checks |
| **Merge** | **Performs manual Squash Merge** | No authority | No authority | MUST NOT auto-merge in v1 | Executes and records merge |
| **Closeout** | Usually no action; resolves true ambiguity if needed | Normally no semantic work | May provide minimal explanatory procedure if retained | **Reacquires merged state, determines Business Delivery status, synchronizes main, converges metadata, performs cleanup** | Authoritative merged / Issue / branch state |
| **Recovery / State Resolution** | Resolves only genuine ambiguity | Used only when semantic judgment is genuinely required | MUST NOT own recovery state machine | **Reconstructs current state from live Git/GitHub facts and selects the unique safe deterministic action or STOP** | Authoritative current state |

---

## 7. Agent Roles

LCK v1 distinguishes two semantic Agent roles.

### 7.1 Implementation Agent

Used in:

```text
Prepare
Delivery
Remediation
```

Primary responsibilities:

```text
Understand
Design
Implement
Diagnose
Explain
```

The Implementation Agent MAY:

- read repository files;
- inspect Git history and diffs;
- modify permitted workspace files;
- run local semantic/debugging tests;
- explain implementation decisions;
- respond to Human steering;
- consume Review findings during explicitly started remediation.

The Implementation Agent MUST NOT directly own:

- branch identity;
- remote identity;
- actionable SHA;
- PR identity;
- push target;
- lifecycle authorization;
- phase transition;
- merge;
- closeout;
- recovery classification.

### 7.2 Independent Review Agent

Used only in Independent Review.

Responsibilities:

```text
Inspect
Reason
Judge
Report
```

The Review Agent MUST:

- operate from a fresh review context;
- review the target resolved by LCK;
- remain read-only with respect to implementation;
- independently judge the Task and current PR;
- report PASS / FAIL and findings.

The Review Agent MUST NOT:

- modify implementation;
- inherit Delivery's semantic verdict as truth;
- supply actionable target SHA as authority;
- start remediation automatically;
- merge.

Implementation Agent and Review Agent are separate workflow roles even when both use Codex or both use Claude.

---

## 8. Skill Role

Skills remain part of LCK v1, but only as Agent-side semantic guidance.

Conceptually:

```text
Skill
=
semantic procedure
+ role instructions
+ allowed semantic behavior
+ LCK entrypoint guidance
```

Not:

```text
Skill
=
lifecycle state machine
+ Git/GitHub authorization engine
+ branch/PR/SHA resolver
+ recovery engine
```

A future Delivery Skill may state, for example:

```text
You are performing Task Delivery.

Your responsibilities:
- understand the Task;
- design the implementation;
- modify the workspace;
- diagnose failures;
- provide semantic delivery metadata.

Use LCK for lifecycle operations.

Do not directly:
- create or select task branches;
- commit;
- push;
- resolve or create PRs;
- change lifecycle state;
- merge.
```

---

## 9. Invocation Model

LCK v1 MUST use an **on-demand invocation model**.

It MUST NOT require a resident daemon.

Each LCK operation follows:

```text
start process
→ reacquire current facts
→ validate preconditions
→ perform one bounded operation or phase action
→ verify postconditions
→ return structured result
→ exit
```

Typical interaction:

```text
Human
  ↓
Codex / Claude session
  ↓
LCK invocation
  ↓
deterministic operation
  ↓
LCK exits
  ↓
Codex / Claude continues semantic work
```

Example conceptual entrypoints:

```text
lck delivery prepare <issue>
lck delivery complete <issue>
lck review prepare <issue-or-pr>
lck remediation prepare <issue>
lck remediation complete <issue>
lck merge preflight <issue-or-pr>
lck closeout <issue>
lck status <issue>
```

Exact CLI design is not frozen by this Charter.

The architectural requirement is only:

> **LCK is invoked on demand and derives its state from current authority.**

---

## 10. Fact Authority Model

### 10.1 Authoritative durable state

The authoritative durable sources are:

- Git commits and refs;
- GitHub Issue state;
- GitHub PR state;
- GitHub current PR head/base;
- GitHub checks;
- GitHub review state;
- GitHub merge state;
- Task contract;
- repository-controlled workflow definitions.

### 10.2 Diagnostic durable state

LCK MAY persist diagnostic evidence such as:

- run ID;
- timestamps;
- Kernel / Runner version;
- workflow source/version/hash;
- validation results;
- effect receipts;
- historical observed SHA;
- historical PR identity;
- Human action;
- review result;
- audit artifacts.

Diagnostic state answers:

> **What happened at that time?**

It MUST NOT answer:

> **What is currently authorized?**

### 10.3 Ephemeral operation state

Within one bounded operation, LCK MAY retain short-lived facts such as:

```text
observed local HEAD = X
observed remote OID = Y
observed PR head = Z
```

These are valid only as precondition guards for that operation.

They MUST NOT become cross-phase authority.

### 10.4 Forbidden state category

LCK v1 MUST NOT create:

> **durable derived authoritative state**

Examples include:

- durable "expected head SHA" used by later phases as authority;
- authoritative cross-phase fact handoff;
- durable freshness contract;
- snapshot lineage required for recovery;
- generic workflow drift state.

---

## 11. Core State Principles

The following principles are mandatory.

### P1 — Evolve, do not replace

LCK MUST evolve from existing Runner infrastructure.

It MUST NOT introduce a parallel long-term workflow runtime.

### P2 — Interactive Agent stays

Codex / Claude windows remain the primary human interaction surface.

### P3 — Semantic / Mechanic separation

Semantic work belongs to the Agent.

Deterministic lifecycle mechanics belong to LCK.

### P4 — Skill is not controller

Skills may guide semantic work and invoke LCK, but MUST NOT own lifecycle control.

### P5 — On-demand, no daemon

LCK v1 uses process-scoped invocations and MUST NOT require a resident service.

### P6 — Live facts are authority

Current Git / GitHub state is authoritative for mechanical identity and lifecycle state.

### P7 — Reacquire across phases

Delivery, Review, Remediation, Merge preflight, and Closeout MUST reacquire mechanical facts rather than trust prior phase snapshots.

### P8 — Guard within an operation

Short-lived operation-local identity guards are allowed to prevent TOCTOU races.

### P9 — Audit is not authority

Historical evidence may be durable but MUST NOT authorize future actions.

### P10 — Static authority, dynamic eligibility

A phase has statically declared capabilities.

Whether a capability can execute now depends on current live preconditions.

### P11 — Bounded Safe Effects

LCK side effects MUST be narrow, typed, deterministic, auditable, and explicitly verified.

### P12 — Runtime owns actionable identity

Agent-provided branch, remote, SHA, PR number, push refspec, or equivalent actionable identity MUST NOT be trusted as execution authority.

### P13 — Critical Outcome is a first-class Task contract requirement

The target workflow MUST support a Task-level Critical Outcome that proves the real supported caller reaches the intended capability and produces an observable result.

Implementing Critical Outcome requires synchronized changes to both:

- the Task / Issue body contract or template;
- deterministic validation / gate execution.

LCK design MUST NOT forget the Task-side contract change.

### P14 — Fail closed on ambiguity

If the deterministic resolver cannot identify a single safe action, the workflow MUST STOP.

### P15 — Review FAIL means STOP

Review FAIL MUST NOT automatically start remediation.

A new remediation invocation requires explicit Human intent.

### P16 — Human merge remains

LCK v1 retains manual Squash Merge.

### P17 — Business Delivery is separate from Cleanup

Successful merge may complete business delivery even when cleanup is still pending.

### P18 — Recovery from reality

Recovery derives current state from Git/GitHub authority, not old workflow lineage.

### P19 — Provider-neutral control

Codex and Claude use the same LCK lifecycle contract.

Provider differences remain in the semantic Agent layer.

### P20 — Complexity must decrease

The final LCK migration MUST reduce or contain control-plane complexity.

---

## 12. Capability Model

LCK v1 uses:

> **Static authority; dynamic eligibility.**

A phase declares what kinds of effects it may perform.

Example conceptual model:

| Phase | Agent authority | LCK effect capability |
|---|---|---|
| Prepare | read / semantic inspection | prepare or restore Task workspace |
| Delivery | workspace write | formal validation; commit; ensure remote; ensure open PR |
| Independent Review | read-only | resolve review target; validation; publish review result |
| Remediation | workspace write | validation; commit; ensure remote; update existing PR |
| Merge | none | merge preflight only |
| Closeout | none normally | main sync; metadata convergence; cleanup |
| Recovery | none by default | deterministic state resolution and bounded recovery effects |

LCK MUST NOT use a generalized global Boolean such as:

```text
write_actions_allowed = true
```

as the primary authorization model.

Instead:

```text
phase capability
+
current operation preconditions
=
operation eligibility
```

---

## 13. Safe Effect Model

Every LCK write operation MUST follow:

```text
resolve current facts
        ↓
validate preconditions
        ↓
perform ONE bounded side effect
        ↓
re-read authoritative facts
        ↓
verify postcondition
        ↓
return structured receipt
```

Every Safe Effect MUST have:

- a clear purpose;
- a narrow input contract;
- no Agent-controlled actionable identity;
- explicit preconditions;
- explicit postconditions;
- deterministic error states;
- audit evidence;
- bounded retries only where safe;
- fail-closed behavior on ambiguity;
- idempotent behavior where practical.

Candidate effects may include:

```text
commit_current_tree
ensure_remote_branch
ensure_open_pr
publish_review
cleanup_task_refs
```

The exact final effect set is not frozen by this Charter.

LCK v1 MUST NOT expose:

```text
run_arbitrary_shell
push_arbitrary_refspec
write_arbitrary_github_object
```

or equivalent generic escape hatches.

---

## 14. Validation and Critical Outcome

### 14.1 Validation boundary

LCK MUST provide a deterministic validation/gate boundary that can execute repository and Task acceptance requirements.

### 14.2 Critical Outcome

The target workflow MUST add a formal **Critical Outcome** to the Task contract.

Conceptually:

```text
real supported caller
        ↓
new or changed capability
        ↓
observable expected result
```

Critical Outcome is intended to prove:

> The Task's claimed user-facing or supported capability is actually connected end-to-end.

It MUST NOT be silently replaced by:

- private helper unit tests;
- module existence;
- local function return values;
- mocked-away integration boundaries;
- passing static checks.

### 14.3 Task contract synchronization

Critical Outcome implementation requires a corresponding change to Task Issue bodies/templates.

The workflow implementation MUST include:

1. Task contract format / authoring rules;
2. deterministic reading of the contract;
3. formal execution or verification;
4. evidence output;
5. Delivery veto when Critical Outcome fails.

Critical Outcome MUST NOT be implemented only in Runner code while leaving Task bodies unable to express the required contract.

### 14.4 Acceptance hierarchy

Conceptually:

```text
Task Contract Valid
        ↓
Critical Outcome PASS
        ↓
Task-specific Acceptance Criteria
        ↓
Regression / integration / unit tests
        ↓
Static validation
        ↓
Delivery mechanics
```

Cheap checks MAY execute earlier for fail-fast efficiency.

But in acceptance semantics:

> **Critical Outcome FAIL = Delivery MUST NOT complete.**

---

## 15. Delivery Contract

A successful Delivery follows the following ownership pattern.

### Human

Starts Delivery and provides semantic steering only when required.

### LCK Prepare

LCK:

- identifies repository and Task;
- reacquires main / branch / PR facts;
- validates lifecycle eligibility;
- prepares or restores the correct Task workspace;
- returns a deterministic Delivery context.

### Implementation Agent

Codex / Claude:

- understands the Task;
- designs the change;
- modifies permitted files;
- adds or updates tests;
- diagnoses failures;
- responds to Human semantic steering;
- returns semantic completion metadata.

The Agent does not directly commit, push, create PRs, or advance lifecycle state.

### LCK Delivery Completion

LCK:

1. reacquires current facts;
2. executes Critical Outcome;
3. runs formal validation;
4. verifies allowed scope / validated tree;
5. commits the validated tree;
6. resolves current commit identity;
7. ensures the remote Task branch;
8. ensures the current OPEN PR;
9. reacquires final Git/GitHub state;
10. verifies Delivery postconditions;
11. emits a Delivery receipt;
12. returns `READY_FOR_REVIEW`;
13. stops.

Delivery MUST end at a Human boundary before Independent Review.

---

## 16. Independent Review Contract

Independent Review MUST be a separate invocation.

LCK:

1. resolves the current OPEN PR from live GitHub state;
2. resolves current PR head and base;
3. prepares a clean read-only review context;
4. runs deterministic applicable validation;
5. invokes or enables a fresh semantic Review Agent.

The Review Agent:

- independently inspects the effective change;
- evaluates Task requirements;
- evaluates Critical Outcome evidence and current applicability;
- returns PASS / FAIL with findings.

Before accepting the verdict, LCK MUST re-query applicability facts.

Examples:

```text
PR head changed during review
→ REVIEW_STALE_HEAD

relevant base changed during review
→ REVIEW_STALE_BASE
```

These are invocation-local stale conditions, not a generalized cross-phase drift system.

### PASS

Returns readiness for Human merge.

### FAIL

Returns `STOP_REQUIRED`.

No automatic remediation occurs.

---

## 17. Remediation Contract

Remediation starts only through explicit Human intent after Review FAIL.

LCK reacquires:

- Task;
- current OPEN PR;
- current PR head/base;
- current workspace state;
- current Review findings.

The Implementation Agent:

- understands the findings;
- changes the implementation;
- diagnoses issues;
- completes semantic repair.

LCK then:

- reacquires current facts;
- executes Critical Outcome;
- runs formal validation;
- commits the validated repair;
- ensures the remote branch;
- updates or reuses the current OPEN PR;
- verifies postconditions;
- stops at the next Independent Review boundary.

Remediation MUST NOT automatically trigger Review.

---

## 18. Merge Contract

LCK v1 retains:

> **Manual Squash Merge**

LCK MAY provide deterministic merge preflight:

- correct PR;
- current head;
- current base;
- required checks;
- review verdict;
- unresolved blocking conditions;
- mergeability.

LCK MUST NOT execute the merge automatically in v1.

The Human performs the Squash Merge.

---

## 19. Closeout Contract

Closeout MUST distinguish:

```text
Business Delivery
```

from:

```text
Cleanup
```

### Business Delivery

If authoritative GitHub facts prove the intended PR has been successfully merged and the Task delivery contract is satisfied:

```text
Business Delivery = COMPLETE
```

### Cleanup

Cleanup may independently be:

```text
COMPLETE
PENDING
```

Cleanup includes items such as:

- local branch cleanup;
- worktree cleanup;
- remote branch cleanup if applicable;
- main synchronization;
- metadata convergence.

A cleanup failure MUST NOT retroactively convert a successfully merged business delivery into business failure.

Cleanup operations SHOULD be idempotent and retryable.

---

## 20. Recovery / State Resolution Model

Recovery is a cross-cutting deterministic resolver, not a semantic lifecycle phase.

Each invocation starts by asking:

> **What is true now?**

Not:

> **How does current state differ from an old workflow snapshot?**

Examples:

| Current live state | LCK behavior |
|---|---|
| local Task branch exists, remote absent | validate workspace and run `ensure_remote_branch` if the current phase permits |
| remote branch exists, OPEN PR absent | `ensure_open_pr` |
| historical CLOSED PR exists | ignore it for active PR resolution |
| exactly one matching OPEN PR exists | use it |
| multiple active matching PRs | STOP |
| remote branch diverged | STOP |
| PR already merged | Business Delivery becomes COMPLETE |
| merge removed remote branch | treat missing remote branch as normal post-merge state |
| cleanup incomplete | Cleanup = PENDING |
| PR head changed during Review | invalidate that Review invocation |
| facts do not produce one safe deterministic action | STOP and surface live facts to Human |

Recovery MUST NOT depend on:

- restoring a previous Kernel process;
- snapshot lineage;
- provenance chains;
- generalized drift graphs.

---

## 21. Retry Policy

Retries MUST be classified.

| Failure type | Default LCK behavior |
|---|---|
| transient HTTP/network failure on idempotent operation | bounded automatic retry allowed |
| state precondition changed | reacquire facts and re-resolve; no blind retry |
| deterministic validation failure | return failure to semantic implementation/remediation flow |
| Critical Outcome failure | Delivery blocked until semantic repair |
| Review FAIL | zero automatic remediation |
| identity ambiguity | zero Agent retry; STOP |
| remote divergence | STOP |
| merge conflict requiring semantic resolution | STOP / explicit remediation |
| cleanup failure | bounded idempotent retry; Business Delivery remains COMPLETE if already merged |

---

## 22. Observability and Audit

LCK MUST retain enough evidence to explain what happened without turning evidence into workflow authority.

Audit records MAY include:

- Task;
- invocation;
- phase;
- LCK / Runner version;
- workflow version/hash;
- semantic engine identity/version;
- start and end timestamps;
- observed facts;
- validation results;
- Critical Outcome results;
- Safe Effect requests and receipts;
- review verdict;
- Human boundary actions;
- failure classification.

Rule:

> **Audit log is historical evidence, not recovery authority.**

---

## 23. Complexity Budget

Complexity control is an acceptance criterion for LCK itself.

### Hard v1 constraints

LCK v1 target:

```text
0 new workflow databases
0 daemons
0 generic workflow DSLs
0 generic dynamic permission engines
0 cross-phase authoritative caches
0 snapshot lineage systems
0 automatic Review→Remediation loops
0 automatic merges
```

### Design rule

For every proposed control mechanism, first ask:

> **Can this be solved by reacquiring current authoritative facts?**

If yes, prefer reacquisition over new durable state.

### Change discipline

A small workflow bug fix SHOULD default to:

```text
0 new durable control-state fields
0 new provenance concepts
```

New write capability SHOULD normally be introduced as one explicit bounded Safe Effect at a time.

The migration SHOULD aim for net deletion or simplification of:

- snapshot authority;
- expected-SHA plumbing;
- handoff freshness logic;
- generic drift categories;
- Skill-owned Git/GitHub mechanics;
- direct Agent lifecycle writes;
- duplicate lifecycle state logic.

---

## 24. Provider Neutrality

LCK lifecycle correctness MUST be independent of whether semantic work is performed by Codex or Claude.

The same lifecycle architecture MUST support:

```text
Codex implementation
→ Claude remediation
→ fresh Codex review
```

or any equivalent combination.

Provider-specific differences may exist in:

- prompt/Skill formatting;
- sandbox configuration;
- semantic tool availability;
- output-schema integration.

They MUST NOT determine:

- current branch;
- actionable SHA;
- current PR;
- phase eligibility;
- push destination;
- merge state;
- recovery state.

---

## 25. Migration Principles

LCK v1 migration MUST proceed by responsibility transfer, not by duplicating the whole workflow.

Preferred pattern:

```text
existing Runner capability
→ extract / normalize deterministic responsibility
→ move lifecycle authority into LCK boundary
→ simplify Skill
→ delete obsolete snapshot / handoff / control logic
```

Not:

```text
build complete LCK platform
→ keep old workflow
→ maintain compatibility forever
```

The migration plan MUST identify existing mechanisms as one of:

```text
KEEP
MOVE
SIMPLIFY
REMOVE
```

Examples of intended direction:

| Existing mechanism | Direction |
|---|---|
| repository-local semantic Skills | KEEP + SIMPLIFY |
| Validation Runner | KEEP + STRENGTHEN |
| Evidence / Fact Runner | KEEP + REORGANIZE into live fact resolution |
| Git/GitHub write mechanics in Skills | MOVE to LCK Safe Effects |
| cross-phase trusted snapshot authority | REMOVE |
| bounded verified fact handoff as workflow authority | REMOVE |
| generic freshness / drift framework | REMOVE or sharply narrow to invocation-local stale conditions |
| dynamic global write authorization | REPLACE with static phase capability + operation precondition |
| automatic review/repair loop | REMOVE |
| manual Squash Merge | KEEP |
| monolithic closeout success state | REPLACE with Business Delivery + Cleanup |

---

## 26. Architecture Acceptance Criteria

Before LCK v1 implementation is considered architecturally complete, the design and implementation MUST demonstrate all of the following:

1. The user can still start work through a simple Codex / Claude window instruction.
2. Codex and Claude can use the same lifecycle contract.
3. Agent sessions may change without invalidating workflow correctness.
4. Skills no longer implement lifecycle mechanics as interpreted state machines.
5. LCK can reconstruct current workflow state from live Git/GitHub authority.
6. Cross-phase authoritative snapshots are not required.
7. Branch / SHA / PR actionable identity is resolved by LCK.
8. Agent cannot directly own commit / push / PR mutation / lifecycle transition.
9. Delivery ends at a Human boundary before Independent Review.
10. Independent Review uses a fresh review role and live-resolved target.
11. Review FAIL always stops.
12. Remediation requires explicit Human intent.
13. Human Squash Merge remains mandatory in v1.
14. Closeout distinguishes Business Delivery from Cleanup.
15. Recovery does not require an old Kernel process or snapshot lineage.
16. Historical CLOSED PRs do not incorrectly block current active PR resolution.
17. Normal lifecycle evolution is not generalized into "drift".
18. Critical Outcome is part of the target Task contract and is enforced as a Delivery gate.
19. Task Issue/template changes required for Critical Outcome are implemented together with the corresponding deterministic validation support.
20. LCK does not require a workflow database or daemon.
21. Control-plane complexity is demonstrably reduced or contained versus the current baseline.
22. Current Runner infrastructure is reused rather than duplicated by a parallel runtime.

---

## 27. Design Freeze Summary

The following decisions are considered the current LCK v1 design baseline.

### Frozen

- Name: **Local Control Kernel Workflow v1 (LCK v1)**.
- Full-lifecycle responsibility model is the authoritative responsibility model.
- Codex / Claude remain the interactive semantic engines.
- Skills remain but cease to be lifecycle controllers.
- LCK evolves from existing Runner infrastructure.
- LCK uses on-demand invocations, not a daemon.
- Git/GitHub live state is authoritative for mechanical facts.
- Mechanical facts are reacquired across phases.
- Operation-local ephemeral guards are allowed.
- Audit evidence is not workflow authority.
- Phase authority is static; operation eligibility is dynamic.
- LCK uses bounded Safe Effects.
- Review FAIL stops.
- Remediation requires explicit Human intent.
- Manual Squash Merge remains.
- Business Delivery and Cleanup are separate.
- Recovery derives state from current reality.
- Critical Outcome is part of the target workflow and requires synchronized Task-contract changes.
- LCK success requires correctness improvement and control-complexity reduction.

### Not yet frozen

The following remain implementation design questions:

- exact CLI command names;
- exact module/file layout;
- exact structured output schemas;
- exact Safe Effect inventory;
- exact Task Critical Outcome syntax;
- exact Task template migration strategy;
- exact Runner refactor sequence;
- exact lifecycle metadata naming;
- exact audit artifact format.

These MUST be decided later without violating the frozen architectural principles above.

---

## 28. Guiding Principle

The design can be summarized as:

> **Keep intelligence in the Agent.
> Keep authority in deterministic control.
> Keep truth in Git and GitHub.
> Keep the Human at irreversible boundaries.
> Keep the control plane small.**
