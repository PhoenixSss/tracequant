# Mainline Before/After + Revert Experiment Record

Copy this Markdown template for one maintainer-operated experiment. Replace
every placeholder with an exact value or record `UNKNOWN` with a reason. Do not
change the measured boundary after BEFORE starts; create a new record if the
boundary changes.

Protocol: [mainline-before-after-revert-protocol.md](../mainline-before-after-revert-protocol.md)

## Experiment identity

| Field | Value |
| --- | --- |
| `protocol_id` | `FILL_ME` |
| `candidate_id` | `FILL_ME` |
| `baseline_main_sha` | `FILL_ME` |
| `candidate_main_sha` | `FILL_BEFORE_AFTER` |
| `task_or_fixed_patch_identity` | `FILL_ME` |
| `task_spec_hash` | `FILL_ME` |
| `base_sha` | `FILL_ME` |
| `expected_head_or_patch_identity` | `FILL_ME` |
| `model` | `FILL_ME` |
| `reasoning_effort` | `FILL_ME` |
| `guardian_model` | `FILL_ME` |
| `guardian_effort` | `FILL_ME` |
| `cli_version` | `FILL_ME` |
| `approval_policy` | `FILL_ME` |
| `sandbox_policy` | `FILL_ME` |
| `network_mode` | `FILL_ME` |
| `agent_invocation_granularity` | `FILL_ME` |
| `guardian_admission` | `FILL_ME` |

## Reviewed-object freeze — record separately for each arm

Freeze this tuple before BEFORE and before AFTER starts. The three fields below
must be recorded together with the arm's `base_sha` and
`expected_head_or_patch_identity`; together they bind the exact reviewed
object used by that arm. Use the existing `AC-<n>` Acceptance Criteria
identifier convention and the complete repository-relative changed-file
inventory (one exact path per entry, no globs).

| Field | BEFORE | AFTER |
| --- | --- | --- |
| `effective_diff_sha256` | `FILL_ME` | `FILL_ME` |
| `changed_files_manifest` | See below | See below |
| `acceptance_criteria_ids` | `FILL_ME` | `FILL_ME` |

### `changed_files_manifest`

Record the complete deterministic path list for each arm's effective diff.

BEFORE:

```text
FILL_ME — one repository-relative path per line
```

AFTER:

```text
FILL_ME — one repository-relative path per line
```

For arms intended to review the same object, all three values and the arm's
base/head identity must match. If any value is missing or differs, stop the
comparison and record `COMPARABILITY = NOT_COMPARABLE`; never replace it with a
new head, diff, manifest, or Acceptance Criteria set after the arm starts.

## Measured boundary — freeze before BEFORE

- Boundary owner: `FILL_ME`
- Frozen at (UTC): `FILL_ME`
- Baseline identity checked: [ ]
- Start condition: `FILL_ME`
- End condition: `FILL_ME`
- Remediation included: `YES / NO`
- Independent Review included: `YES / NO`
- Merge included: `YES / NO`
- Closeout included: `YES / NO`
- Conductor/evidence collection excluded from measured Token/duration: [ ]
- Boundary remained unchanged after results: [ ]

## Maintainer launch checkpoints

- [ ] Baseline and candidate selection frozen.
- [ ] BEFORE was manually started by the maintainer.
- [ ] BEFORE ended at the frozen end condition.
- [ ] BEFORE evidence was manually frozen.
- [ ] Candidate Merge checkpoint was approved through the normal workflow.
- [ ] Candidate `main` SHA was frozen before AFTER.
- [ ] AFTER was manually started by the maintainer with the same boundary.
- [ ] AFTER ended at the frozen end condition.
- [ ] AFTER evidence was manually frozen.
- [ ] Conductor/evidence collection was separately started by the maintainer.

## BEFORE run

| Field | Value |
| --- | --- |
| Started at (UTC) | `FILL_ME` |
| Ended at (UTC) | `FILL_ME` |
| Start/end condition confirmed | `FILL_ME` |
| Base SHA | `FILL_ME` |
| Expected head/patch identity | `FILL_ME` |
| Root session IDs | `FILL_ME` |
| Guardian session IDs | `FILL_ME` |

### BEFORE rollout inventory

| Actor | Session ID | Parent session ID | Exact rollout filename | Bytes | SHA-256 |
| --- | --- | --- | --- | ---: | --- |
| root | `FILL_ME` | `none` | `FILL_ME` | `FILL_ME` | `FILL_ME` |
| guardian | `FILL_ME` | `FILL_ROOT_SESSION` | `FILL_ME` | `FILL_ME` | `FILL_ME` |

### BEFORE metrics and evidence

| Field | Value / evidence identity |
| --- | --- |
| Tokens | `FILL_ME` |
| Duration | `FILL_ME` |
| Root tool calls | `FILL_ME` |
| Guardian turns | `FILL_ME` |
| Guardian Tokens | `FILL_ME` |
| Validation command segments | `FILL_ME` |
| Shell/tool invocation count | `FILL_ME` |
| Repeated Git/GitHub acquisition | `FILL_ME` |
| Compound invocation / command grouping | `FILL_ME` |
| Manual intervention | `FILL_ME` |
| Git / PR / Issue identity | `FILL_ME` |
| Implementation/fixed-patch identity | `FILL_ME` |
| Validation result | `FILL_ME` |
| Review result, if in boundary | `FILL_ME` |
| Runtime identity | `FILL_ME` |
| Integrity evidence | `FILL_ME` |

