# Observability & Access Audit Contract — task-65-round-2-v2

Authoritative source: Issue #125 "Observability / Access Audit" section.
Governs capture, normalization, and negative-evidence access auditing for the
Task #65 v2 benchmark. Tooling: `tooling/observability_preflight.py`,
`tooling/codex_rollout_adapter.py`, `tooling/claude_transcript_adapter.py`,
`tooling/access_audit.py`.

## Observability

### Adapters

Two benchmark-only adapters normalize observed records into mechanical access
events `arm_id / session_id / timestamp / tool / operation / target /
raw_event_reference`; `target` covers git args, gh args, Read paths, Grep
paths/pattern scopes, Glob scopes, shell commands, and evidence/rollout
paths.

- Codex rollout adapter (`tooling/codex_rollout_adapter.py`): parses
  `custom_tool_call` / `custom_tool_call_output` records.
- Claude transcript adapter (`tooling/claude_transcript_adapter.py`): parses
  `tool_use` / `tool_result` records from `~/.claude/projects/**` or the
  actual session transcript resolved at execution time.

Adapters only extract/normalize — they never interpret workflow semantics;
unknown record format → **FAIL CLOSED → NOT VERIFIED**. Parser pinning: the
four record types above. Adapters must ship tests with sample record
fixtures.

### Observability preflight (six checks, before Arm start)

1. **session identity resolvable**;
2. **transcript / rollout source locatable**;
3. **capture active before formal Agent work**;
4. **archive destination isolated for this Arm/session**;
5. **parser supports the current observed record format**;
6. **controlled test tool call appears in capture and can be normalized**.

Verdicts: `OBSERVABILITY VERIFIED` when all six checks pass;
**`BENCHMARK OBSERVABILITY NOT VERIFIED`** on any failure → the formal
Delivery must not start.

Missing/incomplete logs are **never** interpreted as "no access" (capture
incomplete → NOT VERIFIED).

## Access audit

- Existing rollout adapter, transcript adapter, tool-result observability,
  negative evidence, and Delivery/Review dual-session audit designs are not
  rewritten.
- Matching targets:

```text
matching targets = prior contamination inventory          (Class 2)
                 + other-arm current-run dynamic identity sets   (Class 3)
                 + gh / GraphQL 查询结果中的 Issue timeline
                   connected / disconnected metadata（暴露 previous-Arm
                   dynamic identity 时）
```

- Any match → **`BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE`** → fresh
  workspace + fresh Delivery + fresh Review rerun (a prior-benchmark answer
  source is equally invalid).
- **Negative-evidence PASS definition**: capture complete + parser supported +
  audit executed + **0 forbidden matches** (the audit reason string for a PASS
  is "zero forbidden matches"; including inventory matches and
  timeline-metadata matches).
- Delivery and Review are audited independently; each must PASS for the
  result to be valid.
- Matching is deterministic substring/identifier matching on normalized
  lowercased values; PR-number identifiers (bare digits, `#NN`, `gh pr view
  NN`, `/pull/NN`) match only through boundary-checked PR patterns to avoid
  false positives on unrelated digits. Fail-closed bias: any forbidden match
  is reported for human investigation, never silently ignored.

## Non-negotiable semantics

- `capture_complete` / `parser_supported` / `audit_executed` are explicit
  audit inputs; a missing log is never silently treated as "no access".
- Audit verdicts: `PASS` | `NOT VERIFIED` | `BENCHMARK INVALID — BENCHMARK
  INFORMATION LEAKAGE`.
- The audit only matches; it never interprets workflow semantics.
