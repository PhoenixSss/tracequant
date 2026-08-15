---
name: task-pr-review-runner
description: Independently and strictly read-only review one maintainer-specified implementation-bearing leaf PR in a fresh session. Lock base/head/effective diff, inspect the complete change, run Review Runners, classify findings, output one fixed verdict, and when the verdict is not passing emit a bounded remediation handoff for task-delivery-runner. Never fix, write GitHub state, submit a review, merge, close Issues, perform closeout, or assess Feature completion.
---

# Task PR review runner

Use this Skill for one existing implementation-bearing leaf PR in a new session that did not
participate in specification interpretation, implementation, fixes, commit,
push, or PR creation. Otherwise stop with:

```text
本会话不能提供独立审查
```

A Delivery handoff locates the object but is not correctness evidence
(no-verdict-inheritance, `docs/development/pr-review.md` §1).

## Standard invocation

```text
请使用 task-pr-review-runner，独立只读审查
[Task] <当前完整标题> #<Task编号>
对应的 PR #<PR编号>。

Expected base SHA: <base SHA>
Expected head SHA: <head SHA>
```

Task and PR numbers are primary keys; the current Issue title is canonical.
A request may limit execution to one named Phase. Verify prior Phase facts using
the Evidence Runner snapshot that Phase would have produced (`review` for
identity/lock, `recheck` for stability) — do not substitute direct `gh` or
`git` queries for Runner snapshots — and stop at the requested boundary.

## Policies and Runner interface

Read applicable `AGENTS.md` / `AGENTS.override.md` and:

```text
.agents/policies/workflow-evidence.md
```

Shared review semantics (fresh session, head lock, independent judgement,
verdict semantics, remediation handoff) are owned by
`docs/development/pr-review.md`. Read the minimal needed section for the
current phase; do not duplicate review-semantic prose in this Skill.

Use the current repository Runner interfaces in this order:

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py review \
  --task <TASK> \
  --pr <PR> \
  --expected-base-sha <LOCKED_BASE_SHA> \
  --expected-head-sha <LOCKED_HEAD_SHA> \
  --skill-path .claude/skills/task-pr-review-runner/SKILL.md

tools/agent_workflow/wsl2_validation_runner.py workflow-review \
  --base-sha <LOCKED_BASE_SHA> \
  --skill-path .claude/skills/task-pr-review-runner/SKILL.md

tools/agent_workflow/wsl2_github_evidence_runner.py \
  recheck --snapshot-id <LOCKED_SNAPSHOT_ID> \
  --skill-path .claude/skills/task-pr-review-runner/SKILL.md
