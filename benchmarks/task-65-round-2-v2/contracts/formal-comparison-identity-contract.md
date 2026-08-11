# Formal Comparison & Identity Contract — task-65-round-2-v2

Authoritative for the task-65-round-2-v2 preparation tooling. This contract
defines which identities must be identical across Arms for a formal C vs D
comparison, which identities are per-Arm traceable, the diagnostic role of
control-plane file identity, and the rerun policy.

## Canonical principle

Formal cross-Arm comparison requires:

```text
SAME BUSINESS
SAME TASK
SAME EVALUATION
```

It does **not** require `SAME REPOSITORY` or `SAME CONTROL-PLANE FILE TREE`.

## Mandatory shared identities

The only identities whose cross-Arm equality is mandatory for formal
comparison:

| identity | meaning | carried by (run-locked manifest) |
|---|---|---|
| `BUSINESS_SNAPSHOT_ID` | the frozen business snapshot all Arms work on | `benchmark_base_sha` (= `BENCHMARK_BASE_SHA`) |
| `TASK_SPEC_ID` | the frozen Task #65 workload specification | `protocol_identity` (`task-65-round-2-v2`) |
| `EVALUATION_ID` | the formal C vs D evaluation definition | optional `evaluation_id`, assigned at freeze via run-lock `--evaluation-id` |

Any mismatch on any of the three → **comparison invalid / HUMAN GATE**.

`EVALUATION_ID` is only carried (and thus gated) when the freeze operator
assigns it. A formal freeze must assign it; when both compared manifests
carry no `evaluation_id`, the identity is recorded as not carried and is not
gated.

## Per-Arm identities

Each Arm's own experimental identity is **traceable but never required to be
equal across Arms**:

| identity | carried by |
|---|---|
| `WORKFLOW_ID` | `invocation_contract` |
| `RUNNER_ID` | `invocation_contract` |
| `AGENT_RUNTIME_ID` | `agent_identity` |
| `ENVIRONMENT_ID` | `permission_discovery_identity` / #86 environment manifest |

C (current workflow + Codex adapter/runtime) and D (current workflow +
Claude adapter/runtime) may therefore carry different peripheral files and
per-Arm runtime identities, as long as the shared Task / business /
evaluation contract is unchanged.

## File identity report disposition

`tooling/file_identity_report.py` remains in the toolchain and reports:

- path differences (C-only / D-only);
- blob differences;
- sha256 differences;
- file-mode differences;
- role / generation-closure differences (allowed identity fields, per-Arm
  identity digest, unexpected field differences);
- per-agent-pruned-closure indicator.

These results are **DIAGNOSTIC / PROVENANCE**. A control-plane file identity
difference alone must never:

- invalidate an Arm;
- set a mandatory Human Gate;
- prohibit formal C vs D comparison.

The report's `human_gate` field fires **only** when a mandatory shared
identity (`BUSINESS_SNAPSHOT_ID` / `TASK_SPEC_ID` / `EVALUATION_ID`)
mismatches. `disposition` is one of:

- `pass` — shared identities identical and control-plane file identity
  consistent;
- `DIAGNOSTIC — control-plane file identity differences recorded; formal
  comparison permitted if experiment definition permits` — shared
  identities identical, control-plane file identity differs;
- `HUMAN GATE — mandatory shared identity mismatch; formal C vs D comparison
  invalid` — a mandatory shared identity differs.

CLI exit code: `0` when `human_gate` is false, `2` when `human_gate` is true.

## Formal comparison validity

Formal C vs D comparison is decided by the shared identities:

```text
BUSINESS_SNAPSHOT_ID identical
TASK_SPEC_ID identical
EVALUATION_ID identical
```

Any mismatch → comparison invalid / Human Gate.

Peripheral workflow / control-plane identity mismatch → record the
differences and continue if the experiment definition permits.

## Rerun policy

- Arm-specific workflow / infrastructure fix → rerun **only the affected
  Arm**.
- A shared component actually used by multiple Arms changed → rerun the
  **affected Arms only**.
- `BUSINESS_SNAPSHOT_ID` changed → rerun **all affected Arms**.
- `TASK_SPEC_ID` changed → rerun **all affected Arms**.
- `EVALUATION_ID` changed → rerun **all affected Arms**.

A repository HEAD change alone must never automatically invalidate prior
valid Arm runs.
