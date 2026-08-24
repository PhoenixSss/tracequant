# LCK v1 closeout and architecture delta

This report is the implementation evidence for Task #163. It records the
current repository architecture and the boundary between formal Task
lifecycle control and retained audit material. It does not authorize merge,
Issue closure, Feature completion, or a rollback.

## Final authority boundary

| Responsibility | Final owner | Evidence |
| --- | --- | --- |
| Task identity, readiness, workspace recovery | LCK live-state resolver | `tools/agent_workflow/lck.py` |
| Delivery prepare/complete and validated-tree effects | LCK | `delivery prepare|complete` |
| Independent Review target, stale guards, and verdict record | LCK plus the fresh semantic Review Agent | `review prepare|complete` |
| Explicit Remediation admission and completion | LCK plus semantic repair | `remediation prepare|complete` |
| Merge identity and manual merge boundary | LCK preflight; maintainer performs Squash Merge | `merge preflight` |
| Post-merge Business Delivery/Cleanup and recovery | LCK | `closeout` |
| Semantic implementation, review judgment, and reporting | Codex or Claude Skill | dual Skills are byte-identical |
| Feature hierarchy evidence | Feature audit evidence path | audit-only; never Task authority |

LCK reacquires live Git/GitHub facts for every phase. No formal Task phase
accepts an old snapshot, expected SHA, cross-phase handoff, or dynamic global
write-permission field as authority. Operation-local stale guards remain only
where the LCK phase contract needs them.

## Legacy disposition

The active Codex and Claude Task Skills now contain only semantic role,
semantic procedure, LCK invocation guidance, and semantic output/reporting
requirements. They do not stage, commit, push, create PRs, select identity,
merge, close Issues, or run a legacy lifecycle profile.

The executable pre-LCK Task Evidence Runner, its Task profiles, Codex approval
Rules, Claude permission entry, dedicated Runner/Rules tests, and obsolete
Delivery `self_review.py` binder/test have been removed. Only frozen historical
publication material remains under
`docs/workflows/wsl2-github-evidence-runner/`; those files are provenance, not
executable compatibility/control assets. `workflow_evidence.py` remains as a
read-only audit implementation and shared bounded query helper module; its
historical Task snapshot operations are audit material only and active Task
Skills do not invoke them. Formal LCK live-state resolution uses the shared Git
fact helper in read-only observation mode: `local_main_sha` comes from
`refs/heads/main`, `tracking_main_sha` comes from the local
`refs/remotes/origin/main` cache, and authoritative `remote_main_sha` comes
from `git ls-remote origin refs/heads/main`. A stale tracking cache is retained
as a diagnostic fact and never substitutes for the authoritative remote query.
The LCK observation path does not run `git fetch --prune origin`; Git object
materialization and other metadata writes remain bounded lifecycle effects.

## Acceptance coverage

`tests/tools/test_lck_acceptance.py::test_lck_v1_full_lifecycle_has_single_deterministic_control_authority`
is the Critical Outcome verifier. It checks the complete LCK command surface,
Codex/Claude Skill identity, absence of direct lifecycle writes and cross-phase
authority in active Skills/LCK, and the named full-lifecycle regression tests.

The lifecycle regression suite covers:

- fresh Delivery and validated-tree completion;
- Review PASS, FAIL, stale head/base, and explicit Remediation;
- manual Squash Merge preflight;
- Business Delivery complete with Cleanup complete or pending;
- remote divergence, deleted refs, and live recovery behavior.

Provider neutrality is checked by byte equality of the three Task Skills under
`.agents/skills/` and `.claude/skills/`. A fresh Review is still required after
any new implementation head; this report does not perform that review.

## Complexity comparison

The following are static repository measurements, not runtime or token claims.
The historical #88 materials remain the baseline for the pre-LCK operation
inventory; their aggregate command counts are not added to current LOC.

| Measure | #88 / pre-LCK reference | LCK v1 current shape |
| --- | --- | --- |
| Formal lifecycle controller | Skill + Runner + handoff/snapshot paths | `lck.py`: 4,613 LOC |
| Task-control support called by LCK | mechanics split across Skill/Runner/helpers | 1,241 LOC: `critical_outcome.py` 204 + `pr_resolve.py` 481 + `project_status.py` 153 + shared `workflow_common.py` 403 |
| Combined active Task control code | no single deterministic boundary in #88 | 5,854 LOC controller + direct support; reused validation/audit infrastructure excluded |
| Reused Validation infrastructure | existing fixed Validation Runner | 1,163 LOC: `workflow_validation.py` 344 + `wsl2_validation_runner.py` 819; reused rather than duplicated |
| Audit-only Evidence implementation | Task Evidence Runner was part of lifecycle control | `workflow_evidence.py` 1,615 LOC retained as read-only audit/shared-query code, not Task authority |
| Task Skill lifecycle mechanics | direct command/procedure paths | 269 LOC per provider; 538 LOC across Codex + Claude; no direct lifecycle writes |
| Durable cross-phase control state | snapshots, freshness, handoff fields | 0 authoritative cross-phase state in LCK; only diagnostic records and invocation-local guards |
| Snapshot/freshness/drift concepts | cross-phase snapshot/freshness/drift graph | 0 snapshot/freshness authority; one operation-local stale-guard family |
| Dynamic global write authorization | `write_actions_allowed` disposition | absent from LCK and active Task policy |
| Direct Agent lifecycle writes | present in historical baseline | 0 in active Task Skills |
| Duplicate Task identity resolution | Skill/Runner/current-workflow paths | one LCK `LiveStateResolver`; historical audit queries cannot authorize Task phases |
| Main lifecycle test groups | split across old Runner and workflow paths | 92 tests: 55 + 25 + 11 + 1 acceptance |
| Legacy executable components removed in this convergence | old Task Runner/profiles/Rules + durable self-review binder | 7 files removed: Runner, profile spec, Codex Rule, two Runner/Rules tests, self-review binder, binder test |

