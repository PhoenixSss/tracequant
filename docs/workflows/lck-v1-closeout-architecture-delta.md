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

The former Evidence Runner and its historical profile material remain in the
repository as read-only audit/compatibility assets because the repository
security policy protects the established fixed-query approval boundary. They
are not referenced by the active Task Skills, LCK phase eligibility, formal
validation, or any write effect. This is the permitted audit-only retention,
not a second success path. Historical audit operations explicitly request their
read-only local-ref behavior; LCK uses the helper's live-refresh default and
therefore always attempts a real `origin` refresh, reporting failure as
unavailable evidence.

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
| Formal lifecycle controller | Skill + Runner + handoff/snapshot paths | `lck.py`: 4,215 LOC |
| Task Skill lifecycle mechanics | direct command/procedure paths | 474 LOC per provider; 948 LOC across Codex + Claude |
| Durable cross-phase control state | snapshots, freshness, handoff fields | 0 in LCK; only diagnostic records and invocation-local guards |
| Snapshot/freshness/drift concepts | cross-phase snapshot/freshness/drift graph | 0 snapshot/freshness authority; one operation-local stale-guard family |
| Dynamic global write authorization | `write_actions_allowed` disposition | absent from LCK and active Task policy |
| Direct Agent lifecycle writes | present in historical baseline | 0 in active Task Skills |
| Duplicate Task identity resolution | Skill/Runner/current-workflow paths | one LCK `LiveStateResolver` |
| Main lifecycle test groups | split across old Runner and workflow paths | 85 tests: 48 + 25 + 11 + 1 acceptance |
| Legacy components | old Task snapshot/control paths | retained only where classified audit-only by repository policy |

The historical #85 static Skill record reports 685 lines before and 547 lines
after its earlier Runner migration; #88 supplies the operation inventory rather
than a directly comparable LOC measurement. Current values above were measured
from this candidate tree and are descriptive evidence, not lifecycle authority.

## Activation and rollback evidence boundary

Tasks #164–#167 established the LCK implementation on `main` before this
cleanup. This Task removes the remaining active dual-path references and adds
the acceptance verifier. The repository does not contain provider-attributed
Codex and Claude live-session receipts or a maintainer-executed Revert receipt;
those external acceptance facts are therefore recorded as **evidence pending**
and are not fabricated by this report. They must be supplied at Feature-level
acceptance after a real Codex delivery, an equivalent Claude delivery, and a
controlled activation/rollback exercise.

The required rollback boundary remains: stop activation on failure, manually
revert the candidate merge as one controlled change, validate restored main,
and require a fresh reviewed head before reactivation. No Agent or Skill may
bypass that boundary.
