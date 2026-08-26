# LCK v1 Final Migration Matrix

This matrix tracks the LCK v1 migration boundary. It normalizes the
operation inventory from the Task #88 architecture audit against the LCK v1
Design Charter. `KEEP` means the responsibility remains in its current layer,
`MOVE` means deterministic lifecycle authority moves into LCK, `SIMPLIFY` means
the semantic boundary remains while duplicate mechanics are removed, and
`REMOVE` means the mechanism is not part of the target flow.

Tasks #164–#167 established the complete LCK v1 controller: live-state
resolution, Delivery, Independent Review, explicit Remediation, Merge
Preflight, Closeout, and recovery. Task #163 is the final convergence pass:
active Task Skills are semantic-only, formal Task phases use LCK exclusively,
and historical Evidence remains audit-only. See
`lck-v1-closeout-architecture-delta.md` for the final authority and evidence
boundary.

| ID | #88 operation | Pre-LCK owner | LCK v1 target | Final LCK v1 disposition |
|---|---|---|---|---|
| O01 | Resolve intent and exact Task | Agent + Skill routing | KEEP | Human intent + semantic Skill routing; LCK resolves mechanical identity |
| O02 | Delivery admission / readiness | Evidence Runner + Skill | MOVE | LCK live-state admission; legacy Task Evidence Runner removed |
| O03 | Ready → In Progress | Skill / project helper | REMOVE | LCK does not write this transition; Delivery Prepare admits either `Ready` or `In Progress`, and later LCK effects move the Task to `Review` |
| O04 | Read leaf spec and Retrieval v2 context | Agent | KEEP | Agent semantic responsibility |
| O05 | Triggered context expansion | Agent + Human Gate | KEEP | Agent/Human semantic responsibility |
| O06 | Scoped design and implementation | Agent | KEEP | Agent semantic responsibility |
| O07 | Targeted validation | Skill + Validation Runner | KEEP | Agent-requested development feedback; never lifecycle authority |
| O08 | Commit-ready identity | Skill / Git | MOVE | LCK binds the validated tree/head before commit |
| O09 | CI-equivalent validation | Validation Runner | KEEP | Validation Runner called by LCK formal phase gates |
| O10 | Push validated head | Skill / Agent | MOVE | LCK bounded Delivery/Remediation effect |
| O11 | PR resolve/create | PR helper + Skill | MOVE | LCK uses deterministic helper internally; Skill supplies no PR authority |
| O12 | Check wait/read and interpretation | Skill + Runner | SIMPLIFY | LCK mechanical checks gate; semantic interpretation remains Agent-owned |
| O13 | Semantic self-review | Agent + retired binder | SIMPLIFY | Agent semantic judgement only; durable `self_review.py` binder removed |
| O14 | Delivery readiness snapshot | Evidence Runner + Skill | REMOVE | No formal cross-phase snapshot; historical evidence is audit-only |
| O15 | Reviewed object identity lock | Review Skill + Runner | MOVE | LCK live Review target + invocation-local applicability guard |
| O16 | Complete effective-diff inspection | Review Agent | KEEP | Fresh Review Agent semantic responsibility |
| O17 | Review correctness / AC / risk judgement | Review Agent | KEEP | Fresh Review Agent semantic responsibility |
| O18 | Review CI-equivalent validation | Review Skill + Runner | MOVE | LCK runs formal Review validation on exact isolated head |
| O19 | Review recheck and stability | Runner + Review Skill | SIMPLIFY | Review Complete acquires one fresh snapshot and rejects `REVIEW_STALE_PR/HEAD/BASE/TASK/DIFF`; Merge Preflight independently rechecks the accepted receipt |
| O20 | Review verdict and handoff | Review Agent + Skill | SIMPLIFY | PASS/FAIL semantic verdict; LCK records bounded result; no mechanical handoff authority |
| O21 | Remediation admission | Delivery Skill + Runner | MOVE | Human-explicit `lck remediation prepare`; live mechanics + semantic findings only |
| O22 | Repair and new head | Agent + Delivery Skill | MOVE | Agent repairs; LCK completion owns mechanical effects, then STOP before new Review |
| O23 | Manual Squash Merge checkpoint | Maintainer | KEEP | Maintainer-only Squash Merge boundary |
| O24 | Closeout read-only plan | Closeout Skill + Runner | MOVE | LCK closeout live-state eligibility |
| O25 | Main sync and post-merge validation | Skill + Runner | MOVE | LCK bounded closeout synchronization + validation |
| O26 | Lifecycle metadata convergence | Closeout Skill / GitHub | MOVE | LCK closeout effects after authoritative merged/closed state |
| O27 | Exact Task branch cleanup | Closeout Skill | MOVE | LCK identity-proven cleanup effect; cleanup may remain pending |
| O28 | Recovery after valid gate / drift | Skill + Agent | MOVE | LCK reacquires live facts; no lineage/snapshot recovery authority |
| O29 | Feature child-set / completion mechanics | Feature Audit Skill + Agent | KEEP | Separate read-only Feature audit boundary; not Task lifecycle authority |
| O30 | Repeated fact re-query / reformat | Agent / Skill ad hoc | REMOVE | One LCK Task resolver; historical audit queries do not control Task phases |
| O31 | Homogeneous Agent command grouping | Agent orchestration | SIMPLIFY | Optional Agent ergonomics; never lifecycle authority |
| O32 | Context Compiler beyond Retrieval v2 | No current owner | REMOVE | Not part of LCK v1 |

