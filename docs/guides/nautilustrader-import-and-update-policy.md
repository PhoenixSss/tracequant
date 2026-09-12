# NautilusTrader import and update policy

- **Policy status:** active for the TraceQuant v2 bootstrap and later integration work
- **Initial approved identity:** `nautilus-trader==2.0.0rc4`
- **Initial Python baseline:** CPython `3.13` (`>=3.13,<3.14`)
- **Import namespace:** `nautilus_trader`
- **Upstream release identity:** tag `v2.0.0rc4`, commit
  `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`
- **Architecture decision:**
  [ADR-0001](../architecture/adr-0001-nautilustrader-primary-runtime.md)
- **Safety state:** `LIVE_NOT_APPROVED`

## Purpose and current boundary

This policy freezes how TraceQuant acquires, identifies, imports, upgrades, and
rolls back NautilusTrader. It also keeps upstream source and version-sensitive
persistent state separate from TraceQuant-owned code.

The v2 bootstrap installs the initial approved wheel and exposes only an
explicit distribution/import identity seam. It does not claim that a trading
runtime, strategy, catalog, exchange connection, Demo mode, or Live mode is
available. Current implementation facts remain in the
[technical baseline](../architecture/technical-baseline.md). Persistent roots
and any environment beyond this non-production bootstrap require separately
scoped implementation and acceptance work.

The keywords **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## Distribution, lock, and import identity

The only approved initial package declaration is the official PyPI project
`nautilus-trader`, pinned with the exact direct requirement:

```toml
dependencies = [
    "nautilus-trader==2.0.0rc4",
]
```

The installed Python namespace is `nautilus_trader`. Distribution names use a
hyphen and import names use an underscore; code and verification MUST NOT treat
them as interchangeable strings.

The integration change that adds this dependency MUST also commit the resulting
`uv.lock` and a tracked v2 `uv.toml`. That `uv.toml` MUST configure uv to reject
a NautilusTrader source build:

```toml
no-build-package = ["nautilus-trader"]
```

The v2 `uv.toml` is repository policy, not machine-local state. It MUST NOT
carry forward a retired repository-local workflow cache location or introduce
any other repository-local package, source, or audit cache.

