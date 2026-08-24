# LCK v1 Design Charter

> **Status:** Draft Design Baseline
> **Architecture name:** Local Control Kernel Workflow v1
> **Short name:** LCK v1
> **Purpose:** Define the target control architecture, lifecycle responsibilities, design constraints, and migration guardrails for the next-generation local Codex / Claude Agentic SDLC workflow.
> **Current revision:** Adds **Operation Snapshot Isolation** as the lifecycle-wide state model: one phase-specific authoritative snapshot per lifecycle operation, immutable within that operation; bounded postcondition queries verify only effects caused by that operation; the next lifecycle operation reacquires fresh authority.

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
  ├─ acquires phase-specific authoritative snapshots
  ├─ evaluates deterministic gates against frozen operation inputs
  ├─ runs formal validation
  ├─ executes bounded Git/GitHub effects
  ├─ verifies only the postconditions of effects it caused
  └─ returns deterministic results / receipts
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
- a cross-phase handoff snapshot remaining fresh.

A **new lifecycle operation** MUST be able to reconstruct its current workflow state from authoritative current facts.

If one lifecycle operation intentionally spans more than one process invocation because semantic Agent work occurs between LCK entrypoints, LCK MAY persist a sealed operation-owned snapshot / guard solely to resume that same operation. Such state MUST NOT authorize a later lifecycle operation and MUST NOT become cross-phase authority.

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
- lazy or incremental acquisition of authoritative Git/GitHub facts after an operation snapshot has been frozen;
- phase-internal full-state refresh loops used to keep an in-flight operation synchronized with external changes;
- background polling loops that wait for GitHub checks or other asynchronous eligibility facts to change;
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

The normal lifecycle unit is an **operation**. Each new lifecycle operation MUST begin with one bounded authoritative acquisition stage:

```text
start operation
→ acquire every authoritative Git / GitHub fact required by this operation
→ freeze one phase-specific Operation Snapshot
→ evaluate eligibility against that immutable snapshot
→ perform semantic/local work and/or bounded Safe Effects
→ for effects caused by this operation, query only their targeted postconditions
→ emit structured evidence / receipt
→ end operation
```

The authoritative acquisition stage MAY require multiple local Git, remote Git, and GitHub API calls. "Acquire once" means:

> **Each required authoritative fact is acquired during one bounded operation-start window and is not reacquired after the snapshot is frozen.**

It does NOT require one physical shell command or one atomic GitHub API transaction.

Typical interaction remains:

```text
Human
  ↓
Codex / Claude session
  ↓
LCK operation entry
  ↓
Operation Snapshot
  ↓
deterministic operation / semantic handoff
  ↓
LCK exits or seals operation-owned continuation state
  ↓
Codex / Claude continues semantic work
```

A new lifecycle transition MUST acquire a new snapshot. Examples:

```text
Delivery Prepare      → fresh DeliveryPrepareSnapshot
Delivery Complete     → fresh DeliveryCompleteSnapshot
Review                → fresh ReviewSnapshot
Remediation Prepare   → fresh RemediationPrepareSnapshot
Remediation Complete  → fresh RemediationCompleteSnapshot
Merge Preflight       → fresh MergeSnapshot
Closeout               → fresh CloseoutSnapshot
Recovery invocation    → fresh RecoverySnapshot
```

A semantic operation MAY span multiple process invocations when an Agent must act between LCK entrypoints. Independent Review is the primary example: `review prepare` may seal the Review Snapshot and workspace, the Review Agent performs semantic analysis, and `review complete` consumes that same sealed Review Snapshot. `review complete` MUST NOT reacquire Git/GitHub authority for the in-flight Review.

Operation-owned continuation state is allowed only to finish or recover the same operation. It MUST NOT be reused as authority by the next lifecycle operation.

Example conceptual entrypoints remain:

```text
lck delivery prepare <issue>
lck delivery complete <issue>
lck review prepare <issue-or-pr>
lck review complete <review-id>
lck remediation prepare <issue>
lck remediation complete <issue>
lck merge preflight <issue-or-pr>
lck closeout <issue>
lck status <issue>
```

