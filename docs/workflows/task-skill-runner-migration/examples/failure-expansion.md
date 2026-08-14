# Bounded Failure, Unknown, and Drift Expansion

| Runner state | Skill response | Prohibited response |
| --- | --- | --- |
| Evidence `partial`/`unknown` | Identify the exact gate and inspect only its normalized/raw reference or one documented read-only fallback | Treat as complete success or rerun all legacy queries |
| Evidence `drift` | Invalidate the readiness/verdict, recollect from the fixed profile, and report changed fields | Reuse the earlier snapshot |
| Validation `fail` | Inspect failed command, exit code, bounded summaries, and referenced log; repair or report | Hide failure behind compact digest or run the whole direct chain without reason |
| Runner unavailable/version mismatch | Block, report versions/integrity, use rollback or narrow fallback policy | Execute arbitrary `gh`, Git, Python, or shell commands as an untracked replacement |
| Rules prefix allows a tailed command | Runner rejects complete argv before side effects | Claim Rules provide exact end-of-command matching |

Successful digest compaction never applies to the evidence needed to diagnose a failure.
