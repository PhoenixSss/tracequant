# Arm Identity / PR Contract — task-65-round-2-v2

Authoritative source: Issue #125 "Arm Identity / PR Contract" section. This
contract binds four Arms (A/B/C/D) of the Task #65 v2 benchmark (protocol
identity `task-65-round-2-v2`).

## Arm identity schema

Preparation defines the schema (instantiated with real values at #86):

```text
arm_id / agent / generation_id / branch_name_template / pr_title_template /
pr_state = OPEN / pr_draft = false / merge_allowed = false /
auto_merge_allowed = false / closeout_allowed = false /
issue_link = Closes #65（PR body，native contract；closing linkage 由
Development linkage 提供，非 keyword）/ base_sha = BENCHMARK_BASE_SHA /
expected_base_sha / expected_head_sha / workspace_id / delivery_session_id /
review_session_id / evidence_namespace / rollout_namespace /
dynamic_secret_identity_set
```

Machine-readable instantiation: `schemas/arm-identity.schema.json` and
`registry/arm-identity-registry.json` (four deterministic arm records).

## Deterministic naming

| Arm | Agent | generation_id | business branch | control-base branch |
|---|---|---|---|---|
| A | codex | legacy-no-unified-runner-codex | `experiment/task65-v2-a-legacy-no-runner-codex` | `experiment/task65-v2-a-control-base` |
| B | codex | legacy-unified-runner-codex | `experiment/task65-v2-b-legacy-runner-codex` | `experiment/task65-v2-b-control-base` |
| C | codex | current-codex | `experiment/task65-v2-c-current-codex` | `experiment/task65-v2-c-control-base` |
| D | claude | current-claude | `experiment/task65-v2-d-current-claude` | `experiment/task65-v2-d-control-base` |

PR title template: `[Experiment] Task #65 v2 <A|B|C|D> …` (actual values
instantiated at #86).

## Business branch pre-setup

Before Delivery, #86 ensures via the ENSURE-LINKED-BUSINESS-BRANCH CONTRACT
(see `temporary-development-link-contract.md`) that each Arm's deterministic
business branch exists and is Development-linked to #65; the branch is created
from `ARM_CONTROL_BASE_SHA` and carries **no Task #65 business implementation
yet**.

## PR contract (all four Arms)

- **OPEN + NON-DRAFT** (`isDraft == false`).
- **PR body contains native `Closes #65`** (the generation's native PR-body
  closing-keyword line; historical/current Skills are never modified to use a
  different keyword for the benchmark).
- **`Closes #65` is NOT a linkage mechanism**: all four PR bases are per-Arm
  frozen control-base branches (not the repository default branch), so the
  closing keyword produces no `closingIssuesReferences` on a non-default base.
  Formal closing linkage must come from ENSURE-LINKED-BUSINESS-BRANCH → PR
  Development linkage promotion.
- **DO NOT MERGE**; body may state `EXPERIMENTAL — DO NOT MERGE`, without
  changing the non-Draft state.
- The PR is created or reused by the **measured generation's native Delivery**,
  on the **exact pre-linked business branch** (the conductor never pre-creates
  experimental PRs).
- PR base = the Arm's frozen control-base branch; head = the Arm's business
  branch (its final head is `expected_pr_head_sha`).
- **PR effective diff = `ARM_CONTROL_BASE_SHA..head` = only Task #65 business
  changes** (the control-base branch is a direct frozen ancestor of the head);
  the control-plane overlay must not appear in the effective diff. Verification:
  `git diff ARM_CONTROL_BASE_SHA..HEAD` contains only allowed business
  implementation scope.

## Identity binding and pr_resolve (per generation scope)

- **B/C/D**: use each generation's native `pr_resolve.py` fail-closed identity
  validation with fields `baseRefName` / `baseRefOid` / `headRefName` /
  `headRefOid` / `isDraft == false`. The immutable control-base branch
  guarantees GitHub `baseRefOid` stays equal to `ARM_CONTROL_BASE_SHA`.
- **A**: the historical generation has **no `pr_resolve.py`**; one must not be
  added to the A fixture for the benchmark (that would modify the generation
  fixture, which is forbidden). A's equivalent experimental identity
  requirements are verified mechanically by the conductor-side benchmark
  identity validator (`tooling/benchmark_identity_validator.py`) over the same
  fields — this is protocol identity validation, **not** a Generation A
  workflow modification and not an A native workflow capability.
- `expected_pr_base_sha = ARM_CONTROL_BASE_SHA` (expected base branch =
  control-base branch, expected base SHA = `ARM_CONTROL_BASE_SHA`);
  `expected_pr_head_sha` = final business head; any mismatch → fail closed.
- **Base identity is a safety gate**: `baseRefName == expected control-base
  branch` and `baseRefOid == ARM_CONTROL_BASE_SHA` must be verified
  mechanically. If a PR is retargeted to `main`/default branch →
  `IDENTITY INVALID` → HUMAN GATE → the Arm must not continue; an experimental
  PR must never be kept in default-base state.
- **Independent Review binding (all four Arms)**: same `ARM_CONTROL_BASE_SHA`
  + final reviewed head SHA. Delivery records expected base/head SHA after PR
  creation; Review binds the same expected base + final reviewed head.
- All four Arms use their generation's **native PR handling** (no new
  benchmark-specific resolver, no `pr_resolve.py` modification, no gate
  override; A's identity validation goes through the conductor-side benchmark
  identity validator without modifying A's workflow).

## Tooling

- Schema: `schemas/arm-identity.schema.json`
- Registry (deterministic per-arm records): `registry/arm-identity-registry.json`
- Conductor-side identity validator (A and any benchmark identity validation):
  `tooling/benchmark_identity_validator.py`
