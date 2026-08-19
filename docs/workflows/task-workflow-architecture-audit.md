# Task Workflow execution architecture audit

Task: [#88](https://github.com/PhoenixSss/tracequant/issues/88)
Title: `[Task] 审计 Task Workflow 执行架构并定义 Agent / Skill / Runner 目标职责边界`

Status: architecture/audit design only. This document does not change Runtime
Workflow behavior, Runner coverage, command policy, sandbox policy, or tests.

## 1. Audit boundary and evidence status

The current leaf Issue body is the only business specification for this audit.
The mechanical admission facts were locked by the Delivery Evidence Runner:

| Fact | Locked value |
|---|---|
| Task | #88, OPEN, `type:task`, `codex:ready` |
| Project Status at admission | `Ready` |
| Parent | #78, OPEN |
| Main / task base | `255254ebbf707e45c3f3b12c80f42e70a5493b90` |
| Delivery-start snapshot | `ev-6206ebae41790139` |
| Implementation snapshot | `ev-fba94d2cdf0a5839` |
| Current Issue body hash | `f7c36c405bf85793d596d2fc22d470411bc0fc2baee97a46e0e8a61be5836c25` |

The source hierarchy used here is deliberately narrow:

1. current Issue #88 body;
2. `AGENTS.md`, current shared lifecycle/review docs, and current policies;
3. active Codex and Claude Skills;
4. fixed Evidence/Validation Runners, helper contracts, profiles, Rules, and
   their tests;
5. explicitly referenced #86 material only where it provides evidence status.

Task #86's current body is a protocol/specification for a future A → B → C → D
merge-pre benchmark. Its formal identities and results are still `TBD AT
FREEZE`; the repository material register also records the #86 runtime result
as a placeholder. No authoritative A/C1/C2 result bundle was available in the
current repository or the explicitly referenced GitHub source. Therefore all
claims about Guardian admissions, runtime Token cost, command granularity,
Agent grouping, sandbox retries, and A/C1/C2 causal effects are marked
`EVIDENCE_UNAVAILABLE` below. No historical implementation, old PR diff, or
BEFORE/AFTER/Revert experiment was used to fill that gap.

The following terms are used consistently:

- `Observed`: directly present in current code, docs, Skill, Runner, test, or
  locked snapshot.
- `Specified frequency`: the Skill requires the operation at that phase, but
  this repository has no runtime frequency measurement.
- `EVIDENCE_UNAVAILABLE`: the claim needs #86/rollout evidence that is not
  available here; it is not a zero and not a pass.
- `Runner candidate`: a design disposition for a later Task, not an
  implementation authorization for #88.

## 2. Current Workflow execution map

The current GitHub-native flow is:

```text
leaf Issue body + Retrieval v2
        │
        ▼
Delivery-start Evidence Runner
        │  readiness / identity / blocker / lifecycle gates
        ▼
Skill lifecycle write: Ready → In Progress
        │
        ▼
Task branch → Agent implementation → targeted Validation Runner
        │
        ▼
clean committed head → workflow-delivery Validation Runner
        │
        ▼
push → PR resolve/create helper → checks → Agent semantic self-review
        │
        ▼
delivery-readiness Evidence Runner → Review handoff
        │
        ▼
fresh Independent Review Skill
  ├─ review Evidence Runner (locked PR/base/head/diff/facts)
  ├─ complete Agent semantic inspection and evidence matrix
  ├─ workflow-review Validation Runner
  └─ recheck Evidence Runner → one verdict / bounded handoff
        │
        ├─ non-PASS: Delivery remediation admission → new head → fresh Review
        │
        └─ PASS: maintainer manual Squash Merge checkpoint
                │
                ▼
          Closeout read-only plan → synchronize main → post-merge validation
          → exact lifecycle convergence → exact branch cleanup → recheck
                │
                ▼
          separate Feature completion audit/handoff
```

Important current-state observations:

- The leaf Issue body remains semantic authority; Runner snapshots do not
  replace the Agent's reading of the current specification.
- `AGENTS.md` and the shared development docs are semantic owners for Retrieval
  v2, lifecycle, Human Gate, Review independence, head invalidation, and manual
  Merge boundaries. Skills contain executable procedure and adapter guidance.
- The fixed Evidence Runner covers Delivery, PR readiness, Review snapshot,
  Closeout plan, and supported rechecks. It performs bounded read-only Git/GitHub
  acquisition and local ignored artifact writes.
- The fixed Validation Runner covers targeted profiles and CI-equivalent
  Delivery/Review/Closeout profiles. It executes the canonical CI command set,
  checks CI drift, enforces phase preconditions, bounds/redacts logs, and can
  run Skill validators.
- `pr_resolve.py` is a deterministic shared helper, not a Runner. It owns the
  one-query resolve/create path and exact PR identity check; it does not own
  semantic readiness or review correctness.
- `self_review.py` is a deterministic schema/hash/staleness helper, not a
  semantic reviewer and not currently a fixed Evidence Runner profile.
- Feature audit code exists in the internal evidence/validation implementations
  and is tested, but the current fixed WSL2 front doors do not expose a
  `feature-audit` Evidence/Validation profile. This is a coverage/contract gap,
  not a reason to invoke the internal implementation directly from #88.

## 3. Current operation inventory

The inventory is split into two tables so every operation retains the full
Issue-required field set without hiding the failure or recovery semantics.
`Oxx` IDs are stable within this audit.

### 3.1 Ownership and target disposition

| ID | Phase | Operation | Current owner | Target owner | Disposition |
|---|---|---|---|---|---|
| O01 | Entry | Resolve natural-language intent and exact Task | Agent + Skill routing | Skill routing; Agent confirms intent | KEEP_IN_SKILL |
| O02 | Delivery | Delivery admission / readiness preflight | Evidence Runner, invoked by Skill | Evidence Runner; Skill owns admission response | KEEP_IN_RUNNER |
| O03 | Delivery | Ready → In Progress lifecycle transition | Skill / project helper | Skill, with exact object binding | KEEP_IN_SKILL |
| O04 | Delivery | Read current leaf spec and Retrieval v2 context | Agent under Skill guidance | Agent; Skill supplies procedure | KEEP_IN_AGENT |
| O05 | Delivery | Triggered Parent/dependency/doc/ADR expansion | Agent semantic judgement | Agent + Human Gate when unresolved | KEEP_IN_AGENT |
| O06 | Delivery | Design and implement scoped change | Agent | Agent | KEEP_IN_AGENT |
| O07 | Delivery | Targeted validation selection and execution | Skill selects; Validation Runner executes | Skill selects; Runner executes | KEEP_IN_RUNNER |
| O08 | Delivery | Commit scope, clean-head and base/head binding | Agent/Skill procedure + Git | Skill authorizes; Runner validates fixed gates | KEEP_IN_SKILL |
| O09 | Delivery | CI-equivalent validation and Skill validators | Validation Runner | Validation Runner | KEEP_IN_RUNNER |
| O10 | Delivery | Push validated head | Skill / Agent invokes Git | Skill retains side-effect authorization | KEEP_IN_SKILL |
| O11 | Delivery | PR resolve/create and exact identity verification | `pr_resolve.py` helper + Skill | helper/Runner boundary remains explicit | KEEP_IN_SKILL |
| O12 | Delivery | PR checks wait/read and readiness interpretation | Skill reads facts; Runner snapshots facts | Runner facts; Skill decides whether to stop | KEEP_IN_RUNNER |
| O13 | Delivery | Semantic self-review content and acceptance mapping | Agent; `self_review.py` validates structure | Agent for judgement; deterministic binder candidate | KEEP_IN_AGENT |
| O14 | Delivery | Delivery readiness snapshot | Evidence Runner, invoked by Skill | Evidence Runner | KEEP_IN_RUNNER |
| O15 | Review | Lock reviewed Task/PR/base/head/effective diff | Review Skill + Evidence Runner | Evidence Runner locks facts; Skill preserves contract | KEEP_IN_RUNNER |
| O16 | Review | Read complete effective diff and relevant context | Independent Review Agent | Independent Review Agent | KEEP_IN_AGENT |
| O17 | Review | Review evidence matrix, correctness, AC/risk judgement | Independent Review Agent | Independent Review Agent | KEEP_IN_AGENT |
| O18 | Review | Review CI-equivalent validation | Review Skill + Validation Runner | Validation Runner | KEEP_IN_RUNNER |
| O19 | Review | Recheck identity, checks, threads, and stability | Evidence Runner, invoked by Review Skill | Evidence Runner | KEEP_IN_RUNNER |
| O20 | Review | Final semantic verdict and bounded handoff | Independent Review Agent + Skill procedure | Agent verdict; Skill formats bounded handoff | KEEP_IN_AGENT |
| O21 | Remediation | Validate handoff and reviewed-head admission | Delivery Skill + Evidence Runner | Skill semantic admission; Runner identity gates | KEEP_IN_SKILL |
| O22 | Remediation | Repair, final validation, new head | Agent + Delivery Skill + Runners | Same separation; old verdict invalidated | KEEP_IN_AGENT |
| O23 | Merge | Maintainer manual Squash Merge checkpoint | Maintainer | Maintainer | KEEP_IN_SKILL |
| O24 | Closeout | Read-only merge/main/Issue/PR/branch plan | Closeout Skill + Evidence Runner | Evidence Runner facts; Skill gate response | KEEP_IN_RUNNER |
| O25 | Closeout | Synchronize main and run post-merge validation | Closeout Skill + Validation Runner | Skill authorizes sync; Runner validates | KEEP_IN_SKILL |
| O26 | Closeout | Project/label/Issue lifecycle convergence | Closeout Skill / GitHub | Closeout Skill; exact object only | KEEP_IN_SKILL |
| O27 | Closeout | Exact Task branch deletion | Closeout Skill after proof | Closeout Skill; no broad cleanup | KEEP_IN_SKILL |
| O28 | Recovery | Resume after valid prior gate / drift recheck | Skill + Agent judgement | Skill controls recovery; Agent resolves ambiguity | KEEP_IN_SKILL |
| O29 | Feature | Feature completion inventory and semantic completion verdict | Feature Audit Skill + Agent | Agent/Skill semantic audit; deterministic evidence candidate | MOVE_TO_RUNNER |
| O30 | Cross-cutting | Repeated manual re-query/reformat of already locked facts | Agent/Skill ad hoc behavior | Remove as a separate target operation; retain required recheck | REMOVE_FROM_TARGET_FLOW |
| O31 | Cross-cutting | Optional grouping of homogeneous Agent commands | Agent orchestration | Agent may batch only when independently safe | OPTIONAL_AGENT_BATCHING |
| O32 | Cross-cutting | Context compilation beyond Retrieval v2 | Not a current operation | No owner until evidence proves residual need | NEEDS_FURTHER_EXPERIMENT |

`O29` is the only `MOVE_TO_RUNNER` disposition in this document, and it is
limited to deterministic Feature identity/child-set/check/recheck evidence.
The Feature completion judgement itself remains Agent-owned. Moving this
mechanics subset requires a separate Task because the current fixed front door
does not expose the profile.

### 3.2 Required operation attributes

| ID | Inputs → outputs | Semantic judgement? | Deterministic inputs available? | External side effect | Failure semantics / recovery sensitivity | Frequency and observed cost |
|---|---|---|---|---|---|---|
| O01 | User intent + Task number → selected workflow | Yes when intent is ambiguous → route or Human Gate | Task number and current title | None | Do not guess; fail closed | Per invocation; runtime Guardian cost `EVIDENCE_UNAVAILABLE` |
| O02 | Task, locked main, local state, GitHub metadata → pass/stop snapshot | No for facts; Skill interprets disposition | Yes: Issue, labels, Project, parent/blocker, main/worktree | Ignored local artifact only | Any fail/partial/unknown is terminal admission; no auto-repair | Once per Delivery; #88 delivery-start had 12 runner operations; Guardian Token unavailable |
| O03 | Passed readiness + Task identity → Project `In Progress` | No, but exact lifecycle authorization matters | Status and Issue identity | GitHub Project write | Stop on write failure or state drift | Once per Delivery; runtime cost unavailable |
| O04 | Leaf body + applicable rules/docs → working specification | Yes | Current body and deterministic metadata are available; semantic text retrieval is not Runner-owned | None | Missing/ambiguous spec → bounded expansion/Human Gate | Per Delivery; context/Token cost unavailable |
| O05 | Explicit reference or ambiguity → minimum relevant source/evidence | Yes | Trigger and source identity can be deterministic; sufficiency cannot | None | Stop when unresolved; no recursive expansion | Triggered; frequency/cost unavailable |
| O06 | Spec + repository source/tests → implementation diff | Yes | Git scope can be checked; correctness cannot | Files and Git worktree | Repair only in Task scope; no silent expansion | Per Task; runtime cost unavailable |
| O07 | Changed scope + phase → targeted command result | No for command result; Skill chooses profile | Yes for named fixed profiles | Ignored validation artifacts | Bounded failure; inspect named command only | Development as needed; no A/C1/C2 measurements |
| O08 | Branch/base/head/worktree → commit-ready identity | Some semantic scope review | Mostly yes; commit intent and approved scope need Agent/Skill | Git commit | Clean committed head required for final gates; drift stops | Once before final validation; cost unavailable |
| O09 | Base SHA + current tree → CI-equivalent result + logs | No for commands; no business verdict | Yes: fixed command set and phase preconditions | Ignored validation artifacts | Any real failure blocks push/readiness | Once per candidate head; #86 runtime metrics unavailable |
| O10 | Validated head + exact branch → remote branch | No, authorization and scope still matter | Branch/head can be checked | Git remote write | Push only validated head; remote drift stops | Once per candidate head; cost unavailable |
| O11 | Branch/base/head + PR body → one exact PR identity | No for mechanics; body content is Agent/Skill-owned | Yes: bounded list/create/view identity fields | GitHub PR create or reuse | 0→create, 1→reuse, >1 or mismatch→fail closed | Once per Delivery; GitHub/tool cost unavailable |
| O12 | PR/check metadata → wait/read + readiness decision | Read facts no; whether to stop yes | PR checks and required-check classification available | None | Pending/failed/unknown/plan-limit states preserved | Per Delivery readiness; no polling measurement |
| O13 | Effective diff + ACs + validation → self-review JSON | Yes for status/findings/risk; hash/schema are deterministic | Partial: helper binds hashes and structure; fixed Runner profile absent | Ignored self-review artifact | New head makes artifact stale; incomplete evidence cannot be verified | Once per head; runtime cost unavailable |
| O14 | Task/PR/base/head/diff/checks/lifecycle → readiness snapshot | No for facts; Skill interprets | Yes for current fixed profile | Ignored local artifact | Drift, blocking thread, failed gate, or stale check stops | Once before handoff; cost unavailable |
| O15 | Task PR + expected base/head → locked review snapshot | No for identity facts | Yes: PR, diff digest, threads, checks, Issue metadata | Ignored local artifact | Identity mismatch or missing facts blocks Review | Once per Review session; #86 cost unavailable |
| O16 | Locked diff + Task body + relevant source → review evidence | Yes | Relevant source availability is deterministic; correctness is not | None | Incomplete reading cannot yield PASS | Once per Review; transcript/Guardian cost unavailable |
| O17 | Evidence matrix + ACs + code/tests → findings/verdict | Yes, by definition | No deterministic encoding of business correctness | Ignored evidence only | Blocking/High/Medium or missing evidence prevents PASS | Once per Review; no repository Token instrumentation |
| O18 | Reviewed base SHA + current head → Review validation | No for command result | Yes: fixed profile | Ignored validation artifacts | Validation failure/incomplete gate prevents unconditional PASS | Once per reviewed head; cost unavailable |
| O19 | Prior snapshot ID → current identity/check/thread comparison | No for comparison; drift policy is Skill/shared-doc-owned | Yes for locked snapshot schema | Ignored local artifact | Any head/base/diff change invalidates old verdict | Once before verdict; cost unavailable |
| O20 | Review evidence + gates → one verdict/handoff | Yes | Evidence fields available; semantic conclusion not | Ignored report/handoff | PASS / CONDITIONAL / FAIL; old verdict invalid after new head | Once per Review; #86 verdict evidence unavailable |
| O21 | Bounded handoff + PR identity → repair admission | Yes for handoff scope; Runner checks identity | Yes for PR/base/head/fixability | None before repair | Missing handoff or mismatch stops before writes | Per remediation; cost unavailable |
| O22 | In-scope finding → new commit/head + revalidation | Yes for repair | Git/validation facts deterministic | Git commit/push/PR edit | New head always requires fresh Review | Per remediation iteration; cost unavailable |
| O23 | Passing current-head Review + checks → maintainer decision | Yes and intentionally external to Agent/Skill | Current identity/check facts available | Manual GitHub merge | No passing Review or head mismatch blocks merge | At most once per Task; no #88 experiment |
| O24 | Reviewed head + merge SHA + PR/Issue/main/branch → closeout plan | No for facts | Yes for closeout-readonly profile | Ignored local artifact | Merge/closure/tree/branch proof incomplete → stop cleanup | Once after merge; runtime cost unavailable |
| O25 | Merge identity + origin/main → synced main + validation | No for validation; Skill owns safe sync | Yes for phase preconditions | Git fetch/fast-forward; local validation | No reset/force/bypass; failure stops metadata/cleanup | Once after merge; cost unavailable |
| O26 | Verified merge + current metadata → converged lifecycle | No for exact state; no Feature judgement | Status/labels/closure facts available | GitHub Project/label writes | Missing/contradictory state stops | Once after validation; cost unavailable |
| O27 | Exact merged Task branch + proof → branch absent | No for proof; deletion authority remains Skill | Branch tip/tree/worktree facts available | Remote/local branch deletion | Only exact verified Task branch; no broad cleanup | Once after convergence; cost unavailable |
| O28 | Prior snapshot/artifact + current state → resume or stop | Yes when ambiguity/conflict exists | Recheck can provide facts | May resume normal scoped writes | Drift/identity conflict → invalidation/Human Gate | Triggered; no recovery runtime measurement |
| O29 | Feature + direct children + audited main → audit evidence/verdict | Yes for integration/completion | Partial: internal implementation/tests exist; fixed front door absent | Ignored local evidence only | Evidence-insufficient verdict when facts/profile unavailable | Per Feature audit; cost unavailable |
| O30 | Existing snapshot facts → duplicate text/query → no new semantic value | No | Yes when same snapshot identity is locked | None | Remove only duplicate step; never remove mandatory recheck | Suspected from static procedures; runtime frequency unavailable |
| O31 | Independent safe commands → grouped invocation | No for each command; safety grouping can need judgement | Grouping cost/benefit unavailable | None beyond contained commands | Optional; never hide failure boundaries or semantic review | Candidate only; A/C1/C2 grouping evidence unavailable |
| O32 | Retrieval v2 context + measured residual cost → compiler decision | Yes | Residual runtime evidence unavailable | None | No implementation without evidence and separate Task | Not a current operation |

## 4. Target Workflow execution map

The target is a responsibility model, not a mandated number of sessions or
commands. Delivery, Independent Review, and Closeout remain semantically
distinct, while their top-level session strategy remains open for #89/#91.

```text
Canonical leaf spec / Retrieval v2
  → Skill selects phase and invokes one bounded mechanical gate
  → Agent performs semantic work only where understanding, correctness,
    risk, ambiguity, or Human Gate is required
  → Runner returns deterministic facts, validation, identity, and stability
  → Skill applies phase ordering, authorization, fail-closed, and side-effect
    boundaries
  → maintainer makes the manual Squash Merge decision
```

Target responsibilities:

| Layer | Target responsibility | Explicit non-responsibility |
|---|---|---|
| Agent | Read canonical spec; retrieve triggered context; implement; diagnose failures; write semantic self-review; independently judge Review correctness/AC/risk; decide ambiguity/conflict/Human Gate | No silent lifecycle shortcut; no Review verdict inheritance; no direct business-to-exchange authority; no claim from partial Runner evidence |
| Skill | Executable phase procedure; phase order; invoke correct Runner/profile; adapt Agent/Runner boundary; authorize exact Git/GitHub side effects; preserve fail-closed/Human Gate; format bounded handoff | Not a second shared semantic owner; not business correctness; not an alternative evidence source |
| Runner | Bounded Git/GitHub facts; object identity and drift; fixed validation orchestration; deterministic evidence/schema/hash; fixed preconditions; exact side effects only if separately authorized and object-bound | No business correctness; no semantic blocker classification; no Review verdict; no Feature completion judgement |
| Maintainer | Manual Squash Merge and final lifecycle decisions | No automatic merge inferred from Delivery or Review artifacts |

Target invariants retained without change:

- current leaf Issue body is the canonical work-item specification;
- Retrieval v2 remains the default/triggered retrieval contract;
- Independent Review retains semantic independence, strict read-only behavior,
  complete effective-diff inspection, and no Delivery semantic verdict
  inheritance;
- reviewed object identity binds Task/PR/base/head/effective diff and changed
  files; a new head or relevant drift invalidates the old semantic verdict;
- maintainer manual Squash Merge remains the only merge decision;
- post-merge validation, exact object/branch safety, fail-closed gates, and
  Human Gate remain mandatory.

These hard invariants do not choose a top-level session or isolation
implementation. #89 defines the Review trust/isolation/invalidation contract,
and #91 experimentally selects the final execution strategy; #88 does not
choose a fresh root or an alternative isolation design.

## 5. Current → Target delta

| Area | Current state | Target delta | Action in #88 |
|---|---|---|---|
| Shared semantic ownership | Shared docs own lifecycle/review semantics; Skills repeat executable details | Make the boundary explicit and prevent a second semantic owner | Document only |
| Delivery readiness | Fixed Delivery Runner already collects identity/readiness facts | Keep facts in Runner; keep interpretation and lifecycle response in Skill | No runtime change |
| Standard validation | Fixed Validation Runner already owns CI-equivalent command sequence and phase preconditions | Keep command plan and evidence in Runner; Skill only selects profile and handles result | No Runner expansion |
| PR readiness facts | Fixed profiles cover PR identity, checks, threads, diff digest, lifecycle, and recheck | Remove duplicate manual acquisition as a separate Agent operation when same snapshot is authoritative | Future procedure clarification only |
| Self-review mechanics | Agent writes semantic content; `self_review.py` validates structure, changed files, hashes, staleness | Move only deterministic binder/schema/hash finalization behind a future Runner boundary; keep content/findings Agent-owned | Candidate follow-up; no implementation |
| Review mechanics | Runner snapshot + Validation Runner + recheck; Agent owns evidence matrix and verdict | Preserve split; do not compile verdict or choose session isolation in #88 | No runtime change; #89/#91 remain owners |
| Closeout facts | Closeout Runner has detailed merge/tree/main/branch facts; Skill performs sync, metadata, and cleanup | Keep deterministic proof in Runner; keep exact side effects in Skill until a separate safety/experiment decision | No Closeout redesign |
| Feature audit coverage | Internal evidence/validation supports feature audit and tests cover it; fixed WSL2 front door/profile does not | Add a bounded fixed profile only if a later Task proves the interface and failure contract | `RUNNER_CANDIDATE_REQUIRED` |
| Duplicate fact formatting | Agent may restate/re-query facts while reports are assembled | Treat same-snapshot mechanical reformat/requery as no target operation; retain required final recheck | `REMOVE_FROM_TARGET_FLOW` as a design rule |
| Agent command batching | No runtime evidence in #88; homogeneous commands remain possible | Optional only after Runner coverage and safety boundaries are stable | `AGENT_BATCHING_CANDIDATE`; do not implement |
| Context Compiler | Retrieval v2 is current baseline; residual cost is not measured | No compiler is justified without measured residual context cost and a concrete contract | `CONTEXT_COMPILER_NOT_JUSTIFIED` |
| Sandbox/approval | Exact Runner and GitHub reads/writes can be blocked by environment route | Treat as environment/approval dimension, not architecture effect; collect comparable evidence later | `NEEDS_FURTHER_EXPERIMENT` |

## 6. Fixed Workflow Mechanics coverage audit

| Mechanics | Current coverage | Classification | Target disposition |
|---|---|---|---|
| Bounded Issue/relationship/main facts | Delivery Evidence Runner; content hash plus mechanical metadata | Runner fully covers fixed facts | KEEP_IN_RUNNER |
| Identity/readiness/drift gates | Delivery entry points, PR readiness, review, closeout, recheck | Runner fully covers supported phases; Skill owns response | KEEP_IN_RUNNER |
| Standard CI-equivalent validation | Validation profiles and CI drift verification | Runner fully covers Delivery/Review/Closeout; targeted profiles are explicitly non-CI-equivalent | KEEP_IN_RUNNER |
| Targeted validation selection | Skill chooses fixed profile; Runner executes it | Partial by design: choice is procedure, command execution is deterministic | KEEP_IN_SKILL + KEEP_IN_RUNNER |
| PR readiness acquisition | `delivery-readiness` snapshot includes PR, checks, threads, lifecycle, files, commits, diff digest | Runner fully covers available fixed facts | KEEP_IN_RUNNER |
| Check polling/waiting | Skill says wait/read; snapshot reads current check state and classifies required-check configuration | Partial: no evidence here of a Runner-owned polling loop or #86 frequency | NEEDS_FURTHER_EXPERIMENT |
| Self-review artifact materialization | Agent supplies semantics; helper validates schema/AC/file coverage and diff binding | Partial; deterministic helper exists outside fixed Runner | MOVE_TO_RUNNER for mechanics only |
| Review snapshot → validation → recheck | Review Evidence + Review Validation + supported recheck | Runner/Validation fully cover mechanical sequence; Skill still controls order | KEEP_IN_RUNNER |
| Review evidence finalization | Agent evidence matrix and verdict | Not suitable for Runner; semantics are the point | KEEP_IN_AGENT |
| Closeout merge/main/branch facts | Closeout plan + recheck + post-merge Validation | Runner fully covers facts and validation preconditions | KEEP_IN_RUNNER |
| Closeout side effects | Skill synchronizes main, writes lifecycle metadata, deletes exact branch after proof | Deliberately Skill-owned; no current Runner side-effect profile | KEEP_IN_SKILL |
| Feature direct-child set/recheck | Internal implementation and tests exist | Fixed front-door/profile gap | RUNNER_CANDIDATE_REQUIRED |
| Deterministic hashing/schema validation | Runner hashes snapshots and source; helper hashes self-review/diff; tests cover contracts | Partial across two helpers, not one shared profile | MOVE_TO_RUNNER for a bounded future binder; no verdict movement |
| Repeated fact reformatting | Current reports require evidence-linked summaries; actual runtime repetition unavailable | Suspected homogeneous overhead, not measured | REMOVE_FROM_TARGET_FLOW / later experiment |

The audit does not label an operation “Agent-owned” merely because the current
Skill manually invokes a fixed Runner. Invocation/authorization remains Skill
work; the deterministic mechanics remain Runner work.

## 7. Evidence attribution: architecture vs orchestration vs environment

The only directly observed runtime friction during this Delivery was:

1. normal-sandbox `delivery-start` could not write the required ignored Runner
   artifact (`Errno 30: Read-only file system`);
2. the exact bounded Preflight was retried once as required for an invalid
   Runner result;
3. the same exact Runner invocation succeeded under the approved elevated route;
4. a direct read-only `gh issue view` was also blocked by the normal proxy route,
   then succeeded under the approved elevated route;
5. branch creation likewise required elevated filesystem access to `.git`.

This is evidence of sandbox/network/approval friction in this session. It is
not evidence that the Runner's GitHub query set, workflow architecture, or
Agent orchestration caused the difference. `uv` cache behavior was not invoked
by the audit and is therefore `EVIDENCE_UNAVAILABLE`.

| Dimension | Evidence-driven conclusion |
|---|---|
| Workflow architecture | Current layered contracts are observable in docs/Skills/Runner/tests; no behavior comparison was run |
| Runner coverage | Fixed Delivery/Review/Closeout mechanics are present; Feature and self-review binder are partial/gap areas |
| Agent orchestration | Runtime command grouping/Guardian admissions unavailable; no causal conclusion |
| Guardian / command classification | Fixed Runner prefixes and direct GitHub read classifications exist; approval result is not a workflow verdict |
| Sandbox / network / approval | Current session required elevation for local artifact, `.git`, and GitHub network access; treat as separate confound |
| Validation / Quality Gate | Fixed named profiles and CI drift checks exist; no quality redesign is proposed |
| Test-suite structure | Broad contract coverage exists; overlap/legacy cleanup needs an independent coverage audit |

### A/C1/C2 candidate disposition

Because the required runtime evidence is unavailable:

- Guardian admission count: `EVIDENCE_UNAVAILABLE`;
- validation command granularity: statically specified by profiles, runtime
  frequency unavailable;
- repeated safe reads: mechanically possible to inspect from procedures, runtime
  frequency unavailable;
- evidence write/recheck finalization: current mechanics are observable, cost
  and failure frequency unavailable;
- Agent command grouping: `EVIDENCE_UNAVAILABLE`;
- Runner internal mechanics: statically auditable, not a causal runtime metric;
- sandbox/approval/network retry: one current-session observation only, not a
  general treatment effect.

No C1 → C2 Token/Guardian difference, or any single-rule causal effect, is
claimed.

## 8. Candidate dispositions

| Candidate | Evidence | Disposition |
|---|---|---|
| Runner expansion | Standard Delivery/Review/Closeout mechanics are already covered; self-review binder and Feature audit front door are concrete gaps | `RUNNER_CANDIDATE_REQUIRED` for narrowly bounded follow-up Tasks; no broad Runner rewrite |
| Agent command batching | Runtime #86 evidence unavailable; batching may confound workflow comparisons and hide failure boundaries | `AGENT_BATCHING_CANDIDATE`; optional experiment only after coverage audit, never defaulted by #88 |
| Context Compiler | Retrieval v2 is current and no measured residual context cost is available | `CONTEXT_COMPILER_NOT_JUSTIFIED`; #91 may test a narrowly defined hypothesis, not assume implementation |
| Sandbox configuration optimization | Current run shows elevation friction, but no controlled comparison and no `uv` cache evidence | `NEEDS_FURTHER_EXPERIMENT`; do not change `.codex/rules`, `.claude/settings.json`, sandbox, network, or approval policy in #88 |
| Quality Gate profile redesign | Current profiles are deterministic and acceptance requires preserving gates | `REMOVE_FROM_TARGET_FLOW` as an #88 implementation candidate; any redesign needs a separate approved Task |
| Closeout/Recovery redesign | Fixed facts are covered, but side-effect and recovery safety are policy-sensitive | `NEEDS_FURTHER_EXPERIMENT`; no redesign in #88 |
| Independent Review session strategy | Semantic independence and strict read-only are hard invariants; fact inheritance/isolation belongs to #89/#91 | `NEEDS_FURTHER_EXPERIMENT`; #88 defines boundary only |

## 9. Test-suite disposition

No test is deleted, merged, weakened, or reclassified by this Task.

Current structural observations:

- `test_workflow_evidence.py` exercises the internal evidence semantics,
  including delivery gates, review drift, closeout identity, feature child-set
  recheck, and fail-closed behavior.
- `test_wsl2_github_evidence_runner.py` exercises the fixed front-door contract,
  profiles, argument rejection, remote-ref stability, redaction, and skill-path
  identity.
- `test_wsl2_validation_runner.py` exercises fixed command profiles, CI drift,
  clean/main preconditions, timeouts, redaction, and Skill identity.
- `test_self_review.py` exercises artifact binding, structural completeness,
  stale-head/diff detection, evidence constraints, and changed-file coverage.
- `test_workflow_skills.py`, `test_retrieval_v2_policy.py`, and
  `test_agent_neutral_workflow.py` protect source-level contracts and the
  shared semantic-owner boundary. These are not runtime cost measurements.
- Legacy Skill directories are absent and their absence is itself regression
  tested; that is not legacy implementation coverage to delete.

There is intentional-looking boundary overlap between internal implementation
tests and fixed-wrapper tests. Count alone cannot prove duplication: one tests
mechanics and the other tests the guarded entry contract. The disposition is:

`TEST_COVERAGE_AUDIT_CANDIDATE` — later inventory tests by responsibility,
failure mode, and contract boundary; remove or merge only when an exact
duplicate is proven. A separate Task is required for any large cleanup.

## 10. Follow-up Task recommendations

These are recommendations only; #88 creates no Tasks and changes no GitHub
hierarchy.

1. **Fixed evidence coverage convergence** — define and implement, if
   approved, a bounded self-review mechanical binder and a fixed Feature audit
   Evidence/Validation profile. Acceptance must cover schema, identity,
   recheck, failure, and permission boundaries. This is the direct consumer of
   the `MOVE_TO_RUNNER` / `RUNNER_CANDIDATE_REQUIRED` findings.
2. **A/C1/C2 controlled evidence capture** — complete the explicitly referenced
   #86 protocol (or its canonical replacement), separately recording workflow,
   Runner, Agent orchestration, Guardian, sandbox/approval, and quality
   dimensions. Do not attribute composite changes to one layer.
3. **Independent Review fact contract** — #89 owns the final inheritance,
   isolation, and invalidation contract; #88 supplies only the ownership
   boundary and preserves semantic independence/strict read-only.
4. **Review execution strategy experiment** — #91 consumes #89's
   Review trust/isolation/invalidation contract and uses a fixed reviewed object
   to compare Review isolation strategy × fact acquisition / bounded
   fact-handoff strategy. It locks the final Review execution strategy; it does
   not presuppose a Context Compiler or restore the old Issue × Context Compiler
   four-arm experiment. #88 does not choose the final session strategy.
5. **Closeout/recovery safety evaluation** — evaluate fixed side effects and
   recovery paths with exact object identity and failure injection before any
   move to a Runner-owned side-effect profile.
6. **Workflow test coverage audit** — map each test to a semantic contract,
   deterministic mechanic, security boundary, or implementation detail; only
   then propose narrowly scoped cleanup.
7. **Sandbox/approval matrix** — measure GitHub read, ignored evidence write,
   `.git` write, `uv` cache access, network retry, and Guardian admission under
   comparable policies. This must not alter config as part of the measurement.

## 11. Final architecture decision

The current workflow is not a single Agent-owned command chain. It is already a
three-layer architecture with an incomplete mechanical frontier:

- semantic work and correctness remain with the Agent;
- executable ordering, authorization, and fail-closed adaptation remain with
  the Skill;
- deterministic facts, identity, drift, fixed validation, and bounded evidence
  remain with the Runner;
- the maintainer remains the manual Merge authority.

The target is therefore a boundary clarification plus narrowly scoped future
Runner coverage, not a Runner rewrite. No candidate in Section 8 is activated
by this document. Retrieval v2, independent Review semantics, strict read-only,
reviewed-object binding, new-head invalidation, manual Squash Merge, post-merge
validation, and Human Gate are all retained.

## 12. Delivery boundary

Changed repository files are limited to this audit document and its navigation
entry. No runtime code, Runner, Skill, Rules, sandbox/approval configuration,
quality profile, or test suite behavior was changed.

Independent Review has not been started, no Reviewer was spawned, no PR was
merged, no Issue was closed, and no Closeout was performed.
