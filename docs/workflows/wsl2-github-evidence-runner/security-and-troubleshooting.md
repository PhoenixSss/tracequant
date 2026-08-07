# Evidence Runner security and troubleshooting

## Fixed boundaries

The Evidence Runner validates exact argv, current repository entry/origin,
profile schema, object SHA arguments, snapshot IDs, ignored output paths, and
bounded response structure. Current Skill/Runner/tool hashes are recorded in the
result.

## Common outcomes

### Required Checks plan-limit `403`

The result remains `partial` with
`required_checks_configuration = unknown`. Closeout may separately report
`eligible-under-capability-limited-policy` for exact branch cleanup only when all
of that policy's independent gates pass. It never authorizes Merge or other
writes.

### Project or review-thread permission failure

The affected fact is `unknown` and the result is `partial`. Use only a separately
authorized bounded read fallback and report it.

### Authentication, rate limit, network, or service failure

Preserve the classified warning and exit `3`. Do not substitute stale evidence.

### Task/PR linkage, base/head, or remote-ref mismatch

The relevant gate fails. The Runner does not fetch, repair, edit, or reinterpret
the mismatch.

### Large result set

Lists include count and truncation metadata. Truncation makes the result partial;
use the stored result to inspect only the required bounded subset.

### Profile or Runner change

The current file is hashed and used directly. Non-canonical profile/schema or
argv drift fails before GitHub evidence is accepted. Final workflow use should
run from the committed current head so the recorded identity is reproducible.

## Checklist

1. Run from the repository root under `/home`, not `/mnt`.
2. Confirm `origin` is `PhoenixSss/quant-system`.
3. Confirm Task/PR/base/head arguments are current and exact.
4. Confirm the output roots are ignored and writable.
5. Check `gh auth status` only when the active workflow authorizes it; never
   print `gh auth token`.
6. Treat exit `3` as partial and inspect the named gate.
7. Do not add broad direct `gh api`, Git, Python, or shell allow rules.
8. Re-run the fixed profile after capability recovery.
9. Use `recheck` immediately before a stability-sensitive verdict or action.
