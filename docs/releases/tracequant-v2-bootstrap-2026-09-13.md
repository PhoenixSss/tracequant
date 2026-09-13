# TraceQuant v2 bootstrap dependency record

This record binds the initial clean v2 dependency boundary to the committed
lock and the official NautilusTrader release. It is reproducibility evidence,
not a trading-readiness claim.

This is a historical record of the initial product bootstrap. Task #333 later
approved repository-only LCK roots under `tools/lck/`, `tests/tools/lck/`,
`docs/workflows/lck/`, `docs/guides/lck/`, `.agents/`, `.claude/`, and `.codex/`.
Any wording from the initial bootstrap that excluded those workflow-tooling
roots is superseded; the product runtime and NautilusTrader boundaries below are
unchanged. The recorded lock digest is the initial bootstrap identity, not a
claim about a later lock containing development-only LCK dependencies.

| Field | Value |
| --- | --- |
| Distribution | `nautilus-trader==2.0.0rc4` |
| Registry | `https://pypi.org/simple` |
| Upstream tag | `v2.0.0rc4` |
| Upstream commit | `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` |
| Verified target | CPython 3.13, `linux-x86_64` |
| Official wheel | `nautilus_trader-2.0.0rc4-cp313-cp313-manylinux_2_34_x86_64.whl` |
| Wheel SHA-256 | `0c97c4385d55833fc3cce4934ca48a2a5fa0ea1d69d0a775b88c2bbf6fb79005` |
| `uv.lock` SHA-256 | `3ef96a95e4f4eaa276d8c84253659c2242a3fe008ec0beac2566a6f64c3ea083` |
| uv | `0.12.1` |

The committed `uv.toml` rejects a source build of `nautilus-trader`. The clean
verification command was:

```bash
uv sync --locked --dev --no-build-package nautilus-trader --no-cache
```

The sync downloaded the 64.3 MiB upstream distribution, built only the local
TraceQuant package, and installed NautilusTrader without invoking its build
backend. The installed distribution reported `2.0.0rc4`; its module resolved
from the generated `.venv/lib/python3.13/site-packages/nautilus_trader` path,
while TraceQuant resolved from the repository-owned `src/tracequant` path.
`direct_url.json` was absent for NautilusTrader, and its lock entry uses the
standard PyPI registry with official `files.pythonhosted.org` wheels rather than
a Git, path, editable, workspace, source override, fork, or private index.

Upstream source checkouts and audit environments remain outside the repository.
The generated `.venv` is disposable and ignored; it is not a source or
persistent runtime-data root. The bootstrap provides no strategy, catalog,
exchange connectivity, order submission, Demo mode, or Live mode.
