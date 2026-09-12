# TraceQuant v1 planning-state retirement record

## Decision and scope

This record captures the controlled GitHub planning-state convergence required
by [Issue #321](https://github.com/PhoenixSss/tracequant/issues/321) after the
immutable v1 archive was published. It does not claim that unfinished v1 work
was implemented. The retired Issues use GitHub's `NOT_PLANNED` reason, and the
corresponding Project items use `Done` only as a terminal planning state.

The immutable recovery source remains
[`tracequant-v1-archive-2026-09-12`](https://github.com/PhoenixSss/tracequant/releases/tag/tracequant-v1-archive-2026-09-12),
whose peeled source commit is
`27a9fdd877533f933cde4818eba9c186d286c529`. Closed Issue and Pull Request
history, comments, links, branches, commits, and Git identities were preserved.
No Pull Request was merged by this retirement work.

## Acquisition and action chronology

The Issue #321 contract identified an initial population of 41 open v1 Issues
plus PR #311. A maintainer-directed Issue-only convergence batch was recorded
on Issue #321 at `2026-09-12T07:30:50Z` and updated at
`2026-09-12T07:36:07Z`. Live queries confirmed that the 41 Issues were closed
between `2026-09-12T07:31:53Z` and `2026-09-12T07:33:22Z` as
`CLOSED / NOT_PLANNED`, their obsolete `codex:*` lifecycle labels were removed,
and their Project items were `Done`.

Immediately before the remaining actions, PR #311 was `OPEN`, non-draft,
`MERGEABLE / CLEAN`, with base
`a1fb5d4b87c12a0e2bd1fada12e6d4c46076d3b1` and head
`5462516866153989ae4398b816a3b8cb29fb4746`. Its one-file roadmap change was
an obsolete #308-era v1 planning snapshot. The current product-recalibration
baseline, Epic #312, and the immutable v1 archive preserve the relevant
decision and historical identities, so the PR had no independent current
planning outcome that justified merge. It was closed without merge at
`2026-09-12T11:37:42Z`, with the retirement reason recorded on the PR.

The same pre-action query found the already closed Issue #302 still at Project
Status `Review`. That stale Project item was moved to `Done`; its Issue remained
`CLOSED / NOT_PLANNED` and its historical body was not rewritten.

The post-action snapshot was acquired at `2026-09-12T11:38:09Z` from repository
main `427faf44cf892ec0bd821cbe8cedab8cd63dd4c0`.

## Finite transition mapping

Every row below maps an affected live object to its observed final state. The
classification `superseded and archived / not planned` means that Epic #312
replaces the old planning direction, while any tracked implementation remains
recoverable from the immutable v1 archive. It does not mean the old acceptance
criteria were completed.

| Object | Final Issue / PR state | Final Project state | Classification and reason |
|---|---|---|---|
| [#1](https://github.com/PhoenixSss/tracequant/issues/1) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#11](https://github.com/PhoenixSss/tracequant/issues/11) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#12](https://github.com/PhoenixSss/tracequant/issues/12) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#13](https://github.com/PhoenixSss/tracequant/issues/13) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#14](https://github.com/PhoenixSss/tracequant/issues/14) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#15](https://github.com/PhoenixSss/tracequant/issues/15) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#16](https://github.com/PhoenixSss/tracequant/issues/16) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#17](https://github.com/PhoenixSss/tracequant/issues/17) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#18](https://github.com/PhoenixSss/tracequant/issues/18) | `CLOSED / NOT_PLANNED` | `Done` | The completed framework validation is registered separately by #316; the original TraceQuant integration Feature was not proven complete |
| [#19](https://github.com/PhoenixSss/tracequant/issues/19) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#20](https://github.com/PhoenixSss/tracequant/issues/20) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#21](https://github.com/PhoenixSss/tracequant/issues/21) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#22](https://github.com/PhoenixSss/tracequant/issues/22) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#23](https://github.com/PhoenixSss/tracequant/issues/23) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#24](https://github.com/PhoenixSss/tracequant/issues/24) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#25](https://github.com/PhoenixSss/tracequant/issues/25) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#26](https://github.com/PhoenixSss/tracequant/issues/26) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#27](https://github.com/PhoenixSss/tracequant/issues/27) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#28](https://github.com/PhoenixSss/tracequant/issues/28) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#29](https://github.com/PhoenixSss/tracequant/issues/29) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#30](https://github.com/PhoenixSss/tracequant/issues/30) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#31](https://github.com/PhoenixSss/tracequant/issues/31) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#32](https://github.com/PhoenixSss/tracequant/issues/32) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#33](https://github.com/PhoenixSss/tracequant/issues/33) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#34](https://github.com/PhoenixSss/tracequant/issues/34) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#35](https://github.com/PhoenixSss/tracequant/issues/35) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#36](https://github.com/PhoenixSss/tracequant/issues/36) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#37](https://github.com/PhoenixSss/tracequant/issues/37) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#38](https://github.com/PhoenixSss/tracequant/issues/38) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#39](https://github.com/PhoenixSss/tracequant/issues/39) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#40](https://github.com/PhoenixSss/tracequant/issues/40) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#41](https://github.com/PhoenixSss/tracequant/issues/41) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#42](https://github.com/PhoenixSss/tracequant/issues/42) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#43](https://github.com/PhoenixSss/tracequant/issues/43) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#44](https://github.com/PhoenixSss/tracequant/issues/44) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#61](https://github.com/PhoenixSss/tracequant/issues/61) | `CLOSED / NOT_PLANNED` | `Done` | LCK / development-governance planning retired with v1; recoverable from the archive and not copied into v2 |
| [#218](https://github.com/PhoenixSss/tracequant/issues/218) | `CLOSED / NOT_PLANNED` | `Done` | LCK planning retired with v1; recoverable from the archive and not copied into v2 |
| [#250](https://github.com/PhoenixSss/tracequant/issues/250) | `CLOSED / NOT_PLANNED` | `Done` | LCK planning retired with v1; recoverable from the archive and not copied into v2 |
| [#285](https://github.com/PhoenixSss/tracequant/issues/285) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#308](https://github.com/PhoenixSss/tracequant/issues/308) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned; PR #311 was not completion evidence |
| [#309](https://github.com/PhoenixSss/tracequant/issues/309) | `CLOSED / NOT_PLANNED` | `Done` | Superseded and archived / not planned |
| [#302 Project item](https://github.com/PhoenixSss/tracequant/issues/302) | Issue remained `CLOSED / NOT_PLANNED` | `Review` → `Done` | Repaired stale Project state for an already retired v1 Task; no Issue history rewrite |
| [PR #311](https://github.com/PhoenixSss/tracequant/pull/311) | `CLOSED`, `mergedAt = null` | Not a non-terminal Project item | Obsolete v1 planning record; closed without merge, with head identity preserved |

## Expected open planning surface after convergence

The final repository query, taken before LCK created Issue #321's Delivery PR,
returned no open Pull Request. A later PR owned by #321 is the retirement
record's review vehicle, not a surviving v1 planning PR. The only open Issues
were the finite retirement/v2 chain below:

| Objects | Meaning at the snapshot |
|---|---|
| #314, #315, #321 | Phase 0 retirement Feature, read-only retirement gate, and this convergence item; #314 remains blocked by #321 |
| #312 | The v2 Greenfield Reboot Epic; not a v1 planning object |
| #313, #316, #317, #322, #323 | Ordered v2 evidence, design, policy, and bootstrap work; all leaf work remained blocked |

Project items outside `Done` matched that same set exactly: #312 and #315 were
`Specifying`, #321 was `In Progress`, and #313, #314, #316, #317, #322, and
#323 were `Blocked`. No LCK planning item remained active for v2. LCK source
still present in the working v1 tree is historical workflow infrastructure,
not a v2 planning commitment; both LCK and v1 business code remain recoverable
from the immutable archive.

## Gate boundary and reproducibility

The final snapshot establishes planning convergence for Issue #321 only. It
does not itself assert `V1_RETIREMENT_COMPLETE`, close the Phase 0 Feature, or
authorize v2 work. Issue #314 must independently reacquire current Git, GitHub,
archive, retained-asset, repository-cleanliness, and recovery facts. GitHub
state can change after the timestamp above, so that audit must treat this
record as bounded transition evidence rather than current-state authority.