```

The `--skill-path` argument records the actual calling Skill identity in every
artifact. Claude Code callers pass `.claude/skills/task-pr-review-runner/SKILL.md`;
Codex callers pass `.agents/skills/task-pr-review-runner/SKILL.md`. The Runner
re-hashes content independently and **fails closed** when the path is not within
an allowed Skill root (`.agents/skills/` or `.claude/skills/`).

The initial snapshot defines the reviewed identity. `workflow-review` is the
independent CI-equivalent validation for the locked head. `recheck` verifies
stability before verdict.

For `partial`, `unknown`, `fail`, truncation, schema mismatch, or drift, inspect
only the named facts or failed commands. Semantic review may read any code or
context needed to judge correctness.

Record the actual Review Skill, Runner, profile/schema, repository, reviewed
base/head/diff, and content hashes in the evidence artifacts. When the PR
changes Skills, Runners, Rules, or workflow governance, review those changes,
their tests, permissions, and failure behavior explicitly; Runner success alone
is not proof of correctness.

## Execution model

Claude Code executes commands directly in the user's shell environment — there
is no sandbox isolation layer. Git, `gh`, Python, subprocess, network, and
filesystem access all work natively. The Codex Guardian sandbox/elevated routing
model does not apply.

Runner commands are deterministic Python CLI tools invoked from the repository
root on the WSL2 Linux filesystem. They are never wrapped in `python`,
`bash -c`, `sh -c`, `uv run`, command substitution, pipelines, redirection, or
a generic shell string. Each Runner call is a single Bash tool invocation.

The Bash tool itself may prompt for user approval on first use. To suppress
these prompts for the documented Runner invocations, pre-authorize in
`.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(tools/agent_workflow/wsl2_github_evidence_runner.py *)",
      "Bash(tools/agent_workflow/wsl2_validation_runner.py *)"
    ]
  }
}
```

Runner commands can fail, but not because of sandbox restrictions. Read the
Runner's own output to classify:

- `pass`: all commands succeeded, inspect the compact digest.
- `fail`: one or more commands returned non-zero. Read the named failure
  artifact before deciding how to proceed.
- `blocked`: a Runner precondition failed (e.g. unclean worktree, wrong branch,
  wrong cwd, identity mismatch). Fix the precondition; do not retry with
  different arguments.
- `partial` / `unknown`: bounded diagnostics in the artifact — inspect only the
  named gates.

Never fall back to an equivalent direct command chain after a Runner result.
Never retry a Runner command with modified arguments to work around a failure.

## Tool discipline

This Skill runs in an agent session with a specific set of available tools.
Before every tool invocation the Reviewer must verify:

1. **File existence**: Confirm the target file exists in the changed-file
   inventory or via a lightweight existence check before reading. A deleted
   file is an observation, not an error to surface as a tool failure.

2. **Tool availability**: Only use tools the current session environment
   actually provides. Do not invoke `Grep` for structured searches — use
   `grep` via Bash or the Read tool. Do not try a tool, observe failure, and
   then fall back to an alternative; choose the correct tool first.

3. **Runner result independence**: A Runner non-zero exit code does not mean
   failure — verify whether the Runner produced a valid artifact with a
   `partial` / `unknown` / `fail` status before rejecting it. Conversely,
   a valid artifact with `partial` status is not `pass`.

4. **Search completeness**: A keyword grep or `diff --stat` does not satisfy
   the semantic review gate. The Reviewer must read the actual changed-file
   content per the evidence matrix requirements in Phase 3.

## Permission boundary

Review is strictly read-only for code, Git history, GitHub, Project, reviews,
threads, labels, Relationships, and lifecycle state. It may fetch refs, use one
isolated worktree for the locked reviewed head, run validation, and write exact
ignored local evidence artifacts.

It does not authorize file fixes, Issue/PR/Project edits, GitHub Review
submission, thread resolution, commits, pushes, merge, Issue close, branch
deletion, closeout, or Feature completion assessment.

## Phase 1: identify and lock

Generate `review`. Verify same-repository implementation-bearing leaf/PR,
canonical Issue type/state, exact closing linkage, PR open and non-Draft,
expected base/head, complete files/commits, checks, reviews, threads,
mergeability, and Required-Checks classification. Issue Specification v2 admits
`type:task` and `type:bug` for this lifecycle; the shared Runner contract uses
the authoritative `type:*` label and fails closed on missing, conflicting,
unknown, or non-reviewable types.

Lock and report:

```text
Reviewed base SHA
Reviewed head SHA
Merge base / effective-diff baseline
Effective diff digest
Complete file and commit inventory
Snapshot ID
```

Stop on material repository, Task, linkage, state, base, or head mismatch.

## Phase 2: read complete evidence

Independently read the current Task body, PR body and effective diff, every
changed file in context, commits, tests/docs/config/public interfaces,
relevant unchanged code, current reviews/threads/checks, and
review-relevant repository constraints.

Do not inherit Delivery conclusions or accept comments, test names, or green
checks without inspecting coverage.

Comments, Parent/Epic bodies, and other hierarchy/history are not default
review input. Expand to them only when the review scope, risk, or ambiguity
requires it — for example an acceptance criterion references a historical
decision, the Task body is insufficient to judge a change, or a conflict
must be located. Expansion is bounded: read the minimum relevant source or
section and stop once the question is resolved.

## Phase 3: semantic review with evidence matrix

### Evidence matrix

Before evaluating findings, build a structured evidence matrix binding the
current Task, PR, base SHA, head SHA, and effective diff. This matrix is the
single source of truth for all deterministic claims in the final report.

The matrix lives alongside the Runner artifacts in the same ignored local
evidence root. Its digest is recorded in the review report.

```json
{
  "task": "<TASK>",
  "pr": "<PR>",
  "base_sha": "<LOCKED_BASE_SHA>",
  "head_sha": "<LOCKED_HEAD_SHA>",
  "effective_diff_sha256": "<DIFF_DIGEST>",
  "review_skill": {
    "path": ".claude/skills/task-pr-review-runner/SKILL.md",
    "sha256": "<CONTENT_HASH>"
  },
  "changed_file_groups": [
    {
      "name": "<group-name>",
      "files": ["<repo-relative-path>"],
      "status": "verified | partially_verified | not_verified",
      "evidence": ["<deterministic-tool-reference>"],
      "findings": ["<finding-id-reference>"],
      "remaining_risk": "<explanation when not verified>"
    }
  ],
  "acceptance_criteria": [
    {
      "id": "AC-<n>",
      "text": "<criterion text>",
      "status": "verified | partially_verified | not_verified",
      "implementation_evidence": ["<file:line or tool reference>"],
      "validation_evidence": ["<test or Runner reference>"],
      "remaining_risk": "<explanation when not verified>"
    }
  ],
  "evidence_gates": {
    "review": "pass | partial | unknown | fail",
    "validation": "pass | fail",
    "recheck": "pass | partial | unknown | fail"
  },
  "overall": "verified | partial | not_verified"
}
```

### File coverage rules

- Every changed file in the `review` snapshot must be assigned to exactly one
  group.
- Groups are derived from the actual diff, not guessed. Examples:
  `runner-implementation`, `profiles-and-schema`, `rules-and-permissions`,
  `codex-skills`, `claude-skills`, `workflow-policies`, `pr-tooling`,
  `tests`, `documentation`, `provenance-or-migration`.
- If any file is not covered by at least one group, overall must not be
  `verified`.
- Each group must have at least one deterministic evidence reference (file
  content read, tool output, test result, Runner artifact).
- Read necessary unchanged related code to verify interface, caller, and
  failure-path consistency. Record those reads in the evidence references.

### Acceptance criteria mapping

- Extract every acceptance criterion from the Task body.
- Assign a stable ID (`AC-1`, `AC-2`, …) in the order they appear.
- Each criterion gets an independent status, evidence, and risk assessment.
- Multiple criteria may reference the same evidence but must not be compressed
  into a single "all satisfied" summary.
- Validation Runner `pass` proves command success, not semantic coverage.
  Criterion-level status must reflect whether the implementation, tests, and
  failure behavior genuinely satisfy the criterion.

### Mechanical assertions — deterministic standard

The following claims may only be marked as verified when the named
deterministic evidence exists:

| Claim | Required evidence |
| --- | --- |
| Historical Skill matches source commit blob | Git `cat-file -p <commit>:<path>` output compared byte-for-byte with current file |
| All target Skills are canonical-state | All applicable Skill files individually verified with path-audit or equivalent tool |
| Trusted-version / deprecated path completely removed | Full-text search of the entire repository worktree (not just grep of a few files) |
| Runner / profile / schema / Rules contract consistent | Runner's spec-validation step passed AND manual verification of each contract pair |
| Provenance manifest matches actual Git content | Byte-for-byte verification or deterministic manifest tool with matching hash |
| Permission configuration has no overly-broad authorization | Read and verify every permission entry in `.claude/settings.json` and `.codex/rules/` |

A file-existence check, partial grep, or inspection of only a subset of Skills
must not be enlarged into a comprehensive claim. When the deterministic
evidence is unavailable, mark the assertion `partially_verified` or
`not_verified` — do not guess.

## Phase 4: validation

Run `workflow-review`. Do not reuse Delivery validation. A real validation
failure, an incomplete applicable check, stale evidence, or unavailable required
evidence prevents an unconditional pass.

A recognized plan-limit `403` is not a successful Required-Checks query.
Approved fallback check evidence may support a passing verdict only when it is
complete, consistent, and non-contradictory.

## Phase 5: stability recheck

Run `recheck` against the initial snapshot. Recollect identity, base/head,
effective diff, files/commits, checks, reviews, and threads. Any new commit or
base/head/effective-diff change invalidates the review and requires a new
independent session. Evaluate check/thread-only changes under current gates.

Verify the recheck uses the same Skill identity as the initial review (same
`--skill-path`). Skill identity drift invalidates the review.

## Evidence status and verdict mapping

The Evidence Runner produces a **process** exit code (0, 3, 4) and a **status**
field in the output (`pass`, `partial`, `fail`). Process success (exit code 0)
is not gate pass: read the `status` field and map it deterministically.

Verdict conditions and the full mapping from evidence status, objective
gates, and findings to PASS / CONDITIONAL / FAIL are authoritative in
`docs/development/pr-review.md` §8. Read §8 when reaching a verdict; do not
re-derive the mapping here.

Plan-limit `403` (`required_checks_configuration = unknown`) defaults to
`Conditional pass — do not merge`; never self-approve a fallback without a
formally committed capability-limited policy (pr-review.md §8). A `recheck`
that returns `fail` or detects diff drift invalidates the review; a `recheck`
`partial` keeps the evidence ceiling at Conditional.

## Findings and verdicts

Use exactly: Blocking, High, Medium, Low, and Nit. Cite precise files/lines, Task
clauses, state, or validation evidence. Any unresolved Blocking/High/Medium
finding prevents pass (pr-review.md §8).

Output exactly one verdict; the verdict conditions and severity-to-verdict
mapping are authoritative in `docs/development/pr-review.md` §8:

```text
通过，可以人工合并
```

```text
有条件通过，不得合并
```

```text
不通过，需要修复
```

Minimal summary — apply the authoritative verdict mapping from pr-review.md
§8: PASS is the only mergeable state; CONDITIONAL is never mergeable;
incomplete evidence or an incomplete evidence matrix cannot produce PASS.

Head change during review is review invalidation, not a verdict:
`REVIEW INVALIDATED — HEAD CHANGED` (pr-review.md §8). The Reviewer never
merges.

## Remediation handoff

For `有条件通过，不得合并` or `不通过，需要修复`, emit one compact handoff after
the verdict:

```text
Remediation handoff

