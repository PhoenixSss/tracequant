"""Shared helpers for the task-65-round-2-v2 benchmark tests.

The benchmark tooling lives under ``benchmarks/task-65-round-2-v2/tooling``
(outside ``src/`` and ``tests/``, not on the default import path and not
covered by CI mypy), so tests add it to ``sys.path`` and import with
``# type: ignore[import-not-found]`` — the same convention as
``tests/tools/test_pr_resolve.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TOOLING = str(
    Path(__file__).parents[2] / "benchmarks" / "task-65-round-2-v2" / "tooling"
)
if TOOLING not in sys.path:
    sys.path.insert(0, TOOLING)

REPO_ROOT = Path(__file__).parents[2]

GIT_SHA_HEX = 40


def run_git_quiet(
    *args: str, cwd: Path = REPO_ROOT
) -> subprocess.CompletedProcess[str]:
    """Run one git command and assert success (test helper)."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed: {result.stderr.strip()}"
    )
    return result


def init_repo(path: Path) -> Path:
    """Create a throwaway git repository for validator/materializer tests."""
    path.mkdir(parents=True, exist_ok=True)
    run_git_quiet("init", "-q", "-b", "main", cwd=path)
    run_git_quiet("config", "user.email", "benchmark@example.com", cwd=path)
    run_git_quiet("config", "user.name", "Benchmark Test", cwd=path)
    return path


def commit_all(repo: Path, message: str) -> str:
    """Stage and commit everything; return the new HEAD SHA."""
    run_git_quiet("add", "-A", cwd=repo)
    run_git_quiet("commit", "-q", "-m", message, cwd=repo)
    return run_git_quiet("rev-parse", "HEAD", cwd=repo).stdout.strip()


def ls_tree_blob(repo: Path, commit: str, path: str) -> tuple[str, str]:
    """Return (mode, blob_id) of ``path`` at ``commit``; asserts existence."""
    result = run_git_quiet("ls-tree", commit, "--", path, cwd=repo)
    line = result.stdout.strip()
    assert line, f"path {path} missing at {commit}"
    parts = line.split("\t", 1)[0].split()
    assert parts[1] == "blob", line
    return parts[0], parts[2]


def cat_blob(repo: Path, blob_id: str) -> bytes:
    result = subprocess.run(
        ["git", "cat-file", "blob", blob_id],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    return result.stdout


def sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
