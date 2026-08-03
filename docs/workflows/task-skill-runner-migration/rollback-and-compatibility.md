# Rollback and Compatibility

## Version contract

Task #85 raises both fixed runners to `1.1.0`, adds workflow phase profiles, and extends `trusted_runner.py` with `evidence-runner` and `validation-runner` bundles. Profile specs, runner versions, Rules, and trusted bundle manifests must agree before execution. Schema/version mismatch fails closed.

## Bootstrap exception

The Task #85 PR cannot be reviewed through a trusted front door that does not exist in its base. Its independent Review therefore uses the predecessor base's trusted raw control plane, explicitly records the migration exception, and validates the new front doors as changed code. This exception expires when Task #85 merges.

## Rollback procedure

Rollback must be atomic across Skills and runner routing:

1. stop the affected lifecycle action;
2. revert the Task #85 Skill, policy, Rules, runner/profile, validator, and documentation commit together;
3. restore the predecessor raw trusted path only as the normal path of the reverted version;
4. run all Skill validators and workflow tests;
5. verify write approvals and manual Merge remain unchanged;
6. record the rollback and do not mix old Skills with new runner profiles.

Do not merely add the old commands beneath the new runner calls. That creates duplicate facts, stale snapshots, and misleading command/Token measurements.

## Compatibility failures

- Missing runner/profile or schema mismatch: block and report version identities.
- Evidence `partial`/`unknown`: retain status and expand only the affected gate.
- Validation failure: inspect the failed command/log reference; do not rerun the complete direct chain by default.
- Trusted bundle failure: treat as control-plane failure, not permission to execute PR-head tooling as trusted.
- Rules not reloaded: start a fresh Codex session and verify execpolicy; do not broaden direct tool allowances.
- Windows path: the fixed runner contract is WSL2 Linux filesystem; do not silently fall back to `/mnt`.
