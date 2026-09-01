# LCK implementation map

`../lck.py` is the stable CLI facade. Lifecycle implementation lives here so a
maintainer or reviewer can start with the responsibility that owns the behavior.

| Concern | Start here | Typical companion modules |
| --- | --- | --- |
| leaf Issue type/profile contract and routing | `issue_profiles.py`, `profile_policies.py` | `models.py`, `eligibility.py`, `../bug_policy.py`, `../documentation_policy.py`, `../research_policy.py` |
| contracts, immutable state/result models | `models.py` | `issue_profiles.py` |
| live Git/GitHub facts, Fact Profiles, operation snapshots | `state.py` | `models.py` |
| lifecycle admission / eligibility | `eligibility.py` | `state.py` |
| formal validation and required-check gates | `validation.py` | `state.py`, `../documentation_policy.py` |
| bounded Git/GitHub write effects and profile effect executors | `effects.py` | `delivery.py`, `remediation.py`, or `closeout.py` |
| Delivery prepare/complete | `delivery.py` | `eligibility.py`, `validation.py`, `effects.py` |
| isolated Review workspace and review records | `review_workspace.py` | `review.py` |
| Independent Review and Merge Preflight | `review.py` | `review_workspace.py`, `validation.py` |
| remediation and owned-candidate recovery | `remediation.py` | `eligibility.py`, `delivery.py`, `effects.py` |
| post-merge Closeout | `closeout.py` | `eligibility.py`, `profile_policies.py` |
| Agent View, Audit Receipt, failure evidence/replay | `receipts.py` | affected phase module |
| CLI dispatch only | `cli.py` | phase modules |

## Dependency rule

Shared layers (`models`, `state`, `eligibility`, `validation`, `profile_policies`) must not import phase
orchestration modules. Effects depend only on shared layers. Phase modules compose
shared layers/effects. `receipts.py` projects completed phase results, and `cli.py`
is the outermost dispatcher. Avoid runtime import tricks, service locators, or
module mutation to bypass this direction.

`issue_profiles.py` owns canonical label resolution and profile metadata.
`profile_policies.py` owns the one dispatch boundary from that metadata to the
generic contract/candidate/review/completion capabilities. Lifecycle phase
modules must not add another type-driven dispatch table. Profile policies may
return validated, data-only effect descriptors; `effects.py` executes only
registered effect kinds and proves their postconditions.

The same module owns the immutable production `ProfilePolicyRegistry`, the
injectable `LeafIssuePolicy` seam, and the frozen four-stage
`ProfileEvidenceEnvelope` (`contract`, `candidate`, `review`, `completion`).
Policy blockers and stage payloads are validated by the selected policy before
the shared kernel transports or records them; the canonical leaf contract is
referenced by identity/digest rather than copied into the envelope. Review
workspace isolation, path containment, and review identity freshness remain
kernel responsibilities; profile policies only supply bounded artifact
semantics/evidence.

When diagnosing a lifecycle issue, read this map first and inspect the owner module
plus at most its direct companion modules before broad repository searches.

## Test map

The former mixed `tests/tools/test_lck.py` suite is responsibility-owned too:

- `test_lck_state.py` — live-state, Fact Profiles, resolver query contracts.
- `test_lck_issue_profiles.py` — canonical leaf Issue type/profile resolution.
- `test_lck_bug.py` — Bug defect contract and shared lifecycle boundaries.
- `test_lck_prepare.py` — prepare/admission and eligibility behavior.
- `test_lck_review.py` — Review workspace, Review Complete, Merge Preflight.
- `test_lck_remediation.py` — remediation sessions and partial-effect recovery.
- `test_lck_receipts.py` — Agent View / Audit Receipt and failure evidence.
- `test_lck_closeout.py` plus `test_lck_closeout_additional.py` — Closeout.
- `test_lck_delivery.py` — Delivery completion and bounded effects.
- `test_lck_profile_architecture.py` — injectable policy and evidence envelope.
- `test_lck_structure.py` — facade/module dependency guardrails.

`tests/tools/test_lck.py` intentionally retains only the long-lived Critical Outcome
verification node used by historical Task contracts.