Exact CLI design is not frozen by this Charter.

The architectural requirement is:

> **LCK is invoked on demand. Each lifecycle operation derives one immutable input snapshot from current authority, and the next lifecycle operation reacquires fresh authority.**

---

## 10. Fact Authority Model

### 10.1 Authoritative durable state

The authoritative durable sources are:

- Git commits and refs;
- authoritative remote Git refs queried from the remote;
- GitHub Issue state;
- GitHub PR state;
- GitHub current PR head/base;
- GitHub checks;
- GitHub review state;
- GitHub merge state;
- Task contract;
- repository-controlled workflow definitions.

Local remote-tracking refs such as `refs/remotes/origin/main` MAY be used as diagnostics or local materialization state, but MUST NOT silently replace an authoritative remote query when current remote identity is required.

Observation of remote authority SHOULD be read-only when no local materialization is required. A resolver MUST NOT mutate local Git state merely to learn what the remote currently contains.

### 10.2 Operation Snapshot Isolation

Every lifecycle operation MUST acquire a **phase-specific complete Operation Snapshot** before it makes a lifecycle decision or begins expensive / effectful work.

The snapshot contains all authoritative Git / GitHub input facts that the operation is expected to need, for example:

```text
operation identity
Task identity / contract / contract hash
local repository identity and relevant local refs
current authoritative remote refs
current PR identity / head / base
required checks and current check results
phase-specific relationship / merge / project facts
acquisition metadata and source diagnostics
```

The exact fact set is phase-specific. LCK MUST NOT query facts belonging only to hypothetical future phases.

After the snapshot is frozen:

- authoritative input facts are immutable for that operation;
- downstream helpers MUST consume the snapshot rather than call the live-state resolver again;
- the operation MUST NOT lazily fetch previously omitted authoritative facts later in the workflow;
- external changes MUST NOT mutate the in-flight operation's target;
- freshness is evaluated at the next lifecycle transition through a new snapshot.

An Operation Snapshot is an input-consistency boundary, not a claim that Git and GitHub provide a single transactional read across all APIs.

### 10.3 Diagnostic durable state

LCK MAY persist diagnostic evidence such as:

- run ID;
- timestamps;
- Kernel / Runner version;
- workflow source/version/hash;
- operation snapshot hash / acquisition metadata;
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

> **What is currently authorized for a new lifecycle operation?**

### 10.4 Operation-owned continuation state

Within one bounded operation, LCK MAY persist operation-owned state required to survive process interruption, such as:

```text
operation ID
sealed Operation Snapshot
owned workspace / worktree path
in-flight guard / lease metadata
formal-validation evidence path
review handoff marker
```

This state MAY be durable enough to resume the same operation after an interrupted process or Agent handoff.

It MUST:

- be scoped to one operation;
- have explicit ownership and cleanup rules;
- be invalid as authority for the next lifecycle transition;
- never require generalized snapshot lineage.

### 10.5 Derived operation evidence

Evidence produced by the operation MAY grow incrementally after the authoritative snapshot is frozen, for example:

```text
formal validation result
Critical Outcome result
Safe Effect receipt
semantic Review findings
Review verdict
cleanup result
```

This derived evidence does not change the authoritative input snapshot.

### 10.6 Forbidden state category

LCK v1 MUST NOT create:

> **durable derived authoritative state across lifecycle operations**

Examples include:

- durable "expected head SHA" used by a later phase as current authority;
- authoritative cross-phase fact handoff;
- durable freshness contract;
- snapshot lineage required for recovery;
- generic workflow drift state;
- lazy authoritative fact caches that are incrementally filled during an operation and then reused as if they represented one consistent live state.

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

LCK v1 uses on-demand operations and MUST NOT require a resident service.

### P6 — Live facts are authority

Current Git / GitHub state is authoritative for mechanical identity and lifecycle state at the start of a new lifecycle operation.

### P7 — Freshness at operation boundaries

Every new lifecycle operation MUST reacquire the authoritative facts required by that operation rather than trust a prior operation snapshot.

