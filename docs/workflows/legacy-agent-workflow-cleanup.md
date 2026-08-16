# Task #123 Legacy Agent Workflow Cleanup Evidence

This document is the durable cleanup evidence for Task #123. It records the
admission proof, candidate universe, per-path classification, transition
evidence, retention decisions, and final reference graph. It does not define a
new workflow semantic owner.

## 1. Cleanup admission / Migration Acceptance applicability

The formal acceptance report is
[Task #122 Migration Acceptance Report](migration-acceptance/task-122-migration-acceptance-report.md).

```text
FORMAL_ACCEPTANCE_VERDICT =
MIGRATION COMPLETE

FORMAL_ACCEPTANCE_SHA =
8ed78da3377a85fc33ab673892f8ab66278d6f5a

FORMAL_ACCEPTANCE_TREE =
8d7c9596078c38bc34c30ead8f06f28b647a2bef

TASK_123_PRE_CLEANUP_MAIN_SHA =
d5168eb80d55f507e2f6eb78ec7a7dc75a163390

TASK_123_PRE_CLEANUP_TREE =
8d7c9596078c38bc34c30ead8f06f28b647a2bef

PRE_CLEANUP_TREE_IDENTITY_MATCH =
PASS
```

The tree identity was recomputed mechanically:

```text
git rev-parse 8ed78da3377a85fc33ab673892f8ab66278d6f5a^{tree}
= 8d7c9596078c38bc34c30ead8f06f28b647a2bef

git rev-parse d5168eb80d55f507e2f6eb78ec7a7dc75a163390^{tree}
= 8d7c9596078c38bc34c30ead8f06f28b647a2bef
```

The fresh GitHub state check for the cleanup admission recorded:

| Issue | State | Project | Relationship |
| --- | --- | --- | --- |
| #120 | CLOSED / COMPLETED | Done | blocks #122 |
| #121 | CLOSED / COMPLETED | Done | blocks #122 |
| #122 | CLOSED / COMPLETED | Done | blockedBy #120/#121; blocks #123 |
| #123 | OPEN | Review | blockedBy #122 |

The #122 comments collection was empty. The formal report above, rather than
Issue closure state, is the verdict source.

```text
MIGRATION_ACCEPTANCE_APPLICABILITY = PASS
CLEANUP_ADMISSION = PASS
```

## 2. Candidate universe

The candidate universe is the union of:

1. every candidate in the formal #122 Legacy Live-reference Matrix;
2. every path deleted by Task #123;
3. every path modified by Task #123 to converge legacy references;
4. every candidate found by the cleanup/reference/path audits; and
5. every compatibility or historical candidate explicitly checked and retained
   by Phase 2 or Phase 3.

Directory rows below are used only where every contained artifact has the same
classification and decision. Historical records that retain old path strings
are not current executable references.

## 3. Per-candidate cleanup inventory

| Artifact | Phase | Pre-cleanup classification | Replacement | Incoming references before cleanup | Runtime use before cleanup | Validation coverage | Decision | Post-cleanup state | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `.agents/skills/task-delivery/SKILL.md` | 2 | COMPATIBILITY ONLY | `.agents/skills/task-delivery-runner/` | `AGENTS.md`, `CLAUDE.md`, registry, `skill_path_audit.py` baseline, validation/test fixtures, historical evidence | No normal current routing; executable only through legacy/explicit path | Current Skill validators, path audit, routing tests, full workflow validation | DELETE | DEAD / ABSENT | E1, E3, E4, E5 |
| `.agents/skills/task-pr-review/SKILL.md` | 2 | COMPATIBILITY ONLY | `.agents/skills/task-pr-review-runner/` | Same legacy registry, audit, validation/test and historical evidence classes | No normal current routing; executable only through legacy/explicit path | Current Review Skill validator, path audit, routing tests, full workflow validation | DELETE | DEAD / ABSENT | E1, E3, E4, E5 |
| `docs/workflows/agent-skills.md` | 1 | ACTIVE | Current registry plus `skill_path_audit.py` | README/navigation and legacy registry/provenance links | Documentation only | Full-text audit, path audit and workflow tests | MODIFY / RETAIN | ACTIVE | E3, E4, E5 |
| `docs/workflows/workflow-evidence.md` | 1 | COMPATIBILITY ONLY | `.agents/policies/workflow-evidence.md` | README, agent-skills registry, token-optimization history | No executable caller; duplicate navigation/policy summary | Full-text reference audit, full validation and docs tests | DELETE | DEAD / ABSENT | E1, E3, E4, E5 |
| `docs/workflows/context-retrieval-v2/before-after-retrieval.md` | 3 | HISTORICAL EVIDENCE ONLY | None; frozen record | Historical documentation links only | None | Retrieval v2 tests; retained-file audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6, E7 |
| `docs/workflows/task-skill-runner-migration/` | 3 | HISTORICAL EVIDENCE ONLY | None; frozen migration record | Historical provenance links only | None | Migration-material tests; retained-file audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6, E7 |
| `docs/workflows/benchmarks/task-65-round-2/` | 3 | HISTORICAL EVIDENCE ONLY | None; frozen benchmark record | Historical provenance links only | None | Benchmark-material tests; retained-file audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6, E7 |
| `docs/workflows/task-workflow-first-token-optimization.md` | 1 | HISTORICAL EVIDENCE ONLY | `.agents/policies/workflow-evidence.md` for current pointer | Historical docs and old policy pointer | None | Docs/reference audit and full validation | MODIFY / RETAIN | HISTORICAL EVIDENCE ONLY | E3, E4, E7 |
| `docs/workflows/publication-materials/` | 3 | HISTORICAL EVIDENCE ONLY | None; publication archive | Historical publication references | None | Historical-material tests; retained-file audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6, E7 |
| `docs/workflows/wsl2-github-evidence-runner/` | 3 | HISTORICAL EVIDENCE ONLY | Current Runner source paths | Historical runner evidence and publication references | None | Runner material tests; retained-file audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6, E7 |
| `docs/workflows/wsl2-validation-runner/` | 3 | HISTORICAL EVIDENCE ONLY | Current Validation Runner source paths | Historical runner evidence and publication references | None | Runner material tests; retained-file audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6, E7 |
| `docs/workflows/wsl2-codex-environment/` | 3 | HISTORICAL EVIDENCE ONLY | Current environment/Runner contracts | Historical environment evidence and publication references | None | Environment/material tests; retained-file audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6, E7 |
| `docs/planning/quant-system-planning-baseline-v1.0.md` | 3 | HISTORICAL EVIDENCE ONLY | None; historical planning snapshot | Historical planning links | None | Retained-doc audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6 |
| `docs/research/*` | 3 | HISTORICAL EVIDENCE ONLY | None; historical research archive | Historical research links | None | Retained-doc audit | RETAIN | HISTORICAL EVIDENCE ONLY | E6 |
| `AGENTS.override.md` | 1 | DEAD | None; target absent | Stale instruction references in pre-cleanup Skills | None; target absent in accepted tree and current base | Post-cleanup full-text audit and Skill validation | REMOVE REFERENCES | DEAD REFERENCE TARGET | E2, E3, E4 |
| `docs/workflows/task-skill-ab.md` | 1 | DEAD | `skill_path_audit.py` | README and registry provenance links | None; target absent | Post-cleanup full-text audit and path audit | REMOVE REFERENCES | DEAD REFERENCE TARGET | E2, E3, E4 |
| `docs/workflows/task-skill-variants.json` | 1 | DEAD | `skill_path_audit.py` | Registry and workflow-evidence provenance links | None; target absent | Post-cleanup full-text audit and path audit | REMOVE REFERENCES | DEAD REFERENCE TARGET | E2, E3, E4 |
| `tools/agent_workflow/skill_variant_provenance.py` | 1 | DEAD | `skill_path_audit.py` | Registry and workflow-evidence provenance links | None; target absent | Post-cleanup full-text audit and path audit | REMOVE REFERENCES | DEAD REFERENCE TARGET | E2, E3, E4 |
| `tools/agent_workflow/trusted_runner.py` | 3 | DEAD | Current fixed Evidence/Validation Runners | Historical migration references only | None; target absent in accepted tree/current base | Trusted-runner absence tests and full validation | RETAIN ABSENT | DEAD / ABSENT | E6, E7 |
| `.agents/policies/task-workflow-telemetry.md` | 3 | DEAD | External rollout analysis boundary | Historical telemetry references only | None; target absent | Runtime telemetry removal tests | RETAIN ABSENT | DEAD / ABSENT | E6, E7 |
| `.agents/task-workflow-telemetry.example.toml` | 3 | DEAD | External rollout analysis boundary | Historical telemetry references only | None; target absent | Runtime telemetry removal tests | RETAIN ABSENT | DEAD / ABSENT | E6, E7 |
| `tools/agent_workflow/telemetry.py` | 3 | DEAD | External rollout analysis boundary | Historical telemetry references only | None; target absent | Runtime telemetry removal tests | RETAIN ABSENT | DEAD / ABSENT | E6, E7 |
| `tests/tools/test_task_workflow_telemetry.py` | 3 | DEAD | Runtime telemetry removal assertions | Historical test references only | None; target absent | Runtime telemetry removal tests | RETAIN ABSENT | DEAD / ABSENT | E6, E7 |
| `docs/workflows/task-workflow-telemetry.md` | 3 | DEAD | External rollout analysis boundary | Historical telemetry references only | None; target absent | Runtime telemetry removal tests | RETAIN ABSENT | DEAD / ABSENT | E6, E7 |
| `docs/workflows/task-workflow-token-baseline-63-64.md` | 3 | DEAD | External rollout analysis boundary | Historical telemetry references only | None; target absent | Runtime telemetry removal tests | RETAIN ABSENT | DEAD / ABSENT | E6, E7 |

```text
CANDIDATE_COUNT = 25
CANDIDATES_CLASSIFIED = ALL
DELETED_WITH_PROOF = 3 / 3
UNCERTAIN = NONE
HISTORICAL_EVIDENCE_RETAINED = PASS
```

For every deleted artifact above, the proof chain is:

```text
replacement exists
+ current callers/references converged
+ no runtime use
+ validation coverage exists
= DELETE permitted
```

The two legacy Skills and duplicate evidence document were
COMPATIBILITY ONLY before cleanup; they are not described as DEAD from the
start. Their incoming references were removed or redirected in this PR.
Historical documents may still contain old path strings as frozen provenance;
those strings are not current executable references.

## 4. Final source-of-truth matrix

| Artifact | Final classification | Responsibility |
| --- | --- | --- |
| `AGENTS.md` | ACTIVE | repository invariants and natural-language entry resolution |
| `CLAUDE.md` | ACTIVE | Claude-specific thin adapter and Skill discovery |
| `docs/development/issue-workflow.md` | ACTIVE | lifecycle/readiness/Delivery/Closeout/Feature semantics |
| `docs/development/pr-review.md` | ACTIVE | Independent Review semantics and verdict/remediation contract |
| `.agents/policies/workflow-evidence.md` | ACTIVE | deterministic evidence contract |
| Current Codex Skills | ACTIVE | Codex executable procedure |
| Current Claude Skills | ACTIVE | Claude executable procedure |
| Current Evidence/Validation Runners and helpers | ACTIVE | identity, gates, evidence and validation mechanics |
| Migration/benchmark/publication/research records | HISTORICAL EVIDENCE ONLY | immutable provenance and audit evidence |
| Claude permission-boundary guidance | COMPATIBILITY ONLY | dual-agent adapter guidance |
| Legacy Skills, duplicate evidence doc and provenance bundle | DEAD / ABSENT | replaced by current Runner/policy/audit surface |
| UNCERTAIN candidates | NONE | no unresolved candidate remained after audit |

## 5. Final reference graph

```text
AGENTS.md
  -> docs/development/issue-workflow.md
  -> current Delivery / Closeout Skills

Independent Review
  -> docs/development/pr-review.md
  -> current Review Skills

Evidence mechanics
  -> .agents/policies/workflow-evidence.md
  -> current Evidence / Validation Runners

Codex
  -> .agents/skills/* current Skills

Claude
  -> .claude/skills/* current Skills

legacy task-delivery / task-pr-review:
  ABSENT from active namespace

docs/workflows/workflow-evidence.md:
  ABSENT

dead current path references:
  ZERO

historical frozen evidence:
  RETAINED
```

The final dead-reference audit was run against the complete current worktree
for the target names in the formal #122 matrix and Task #123 diff. Historical
provenance mentions under migration, benchmark and publication evidence were
reviewed separately and retained intentionally.

Audit result:

- active routing, production-code and non-historical navigation references to
  the retired paths: `ZERO`;
- retained non-executable references: `agent-skills.md`'s retirement row,
  frozen migration/benchmark/publication records, this report and the
  historical #122 report;
- negative regression assertions: retained in
  `tests/tools/test_agent_neutral_workflow.py` and verified by the workflow
  test profile;
- dead target names (`AGENTS.override.md`, `task-skill-ab.md`,
  `task-skill-variants.json`, `skill_variant_provenance.py`): no live target
  or executable caller; only the evidence/negative-assertion classes above;
- `tools/agent_workflow/skill_path_audit.py`: `PASS`.

## 6. Evidence ledger

- E1 — Task #122 report, accepted identity, and Task #123 effective diff.
- E2 — `git ls-tree` at accepted SHA and pre-cleanup base proving dead targets
  are absent; tree identity command recorded above.
- E3 — `git grep` at pre-cleanup base for legacy Skills, duplicate evidence
  document and dead-pointer names.
- E4 — post-cleanup `rg`/git grep audit distinguishing current/executable
  references from historical provenance references.
- E5 — `tools/agent_workflow/skill_path_audit.py`, current schema 4, zero
  violations; current Skill and workflow regression tests.
- E6 — retained historical evidence and material-index tests.
- E7 — repository/workflow validation: lock, pytest, Ruff, mypy, diff check and
  Skill validators.

## 7. Task #123 boundary

This document does not authorize further deletion, workflow redesign, telemetry
creation, Context Compiler work, Issue Specification changes, Ruleset changes,
merge, or closeout. It records the evidence for the exact cleanup already in
Task #123 and must be reviewed independently at the new PR head.
