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

Known heavyweight LCK operations (`delivery complete`, `review prepare`, and
formal workflow validation) use a fixed 30-second wait window for the first
wait and every subsequent still-running poll. A process that exits earlier is
returned immediately; the 30-second value is a maximum wait window, not a
minimum runtime. Adaptive polling intervals are not part of the workflow
contract.

The local profile may choose:

```text
sandbox-first   try normal execution; elevate only after a classified sandbox failure
elevated-first  use the exact approved Runner argv when normal execution is known to fail
prompt          request approval
```

A route is valid only for the exact LCK/validation contract. It cannot alter
Task/PR IDs, base/head SHAs, repository, output paths, or profile semantics.

## Authoritative LCK route contract

The versioned execution-profile example contains one exact `uv run --frozen
python tools/agent_workflow/lck.py ...` rule for every supported LCK operation.
The route classification is deterministic and is resolved before the command
starts; an Agent must not probe the normal sandbox first when the matching rule
is `elevated-first`.

| LCK invocation | Route | Why |
| --- | --- | --- |
| `status` | `sandbox-first` | Resolves live facts without changing repository metadata |
| `delivery prepare` | `elevated-first` | May create or switch the Task branch |
| `delivery complete` | `elevated-first` | Commits, pushes, and performs the authorized PR/Project effects |
| `review prepare` | `sandbox-first` | Keeps the source repository read-only; the temporary clone is operation-owned |
| `review complete` | `sandbox-first` | Writes only ignored LCK continuity/evidence state in the source repository |
| `remediation prepare` | `elevated-first` | May restore or switch the Task branch |
| `remediation no-change` | `sandbox-first` | Writes only the ignored no-change receipt |
| `remediation complete` | `elevated-first` | Reuses the authorized commit, push, and existing-PR effects |
| `merge preflight` / `merge-preflight` | `sandbox-first` | Read-only merge gate; it never merges |
| `closeout` | `elevated-first` | Performs the authorized main, lifecycle metadata, and exact-branch effects |

`elevated-first` is reserved for operations whose already-authorized LCK path
can write Git metadata or GitHub lifecycle state. The profile must not contain
a generic `uv`, `python`, `git`, or `gh` write rule. Explicit read-only rules
may remain `sandbox-first`, including the source-repository boundary of
Independent Review. This table chooses how an active Skill-authorized LCK
command runs; it never grants lifecycle, GitHub, commit, push, merge, or
cleanup permission.

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

Mutable artifact ownership is explicit; these roots are not interchangeable fallbacks:

```text
.agents/evidence.local/            # legacy/non-LCK Evidence Runner output
.agents/validation.local/          # Validation Runner output in a writable execution workspace
.workflow.local/lck/               # source-repository LCK runtime state and durable Review evidence
$TMPDIR/tracequant-lck-review-*    # operation-owned standalone Review clones only
```

Repository-local roots must be Git ignored. During Independent Review, the **source
repository** may write only under `.workflow.local/lck/`; it MUST NOT write source
`.agents/evidence.local/`, source `.agents/validation.local/`, the tracked tree, or source
Git metadata. Formal Review validation may create `.agents/validation.local/` **inside the
standalone temporary clone** because that directory is part of the disposable validation
workspace; evidence that must survive clone deletion is copied to
`.workflow.local/lck/review-validation/` in the source repository before cleanup.

Temporary clones are owned by the Review operation, live only until Review Complete (or
failed/interrupted Prepare cleanup), and MUST NOT register or mutate source
`.git/worktrees`. Creating, sealing, or removing the Review clone is expected to work in
the normal sandbox and is not by itself a reason to elevate the LCK command. A known
required write route should be correct on the first formal call; do not intentionally run
a known-failing sandbox probe before an approved exact route.

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
