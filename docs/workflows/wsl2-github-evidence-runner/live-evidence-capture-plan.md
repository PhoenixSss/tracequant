# Task #84 Live Evidence Capture Plan

This document defines the evidence that must be captured after the Task #84
trusted files are committed and the real Pull Request exists. It prevents
incomplete or inferred metrics from being presented as observed results.

## Evidence classes

The Delivery must create four committed, redacted summaries:

```text
live-profile-evidence.json
live-recheck-evidence.json
rules-live-evidence.json
environment-capability.json
```

Raw runner results, workflow-evidence snapshots, command output, Codex
rollout logs, and credentials remain external under ignored locations.

## 1. Fixed profile observation

Run one real `review` profile from a new Codex session after trusted files
match committed `HEAD`.

Capture:

- exact Task, PR, base SHA, and head SHA;
- profile, status, partial flag, and exit code;
- snapshot ID and fingerprint;
- API query count and fixed runner Git-operation count;
- duration;
- compact stdout byte count, including whether the terminal newline is counted;
- result path and SHA-256;
- Guardian turns, approval prompts, and elevated executions;
- Codex session/rollout locator when exposed, otherwise `unavailable`;
- runner, `gh`, Git, and Codex versions.

The compact digest already includes `api_calls`, `duration_ms`,
`result_path`, `result_sha256`, `snapshot_id`, and status fields. Do not
invent command-level values absent from the normalized result.

## 2. Stability recheck observation

Recheck the exact snapshot created by the live profile:

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py recheck \
  --snapshot-id <SNAPSHOT_ID>
```

Capture stable/changed state, changed fields, status, partial flag, API
calls, duration, stdout bytes, result path, and result SHA-256. A recheck
must not silently substitute old evidence for current GitHub facts.

## 3. Rules observation

Use real `codex execpolicy check` against the committed Rules and record a
matrix that separates policy routing from runner semantic acceptance.

Required cases include:

- every fixed profile: `allow`;
- fixed profile plus arbitrary tail: prefix `allow`;
- the same tailed runner invocation: runner rejects before evidence queries;
- direct `gh`, `gh api`, Git, Python, `bash`, and `sh`: not `allow`;
- Git/GitHub writes: `prompt`, `forbidden`, or unmatched, never `allow`;
- `gh auth token`: `forbidden`.

The matrix must record the actual Codex version.

## 4. Environment capability refresh

Refresh the committed baseline without recording secrets:

- WSL distribution and kernel;
- repository path class (`/home`, not `/mnt`);
- `gh`, Git, uv, and Codex versions;
- authentication status and scope names only;
- Project read capability;
- base/head OID metadata capability;
- Required Checks configuration result and plan-limit classification;
- confirmation that token values, cookies, headers, and proxy credentials
  are absent.

Never run or record `gh auth token`.

## Metrics and claim rules

Use these classifications exactly:

```text
observed      directly captured in this Task #84 run
derived       deterministic calculation from observed fields
expected      architecture or protocol expectation
not-measured  no authoritative observation exists
unavailable   the environment did not expose the field
```

A single Task #84 profile may support the one-invocation mechanism and
output-size observations. It does not establish Task #65 Token reduction,
workflow-wide savings, or candidate superiority.

## Repository inclusion

Commit only bounded summaries and manifests. Keep these external:

```text
.agents/evidence.local/
raw GitHub responses
raw Codex rollout JSONL
authentication output containing sensitive values
complete environment dumps
```

The external manifest should identify raw artifacts by SHA-256 and logical
purpose without copying their contents into the repository.
