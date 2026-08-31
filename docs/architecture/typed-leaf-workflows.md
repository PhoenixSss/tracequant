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
traceable through their real typed Issues:

- Documentation: [Issue #212](https://github.com/PhoenixSss/tracequant/issues/212),
  which completed the repository-backed Documentation lifecycle.
- Research: [Issue #191](https://github.com/PhoenixSss/tracequant/issues/191),
  which completed the repository-backed Research lifecycle.
- Bug: [Issue #201](https://github.com/PhoenixSss/tracequant/issues/201),
  which completed the heading-level Critical Outcome Bug repair and regression
  lifecycle.
- Task production path: the current [Task #202](https://github.com/PhoenixSss/tracequant/issues/202)
  lifecycle.

Issues #198, #199, and #200 are retained as historical Task implementation
fixtures because their current labels are `type:task`; they are not typed
activation receipts. Live LCK receipts, PR identities, fresh Review, and
post-merge Closeout remain the authoritative evidence for each execution.