## Historical Task #159 core baseline (non-authoritative)

`tools/agent_workflow/lck.py` provides three deterministic layers:

1. `LiveStateResolver` reacquires repository, Task, Project, relationship,
   local branch/HEAD, remote branch/OID, current OPEN PR, checks, merged, and
   cleanup-relevant facts.
2. `PhaseEligibilityResolver` applies static phase capabilities to those live
   facts and stops on ambiguity, missing authority, or unresolved blockers.
3. `DeliveryPreparer` creates, selects, or restores the uniquely resolved Task
   workspace and verifies the postcondition by reacquiring live facts.

At the Task #159 baseline the core did not accept `expected_sha` or `snapshot_id`,
did not persist cross-phase authority, ignored CLOSED PRs when resolving the active
PR, and exposed no commit/push/PR mutation. Task #160 intentionally extends only
the Initial Delivery portion of that boundary.

## Active Delivery boundary

The active Initial Delivery path uses explicit operation boundaries rather than repeated
full-state refresh:

1. `DeliveryPreparer` acquires one fresh Delivery Prepare snapshot and prepares the Task
   workspace from that frozen authority.
2. `DeliveryCompleter` is a new operation. It acquires one fresh Delivery Complete
   snapshot, binds the required-check policy to the exact authoritative `main` base commit
   (never the candidate checkout), reads the current Task Critical Outcome, stages the
   candidate tree, runs the bounded Critical Outcome verifier and repository formal
   validation, and commits exactly the validated tree.
3. `EnsureRemoteBranchEffect` synchronizes only the resolved Task branch without force
   push and verifies the exact remote-ref postcondition; divergence fails closed.
4. `EnsureOpenPrEffect` reuses or creates exactly one non-Draft OPEN PR from the frozen
   operation identity and verifies only the exact PR postcondition.
5. `DeliveryChecksGate` evaluates exact-head GitHub check results against the required
   names derived from static jobs in `.github/workflows/ci.yml` at that immutable
   trusted-base commit. A candidate edit to the CI workflow is future policy only.
   Pending CI ends the
   invocation; LCK does not poll waiting for external state to change.
6. Bounded metadata effects use targeted postcondition verification. Delivery Complete
   does not run a final full `LiveStateResolver` refresh; the next lifecycle invocation
   acquires fresh authority before making its own decision.

The Task body carries a required `Critical Outcome` contract. Its verification target is
one bounded `tests/...::test_...` pytest node id; Issue text cannot inject arbitrary shell
commands. Operation snapshots are immutable within each invocation and never become
cross-operation authority.

