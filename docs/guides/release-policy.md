# TraceQuant and LCK release policy

> **Status:** Current release policy and LCK preview record
> **Current LCK preview:** `lck-v0.1.0-preview.2`
> **Last repository-state check:** 2026-09-03

This policy defines how releases of the TraceQuant project and previews of the
Local Control Kernel (LCK) are identified, checked, documented, and kept
traceable. It is a release decision and record-keeping policy, not release
automation. The GitHub Release record and its named assets are the authority
for the exact source commit, manifest, and digest of each published preview.

## 1. One repository, two release tracks

TraceQuant remains the primary project and repository. Its long-term goal is an
auditable research-to-live quantitative trading system for cryptocurrency
perpetual futures. The current repository is still a Research MVP foundation;
it does not provide an exchange client, research pipeline, backtester,
strategy, model, order execution, risk engine, Demo, or Live trading.

LCK is an engineering capability developed within TraceQuant to make
Codex-centered, AI-assisted repository work deterministic, auditable, and
human-controlled. LCK is not an independent repository, product, general
Agent platform, trading module, or risk authority. A separate LCK release
identity makes the component snapshot traceable; it does not change that
ownership or boundary.

The tracks may progress at different speeds. A TraceQuant project release can
exist while LCK is still preview-only, and an LCK preview can be published
without changing the TraceQuant package version. Neither track inherits the
other track's stability claim.

| Track | Release object | Identity convention | Meaning |
| --- | --- | --- | --- |
| TraceQuant project | A reviewed TraceQuant repository state and, when applicable, its project package | `tracequant-v<MAJOR>.<MINOR>.<PATCH>`; the package version remains the value in `pyproject.toml` | A project release. It must not imply that planned quantitative or trading capabilities are implemented. |
| LCK component preview | A deliberately scoped, manifest-backed LCK source archive, if one is published | `lck-v<MAJOR>.<MINOR>.<PATCH>-preview.<N>` | A versioned LCK snapshot for manual evaluation and adaptation inside TraceQuant's project context. |

