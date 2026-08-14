# Validation Runner maintenance and adoption

## Synchronized contract

A validation-command change may require coordinated updates to:

1. `.github/workflows/ci.yml`;
2. `wsl2_validation_profiles.json`;
3. the Runner canonical command allowlist;
4. CI/profile drift tests;
5. exact-argv and failure-path tests;
6. execpolicy routing tests;
7. Skill and operational documentation.

Incomplete updates must fail rather than silently execute a different command
set.

## Development and final validation

During Runner, profile, Rules, or Skill development, use an appropriate targeted
profile or explicit authorized development checks. Final workflow validation is
run from the current clean committed head and records actual content hashes.
There is no separate control-plane checkout or cross-commit bundle.

Rules changes require a fresh Codex session for live verification. Static
`codex execpolicy check` proves matching behavior but does not prove a running
session reloaded the changed project Rules.

## Profile design

Add a profile only for a stable named contract. Profiles must not expose
arbitrary commands, paths, shell expressions, or free-form pytest arguments.
The readable JSON specification and the independent in-code allowlist are both
required: the specification describes the plan; the allowlist prevents the plan
from becoming an arbitrary-command channel.

## Adoption criteria

The Runner is suitable when commands are stable, repeated, and verbose; success
can be represented by a compact digest; and failure still has command-level
artifacts. It is not a replacement for interactive implementation commands or
semantic review.

Maintain these invariants:

- Rules authorize a small entry; the Runner validates complete argv.
- Targeted evidence is never reported as CI-equivalent evidence.
- Workflow profiles bind results to the current repository head and active
  Skill/Runner hashes.
- Full logs remain ignored and local.
- Failures, truncation, timeout, and partial evidence remain visible.
- Post-merge validation requires synchronized clean main identity.

## Rollback

Rollback must revert consuming Skills, Runner/profile contracts, Rules, tests,
and documentation together. The result must have exactly one complete
validation path; validation must not be skipped or duplicated.
