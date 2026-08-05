# Workflow evidence and compact validation policy

## Responsibilities

```text
Skill       authorization, phases, semantic judgment, findings, verdict
Evidence    deterministic current Git/GitHub facts and stability snapshots
Validation  deterministic command plans, exit codes, and bounded diagnostics
Maintainer  manual Merge and Feature closeout
```

Evidence and Validation never authorize repository or GitHub writes and never
replace semantic correctness review.

## Current front doors

Runner Skills use the current repository entries directly:

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
tools/agent_workflow/wsl2_validation_runner.py
```

The implementation CLIs are Runner internals:

```text
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_validation.py
```

For each phase, the named Runner is the single mechanical source. Do not run its
implementation CLI or a second equivalent Git/`gh`/`uv` chain after a valid
result.

| Workflow phase | Evidence profile | Validation profile |
| --- | --- | --- |
| Runner Delivery preflight | `delivery` | named `targeted*` only when useful |
| Runner Delivery final | `delivery-readiness` | `workflow-delivery --base-sha <base>` |
| Independent Runner Review | `review`, then `recheck` | `workflow-review --base-sha <base>` |
| Closeout | `closeout-readonly`, then `recheck` | `workflow-closeout --base-sha <PR base>` |

Validation is not reused across Delivery, Review, and Closeout. A targeted
profile is not CI-equivalent.

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

Content identity is reproducibility evidence, not a source hierarchy. Final
Delivery and Review validation bind a clean committed current head. Object SHA
locks remain mandatory even though control-plane version locks do not.

When a PR changes Skills, Runners, Rules, profiles, or workflow governance, an
independent Review directly evaluates those changes, tests, permissions, and
failure behavior. Runner success is supporting evidence, never self-approval.

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

## Evidence contract

The Evidence Runner supports:

```text
delivery
delivery-readiness
review
pre-merge
closeout-readonly
recheck
```

It validates complete argv, repository identity, fixed queries, and fixed
read-only Git operations. It accepts no arbitrary repository, API path, raw
`gh`/Git argv, shell string, output path, or cwd.

Snapshots contain bounded normalized facts, explicit `pass`/`fail`/`unknown`
gates, operation counts, truncation state, content identities, snapshot ID, and
stability fingerprint. Distinguish no Required Checks, plan-limited `403`, real
permission/auth/network/rate-limit failure, pending/failed checks, and unavailable
facts.

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
validators. Delivery and Review require a clean committed worktree.
`workflow-closeout` additionally requires clean local `main == origin/main`.

Success stdout is a compact digest. Full redacted results and bounded failure
diagnostics remain in the ignored validation directory.

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
