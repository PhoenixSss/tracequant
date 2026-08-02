# WSL2 GitHub Evidence Runner

## Purpose

`tools/agent_workflow/wsl2_github_evidence_runner.py` is the fixed, read-only
front door for Task workflow evidence collection in the supported WSL2
workspace.

It consolidates Task, Pull Request, checks, review threads, Project status,
changed files, commits, diff identity, and local/remote Git facts into one
bounded invocation. It does not comment, edit labels, update Project fields,
close Issues, push, merge, fetch, switch branches, or delete branches.

Task #85 may switch workflow Skills to this runner. Task #84 only establishes
the runner, evidence contract, Rules, approval boundary, tests, and integration
instructions.

## Fixed repository and trusted files

The runner is restricted to:

```text
PhoenixSss/quant-system
```

It must be launched from the repository root on the WSL2 Linux filesystem. It
rejects `/mnt` workspaces, symlink entry points, wrong origins, unknown profiles,
unknown arguments, arbitrary repository names, raw REST/GraphQL paths, arbitrary
`gh`/Git arguments, shell strings, and output paths.

Before any evidence subprocess runs, the following files must match their
tracked `HEAD` blobs:

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
tools/agent_workflow/wsl2_github_evidence_profiles.json
.codex/rules/quant-system-wsl-evidence.rules
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_common.py
```

Intentional changes to these files must be committed before the fixed runner can
be exercised again.

## Profiles

### Delivery preflight

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py delivery \
  --task 84 \
  --expected-main-sha <FULL_MAIN_SHA>
```

Collects Task, relationship, local Git, `origin/main`, and remote `main` facts
without a PR requirement.

### Delivery readiness

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py delivery-readiness \
  --task 84 \
  --pr <PR_NUMBER> \
  --expected-base-sha <FULL_BASE_SHA> \
  --expected-head-sha <FULL_HEAD_SHA>
```

### Independent review

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py review \
  --task 84 \
  --pr <PR_NUMBER> \
  --expected-base-sha <FULL_BASE_SHA> \
  --expected-head-sha <FULL_HEAD_SHA>
```

### Pre-merge revalidation

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py pre-merge \
  --task 84 \
  --pr <PR_NUMBER> \
  --expected-base-sha <FULL_BASE_SHA> \
  --expected-head-sha <FULL_HEAD_SHA>
```

### Read-only closeout evidence

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py closeout-readonly \
  --task 84 \
  --pr <PR_NUMBER> \
  --expected-head-sha <FULL_REVIEWED_HEAD_SHA> \
  --expected-merge-sha <FULL_MERGE_SHA>
```

This profile plans and verifies closeout facts only. It does not close the Issue,
update Project fields, fetch, switch, delete branches, or perform cleanup.

### Snapshot recheck

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py recheck \
  --snapshot-id ev-0123456789abcdef
```

Only snapshots created by supported `workflow_evidence.py` Task/PR operations
can be rechecked. Head, checks, threads, Project/Issue metadata, diff identity,
and applicable Git identities are recollected. Material drift produces a failed
result.

## Result and exit contract

The model-visible stdout is a compact JSON digest. The full normalized result is
written below:

```text
.agents/evidence.local/wsl2-github-runs/<RUN_ID>/result.json
```

The result contains:

```yaml
identity:
  task:
  pr:
  repository:
  base_sha:
  head_sha:
  merge_sha:
issue:
  state:
  labels:
  project_status:
pull_request:
  state:
  draft:
  mergeable:
  checks:
  unresolved_threads:
scope:
  changed_files:
  commits:
  diff_digest:
git:
  current_branch:
  working_tree_clean:
  local_main:
  origin_main:
  remote_main:
  remote_head:
stability:
  snapshot_id:
  snapshot_fingerprint:
  stable:
  changed_fields:
  partial:
