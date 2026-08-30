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

## Activation evidence

The typed activation implementations are merged and remain independently
traceable through their repository tasks:

- Documentation: [Task #198](https://github.com/PhoenixSss/tracequant/issues/198)
  and its merged implementation commit `6b19467`.
- Research: [Task #199](https://github.com/PhoenixSss/tracequant/issues/199)
  and its merged implementation commit `83534ab`.
- Bug: [Task #200](https://github.com/PhoenixSss/tracequant/issues/200)
  and its merged implementation commit `500c8ad`.
- Task production path and heading-level Critical Outcome regression:
  [Task #201](https://github.com/PhoenixSss/tracequant/issues/201) and the
  current Task #202 lifecycle.

Task #202's historical requirement names #66 as the Documentation activation;
current GitHub state identifies #66 as the earlier documentation-related
`type:task`, while #198 is the formal typed Documentation activation. This
distinction is recorded rather than presenting a stale Issue number as a
typed receipt. Live LCK receipts, PR identities, fresh Review, and post-merge
Closeout remain the authoritative evidence for each execution.