Freshness belongs to lifecycle transition boundaries, not to repeated refresh inside an already-running operation.

### P8 — Operation Snapshot Isolation

Each lifecycle operation MUST acquire its required authoritative input facts once, freeze them as an immutable Operation Snapshot, and use that snapshot throughout the operation.

Downstream helpers MUST NOT silently reacquire live authority after snapshot freeze.

Operation-local identity guards and sealed continuation state are allowed to prevent TOCTOU and support interruption recovery for the same operation.

### P9 — Audit is not authority

Historical evidence may be durable but MUST NOT authorize future lifecycle operations.

### P10 — Static authority, dynamic eligibility

A phase has statically declared capabilities.

Whether a capability can execute now depends on the fresh Operation Snapshot acquired at that operation boundary.

### P11 — Bounded Safe Effects

LCK side effects MUST be narrow, typed, deterministic, auditable, and explicitly verified.

Post-effect verification MUST query only the facts necessary to prove the postcondition of the effect LCK just caused. It MUST NOT trigger an unrelated full-state re-resolution.

### P12 — Runtime owns actionable identity

Agent-provided branch, remote, SHA, PR number, push refspec, or equivalent actionable identity MUST NOT be trusted as execution authority.

### P13 — Critical Outcome is a first-class Task contract requirement

The target workflow MUST support a Task-level Critical Outcome that proves the real supported caller reaches the intended capability and produces an observable result.

Implementing Critical Outcome requires synchronized changes to both:

- the Task / Issue body contract or template;
- deterministic validation / gate execution.

LCK design MUST NOT forget the Task-side contract change.

### P14 — Fail closed on ambiguity

If the deterministic resolver cannot construct one complete, unambiguous Operation Snapshot for the requested lifecycle operation, the workflow MUST STOP before expensive or effectful work begins.

### P15 — Review FAIL means STOP

Review FAIL MUST NOT automatically start remediation.

A new remediation invocation requires explicit Human intent.

### P16 — Human merge remains

LCK v1 retains manual Squash Merge.

### P17 — Business Delivery is separate from Cleanup

Successful merge may complete business delivery even when cleanup is still pending.

### P18 — Recovery from reality

A new recovery operation derives current state from Git/GitHub authority, not old workflow lineage.

An interrupted in-flight operation MAY resume from its own sealed operation snapshot and ownership guard, but that state cannot authorize a later operation.

### P19 — Provider-neutral control

Codex and Claude use the same LCK lifecycle contract.

Provider differences remain in the semantic Agent layer.

### P20 — Complexity must decrease

The final LCK migration MUST reduce or contain control-plane complexity.

Repeated nested resolution, hidden live queries, full-state refresh loops, and polling-based eligibility logic are control-plane complexity and SHOULD be removed.

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

Every LCK write operation begins from a frozen Operation Snapshot. Each Safe Effect MUST follow:

```text
Operation Snapshot already frozen
        ↓
validate effect-specific preconditions against that snapshot
        ↓
perform ONE bounded side effect
        ↓
query ONLY the authoritative fact(s) needed to prove this effect's postcondition
        ↓
verify exact postcondition
        ↓
return structured Effect Receipt
```

Examples:

```text
push exact commit H to remote Task ref
        ↓
git ls-remote exact Task ref
        ↓
verify remote ref == H
```

```text
create or update PR P
        ↓
query PR P identity
        ↓
verify expected head/base/state
```

A Safe Effect postcondition query is not a new lifecycle snapshot and MUST NOT be used as an excuse to re-resolve unrelated Task / PR / branch / relationship facts.

If the targeted postcondition reveals ambiguity, conflict, or an unexpected external change, the current operation MUST STOP. A subsequent lifecycle operation will acquire a fresh snapshot.

Every Safe Effect MUST have:

- a clear purpose;
- a narrow input contract;
- no Agent-controlled actionable identity;
- explicit preconditions derived from the current Operation Snapshot;
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

### LCK Delivery Prepare

`Delivery Prepare` is one lifecycle operation.

LCK:

