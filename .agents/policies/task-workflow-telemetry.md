# Task workflow telemetry policy

## Purpose

This policy defines optional, local-only measurement of Task workflow token and
process cost. It applies to:

```text
.agents/skills/task-delivery/SKILL.md
.agents/skills/task-pr-review/SKILL.md
.agents/skills/task-closeout/SKILL.md
.agents/skills/feature-completion-audit/SKILL.md
```

Telemetry is side-channel observation only:

```text
observation
!= workflow authorization
!= correctness evidence
!= merge authorization
!= Feature completion evidence
```

The active workflow Skill remains the only source for lifecycle permissions,
gates, validation, findings, and verdicts. Telemetry never authorizes a command,
write, review, merge, Issue close, Project mutation, branch cleanup, or Feature
closeout.

## Activation and local storage

Telemetry is disabled unless the maintainer explicitly starts a run for the
specified Task with `tools/agent_workflow/telemetry.py start`.

Repository example configuration:

```text
.agents/task-workflow-telemetry.example.toml
```

Optional local configuration:

```text
.agents/task-workflow-telemetry.local.toml
```

Local data directory:

```text
.agents/telemetry.local/
```

The local configuration and data directory must be ignored by Git and must not
enter tracked, staged, committed, pushed, or Pull Request scope. If either path
is tracked, staged, or not ignored, do not write telemetry and report a local
safety configuration error. Preserve the normal workflow's own decision about
whether it can continue.

Telemetry data must not contain credentials, authentication material, complete
prompts, complete assistant responses, private reasoning, source or test
contents, complete command output, complete environment variables, usernames,
or sensitive absolute paths.

## Modes

Supported modes are:

- `baseline-only`: record a complete local measurement and summary without
  automatically comparing or optimizing the workflow;
- `spot-check`: record the same data and optionally compare it with completed
  local runs in the same baseline family.

Telemetry does not implement token optimization. A later optimization requires a
separately approved Task based on sanitized aggregate results.

## Baseline family

Every run records at least:

```text
task_kind
size
risk_class
workflow_shape
```

Strict default comparison requires all four values to match. Do not combine all
Task types into one global average. Fewer than three comparable completed runs
permit structural comparison only, not a statistical anomaly conclusion.

## Measurement integrity

Do not perform extra repository reads, GitHub queries, validations, command
retries, or report expansion only to populate telemetry. Use counts and facts
already produced by the normal workflow.

A telemetry failure is recorded as `telemetry incomplete`. It does not turn a
successful workflow gate into a failure, and it does not turn a failed gate into
success.

The following phases are supported:

```text
task-specification
task-delivery
task-pr-review
manual-merge
task-closeout
feature-completion-audit
```

Each Agent session writes at most one primary `phase-summary` for its phase.
Interruption, rework, review-run, and usage-patch events may be appended
separately. Events are append-only.

## Token usage

Supported usage sources are exactly:

```text
runtime-exact
client-export
estimated-external
unavailable
```

Unknown values are `null`, never fabricated as zero. Skills must not claim that
a character count is an exact token count. `reasoning_tokens` may contain only a
runtime-exposed aggregate count; telemetry never records private reasoning.
Usage may be patched after a session ends. Existing exact usage must not be
replaced by an estimate.

Exact and estimated usage are reported separately. Cross-model or cross-workflow
comparisons must retain model identity, workflow `main` SHA, and telemetry schema
version.

## Recorded aggregates

A phase may record aggregate counts for:

- files, lines, bytes, and estimated repeated bytes by context category;
- tool calls, GitHub queries, Git commands, validation commands, sandbox and
  elevated attempts, retries, and controlled retry categories;
- report characters, lines, estimated tokens, and whether the report was copied
  into the next phase;
- commits added after first handoff, head-SHA changes, independent review runs,
  review invalidations, findings by severity, maintainer decisions, and
  interruptions;
- phase and workflow outcome, validation state, telemetry completeness, and
  limitations.

Record safe command categories, not complete sensitive command lines.

## Run identity

`start` requires the Task number, canonical title, baseline classification,
repository slug, and current workflow `main` SHA. The manifest also records a
unique run ID, mode, timestamps, status, optional model, and optional associated
Feature number.

After PR creation, a phase event may supplement `pr_number`, `base_sha`, and
`head_sha` in its `identity` object. Later head-SHA values preserve event history
and the deterministic summary reports the latest value. Task title, Feature
number, and workflow SHA must not conflict with the manifest.

## Phase event schema

A `record` data file is a JSON object with `schema_version: 1`. It may contain
only the fields shown below. Omitted measurements remain unknown; token fields
use `null`, not fabricated zeroes.

