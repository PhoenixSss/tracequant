# Mainline Before/After + Revert Protocol

This document is the authoritative, maintainer-operated protocol for measuring
Workflow optimization candidates on the real `main` branch. It defines the
manual operating procedure and the evidence that a maintainer or an
independently started conductor should record. It does not authorize or
execute an experiment, change `main`, select a candidate, or decide an outcome.

The maintainer is the experiment orchestrator. The repository provides this
protocol and the manual record template only; it does not provide an
experiment runner, coordinator, watcher, validator, runtime state, or other
experiment-specific automation.

The record template is
[`templates/mainline-experiment-record.template.md`](templates/mainline-experiment-record.template.md).
Copy it outside the measured run, fill it manually, and freeze each section
at the checkpoint described below.

## 1. Roles and authority

### Maintainer — experiment orchestrator

The maintainer is responsible for all experiment orchestration and final
decisions:

- choose the baseline and candidate;
- freeze the baseline and the measured boundary before BEFORE starts;
- manually start BEFORE and AFTER runs;
- separately start the read-only conductor/evidence collection;
- approve the candidate Merge checkpoint through the normal workflow;
- manually freeze BEFORE and AFTER evidence;
- judge comparability from the frozen evidence;
- decide `KEEP` or `REVERT`;
- perform or explicitly authorize a Revert.

No repository component added for this protocol may take any of those actions
automatically.

### Measured Agent

Codex or Claude executes only the Workflow being measured. The Measured Agent
does not:

- start another experimental arm;
- decide when to collect experiment statistics;
- compare BEFORE and AFTER;
- select a candidate or decide `KEEP` / `REVERT`;
- Merge a candidate or Revert `main` automatically.

### Conductor / evidence collection

The maintainer starts the conductor or evidence collection independently after
the relevant measured run has completed. It reads completed rollout, Git,
GitHub, and evidence data and produces a read-only manual record. It:

- is outside the measured run;
- is excluded from measured Token and duration;
- does not modify the implementation under test;
- is not a resident sidecar monitor;
- is not automatically started by a repository runtime service.

The conductor may mechanically collect facts, but collection remains a
maintainer-operated action and is not a new repository runtime capability.

## 2. Mainline lifecycle

The maintainer follows this sequence on real `main`:

```text
maintainer freezes baseline main and the measured boundary
→ maintainer manually starts BEFORE
→ maintainer freezes BEFORE evidence
→ candidate is implemented and merged to main through the normal workflow
→ maintainer freezes candidate main identity
→ maintainer manually starts AFTER with the same measured boundary
→ maintainer freezes AFTER evidence
→ independently started conductor/evidence collection is frozen outside the measured runs
→ maintainer compares the frozen evidence
→ maintainer decides KEEP or REVERT
→ if REVERT is chosen, maintainer performs or explicitly authorizes it
→ maintainer verifies revert and cleanup integrity
```

The candidate is not run in parallel on a Task branch for this protocol. A
candidate Merge is a normal workflow checkpoint; this protocol grants no Merge
authority. The protocol itself must not execute a BEFORE, AFTER, or Revert
experiment.

## 3. Pre-BEFORE boundary freeze

Before manually starting BEFORE, the maintainer creates a copy of the record
template and freezes the measured lifecycle boundary. At minimum, record:

- the start condition;
- the end condition;
- whether remediation is included;
- whether Independent Review is included;
- whether Merge is included;
- whether Closeout is included;
- that independently started conductor/evidence collection is excluded from
  measured Token and duration;
- the person, timestamp, and baseline identity that froze the boundary.

The boundary cannot be changed after observing results. If the boundary must
change, record a new experiment rather than relabelling the existing result.

The pre-BEFORE identity freeze must include:

```text
protocol_id
candidate_id
baseline_main_sha
candidate_main_sha (filled before AFTER)
task_or_fixed_patch_identity
task_spec_hash
base_sha
expected_head_or_patch_identity
model
reasoning_effort
guardian_model
guardian_effort
cli_version
approval_policy
sandbox_policy
network_mode
agent_invocation_granularity
guardian_admission
```

For a fixed patch, record the patch identity in both the candidate and the
expected-head fields as applicable. For a Task, record the Task/PR identity and
the exact specification hash. BEFORE must use the frozen baseline identity;
AFTER must use the frozen candidate `main` identity.

## 4. Manual BEFORE and AFTER records

The maintainer manually starts each arm and records the start and end
conditions from the frozen boundary. The two arms use the same workload and
boundary. Evidence collection starts only after the measured end condition and
is not charged to either arm.

For each arm, record:

- root session IDs and Guardian session IDs;
- exact rollout filenames, byte sizes, and SHA-256 hashes;
- each rollout's parent relationship (`root` has no parent; a Guardian entry
  names its root parent);
