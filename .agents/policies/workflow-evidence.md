# Workflow evidence and compact validation policy

## Purpose and responsibility

This is the shared normative source for deterministic evidence, compact
validation, stability rechecks, and success reports used by the four workflow
Skills.

```text
Skill       authorization, phases, semantic judgment, findings, verdict
Evidence    current mechanical facts, normalization, bounded snapshots
Validation  current applicable checks, bounded results
Maintainer  manual Merge and Feature closeout
```

Evidence never authorizes a write or proves semantic correctness.

## Local data and command routing

Tools may write only below the exact Git-ignored roots:

```text
.agents/evidence.local/
.agents/validation.local/
```

Never stage, commit, attach, or treat these files as repository truth. Stored
metadata and diagnostics must be bounded and must exclude credentials, auth
headers, cookies, complete environment variables, private keys, complete Issue
or PR bodies, source, complete diffs, private reasoning, transcripts, unbounded
command output, and machine-sensitive absolute paths.

The active Skill authorizes an operation; `command-execution.md` only selects
its execution context. Tool failure is not a workflow verdict. A Skill may use
its safe read-only fallback, but must report the fallback and limitation. Failed
or incomplete evidence is never `pass`.

## Evidence snapshots

Use the phase operation instead of repeating the legacy mechanical query chain:

```text
delivery-preflight       delivery-readiness
pr-review-snapshot       pr-review-recheck
closeout-plan            closeout-final
feature-audit-snapshot   feature-audit-recheck
```

The WSL2 fixed front door is:

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
```

It maps named Task workflow profiles to the operations above, validates complete
argv and repository identity, disables `git fetch`, compares fixed remote refs
with `git ls-remote`, and preserves partial/fail semantics. Task #85 controls
whether and when workflow Skills replace their current invocation paths with this
runner.

Each snapshot must be deterministic, machine-readable, bounded, and include:

- repository and subject identity;
- trusted SHA, runner source SHA/content digest, and applicable expected and
  observed base/head/merge/main identities;
- applicable labels, Project state, relationships, branch facts, checks,
  threads, and direct-child metadata;
- explicit truncation markers and aggregate operation counts;
- distinct `pass`, `fail`, and `unknown` gates;
- distinct handling of plan-limit `403`, absent Required Checks, pending or
  failed checks, endpoint failure, and unavailable facts;
- a snapshot ID and stability fingerprint.

The Agent still reads the complete current Task/Feature body, PR diff, and
relevant source, tests, docs, and governance. Snapshot metadata does not replace
specification review, code review, acceptance mapping, integration review, or
safety judgment.

A snapshot is current only when generated. Rechecks recollect facts and compare
applicable identity/title, base/head, effective diff, checks, threads, merge,
`origin/main`, audited main, and direct-child set. Material drift invokes the
Skill's stop or re-review rule; an old snapshot is never current evidence.

## Trusted control plane

A reviewed change must not control its own review. If a PR changes applicable
agent rules, Skills, this policy, command policy, Evidence, Validation,
or another shared governance dependency, use locked PR-base versions. Feature
audits use the locked audited-main versions.

The bootstrap must itself come from the trusted commit or a detached trusted
worktree. Then use `trusted_runner.py` to extract Evidence/Validation from the
same commit and record trusted SHA, runner source/content digest, reviewed head
or audited main, and diff/snapshot digest.

```text
python tools/agent_workflow/trusted_runner.py \
  --trusted-sha <trusted-sha> --tool evidence -- \
  pr-review-snapshot ...
```

A PR-head bootstrap cannot establish trust in that same head. If control-plane
isolation cannot be proven, stop without a passing verdict.

## Validation

Run `workflow_validation.py run --phase <phase>`. For PR work pass the locked
base SHA so governance changes are detected from `base...HEAD`. The runner uses
current workflows, `pyproject.toml`, lock files, repository structure, and Skill
validator rules.

It must not skip or weaken applicable checks, reuse results across phases or
SHAs, label a partial set CI-equivalent, or hide a missing required tool.
Delivery, independent review, closeout, and Feature audit remain separate
validation observations.

Success output is command ID, exit code, duration, status, and short summary.
Failure includes bounded diagnostics. Sanitized size-limited logs may be written
only to the ignored validation directory.

## Skill integration and reports

Normal Skills use Evidence/Validation **instead of** the full legacy mechanical
chain; they do not run both. Skills retain scope, permissions, trusted-control
plane, identity/specification gates, phase order, stop conditions, exact writes,
implementation/semantic review, acceptance mapping, severity/verdict, fallback,
and report contracts.

Compact success reporting is allowed only when required evidence and validation
pass with no drift, finding, fallback, conflict, or maintainer decision. It still
includes canonical identity, relevant URLs/branches/SHAs, scope summary,
validation/checks, lifecycle state, threads, limitations, operations not
performed, and exact next step. Feature reports additionally include audited
main, direct-child and acceptance summaries, findings, and one fixed verdict.

Any abnormal path uses a detailed report. Handoffs never copy complete bodies,
diffs, successful logs, or prior reports. Delivery handoff locates the review
object and is not review evidence.

## External Token analysis boundary

Repository workflow Skills do not start, update, validate, or summarize a runtime
usage run. Token-consumption analysis is an external maintainer activity based on
Codex rollout logs and Task metadata. Do not add queries, validation, report fields,
local writes, fallbacks, or verdict branches solely for that analysis. Raw rollout
logs and generated analysis reports are not repository artifacts, and missing or
failed external analysis never changes a Skill verdict.
