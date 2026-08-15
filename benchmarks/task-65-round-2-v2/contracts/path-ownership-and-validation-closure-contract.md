# Path Ownership & Native Validation Closure Contract

Authoritative for the task-65-round-2-v2 preparation tooling. This contract
separates the shared business snapshot from generation runtime inputs and from
conductor-only benchmark preparation material.

## Ownership classes

| class | owned paths | Arm runtime treatment |
|---|---|---|
| `BUSINESS_SNAPSHOT` | ordinary repository workload and product paths outside the explicit control-plane classes | inherited from `BENCHMARK_BASE_SHA` |
| `GENERATION_CONTROL_PLANE` | identity files, `.agents/**`, `.claude/**`, `.codex/**`, `tools/agent_workflow/**`, `tests/tools/**`, and `docs/workflows/**` | A/B are explicitly installed or absent by pinned manifest; C/D are selected by template path classes |
| `CONDUCTOR_BENCHMARK_TOOLING` | `benchmarks/task-65-round-2-v2/**` and `tests/benchmarks/**` | excluded from generation-native runtime validation; A/B projection uses explicit directory `ENSURE_ABSENT` sentinels |

The conductor namespace is not a historical workflow generation and must not
become an A/B validation input merely because it is tracked at the current
preparation source. The current PREP CI still validates it in the preparation
workspace; that is separate from native A/B validation.

## `docs/workflows/**`

The complete A/PREP delta is enumerated at freeze time, not inferred from a
single allowlist. Paths present at A are installed verbatim from A. Paths
present only at PREP are explicit `ENSURE_ABSENT` entries for A. B pins the
complete B-era path set. This prevents current workflow documentation from
silently inheriting into A and preserves A's historical documentation and
validation semantics.

## Closure rule

Before native validation, the control-base validator must pass the complete
managed-path gate. Current conductor paths are covered by an explicit absent
sentinel and are not passed to a generation's native test closure. No
historical test, Skill, or workflow source is rewritten to accommodate the
current preparation namespace.