1. acquires one complete `DeliveryPrepareSnapshot` containing the Task, current local repository facts, authoritative remote main / Task refs, current PR facts, and other Prepare-specific eligibility inputs;
2. freezes that snapshot;
3. validates lifecycle eligibility;
4. prepares or restores the correct Task workspace;
5. verifies only the local postconditions of workspace preparation;
6. returns a deterministic Delivery context / prepare receipt.

LCK MUST NOT repeatedly resolve Git/GitHub state while preparing the workspace.

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

LCK does not continuously refresh Git/GitHub while the Agent implements. External changes are evaluated when the next lifecycle operation begins.

### LCK Delivery Completion

`Delivery Complete` is a **new lifecycle operation** and therefore starts from a fresh `DeliveryCompleteSnapshot`.

LCK:

1. acquires and freezes all authoritative facts required for Delivery completion;
2. compares the fresh operation inputs with the permitted Delivery context / Task contract as required;
3. executes Critical Outcome;
4. runs formal validation;
5. verifies allowed scope / validated tree;
6. commits the validated tree;
7. verifies the commit effect locally;
8. ensures the remote Task branch and verifies only that remote-ref postcondition;
9. ensures the current OPEN PR and verifies only that PR effect's postcondition;
10. emits a Delivery receipt built from the frozen snapshot, validation evidence, and Safe Effect receipts;
11. returns `READY_FOR_REVIEW`;
12. stops.

Delivery Completion MUST NOT perform a final full Git/GitHub re-resolution merely to "refresh" facts it already acquired at operation start. Any unexpected targeted postcondition failure causes STOP; the next invocation will reacquire fresh authority.

Delivery MUST end at a Human boundary before Independent Review.

---

## 16. Independent Review Contract

Independent Review MUST be a separate semantic workflow operation with **snapshot isolation**.

### Review Snapshot acquisition

Before creating a review workspace, running formal validation, or starting semantic review, LCK MUST acquire one complete immutable `ReviewSnapshot` containing every authoritative fact required by that Review, including as applicable:

- repository identity;
- Task identity, state, labels, Task contract, and Task contract hash;
- authoritative remote main and Task branch identity;
- the unique current OPEN PR;
- exact PR head / base / branch identities;
- current required checks configuration;
- current check results bound to the exact reviewed head;
- Review-specific relationship / eligibility facts.

If any required fact cannot be acquired unambiguously, Review MUST STOP **before** expensive validation or review work begins.

After the `ReviewSnapshot` is frozen:

- no authoritative Git/GitHub fact is reacquired for that in-flight Review;
- no downstream Review helper may call the full live-state resolver;
- no checks polling or phase-internal refresh loop is allowed;
- the exact Task contract, PR, base, and head being reviewed remain immutable Review inputs.

LCK then:

1. prepares a clean isolated review context for the exact snapshot head;
2. runs deterministic applicable validation against that immutable target;
3. persists validation evidence;
4. invokes or enables a fresh semantic Review Agent.

The Review Agent:

- independently inspects the effective change bound to the `ReviewSnapshot`;
- evaluates Task requirements;
- evaluates Critical Outcome evidence included in or bound to that snapshot / validation evidence;
- returns PASS / FAIL with findings.

If Review uses separate `review prepare` and `review complete` process invocations, the sealed `ReviewSnapshot` and owned review workspace are operation-owned continuation state. `review complete` MUST consume them and MUST NOT query current Git/GitHub state as new authority.

### External changes during Review

External Git/GitHub changes occurring after Review Snapshot acquisition do **not** mutate the in-flight Review.

For example, if Review begins on head `A` and another actor later pushes head `B`, the Review may still complete and produce:

```text
ReviewReceipt
  reviewed_head = A
  reviewed_task_contract = T
  verdict = PASS | FAIL
```

The resulting receipt is exact historical evidence for `A` / `T`. Whether it is still applicable is evaluated by the **next lifecycle transition**, especially Merge Preflight, which acquires a fresh snapshot.

Therefore LCK MUST NOT perform a final `Review` live-state refresh solely to detect:

```text
PR head changed during review
base changed during review
Task body changed during review
checks changed during review
```

