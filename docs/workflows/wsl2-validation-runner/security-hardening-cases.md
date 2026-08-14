# Validation Runner security cases

## Complete argv

Execpolicy uses prefix matching. A routed prefix may still contain an invalid
tail. The Runner therefore rejects unexpected argument count, option, value,
separator, shell form, and non-canonical command before creating success
evidence.

## Repository and output identity

The entry must be the current repository Runner, invoked from that repository
root under the Linux filesystem. Symlinks, wrong roots, `/mnt` repositories,
and output paths outside ignored `.agents/validation.local/wsl2-runs/` fail
closed.

Result artifacts bind repository head/clean state and current Skill, Runner,
profile, Rules, and workflow-validation content hashes.

## Writable profile protection

The profile JSON is not an arbitrary command source. Every command ID and argv
must match an in-code canonical allowlist; workflow profiles have fixed phase,
base-SHA, validator, and precondition contracts. Modified non-canonical content
fails before subprocess execution.

## Process tree cleanup

Every child starts in a new POSIX session. Timeout and interruption signal the
complete process group, wait for a bounded grace period, and escalate when
necessary. Result-write failure is a Runner failure, not a pass.

## Sensitive output

Complete stdout/stderr stays in ignored local artifacts and is redacted for
common credentials, authorization headers, cookies, private keys, and secrets.
The terminal receives only a bounded digest on success and bounded diagnostics
on failure.

## Governance changes

When a PR modifies a Skill, Runner, Rules, profile, or workflow, independent
Review directly inspects the changed behavior, tests, permission routing, and
failure paths. Runner success is evidence, not proof of semantic correctness or
permission to merge.