The first LCK preview, [`lck-v0.1.0-preview.1`](https://github.com/PhoenixSss/tracequant/releases/tag/lck-v0.1.0-preview.1),
was published and remains an immutable historical release. The corrected
current preview is [`lck-v0.1.0-preview.2`](https://github.com/PhoenixSss/tracequant/releases/tag/lck-v0.1.0-preview.2),
which supersedes `preview.1` without reusing its tag, source commit, manifest,
or archive. Preview number `N` increases for another published candidate in
the same preview series. A stable LCK identity removes the `-preview.N` suffix
only after the stable criteria in Section 8 are met. A version or tag is never
reused for a different commit, manifest, or archive digest.

The `version = "0.1.0"` value currently in `pyproject.toml` identifies the
`tracequant` Python project. It is not automatically the LCK version, and
publishing an LCK preview must not change it solely to express the LCK
preview.

## 2. Current availability and source-of-truth boundaries

At the state check recorded above, `preview.1` and the corrected `preview.2`
are distinct GitHub pre-releases. `preview.2` is the current supported LCK
source archive for manual evaluation and adaptation; `preview.1` remains an
immutable historical release and is clearly marked as superseded. The
repository-copy path remains available as an alternative. A GitHub-generated
source archive for an arbitrary commit, branch, or tag is not by itself an LCK
release archive.

The live [GitHub Release record](https://github.com/PhoenixSss/tracequant/releases/tag/lck-v0.1.0-preview.2)
is authoritative for `preview.2`'s exact source commit, included-path
manifest, manifest digest, archive digest, metadata, checksums, and validation
records. This policy records the meaning and continuity of those facts; it
does not silently replace or rewrite the `preview.1` tag or assets.

The documents have deliberately different responsibilities:

- The [README](../../README.md) gives project orientation, current package
  metadata, and current capability limits.
- The [LCK overview](LCK-overview.md) explains what LCK is, how the lifecycle
  is divided, and where LCK stops.
- The [manual adoption guide](LCK-adoption.md) explains repository-copy
  adoption, adaptation points, and the versioned archive-adoption path.
- The [technical baseline](../architecture/technical-baseline.md) is the
  authority for current TraceQuant implementation facts and deferred product
  boundaries.
- The [LCK v1 Design Charter](../workflows/LCK-v1-Design-Charter.md) records
  design intent and is not evidence that future capabilities exist.

This policy owns release identity, scope, gates, metadata, release notes, and
continuity. It links to those documents instead of duplicating their complete
lifecycle, adoption, or architecture descriptions.

## 3. Release scope

Every release candidate has an explicit scope statement and an exact file
manifest. A directory name in a release note is not sufficient: the manifest
must either enumerate every included file or record a deterministic expansion
whose resulting file list and digest are preserved with the release record.

### TraceQuant project release

A TraceQuant release represents the reviewed repository state at one exact
commit. It may contain the project source, documentation, tests, and package
metadata appropriate to the project release. Its notes must continue to
distinguish current implementation from future `apps/`, `packages/`,
`deploy/`, research, execution, and risk boundaries. A project archive is not
evidence that the eventual trading system is complete.

### LCK-specific release archive

An LCK archive is a manually prepared, purpose-specific snapshot rather than
an extracted Python package. The first preview's manifest should include the
coherent LCK source and the contracts needed to understand and validate that
snapshot, including the applicable items from these sets:

1. LCK facade and kernel: `tools/agent_workflow/lck.py`,
   `tools/agent_workflow/lck_core/`, and every adjacent policy, contract,
   workflow, profile, and validation module imported by that snapshot;
2. control contracts: the applicable `AGENTS.md`, `.agents/policies/`,
   provider Skills, Issue form, CI workflow, and provider-specific rules;
3. workflow documentation: the current Issue workflow, Independent Review
   contract, Agent Skills registry, and the public LCK overview/adoption
   boundaries;
4. validation material and relevant workflow tests needed to reproduce the
   supported checks for the selected snapshot.

The exact paths, provider variants, and any exclusions within those sets are
the release manifest's decision. The [adoption guide's source and contract
lists](LCK-adoption.md#path-a-repository-copy-adoption) are the starting point
for assembling a coherent set, not permission to copy only `lck.py` or to
assume that a future revision has the same layout.

An LCK archive excludes, unless a future policy revision explicitly changes
the track:

- the `tracequant` runtime package and unrelated future product boundaries
  when they are not required by the selected LCK snapshot;
- exchange data, research datasets, credentials, private keys, `.env` files,
  logs, databases, caches, virtual environments, build output, and ignored
  local workflow state such as `.workflow.local/` and validation artifacts;
- unrelated historical reports, experiments, or source files that are not in
  the manifest;
- GitHub credentials, authentication material, and any secret-bearing local
  configuration;
- release automation, an installer, or packaging metadata that the repository
  does not currently provide.

The archive must record the full source commit even if the archive itself does
not contain `.git` history. Manual adopters must still adapt repository
identity, Issue forms, Project fields, CI, permissions, paths, and provider
integration as described by the adoption guide.

## 4. Distinguishing distribution forms

These three forms must not be described as interchangeable:

1. **GitHub repository source archive.** GitHub may generate a ZIP or tarball
   for a repository ref. It is a view of the repository tree at that ref. It
   is not automatically a supported LCK distribution, does not define an LCK
   manifest, and does not establish compatibility or checksums beyond what a
   maintainer separately records.
2. **LCK-specific release archive.** This is an explicitly published LCK
   preview artifact with a release identity, exact source commit, included
   paths, compatibility notes, license information, integrity data, and
   manual-adoption instructions. It is a source snapshot for evaluation and
   adaptation, not an installable universal product.
3. **Future independently packaged or installable distribution.** A package
   or installer would require a separate scoped implementation and release
   contract covering package boundaries, dependencies, entry points,
   supported platforms, upgrade behavior, security, and reproducible builds.
   No such LCK distribution is promised by this policy or currently supplied
   by TraceQuant.

## 5. Required release metadata and integrity

If an LCK-specific archive is published, its GitHub Release notes and the
archive's accompanying metadata must provide, at minimum:

- LCK version and exact tag;
- TraceQuant repository identity and the full source commit SHA;
- publication date and whether the release is a preview or stable release;
- the exact included-path manifest, its format, and a manifest digest;
- compatibility notes for the selected snapshot, including the current
  Python/uv assumptions and any PyYAML or other development-tool requirement;
- `LICENSE` and applicable attribution or third-party notice information;
- archive filename, media type, byte size, and SHA-256 or an equivalent
  integrity value; and
- a link to the [manual adoption guide](LCK-adoption.md), with any known
  adaptation, upgrade, or unsupported-environment limitations.

The source commit, tag, manifest, and artifact digest form one identity set.
The maintainer must verify that the tag resolves to the recorded commit, that
the archive expands to the recorded manifest, and that a fresh digest matches
the published value. Rebuilding an artifact with a different digest requires
a new release identity; the old identity is not silently replaced.

## 6. Release entry gates

Publication is allowed only after every applicable gate below has a recorded
pass and the maintainer has approved the candidate. A pending or unavailable
gate is not a pass.

### Candidate identity and cleanliness

- The candidate is based on a reviewed, known commit on the intended
  TraceQuant line; the full SHA is recorded before publication.
- The release working tree is clean, the tag name is unused, and the tag is
  created only after the final candidate identity is fixed.
- The staged or archived file list exactly matches the manifest. No generated,
  local, secret-bearing, unrelated, or out-of-scope file is included.
- The commit, tag, manifest digest, archive digest, and GitHub Release target
  agree. A branch name, short SHA, latest pointer, or conversational memory
  cannot substitute for the full identity set.

### Documentation state

- This policy is present at its canonical path and agrees with the selected
  release identity and availability facts.
- The README, LCK overview, manual adoption guide, technical baseline, and
  relevant workflow documentation describe the same current boundaries and
  do not claim that a planned capability is implemented.
- Release notes state what is current, what remains evolving, and what a
  manual adopter must do. Broken links, stale version claims, or unresolved
  scope contradictions block publication.

### CI and LCK validation

- The exact candidate commit passes the current relevant CI checks. The
  repository's current `quality` job runs `uv lock --check`, frozen pytest,
  Ruff lint, Ruff format checking, and strict mypy; a release record names the
  observed run or its equivalent evidence.
- The relevant LCK/Validation Runner contract passes on the exact candidate,
  with workflow changes receiving the workflow-specific validation and
  workflow tests required by the current repository contract. Validation
  evidence is retained as bounded evidence, not used as permission to change
  the candidate afterward.
- If the release scope changes LCK source, profile policy, Skills, CI, Rules,
  or workflow semantics, the candidate also receives the fresh independent
  review and maintainer manual-merge boundary required by the repository
  workflow. Delivery validation or a passing CI job does not replace that
  review.

### License and secret hygiene

- `LICENSE` is present in the source and any LCK archive, the release notes
  identify the applicable Apache-2.0 license, and included third-party
  material has its required attribution and license obligations recorded.
- The maintainer checks the exact manifest, staged tree, release metadata, and
  archive contents for credentials, tokens, private keys, authentication
  headers, `.env` material, private research data, and other secrets. Known
  redaction patterns are useful diagnostics but are not a guarantee against
  secrets embedded in free text.
- The current CI workflow has no dedicated license scanner or secret scanner.
  Therefore a green CI result does not satisfy these gates by itself; the
  release record must identify the manual or separately approved tool check
  used, or publication stops.

### Maintainer approval

The maintainer explicitly approves the exact version, tag, source SHA, scope
manifest, metadata, validation evidence, release notes, and preview/stable
classification. Approval is for that identity set only. Changing any member
of it requires the gates to be re-run and approval to be renewed.

## 7. Release-note requirements

Every TraceQuant or LCK release note must be specific about the track. An LCK
preview note must include:

- the current capabilities of the released snapshot and the exact source
  commit;
- known limitations, active evolution, compatibility assumptions, and manual
  adoption steps;
- the boundary between the TraceQuant project and LCK, including that LCK is
  an engineering capability within TraceQuant rather than a standalone
  product;
- provider neutrality, with Codex identified as a primary use case without
  claiming that other providers or environments are automatically supported;
- the archive's included paths, integrity value, license, and any required
  attribution;
- the validation and approval record at a bounded, reproducible level; and
- correction, withdrawal, or upgrade guidance if the release supersedes or
  limits an earlier one.

Notes must not claim completed quantitative research, backtesting, Demo or
Live trading, production execution, a standalone LCK repository or product,
one-click or zero-configuration installation, universal portability, or
external adoption. Reuse value and future portability are directions, not
evidence of those outcomes.

## 8. Preview and stable status

The LCK preview series remains **pre-release** and actively evolving.
`lck-v0.1.0-preview.2` is the current corrected preview and its public
adoption surface is a fixed source snapshot for manual copying and adaptation,
not a stable universal interface. `preview.1` remains visible as an immutable
historical release and is superseded by `preview.2`.

An LCK stable release requires, at minimum:

- a documented compatibility and upgrade contract for the published scope;
- a repeatable validation path that is successful for the exact candidate;
- a stable, supported adoption surface whose repository-specific assumptions
  are explicit;
- complete metadata, license, secret, documentation, integrity, and
  maintainer-approval gates; and
- a continuity record showing how the stable release relates to earlier
  previews and how corrections will be issued.

Until those conditions are met, a release must retain the preview suffix and
GitHub pre-release status. TraceQuant's project release status and LCK's
preview/stable status are independent decisions.

## 9. Continuity, corrections, and withdrawal

Release history is part of the artifact's audit trail. Each published release
keeps its tag, source SHA, manifest, digest, notes, and status visible in the
Git history or GitHub Release record. A later release must point to the
identity it supersedes; "latest" is a convenience label, never an identity.

The continuity rules are:

- published tags and archive identities are immutable;
- preview versions and later stable versions are monotonically identifiable;
- every release remains traceable to one exact source commit and manifest;
- a correction uses a new patch or preview identity and explains the defect,
  impact, and relationship to the earlier release;
- a changed archive digest, included path, or source commit is a new release,
  even when the human-readable version would otherwise look convenient; and
- a withdrawn release is marked withdrawn with a reason and replacement or
  safety guidance. It is not silently rewritten or made to appear as a
  different artifact. If exceptional legal or security action requires
  removing an asset or tag, the withdrawal record must preserve the former
  identity, date, reason, and replacement relationship as far as the hosting
  service permits.

The policy does not require a floating compatibility promise. Adopters must
select a named release, verify its digest, read its compatibility notes, and
adapt the repository integrations against that exact snapshot. Future source
changes, packaging, portability, or release automation require their own
scoped work and must not be inferred from this policy alone.
