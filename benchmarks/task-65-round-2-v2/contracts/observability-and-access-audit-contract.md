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
| B. INPUT_CONTEXT_BEARING | `attachment` (sub-types `file`, `agent_listing_delta`, `skill_listing`, `todo_reminder`, `command_permissions`), `summary`, `last-prompt`, user message text (`user-prompt`, `isCompactSummary` continuation summary) | extracted as context inputs (`context-input.schema.json`), audited with the same Class 2 / Class 3 forbidden identifiers; never silently skipped |

`attachment:command_permissions` carries the tool-permission context injected
into the session (`allowedTools` string list).  Contract: `type` must be
exactly `command_permissions`; `allowedTools` must exist and be a list of
strings; the full list enters the context-input contamination audit
untruncated; an empty list is legal and produces zero contamination matches;
missing key, non-list, or non-string element → fail closed.  Rationale: the
injected permission/tool context may carry strings matchable to Class 2 /
Class 3 identities.
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
5. **parser supports the current observed record format** — decided by the
   REAL parser machinery, not an allowlist: the declared
   `parser_record_formats` must be adapter-supported (`codex:custom_tool_call`,
   `codex:custom_tool_call_output`, and the full `claude:*` taxonomy of the
   current tested runtime) AND the formal adapter must actually consume every
   record of the observed source — top-level record types, sub-types,
   content-item types, structural invariants, session identity, and metadata
   discriminators.  Any record the full parser rejects → check 5 FAIL;
6. **controlled test tool call appears in capture and can be normalized**.

Verdicts: `OBSERVABILITY VERIFIED` when all six checks pass;
**`BENCHMARK OBSERVABILITY NOT VERIFIED`** on any failure → the formal
Delivery must not start.

Missing/incomplete logs are **never** interpreted as "no access" (capture
incomplete → NOT VERIFIED).

## Access audit

- The audit input contract is the canonical schema document
  `schemas/contamination-inventory.schema.json` (`{"protocol_identity": ...,
  "entries": [...]}`).  API and CLI share the single
  `load_inventory_entries` loader: the document is schema-validated and its
  `entries` are extracted mechanically.  A bare JSON array or any other shape
  is an unsupported implicit format and fails closed — no shape guessing, no
  second format.
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

### Contamination identity semantics

Every match carries `identity_classes` (`PRIOR_BENCHMARK_CLASS_2` /
`OTHER_ARM_CURRENT_RUN_CLASS_3`):

- **(A) CURRENT_ARM_OWN_EVIDENCE → allowed.** The audit takes an optional
  `current_run_identity` input `{arm_id, session_id, own_evidence_paths}`
  (validated fail closed; arm id ∈ {A,B,C,D}; non-empty session id; own
  paths under a conductor-local `.agents/evidence.local` /
  `.agents/validation.local` / `.agents/benchmark-fixtures.local` root). A
  Class 2 identifier occurrence whose span overlaps a boundary-checked
  occurrence of one of the current run's own paths is the current run's own
  freshly written evidence: exempted and recorded in
  `own_evidence_exemptions` / `exemption_count` (the report echoes the
  validated `current_run_identity`). The exemption is **never silent
  ignoring**: it is a span-overlap mechanism scoped to the current run's
  declared own paths, and it applies **only to Class 2 (inventory)
  identifiers** — other-arm current-run identities (Class 3) are never a
  current arm's own artifact.
- **(B) PRIOR_BENCHMARK / HISTORICAL_ANSWER_BEARING → Class 2 forbidden.**
- **(C) OTHER_ARM_CURRENT_RUN → Class 3 forbidden** (cross-arm dynamic
  identity sets + timeline-metadata-connected identities).

**The generic evidence/validation roots (`.agents/evidence.local`,
`.agents/validation.local`) are NOT forbidden identifiers.** The inventory
carries only specific prior-run artifact identities (artifact provenance /
specific identity / arm / session / path identity, e.g. prior-run namespaces
and evidence files under those roots); a bare root mention in a target never
matches. The audit never ignores an evidence/validation root wholesale.

Fail closed: an inventory entry whose locations carry **no** concrete
identifier (nothing matchable → would silently weaken the audit), or a
malformed / missing-key / non-conductor-local-path `current_run_identity`,
aborts the audit (`BenchmarkError`, CLI exit 1).

- Any non-exempt match → **`BENCHMARK INVALID — BENCHMARK INFORMATION
  LEAKAGE`** → fresh workspace + fresh Delivery + fresh Review rerun (a
  prior-benchmark answer source is equally invalid).
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