Those conditions become **receipt-staleness conditions at the next transition**, not reasons to mix multiple live-state times inside one Review.

### PASS

Produces a Review receipt for the exact frozen Review Snapshot and returns readiness for a later Human merge decision / Merge Preflight.

### FAIL

Returns `STOP_REQUIRED` and publishes / persists findings bound to the exact Review Snapshot.

No automatic remediation occurs.

---

## 17. Remediation Contract

Remediation starts only through explicit Human intent after Review FAIL.

### Remediation Prepare

`Remediation Prepare` is a new lifecycle operation and acquires one fresh `RemediationPrepareSnapshot` containing:

- Task;
- current OPEN PR;
- current PR head/base;
- current workspace state;
- the relevant failed Review receipt / findings as historical evidence.

The fresh snapshot determines whether those findings are still applicable to the current target. Once frozen, Remediation Prepare does not repeatedly refresh live authority.

The Implementation Agent:

- understands the findings;
- changes the implementation;
- diagnoses issues;
- completes semantic repair.

### Remediation Complete

`Remediation Complete` is another new lifecycle operation and therefore acquires a fresh `RemediationCompleteSnapshot`.

LCK then:

- evaluates current Task / PR / workspace identity from that snapshot;
- executes Critical Outcome;
- runs formal validation;
- commits the validated repair;
- verifies the commit effect;
- ensures the remote branch and verifies only the remote-ref postcondition;
- updates or reuses the current OPEN PR and verifies only the PR postcondition;
- emits a Remediation receipt;
- stops at the next Independent Review boundary.

LCK MUST NOT perform a final full-state refresh after these effects. Unexpected effect postconditions cause STOP; the next invocation reacquires current authority.

Remediation MUST NOT automatically trigger Review.

---

## 18. Merge Contract

LCK v1 retains:

> **Manual Squash Merge**

Merge Preflight is a **new lifecycle operation** and MUST acquire a fresh `MergeSnapshot`.

The snapshot MAY include:

- correct current PR;
- current Task contract / relevant Task state;
- current head;
- current base;
- current required checks and results;
- mergeability;
- the applicable Review receipt / verdict as historical evidence;
- unresolved blocking conditions.

Merge Preflight compares the fresh current identity with the exact identity recorded in the Review receipt.

Examples:

```text
current PR head != reviewed head
→ REVIEW_STALE_HEAD
→ STOP and require fresh Review

current Task contract hash != reviewed Task contract hash
→ REVIEW_STALE_CONTRACT
→ STOP and require fresh Review

current relevant base != reviewed base
→ REVIEW_STALE_BASE
→ STOP and require fresh Review
```

This is the correct freshness boundary for Review applicability.

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

`Closeout` is a new lifecycle operation and MUST acquire one fresh `CloseoutSnapshot` before deciding business delivery or cleanup actions.

The snapshot may include:

- current Task state;
- authoritative merged PR identity;
- merge commit / merged head identity;
- authoritative main identity;
- project / metadata state;
- relevant local / remote cleanup facts.

### Business Delivery

If the frozen `CloseoutSnapshot` proves the intended PR has been successfully merged and the Task delivery contract is satisfied:

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

Each cleanup Safe Effect verifies only its own targeted postcondition. Closeout MUST NOT run the full live-state resolver again after each cleanup action.

A cleanup failure MUST NOT retroactively convert a successfully merged business delivery into business failure.

Cleanup operations SHOULD be idempotent and retryable. A later Closeout / cleanup retry begins as a new operation with a fresh snapshot.

---

## 20. Recovery / State Resolution Model

Recovery is a cross-cutting deterministic capability, not a semantic lifecycle phase.

Each **new recovery operation** begins by asking:

> **What is true now?**

Not:

> **How does current state differ from an old cross-phase workflow snapshot?**

The Recovery resolver acquires one fresh `RecoverySnapshot`, freezes it, and selects the unique safe deterministic action or STOP. It MUST NOT repeatedly re-resolve the entire world during that same recovery operation.

