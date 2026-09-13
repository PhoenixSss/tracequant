# Claude adapter

Follow `AGENTS.md` for repository invariants and workflow routing. Claude Skills
under `.claude/skills/` are thin adapters to the canonical procedures under
`.agents/skills/`. Claude-specific permissions in `.claude/settings.json` do not
change LCK authority, review independence, fail-closed behavior, or the human-only
merge boundary.
