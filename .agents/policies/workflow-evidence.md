# Workflow evidence and compact validation policy

## Purpose and responsibility

This is the shared normative source for deterministic workflow evidence,
fixed-runner validation, stability rechecks, failure expansion, and compact
success reports.

```text
Skill       authorization, phases, semantic judgment, findings, verdict
Evidence    current mechanical facts, normalization, snapshot/recheck
Validation  current applicable checks, exit codes, bounded diagnostics
Maintainer  manual Merge and Feature closeout
```

Evidence and Validation never authorize repository or GitHub writes and never
replace semantic correctness review.

## Fixed Task workflow front doors

After Task #85, normal Task Skills invoke these repository entries directly:

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
tools/agent_workflow/wsl2_validation_runner.py
tools/agent_workflow/trusted_runner.py   # independent Review bootstrap only
```

The underlying implementation CLIs remain versioned components:

```text
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_validation.py
```

They are not normal Task Skill entry points. Do not run a fixed Runner and then
repeat its raw implementation CLI or the legacy direct Git/`gh`/`uv` chain for
the same phase.

### Authoritative Task mapping

| Skill phase | Evidence profile | Validation profile |
| --- | --- | --- |
| Delivery preflight | `delivery` | named `targeted*` only when useful during implementation |
| Delivery final | `delivery-readiness` | `workflow-delivery --base-sha <base>` |
| Independent Review | trusted-base `review`, then trusted-base `recheck` | trusted-base `workflow-review --base-sha <base>` |
| Closeout | `closeout-readonly`, then `recheck` | `workflow-closeout --base-sha <PR base>` |

The phase profile is the authoritative final validation observation. A targeted
profile is not CI-equivalent. Validation is never reused across Delivery,
Review, and Closeout.

Feature completion audit is outside Task #85. It keeps its existing trusted raw
Evidence/Validation path until a separately approved migration.

## Local data and command routing

Tools may write only below the exact Git-ignored roots:

```text
.agents/evidence.local/
.agents/validation.local/
```

Never stage, commit, attach, or treat these files as repository truth. Stored
metadata and diagnostics must be bounded and exclude credentials, auth headers,
cookies, complete environment variables, private keys, complete Issue/PR
bodies, source, complete diffs, private reasoning, transcripts, unbounded output,
and machine-sensitive absolute paths.

The active Skill authorizes an operation; `command-execution.md` selects only
its execution context. A Runner failure is not a workflow verdict. Failed,
partial, unknown, stale, or conflicting evidence is never `pass`.

## Evidence profiles and snapshots

The fixed Evidence Runner supports:

```text
delivery
delivery-readiness
review
pre-merge
closeout-readonly
recheck
```

It validates the complete argv, repository identity, trusted files, fixed GitHub
queries, and fixed read-only Git operations. It accepts no arbitrary repository,
REST/GraphQL path, `gh`/Git argument, shell string, output path, or cwd.

Each snapshot must be deterministic, machine-readable, bounded, and include:

- repository and subject identity;
- expected and observed base/head/merge/main identities;
- trusted SHA and Runner/tool content digests;
- applicable labels, Project state, Relationships, branch facts, checks, threads,
  changed files/commits, and diff digest;
- explicit truncation and aggregate operation counts;
- distinct `pass`, `fail`, and `unknown` gates;
- distinct plan-limit `403`, absent Required Checks, pending/failed checks,
  endpoint failure, and unavailable facts;
- a separate capability-limited cleanup eligibility field for exact Task branch
  cleanup when, and only when, a recognized Required Checks plan-limit `403` is
  preserved as an unknown gate;
- snapshot ID and stability fingerprint.

The Agent still reads complete current specifications, PR diff, source, tests,
docs, and governance for semantic work. Snapshot metadata does not replace
acceptance mapping, code review, integration review, or safety judgment.

A snapshot is current only when generated. Recheck recollects current facts and
compares applicable identity, base/head, diff, checks, threads, merge, refs, and
branch state. Material drift invokes the Skill's stop/re-review rule; an old
snapshot is never current evidence.

## Validation profiles

The fixed Validation Runner retains these reusable profiles:

```text
current-ci-equivalent
targeted
targeted:tools-tests
targeted:workflow-tests
post-merge
```

Task Skills use the phase profiles added by Task #85:

```text
workflow-delivery --base-sha <base>
workflow-review --base-sha <base>
workflow-closeout --base-sha <PR base>
```

Each phase profile invokes the versioned workflow validator once, runs current
applicable CI-equivalent commands, detects governance changes from `base...HEAD`,
and requires all repository Skill validators. `workflow-closeout` additionally
requires clean local `main == origin/main`.

Success stdout is a compact digest. Failure preserves the failed command, exit
code, duration, digests, truncation state, and bounded diagnostics in the ignored
validation directory. A compact success summary is never permission to skip an
internal command.

## Trusted independent Review control plane

A reviewed change must not control its own review. The bootstrap
`trusted_runner.py` must come from the locked PR base or a detached trusted-base
worktree. It extracts a complete fixed Evidence or Validation front-door bundle
from that same commit and records the trusted SHA and file digests.

Normal future Review uses:

```text
--tool evidence-runner
--tool validation-runner
```

The extracted front door operates on the reviewed target repository while its
Runner, profiles, Rules, and underlying tool implementation remain the locked
base versions. If bootstrap isolation cannot be proven, stop without pass.

The Task #85 migration PR itself is a one-time bootstrap exception: its base
contains only the predecessor trusted raw-tool choices. Its independent review
uses that predecessor base control plane to review the migration. After merge,
this exception expires; later Task reviews use trusted fixed front doors.

## No-duplication and fallback contract

For one phase/fact, there is one authoritative Runner source. Never:

- run both fixed and raw Evidence for the same phase;
- run the fixed phase Validation profile and a second full direct `uv` chain;
- repeat all direct `gh`/Git reads after a valid snapshot;
- treat a Delivery snapshot or conclusion as Review evidence;
- convert partial/unknown into success through selective fallback.

When a Runner returns `partial`, `unknown`, `fail`, drift, truncation, or a
schema/version mismatch, expand only the named target:

1. retain the original status and snapshot/result identity;
2. perform the minimum authorized bounded read or inspect the failed-command log;
3. record the fallback and limitation;
4. do not rerun the complete replaced path;
5. stop before any dependent write or verdict when the gate remains unresolved.

Runner unavailability or incompatibility is a control-plane failure. Rollback to
the predecessor path is a maintainer-authorized repository change, not an
implicit runtime fallback.

## Capability-limited cleanup eligibility

A recognized GitHub Required Checks plan-limit `403` remains
`required_checks_configuration = unknown` and keeps the Evidence result `partial`.
Closeout may compute a separate
`cleanup_eligibility.status = eligible-under-capability-limited-policy` only for
exact Task branch cleanup. This derived judgment never changes Required Checks
semantics and must not be reused for Merge, push, Issue, Project, label, review,
or other writes.

Eligibility requires a merged PR, correct closing linkage, final Task metadata,
stable recheck, successful observed check runs, synchronized local and remote
main at the merge SHA, exact local and remote branch names and tips, merge-tree
equality with the reviewed head, a clean worktree, fixed repository identity, no
target-branch worktree use, and a cleanup plan containing only the verified Task
branch. Authentication, scope, permission, rate-limit, network, schema, service,
unknown Required Checks failures, missing check runs, failed/pending/stale
checks, ref drift, tree drift, or worktree conflicts keep cleanup blocked.

## Skill integration and reports

Skills retain scope, permissions, trusted-control-plane rules, phase order,
identity/specification gates, exact writes, semantic implementation/review,
acceptance mapping, severity/verdict, stop conditions, and report contracts.
Runners remove duplicate mechanical paths; they do not remove responsibilities.

Compact success reporting is allowed only with complete required evidence,
validation pass, no drift, no finding, no fallback, no conflict, and no pending
maintainer decision. It still includes canonical identity, URLs/branches/SHAs,
scope summary, validation/checks, lifecycle state, threads, limitations,
actions not performed, and exact next step.

Any abnormal path uses a detailed report. Handoffs never copy complete bodies,
diffs, successful logs, or prior reports. Delivery handoff is not Review proof.

## External Token analysis boundary

Repository Skills do not start, update, validate, or summarize runtime Token
usage. Token analysis remains an external maintainer activity based on Codex
rollout logs and Task metadata. Do not add queries, validation, local writes,
fallbacks, or verdict branches solely for Token analysis. Missing external
analysis never changes workflow permissions or verdicts.
