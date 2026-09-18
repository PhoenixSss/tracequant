---
name: task-delivery-runner
description: Deliver a ready leaf Issue, remediate an explicitly identified failed Review, or refresh an existing Review candidate onto current main when the maintainer explicitly requests it.
---

# Leaf delivery runner

Use this Skill for one existing leaf Issue explicitly named by the maintainer. The
Issue number is the mechanical key; the current GitHub title and body are canonical.

## Execution route contract

Select the route from the exact LCK invocation before running it. Known
Git-metadata or GitHub-lifecycle write operations use `elevated-first`:
`delivery prepare`, `delivery complete`, `remediation prepare`,
`remediation complete`, `refresh`, and `closeout`. Source-repository read-only
operations use `sandbox-first`: `status`, `review prepare`, `review complete`,
`remediation no-change`, and `merge preflight` (including its compatibility
alias `merge-preflight`).

Read the optional ignored `.agents/execution-profile.local.toml` when present.
It may route only exact documented runner invocations and cannot alter Task/PR
IDs, base/head SHAs, repository, output paths, or profile semantics. The route
classification is deterministic and is resolved before the command starts; an
Agent must not probe the normal sandbox first when the matching rule is
`elevated-first`. The profile must not contain a generic `uv`, `python`, `git`,
or `gh` write rule. Explicit read-only rules may remain `sandbox-first`,
including the source-repository boundary of Independent Review.

This route only selects the execution context for a command already authorized
by this Skill and LCK; it does not grant branch, commit, push, PR, Project,
merge, cleanup, or lifecycle authority. Do not intentionally run a known write
operation in the sandbox to obtain a predictable `.git/index.lock` or
equivalent failure before using its approved route.

Known heavyweight LCK operations (`delivery complete`, `refresh`,
`review prepare`, and formal workflow validation) use a fixed 30-second wait
window for the first wait and every subsequent still-running poll (for example,
`write_stdin`/`yield_time_ms=30000`). A process that exits earlier is returned
immediately; the 30-second value is a maximum wait window, not a minimum
runtime. Adaptive polling intervals are not part of the workflow contract.

Codex-only failure classes are `sandbox-denied` (local process, network, or an
exact ignored output path blocked) and `credential-isolated` (credentials
unavailable only in the current context). Only these two justify an
exact-context retry; a real command failure never justifies a broader-permission
retry or an equivalent direct command chain.

## Select one workflow

Choose exactly one branch and read only its linked instructions:

- For a new implementation or ordinary Delivery request, read
  [references/initial-delivery.md](references/initial-delivery.md).
- For Remediation, require an explicit maintainer request and the failed LCK
  `review_id`, then read [references/remediation.md](references/remediation.md).
- For Candidate Refresh, require an explicit maintainer request for an existing
  leaf Issue in Review, then read
  [references/candidate-refresh.md](references/candidate-refresh.md).

Do not infer Remediation from an open PR, failing checks, comments, or an old review.
If neither branch can be selected safely, stop at a Human Gate.

The routed LCK entry points are `delivery prepare` / `delivery complete`,
`remediation prepare` / `remediation no-change` / `remediation complete`, and
the one-shot `refresh <TASK>` operation.
Their exact commands in the selected reference use the stable
`uv run --frozen python -m tools.lck` front door.

## Shared guardrails

Read applicable `AGENTS.md`. Use these canonical owners only when their decisions
apply to the selected branch:

- `.agents/policies/command-execution.md` for launcher preflight and failure
  classification;
- `.agents/policies/context-retrieval.md` for scoped context acquisition;
- `.agents/policies/workflow-evidence.md` for validation and evidence consumption;
- `docs/workflows/lck/lifecycle.md` and, for Remediation,
  `docs/workflows/lck/review-and-remediation.md` for shared lifecycle semantics.

Before the first LCK command, verify `command -v uv`, `uv --version`, and
`uv run --frozen python --version`. A launcher failure is an environment failure,
not a lifecycle verdict.

LCK alone owns branch selection, staging, commit, push, PR identity/effects, and
Project lifecycle writes. Never replace an LCK STOP with direct Git/GitHub commands,
an archived snapshot, guessed identity, direct Git fallback, or a broader-permission
retry of a real command failure. Candidate Refresh is the sole narrow exception for
an LCK-owned exact `--force-with-lease`; Agents never run it directly. Unknown,
stale, divergent, or ambiguous authority fails closed.

A successful branch stops at its documented result and Human boundary. This Skill
never starts Independent Review, merges, closes an Issue, performs Closeout, or
assesses Feature completion.
