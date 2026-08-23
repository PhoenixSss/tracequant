# LCK v1 Current → Target Migration Matrix

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

| ID | #88 operation | Current owner | LCK v1 target | Task #159 boundary |
|---|---|---|---|---|
| O01 | Resolve intent and exact Task | Agent + Skill routing | KEEP | KEEP |
| O02 | Delivery admission / readiness | Evidence Runner + Skill | MOVE | Live facts reused; full admission remains follow-up |
| O03 | Ready → In Progress | Skill / project helper | MOVE | Not moved in this Task |
| O04 | Read leaf spec and Retrieval v2 context | Agent | KEEP | KEEP |
| O05 | Triggered context expansion | Agent + Human Gate | KEEP | KEEP |
| O06 | Scoped design and implementation | Agent | KEEP | KEEP |
| O07 | Targeted validation | Skill + Validation Runner | KEEP | KEEP |
| O08 | Commit-ready identity | Skill / Git | MOVE | No commit effect here |
| O09 | CI-equivalent validation | Validation Runner | KEEP | KEEP |
| O10 | Push validated head | Skill / Agent | MOVE | LCK Delivery effect |
| O11 | PR resolve/create | PR helper + Skill | MOVE | LCK Delivery effect |
| O12 | Check wait/read and interpretation | Skill + Runner | SIMPLIFY | LCK checks gate; semantic interpretation remains Agent-owned |
| O13 | Semantic self-review | Agent + helper | SIMPLIFY | Semantic judgement stays Agent-owned |
| O14 | Delivery readiness snapshot | Evidence Runner + Skill | MOVE | Snapshot is diagnostic, not LCK authority |
| O15 | Reviewed object identity lock | Review Skill + Runner | MOVE | LCK live Review target + invocation-local guard |
| O16 | Complete effective-diff inspection | Review Agent | KEEP | KEEP |
| O17 | Review correctness / AC / risk judgement | Review Agent | KEEP | KEEP |
| O18 | Review CI-equivalent validation | Review Skill + Runner | KEEP | LCK runs formal Review validation on exact isolated head |
| O19 | Review recheck and stability | Runner + Review Skill | SIMPLIFY | `REVIEW_STALE_HEAD` / `REVIEW_STALE_BASE` invocation-local guard only |
| O20 | Review verdict and handoff | Review Agent + Skill | SIMPLIFY | Review Agent returns PASS/FAIL; LCK records diagnostic result; no mechanical handoff authority |
| O21 | Remediation admission | Delivery Skill + Runner | MOVE | Explicit `lck remediation prepare`; live mechanics + semantic findings only |
| O22 | Repair and new head | Agent + Delivery Skill | MOVE | LCK completion reuses Delivery effects and existing PR, then STOP before new Review |
| O23 | Manual Squash Merge checkpoint | Maintainer | KEEP | KEEP |
| O24 | Closeout read-only plan | Closeout Skill + Runner | MOVE | Closeout phase eligibility only |
| O25 | Main sync and post-merge validation | Skill + Runner | MOVE | Out of scope |
| O26 | Lifecycle metadata convergence | Closeout Skill / GitHub | MOVE | Out of scope |
| O27 | Exact Task branch cleanup | Closeout Skill | MOVE | Out of scope |
| O28 | Recovery after valid gate / drift | Skill + Agent | MOVE | Reacquire live facts; no lineage; Review uses only invocation-local stale guards |
| O29 | Feature child-set / completion mechanics | Feature Audit Skill + Agent | MOVE | Separate Feature audit boundary |
| O30 | Repeated fact re-query / reformat | Agent / Skill ad hoc | REMOVE | No snapshot authority |
| O31 | Homogeneous Agent command grouping | Agent orchestration | SIMPLIFY | Optional; never hides failure boundaries |
| O32 | Context Compiler beyond Retrieval v2 | No current owner | REMOVE | Not justified by the Charter |

