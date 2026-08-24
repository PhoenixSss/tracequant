# Workflow evidence and compact validation policy

## Responsibilities

```text
LCK         lifecycle control and current mechanical authority for migrated phases
Skill       semantic procedure, judgment, findings, and human-facing report
Evidence    deterministic compatibility/audit facts for non-migrated or historical paths
Validation  deterministic command plans, exit codes, and bounded diagnostics
Maintainer  explicit remediation start, manual Merge, and Feature closeout
```

Evidence and Validation never authorize repository or GitHub writes and never
replace semantic correctness review. For migrated Delivery / Review / Remediation
phases, LCK reacquires the current mechanical facts itself; persisted Evidence
snapshots and cross-phase handoffs are not lifecycle authority.

## Current front doors

Migrated lifecycle phases enter through LCK:

```text
tools/agent_workflow/lck.py delivery prepare|complete
tools/agent_workflow/lck.py review prepare|complete
tools/agent_workflow/lck.py remediation prepare|complete
```

The Validation Runner remains current for bounded deterministic validation;
historical Evidence output is audit material only and is not a Task lifecycle
entry point:

```text
tools/agent_workflow/wsl2_validation_runner.py
```

Their implementation CLIs are Runner/LCK internals, not alternate Skill write routes:

```text
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_validation.py
```

| Workflow phase | Current mechanical front door | Validation |
| --- | --- | --- |
| Initial Delivery | `lck.py delivery prepare|complete` | LCK runs formal Delivery validation |
| Independent Review | `lck.py review prepare|complete` | LCK runs formal Review validation on the live-resolved head |
| Explicit Remediation | `lck.py remediation prepare|complete` | LCK reuses migrated Delivery validation/effects |
| Closeout | `lck.py closeout <TASK>` | LCK closeout gate and effects |

Historical Evidence snapshots may locate audit material, but they must not
select or authorize a current Task target. A targeted validation profile is not
CI-equivalent.

## Execution identity

Use the Skill and Runner explicitly invoked from the current repository. Do not
load them from `main`, a PR base, audited main, or another commit. Do not create
a detached worktree or bundle merely to obtain a different control-plane
version.

Every result artifact records, when applicable:

- Skill name/path and content SHA-256;
- Runner, profile specification, Rules, and implementation content SHA-256;
- profile/schema/Runner versions;
- repository head and clean/dirty state;
- Task, PR, base/head/effective-diff, audited-main, or merge identities.

Content identity is reproducibility evidence, not a source hierarchy. LCK binds
formal Delivery / Review validation to the current invocation target. Review
head/base applicability is invocation-local; it is not a reusable cross-phase
freshness contract or persisted authorization token.

When a PR changes Skills, Runners, Rules, profiles, or workflow governance, an
independent Review directly evaluates those changes, tests, permissions, and
failure behavior. Runner success is supporting evidence, never self-approval.

## PR resolve/create

`tools/agent_workflow/pr_resolve.py` is a shared deterministic helper used by
LCK Delivery effects. It is not a Skill entry point and does not own lifecycle
state. LCK supplies the live-resolved Task/base/branch context and rechecks the
result before any dependent effect. Agents and Skills do not call this helper
directly or supply PR identity as authority.

Semantic implementation self-checking remains Agent-owned and ephemeral. The
pre-LCK durable `self_review.py` binder/artifact gate was removed during LCK v1
cleanup; no self-review artifact can authorize Delivery or Independent Review.

## Local artifacts

Tools may write only below the exact Git-ignored roots:

```text
.agents/evidence.local/
.agents/validation.local/
```

Never stage or commit these files. Stored output must be bounded and must exclude
credentials, auth headers, cookies, private keys, complete environment dumps,
private reasoning, transcripts, unbounded source/diffs, and machine-sensitive
absolute paths.

## Historical evidence contract

The historical Evidence implementation may still be used for Feature audit
evidence:

```text
feature-audit-snapshot
feature-audit-recheck
```

These operations are read-only audit evidence. They are not a formal Task
phase selector, do not authorize writes, and do not create a cross-phase
freshness or handoff contract.

Audit artifacts contain bounded normalized facts and explicit
`pass`/`fail`/`unknown` gates. They are historical evidence, not lifecycle
authority.

The Agent still reads complete current specifications, diffs, source, tests,
docs, and governance for semantic work. Snapshot metadata is not semantic
review.

## Validation contract

Reusable profiles:

```text
current-ci-equivalent
targeted
targeted:tools-tests
targeted:workflow-tests
post-merge
```

Workflow profiles:

```text
workflow-delivery --base-sha <base>
workflow-review --base-sha <base>
workflow-closeout --base-sha <PR base>
```

Workflow profiles run the current CI-equivalent plan and all repository Skill
validators. LCK invokes formal Review validation inside the isolated exact-head
standalone Review clone before sealing the temporary repository read-only; the profile result
is invocation evidence, not a cross-phase snapshot. `workflow-closeout` additionally
requires clean local `main == origin/main`.

Success stdout is a compact digest. Full redacted results and bounded failure
diagnostics remain in the ignored validation directory.

## LCK admission and Recovery boundary

Delivery Preflight is a terminal admission gate, not a remediation phase.
Recovery rules apply only after Invocation Preflight has passed and never
authorize remediation of a Preflight result. A valid non-pass Preflight
result (`fail`, `partial`, `unknown`, `blocked`, lifecycle conflict,
identity conflict, incompatible entry state, or maintainer-decision-required)
is a final disposition — not a recoverable state.

LCK pass is required before a phase-owned effect may run. Any non-pass
admission (`fail`, `partial`, `unknown`, `blocked`, lifecycle conflict,
identity conflict, incompatible entry state, or maintainer-decision-required)
forbids all write operations, auto-remediation, state modification, and
re-invocation of the same operation to obtain `pass`.

Historical Task evidence records may mention the old `worktree_state_compatible`
gate and Delivery entry-point names. Those records are audit provenance only; the
executable Task Evidence Runner, profiles, and approval Rules have been removed.

Historical remediation evidence is not part of the LCK Review / Remediation
lifecycle and MUST NOT authorize a current repair from an expected base/head
or bounded handoff. Current remediation authority is `lck.py remediation
prepare`, which reacquires the live Task/PR/head/base and uses the failed
Review record only for semantic findings.

## Failure expansion

`partial`, `unknown`, `fail`, drift, truncation, schema mismatch, or Runner
unavailability never becomes `pass` through selective fallback.

1. Preserve the original result and identity.
2. Inspect only the named gate, fact, or failed-command log.
3. Record the limitation or fallback.
4. Do not rerun an equivalent complete command chain.
5. Stop before a dependent write or verdict while the gate is unresolved.

## Capability-limited cleanup

A recognized Required-Checks plan-limit `403` remains
`required_checks_configuration = unknown` and keeps Evidence `partial`.
Closeout may compute `eligible-under-capability-limited-policy` only for exact
Task branch cleanup and only under the complete conditions defined by
`task-closeout`. It never authorizes Merge, push, Issue/Project/label writes, or
Review actions.

## Reporting and external analysis

Compact success reporting requires complete evidence, validation pass, stable
identity, no unresolved finding, no fallback/conflict, and no pending maintainer
decision. Abnormal paths use a detailed report.

Repository Skills do not measure runtime Token use. Token analysis remains an
external maintainer activity based on Codex rollout logs and Task metadata.
