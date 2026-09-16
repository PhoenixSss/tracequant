# Initial Delivery

Lifecycle semantics: `docs/workflows/lck/lifecycle.md` §6, consulted only when
needed. The active leaf contract defines scope; do not fabricate Critical Outcome
for non-Task profiles. Task requires Caller, Capability, Observable result and a
bounded `tests/.../test_*.py::test_*` Verification test.

1. Run `uv run --frozen python -m tools.lck delivery prepare <TASK>`.
   Proceed only on `READY_FOR_DELIVERY`; LCK owns workspace preparation and the
   verified Project Status transition to `In Progress`.
2. Implement the smallest complete contract change in that workspace. Use the
   matching focused pytest node, scoped static check or targeted Runner profile
   as development feedback. Before completion, inspect the entire workspace diff
   and remove unrelated, generated, secret-bearing or prohibited changes.
3. Once targeted-ready, enter LCK Delivery Complete directly, under the validation
   boundary in `.agents/policies/workflow-evidence.md`. The formal full checks
   belong to LCK, not a precautionary pre-run. A concrete failure, unresolved
   diagnostic concern or explicit maintainer request can justify diagnostic expansion.

```bash
uv run --frozen python -m tools.lck delivery complete <TASK> \
  --commit-message "<scoped commit message>" \
  --summary "<implementation summary>" \
  --risks "<risks or limitations>"
```

Supply semantic metadata only. LCK executes profile gates, formal validation,
validated-tree commit, remote/PR effects, check observation, Project Status Review
and final live identity verification. Pending/failed/missing checks are a
non-blocking Initial Delivery observation; they remain later Review/Merge gates.
A failed contract/gate or final postcondition ends this invocation; no fallback.

On `READY_FOR_REVIEW`, report canonical Issue/PR URLs, changed-file summary,
Critical Outcome (when applicable), formal validation, LCK effects, checks and
limitations, lifecycle state and semantic risks. Include the exact fresh-session
prompt, replacing the placeholder with the Task number:

```text
请使用 task-pr-review-runner，在全新独立会话中只读审查 Task #<TASK> 的当前 OPEN PR。
```

State that Independent Review, Merge, Issue close, post-merge closeout, branch
deletion and Feature completion were not performed. Stop at the Human boundary.
