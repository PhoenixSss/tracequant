---
name: task-pr-review-runner
description: Independently review the current live Task PR in a fresh, implementation-read-only LCK context. LCK resolves the PR/base/head/effective diff/Task Contract/validation from live state and guards verdict applicability. The Review Agent only Inspect/Reason/Judge/Report; it never fixes, writes GitHub state, merges, closes Issues, or starts remediation.
---

# Task PR review runner

Use this Skill only in a fresh session that did not implement or remediate the head
being reviewed. Shared semantics are owned by `docs/development/pr-review.md` and
lifecycle placement by `docs/development/issue-workflow.md`.
Read applicable `AGENTS.md` and `.agents/policies/command-execution.md`; Review has no direct Git/GitHub write authority.

## Execution route contract

`review prepare`, `review complete`, and `merge preflight` (including the
`merge-preflight` compatibility alias) use `sandbox-first`. These operations
keep the source repository read-only; Review's temporary clone and ignored LCK
runtime state remain operation-owned exceptions defined by the policy. This
classification is selected from the exact LCK invocation before it starts.

The route only selects the execution context for a command already authorized
by this Skill and LCK. It never grants GitHub, lifecycle, merge, or write
authority, and Review must not be rerouted through a generic elevated `uv`,
`python`, `git`, or `gh` rule. Preserve the policy's exact-context retry rules
for a genuine `sandbox-denied` or `credential-isolated` result.

## Invocation

```text
请使用 task-pr-review-runner，独立只读审查 Task #<TASK> 的当前 OPEN PR。
```

The Task number is the only mechanical key supplied to LCK. A maintainer may mention
a PR number for human intent, but the Agent MUST NOT pass PR/base/head/checks/snapshot
facts from Delivery as Review authority.

Before the first LCK command, verify the project launcher:

```bash
command -v uv
uv --version
uv run --frozen python --version
```

If this preflight fails, report an environment/launcher failure. It is not a
Review verdict, and must not be converted into `STOP_REQUIRED`.

## Long-operation wait and progress contract

For known heavyweight operations — `delivery complete`, `review prepare`, and
formal workflow validation — use a fixed 30-second wait window for the first
wait and every subsequent still-running poll (for example,
`write_stdin`/`yield_time_ms=30000`). Do not use adaptive 1-second, 10-second,
20-second, or 30-second polling. If the process exits earlier, return its
output immediately; 30 seconds is the maximum wait window, not a minimum
runtime.

During these operations, structured progress may appear on stderr. It is
bounded, non-authoritative observability only: use it to identify the current
operation and stage, never as evidence for eligibility, freshness, verdicts,
retry, Merge, Closeout, or any lifecycle result. The final stdout JSON remains
the only machine-parseable lifecycle result.

## 1. LCK Review Prepare

```bash
uv run --frozen python tools/agent_workflow/lck.py review prepare <TASK>
```

Proceed only on `READY_FOR_SEMANTIC_REVIEW`. Use the returned `review_id`, current
Task Contract, current review target, validation/check state, and `review_root`.
The `review_root` is a detached, clean, implementation-read-only standalone temporary
clone for the live-resolved head. The source tracked tree and source Git metadata remain
read-only; only ignored LCK operation/evidence state may be written outside the clone.
Review Prepare may begin while CI checks are pending or have failed; check success
is revalidated as a fresh gate by Review Complete and Merge Preflight.

If LCK returns STOP or stale, do not fall back to archived evidence snapshots,
expected SHAs, direct `gh` selection, or a Delivery handoff.

## 2. Semantic Review

Inside the read-only review context, do exactly:

```text
Inspect
Reason
Judge
Report
```

Read the complete effective diff and necessary related code. Build an independent AC
coverage/evidence matrix. The current Task Contract, effective diff, and necessary related
code are the default semantic context. Comments, Parent/Epic bodies, and other hierarchy
or history are not default Review input; expand only when the current Task explicitly
references them or a concrete ambiguity/dependency requires it. Check correctness, failure
behavior, tests, docs/config/public interfaces, and workflow/security boundaries when
applicable. Delivery conclusions, old Review verdicts, and old remediation rationale are
not evidence.

Findings use Blocking, High, Medium, Low, Nit. Blocking/High/Medium defects or unmet
Task requirements produce FAIL. When the unmet requirement is provider-attributed,
cross-provider, or otherwise can only be truthfully evidenced by a separate/fresh Review of
the candidate head, report it explicitly as a **Review-acceptance evidence gap**. Do not
word such a finding as a requirement to block the earlier Delivery/Remediation completion
that creates the head being reviewed, and never suggest fabricating the receipt.

## 3. Complete Review with a fresh applicability snapshot

`review complete` is a new LCK operation. Submit the semantic verdict for the sealed
Review Prepare target:

PASS:

```bash
uv run --frozen python tools/agent_workflow/lck.py review complete <TASK> \
  --review-id <REVIEW_ID> \
  --verdict PASS
```

FAIL: write the complete blocking findings to an ignored or temporary file outside the
read-only standalone Review clone, then:

```bash
uv run --frozen python tools/agent_workflow/lck.py review complete <TASK> \
  --review-id <REVIEW_ID> \
  --verdict FAIL \
  --findings-file <FINDINGS_FILE>
```

Review Complete acquires current authority exactly once and compares it with the sealed
Review Prepare target before accepting either verdict. A changed target returns an
explicit stale result such as `REVIEW_STALE_HEAD`, `REVIEW_STALE_BASE`,
`REVIEW_STALE_TASK`, or `REVIEW_STALE_DIFF`. A stale semantic verdict is not accepted as
the current Review result; start a fresh Review Prepare for the new target.

For a PASS, current applicable PR checks must also still pass. For a FAIL, current
checks are only observed so a semantic finding is not delayed by pending or failed
CI. Review Complete itself performs no nested/full-state refresh after its operation
snapshot is frozen.

A valid PASS returns `READY_FOR_MERGE_PREFLIGHT`, not direct merge readiness. FAIL returns
`STOP_REQUIRED` and stops; do not start Remediation automatically.

## 4. PASS-only merge preflight

Only after Review Complete returns `READY_FOR_MERGE_PREFLIGHT`, run the next read-only LCK
operation:

```bash
uv run --frozen python tools/agent_workflow/lck.py merge preflight <TASK>
```

Merge Preflight acquires a new fresh snapshot and independently verifies the accepted
Review receipt against the current Task / PR / head / base, current required checks,
blockers, and mergeability. It is intentionally a separate freshness boundary because
state may change again after Review Complete.

If Merge Preflight returns `READY_FOR_HUMAN_MERGE`, stop at the maintainer manual Squash
Merge boundary. LCK and the Review Agent never merge.

## 5. Report

On PASS, report `通过，可以人工合并` only after Merge Preflight returns
`READY_FOR_HUMAN_MERGE`. Include the reviewed head/base/diff, Review Complete
applicability result, merge-preflight result, AC coverage, validation/checks, and any
limitations.

If Review Complete returns a stale result, report that the semantic verdict was not
accepted for the current target and that a fresh Independent Review is required.

On FAIL, report `不通过，需要修复`, the findings and evidence, the returned
`STOP_REQUIRED`, and the failed `review_id` that the maintainer may explicitly use for
Remediation while it remains the latest completed Review. Do not run Merge Preflight,
Do not emit an automatic Delivery prompt, and do not start Remediation.

Independent Review never modifies implementation, submits a GitHub Review, merges,
closes the Task, performs Closeout, or assesses Feature completion.
