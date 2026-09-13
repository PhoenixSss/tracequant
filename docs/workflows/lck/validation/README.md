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
uv run --frozen python -m tools.lck.wsl2_validation_runner current-ci-equivalent
uv run --frozen python -m tools.lck.wsl2_validation_runner targeted
uv run --frozen python -m tools.lck.wsl2_validation_runner targeted:tools-tests
uv run --frozen python -m tools.lck.wsl2_validation_runner targeted:workflow-tests
uv run --frozen python -m tools.lck.wsl2_validation_runner post-merge
uv run --frozen python -m tools.lck.wsl2_validation_runner workflow-delivery --base-sha <SHA>
uv run --frozen python -m tools.lck.wsl2_validation_runner workflow-review --base-sha <SHA>
uv run --frozen python -m tools.lck.wsl2_validation_runner workflow-closeout --base-sha <SHA>
```

The fixed profiles are defined in
`tools/lck/config/validation_profiles.json`. Workflow profiles invoke
`validation_runner.py` once with the corresponding phase contract.

`targeted` profiles are development evidence only. They are not complete
CI-equivalent validation.

## Current CI contract

The canonical CI command set is:

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tools/lck tests
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

The Rules file authorizes fixed Runner profile prefixes and the current CI's
explicit `uv run --frozen ruff format --check .` shape. Other direct validation
commands remain fail-closed where prefix matching cannot prove their complete
argv safe. Prefix routing is not semantic acceptance: the Runner validates the
complete argv.

## Result contract

Success stdout is one compact JSON object containing profile, status, command
counts, duration, result path, result SHA-256, and no failed command. Detailed
per-command argv, exit code, duration, output, truncation, and hashes remain in
the ignored result directory.

On failure, inspect only the reported failed command and its bounded artifact.
Do not replay the complete validation chain unless the profile contract itself
requires a new full run after repair.
