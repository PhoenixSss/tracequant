# Benchmark Information Boundary — task-65-round-2-v2

Authoritative source: Issue #125 "Benchmark Information Boundary" and
"Workspace / Environment Isolation" sections. Replaces any broader
"all historical repo content is readable" formulation with three classes.

## Class 1 — STATIC_REPOSITORY_CONTEXT (readable)

- Ordinary TraceQuant Git history, A/B workflow source commits (workflow
  source identity), workflow evolution, ordinary historical Issues / docs /
  ADRs, general development history without Task #65 benchmark answers, and
  protocol/process documents without answer-bearing content.
- **Unified rule: STATIC_REPOSITORY_CONTEXT is readable EXCEPT artifacts
  classified as PRIOR_BENCHMARK_CONTAMINATION_SECRET (Class 2).** Whether an
  artifact is closed, historical, or present in Git history **cannot override
  the Class 2 classification**.

## Class 2 — PRIOR_BENCHMARK_CONTAMINATION_SECRET (forbidden for A/B/C/D)

Historical material containing Task #65 implementation answers / findings /
verdicts / metrics; for the new v2 benchmark it is a contamination source. At
minimum: v1/v1.1 Windows business implementation, previous WSL candidate
implementation, previous Task #65 Delivery rollouts, previous Review
rollouts, previous findings, previous verdicts, previous rework results,
previous token metrics, previous durations, previous comparisons/evaluations,
and any prior-round material that could leak the Task #65 solution or
evaluation result.

- A closed/historical commit/PR/file does **not** automatically become
  readable; classification is based on whether it leaks this round's Task #65
  implementation/evaluation answers, not on creation time.
- v1/v1.1 Draft-PR behavior is **HISTORICAL PROTOCOL BEHAVIOR**, explicitly
  superseded by this protocol; it does not constitute generation semantics
  that v2 must inherit.
- Machine-readable inventory: `inventory/prior-benchmark-contamination-inventory.json`
  (16 entries; scope = all four Arms). Unclassified Task #65 prior-benchmark
  artifact → fail closed → contamination status NOT VERIFIED → Human Gate /
  #86 ratification.

## Class 3 — CURRENT_RUN_CROSS_ARM_SECRET (forbidden)

Current `task-65-round-2-v2` other-Arm dynamic output: implementation,
branch-specific diffs, unique commits, PR content, Delivery rollouts, Review
rollouts, findings, verdicts, rework, token metrics, durations, maintainer
evaluations, comparisons; plus previous-Arm dynamic identity exposed in
timeline metadata (see the Temporary Development Link Contract).

## Formal forbidden input

```text
FORBIDDEN_BENCHMARK_INPUT
  = PRIOR_BENCHMARK_CONTAMINATION_SECRET      (Class 2)
  + OTHER_ARM_CURRENT_RUN_SECRET              (Class 3)
```

PASS requires proving that both the Delivery session and the Review session
never read `FORBIDDEN_BENCHMARK_INPUT` (negative-evidence access audit).

## Workspace / environment isolation

- Each Arm runs in a **fresh isolated workspace**; the following must not be
  exposed in runtime input (mechanical constraint): other-Arm local
  branches/refs, other-Arm workspaces, other-Arm evidence/result/metrics,
  other-Arm rollout/review transcripts, conductor fixture-store path,
  conductor result/comparison path.
- cwd / environment variables must not point at the conductor workspace,
  fixture store, or other-Arm workspace/evidence.
- Static repository history still follows the Class 1 / Class 2 carve-out
  rules (no reintroduction of object-store absolute secrecy).
- Fixture manifest closures are explicitly limited to
  **repository-contained workflow generation input**.
- User-level Codex config, Claude global config, Guardian, host permissions,
  and the execution environment (including the historical WSL2 toolchain
  context) belong to the **environment manifest** recorded by #86 — never
  mixed into generation fixtures.
- Run-lock is generated at freeze; observability preflight runs before Arm
  start.

## Normal-reading carve-outs

Normal reading of #65 (title, body / frozen Task specification, labels,
native dependency metadata) is not leakage; only actually reading
previous-Arm dynamic identity triggers invalidation.
