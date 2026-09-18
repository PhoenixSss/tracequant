---
name: task-closeout
description: Complete post-merge closeout for a maintainer-specified leaf Issue after the maintainer says its PR was manually Squash Merged.
---

# Task closeout

Use this Skill only after the maintainer states that a specific PR was manually
Squash Merged and asks to close out its exact Task. That statement authorizes live
verification; it is not proof of merge. Task and PR numbers are keys, while current
GitHub facts remain authoritative. This Skill never merges.

Read applicable `AGENTS.md`, `.agents/policies/command-execution.md`, and the
Closeout evidence-consumption boundary in `.agents/policies/workflow-evidence.md`.
Shared post-merge semantics are in `docs/workflows/lck/lifecycle.md` §11 and §13.
Do not load business hierarchy, comments, or implementation history unless a live
linkage, closure, or merge-identity anomaly requires them.

Before a manual merge, the separate read-only gate is:

```bash
uv run --frozen python -m tools.lck merge preflight <TASK>
```

It may return `READY_FOR_HUMAN_MERGE`; it never performs the merge. After the
maintainer's merge statement, run the closeout entry point:

```bash
uv run --frozen python -m tools.lck closeout <TASK>
```

LCK reacquires the unique PR, merge identity, Issue/Project state, branch ownership,
and worktree safety. Never substitute old receipts, direct Git/GitHub commands, force
push/reset, manual Issue closure, or broader cleanup. Ambiguous identity, conflicting
PRs, divergence, or unsafe ownership fails closed.

Consume the compact result as:

```text
Business Delivery: COMPLETE | NOT_COMPLETE
Cleanup: COMPLETE | PENDING
```

A verified merge can make Business Delivery complete while cleanup remains pending;
rerun only the same LCK command for the documented idempotent recovery. LCK may
fast-forward main, converge lifecycle metadata after authoritative Issue closure,
and remove only the verified Task branch. A provider-deleted remote branch is a
normal state.

On terminal `COMPLETE` / `COMPLETE`, report the Task, bounded effects, limitations,
and stop. The returned `receipt_reference` is an audit pointer, not a default reading
assignment. Open only the evidence needed to diagnose STOP, `PENDING`, partial or
unknown state, an effect anomaly, insufficient compact output, or an explicit audit
request.

Report that no merge, manual Issue close, repair commit, unrelated branch cleanup,
or Feature completion was performed. Closeout never assesses Feature completion.

## Execution route contract

`closeout` uses `elevated-first` because its already-authorized effects may write
Git metadata and GitHub lifecycle state. `merge preflight` remains
`sandbox-first` because it is a source-repository read-only gate and never
merges. Select these routes from the exact LCK invocation before execution;
never apply an elevated route to generic `uv`, `python`, `git`, or `gh`
commands. The route changes execution context only and does not grant merge,
Issue, Project, label, branch, or cleanup authority.

`closeout` is a known heavyweight LCK operation and uses a fixed 30-second wait
window for the first wait and every subsequent still-running poll (for example,
`write_stdin`/`yield_time_ms=30000`). A process that exits earlier is returned
immediately; the 30-second value is a maximum wait window, not a minimum
runtime. Adaptive polling intervals are not part of the workflow contract.

Codex-only failure classes are `sandbox-denied` (local process, network, or an
exact ignored output path blocked) and `credential-isolated` (credentials
unavailable only in the current context). Only these two justify an
exact-context retry; a real command failure never justifies a broader-permission
retry or an equivalent direct command chain.

Read the optional ignored `.agents/execution-profile.local.toml` when present.
It may route only exact documented LCK invocations and cannot change merge
identity, lifecycle metadata, or cleanup scope.
