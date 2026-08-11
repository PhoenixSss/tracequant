# NON-FORMAL 演练产物标注约定 — task-65-round-2-v2

Authoritative source: Issue #125 "Smoke Validation" section and Deliverable
10. Governs the labeling of preparation/rehearsal products so they can never
be mistaken for formal benchmark products.

## Definitions

- **Formal product**: products of a #86-instantiated Arm run —
  control-base branches, Arm branches/PRs, evidence snapshots, run-locked
  manifests, audit reports, metrics — produced on the actual locked
  `BENCHMARK_BASE_SHA` toolchain.
- **NON-FORMAL rehearsal product**: any product of PREP validation —
  materializer / validator / adapter tests and smoke runs on the
  `PREP_VALIDATION_SOURCE_SHA` toolchain, local ephemeral clones
  (construct → verify → discard, never creating remote branches), and any
  dry-run of benchmark tooling. PREP smoke verifies **tooling mechanism
  only**, never Arm outcomes.

## Labeling rule

Any NON-FORMAL rehearsal product must be explicitly labeled **NON-FORMAL** —
in the artifact itself (file header, record field, or filename) and in any
record that references it — and must never be mixed with formal products.

Applies at minimum to:

- PREP smoke outputs of the materializer, control-base validator, run-lock,
  file-identity report, adapters, observability preflight, and access audit;
- ephemeral-clone validation runs;
- any dry-run manifest/bundle/record produced during this Task's own
  validation.

## Boundary

- PREP smoke runs on `PREP_VALIDATION_SOURCE_SHA` (fresh-locked at
  Preparation execution, separate from `BENCHMARK_BASE_SHA`).
- After the #86 freeze, formal smoke for A/B/C/D must be re-run against the
  actual locked `BENCHMARK_BASE_SHA` (formal smoke on the
  `BENCHMARK_BASE_SHA` toolchain).
- Historical A/B that cannot run fixture-verbatim on the `BENCHMARK_BASE_SHA`
  toolchain → **HUMAN GATE**; forbidden: fixing the historical fixture,
  current-generation fallback, synthetic repair.

## Consequence

A NON-FORMAL-labeled product that reaches a formal comparison, or a formal
claim built on a rehearsal product, invalidates the affected Arm's evidence
and requires a fresh run.