```

Exit codes:

| Code | Status | Meaning |
| --- | --- | --- |
| `0` | `pass` | Required fixed evidence is complete and no gate failed. |
| `3` | `partial` | A required fact is unknown, unavailable, truncated, rate-limited, or permission-limited. |
| `4` | `fail` | Identity, linkage, checks, drift, threads, or Git/remote consistency failed. |
| `2` | runner error | Invocation, repository, integrity, profile, or local I/O failed closed. |

A `partial` result is useful evidence but is not complete success. For example,
GitHub plan-limit `403`, unavailable Project/threads data, API rate limits, or
bounded changed-file truncation remain explicit partial states.

## Read-only Git behavior

The underlying workflow evidence tool runs with:

```text
WORKFLOW_EVIDENCE_READ_ONLY=1
```

In this mode it does not run `git fetch`. It reads local refs and the worktree,
while the fixed runner uses `git ls-remote --heads` for current remote `main` and
PR-head comparison. A local `origin/main` mismatch with remote `main`, or an open
PR head mismatch with its remote branch, fails evidence consistency.

The runner's local writes are limited to exact Git-ignored evidence directories.
No raw GitHub token, auth header, cookie, complete environment, Issue/PR body,
source diff, or unbounded command output is stored.

## Rules and complete argv validation

`.codex/rules/quant-system-wsl-evidence.rules` authorizes only the fixed runner
and named profile prefixes.

Codex Rules use prefix matching. An execpolicy `allow` therefore authorizes the
entry/profile prefix, not arbitrary trailing arguments. The runner performs the
complete argument-count, option, value, repository, and snapshot validation
before any GitHub or Git subprocess. Known API/shell/command injection options
are also `forbidden` as defense in depth.

Direct `gh`, direct raw API calls, direct Git commands, Python/shell wrappers,
and write operations are not allow-listed by the evidence Rules.

## Skill integration for Task #85

A consuming Skill should:

1. authorize the lifecycle action and exact profile;
2. invoke one fixed runner command;
3. treat exit `3` as partial, not pass;
4. read the compact digest and referenced normalized result;
5. preserve semantic review and workflow judgment;
6. perform a `recheck` before a stability-sensitive decision;
7. retain all GitHub/Git write gates outside this runner.

Do not run both the fixed runner and the complete legacy mechanical query chain
unless a reported partial/failure requires a documented read-only fallback.


## Publication evidence package

The Task #84 publication package includes:

- `historical-command-baseline.json`: Task #63/#64 aggregate Git, GitHub/`gh`,
  Guardian, duration, and Token context derived from external reports without
  committing raw rollout logs;
- `environment-capability.json`: source-derived WSL2 and GitHub CLI capability
  baseline with explicit live-refresh fields;
- `live-evidence-capture-plan.md`: exact capture and claim contract;
- `templates/`: valid JSON examples for live profile, recheck, Rules, and
  external evidence manifest files;
- `publication-readiness.md`: final-document coverage and remaining local gates;
- `visuals/`: editable historical, live-metric, and capability-drift sources.

The examples are not pass evidence. Local Delivery must copy them to the final
filenames and replace only fields backed by actual observations.

## Live probe

Task #84 Delivery should perform an explicit real-repository probe after the
trusted files are committed:

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py review \
  --task 84 \
  --pr <PR_NUMBER> \
  --expected-base-sha 74a75872078221c38dbd132a1d438b0bb05c1870 \
  --expected-head-sha <DELIVERY_HEAD>
```

Record the compact stdout size, duration, API operation count, Guardian turns,
approvals, elevated executions, status, snapshot ID, result SHA-256, and local
result path. Raw local evidence remains ignored.

## Rollback

Before Task #85 adopts the runner, rollback is simply to stop invoking it and
remove or disable its project Rules. Existing Skills continue to use the current
workflow evidence paths.

After Skill adoption, rollback must be coordinated with the corresponding Skill
change so evidence collection is not skipped.
