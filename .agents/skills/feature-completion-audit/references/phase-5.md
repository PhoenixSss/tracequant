## Phase 5: validation and remote checks

Run the Feature audit Validation profile against the locked audited-main
worktree. Read actual remote-main checks and Required-Checks configuration.
Preserve no-configured, plan-limit `403`, pending, failed, stale, cancelled,
skipped, and unavailable states.

A real validation failure is a completion gap. Missing or ambiguous evidence
without a confirmed defect uses the evidence-insufficient verdict.


Use the repository Validation Runner:

```bash
uv run --frozen python -m tools.lck.validation_runner run \
  --phase feature-audit --include-skill-validators --require-skill-validator
```

Record the actual invoking Skill package identity, Evidence/Validation Runner,
profile/schema, audited main, Feature snapshot, direct-child-set digest and
content hashes. Historical reports locate evidence but are not completion proof.
