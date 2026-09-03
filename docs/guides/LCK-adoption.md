# Manual LCK adoption and distribution boundaries

This guide explains how an external open-source repository can manually study
and adopt the Local Control Kernel (LCK) from TraceQuant during its current
initial and actively evolving phase. It describes the boundaries of the
available material; it is not an installation guide or a promise of
portability.

## What LCK is

LCK is an engineering capability developed within TraceQuant to make
Agent-assisted repository delivery auditable, deterministic, and human
controlled. It separates semantic work from deterministic lifecycle mechanics:

- a human maintainer owns intent, ambiguity decisions, and the final merge;
- an Implementation Agent understands and changes the scoped work item;
- a fresh Review Agent independently inspects and judges the candidate;
- LCK resolves live Git/GitHub identity, applies lifecycle gates, runs formal
  validation, performs bounded lifecycle effects, and records evidence;
- Git and GitHub remain the current-state authority.

LCK is invoked on demand. It is not a daemon, a standalone product, a general
Agent platform, a trading module, or a risk authority. TraceQuant itself is
still a Research MVP foundation: it does not currently provide exchange data
ingestion, a backtester, strategies, model training, order execution, risk
decisions, Demo, or Live trading.

## Adoption paths and current availability

There are two manual paths. They have different availability and maintenance
implications:

