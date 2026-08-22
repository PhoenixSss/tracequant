---
name: task-pr-review-runner
description: Independently review the current live Task PR in a fresh, implementation-read-only LCK context. LCK resolves the PR/base/head/effective diff/Task Contract/validation from live state and guards verdict applicability. The Review Agent only Inspect/Reason/Judge/Report; it never fixes, writes GitHub state, merges, closes Issues, or starts remediation.
---

# Task PR review runner

Use this Skill only in a fresh session that did not implement or remediate the head
being reviewed. Shared semantics are owned by `docs/development/pr-review.md` and
lifecycle placement by `docs/development/issue-workflow.md`.
Read applicable `AGENTS.md` and `.agents/policies/command-execution.md`; Review has no direct Git/GitHub write authority.

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

## 1. LCK Review Prepare

```bash
uv run --frozen python tools/agent_workflow/lck.py review prepare <TASK>
```

Proceed only on `READY_FOR_SEMANTIC_REVIEW`. Use the returned `review_id`, current
Task Contract, current review target, validation/check state, and `review_root`.
The `review_root` is a detached, clean, implementation-read-only worktree for the
live-resolved head.

If LCK returns STOP or stale, do not fall back to Evidence Runner snapshots, expected
SHAs, direct `gh` selection, or a Delivery handoff.

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
Task requirements produce FAIL.

## 3. Complete the same Review invocation

PASS:

```bash
uv run --frozen python tools/agent_workflow/lck.py review complete <TASK> \
  --review-id <REVIEW_ID> \
  --verdict PASS
```

FAIL: write the complete blocking findings to an ignored or temporary file outside the
read-only implementation worktree, then:

```bash
uv run --frozen python tools/agent_workflow/lck.py review complete <TASK> \
  --review-id <REVIEW_ID> \
  --verdict FAIL \
  --findings-file <FINDINGS_FILE>
```

LCK re-resolves the current target before accepting the verdict. A head/base change
returns `REVIEW_STALE_HEAD` / `REVIEW_STALE_BASE`; stale Review output is not PASS and
requires a new fresh Review invocation.

## 4. Report

On PASS, report `通过，可以人工合并` / `READY_FOR_HUMAN_MERGE`, the reviewed live head/base/diff, AC coverage,
validation/checks, limitations, and that the workflow stopped at the maintainer manual
Squash Merge boundary.

On FAIL, report `不通过，需要修复`, the findings and evidence, the returned
`STOP_REQUIRED`, and the failed `review_id` that the maintainer may explicitly use for
Remediation while it remains the latest completed Review. Do not emit an automatic
Delivery prompt and do not start Remediation.

Independent Review never modifies implementation, submits a GitHub Review, merges,
closes the Task, performs Closeout, or assesses Feature completion.
