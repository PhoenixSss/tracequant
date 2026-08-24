# Command execution policy

## Authority

The active Skill authorizes an operation. This policy selects the execution
context for that already-authorized command; it never expands lifecycle, Git,
GitHub, scope, or review permissions.

Read the optional ignored `.agents/execution-profile.local.toml` when present.
It may route only exact documented Runner invocations. Repository Rules and the
Runner still validate full argv, cwd, repository, profile, and identity.

## Deterministic workflow commands

Normal lifecycle and validation commands are:

```text
uv run --frozen python tools/agent_workflow/lck.py <phase> <operation> <task>
tools/agent_workflow/wsl2_validation_runner.py <named-profile> <fixed-args>
```

Run them from the current repository root on the WSL2 Linux filesystem. Do not
wrap them in `bash -c`, `sh -c`, command substitution, pipelines, redirection,
or a generic shell string.

The local profile may choose:

```text
sandbox-first   try normal execution; elevate only after a classified sandbox failure
elevated-first  use the exact approved Runner argv when normal execution is known to fail
prompt          request approval
```

A route is valid only for the exact LCK/validation contract. It cannot alter
Task/PR IDs, base/head SHAs, repository, output paths, or profile semantics.

## Failure classification

Before retrying, classify the failure:

- `sandbox-denied`: local process, network, or exact ignored output path blocked;
- `credential-isolated`: credentials unavailable only in the current context;
- `network-unavailable`: DNS/proxy/TCP failure;
- `permission-denied`: GitHub or filesystem permission failure;
- `command-failed`: the command ran and returned a real validation/evidence
  failure;
- `contract-invalid`: wrong argv, cwd, repository, profile, schema, or identity.

Only `sandbox-denied` or `credential-isolated` may justify an exact-context
retry. Do not retry a real command failure with broader permissions. Do not use
an equivalent direct command chain as fallback.

## Allowed local writes

Runners may write exact bounded artifacts under:

```text
.agents/evidence.local/
.agents/validation.local/
.workflow.local/lck/              # ignored LCK operation state / preserved Review evidence
$TMPDIR/tracequant-lck-review-*    # operation-owned standalone Review clones only
```

Repository-local roots must be Git ignored. Independent Review may write only its ignored
LCK operation/evidence state; the source tracked tree and source Git metadata remain
read-only. Temporary clones are owned by the Review operation, live only until Review
Complete (or failed/interrupted Prepare cleanup), and MUST NOT register or mutate source
`.git/worktrees`. Creating, sealing, or removing the Review clone is expected to work in
the normal sandbox and is not by itself a reason to elevate the LCK command. A known required write route should
be correct on the first formal call; do not intentionally run a known-failing sandbox
probe before an approved exact route.

## Git and GitHub boundaries

LCK live-state collection and validation do not authorize writes by the Agent.
The active Skill must separately authorize every Git or GitHub mutation; the
LCK lifecycle command is the only formal mechanism allowed to perform the
phase-owned effects.

Always require explicit workflow authorization for:

```text
git fetch / switch / checkout / add / commit / push / branch deletion
gh issue / pr / project writes
worktree creation or removal
```

Never authorize:

```text
gh auth token
force push
--admin
protection bypass
git reset --hard
git clean
arbitrary branch deletion
automatic Merge
manual Issue close by a workflow Skill
```

Stage explicit paths; never use `git add .`. Branch cleanup must follow
`task-closeout` exact identity and safety gates.

## Independent Review

Independent Review uses the current explicitly invoked Review Skill and current
repository Runner. Its safety boundary is a fresh session, strict read-only
behavior, locked PR base/head/effective diff, complete semantic inspection,
independent validation, stability recheck, and maintainer manual Merge.

A local execution profile cannot change reviewed SHAs, findings, severity,
verdict, or the read-only boundary.

## Reporting

Record command identity, execution context, retries, failure classification,
result path/digest, and limitations when abnormal. Never expose credentials or
complete environment values.
