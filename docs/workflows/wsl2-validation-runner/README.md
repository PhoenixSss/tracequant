# WSL2 Task Workflow Validation Runner

## Scope

Task #83 introduces a fixed validation runner for the WSL2 Codex workflow. It is
an execution and evidence compression layer, not an authorization layer.

The fixed entry is:

```bash
tools/agent_workflow/wsl2_validation_runner.py <profile>
```

Allowed profiles are versioned in
`tools/agent_workflow/wsl2_validation_profiles.json`:

| Profile | Purpose | CI-equivalent |
| --- | --- | --- |
| `current-ci-equivalent` | Current CI validation commands plus local `git diff --check` | Yes |
| `targeted` | Fixed tools test preset | No |
| `targeted:tools-tests` | Named tools test preset | No |
| `targeted:workflow-tests` | Named workflow runner test preset | No |
| `post-merge` | Post-merge validation after a synchronized clean `main` | Yes, after post-merge preconditions |

`targeted` profiles are never complete CI-equivalent evidence.

## Current CI Contract

The runner independently checks the current CI workflow and the canonical spec.
The CI validation commands are:

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tests
```

`current-ci-equivalent` and `post-merge` also run:

```bash
git diff --check
```

The whitespace check is a repository workflow validation requirement and is
recorded as an explicit local addition to the CI workflow commands.

## Security Model

The runner:

- accepts no shell string, command flag, path flag, arbitrary pytest argument, or
  trailing argument;
- uses fixed argv arrays from the canonical profile spec;
- starts only from the repository root;
- rejects `/mnt/<drive>` repository paths;
- rejects symlink invocation and wrong Git roots;
- writes only under ignored `.agents/validation.local/wsl2-runs/`;
- records full logs only in controlled JSON files;
- prints only a compact JSON digest on success;
- propagates subcommand failures, timeouts, interruptions, and result write
  failures as non-zero exits;
- records runner, profile spec, and rules SHA-256 at runtime;
- redacts common token, cookie, private-key, and secret patterns.

The runner does not modify business code, Git history, branches, GitHub state,
Project state, labels, Issues, Pull Requests, or credentials.

## Rules

Project rules live at:

```text
.codex/rules/quant-system-wsl-validation.rules
```

The rules only allow the fixed runner entry with approved profiles. They do not
allow direct `python`, `python3`, `uv`, `uv run`, `bash`, `sh`, GitHub writes,
`git push`, branch deletion, `git reset`, `git clean`, or raw GitHub API calls.
The current execpolicy language is prefix-based, so the rules also include
stricter forbidden prefixes for known injection flags and shell metacharacter
forms. The runner remains the final argument validator and rejects any trailing
argument before executing a subcommand.

Static checks can be run with:

```bash
codex execpolicy check --pretty --rules .codex/rules/quant-system-wsl-validation.rules -- tools/agent_workflow/wsl2_validation_runner.py current-ci-equivalent
codex execpolicy check --pretty --rules .codex/rules/quant-system-wsl-validation.rules -- tools/agent_workflow/wsl2_validation_runner.py targeted
codex execpolicy check --pretty --rules .codex/rules/quant-system-wsl-validation.rules -- tools/agent_workflow/wsl2_validation_runner.py post-merge
```

Rules activation in the live Codex session may require restarting Codex or
starting a new session. Static `execpolicy check` results are not live Guardian
measurements.

## Live Activation Checkpoint

After the rules file is installed in the project rules location, restart Codex or
start a new Codex session in the WSL2 workspace and run exactly:

```bash
tools/agent_workflow/wsl2_validation_runner.py targeted
```

Record these external fields outside the repository:

```text
profile
run_id
Guardian turns
approval prompts
elevated executions
duration
stdout byte count
result_path
result_sha256
notes
```

Do not broaden global rules to avoid this checkpoint.

## Task #83 Observed Live Activation

Task #83 includes one live activation observation from the current WSL2 Codex
environment after the new project rules were loaded in a new Codex session:

- profile: `targeted`
- direct execution: yes
- Guardian turns: `0`
- approval prompts: `0`
- elevated executions: `0`
- duration: `10284 ms`
- stdout: `346 bytes`
- raw result remains external/ignored

The committed summary evidence is
`docs/workflows/wsl2-validation-runner/live-activation-evidence.json`. The raw
runner result remains below ignored `.agents/validation.local/` and is not a
repository artifact.

This is an observation for Task #83 in the current environment, not a permanent
guarantee for every machine or every profile. The `current-ci-equivalent` and
`post-merge` live Guardian behavior were not measured by this targeted probe.
Static `execpolicy check` coverage remains separate from this real live
activation result.

## Rollback

To roll back Task #83 behavior before adoption by later Skills, remove or ignore
the project rules file and stop invoking `tools/agent_workflow/wsl2_validation_runner.py`.
The existing `workflow_validation.py` and Task Skills remain unchanged by this
Task.

## Known Follow-Up

GitHub Evidence helper compatibility, including current `baseRefOid` behavior
and fallback queries, is deferred to Task #84.
