# WSL2 Task Workflow Validation Runner

## Purpose

`wsl2_validation_runner.py` is the single mechanical entry for named local
validation contracts. It validates complete argv, repository identity, profile
schema, canonical commands, phase preconditions, CI drift, output location, and
process cleanup before or while running any subcommand.

It compresses successful evidence; it does not authorize workflow lifecycle
writes, Review, Merge, or Closeout.

## Entry points

```bash
tools/agent_workflow/wsl2_validation_runner.py current-ci-equivalent
tools/agent_workflow/wsl2_validation_runner.py targeted
tools/agent_workflow/wsl2_validation_runner.py targeted:tools-tests
tools/agent_workflow/wsl2_validation_runner.py targeted:workflow-tests
tools/agent_workflow/wsl2_validation_runner.py post-merge
tools/agent_workflow/wsl2_validation_runner.py workflow-delivery --base-sha <SHA>
tools/agent_workflow/wsl2_validation_runner.py workflow-review --base-sha <SHA>
tools/agent_workflow/wsl2_validation_runner.py workflow-closeout --base-sha <SHA>
```

The fixed profiles are defined in
`tools/agent_workflow/wsl2_validation_profiles.json`. Workflow profiles invoke
`workflow_validation.py` once with the corresponding phase contract.

`targeted` profiles are development evidence only. They are not complete
CI-equivalent validation.

## Current CI contract

The canonical CI command set is:

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tests
```

CI-equivalent local profiles also run `git diff --check`. The Runner compares the
workflow file, profile specification, and in-code canonical allowlist so drift
fails before evidence is accepted.

## Execution identity

The Runner uses the current repository files. Each result records:

- repository head SHA and clean/dirty state;
- active Skill path and SHA-256 when the profile belongs to a workflow Skill;
- Runner, profile specification, Rules, and workflow validation paths and
  SHA-256 values;
- profile/schema and exact command results.

These values provide reproducibility. They do not create a main/base control
plane and do not require cross-commit extraction.

Final `workflow-delivery`, `workflow-review`, and `workflow-closeout` validation
requires a clean worktree. Development profiles may run while the current files
are being changed.

## Safety boundary

The Runner:

- starts only from the repository root under the Linux filesystem, not `/mnt`;
- rejects symlink entry, wrong repository identity, invalid/trailing argv, and
  non-canonical profile commands;
- accepts no shell string, arbitrary command, arbitrary pytest argument, or
  output path;
- writes only below ignored `.agents/validation.local/wsl2-runs/`;
- starts each child in a dedicated process group and terminates the complete
  group on timeout or interruption;
- stores complete redacted logs locally and prints a compact digest;
- propagates validation, timeout, interruption, and artifact-write failures as
  non-zero exits.

The Rules file authorizes only fixed Runner profile prefixes. Prefix routing is
not semantic acceptance: the Runner validates the complete argv.

## Result contract

Success stdout is one compact JSON object containing profile, status, command
counts, duration, result path, result SHA-256, and no failed command. Detailed
per-command argv, exit code, duration, output, truncation, and hashes remain in
the ignored result directory.

On failure, inspect only the reported failed command and its bounded artifact.
Do not replay the complete validation chain unless the profile contract itself
requires a new full run after repair.
