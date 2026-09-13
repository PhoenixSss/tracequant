# TraceQuant agent instructions

## Ownership boundary

TraceQuant product code belongs under `src/tracequant/`. The Local Control Kernel
(LCK) is repository tooling owned by `tools/lck/`; neither side may import the
other. `nautilus_trader` remains confined to the product integration boundary
documented in `docs/architecture/repository-structure.md`.

## Issue-driven workflow

Start from the maintainer-specified leaf Issue. Read its body and labels first;
expand to parents, blockers, comments, or broad design documents only when the
leaf contract is incomplete, ambiguous, safety-sensitive, or explicitly requires
that context. Git and GitHub provide mechanical identity and lifecycle facts;
conversation history and local receipts do not replace fresh LCK resolution.

Natural-language entries route to the canonical procedures:

- implementing an Issue: `.agents/skills/task-delivery-runner/SKILL.md`;
- reviewing a PR: `.agents/skills/task-pr-review-runner/SKILL.md`;
- closing out a manually merged PR: `.agents/skills/task-closeout/SKILL.md`;
- auditing Feature completion: `.agents/skills/feature-completion-audit/SKILL.md`.

The stable lifecycle entry is:

```bash
uv run --frozen python -m tools.lck --help
```

LCK owns deterministic branch, commit, push, PR, validation, receipt, freshness,
and recovery mechanics. Agents own semantic implementation and review. A human
maintainer alone authorizes merge and explicit remediation after Review FAIL.

## Repository rules

- Do not place LCK implementation or contracts in `src/tracequant/`.
- Do not place product implementation or product configuration in `tools/lck/`.
- Do not restore archived v1 business trees or import archived v1 modules.
- Keep product documentation outside `docs/workflows/lck/` and
  `docs/guides/lck/`; those two trees are LCK-only.
- Keep local LCK state ignored and bounded. It is diagnostic evidence, not
  lifecycle authority or a product persistence root.
- Unknown identity, stale state, incomplete evidence, or conflicting authority
  fails closed.
- Run the repository's locked pytest, Ruff, formatting, and mypy checks before
  delivery completion.

Detailed lifecycle semantics live in `docs/workflows/lck/lifecycle.md` and
`docs/workflows/lck/review-and-remediation.md`. Do not duplicate them here.
Context acquisition is governed by `.agents/policies/context-retrieval.md`.
