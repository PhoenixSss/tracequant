# Typed leaf workflow architecture

This is the short navigation map for the four supported leaf Issue profiles.
The complete lifecycle contract remains in
[`docs/development/issue-workflow.md`](../development/issue-workflow.md) and
the Independent Review contract remains in
[`docs/development/pr-review.md`](../development/pr-review.md).

## Routing and ownership

```text
canonical type:* label
        ↓
lck_core/issue_profiles.py          # one resolver and profile registry
        ↓
lck_core/profile_policies.py       # one profile-policy dispatch layer
        ↓
shared LCK phase controllers        # one Delivery / Review / Remediation / Closeout kernel
        ↓
Git/GitHub effects, receipts, and lifecycle state
```

The resolver accepts exactly one canonical leaf label. Missing, conflicting,
unknown, `type:feature`, and `type:epic` labels stop before effects. Codex and
Claude select equivalent semantic contracts; neither provider owns a second
lifecycle controller.

| Type label | Semantic contract owner | Profile-specific policy | Branch namespace | Critical Outcome |
| --- | --- | --- | --- | --- |
| `type:task` | Task Issue body | `critical_outcome.py` | `task/` (legacy aliases remain compatible) | required |
| `type:bug` | Bug Issue form | `bug_policy.py` | `bug/` | not required |
| `type:documentation` | Documentation Issue form | `documentation_policy.py` | `documentation/` | not required |
| `type:research` | Research Issue form | `research_policy.py` | `research/` | not required |

The profile registry stores the policy selectors and candidate capabilities.
`profile_policies.py` is the only adapter that dispatches those selectors to
the typed policy modules. Phase controllers do not maintain a type allowlist;
they call the shared adapter and retain one mechanical owner for live-state
resolution, identity/freshness, validation, effects, receipts, and recovery.

## Ordinary profile extension contract

Adding an ordinary leaf profile is a registration change. The implementation
may add a profile policy, one registration entry to the existing generic
registry, profile-owned contract/evidence/blocker schema and codecs, and
profile-specific tests. It must use the existing generic Issue Form parser,
policy blocker contract, evidence envelope, effect descriptor/executor,
phase controllers, and receipt infrastructure.

The shared Delivery, Review, Remediation, and Closeout kernel must not gain a
profile-specific branch, concrete policy import, fixed result/model/receipt
field, or new profile semantics in shared facts. Eligibility must aggregate
the generic shared blocker gate and registered policy blockers; it must not
contain a special case for a profile or blocker. A profile cannot use an
arbitrary callable or hidden side effect to bypass the registry, policy
validation, bounded effect executor, postcondition, or receipt authority.

The generic registry injection seam is test-only for architecture acceptance:
a synthetic fifth profile is registered in an independent registry and is
resolved by an injected profile resolver. The production canonical registry
and its four supported profiles remain unchanged. This fixture must traverse
the same contract, blocker, candidate, review, completion, effect, and
receipt capabilities as a real profile.

Extending a generic protocol or adding a previously absent generic blocker,
evidence, effect, parser, or Kernel capability is an architecture exception.
It requires a separately designed and reviewed architecture change; it is not
part of an ordinary profile registration. The acceptance suite in
`tests/tools/test_lck_profile_architecture.py` mechanically checks both sets
of boundaries and the shared Kernel invariants, including immutable snapshots,
fresh Review identity, maintainer-only merge, bounded effects, postconditions,
and audit receipt authority.

## Activation evidence

The typed activation implementations are merged and remain independently
traceable through their real typed Issues:

- Documentation: [Issue #212](https://github.com/PhoenixSss/tracequant/issues/212),
  which completed the repository-backed Documentation lifecycle.
- Research: [Issue #191](https://github.com/PhoenixSss/tracequant/issues/191),
  merged as [PR #216](https://github.com/PhoenixSss/tracequant/pull/216) with the
  versioned artifact
  [`docs/research/binance-usdm-public-history-source-contract.md`](../research/binance-usdm-public-history-source-contract.md);
  its canonical Project `Research Outcome` is `ARCHITECTURE DECISION`.
- Bug: [Issue #201](https://github.com/PhoenixSss/tracequant/issues/201),
  which completed the heading-level Critical Outcome Bug repair and regression
  lifecycle.
- Task production path: the current [Task #202](https://github.com/PhoenixSss/tracequant/issues/202)
  lifecycle.

Issues #198, #199, and #200 are retained as historical Task implementation
fixtures because their current labels are `type:task`; they are not typed
activation receipts. Live LCK receipts, PR identities, fresh Review, and
post-merge Closeout remain the authoritative evidence for each execution.
