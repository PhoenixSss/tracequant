# Task #83 Security Hardening Cases

This document records security-relevant cases discovered while preparing the
WSL2 validation runner for independent review. These were controlled artifact
review findings, not production incidents.

## Case 1: Mutable profile specification could become an arbitrary command launcher

### Trigger

The project Rules allowed the fixed entry:

```text
tools/agent_workflow/wsl2_validation_runner.py targeted
```

The pre-hardening runner then loaded command `argv` values from a writable JSON
profile specification.

### Pre-fix behavior

Changing the `targeted` command in the profile specification to an unrelated
command could cause the otherwise allowed runner entry to execute that command.
The runner recorded hashes after execution, but did not fail closed before the
first subcommand.

### Root cause

The authorization and semantic boundaries were incomplete:

1. Rules authorized a stable path and profile prefix.
2. The runner trusted mutable profile content.
3. Runtime hashing was observational rather than an execution gate.

Prefix-based Rules cannot establish the semantic safety of a mutable file that
the allowed executable consumes.

### Security impact

A writable profile file could turn a narrowly allowed runner invocation into an
arbitrary command execution path. This violated the Task requirement that the
runner accept only versioned, fixed commands and that trusted-file changes be
detected before execution.

### Fix

Runner version `1.0.1` now:

- verifies the runner, profile specification, and project Rules against their
  tracked `HEAD` blobs before creating a run directory or executing a command;
- rejects staged, unstaged, symlinked, untracked, or replaced trusted files;
- loads the profile from the bytes that were actually verified;
- validates every command ID and `argv` against an in-runner canonical
  allowlist;
- rejects invalid and duplicate command IDs before execution.

### Regression evidence

`tests/tools/test_wsl2_validation_runner.py` covers:

- modified runner rejection;
- modified profile rejection;
- committed but non-canonical command rejection;
- invalid path-like command ID rejection;
- duplicate command ID rejection;
- CI/profile drift rejection.

### Residual boundary

A legitimate runner, profile, or Rules change must be committed and reviewed
before the fixed runner can execute again. This is intentional fail-closed
maintenance cost, not a defect.

### Lesson

Rules authorize the entry. The runner must independently enforce argument,
configuration, and command semantics. Recording a hash after execution is not
an integrity control.

## Case 2: Timeout terminated the wrapper but could leave descendants alive

### Trigger

Validation commands are launched through wrappers such as:

```text
uv -> pytest / Ruff / mypy
```

### Pre-fix behavior

The original timeout path killed the direct child process and then waited for
its pipes. A descendant could remain alive, keep stdout/stderr open, and prevent
the runner from completing promptly.

### Root cause

Process lifetime was managed at the direct-process level rather than the
process-tree level.

### Reliability and security impact

A timed-out validation could:

- continue consuming resources;
- retain output pipes;
- delay or prevent structured result creation;
- leave the repository in an ambiguous validation state.

### Fix

Runner version `1.0.1` now:

- starts each validation command in a new POSIX session;
- targets the complete process group on timeout or interruption;
- sends a bounded graceful signal first;
- escalates to `SIGKILL` when the process group does not exit;
- preserves explicit timeout/interruption status in the structured result.

### Regression evidence

`test_timeout_and_sigint_are_explicit` creates a descendant process and verifies
that it does not survive process-group cleanup. It also verifies that
interruption returns an explicit non-success result.

### Residual boundary

The process-group implementation is POSIX-specific. The supported execution
environment for this runner is WSL2 Linux; other platforms require a separately
reviewed process-tree strategy.

### Lesson

Timeout handling is not complete unless the entire command tree is bounded and
the structured evidence path remains available.

## Case 3: Live Rules evidence predates the final runner hardening

The recorded live Rules probe executed runner version `1.0.0`. The final
review candidate uses runner version `1.0.1`.

The Rules file and allowed entry did not change between those versions:

```text
.codex/rules/quant-system-wsl-validation.rules
SHA-256: f593c3959cc94dc2621a97e468617bb3e98a2dd0332c6ca7ed7ecc9bc1522186
```

Therefore the existing live probe supports this narrow claim:

> In the observed new Codex session, the fixed `targeted` runner entry matched
> the project Rules and executed directly without Guardian or approval.

It does **not** independently prove every runtime behavior of runner `1.0.1`.
The new integrity and process-tree semantics are supported by automated tests
and static review. A second live probe may be recorded later, but the original
observation must not be overwritten.
