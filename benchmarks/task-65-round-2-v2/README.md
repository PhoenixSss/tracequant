# task-65-round-2-v2 — Task #65 四代 Agent Workflow 可执行基准 Fixture 与分类注册

Protocol identity: `task-65-round-2-v2`. Preparation Task #125 deliverable
namespace. This namespace prepares the deterministic, reproducible,
mechanically verifiable generation fixtures and classification registry for
the four-generation Task #65 Agent Workflow benchmark (A/B/C/D). It does
**not** run the formal benchmark; all real instantiation (branches, SHAs,
PRs, measurements) belongs to #86.

## Layout

```text
benchmarks/task-65-round-2-v2/
  README.md                     this file
  schemas/                      JSON Schemas (generation manifest / arm identity /
                                contamination inventory / access event)
  manifests/                    A/B pinned manifests + C/D current-generation
                                template manifests; C/D run-locked output is
                                generated at freeze and consumed directly
  tooling/                      deterministic benchmark tooling (stdlib only)
  inventory/                    prior-benchmark contamination inventory (Class 2)
  registry/                     deterministic Arm identity registry (4 arms)
  contracts/                    6 contract documents (see below)
```

## Deliverables map (Issue #125)

| # | Deliverable | Location |
|---|---|---|
| 1 | A/B pinned generation manifests | `manifests/generation-{a,b}-pinned-manifest.json` |
| 2 | C/D CURRENT GENERATION TEMPLATE MANIFESTS | `manifests/generation-{c,d}-current-template-manifest.json` |
| 3 | run-lock program (at #86 freeze) | `tooling/run_lock.py` → C/D RUN-LOCKED MANIFESTS + `tooling/file_identity_report.py` → C/D FILE IDENTITY REPORT |
| 4 | deterministic materializer | `tooling/generation_materializer.py` |
| 5 | control-base validator (three-state projection) | `tooling/control_base_validator.py` |
| 6 | Arm identity schema / deterministic naming / creation contract (+ conductor-side A validator) | `schemas/arm-identity.schema.json`, `registry/arm-identity-registry.json`, `contracts/arm-identity-and-pr-contract.md`, `contracts/temporary-development-link-contract.md`, `tooling/benchmark_identity_validator.py` |
| 7 | prior-benchmark contamination inventory | `inventory/prior-benchmark-contamination-inventory.json` |
| 8 | Observability toolchain | `tooling/codex_rollout_adapter.py`, `tooling/claude_transcript_adapter.py`, `tooling/access_audit.py`, `tooling/observability_preflight.py` |
| 9 | contracts / tests / docs | `contracts/`, `tests/benchmarks/` |
| 10 | NON-FORMAL labeling convention | `contracts/non-formal-labeling-convention.md` |

## Contracts (authoritative sources)

- [arm-identity-and-pr-contract.md](contracts/arm-identity-and-pr-contract.md)
- [temporary-development-link-contract.md](contracts/temporary-development-link-contract.md)
- [runtime-projection-and-control-base-contract.md](contracts/runtime-projection-and-control-base-contract.md)
- [benchmark-information-boundary.md](contracts/benchmark-information-boundary.md)
- [observability-and-access-audit-contract.md](contracts/observability-and-access-audit-contract.md)
- [non-formal-labeling-convention.md](contracts/non-formal-labeling-convention.md)

## Key facts

- **Generations**: A = legacy-no-unified-runner-codex
  (`A_WORKFLOW_SOURCE_SHA` = `a492f0b334f950f2613b4b2204e96bef413355be`);
  B = legacy-unified-runner-codex
  (`B_WORKFLOW_SOURCE_SHA` = `e4a38d8404b6ad935fc24430a70e715b4504aa57`);
  C/D = current generation (`current-codex` / `current-claude`), sourced from
  `BENCHMARK_BASE_SHA` (locked at the #86 freeze).
- **Runtime projection** (three-state): `INSTALL_GENERATION_VERSION` /
  `INHERIT_BUSINESS_BASE` / `ENSURE_ABSENT`; `runtime_install = false` alone
  is never a decision; identity-required paths need explicit per-file actions.
- **Path ownership**: `BUSINESS_SNAPSHOT`, `GENERATION_CONTROL_PLANE`, and
  conductor-only `CONDUCTOR_BENCHMARK_TOOLING` are mechanically distinct; see
  [path ownership and validation closure contract](contracts/path-ownership-and-validation-closure-contract.md).
- **Control bases**: A/B = one synthetic benchmark-only control-plane commit
  on top of `BENCHMARK_BASE_SHA`; C/D = frozen branch with tip directly at
  `BENCHMARK_BASE_SHA`.
- **Information boundary**: Class 1 STATIC_REPOSITORY_CONTEXT (readable
  except Class 2), Class 2 PRIOR_BENCHMARK_CONTAMINATION_SECRET (forbidden
  for all four Arms), Class 3 CURRENT_RUN_CROSS_ARM_SECRET (forbidden).
- **Fixture store**: conductor-local gitignored `.agents/benchmark-fixtures.local/`;
  fixtures are never committed.
- **Manifest input contract**: the materializer dispatches only on the explicit
  `kind` discriminator: `pinned` for A/B or `run_locked` for C/D.  Run-locked
  input carries the resolved source SHA, selector, lock timestamp, and complete
  generation identity digest; the materializer never re-resolves a mutable ref.
- **NON-FORMAL**: this Task's PREP validation only verifies tooling
  mechanism on `PREP_VALIDATION_SOURCE_SHA` (ephemeral clones, no remote
  branches); all rehearsal products are labeled NON-FORMAL and never mixed
  with formal products. After the #86 freeze, formal smoke runs against the
  actual locked `BENCHMARK_BASE_SHA`.

## Tooling usage (deterministic CLI, run from repo root)

```bash
uv run python benchmarks/task-65-round-2-v2/tooling/generation_materializer.py \
  --manifest benchmarks/task-65-round-2-v2/manifests/generation-a-pinned-manifest.json \
  --repo-root . --store .agents/benchmark-fixtures.local

# At freeze, materialize the generated C/D manifests independently.  The
# expected SHA is optional but recommended as an additional caller-side lock.
uv run python benchmarks/task-65-round-2-v2/tooling/generation_materializer.py \
  --manifest <OUT>/generation-c-run-locked-manifest.json \
  --repo-root . --store <OUT>/c-bundle --expected-source-sha <BASE>

uv run python benchmarks/task-65-round-2-v2/tooling/control_base_validator.py \
  --arm A --manifest .../generation-a-pinned-manifest.json \
  --benchmark-base-sha <BASE> --control-base-sha <ARM_CONTROL_BASE_SHA>

uv run python benchmarks/task-65-round-2-v2/tooling/run_lock.py \
  --template manifests/generation-c-current-template-manifest.json \
  --template manifests/generation-d-current-template-manifest.json \
  --benchmark-base-sha <BASE> --out-dir <OUT>

uv run python benchmarks/task-65-round-2-v2/tooling/access_audit.py \
  --events <events.json> --inventory inventory/prior-benchmark-contamination-inventory.json \
  --context-inputs <context-inputs.json> \
  --capture-complete --parser-supported --audit-executed
```

The Claude transcript adapter normalizes the current runtime transcript
(Claude Code VSCode 2.1.226) into access events + context inputs, with the
resolved session identity injected explicitly:

```bash
uv run python -c "
import sys; sys.path.insert(0, 'benchmarks/task-65-round-2-v2/tooling')
from claude_transcript_adapter import parse_transcript_file
events, context_inputs, diagnostics = parse_transcript_file(
    '<TRANSCRIPT>.jsonl', arm_id='D', session_id='<SESSION_ID>')
print(diagnostics)
"
```

Tests: `uv run pytest tests/benchmarks/`.

Tooling modules are flat (top-level imports resolved via the tooling
directory on ``sys.path``; no package form).  Local strict type-check of the
tooling itself (CI only checks ``mypy src tests``):

```bash
MYPYPATH=benchmarks/task-65-round-2-v2/tooling \
  uv run mypy --strict benchmarks/task-65-round-2-v2/tooling
```
