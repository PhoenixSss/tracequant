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
tools/agent_workflow/lck.py remediation prepare|no-change|complete
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
| Explicit Remediation | `lck.py remediation prepare|no-change|complete` | LCK reuses migrated Delivery validation/effects; no-change closes an unchanged prepared session |
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

Mutable artifact roots have distinct owners and MUST NOT be treated as interchangeable
fallback locations:

```text
.agents/evidence.local/            historical/non-LCK Evidence Runner output
.agents/validation.local/          Validation Runner output in the workspace being validated
.workflow.local/lck/               LCK runtime state and durable LCK Review evidence
```

All repository-local roots above must be Git ignored and MUST never be staged or
committed. The ownership boundary for Independent Review is stricter: the source
repository writes Review runtime state and preserved evidence only below
`.workflow.local/lck/`. Formal Review validation runs inside the operation-owned
standalone clone and may emit its ordinary `.agents/validation.local/` result **inside
that clone**; before the clone is sealed/deleted, LCK copies only the bounded evidence
that must survive to `.workflow.local/lck/review-validation/` in the source repository.
Independent Review MUST NOT write source `.agents/evidence.local/` or source
`.agents/validation.local/`.

Stored output must be bounded and must exclude credentials, auth headers, cookies,
private keys, complete environment dumps, private reasoning, transcripts, unbounded
source/diffs, and machine-sensitive absolute paths.

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
diagnostics remain in the artifact root owned by that execution context: ordinary
Validation Runner invocations use `.agents/validation.local/`, while formal Review
results that must outlive the temporary clone are preserved under
`.workflow.local/lck/review-validation/`.

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
prepare`, which reacquires the live Task/PR/head/base. A workspace-local failed
Review record is the default semantic-findings source only. If a maintainer
intentionally switches clone or Agent runtime and that ignored audit record is
unavailable, `remediation prepare --findings-file <FILE>` may carry the completed
Review findings across that boundary. This is a semantic-only handoff: it cannot
supply or override PR/head/base/branch/check/check-policy authority, and the normal
Codex/local-record path remains unchanged.

A prepared Remediation session is operation-continuity state, not cross-phase
authority, but it must still reach a formal terminal operation. If semantic
inspection finds no implementation change is required, `remediation no-change`
reacquires the current PR/base/head, requires the selected Task workspace to be
clean and unchanged, writes a local no-change receipt, and releases the session.
It does not commit, push, create a new head, set `fresh-review-required`, or
satisfy deferred provider/cross-runtime Review acceptance. While a prepared
Remediation session remains open, Review Prepare fails closed rather than
interleaving a new Review with an unfinished implementation role.

## Failure expansion

`partial`, `unknown`, `fail`, drift, truncation, schema mismatch, or Runner
unavailability never becomes `pass` through selective fallback.

1. Preserve the original result and identity.
2. Inspect only the named gate, fact, or failed-command log.
3. Record the limitation or fallback.
4. Do not rerun an equivalent complete command chain.
5. Stop before a dependent write or verdict while the gate is unresolved.

## Capability-limited cleanup

Historical/read-only Evidence diagnostics may still observe a Required-Checks
plan-limit `403`; in that evidence model the observation remains
`required_checks_configuration = unknown` and keeps Evidence `partial`. It is
never LCK lifecycle authority.

LCK itself does **not** discover its required-check policy from the
plan/permission-dependent branch-protection endpoint. The repository-controlled
required-check policy is derived from the statically named jobs in the canonical
`.github/workflows/ci.yml`, read from the operation's **exact trusted base commit**
rather than from the caller's mutable checkout or candidate head. LCK v1 fails
closed on dynamic job names or matrix jobs instead of guessing a check identity.
Delivery Complete uses current authoritative `main`; Review / Remediation / Merge
Preflight use the exact PR base. The snapshot records the policy source SHA and
contract hash. Candidate-head policy edits are future policy only and cannot
self-authorize the candidate. GitHub supplies only the live result for the
base-required names on the exact PR head. Missing, malformed, unavailable, or
base-mismatched policy fails closed before dependent lifecycle effects; there is
no working-tree or GitHub-discovery fallback.

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