The lock entry MUST resolve `nautilus-trader` from the standard PyPI registry,
not from a Git URL, direct URL, local directory, editable install, workspace,
or `tool.uv.sources` override. The official
[PyPI release record](https://pypi.org/project/nautilus-trader/2.0.0rc4/) and
the locked wheel hash are the package acquisition evidence. The upstream tag
and commit identify the reviewed source release; an upstream checkout is never
an installation source for TraceQuant.

TraceQuant supports a Python/platform target for NautilusTrader only when all
of these conditions hold:

1. the target is inside TraceQuant's declared CPython and deployment matrix;
2. upstream publishes an official compatible wheel for the exact pinned
   version and target;
3. a clean, disposable environment installs the committed lock with
   `uv sync --locked --dev --no-build-package nautilus-trader --no-cache`;
4. installation did not invoke a NautilusTrader build backend; and
5. the distribution and import identity checks below pass.

The `--no-cache` check prevents a locally built cached wheel from satisfying
the clean-install gate. The committed `no-build-package` setting makes normal
syncs fail when no compatible wheel exists. A missing wheel, unsupported
CPython, unsupported platform, resolution failure, or install failure is a
hard failure; it MUST NOT trigger a source build or a relaxed target matrix.
The uv behavior is defined by its official
[`no-build-package` setting](https://docs.astral.sh/uv/reference/settings/#no-build-package).

The integration acceptance suite MUST mechanically prove that:

- `importlib.metadata.version("nautilus-trader")` equals the exact direct pin;
- `import nautilus_trader` succeeds in the locked environment;
- the imported module resolves from the installed distribution in that
  environment, not from the repository, an upstream checkout, `PYTHONPATH`, or
  another injected source root;
- `uv lock --check` succeeds and the lock has no NautilusTrader Git, path,
  editable, workspace, or source override; and
- the wheel filename, PyPI index identity, wheel SHA-256, Python/platform tag,
  upstream release/tag and commit, and lock digest are captured in the
  acceptance record.

These checks are additions to the repository's canonical quality commands,
not a replacement command set.

## Source and environment isolation

Upstream source checkouts, official-source audits, develop/nightly experiments,
wheel downloads retained for evidence, unpacked wheels, compiler output, and
build artifacts MUST live outside both the TraceQuant repository and its
project environment. They MUST NOT be committed or placed under `src/`,
`tests/`, `docs/`, `vendor/`, `.venv`, or another repository-local path.

The repository MUST NOT contain or depend on any of the following:

- a vendored copy of NautilusTrader source, generated bindings, examples,
  fixtures, or tests;
- a NautilusTrader Git submodule;
- a Git, local-path, editable, workspace, or source-checkout dependency;
- imports from a private module or a name documented as internal upstream;
- a monkey patch of an upstream class, function, module, or global; or
- an unapproved fork, repackaged wheel, or private package index substitute.

An audit MAY inspect a fixed upstream checkout in a disposable external
workspace. Its result MUST record the exact tag/commit and remain evidence; the
checkout never becomes application input or import state.

## Code ownership and import boundary

The authoritative code boundary is the approved v2
[repository structure](../architecture/repository-structure.md). Direct
production imports of `nautilus_trader` are confined to
`tracequant.integrations.nautilus`. Dedicated NautilusTrader acceptance tests
MAY import the public upstream symbols they verify. A static import-boundary
test MUST reject direct or dynamic upstream imports elsewhere.

TraceQuant production strategy/model behavior is implemented as concrete,
Nautilus-native Strategy/Actor code inside that boundary:

```text
TraceQuant research artifact
  -> tracequant.integrations.nautilus.strategies
       -> public Nautilus Strategy/Actor, OrderFactory, and runtime APIs
            -> installed nautilus_trader distribution
```

Model loading and inference MUST live with the concrete strategy that consumes
them. The repository MUST NOT introduce a generic Strategy Adapter,
model-to-runtime middleware, project Order/Position DTOs, or a parallel strategy
runtime. Concrete strategy code MAY create, submit, cancel, or modify orders
only through public Nautilus Strategy and OrderFactory APIs; it MUST NOT
construct an exchange client, bypass Nautilus risk/execution, or mutate upstream
internals. Integration code MUST remain use-case-shaped and MUST NOT mirror
upstream package directories. Acceptance code MUST verify public API
compatibility, import ownership, policy and Nautilus risk vetoes, order
behavior, persistence, and reconciliation without becoming production code.

NautilusTrader owns its trading data/instrument types, Strategy/Actor runtime,
orders, positions, account/portfolio state, core risk, fills/accounting,
Binance adapter, cache, restart, and reconciliation semantics. TraceQuant owns
raw-source provenance, causal research data, features and labels, model
artifacts and alpha, project risk thresholds and confirmed-missing policy
extensions, environment admission, acceptance evidence, and operator approval.
TraceQuant MUST NOT copy or wrap upstream domain objects into a parallel mutable
trading domain or place a generic adapter layer between a concrete
TraceQuant-owned Nautilus Strategy/Actor and the public upstream runtime.

## Persistent identity and migration

Every catalog, cache, and run root created after integration MUST be partitioned
by an immutable Nautilus identity:

```text
<package_version>+<upstream_release_or_commit>
```

For the initial baseline this is
`2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d`. Each root manifest MUST
repeat the full identity, applicable schema identity, TraceQuant code version,
and environment lock digest. On open, configured, imported, path, and manifest
identities MUST agree exactly. A missing identity or mismatch is a hard error.

An upgrade MUST allocate new catalog, cache, and run partitions before it can
read or write persistent state. It MUST NOT use `latest`, `current`, an
unversioned fallback, or the last-known-good partition as its candidate write
target. The last-known-good partitions remain immutable during candidate
validation and until an explicit later retention decision.

Schema or data migration is never an in-place operation. A reviewed migration
MUST read an immutable prior partition and write a new candidate-identity and
schema-identity partition, or rebuild the new partition from immutable raw
inputs. It MUST record input/output identities and digests, migration code
version, result, and rollback point. An interrupted, failed, or partial
migration leaves the previous partition active and the candidate partition
inactive.

Promotion changes explicit configuration to select a fully qualified identity;
directory renames, mutable aliases, and implicit discovery are prohibited.

## Candidate upgrade procedure

NautilusTrader upgrades are one-package, separately reviewed changes. An
automatic dependency updater, broad `uv lock --upgrade`, unrelated dependency
change, or opportunistic lock refresh MUST NOT change its direct pin. The exact
pin makes such drift mechanically visible.

An upgrade candidate follows this sequence:

1. **Propose one exact version.** Change only the direct
   `nautilus-trader==<candidate>` requirement, then run the bounded
   `uv lock --upgrade-package 'nautilus-trader==<candidate>'` operation. Any
   transitive lock changes MUST be necessary for that candidate and explained;
   unrelated changes are removed or split.
2. **Identify the release.** Record the package version, upstream tag and exact
   commit when available, release date, official PyPI project, wheel filename
   and SHA-256, supported CPython/platform tags, and resulting lock digest.
3. **Review upstream change.** Inspect official release notes/changelog, public
   API and deprecation changes, catalog/cache/schema migrations, security
   advisories, license or packaging changes, and unresolved defects relevant to
   the selected version.
4. **Review Binance behavior.** Inspect changes to Binance USD-M market data,
   instruments, orders, post-only/reduce-only behavior, position and margin
   modes, fills, funding/fees, cache, restart, and reconciliation. Missing or
   contradictory evidence fails the affected gate.
5. **Install in isolation.** Use a clean disposable environment and cache
   outside the repository. Pass the wheel-only resolution, lock, import, and
   provenance checks. Develop/nightly wheels do not qualify.
6. **Run compatibility and migration tests.** Exercise the required matrix
   below against new candidate roots, never against last-known-good roots.
7. **Compare results.** Compare candidate evidence with the last-known-good
   result using predeclared tolerances. Unexplained differences fail; they are
   not accepted as version noise.
8. **Promote explicitly.** A maintainer-approved, independently reviewed change
   commits the exact pin, lock, compatibility record, and explicit selected
   identity. Merely passing tests does not promote a candidate or authorize
   Live.

The compatibility matrix MUST cover every currently implemented affected
surface and, when applicable:

- clean wheel-only install, distribution/import identity, public API imports,
  and static import ownership;
- TraceQuant data conversion and catalog read/write against immutable fixtures;
- deterministic strategy, risk-veto, backtest, fills, fees, funding,
  accounting, and report comparisons;
- Binance adapter configuration and protocol acceptance, without submitting a
  Live order;
- cache open, warm/cold restart, unknown-order handling, and reconciliation;
- new-partition migration or rebuild plus mismatch/failure injection; and
- the repository's canonical tests, lint, format, type, lock, and security
  checks.

Only gates whose supported surface exists are run, but absence of an
integration, Demo, restart, or Live surface is recorded as `NOT_VERIFIED`, not
as a pass. The Live admission requirements in ADR-0001 remain closed until
separate evidence and human approval satisfy them.

## Upgrade record and promotion decision

Every candidate that is accepted MUST add a versioned repository record. The
record MUST contain:

- candidate package version and prior active version;
- upstream tag/release and exact commit when available;
- PyPI project URL, wheel filename, Python/platform tag, and wheel SHA-256;
- `pyproject.toml` pin, `uv.toml` source-build policy and digest, `uv.lock`
  digest, TraceQuant commit, and environment lock digest;
- release, migration, security, license/packaging, and Binance review results;
- each compatibility command or test target, result artifact/digest, and
  explicit last-known-good comparison;
- candidate catalog/cache/run and schema identities;
- explicit promotion decision, approver, date, and unresolved limitations;
  and
- Git rollback point and persistent-data rollback identity.

A rejected or incomplete candidate MAY retain bounded evidence, but it MUST be
marked inactive and MUST NOT change the selected version or roots.

## Failure and rollback

Failure at resolution, install, provenance, review, security, migration,
compatibility, comparison, or promotion leaves the prior exact version and its
catalog/cache/run roots active. Candidate code and state MUST NOT be partially
promoted.

Rollback has two independent parts:

1. **Code and environment:** revert to the recorded last-known-good Git point,
   which restores `pyproject.toml`, `uv.toml`, and `uv.lock`; create/sync a
   clean environment from that lock with NautilusTrader source builds disabled;
   and verify the recorded distribution/import identity.
2. **Persistent state:** select the recorded last-known-good fully qualified
   catalog/cache/run identities. Never down-migrate, rewrite, or copy candidate
   state over those partitions.

If either rollback part cannot be verified, the runtime remains stopped. Local
and exchange state disagreement, unknown order state, or failed reconciliation
also prevents new positions until separately resolved. Rollback never enables
Live or bypasses risk authority.

## Develop, nightly, and fork policy

Develop/nightly wheels are permitted only for upstream investigation in a
disposable external environment. They MUST NOT modify `pyproject.toml`,
`uv.toml`, `uv.lock`, the project environment, accepted evidence, or any
catalog/cache/run root. Results are exploratory and cannot promote a version.

A fork requires a separate accepted architecture decision before any fork
artifact is used. That decision MUST name the defect or requirement, why an
upstream release cannot satisfy it, fork repository/commit and patch digest,
security and maintenance owner, package/import identity, compatibility scope,
expiry or review date, upstream issue or pull request, and an explicit exit
plan back to upstream. Without all of those items, the fork remains prohibited.

## Acceptance mapping

| Required outcome | Mechanical or review evidence |
| --- | --- |
| Official exact wheel resolves without a source checkout | Exact direct pin, tracked `uv.toml` wheel-only policy, and committed lock; clean `--no-cache --no-build-package` install; PyPI wheel provenance receipt |
| Source and import ownership are unambiguous | Distribution/import identity test, module-origin assertion, static import-boundary and tree-policy tests |
| Unrelated updates cannot introduce a new version | Exact direct pin; no source override; candidate-only lock diff; prohibition on automatic/broad upgrade promotion |
| Candidate cannot overwrite last-known-good roots | Fully qualified identity partitions, new candidate roots, immutable prior manifests, mismatch/failure tests |
| Accepted upgrade is auditable | Required versioned upgrade record with upstream, wheel, lock, compatibility, comparison, promotion, and rollback identities |
| Any failed gate preserves the prior version | No partial promotion; independent Git/environment and persistent-state rollback; fail-closed runtime admission |
