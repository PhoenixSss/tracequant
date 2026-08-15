# Runtime Projection & Control-base Contract — task-65-round-2-v2

Authoritative source: Issue #125 "Runtime Projection Model", "Materializer",
"Control-base Model" sections. Defines how a Formal Arm's runtime tree is
mechanically derived from a generation manifest, and the committed control
base that carries each Arm's runtime/PR/Review identity.

## Runtime projection model

The Formal Arm runtime tree is mechanically determined by the manifest; every
managed path resolves to exactly one three-state action:

| action | semantics | condition |
|---|---|---|
| `INSTALL_GENERATION_VERSION` | install the generation's version of the path | role = `EXECUTION_REQUIRED`, `VALIDATION_PRESENCE_REQUIRED`, or a source-existing `OPTIONAL_HISTORICAL_LIMITATION`; `IDENTITY_REQUIRED` must give explicit per-file `INSTALL` / `INHERIT` / `ABSENT` + `reason`, **no implicit default** |
| `INHERIT_BUSINESS_BASE` | keep/inherit the `BENCHMARK_BASE_SHA` version of the path | the path exists at `BENCHMARK_BASE_SHA` and the generation version is not installed |
| `ENSURE_ABSENT` | the path is physically absent from the control-base tree | deletion required (e.g. A generation `.claude/**`, `.codex/**`, `CLAUDE.md`) or explicit exclusion |

Rules:

- `runtime_install = false` alone is **not** a decision; keep-vs-delete is
  always explicitly given by `INHERIT_BUSINESS_BASE` / `ENSURE_ABSENT`; the
  materializer never guesses.
- `GENERATION_CONTROL_PLANE` paths must never use `INHERIT_BUSINESS_BASE`; a
  source-existing historical control-plane path is installed verbatim, while
  a source-absent path is explicitly `ENSURE_ABSENT`.
- Tree paths not covered by the projection inherit `BENCHMARK_BASE_SHA`
  (business tree content does not vary with the generation).
- A-generation projection must make `.claude/**`, `.codex/**`, `CLAUDE.md`
  **absent** from the control-base tree; B-generation projection replaces
  `.claude/`, `.codex/rules/`, `CLAUDE.md` with B-generation versions.
- Standalone fixture smoke tests, provenance docs, benchmark manifests, and
  historical audit-only material never override the current business tree
  merely by existing in a full fixture.

### Mechanically complete managed-path universe

The projection tooling derives one bounded runtime-control-plane universe from
the union of the business-base tree and the historical generation source. It
classifies repository instruction files (`AGENTS.md`, `CLAUDE.md`), `.agents`
policies/skills and execution-profile input, `.claude/**`, `.codex/**`,
`tools/agent_workflow/**`, workflow validation inputs under `tests/tools/**`,
`docs/workflows/**`, and the current workflow identity documents
`docs/development/issue-workflow.md` and `docs/development/pr-review.md`.
The conductor-only namespaces `benchmarks/task-65-round-2-v2/**` and
`tests/benchmarks/**` are separately classified as
`CONDUCTOR_BENCHMARK_TOOLING`; A/B completeness covers them with explicit
directory `ENSURE_ABSENT` sentinels so they cannot enter native generation
validation by inheritance. Other ordinary business paths remain outside this
universe.

Every derived path must be covered by exactly one projection entry. A
directory absence sentinel such as `.claude` explicitly covers its descendants;
it is still checked against every derived file path. A current-only
workflow/control-plane path is `ENSURE_ABSENT` unless an exact, canonical
source-absent inherit allowlist entry explicitly authorizes
`INHERIT_BUSINESS_BASE`. Omission from a historical manifest is never an
inherit decision. Missing, duplicate, or invalid coverage fails closed.

## Materializer

Deterministic materializer extracting generation fixtures blob-by-blob from
git objects with schema validation; identical manifest input always yields
the same tree/hash.

Hermetic / fail-closed contract:

- **verbatim blob extraction** (byte-for-byte, no rewriting of any kind);
- **deterministic** (same input → same output);
- **idempotent** (re-running changes nothing);
- **no synthetic repair** (stale references are never fixed; current files are
  never spliced in to make a historical generation runnable);
- **no current-generation fallback** (missing/mismatched historical files are
  never replaced by current files);
- **no cross-generation shared runtime file** (one runtime file is never
  shared across generations);
- **no symlink**;
- **validate git file mode** (destination mode matches the source blob mode);
- fail-closed conditions (any trigger → **HUMAN GATE**): source path missing;
  blob mismatch; sha256 mismatch; manifest/source identity mismatch;
- two materializations of the same source + manifest must produce
  byte-identical bundles / identical tree identity.

The fixture store is conductor-only, in the **conductor-local gitignored store**
(`.agents/benchmark-fixtures.local`); fixtures are **never committed** to the
repository. The materializer depends only on git objects + manifest, never on
host/environment dimensions (environment is covered by
`Workspace / Environment Isolation`). Tooling: `tooling/generation_materializer.py`.

## Control-base model

**Committed Arm Control Base** — each Arm's runtime/PR/Review identity base is
a **committed** (not worktree-overlaid) control base.

### A/B construction contract (once per Arm, instantiated by #86)

1. `git checkout --detach BENCHMARK_BASE_SHA`
2. create the deterministic frozen control-base branch (naming per the Arm
   Identity / PR Contract);
3. apply the generation's runtime control-plane projection (three-state
   actions);
4. commit exactly **one synthetic benchmark-only control-plane commit**;
5. obtain `A_ARM_CONTROL_BASE_SHA` / `B_ARM_CONTROL_BASE_SHA`.

Synthetic-commit invariants:

- parent == `BENCHMARK_BASE_SHA`;
- diff contains only manifest-allowed runtime control-plane path
  additions/deletions/changes;
- no Task #65 business implementation;
- no benchmark result / evidence / other-generation files;
- never merged to `main`;
- used only as the Arm's immutable experimental PR base; **the branch must not
  move during the Arm run** — any movement → identity invalid → fail closed;
- the commit tree/hash must be verified by the validator.

### C/D model

No synthetic commit; immutable frozen control-base branch whose tip points
directly at `BENCHMARK_BASE_SHA`:
`C_ARM_CONTROL_BASE_SHA = D_ARM_CONTROL_BASE_SHA = BENCHMARK_BASE_SHA`. Each
C/D business PR uses its corresponding frozen control-base branch as base.

### Control-base validator (deterministic, delivered by Preparation)

Verifies:

- parent == `BENCHMARK_BASE_SHA`;
- diff ⊆ the generation's `runtime_control_plane_paths`;
- no business source changes, no Task #65 implementation, no benchmark
  result/evidence, no other-generation files;
- projection actions verified mechanically: `INSTALL_GENERATION_VERSION` →
  hash == the path's blob/hash in the generation manifest;
  `INHERIT_BUSINESS_BASE` → hash/tree == the path's blob at `BENCHMARK_BASE_SHA`;
  `ENSURE_ABSENT` → path physically absent;
- installed runtime control-plane matches the fixture manifest's blob/hash;
- `expected_absent_paths` are truly absent from the control-base tree;
- control-base worktree clean.

The validator also derives the complete managed-path universe and fails closed
when any path has no action, more than one covering action, or an undeclared
current-only workflow path remains in the projected control base. Current
generation run-locking applies the same completeness check to template path
classes.

Any failure → **HUMAN GATE**. The validator is testable in the PREP
validation phase on a local ephemeral clone (construct → verify → discard,
never creating remote branches). Tooling: `tooling/control_base_validator.py`.

## Ownership boundary

The four control-base branches belong to the **#86 run identity**: Preparation
delivers only the naming schema / creation contract / validator / manifest
fields — it never creates branches; #86 freeze/execution instantiates
branches / SHAs / PRs.
