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

Task #83 uses a layered control boundary:

| Layer | Responsibility |
| --- | --- |
| Execpolicy Rules | Authorize only the fixed runner entry and named profile prefixes. |
| Validation Runner | Validate complete argv, argument count, argument values, profile semantics, configuration integrity, and canonical command semantics before executing validation subcommands. |

`policy allow != command semantic acceptance`. Because the current execpolicy
rules use prefix matching, a fixed profile followed by an arbitrary trailing
value can still receive an `allow` policy decision. The runner must, and does,
reject that full argv before creating success evidence or executing validation
commands.

The runner:

- accepts no shell string, command flag, path flag, arbitrary pytest argument, or
  trailing argument;
- uses fixed argv arrays from the canonical profile spec and verifies each command ID/argv against an in-runner canonical allowlist;
- starts only from the repository root;
- rejects `/mnt/<drive>` repository paths;
- rejects symlink invocation and wrong Git roots;
- writes only under ignored `.agents/validation.local/wsl2-runs/`;
- records full logs only in controlled JSON files;
- prints only a compact JSON digest on success;
- propagates subcommand failures, timeouts, interruptions, and result write
  failures as non-zero exits;
- starts every validation command in a dedicated POSIX session and terminates the complete process group on timeout or interruption, preventing `uv` descendants from surviving the Runner;
- verifies the Runner, profile spec, and Rules against their tracked `HEAD` blobs before any validation subcommand executes, rejecting staged, unstaged, symlinked, untracked, or replaced trusted files;
- records the verified Runner, profile spec, and Rules SHA-256 in the result;
- redacts common token, cookie, private-key, and secret patterns.

The runner does not modify business code, Git history, branches, GitHub state,
Project state, labels, Issues, Pull Requests, or credentials.

## Trusted-file and process-tree boundary

Before creating a run directory or executing a validation command, the Runner verifies these files against their exact tracked `HEAD` blobs:

```text
tools/agent_workflow/wsl2_validation_runner.py
tools/agent_workflow/wsl2_validation_profiles.json
.codex/rules/quant-system-wsl-validation.rules
```

Any staged or unstaged change fails closed. The profile spec is then loaded from the already verified bytes, and every command ID and argv must match the Runner's canonical command allowlist. This prevents a writable profile JSON from turning an allowed Runner invocation into an arbitrary command launcher. Intentional Runner, profile, or Rules changes must be committed and reviewed before the fixed entry can run again.

Each child command uses a new POSIX session. On timeout the Runner signals the complete process group, waits for a bounded grace period, and escalates to `SIGKILL` if required. The same bounded process-group cleanup applies to interruption.

## Rules

Project rules live at:

```text
.codex/rules/quant-system-wsl-validation.rules
```

The rules only allow the fixed runner entry with approved profiles. They do not
allow direct `python`, `python3`, `uv`, `uv run`, `bash`, `sh`, GitHub writes,
`git push`, branch deletion, `git reset`, `git clean`, or raw GitHub API calls.
The current execpolicy language is prefix-based. Rules do not provide complete
argv exact-match or end-of-command matching. They authorize the entry/profile
prefix, while the runner validates complete argv semantics. The rules also
include stricter forbidden prefixes for known injection flags and shell
metacharacter forms as defense-in-depth; those forbidden examples are not a
claim that Rules enumerate every arbitrary trailing value.

Static checks can be run with:

```bash
codex execpolicy check --pretty --rules .codex/rules/quant-system-wsl-validation.rules -- tools/agent_workflow/wsl2_validation_runner.py current-ci-equivalent
codex execpolicy check --pretty --rules .codex/rules/quant-system-wsl-validation.rules -- tools/agent_workflow/wsl2_validation_runner.py targeted
codex execpolicy check --pretty --rules .codex/rules/quant-system-wsl-validation.rules -- tools/agent_workflow/wsl2_validation_runner.py targeted tests/tools
codex execpolicy check --pretty --rules .codex/rules/quant-system-wsl-validation.rules -- tools/agent_workflow/wsl2_validation_runner.py post-merge
```

The `targeted tests/tools` static check is expected to return `allow` because
of prefix matching. The paired runner invocation is expected to return non-zero
before any validation subcommand starts.

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

## Publication and maintenance materials

- `security-hardening-cases.md`: controlled pre-review failure cases, root
  causes, fixes, regression evidence, and residual boundaries.
- `maintenance-and-adoption.md`: synchronized maintenance points, adoption
  guidance, costs, reusable principles, and rollback.
- `current-ci-equivalent-evidence.json`: run-level evidence summary with
  explicit unavailable raw-result and command-level fields.
- `live-activation-evidence.json`: targeted Rules activation observation and
  the runner 1.0.0 versus 1.0.1 applicability boundary.
- `publication-materials.json`: claim, metric, limitation, and final-document
  mapping index.
- `visuals/`: editable CSV and Mermaid sources for the final guide and article.

## Rollback

Task #85 adopts this runner in the Task lifecycle Skills. Rollback must revert the
Skill routing, runner/profile/Rules version, validators, and documentation as one
compatible change; do not append the old direct validation chain beneath the fixed
runner call.

## Skill adoption

Task #85 adds `workflow-delivery`, `workflow-review`, and `workflow-closeout`
profiles and switches the Task Skills to these fixed paths. Runtime Token and
Guardian comparison remains deferred to Task #86.