```json
{
  "schema_version": 1,
  "event_type": "phase-summary",
  "recorded_at": null,
  "identity": {
    "task_canonical_title": null,
    "pr_number": null,
    "feature_number": null,
    "base_sha": null,
    "head_sha": null,
    "workflow_main_sha": null,
    "model": null,
    "changed_files_count": null,
    "changed_lines": null,
    "acceptance_criteria_count": null
  },
  "usage": {
    "source": "unavailable",
    "input_tokens": null,
    "cached_input_tokens": null,
    "output_tokens": null,
    "reasoning_tokens": null,
    "total_tokens": null,
    "model": null
  },
  "context": {},
  "operations": {
    "tool_calls": null,
    "github_queries": null,
    "git_commands": null,
    "validation_commands": null,
    "sandbox_attempts": null,
    "elevated_attempts": null,
    "retries": null,
    "retry_categories": {},
    "command_categories": {}
  },
  "report": {
    "report_characters": null,
    "report_lines": null,
    "report_estimated_tokens": null,
    "report_estimation_method": null,
    "copied_to_next_phase": null
  },
  "rework": {
    "commits_added_after_first_handoff": null,
    "head_sha_changes": null,
    "independent_review_runs": null,
    "review_invalidations": null,
    "maintainer_decisions": null,
    "interruptions": null,
    "findings_by_severity": {}
  },
  "outcome": {
    "phase_result": null,
    "workflow_result": null,
    "review_verdict": null,
    "feature_audit_verdict": null,
    "validation_passed": null,
    "telemetry_complete": null
  },
  "limitations": []
}
```

`event_type` accepted by `record` is one of:

```text
phase-summary
interruption
rework
review-run
manual-merge
```

The CLI creates `usage-patch` events itself. A phase may have only one primary
`phase-summary`; other event types preserve interruptions and rework without
replacing history.

Context is keyed by these categories:

```text
task_and_comments
governance
skills_and_policies
templates_and_workflows
source
tests
documentation
pr_diff_and_commits
github_facts
validation_output
previous_handoff
other
```

Each present category may contain only:

```text
files_read
bytes_read
lines_read
repeated_bytes_estimate
```

Retry categories are:

```text
sandbox-permission
credential-session
filesystem-isolation
real-validation-failure
remote-failure
other
```

Safe command categories are:

```text
git-read
git-write-authorized
github-read
github-write-authorized
validator
test
lint
format
type-check
telemetry
other
```

Finding keys are exactly `blocking`, `high`, `medium`, `low`, and `nit`.
Counts are non-negative integers. A missing optional count is unknown; an
explicit empty findings or category object means no events in that category.

`patch-usage` accepts either the `usage` object alone or:

```json
{
  "schema_version": 1,
  "usage": {
    "source": "client-export",
    "input_tokens": 100,
    "cached_input_tokens": 20,
    "output_tokens": 25,
    "reasoning_tokens": null,
    "total_tokens": 125,
    "model": "model-identifier"
  }
}
```

Do not place raw text, file paths, prompts, command lines, or output inside
`limitations` or other free-text fields. Keep free text short, aggregate, and
non-sensitive.

## Workflow integration

At the beginning of a workflow session, perform one lightweight local telemetry
status check. If there is no active run, do nothing else for telemetry.

When a run is active:

- maintain aggregate counters from facts already generated by normal work;
- append one phase summary at completion or interruption;
- do not add GitHub queries or validation commands;
- do not copy raw output or file contents;
- do not treat another phase's telemetry as correctness evidence;
- report a failed telemetry append as incomplete and continue according to the
  workflow's existing gates.

`task-pr-review` remains independently read-only. It does not trust delivery
telemetry, and it may append only to the exact ignored telemetry directory.
`feature-completion-audit` likewise does not trust Task telemetry as completion
evidence and may append only to that exact ignored directory.

## Command execution

Telemetry CLI commands and exact ignored local telemetry writes are subject to
`.agents/policies/command-execution.md`.

Command routing changes execution context only. It does not authorize tracked
file changes, GitHub mutation, Merge, Issue close, Project writes, branch
cleanup, or any other workflow action. The CLI does not access the network, run
GitHub mutations, or launch Git, tests, validators, or other workflow commands.

## Local CLI contract

`tools/agent_workflow/telemetry.py` provides:

```text
start
status
record
patch-usage
finish
validate
summarize
```

The CLI validates schema version, configuration, ignored storage, event order,
usage consistency, and sensitive-field prohibitions. It uses append-only event
records and deterministic summaries. It does not silently migrate an
unsupported schema or overwrite history.

For `task-only` and `task-with-re-review`, finish the run after `task-closeout`.
For `task-plus-feature-audit`, record closeout but keep the same run active until
the associated Feature audit records its phase; then finish by Task or by the
associated Feature selector. Do not create a second run for the Feature audit.

## Spot-check interpretation

Spot-check anomaly flags are informational and cannot change a Task, Pull
Request, or Feature verdict. Supported flags include:

```text
total-token-high
phase-token-high
repeated-context-high
tool-output-high
review-restart-high
retry-high
report-size-high
```

A summary must retain quality indicators such as validation, findings, review
invalidations, maintainer decisions, and workflow result. Lower token usage
alone is not evidence of a successful optimization.

## Prohibited data and behavior

Never use telemetry to:

- store or expose credentials, private reasoning, complete conversations, source
  contents, or complete command output;
- upload data or call an external service;
- modify a workflow verdict or permission;
- create a Token Optimization Task automatically;
- modify Skills, policies, prompts, or local profiles automatically;
- run extra GitHub, Git, validation, or evidence-collection commands;
- bypass independent review, new-SHA re-review, manual Merge, or manual Feature
  closeout;
- submit raw telemetry to Git.