The Initial Delivery, Review, Remediation, and Closeout Skills are semantic-only plus LCK
entrypoint guidance. Historical Evidence operations may remain for audit material, but
they are not formal lifecycle authorization paths. Merge remains a maintainer-only manual
Squash Merge boundary.

## Review / Remediation boundary

The active Review path is:

1. `review prepare <Task>` acquires one fresh Review Prepare snapshot containing the
   current OPEN PR, base/head, current Task Contract, effective diff and checks; no
   expected SHA/PR input is accepted.
2. LCK creates a detached standalone temporary clone for the exact reviewed head without
   registering or mutating source `.git/worktrees`, restores the clone origin to the real
   remote, runs formal Review validation there, then seals the whole clone read-only before
   the semantic Agent inspects it. Review Complete deletes the clone directly after recording
   PASS / FAIL / STALE; interruption recovery owns only the temporary path.
3. The semantic Review Agent judges only that sealed target.
4. `review complete <Task> --review-id ...` is a **new LCK operation**. It acquires one
   fresh Review Complete snapshot and compares current PR/head/base/Task Contract/effective
   diff with the sealed reviewed identity. Changes return
   `REVIEW_STALE_PR/HEAD/BASE/TASK/DIFF` and the semantic verdict is not accepted for the
   current target.
5. An applicable PASS returns `READY_FOR_MERGE_PREFLIGHT`; an applicable FAIL returns
   `STOP_REQUIRED`. Neither path starts another write lifecycle phase automatically.
6. After PASS, the Review Skill runs the separate read-only `merge preflight` operation.
   Merge Preflight reacquires fresh authority and only then may return
   `READY_FOR_HUMAN_MERGE` for the maintainer manual Squash Merge boundary.
7. A failed Review record is diagnostic/audit state. Human-started Remediation uses its
   findings as semantic input, while `remediation prepare` independently reacquires the
   current Task/PR/head/base/workspace.
8. `remediation complete` requires actual repair changes, reruns Critical Outcome and
   formal Delivery validation, commits the exact validated tree, synchronizes the Task
   branch, reuses the existing OPEN PR, evaluates current operation checks, and returns
   `READY_FOR_NEW_REVIEW` followed by STOP. Evidence that can only be produced by the
   resulting repaired head or a separate provider/fresh Review remains pending for that
   later Review acceptance boundary; it does not circularly block Remediation Complete.

This cutover intentionally deletes the former formal dependence on bounded verified fact
handoff, cross-phase freshness contracts, snapshot-derived current authority, repeated
nested resolution, and generalized drift categories. Historical evidence remains
audit-only.

## Mainline activation and rollback procedure

The Task #163 cleanup is eligible for mainline activation only after all of the following have occurred:

1. an independent Review of the current PR head passes;
2. the maintainer performs the required Squash Merge;
3. `main` is synchronized and post-merge validation confirms the merged LCK
   controller, provider-neutral Skills, and repository workflow validators.

If activation or post-merge validation fails, the maintainer must stop further
activation, record the failure, and revert the candidate merge as one controlled
change to the last reviewed/merged LCK v1 state. No Legacy Task control path is
reactivated during rollback. The maintainer then synchronizes `main`, reruns the
relevant post-merge validation, and records the restored LCK authority boundary.
A later activation requires a new reviewed head and a fresh maintainer merge
decision; no Agent or Skill may bypass this rollback boundary.

The controlled rollback boundary is mechanically verified by
`tests/tools/test_lck_delivery.py::test_lck_rollback_procedure_reverts_candidate_and_requires_fresh_review`.
That regression creates a squash candidate in an isolated Git repository, executes one
`git revert` after simulated activation failure, proves the restored main tree is clean
and equal to the last reviewed state, then proves the old Review identity is rejected
with `REVIEW_STALE_HEAD` for a fresh activation head. It does not inject a failure into
the healthy live activation path.
