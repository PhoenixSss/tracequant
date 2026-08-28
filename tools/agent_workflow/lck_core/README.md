# LCK implementation map

`../lck.py` is the stable CLI facade. Lifecycle implementation lives here so a
maintainer or reviewer can start with the responsibility that owns the behavior.

| Concern | Start here | Typical companion modules |
| --- | --- | --- |
| contracts, immutable state/result models | `models.py` | — |
| live Git/GitHub facts, Fact Profiles, operation snapshots | `state.py` | `models.py` |
| lifecycle admission / eligibility | `eligibility.py` | `state.py` |
| formal validation and required-check gates | `validation.py` | `state.py` |
| bounded Git/GitHub write effects | `effects.py` | `delivery.py` or `remediation.py` |
| Delivery prepare/complete | `delivery.py` | `eligibility.py`, `validation.py`, `effects.py` |
| isolated Review workspace and review records | `review_workspace.py` | `review.py` |
| Independent Review and Merge Preflight | `review.py` | `review_workspace.py`, `validation.py` |
| remediation and owned-candidate recovery | `remediation.py` | `eligibility.py`, `delivery.py`, `effects.py` |
| post-merge Closeout | `closeout.py` | `eligibility.py` |
| Agent View, Audit Receipt, failure evidence/replay | `receipts.py` | affected phase module |
| CLI dispatch only | `cli.py` | phase modules |

## Dependency rule

Shared layers (`models`, `state`, `eligibility`, `validation`) must not import phase
orchestration modules. Effects depend only on shared layers. Phase modules compose
shared layers/effects. `receipts.py` projects completed phase results, and `cli.py`
is the outermost dispatcher. Avoid runtime import tricks, service locators, or
module mutation to bypass this direction.

When diagnosing a lifecycle issue, read this map first and inspect the owner module
plus at most its direct companion modules before broad repository searches.

## Test map

The former mixed `tests/tools/test_lck.py` suite is responsibility-owned too:

- `test_lck_state.py` — live-state, Fact Profiles, resolver query contracts.
- `test_lck_prepare.py` — prepare/admission and eligibility behavior.
- `test_lck_review.py` — Review workspace, Review Complete, Merge Preflight.
- `test_lck_remediation.py` — remediation sessions and partial-effect recovery.
- `test_lck_receipts.py` — Agent View / Audit Receipt and failure evidence.
- `test_lck_closeout.py` plus `test_lck_closeout_additional.py` — Closeout.
- `test_lck_delivery.py` — Delivery completion and bounded effects.
- `test_lck_structure.py` — facade/module dependency guardrails.

`tests/tools/test_lck.py` intentionally retains only the long-lived Critical Outcome
verification node used by historical Task contracts.