- Git / PR / Issue and implementation or fixed-patch identity;
- validation and Review results when they are inside the frozen boundary;
- Token and duration;
- root tool calls;
- Guardian turns and Guardian Tokens;
- validation command segments;
- shell/tool invocation count;
- repeated Git/GitHub acquisition;
- compound invocation / command grouping;
- manual intervention;
- runtime identity and integrity evidence.

The manual record must distinguish an unknown or unavailable value from zero.
Do not infer candidate benefit from fewer Guardian turns, commands, or Tokens
without checking the comparability dimensions below.

## 5. Runtime comparability

When comparing BEFORE and AFTER, explicitly record whether each dimension
changed:

| Dimension | What must be compared |
| --- | --- |
| `workflow_change` | the candidate implementation or fixed patch |
| `model_change` | root and Guardian model and effort |
| `cli_runtime_change` | CLI, runtime, operating system, and execution host |
| `sandbox_change` | sandbox and filesystem capability |
| `approval_policy_change` | approval and Guardian routing policy |
| `network_change` | network availability, proxy, credentials, and route |
| `agent_invocation_granularity_change` | root/subagent/session decomposition and command grouping |
| `guardian_admission_change` | Guardian admission source or routing |

Use the following judgment:

- `STRICT` only when the workflow changed and every other dimension is
  unchanged and evidenced;
- `CONDITIONAL` when a confound cannot be eliminated. Record the confound and
  write exactly `COMPARABILITY = CONDITIONAL` in the conclusion;
- `NOT_COMPARABLE` when the workflow change is absent or the required identity
  is missing or cannot be trusted.

`CONDITIONAL` is not strict causal proof. A descriptive difference may still be
retained, but it must not be presented as an effect caused solely by the
candidate.

## 6. Historical-answer contamination audit

When reusing the same Task or fixed patch, perform an audit if prior artifacts
may be available. The maintainer decides whether the audit is needed, and the
independently started conductor reads only the minimum metadata needed.

The audit must distinguish:

```text
metadata-only exposure
answer-bearing implementation access
```

Metadata-only exposure includes identity, size, hash, timestamp, or relationship
facts that do not reveal the implementation answer. Answer-bearing access
includes a source patch, implementation answer, review finding, generated
solution, or any content that can teach the measured Agent the answer.

Do not open a historical implementation or answer merely to prove that it was
not opened. Record any unavoidable answer-bearing access as contamination or
confounding; never relabel it as metadata-only.

## 7. Evidence freeze and decision

After each run, the maintainer manually starts or authorizes independent
read-only evidence collection and freezes a snapshot containing, as applicable:

- Git / PR / Issue identity;
- implementation or fixed-patch identity;
- validation and Review result;
- complete rollout inventory and hashes;
- Token, duration, Guardian, and tool inventory;
- runtime and comparability identity;
- manual interventions;
- integrity evidence and cleanup preconditions.

At the final comparison freeze, both arm records must be present, the candidate
`main` SHA must be complete, the contamination audit must be recorded, and the
comparability judgment must be explicit. Evidence collection is outside the
measured Token and duration and must never be merged into measured metrics.

`KEEP` is a maintainer decision and requires all of the following to be
supported by the frozen evidence:

- correctness passes;
- the Quality Gate does not degrade;
- the measured hypothesis is supported;
- there is no unacceptable operational regression.

If any of those conditions fails, or the evidence is ambiguous or unavailable,
the maintainer must choose `REVERT` or enter the applicable Human Gate; the
record must not describe an unsupported `KEEP` as a result.

## 8. Revert discipline and cleanup integrity

Freeze a Revert plan even when the candidate is kept. Before a Revert, the
maintainer checks:

- candidate identity and the target Revert commit;
- the intended restored tree or current baseline, including approved intervening
  mainline changes;
- the normal Git/GitHub workflow and explicit authorization;
- Issue, PR, branch, and Development linkage cleanup expectations;
- that historical evidence will remain read-only and intact.

The maintainer performs or explicitly authorizes the Revert. The protocol does
not implement an automatic Revert engine or a machine-enforced Revert
validator. Afterward, the maintainer uses ordinary Git/GitHub read-only checks
to confirm the restored tree, exact lifecycle cleanup, absence of stale
Development linkage, and no mutation or deletion of historical evidence.
Do not force-push, reset, or erase experiment evidence.

## 9. Scope boundary

This is a documentation Task. It does not add an experiment runner,
coordinator, sidecar monitor, experiment-record validator, machine-enforced
comparability gate, automatic rollout/session watcher, automatic Token
collector, automatic BEFORE/AFTER or evidence orchestration, candidate
selection, Merge/Revert automation, or #90-specific runtime state.

It also does not run a historical benchmark, switch a candidate, execute a
BEFORE/AFTER/Revert experiment, restore the old A/B/C/D benchmark architecture,
modify sandbox/Full Access, Runner coverage, Independent Review strategy,
Context Compiler, Agent command batching, or any other Workflow candidate.

The protocol and manual Markdown template are sufficient for #91 and other
approved Workflow candidates; a future need for automation requires a separate
Task.