The historical #85 static Skill record reports 685 lines before and 547 lines
after its earlier Runner migration; #88 supplies the operation inventory rather
than a directly comparable LOC measurement. Current values above were measured
from this candidate tree and are descriptive evidence, not lifecycle authority.

The LOC boundary is explicit to avoid understating LCK complexity: the 4,613-line
core is reported separately from the 1,241 lines of support it directly calls.
Validation infrastructure is reported separately because it predates LCK and is
reused; audit-only Evidence code is also reported separately because it cannot
authorize a Task transition. This comparison demonstrates containment, not a
claim that LCK itself is smaller than every pre-LCK file aggregate.

## Design Charter architecture acceptance

The 22 Architecture Acceptance Criteria in `LCK-v1-Design-Charter.md` are mapped
explicitly below. `Satisfied (static/regression)` means the repository contract
and automated tests establish the architecture property; it does not fabricate
provider-attributed live-session evidence.

| # | Charter criterion | Status | Repository evidence |
| --- | --- | --- | --- |
| 1 | Simple Codex / Claude window instruction | Satisfied (static); live receipt pending | Task Skills expose short standard invocations |
| 2 | Same lifecycle contract for Codex and Claude | Satisfied (static) | byte-identical Task Skills; acceptance guard |
| 3 | Session changes do not invalidate correctness | Satisfied (design/regression); cross-provider live receipt pending | live-state reacquisition; no cross-phase authority token |
| 4 | Skills are not lifecycle state machines | Satisfied | no direct Git/GitHub lifecycle writes; LCK entry guidance only |
| 5 | Reconstruct from live Git/GitHub state | Satisfied | `LiveStateResolver`, including read-only remote-main integration regression |
| 6 | No required authoritative cross-phase snapshots | Satisfied | no `snapshot_id`/Evidence authority in LCK |
| 7 | Branch/SHA/PR actionable identity resolved by LCK | Satisfied | Delivery/Review/Remediation/Closeout live-resolution tests |
| 8 | Agent does not own commit/push/PR/lifecycle mutation | Satisfied | Skill guards + LCK bounded effects |
| 9 | Delivery stops before Independent Review | Satisfied | `READY_FOR_REVIEW`; no automatic Review invocation |
| 10 | Fresh Review role + live target | Satisfied | isolated read-only review worktree + live target tests |
| 11 | Review FAIL always stops | Satisfied | `STOP_REQUIRED` regression |
| 12 | Remediation requires Human intent | Satisfied | explicit failed `review_id` admission; no auto-remediation |
| 13 | Human Squash Merge mandatory | Satisfied | merge preflight stops at maintainer boundary; no merge effect |
| 14 | Business Delivery separated from Cleanup | Satisfied | closeout state model + cleanup-pending regression |
| 15 | Recovery needs no old Kernel/snapshot lineage | Satisfied | live recovery/resume tests |
| 16 | Historical CLOSED PRs do not block active resolution | Satisfied | closed-PR regression |
| 17 | Normal evolution not generalized into drift | Satisfied | operation-local Review stale guard only |
| 18 | Critical Outcome enforced as Delivery gate | Satisfied | parser/verifier + Delivery fail-closed regression |
| 19 | Task template and deterministic Critical Outcome validation evolve together | Satisfied | `.github/ISSUE_TEMPLATE/task.yml`, `critical_outcome.py`, tests |
| 20 | No workflow database or daemon | Satisfied | on-demand CLI; acceptance source guard |
| 21 | Control complexity reduced/contained vs baseline | Satisfied with explicit measurement boundary | complexity table above; legacy executable components removed |
| 22 | Existing Runner infrastructure reused, not duplicated | Satisfied | existing Validation Runner retained and invoked by LCK; legacy Task Evidence Runner removed |

The separate Task #163 **Dual Agent acceptance** requirement is stronger than
these static architecture checks. Real provider-attributed Codex/Claude paths and
fresh cross-provider Review combinations remain external evidence pending, as
recorded below.

## Activation and rollback evidence boundary

Tasks #164–#167 established the LCK implementation on `main` before this
cleanup. This Task removes the remaining active dual-path references and adds
the acceptance verifier. The repository does not contain provider-attributed
Codex and Claude live-session receipts or a maintainer-executed activation/rollback
receipt. Fresh provider-attributed Codex and Claude implementation receipts and
cross-provider Review receipts are **pre-merge** evidence. They remain pending
and are not fabricated by this report; they must be recorded before the current
OPEN PR can receive Independent Review PASS. Static Skill identity and unit
tests do not substitute for the Task's explicit live Dual Agent requirement.

The current Task's Squash Merge receipt, mainline activation receipt, and
Closeout receipt are **post-merge** evidence. They cannot be produced while the
PR is OPEN and are not prerequisites for this PR's Independent Review PASS.
They remain required for final Task acceptance after a maintainer performs the
Human Squash Merge. Rollback evidence is required only if a real activation
failure occurs; the rollback procedure/mechanics are pre-merge acceptance and
must not be turned into a forced failure injection.

The required rollback boundary remains: stop activation on failure, manually
revert the candidate merge as one controlled change, validate restored main,
and require a fresh reviewed head before reactivation. No Agent or Skill may
bypass that boundary.
