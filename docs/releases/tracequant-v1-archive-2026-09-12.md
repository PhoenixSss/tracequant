# TraceQuant v1 archive publication record

## Publication identity

The historical, non-production TraceQuant v1 archive was published once at
`2026-09-12T09:20:14Z`:

| Field | Exact value |
| --- | --- |
| GitHub Release | [`tracequant-v1-archive-2026-09-12`](https://github.com/PhoenixSss/tracequant/releases/tag/tracequant-v1-archive-2026-09-12) |
| Release database ID | `387517006` |
| Annotated tag object | `1ed4507486fbb5280f9bced5833713b26e7d3512` |
| Peeled source commit | `27a9fdd877533f933cde4818eba9c186d286c529` |
| Source tree | `8406076ce81a2476005e3c938abd4c5a088373c2` |
| Package version in the archive | `0.1.0` |
| Tracked scope | Complete 183-file Git tree at the source commit |
| Custom Release assets | None |
| GitHub classification | Published, non-draft, non-prerelease historical archive |

The remote tag ref resolves to the annotated tag object above and peels to the
same commit as the Release target. The tag name, annotated tag object, peeled
commit, source tree, Release target, and custom asset set are the fixed recovery
identity and must not be moved, rewritten, or reused. GitHub's generated source
archives are views of this tagged tracked tree; no separately prepared archive
was uploaded. The Release title, notes, and latest/prerelease metadata are not
part of that fixed identity and may be corrected only under the bounded,
recorded exception in the release policy.

Creation and a bounded post-publication correction both requested
`make_latest=false`; that correction addressed only the requested floating
latest classification and preserved every fixed recovery-identity value listed
above. At the recorded verification time, GitHub's `releases/latest`
convenience endpoint nevertheless resolved this archive because it was the
repository's only published non-prerelease Release. That floating endpoint is
not release identity or a recovery source; consumers must use the exact tag and
URL above.

## Validated tracked snapshot

Immediately before tag creation, the tracked worktree was clean and
`HEAD == main == origin/main == 27a9fdd877533f933cde4818eba9c186d286c529`.
The exact tree passed the repository's `current-ci-equivalent` profile:

| Validation fact | Value |
| --- | --- |
| Validation status | `pass` |
| Runner version | `1.2.0` |
| Commands passed | `6/6` |
| Result SHA-256 | `6dc036755b21bafc0faef91eba1982b0c024700bf1e3ffdc21ca60e04ebdb465` |

The exact tracked paths were also checked for sensitive filenames and common
private-key/token signatures without emitting matching values. No content
signature matched; `.env.example` was the sole sensitive-name match and is the
documented configuration example. `detect-secrets` was unavailable in the
publication environment, so this bounded check is not represented as a
universal guarantee against secrets embedded in arbitrary free text.

## Implemented and absent capabilities

The archive contains the v1 configuration, logging, UTC and immutable domain
foundation; typed Binance USDⓈ-M public-history contracts; immutable local Raw
Parquet/manifest storage; bounded BTCUSDT/ETHUSDT 1m contract-, mark-price- and
index-price-Kline archive/REST acquisition; and bounded settled-funding
archive/REST acquisition.

It also contains the complete v1 LCK facade and kernel, workflow policies,
provider Skills, documentation, validation tooling, and tests. The tagged tree
therefore recovers both the business implementation and LCK without depending
on the later v2 tree.

The archive has no general research or feature pipeline, canonical data repair,
backtester, strategy, model training, experiment tracking, private exchange
API, order execution, account/position ledger, risk engine, database service,
Demo trading, Live trading, or multi-exchange production runtime. Live trading
is neither approved nor enabled. The tracked `apps/`, `packages/`, and
`deploy/` README files remain v1 future-boundary placeholders; they are not v2
initialization. The archived dependency set does not include NautilusTrader.

## External asset boundary

The tracked safe record is
[`tracequant-v1-retained-assets-manifest.json`](../research/tracequant-v1-retained-assets-manifest.json),
whose archived SHA-256 is
`582bed3de6a437335828c1e2918e8cec0f65a1d32539d2a93368dcb9406118e6`.
It records 8,166 retained files and 175,513,200 logical bytes. The protected
external archive SHA-256 is
`03bfb00e9fc0165e68def8c3c5daa4839cac5e64088d7869e1c9c90c1efec0b0`;
the protected full external manifest SHA-256 is
`c7c3a33fc67161e17db259cc467cdc3f247e3fd2cae95174747719e0e82cc92a`.

The retained payload, full path-level manifest, audit source checkouts, virtual
environments, caches, local workflow state, and any Secret material remain
outside Git and GitHub Release artifacts under the recovery owner's access
controls.

## Recovery

Fetch the exact annotated tag, verify that it peels to the commit above, and
inspect the tagged tree before use. The tag's `README.md`, technical baseline,
release policy, LCK overview, and LCK adoption guide define the archived
capabilities and limitations.

External retained assets are a separate controlled recovery path. Verify the
safe-manifest, protected-manifest, and archive digests before extracting them
into a new restricted location. They are not required to recover the tracked
business code or LCK from Git.
