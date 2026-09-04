# Task #194 baseline replay protocol

Protocol ID: `tracequant-production-equivalent-review`

Protocol version: `v1`

The protocol has four semantic stages:

```text
Inspect → Reason → Judge → Report
```

For each fixture, the runner creates a fresh Run, materializes the exact frozen
head from its Git bundle, verifies a clean detached Subject, and invokes the
protocol from the current Harness checkout. Detection Subjects are read-only.
The Harness is never replaced by historical Subject code and the Subject is not
added to `PYTHONPATH`.

Each fixture is replayed at least twice. A replay is reproducible only when its
fixture identity, semantic output, model/config, token accounting, and verdict
match while Run IDs remain distinct and Run workspaces are cleaned after every
execution. Wall-clock time is recorded as an observation and is not used as a
semantic equality key.

Candidate findings use public semantic fingerprints (`path`, `symbol`, and
`category`). The scorer maps those fingerprints to scorer-only known findings
after Review. A stable fixture's unmatched candidate finding is explicitly
`needs-adjudication`; it is never automatically labeled a false positive.

The baseline is intentionally limited to the current repository's deterministic
Review adapter. It does not change the production LCK Review gate or implement a
new Review vNext semantic pipeline.
