# TraceQuant v1 immutable archive identity

TraceQuant v1 remains available as a historical, non-production recovery
snapshot after the v2 tracked-tree transition.

| Field | Immutable value |
| --- | --- |
| GitHub Release | [`tracequant-v1-archive-2026-09-12`](https://github.com/PhoenixSss/tracequant/releases/tag/tracequant-v1-archive-2026-09-12) |
| Release database ID | `387517006` |
| Annotated tag object | `1ed4507486fbb5280f9bced5833713b26e7d3512` |
| Peeled commit | `27a9fdd877533f933cde4818eba9c186d286c529` |
| Source tree | `8406076ce81a2476005e3c938abd4c5a088373c2` |
| Custom Release assets | None |

The tag name, tag object, peeled commit, source tree, Release target, and empty
custom-asset set are the fixed recovery identity. They must not be moved,
rewritten, deleted, or reused. The v2 baseline is a normal descendant commit;
deleting v1 paths from that descendant does not delete them from the archive.

The archived tree contains the complete v1 business implementation and its
then-current delivery controller, policies, documentation, tests, and tooling.
It does not contain NautilusTrader, a strategy, a general backtester, private
exchange access, order execution, account/position state, Demo trading, or Live
trading.

The protected v1 retained-asset archive remains outside Git under the recovery
owner's access controls. Its tar SHA-256 is
`03bfb00e9fc0165e68def8c3c5daa4839cac5e64088d7869e1c9c90c1efec0b0`; the
protected full manifest SHA-256 is
`c7c3a33fc67161e17db259cc467cdc3f247e3fd2cae95174747719e0e82cc92a`.
Those external assets are not required to recover the tracked v1 source.

Recovery must fetch the exact annotated tag, verify all identities above, and
inspect or extract it in a separate controlled location. The archive is
historical evidence, not production approval and not an input to the v2 runtime.
