# Validation Runner Maintenance and Adoption

## Maintenance contract

The validation runner intentionally has several synchronized control points.
Changing a validation command may require updates to:

1. `.github/workflows/ci.yml`;
2. `tools/agent_workflow/wsl2_validation_profiles.json`;
3. the runner's canonical command allowlist;
4. CI/profile drift tests;
5. real execpolicy positive, negative, and prefix-boundary tests when the entry
   or profile changes;
6. runner exact-argv and trailing-argument rejection tests;
7. usage, security, and publication documentation.

The drift and integrity tests are designed to make an incomplete update fail
rather than silently execute a different validation set.

## Known maintenance costs

### Trusted files must be committed

The runner compares trusted files with their tracked `HEAD` blobs before
execution. During intentional development of the runner, profile specification,
or Rules, the fixed runner will fail closed until the change is committed.

Development validation before that commit must use explicit project commands.
After commit, the fixed runner must be exercised again.

### Rules changes need a fresh Codex session

Static `codex execpolicy check` validates matching behavior, but does not prove
that a running Codex session reloaded the new project Rules. A Rules change
requires a new or restarted Codex session and a narrowly scoped live probe.

Before adopting Codex Rules, first confirm the matching semantics of the active
policy language. The Task #83 rules use prefix matching, not exact command
matching. Do not treat a prefix rule as a complete argv allowlist.

### New profiles are governance changes

A new profile is appropriate only when it represents a stable, named validation
contract. It must not be used to expose arbitrary pytest arguments, file paths,
shell commands, or one-off debugging commands.

### Canonical duplication is deliberate

The profile specification is readable and versioned. The in-runner canonical
allowlist is an independent semantic guard. This duplication is deliberate:
the specification describes the plan, while the runner prevents a modified plan
from becoming an arbitrary-command channel.

## Adoption guidance

This design is suitable when:

- validation commands are stable and repeated;
- model-visible command count and output volume matter;
- successful validation output is much larger than the decision-relevant
  digest;
- the environment can enforce a fixed repository identity;
- failures still require command-level evidence.

It is less suitable when:

- users need arbitrary interactive command composition;
- the command set changes on nearly every run;
- trusted files cannot be tied to reviewed repository content;
- the platform cannot reliably terminate child process trees.

## Reusable principles

- Rules authorize a small entry point; they do not replace input validation.
- The runner owns profile semantics and fail-closed behavior.
- Rules and runner behavior must be tested separately.
- New profiles must update both the execpolicy matrix and runner exact-argv
  rejection tests.
- Reviewers should check composed Rules/Runner behavior, not only Rules text.
- Full logs remain external and ignored; the model receives a compact digest.
- Every real subcommand, exit code, duration, and truncation state remains
  auditable.
- Permission reduction must not hide failures or weaken evidence.
- Static policy checks and live Guardian observations are separate evidence
  classes.
- `targeted` evidence must never be presented as complete CI-equivalent
  validation.
- A post-merge profile must enforce branch, cleanliness, and remote-identity
  preconditions before validation.

## Rollback

Before later Skills depend on the runner, rollback consists of:

1. stop invoking the fixed runner;
2. remove or disable the project Rules entry;
3. continue using the existing explicit validation commands;
4. retain prior evidence and mark the runner adoption as rolled back.

After Skills adopt the runner, rollback must be coordinated with the consuming
Skill change so that validation is not accidentally skipped.
