# Claude adapter

Follow `AGENTS.md` for repository invariants and workflow routing. Claude Skills
under `.claude/skills/` are self-contained provider copies of the canonical
procedures under `.agents/skills/`.

- Use the `Skill` tool to invoke `.claude/skills/`; do not manually `Read`
  `.agents/skills/`. Each provider Skill set adapts to its own runtime.
- `.codex/rules/` is Codex-only and is ignored by Claude Code.
- Claude permissions come from `.claude/settings.json`, not from `.codex/rules/`.
- This repository runs Claude without a sandbox or elevated-execution layer;
  run documented LCK commands directly.

Claude-specific permissions do not change LCK authority, review independence,
fail-closed behavior, or the human-only merge boundary.
