# LCK v1 Current → Target Migration Matrix

This matrix is the Task #159 implementation boundary. It normalizes the
operation inventory from the Task #88 architecture audit against the LCK v1
Design Charter. `KEEP` means the responsibility remains in its current layer,
`MOVE` means deterministic lifecycle authority moves into LCK, `SIMPLIFY` means
the semantic boundary remains while duplicate mechanics are removed, and
`REMOVE` means the mechanism is not part of the target flow.

Task #159 established live-state resolution, phase eligibility, and Delivery
Prepare. Task #160 implements the candidate **Initial Delivery** LCK controller:
Critical Outcome + formal validation, validated-tree commit, remote
synchronization, OPEN-PR resolve/create, checks, Project Status `Review`, and
final `READY_FOR_REVIEW` verification. Task #160 itself is delivered through
the pre-cutover Current Workflow; the candidate controller becomes lifecycle
authority only after the maintainer merge boundary. Review/Remediation, merge,
closeout, and recovery cutovers remain bounded follow-up work.

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
| O10 | Push validated head | Skill / Agent | MOVE | Candidate implemented; Current Workflow remains authority for #160 pre-cutover delivery |
| O11 | PR resolve/create | PR helper + Skill | MOVE | Candidate implemented; Current Workflow remains authority for #160 pre-cutover delivery |
| O12 | Check wait/read and interpretation | Skill + Runner | SIMPLIFY | Candidate gate implemented; Current Workflow remains authority for #160 pre-cutover delivery |
| O13 | Semantic self-review | Agent + helper | SIMPLIFY | Semantic judgement stays Agent-owned |
| O14 | Delivery readiness snapshot | Evidence Runner + Skill | MOVE | Snapshot is diagnostic, not LCK authority |
| O15 | Reviewed object identity lock | Review Skill + Runner | MOVE | Live resolver foundation only |
| O16 | Complete effective-diff inspection | Review Agent | KEEP | KEEP |
| O17 | Review correctness / AC / risk judgement | Review Agent | KEEP | KEEP |
| O18 | Review CI-equivalent validation | Review Skill + Runner | KEEP | KEEP |
| O19 | Review recheck and stability | Runner + Review Skill | SIMPLIFY | Invocation-local recheck only |
| O20 | Review verdict and handoff | Review Agent + Skill | KEEP | KEEP |
| O21 | Remediation admission | Delivery Skill + Runner | MOVE | Phase resolver exposes eligibility only |
| O22 | Repair and new head | Agent + Delivery Skill | MOVE | Out of scope |
| O23 | Manual Squash Merge checkpoint | Maintainer | KEEP | KEEP |
| O24 | Closeout read-only plan | Closeout Skill + Runner | MOVE | Closeout phase eligibility only |
| O25 | Main sync and post-merge validation | Skill + Runner | MOVE | Out of scope |
| O26 | Lifecycle metadata convergence | Closeout Skill / GitHub | MOVE | Out of scope |
| O27 | Exact Task branch cleanup | Closeout Skill | MOVE | Out of scope |
| O28 | Recovery after valid gate / drift | Skill + Agent | MOVE | Reacquire live facts; no lineage |
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

## Candidate boundary implemented by Task #160 (pre-activation)

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

Until the maintainer merge boundary, #160 continues to use the pre-cutover
Current Workflow for its own Delivery. The LCK path above is candidate behavior
and is not retroactively treated as #160's lifecycle authority.

The Task body now carries a required `Critical Outcome` contract. Its verification
target is one bounded `tests/...::test_...` pytest node id; Issue text cannot inject
arbitrary shell commands. Operation-local base/body/head guards are ephemeral and are
reacquired on retry rather than persisted as cross-phase authority.

The Initial Delivery section of `task-delivery-runner` is now semantic-only plus LCK
entrypoint guidance. The existing Review remediation procedure remains temporarily on
the current Runner contract because its authority cutover belongs to the next migration
Task; merge and closeout remain Human/later-task boundaries.