## Task #159 Core baseline

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

The candidate Initial Delivery path uses one LCK-owned sequence after activation:

1. `DeliveryPreparer` resolves and prepares the Task workspace.
2. `DeliveryCompleter` reads the current Task Critical Outcome, stages the candidate
   tree, runs the bounded Critical Outcome verifier and repository formal validation,
   and commits exactly the validated tree.
3. `EnsureRemoteBranchEffect` synchronizes only the resolved Task branch without force
   push; divergence fails closed.
4. `EnsureOpenPrEffect` re-resolves current identity and reuses or creates exactly one
   non-Draft OPEN PR using the existing deterministic PR helper.
5. `DeliveryChecksGate` waits boundedly for applicable checks; failed, pending, or
   unknown checks stop before Project Status moves to `Review`.
6. Final live verification proves the Task body/base stayed stable within the
   operation, the final state is the same PR/head observed by the checks gate,
   applicable checks remain successful, and local HEAD == remote Task branch ==
   PR head before returning `READY_FOR_REVIEW`.

The Task body now carries a required `Critical Outcome` contract. Its verification
target is one bounded `tests/...::test_...` pytest node id; Issue text cannot inject
arbitrary shell commands. Operation-local base/body/head guards are ephemeral and are
reacquired on retry rather than persisted as cross-phase authority.

The Initial Delivery, Review, Remediation, and Closeout Skills are semantic-only
plus LCK entrypoint guidance. Historical Evidence operations may remain for
audit material, but they are not formal lifecycle authorization paths. Merge
remains a maintainer-only manual Squash Merge boundary.

## Review / Remediation boundary

The active path is now:

1. `review prepare <Task>` resolves the current OPEN PR, base/head, current Task
   Contract, effective diff and checks from live authority; no expected SHA/PR input is
   accepted.
2. LCK creates a detached clean worktree for the reviewed head, runs formal Review
   validation there, then removes implementation write bits before the semantic Agent
   inspects it.
3. A random `review_id` locates only the bounded prepare→complete applicability guard.
   Completion reacquires live facts; a changed head/base produces
   `REVIEW_STALE_HEAD` / `REVIEW_STALE_BASE` instead of accepting the verdict.
4. PASS returns `READY_FOR_HUMAN_MERGE`; FAIL returns `STOP_REQUIRED`. Neither path
   starts another lifecycle phase automatically.
5. A failed Review record is diagnostic/audit state. Human-started Remediation uses its
   findings as semantic input, while `remediation prepare` independently reacquires the
   current Task/PR/head/base/workspace.
6. `remediation complete` requires actual repair changes, reruns Critical Outcome and
   formal Delivery validation, commits the exact validated tree, synchronizes the Task
   branch, reuses the existing OPEN PR, waits for current checks, and returns
   `READY_FOR_NEW_REVIEW` followed by STOP.

This cutover intentionally deletes the former formal dependence on bounded verified
fact handoff, cross-phase freshness contracts, snapshot-derived Review target authority,
and generalized drift categories. Historical evidence remains audit-only.

## Mainline activation and rollback procedure

The final LCK cleanup becomes the sole Initial Delivery lifecycle authority after
all of the following have occurred:

1. an independent Review of the current PR head passes;
2. the maintainer performs the required Squash Merge;
3. `main` is synchronized and post-merge validation confirms the merged LCK
   controller, provider-neutral Skills, and repository workflow validators.

If activation or post-merge validation fails, the maintainer must stop further
LCK Initial Delivery activation, record the failure, and revert the candidate
merge as one controlled change. The pre-cutover Current Workflow remains the
authority while the revert is validated. The maintainer then synchronizes
`main`, reruns the relevant post-merge validation, and records the restored
authority boundary before any later reactivation attempt. A later activation
requires a new reviewed head and a fresh maintainer merge decision; no Agent or
Delivery Skill may bypass this rollback boundary.
