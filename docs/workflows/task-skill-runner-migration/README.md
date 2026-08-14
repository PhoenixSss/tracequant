# Task Workflow Skill Runner Migration

Task #85 changes the normal Task lifecycle Skills from raw mechanical evidence and validation entry points to the fixed WSL2 Evidence and Validation Runner front doors. The migration removes duplicated normal-path instructions without removing semantic review, failure diagnosis, write approvals, or manual merge.

## Normal authority map

| Phase | Evidence authority | Validation authority | Stability check |
| --- | --- | --- | --- |
| Delivery | `delivery`, then `delivery-readiness` | named targeted presets during development; `workflow-delivery` for final phase validation | readiness snapshot |
| Review | trusted-base `evidence-runner review` | trusted-base `validation-runner workflow-review` | trusted-base `recheck` |
| Closeout | `closeout-readonly` | `workflow-closeout` on clean synchronized `main` | `recheck` |

The fixed runners own mechanical collection, schema, integrity, bounded output, and phase validation. Skills retain intent authorization, acceptance interpretation, code inspection, finding classification, write decisions, and lifecycle judgment.

## No duplicate normal path

A successful phase invokes the fixed front door and consumes its compact digest. It must not also run the complete raw `workflow_evidence.py`, `workflow_validation.py`, direct `gh`, or direct CI command chain. Raw tools remain implementation internals, tests, or a documented bounded fallback when the fixed runner itself is unavailable or reports a specific unsupported fact. The fallback must be reported and cannot convert partial/unknown into pass.

## Trusted Review

Review extracts the fixed Evidence and Validation Runner bundles from the locked base via `trusted_runner.py`. The target repository remains the PR worktree; the control plane comes from the locked base. Delivery conclusions are not inherited. Head, diff, checks, threads, or Project drift invalidates the verdict.

Task #85 is the bootstrap migration that first introduces the new trusted front-door extraction. Its own review must use the predecessor base control plane because the locked base cannot contain code introduced by this PR. After Task #85 merges, later Task reviews use the fixed trusted front doors normally.

## Closeout boundary

Task #85 changes only the read-only evidence and validation entries used by Closeout. It does not implement a new Closeout state machine, merge a PR, widen branch-deletion permissions, or remove the maintainer's manual-merge requirement.

## Evidence and publication materials

- `before-after-command-paths.json`: exact static comparison against the uploaded Task #84 main package.
- `removed-legacy-paths.md`: removed/disabled routes and retained semantic responsibilities.
- `rollback-and-compatibility.md`: version, bootstrap, fallback, and rollback contract.
- `examples/`: compact success and bounded failure/unknown/drift behavior.
- `publication-materials.json`: claims, metrics, limitations, and final-document mapping.
- `live-evidence-capture-plan.md`, `publication-readiness.md`, and `templates/`: bounded local Delivery evidence contract.
- `visuals/`: editable CSV and Mermaid sources.

Static reductions are observed source facts. Runtime Guardian, Token, and end-to-end command reductions are not claimed here; Task #86 owns the controlled candidate experiment.
