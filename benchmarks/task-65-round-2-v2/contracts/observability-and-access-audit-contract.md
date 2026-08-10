# Observability & Access Audit Contract — task-65-round-2-v2

Authoritative source: Issue #125 "Observability / Access Audit" section.
Governs capture, normalization, and negative-evidence access auditing for the
Task #65 v2 benchmark. Tooling: `tooling/observability_preflight.py`,
`tooling/codex_rollout_adapter.py`, `tooling/claude_transcript_adapter.py`,
`tooling/access_audit.py`.

## Observability

### Adapters

Two benchmark-only adapters normalize observed records into mechanical
streams; `target` covers git args, gh args, Read paths, Grep paths/pattern
scopes, Glob scopes, shell commands, and evidence/rollout paths.

- Codex rollout adapter (`tooling/codex_rollout_adapter.py`): parses
  `custom_tool_call` / `custom_tool_call_output` records into access events.
- Claude transcript adapter (`tooling/claude_transcript_adapter.py`): parses
  the current tested Claude runtime transcript
  (Claude Code VSCode 2.1.226, `~/.claude/projects/**` JSONL) into access
  events and context inputs.

Adapters only extract/normalize — they never interpret workflow semantics;
unknown record format → **FAIL CLOSED → NOT VERIFIED**. Adapters must ship
tests with de-identified sample record fixtures.

### Claude transcript record taxonomy

Every top-level record type is classified explicitly
(schema/contract-backed in `claude_transcript_adapter.py`); there is no
"not a tool_use, skip it" catch-all:

| Class | Record types | Behavior |
|---|---|---|
| A. ACCESS_BEARING | `assistant`, `user` | `message.content` items `tool_use` / `tool_result` → canonical access events `arm_id / session_id / timestamp / tool / operation / target / raw_event_reference` |
| B. INPUT_CONTEXT_BEARING | `attachment` (sub-types `file`, `agent_listing_delta`, `skill_listing`, `todo_reminder`), `summary`, `last-prompt`, user message text (`user-prompt`, `isCompactSummary` continuation summary) | extracted as context inputs (`context-input.schema.json`), audited with the same Class 2 / Class 3 forbidden identifiers; never silently skipped |
| C. KNOWN_NON_ACCESS_METADATA | `queue-operation`, `ai-title`, `file-history-delta`, `file-history-snapshot`, `system`, `meta`, `isMeta`, `pr-link`, `mode` | explicit allowlist with structural + semantic validation (e.g. `queue-operation.operation` ∈ {enqueue, dequeue}; `system.subtype` ∈ {api_error, compact_boundary}; file-history backup entries may only carry registry keys `backupFileName`/`version`/`backupTime`/`realParentDir` and never content payloads); violation → fail closed |
| D. UNKNOWN | anything else | **FAIL CLOSED / NOT VERIFIED** |

Evidence basis for C: on the current tested runtime, file-history
snapshot/delta records are the file-history feature's backup registry (paths +
backup metadata), never file content; `system` records carry harness
error/compaction markers without `message`; the remaining metadata types
carry UI/queue/lifecycle data only. Any record whose structure could carry
content or access fails closed instead of being classified.

### Session identity model

Transcript records carry `sessionId`, but the resolved session identity is an
**explicit adapter input** from the observability preflight. Rules:

- `parse_transcript` / `parse_transcript_file` require a non-empty
  `session_id`; every normalized event and context input is stamped with it
  (`no empty session_id`).
- The transcript path must mechanically match the session identity: the
  Claude transcript basename stem equals the session id
  (`~/.claude/projects/<project>/<session-id>.jsonl`); mismatch → fail closed
  (`verify_session_path_match`).
- A record carrying a `sessionId` conflicting with the injected identity →
  fail closed.
- No default value, no silent fallback inference.

### Observability preflight (six checks, before Arm start)

1. **session identity resolvable** (for `claude_transcript` sources this
   includes the mechanical transcript-path ↔ session-identity match);
2. **transcript / rollout source locatable**;
3. **capture active before formal Agent work**;
4. **archive destination isolated for this Arm/session**;
5. **parser supports the current observed record format** — supported
   formats: `codex:custom_tool_call`, `codex:custom_tool_call_output`, and
   the full `claude:*` taxonomy of the current tested runtime;
6. **controlled test tool call appears in capture and can be normalized**.

Verdicts: `OBSERVABILITY VERIFIED` when all six checks pass;
**`BENCHMARK OBSERVABILITY NOT VERIFIED`** on any failure → the formal
Delivery must not start.

Missing/incomplete logs are **never** interpreted as "no access" (capture
incomplete → NOT VERIFIED).

## Access audit

- The audit matches **access events and context inputs**; context inputs
  (attachment/summary/prompt content that entered the session without a tool
  call) are matched with the same forbidden identifiers, so Class 2 / Class 3
  identities are detected even when no tool call touched them.  The canonical
  Class 1/2/3 contract is unchanged.
- Matching targets:

```text
matching targets = prior contamination inventory          (Class 2)
                 + other-arm current-run dynamic identity sets   (Class 3)
                 + gh / GraphQL 查询结果中的 Issue timeline
                   connected / disconnected metadata（暴露 previous-Arm
                   dynamic identity 时）
```

- Forbidden identifiers are the inventory's concrete `identifier` /
  `path` / `ref` / `pr` / `branch` / `commit` values; category labels
  (`kind` values such as `commit` / `path` / `branch`) are **not**
  identifiers and never match.
- Any match → **`BENCHMARK INVALID — BENCHMARK INFORMATION LEAKAGE`** → fresh
  workspace + fresh Delivery + fresh Review rerun (a prior-benchmark answer
  source is equally invalid).
- **Negative-evidence PASS definition**: capture complete + parser supported +
  audit executed + **0 forbidden matches** (access or context; the audit
  reason string for a PASS is "zero forbidden matches").
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
