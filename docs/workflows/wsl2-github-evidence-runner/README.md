# WSL2 GitHub Evidence Runner

## Purpose

`wsl2_github_evidence_runner.py` is the single read-only mechanical entry for
Task/PR workflow facts. It validates complete argv, repository and object
identity, fixed profile/schema, query boundaries, output paths, and snapshot
stability before returning a compact result.

## Entry points

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py delivery \
  --task <TASK> --expected-main-sha <SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py delivery-readiness \
  --task <TASK> --pr <PR> \
  --expected-base-sha <SHA> --expected-head-sha <SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py review \
  --task <TASK> --pr <PR> \
  --expected-base-sha <SHA> --expected-head-sha <SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py pre-merge \
  --task <TASK> --pr <PR> \
  --expected-base-sha <SHA> --expected-head-sha <SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py closeout-readonly \
  --task <TASK> --pr <PR> \
  --expected-head-sha <SHA> --expected-merge-sha <SHA>

tools/agent_workflow/wsl2_github_evidence_runner.py recheck \
  --snapshot-id <SNAPSHOT_ID>
```

Profiles are fixed in `wsl2_github_evidence_profiles.json`. The Runner invokes
`workflow_evidence.py` once, stores bounded artifacts below ignored
`.agents/evidence.local/`, checks current remote refs, and prints one compact
JSON digest.

## Execution identity

The Runner uses the current repository files. Results record the active Skill,
Runner, profile specification, Rules, evidence tool, shared helper, repository
head/worktree state, and content hashes. These values provide reproducibility;
they are not a main/base version gate.

Task base, PR base/head/effective diff, audited main, merge SHA, and snapshot
fingerprints remain locked as identities of the object being processed.

## Status model

- `pass` / exit `0`: all applicable gates pass and no current evidence warning
  or truncation remains;
- `partial` / exit `3`: one or more facts are unknown, unavailable, truncated,
  or capability-limited without a confirmed failing gate;
- `fail` / exit `4`: a required identity, linkage, state, check, thread, or
  stability gate fails;
- Runner contract/I/O error / exit `2`: invocation, schema, repository, or
  artifact handling failed.

A plan-limit Required Checks `403` remains `partial`; it is never converted into
full required-check evidence. Other auth, scope, rate-limit, network, and schema
failures remain separately classified.

## Safety boundary

The Runner:

- accepts only one fixed repository and fixed profile argv;
- accepts no arbitrary repository, API, GraphQL, Git, shell, cwd, or output
  argument;
- performs fixed read-only GitHub and Git observations;
- never writes Issue, PR, Project, Review, label, Relationship, branch, commit,
  or merge state;
- rejects symlink entry, wrong origin/root, injected/trailing argv, and snapshot
  tampering;
- bounds lists and output, records truncation, and redacts sensitive values;
- compares current remote main/head with observed identities without repairing
  drift.

Rules route only the fixed Runner profile prefixes. The Runner validates the
complete command and object identities.

## Bounded fallback

For `partial`, `unknown`, `fail`, truncation, schema mismatch, or drift, inspect
only the named fact or gate. Do not replay the complete Git/GitHub query set.
Use `recheck` for a stability-sensitive decision rather than reusing an old
snapshot.
