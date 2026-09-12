# TraceQuant v1 retirement gate

Research Outcome: IMPLEMENT

## Verdict

**V1_RETIREMENT_COMPLETE**

The read-only gate was reacquired at `2026-09-12T12:48:53Z`. All required
identity, retained-asset, repository, planning-state, and recovery checks
passed. There are no blocking facts owned by preceding v1 retirement items.
This verdict authorizes only the next blocked v2 documentation item, Issue
#316; it does not authorize v2 implementation, Feature completion, asset
cleanup, or Live trading.

## Gate evidence

| Gate | Current evidence | Result |
| --- | --- | --- |
| Immutable Git and Release identity | Remote tag `tracequant-v1-archive-2026-09-12` still resolves to annotated tag object `1ed4507486fbb5280f9bced5833713b26e7d3512`, peels to commit `27a9fdd877533f933cde4818eba9c186d286c529`, and has tree `8406076ce81a2476005e3c938abd4c5a088373c2`. GitHub Release database ID `387517006` is published, non-draft, non-prerelease, targets the same commit, and has no custom assets. | PASS |
| External retained assets and Secret boundary | The Git-safe manifest digest remains `582bed3de6a437335828c1e2918e8cec0f65a1d32539d2a93368dcb9406118e6`. The protected full manifest, tar, and source-removal record retain digests `c7c3a33fc67161e17db259cc467cdc3f247e3fd2cae95174747719e0e82cc92a`, `03bfb00e9fc0165e68def8c3c5daa4839cac5e64088d7869e1c9c90c1efec0b0`, and `2c56225c213f4309c01af5bb1edb249d352a1020de400b26fb3abcc5144bcb14`. A fresh protected verification matched all 8,166 regular files and 175,513,200 logical bytes by relative path, mode, size, and SHA-256 in both payload and tar. The external root remains `0700`; protected files remain `0600`. No protected path or value was emitted. | PASS |
| Repository cleanliness and local ownership | Before creating this report, the tracked worktree at current `main` baseline `d7eef4ba1c8e9699a57d1b6d697b1d5917a73103` was clean and had zero ordinary-untracked entries. All 8,415 ignored entries were classified: uv cache 5,414; virtual environment 2,699; tool caches 35; Python bytecode 135; distribution outputs 3; post-transfer LCK outputs 92; post-transfer Validation outputs 37. There were zero unresolved entries, and neither the migrated historical Evidence root nor the Task #86 freeze source remained in the repository. | PASS |
| Issue, PR, and Project convergence | The 42 retired v1 planning Issues recorded by Issue #321 remain `CLOSED / NOT_PLANNED` with Project Status `Done`. PR #311 remains closed without merge (`mergedAt = null`), and there was no open PR at acquisition time. Preceding Issues #318-#321 remain `CLOSED / COMPLETED / Done`. The Project contained 189 items; its only eight non-`Done` items were the finite retirement/v2 transition set #312-#317, #322, and #323. Their states matched the transition contract, including #314 `In Progress`, #315 `Specifying`, and the v2 leaves `Blocked`; no pre-retirement v1 backlog item remained active. | PASS |
| Business code and LCK recovery | An isolated clone of the exact immutable tag recovered 183 tracked files at the commit and tree above. The `tracequant` business package and `tools/agent_workflow/lck.py` were present, and the archived LCK CLI started successfully. The recovered tree passed its six-command `current-ci-equivalent` profile (lock check, pytest, Ruff check, Ruff format check, mypy, and Git diff check) with Runner `1.2.0`; fresh result digest `83d835853f286d2cf4a7c1ce6d469636520bf114d5354919059fdc45ffbc3434`. The temporary recovery clone was then removed; the current repository HEAD and tracked tree were unchanged. | PASS |

## Boundaries

- The audit did not remediate or move assets, edit retirement/planning objects,
  create a tag or Release, initialize v2, repeat NautilusTrader selection
  research, or assess LCK/business Feature completion.
- LCK's workflow-owned transition of Issue #314 from `Ready` to `In Progress`
  is Delivery control state, not a retirement-state mutation or part of the
  gate evidence.
- Post-transfer `.workflow.local/lck/**` and
  `.agents/validation.local/**` files are new workflow-owned outputs. They are
  not silently appended to the immutable Issue #320 retained-asset manifest.
- The GitHub and Project facts are a timestamped acquisition. Any later
  identity mismatch, reopened v1 item, new non-transition planning item,
  retained-asset checksum failure, access-control widening, or recovery failure
  invalidates this verdict and requires the gate to stop.

## Reproduction summary

The audit used `git ls-remote`, `git cat-file`, and exact tag/tree resolution;
GitHub CLI read-only Release, Issue, PR, and Project queries; Git-native tracked,
untracked, and ignored inventories; SHA-256 plus path/mode/size verification of
the protected manifest, payload, and tar without extraction into the working
repository; and the archived repository's own
`wsl2_validation_runner.py current-ci-equivalent` in an isolated temporary
clone. The tracked publication and migration records remain the safe durable
inputs; the protected path-level manifest remains outside Git.