| Path | Current status | What it means |
| --- | --- | --- |
| Repository copy | Available for manual evaluation and adaptation | Inspect a chosen TraceQuant revision, copy the coherent LCK source and workflow material, then adapt every repository-specific integration point. |
| Versioned release archive | Available: [`lck-v0.1.0-preview.2`](https://github.com/PhoenixSss/tracequant/releases/tag/lck-v0.1.0-preview.2) | Use the corrected, manifest-backed preview for manual evaluation and adaptation. The release record contains the exact source commit, manifest, archive digest, compatibility information, and validation summary. |

The versioned path is a supported LCK source snapshot, not a generic GitHub
source archive for an arbitrary commit or tag. `preview.1` remains immutable
and is clearly superseded; do not replace its tag or assets. Use the exact
`preview.2` release identity, record its source commit and archive digest,
extract that snapshot into the adopting repository, and follow its
compatibility and upgrade notes.

## Path A: repository-copy adoption

This path starts by cloning or inspecting the
[TraceQuant repository](https://github.com/PhoenixSss/tracequant) and selecting
one coherent revision. Record the repository URL, commit SHA (or other
immutable revision identity), and the date of inspection. The implementation
is evolving, so do not combine files from unrelated revisions.

At the current TraceQuant revision, the LCK implementation is a coherent
source tree rather than a separately installable package. The stable CLI facade
is `tools/agent_workflow/lck.py`; its implementation is under
`tools/agent_workflow/lck_core/`. The current generic four-profile kernel also
depends on the adjacent source modules and validation inputs below:

```text
tools/agent_workflow/
  lck.py
  lck_core/
  bug_policy.py
  critical_outcome.py
  documentation_policy.py
  issue_form_contract.py
  markdown_sections.py
  pr_resolve.py
  project_status.py
  research_policy.py
  workflow_common.py
  workflow_validation.py
  wsl2_validation_profiles.json
  wsl2_validation_runner.py
.codex/rules/tracequant-wsl-validation.rules
```

Copy this set as a source snapshot and inspect its imports and validation
identity paths before deleting or substituting anything. In particular,
copying only `lck.py` is insufficient. The LCK implementation map in
[`tools/agent_workflow/lck_core/README.md`](../../tools/agent_workflow/lck_core/README.md)
is the starting point for understanding each responsibility-owned module.
The current snapshot is built around Python 3.13 and uv 0.12.1, and its Issue
form parser uses PyYAML; these are implementation dependencies to verify rather
than universal LCK requirements.

The source tree is only one part of the adoption. Copy and adapt the relevant
workflow contract as a coherent set:

```text
AGENTS.md
.github/ISSUE_TEMPLATE/documentation.yml
.github/workflows/ci.yml
.agents/policies/command-execution.md
.agents/policies/workflow-evidence.md
.agents/skills/task-delivery-runner/SKILL.md
.agents/skills/task-pr-review-runner/SKILL.md
.agents/skills/task-closeout/SKILL.md
.agents/skills/feature-completion-audit/SKILL.md
.claude/skills/task-delivery-runner/SKILL.md       # when Claude is supported
.claude/skills/task-pr-review-runner/SKILL.md
.claude/skills/task-closeout/SKILL.md
.claude/skills/feature-completion-audit/SKILL.md
docs/development/issue-workflow.md
docs/development/pr-review.md
docs/workflows/agent-skills.md
```

The list above is an adoption starting point, not a frozen package manifest.
The adopting repository must inspect transitive imports, active Skills, CI
commands, templates, and the selected revision's release notes or source
history. Historical Evidence Runner material is audit provenance, not an
alternate LCK lifecycle entry point.

## Path B: versioned release-archive adoption

The current supported archive is the pre-release
[`lck-v0.1.0-preview.2`](https://github.com/PhoenixSss/tracequant/releases/tag/lck-v0.1.0-preview.2).
Its archive is a fixed source snapshot, not a floating view of `main`. Use the
following manual sequence:

1. Select `preview.2` rather than an unversioned branch or arbitrary commit.
2. Download its named archive and metadata assets; verify and record the tag,
   exact source commit, manifest digest, archive digest, and pre-release status.
3. Extract the archive into a controlled location in the adopting repository.
4. Read the archive's compatibility and upgrade guidance before replacing an
   earlier snapshot.
5. Adapt the repository integration points listed in the matrix below and run
   non-destructive validation before any lifecycle write.

The archive does not authorize automatic installation, and it does not turn
TraceQuant's current source snapshot into a standalone product. A later
correction or changed digest requires a new immutable release identity.

## Reuse and adaptation matrix

The following matrix distinguishes reusable ideas from material that needs
adaptation and conventions that remain TraceQuant-specific.

| Area | Directly reusable | Copyable but requires repository adaptation | TraceQuant-specific boundary |
| --- | --- | --- | --- |
| Responsibility split | Human intent/merge, Agent semantic work, fresh Review Agent, LCK deterministic control, Git/GitHub current facts | Map the roles to the adopting team's maintainers and supported providers | TraceQuant's maintainer roles and issue ownership are not transferred automatically |
| LCK concepts | On-demand operations, fresh phase boundaries, operation snapshots, fail-closed ambiguity handling, bounded effects, audit-not-authority, manual merge | Preserve the invariants while matching the adopting repository's lifecycle and risk model | The concepts do not prove that an adopting repository has implemented them correctly |
| LCK CLI and kernel | `tools/agent_workflow/lck.py` and `tools/agent_workflow/lck_core/` as a coherent source snapshot | Repository root, default branch, GitHub identity, Issue/PR resolution, permissions, paths, and supported leaf profiles | `PhoenixSss/tracequant` identity and its current four-profile registry |
| Profile policies and forms | Generic typed-profile pattern and shared Issue-form parsing | Canonical `type:*` labels, form fields, blocker rules, candidate paths, and any profile policy | TraceQuant's `type:task`, `type:bug`, `type:documentation`, `type:research` labels and policy semantics |
| Workflow guidance | Shared lifecycle and Review principles in the current development docs | Rewrite examples, paths, maintainer instructions, and provider adapters for the adopting repository | TraceQuant's exact `AGENTS.md`, Project lifecycle, branch namespaces, and Issue relationships |
| Agent Skills | Semantic Skill structure and the rule that Skills invoke LCK rather than own lifecycle state | Copy only the supported provider's current Skills, update discovery paths and prompts, and validate them | `.agents/skills/`, `.claude/skills/`, and their TraceQuant-specific provider/sandbox instructions |
| Validation | Bounded validation, exact-head identity, static CI job policy, and redacted local evidence principles | Toolchain commands, language checks, CI job names, base-commit policy, permissions, and ignored output roots | `.github/workflows/ci.yml`, `wsl2_validation_profiles.json`, and `.codex/rules/tracequant-wsl-validation.rules` as currently named |
| GitHub lifecycle | Issue → Delivery → human stop → Independent Review → manual merge → Closeout model | Issue state labels, Project fields/statuses, PR policy, branch namespace, authentication, and repository permissions | TraceQuant's `codex:ready`, Project statuses, `documentation/<Issue>-<slug>` naming, and current GitHub metadata |
| Local state and evidence | Keep runtime/evidence local, bounded, ignored, and separate from authority | Configure and verify equivalent ignored paths; never stage local state | `.workflow.local/lck/`, `.agents/validation.local/`, and `.agents/evidence.local/` ownership rules |
| Product and architecture docs | The practice of separating current implementation from design intent and future work | Write an equivalent baseline for the adopting repository | TraceQuant's quantitative-trading roadmap, `src/tracequant/`, and future `apps/`, `packages/`, and `deploy/` boundaries |

The matrix is deliberately not a portability claim. A copied file is not
correctly adopted until its repository-specific assumptions are identified,
adapted, and validated.

## Minimum manual adoption sequence

Use a disposable branch, test repository, or a small documentation Issue for
the first exercise. Keep production repositories and credentials out of the
first trial.

### 1. Select and record the source

Choose either a coherent TraceQuant commit for repository-copy adoption or a
published supported release for archive adoption. Record the source identity,
date, included LCK components, and any known compatibility limitations.

### 2. Inspect the adopting repository

Before copying files, decide which of the current LCK contracts the repository
can actually support:

- GitHub Issues, pull requests, labels, and the required Project lifecycle;
- a deterministic default branch and one profile-owned Issue branch;
- the GitHub CLI and Git permissions needed by the bounded effects;
- the language/toolchain commands used by formal validation;
- one or more supported semantic Agent providers;
- ignored local directories for LCK runtime and validation evidence.

If an assumption cannot be mapped safely, stop and adapt the contract before
running lifecycle effects. Do not silently substitute a guessed branch, PR,
SHA, check, or Project field.

### 3. Copy and adapt the integration points

Copy the coherent source and guidance sets described above. Then adapt, at
minimum:

- repository owner/name, default branch, and remote identity;
- Issue form fields, canonical type labels, readiness/blocker semantics, and
  Project status fields;
- CI workflow and its statically named required jobs;
- validation profiles, supported language checks, Python/uv or equivalent
  launcher assumptions, and exact ignored output paths;
- `AGENTS.md`, provider Skills, and links to the adopting repository's own
  technical baseline and structure documentation.

Keep the LCK source's fail-closed behavior and bounded effects while making
these mappings explicit. Do not solve adaptation by adding an unbounded shell,
arbitrary refspec, arbitrary GitHub write, or hidden lifecycle controller.

### 4. Run non-destructive validation

From the adopting repository root, first verify the launcher required by the
selected snapshot:

```bash
command -v uv
uv --version
uv run --frozen python --version
```

Then run the adapted read-only status operation and the repository's ordinary
static/documentation checks. In the current TraceQuant source shape, the LCK
status entry is:

```bash
uv run --frozen python tools/agent_workflow/lck.py status <ISSUE_NUMBER>
```

Confirm that the result resolves one unambiguous Issue/profile and that local
runtime output remains under ignored paths. A status result is diagnostic
information; it does not authorize a lifecycle write.

### 5. Exercise one small lifecycle

For a tiny documentation-only test Issue, invoke the current LCK entry points
with only the Issue number. LCK must resolve the branch, SHA, PR, and lifecycle
facts itself:

```bash
uv run --frozen python tools/agent_workflow/lck.py delivery prepare <ISSUE_NUMBER>

# In the prepared workspace, make one small documentation change and run the
# adapted targeted checks.

uv run --frozen python tools/agent_workflow/lck.py delivery complete <ISSUE_NUMBER> \
  --commit-message "docs: exercise manual LCK adoption" \
  --summary "Exercise the adapted documentation delivery path." \
  --risks "Disposable adoption exercise; no production behavior changed."
```

The expected initial terminal boundary is `READY_FOR_REVIEW`. Stop there for
the first exercise unless a maintainer explicitly intends to continue. If the
exercise continues, it must use a fresh Review invocation and retain the human
merge boundary:

```text
delivery → HUMAN STOP → independent review → merge preflight →
maintainer manual Squash Merge → closeout
```

Do not pass branch names, SHAs, PR numbers, remotes, or refspecs as authority
to LCK. A failed Review stops and requires explicit maintainer-directed
remediation; a changed head requires a fresh Review.

## Roles and provider neutrality

Codex is a primary use case for LCK, but LCK is provider-neutral by design.
The current workflow documentation also supports Claude as an equivalent
semantic provider. Provider differences may affect prompt format, tools, or
sandbox setup; they must not decide the actionable branch, SHA, PR, push
destination, lifecycle state, merge state, or recovery state.

| Role | Responsibility in an adopting repository |
| --- | --- |
| Human maintainer | Define intent, resolve real ambiguity, approve remediation when needed, and perform the manual merge. |
| Implementation Agent | Read the current Issue, implement only its scope, diagnose failures, and explain the change. |
| Fresh Review Agent | Independently inspect the exact candidate, judge requirements and risks, and report PASS or FAIL without modifying it. |
| Semantic Skill | Guide the provider's semantic work and invoke LCK; it is not a lifecycle state machine. |
| LCK | Reacquire current mechanical facts at each operation boundary, validate, perform bounded authorized effects, and record evidence. |
| Adopting repository | Own its Issue contract, labels, Project, CI, branch rules, ignored local state, documentation, and adaptation decisions. |

## Current implementation, active evolution, and future portability

Keep these statuses separate when describing LCK:

### Current implementation

In the current TraceQuant repository, LCK is an on-demand local CLI with a
shared kernel and four typed leaf profiles. The live lifecycle has separate
Delivery, Independent Review, Remediation, Merge Preflight, and Closeout
operations. Documentation leaves use a repository-owned safe-change policy;
they do not require a fabricated Critical Outcome.

The [LCK Overview](LCK-overview.md),
[current Issue workflow](../development/issue-workflow.md),
[Independent Review contract](../development/pr-review.md),
[Agent Skills registry](../workflows/agent-skills.md),
[technical baseline](../architecture/technical-baseline.md), and
[repository structure](../architecture/repository-structure.md) describe the
current TraceQuant context. These documents are the sources to re-check when
the copied snapshot and the current branch differ.

### Active evolution and design baseline

The [LCK v1 Design Charter](../workflows/LCK-v1-Design-Charter.md) is a design
baseline, not proof that every future capability exists. Its durable principles
include semantic/mechanical separation, live facts as authority, one
phase-specific operation snapshot per LCK invocation, fail-closed ambiguity,
bounded effects, no daemon, provider neutrality, Review FAIL → STOP, and human
manual Squash Merge. The [typed leaf workflow map](../architecture/typed-leaf-workflows.md)
records the current profile ownership boundary.

Because LCK is actively evolving, future changes may alter source layout,
profile contracts, validation, Skills, or lifecycle details. A repository-copy
adopter must re-check the selected revision rather than assuming compatibility
with future TraceQuant main.

### Future portability work

The separately published `preview.2` archive is available through the
versioned path described above. A standalone LCK repository, package
extraction, one-click installer, zero-configuration setup, universal
compatibility, and automatic external adoption are not provided by this guide.
They remain future portability or distribution work, not current implementation
facts.

Manual adoption also does not decouple LCK core from TraceQuant, change
TraceQuant's product identity, or grant an adopting repository any of
TraceQuant's quantitative, trading, exchange, or risk capabilities.

## Distribution boundary checklist

An adoption description is within scope only when it:

- names the exact source revision or supported release;
- states whether the selected distribution path is actually available;
- separates direct reuse from repository adaptation;
- identifies Issue, label, Project, CI, directory, Skill, and local-state
  assumptions;
- validates the adapted repository non-destructively before lifecycle writes;
- preserves fresh review and maintainer merge boundaries; and
- labels future packaging or portability work as future.

It must not claim that TraceQuant has completed quantitative research,
backtesting, Demo, Live trading, production execution, or general external
adoption.
