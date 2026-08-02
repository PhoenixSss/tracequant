# Security, Failure Cases, and Troubleshooting

## Security controls

- fixed repository and origin identity;
- fixed profile names and option contracts;
- no arbitrary REST path, GraphQL, `gh`, Git, shell, or filesystem argument;
- tracked-`HEAD` integrity checks before subprocess execution;
- no `git fetch` in the fixed read-only profile;
- current remote refs queried with fixed `git ls-remote --heads` argv;
- normalized bounded snapshots and compact stdout;
- ignored local evidence only;
- sensitive token/header/cookie/private-key patterns redacted;
- GitHub and Git writes remain outside the allow rules.

## Failure and partial cases

### Required Checks plan-limit 403

The snapshot records `plan-limited-403`. Current check runs may still be
available, but the result remains `partial`; it must not be presented as full
required-check configuration evidence.

### ProjectV2 or review-thread permission failure

The unavailable field is marked unknown and the result exits `3`. A Skill may
use a separately authorized read-only fallback, but must report that fallback.

### API rate limit or network failure

Warnings are bounded and redacted. The result exits `3`. Old evidence cannot be
substituted as current evidence.

### Task/PR linkage mismatch

A PR that does not close the expected Task produces a failed gate and exit `4`.

### Remote-ref drift

The runner compares local `origin/main` with current remote `main`, and an open
PR's GitHub head SHA with the remote head branch. A mismatch fails evidence
consistency. The runner does not repair the mismatch or fetch.

### Large changed-file set

Bounded lists carry `count` and `truncated`. Truncation makes the result partial;
the full diff remains outside model-visible output.

### Trusted file changed after approval

Any staged, unstaged, symlinked, untracked, or replaced runner/spec/Rules/shared
Evidence file is rejected before GitHub queries. Commit and review the intended
change, then run again.

### Rules allow a profile prefix plus trailing argv

This is the known Codex prefix-matching boundary, not semantic acceptance. The
runner's argparse contract rejects arbitrary trailing values before evidence
collection. Tests must verify policy behavior and runner behavior separately.

## Troubleshooting checklist

1. Confirm the command is run from the repository root under `/home`, not `/mnt`.
2. Confirm `origin` resolves to `PhoenixSss/quant-system`.
3. Confirm all trusted files are committed and the working/index copies match `HEAD`.
4. Run `gh auth status` outside the runner when the active workflow authorizes it.
5. Check that Project read permission is present when Project fields are needed.
6. Treat exit `3` as partial and inspect the referenced normalized result.
7. Do not "fix" partial evidence by adding broad direct `gh api` or Git allow rules.
8. Re-run the fixed profile after network, permission, or rate-limit recovery.
9. Use `recheck` before a stability-sensitive decision instead of reusing an old snapshot.
