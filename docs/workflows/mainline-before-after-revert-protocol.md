# Workflow Mainline Before/After + Revert Protocol v1

This document owns the reusable experiment protocol for Workflow optimization
candidates, including Issue #91. It defines how a future candidate is measured;
it does not authorize or execute an experiment, select a business Task, change
`main`, or optimize any candidate variable.

The canonical machine-facing field contract is implemented by
`tools/agent_workflow/mainline_experiment.py`. Raw rollout files and external
Token reports remain outside the repository.

## 1. Mainline lifecycle and roles

The formal sequence is fixed before execution:

```text
baseline main
→ freeze BEFORE record and measured boundary
→ run BEFORE
→ freeze BEFORE evidence
→ apply the separately approved candidate to main
→ freeze AFTER identity
→ run AFTER with the same measured boundary
→ freeze AFTER evidence
→ decide keep or revert
→ verify cleanup integrity
```

The candidate reaches `main` only through its normal approved Task, PR,
Independent Review, and maintainer manual Merge lifecycle. This protocol does
not grant Merge or revert authority. A revert is a normal, mechanically
identifiable Git revert through the repository's approved lifecycle; the
experiment conductor must never rewrite history or switch `main` merely to
simulate either arm.

Three roles are kept separate:

- **Measured session** performs only the lifecycle boundary declared before the
  run. Its Tokens, duration, Guardian, and tool metrics are the experiment data.
- **Conductor** freezes identities, starts sessions, inventories artifacts, and
  makes no measured implementation decisions. Its sessions are recorded under
  `conductor` and excluded from both arms' metrics.
- **Evidence collector** parses already-completed rollouts, hashes files, and
  constructs the evidence snapshot. Collection begins only after the measured
  end condition and its Tokens/duration are never added to a measured run.

## 2. Pre-run freeze

Before starting BEFORE, create a record and pass
`validate_record(record, checkpoint="pre_run")`. The record freezes:

- `protocol_id`, `candidate_id`, `baseline_main_sha`, candidate patch identity,
  fixed Task or fixed-patch identity, Task specification hash, base and expected
  head/patch identity;
- model and reasoning effort for root and Guardian, CLI/runtime version,
  approval policy, sandbox policy, and network mode;
- Agent invocation granularity and Guardian admission source;
- the measured start and end conditions and whether remediation, Independent
  Review, Merge, and Closeout are inside the boundary;
- the explicit rule `evidence_collection_excluded: true`.

The AFTER identity additionally freezes `candidate_main_sha` before AFTER
starts. If the candidate SHA is not yet known when BEFORE begins, the approved
candidate patch identity is frozen first and the resulting `candidate_main_sha`
is appended before AFTER without changing the measured boundary. Boundary
changes after observing results invalidate the comparison; they do not create
a new version of the same run.

## 3. Comparability and candidate-effect isolation

The record compares the arms across these formal dimensions:

| Dimension | Meaning |
| --- | --- |
| `workflow_change` | the candidate under test |
| `model_change` | root or Guardian model/effort change |
| `cli_runtime_change` | CLI, runtime, OS, or execution-host change |
| `sandbox_change` | sandbox or filesystem capability change |
| `approval_policy_change` | approval/Guardian routing policy change |
| `network_change` | network availability, proxy, or credential route change |
| `agent_invocation_granularity_change` | root/subagent/session decomposition change |
| `guardian_admission_change` | admission source or Guardian routing change |

`STRICT` requires `workflow_change=true` and every other dimension unchanged.
Any recorded non-candidate change makes the result `CONDITIONAL` and requires a
reason stating the affected interpretation. Missing or invalid dimensions, or
no workflow change, are `NOT_COMPARABLE`. A reduction in Guardian turns,
commands, or Tokens is never attributed to the candidate without considering
invocation granularity, Guardian admission, sandbox/approval/network, and
model/runtime differences.