An interrupted in-flight operation is a special case. If LCK has an operation-owned sealed snapshot / guard and the operation can be mechanically proven resumable, LCK MAY resume the **same** operation from that sealed input contract. This is not cross-phase authority. If ownership or resumability is ambiguous, abandon / clean up safely where possible and start a new lifecycle operation from fresh authority.

Examples:

| Current facts at a new operation boundary | LCK behavior |
|---|---|
| local Task branch exists, remote absent | validate current operation eligibility and run `ensure_remote_branch` if permitted |
| remote branch exists, OPEN PR absent | `ensure_open_pr` |
| historical CLOSED PR exists | ignore it for active PR resolution |
| exactly one matching OPEN PR exists | use it |
| multiple active matching PRs | STOP |
| remote branch diverged | STOP |
| PR already merged | Business Delivery becomes COMPLETE |
| merge removed remote branch | treat missing remote branch as normal post-merge state |
| cleanup incomplete | Cleanup = PENDING |
| current MergeSnapshot head differs from reviewed head | Review receipt is stale; STOP |
| Review operation was interrupted after snapshot/workspace seal | resume the same owned Review target if resumability is mechanically proven; otherwise STOP / abandon safely |
| facts do not produce one safe deterministic action | STOP and surface exact unavailable / ambiguous facts to Human |

Recovery MUST NOT depend on:

- restoring a previous Kernel process;
- cross-phase snapshot lineage;
- provenance chains used as current authority;
- generalized drift graphs;
- repeatedly refreshing live state inside one recovery operation.

---

## 21. Retry Policy

Retries MUST be classified and MUST preserve Operation Snapshot Isolation.

| Failure type | Default LCK behavior |
|---|---|
| transient HTTP/network failure during authoritative snapshot acquisition | bounded retry of the same idempotent fact query is allowed **before snapshot freeze** |
| required authoritative fact still unavailable after bounded acquisition retry | STOP before expensive/effectful work |
| CI / required checks are pending at snapshot acquisition | STOP / WAIT boundary; start a fresh operation later; no in-operation polling loop |
| targeted Safe Effect postcondition query has a transient HTTP/network failure | bounded retry of that exact postcondition query allowed |
| targeted Safe Effect postcondition reveals state conflict / unexpected external change | STOP; do not full re-resolve; next lifecycle operation reacquires fresh authority |
| deterministic validation failure | persist diagnostic evidence and return failure to semantic implementation/remediation flow |
| Critical Outcome failure | Delivery blocked until semantic repair |
| Review FAIL | zero automatic remediation |
| identity ambiguity | zero Agent retry; STOP |
| remote divergence | STOP |
| merge conflict requiring semantic resolution | STOP / explicit remediation |
| cleanup failure | bounded idempotent retry; Business Delivery remains COMPLETE if already merged |

A retry MUST NOT turn into a hidden second full `LiveStateResolver` pass after an Operation Snapshot has been frozen.

---

## 22. Observability and Audit

LCK MUST retain enough evidence to explain what happened without turning evidence into workflow authority.

Audit records MAY include:

- Task;
- operation / invocation IDs;
- phase;
- LCK / Runner version;
- workflow version/hash;
- semantic engine identity/version;
- start and end timestamps;
- Operation Snapshot hash / identity;
- snapshot acquisition start/end timestamps;
- authoritative source / command identifiers used during acquisition;
- observed input facts;
- operation-owned workspace / guard identity;
- validation results;
- Critical Outcome results;
- Safe Effect requests and targeted postcondition receipts;
- review verdict;
- Human boundary actions;
- failure classification and bounded diagnostics.

Rule:

> **Audit log and historical Operation Snapshots explain what happened. They do not authorize a new lifecycle operation.**