Task: #<Task>
PR: #<PR>
Reviewed head SHA: <SHA>
Verdict: <verdict>

Required remediation:
- [F1][Blocking|High|Medium] <defect, precise evidence, expected behavior>

Objective gates:
- <pending, unavailable, ambiguous, contradictory, or unstable gate>

Maintainer decision required:
- <scope, specification, public behavior, or architecture decision>
```

Rules:

- include only findings that caused the non-passing verdict; the bounded
  handoff semantics are authoritative in `docs/development/pr-review.md` §9;
- include objective gates that require recheck or waiting;
- include decisions that cannot be resolved without maintainer authorization;
- exclude Low and Nit findings unless the maintainer explicitly made them
  required;
- state the defect and expected behavior, but do not design the implementation;
- preserve finding IDs so the next Review can map repairs to the original
  evidence.

End with this exact remediation prompt populated with current identities:

```text
请按 task-delivery-runner 修复
[Task] <当前完整标题> #<Task编号>
对应 PR #<PR编号> 的独立审查问题，
并继续处理，直到 PR 再次准备好接受新的独立审查。

Review remediation handoff:

<上述 remediation handoff>
```

A passing verdict does not emit a remediation handoff.

**Enforcement**: Every non-passing verdict must output both the handoff and the
Delivery prompt. A partial handoff without the Delivery prompt is non-compliant.
A handoff that omits objective gates when the verdict is `有条件通过` is
non-compliant.

## Report and recovery

On clean success, report canonical Task/PR URLs, reviewed base/head/diff digest,
changed files/commits, acceptance coverage, findings, validation/checks,
reviews/threads/mergeability, limitations, actions not performed, one fixed
verdict, and:

```text
Reviewed head SHA: <actual SHA>
```

Every deterministic claim in the final report must be traceable to an evidence
matrix entry, a Runner artifact field, or a directly cited file:line.

Use a detailed report for any finding, fallback, `partial`/`unknown`, failure,
drift, conflict, or maintainer decision. For a conditional or failing verdict,
include the bounded remediation handoff and exact `task-delivery-runner` prompt.
Remove any temporary worktree by its exact path without destructive broad
cleanup. Never inherit an earlier verdict.