External summaries must render an unavoidable confound explicitly as:

```text
COMPARABILITY = CONDITIONAL
```

They must not describe that comparison as a strict causal result.

This classification controls causal claims, not whether descriptive evidence
may be preserved. The protocol records these variables; it does not optimize
them.

## 4. Run and rollout evidence

Each BEFORE and AFTER run records exact root and Guardian session IDs. Every
rollout inventory entry contains:

```text
actor
session_id
parent_session_id
rollout_filename
byte_size
sha256
```

Guardian entries name their root parent; root entries have a null parent.
Measured metrics include Tokens, duration, root tool calls, Guardian turns and
Tokens, validation command segments, shell/tool invocation count, repeated
Git/GitHub acquisition, compound invocation count, command grouping, and manual
intervention. Admission source belongs to the frozen experiment identity.

The adapter `adapt_collected_run(...)` accepts only experiment identity,
rollout inventory, and measured metrics. It intentionally has no conductor
argument. Conductor session IDs, rollout filenames, sizes, hashes, and parent
relationships are stored separately under `conductor`, making accidental metric
inclusion detectable.

## 5. Historical-answer contamination

Reusing a Task or fixed patch requires an access audit when prior artifacts may
exist. The audit begins with known metadata and does not open a historical
implementation, review answer, or rollout merely to prove that it exists.
Every access is classified as exactly one of:

- `metadata_only_exposure`: identity, size, hash, timestamps, relationship, or
  other facts that do not reveal the answer;
- `answer_bearing_implementation_access`: source patch, implementation answer,
  findings, generated solution, or other content that can teach the measured
  session the answer.

Unavoidable answer-bearing exposure is reported as contamination/confounding;
it is never relabelled metadata-only. The formal record must state
`proactive_answer_access_prohibited: true`.

## 6. Evidence freeze and decision

After each measured session ends, the collector freezes the following without
charging collection to the measured run:

- Git, Issue, PR, implementation/fixed-patch identities;
- validation and Independent Review result within the declared boundary;
- complete rollout inventory, Token, duration, Guardian/tool, runtime, command,
  and manual-intervention evidence;
- integrity hashes and cleanup preconditions.

At the final `evidence_frozen` checkpoint, the record must include a complete
candidate main SHA, both run inventories and metrics, freeze timestamp,
comparability classification, contamination audit, decision, and mechanical
revert plan.

The candidate may be `keep` only when correctness and the Quality Gate pass,
the measured hypothesis is supported, and there is no unacceptable operational
regression. It must be `revert` when any of those conditions fails. Ambiguous or
unavailable evidence does not support `keep`; it triggers the applicable Human
Gate or a separately authorized follow-up.

## 7. Revert and cleanup integrity

The revert plan is frozen even when the candidate is kept. It identifies the
candidate commit, the approved mechanical method (normally a Git revert), and
the expected restored tree or intended current baseline. After an actual
revert, independently verify:

- target tree equals the intended current baseline, accounting only for
  explicitly approved intervening mainline changes;
- exact Issue, PR, and Task branch lifecycle cleanup;
- no stale Development linkage;
- no mutation or deletion of historical experiment evidence.

The revert is not performed by this protocol Task. A future experiment must
use the normal Issue/PR/Review/manual-Merge workflow for both candidate and
revert and must not force-push, reset, or erase evidence.

## 8. Explicit exclusions

Protocol validation verifies the schema, documentation, adapter separation,
and tests. It does not require a live Before/After/Revert run. It does not
restore or invoke the historical A/B/C/D benchmark architecture, run any
existing benchmark, choose a business Task, alter Runner coverage, change the
Independent Review strategy, introduce a Context Compiler, batch Agent
commands, or change sandbox/Full Access policy.

The example at
`docs/workflows/templates/mainline-experiment-record.example.json` is a
pre-run template for Issue #91 or another approved Workflow candidate. Values
must be replaced with exact identities before use.