## AFTER run

| Field | Value |
| --- | --- |
| Started at (UTC) | `FILL_ME` |
| Ended at (UTC) | `FILL_ME` |
| Start/end condition confirmed | `FILL_ME` |
| Base SHA | `FILL_ME` |
| Expected head/patch identity | `FILL_ME` |
| Root session IDs | `FILL_ME` |
| Guardian session IDs | `FILL_ME` |

### AFTER rollout inventory

| Actor | Session ID | Parent session ID | Exact rollout filename | Bytes | SHA-256 |
| --- | --- | --- | --- | ---: | --- |
| root | `FILL_ME` | `none` | `FILL_ME` | `FILL_ME` | `FILL_ME` |
| guardian | `FILL_ME` | `FILL_ROOT_SESSION` | `FILL_ME` | `FILL_ME` | `FILL_ME` |

### AFTER metrics and evidence

| Field | Value / evidence identity |
| --- | --- |
| Tokens | `FILL_ME` |
| Duration | `FILL_ME` |
| Root tool calls | `FILL_ME` |
| Guardian turns | `FILL_ME` |
| Guardian Tokens | `FILL_ME` |
| Validation command segments | `FILL_ME` |
| Shell/tool invocation count | `FILL_ME` |
| Repeated Git/GitHub acquisition | `FILL_ME` |
| Compound invocation / command grouping | `FILL_ME` |
| Manual intervention | `FILL_ME` |
| Git / PR / Issue identity | `FILL_ME` |
| Implementation/fixed-patch identity | `FILL_ME` |
| Validation result | `FILL_ME` |
| Review result, if in boundary | `FILL_ME` |
| Runtime identity | `FILL_ME` |
| Integrity evidence | `FILL_ME` |

## Comparability judgment

| Dimension | Changed? | Evidence / explanation |
| --- | --- | --- |
| `workflow_change` | `YES / NO / UNKNOWN` | `FILL_ME` |
| `model_change` | `YES / NO / UNKNOWN` | `FILL_ME` |
| `cli_runtime_change` | `YES / NO / UNKNOWN` | `FILL_ME` |
| `sandbox_change` | `YES / NO / UNKNOWN` | `FILL_ME` |
| `approval_policy_change` | `YES / NO / UNKNOWN` | `FILL_ME` |
| `network_change` | `YES / NO / UNKNOWN` | `FILL_ME` |
| `agent_invocation_granularity_change` | `YES / NO / UNKNOWN` | `FILL_ME` |
| `guardian_admission_change` | `YES / NO / UNKNOWN` | `FILL_ME` |

- Classification: `STRICT / CONDITIONAL / NOT_COMPARABLE`
- If conditional, conclusion must include: `COMPARABILITY = CONDITIONAL`
- Causal interpretation and limitations: `FILL_ME`

## Historical-answer contamination audit

- Audit required because prior artifacts may exist: `YES / NO`, reason: `FILL_ME`
- Proactive answer access prohibited: [ ]

| Artifact/access | Classification (`metadata-only exposure` or `answer-bearing implementation access`) | Evidence / consequence |
| --- | --- | --- |
| `FILL_ME` | `FILL_ME` | `FILL_ME` |

Do not open historical implementation or answer merely to prove non-access.

## Final evidence freeze and decision

- Evidence collector/conductor was outside measured Token/duration: [ ]
- BEFORE and AFTER evidence inventories are complete or limitations are
  documented: [ ]
- Evidence frozen at (UTC): `FILL_ME`
- Evidence frozen by maintainer: `FILL_ME`

| Decision input | Result | Evidence |
| --- | --- | --- |
| Correctness | `PASS / FAIL / UNKNOWN` | `FILL_ME` |
| Quality Gate | `PASS / DEGRADED / UNKNOWN` | `FILL_ME` |
| Measured hypothesis | `SUPPORTED / NOT_SUPPORTED / UNKNOWN` | `FILL_ME` |
| Operational regression | `ACCEPTABLE / UNACCEPTABLE / UNKNOWN` | `FILL_ME` |

- Decision: `KEEP / REVERT / HUMAN GATE`
- Maintainer rationale: `FILL_ME`

## Revert plan and post-Revert verification

| Field | Value |
| --- | --- |
| Candidate identity | `FILL_ME` |
| Target Revert commit | `FILL_ME` |
| Approved method and authorization | `FILL_ME` |
| Expected restored tree/current baseline | `FILL_ME` |

Before Revert:

- [ ] Candidate identity checked.
- [ ] Target commit checked.
- [ ] Expected restored tree/current baseline recorded.
- [ ] Issue / PR / branch / Development linkage cleanup expectations recorded.
- [ ] Historical evidence is preserved read-only.

After Revert, if performed:

- [ ] Restored tree matches the intended baseline, accounting for approved
  intervening mainline changes.
- [ ] Issue / PR / branch lifecycle cleanup is correct.
- [ ] No stale Development linkage remains.
- [ ] Historical evidence was not mutated or deleted.
- Verification commands and evidence identities: `FILL_ME`