A later operation MUST reacquire current authority even when a prior snapshot or receipt is available for comparison.

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
0 phase-internal full-state refresh loops
0 hidden downstream live-state resolver calls after snapshot freeze
0 lazy incremental authoritative fact caches inside an operation
0 background polling loops for checks eligibility
```

### Design rule

For every lifecycle operation, first ask:

> **What authoritative facts does this operation actually require?**

Acquire that phase-specific fact set once at operation start, freeze it, and pass the resulting snapshot downward.

For every proposed post-effect query, ask:

> **What exact authoritative fact proves the effect I just caused?**

Query only that postcondition. Do not use a full live-state refresh as a generic verification mechanism.

### Change discipline

A small workflow bug fix SHOULD default to:

```text
0 new durable cross-operation control-state fields
0 new provenance concepts
0 new hidden Resolver entrypoints
```

New write capability SHOULD normally be introduced as one explicit bounded Safe Effect at a time.

The migration SHOULD aim for net deletion or simplification of:

- repeated nested `resolver.resolve()` calls;
- snapshot authority across lifecycle operations;
- expected-SHA plumbing used as current authority;
- handoff freshness logic;
- generic drift categories;
- phase-internal check polling;
- full-state post-effect refreshes;
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
→ make operation entry acquire one phase-specific snapshot
→ pass immutable snapshot to downstream deterministic helpers
→ move bounded lifecycle effects into LCK
→ verify each effect with targeted postconditions
→ simplify Skill
→ delete obsolete repeated-resolution / snapshot-handoff / control logic
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
| Evidence / Fact Runner | KEEP + REORGANIZE into operation-start snapshot acquisition |
| Git/GitHub write mechanics in Skills | MOVE to LCK Safe Effects |
| repeated / nested `resolver.resolve()` inside helpers | REMOVE; helpers consume Operation Snapshot |
| live remote observation implemented through broad local `git fetch --prune` | REMOVE where observation-only; prefer read-only authoritative remote query |
| full-state re-resolution after a bounded effect | REPLACE with targeted postcondition verification |
| phase-internal CI/check polling | REMOVE; STOP and use a fresh later invocation |
| cross-phase trusted snapshot authority | REMOVE |
| bounded verified fact handoff as workflow authority | REMOVE |
| generic freshness / drift framework | REMOVE or sharply narrow to operation boundary receipt-staleness checks |
| dynamic global write authorization | REPLACE with static phase capability + operation snapshot precondition |
| automatic review/repair loop | REMOVE |
| manual Squash Merge | KEEP |
| monolithic closeout success state | REPLACE with Business Delivery + Cleanup |

Migration tests SHOULD verify snapshot acquisition boundaries and absence of hidden live queries without requiring a large synthetic workflow simulator.

---

## 26. Architecture Acceptance Criteria

Before LCK v1 implementation is considered architecturally complete, the design and implementation MUST demonstrate all of the following:

1. The user can still start work through a simple Codex / Claude window instruction.
2. Codex and Claude can use the same lifecycle contract.
3. Agent sessions may change without invalidating workflow correctness.
4. Skills no longer implement lifecycle mechanics as interpreted state machines.
5. LCK can reconstruct current workflow state from live Git/GitHub authority at each new lifecycle operation boundary.
6. Cross-phase authoritative snapshots are not required.
7. Branch / SHA / PR actionable identity is resolved by LCK.
8. Agent cannot directly own commit / push / PR mutation / lifecycle transition.
9. Delivery ends at a Human boundary before Independent Review.
10. Independent Review uses a fresh review role and a single immutable Review Snapshot.
11. Review FAIL always stops.
12. Remediation requires explicit Human intent.
13. Human Squash Merge remains mandatory in v1.
14. Closeout distinguishes Business Delivery from Cleanup.
15. Recovery does not require an old Kernel process or cross-phase snapshot lineage.
16. Historical CLOSED PRs do not incorrectly block current active PR resolution.
17. Normal lifecycle evolution is not generalized into "drift".
18. Critical Outcome is part of the target Task contract and is enforced as a Delivery gate.
19. Task Issue/template changes required for Critical Outcome are implemented together with the corresponding deterministic validation support.
20. LCK does not require a workflow database or daemon.
21. Control-plane complexity is demonstrably reduced or contained versus the current baseline.
22. Current Runner infrastructure is reused rather than duplicated by a parallel runtime.
23. Every new lifecycle operation has one explicit authoritative snapshot-acquisition boundary before expensive or effectful work.
24. After snapshot freeze, downstream helpers do not reacquire authoritative Git/GitHub state or invoke hidden full-state resolution.
25. Operation Snapshot facts are phase-specific and complete for that operation; authoritative facts are not lazily added later.
26. Independent Review performs no authoritative Git/GitHub refresh after Review Snapshot freeze; external changes do not mutate the in-flight review target.
27. Merge Preflight acquires fresh authority and rejects a Review receipt whose Task contract / PR / head / relevant base identity is stale.
28. Safe Effects verify their own targeted postconditions and do not trigger unrelated full-state re-resolution.
29. Pending asynchronous eligibility such as CI checks causes a STOP / WAIT boundary and a later fresh invocation rather than an in-operation polling loop.
30. Operation-owned sealed snapshots / guards may resume the same interrupted operation but cannot authorize later lifecycle operations.
31. Observation-only remote fact acquisition does not require broad mutation of local Git metadata when a read-only authoritative remote query is sufficient.
32. Failure reports preserve the exact unavailable / ambiguous fact or failed query instead of collapsing unrelated failures into a generic unresolved-state message.

---

## 27. Design Freeze Summary

The following decisions are considered the current LCK v1 design baseline.

### Frozen

- Name: **Local Control Kernel Workflow v1 (LCK v1)**.
- Full-lifecycle responsibility model is the authoritative responsibility model.
- Codex / Claude remain the interactive semantic engines.
- Skills remain but cease to be lifecycle controllers.
- LCK evolves from existing Runner infrastructure.
- LCK uses on-demand operations, not a daemon.
- Git/GitHub live state is authoritative for mechanical facts at new lifecycle operation boundaries.
- **Operation Snapshot Isolation** is the lifecycle-wide state model.
- Each new lifecycle operation acquires one phase-specific complete authoritative snapshot and freezes it before expensive/effectful work.
- Authoritative input facts are not reacquired or lazily added after snapshot freeze.
- Downstream helpers consume the Operation Snapshot and do not own hidden live-state resolution.
- Freshness is checked at the next lifecycle transition, not by repeatedly refreshing an in-flight operation.
- Independent Review stays bound to one immutable Review Snapshot; Merge Preflight detects stale Review receipts against fresh authority.
- Operation-owned sealed snapshots / guards are allowed only to resume the same operation and are not cross-phase authority.
- Audit evidence is not workflow authority.
- Phase authority is static; operation eligibility is dynamic from the fresh operation snapshot.
- LCK uses bounded Safe Effects.
- Safe Effects use targeted postcondition verification rather than full-state refresh.
- Observation-only remote state resolution should be read-only and should not mutate local Git merely to learn current remote facts.
- Pending asynchronous eligibility stops the current operation; LCK does not poll inside the operation waiting for the world to change.
- Review FAIL stops.
- Remediation requires explicit Human intent.
- Manual Squash Merge remains.
- Business Delivery and Cleanup are separate.
- Recovery derives new-operation state from current reality.
- Critical Outcome is part of the target workflow and requires synchronized Task-contract changes.
- LCK success requires correctness improvement and control-complexity reduction.

### Not yet frozen

The following remain implementation design questions:

- exact CLI command names;
- exact module/file layout;
- exact structured output schemas;
- exact Operation Snapshot dataclass / schema layout;
- exact Safe Effect inventory;
- exact Task Critical Outcome syntax;
- exact Task template migration strategy;
- exact Runner refactor sequence;
- exact lifecycle metadata naming;
- exact audit artifact format;
- exact implementation mechanism for same-operation interruption guards / leases.

These MUST be decided later without violating the frozen architectural principles above.

---

## 28. Guiding Principle

The design can be summarized as:

> **Keep intelligence in the Agent.
> Keep authority in deterministic control.
> Keep truth in Git and GitHub.
> Acquire that truth once per lifecycle operation.
> Freeze authoritative inputs while the operation runs.
> Verify only the effects the operation itself caused.
> Refresh truth at the next lifecycle boundary.
> Keep the Human at irreversible boundaries.
> Keep the control plane small.**

---
