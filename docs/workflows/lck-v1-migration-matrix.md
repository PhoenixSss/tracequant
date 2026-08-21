# LCK v1 Current → Target Migration Matrix

This matrix is the Task #159 implementation boundary. It normalizes the
operation inventory from the Task #88 architecture audit against the LCK v1
Design Charter. `KEEP` means the responsibility remains in its current layer,
`MOVE` means deterministic lifecycle authority moves into LCK, `SIMPLIFY` means
the semantic boundary remains while duplicate mechanics are removed, and
`REMOVE` means the mechanism is not part of the target flow.

Task #159 implements only the live-state, phase-eligibility, and Delivery
Prepare rows marked `implemented here`. Commit, push, PR mutation, merge,
closeout, and full Skill cutover remain bounded follow-up work owned by their
respective Tasks.

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
| O10 | Push validated head | Skill / Agent | MOVE | Out of scope |
| O11 | PR resolve/create | PR helper + Skill | MOVE | Out of scope |
| O12 | Check wait/read and interpretation | Skill + Runner | SIMPLIFY | Live facts are returned; polling remains follow-up |
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

## Implemented LCK Core boundary

`tools/agent_workflow/lck.py` provides three deterministic layers:

1. `LiveStateResolver` reacquires repository, Task, Project, relationship,
   local branch/HEAD, remote branch/OID, current OPEN PR, checks, merged, and
   cleanup-relevant facts.
2. `PhaseEligibilityResolver` applies static phase capabilities to those live
   facts and stops on ambiguity, missing authority, or unresolved blockers.
3. `DeliveryPreparer` creates, selects, or restores the uniquely resolved Task
   workspace and verifies the postcondition by reacquiring live facts.

The core does not accept `expected_sha` or `snapshot_id`, does not persist
cross-phase authority, ignores CLOSED PRs when resolving the active PR, and
does not expose commit, push, PR-create, merge, or arbitrary shell effects.
