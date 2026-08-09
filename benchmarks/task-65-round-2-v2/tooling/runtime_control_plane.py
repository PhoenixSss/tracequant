"""Mechanical runtime-control-plane path classification.

The runtime projection is evaluated against a repository tree, not only the
paths that a historical manifest happened to enumerate.  This module defines
the bounded repository-owned control-plane universe used by pinned manifests,
current-generation run-locking, and the control-base validator.

Paths outside these classes remain ordinary business-base paths and are
allowed to inherit without a projection action.  A path inside the universe
must be covered by exactly one manifest projection entry; an entry may be an
exact file path or an explicit directory absence sentinel such as ``.claude``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from benchmark_common import BenchmarkError, run_git

# Ownership is intentionally narrower than the entire repository.  In
# particular, tests/benchmarks are Task #125 business tooling, while
# tests/tools are generation-specific workflow validation inputs.
RUNTIME_CONTROL_PLANE_PATH_CLASSES: tuple[tuple[str, str], ...] = (
    ("AGENTS.md", "IDENTITY_REQUIRED"),
    ("CLAUDE.md", "IDENTITY_REQUIRED"),
    (".agents/execution-profile.example.toml", "VALIDATION_PRESENCE_REQUIRED"),
    (".agents/policies/", "VALIDATION_PRESENCE_REQUIRED"),
    (".agents/skills/", "VALIDATION_PRESENCE_REQUIRED"),
    (".claude/", "VALIDATION_PRESENCE_REQUIRED"),
    (".codex/", "VALIDATION_PRESENCE_REQUIRED"),
    ("tools/agent_workflow/", "EXECUTION_REQUIRED"),
    ("tests/tools/", "VALIDATION_PRESENCE_REQUIRED"),
    ("docs/development/issue-workflow.md", "IDENTITY_REQUIRED"),
    ("docs/development/pr-review.md", "IDENTITY_REQUIRED"),
)


def control_plane_role(path: str) -> str | None:
    """Return the canonical role for a path, or ``None`` for business data."""
    for pattern, role in RUNTIME_CONTROL_PLANE_PATH_CLASSES:
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return role
        elif path == pattern:
            return role
    return None


def tree_paths(repo_root: Path, commit: str) -> set[str]:
    """Return all regular-file paths in a commit tree."""
    result = run_git(repo_root, "ls-tree", "-r", "--name-only", commit)
    if result.returncode != 0:
        raise BenchmarkError(f"git ls-tree failed: {result.stderr.strip()}")
    return {line for line in result.stdout.splitlines() if line.strip()}


def runtime_control_plane_paths(repo_root: Path, commit: str) -> set[str]:
    """Derive the complete control-plane file set at ``commit``."""
    return {path for path in tree_paths(repo_root, commit) if control_plane_role(path)}


def managed_runtime_control_plane_paths(
    repo_root: Path,
    business_base_sha: str,
    generation_source_sha: str,
) -> set[str]:
    """Return the union that a historical projection must classify.

    The business base contributes current-only paths which must not silently
    inherit.  The generation source contributes historical paths which must
    not disappear from the projection merely because the current tree no
    longer contains them.
    """
    return runtime_control_plane_paths(repo_root, business_base_sha) | (
        runtime_control_plane_paths(repo_root, generation_source_sha)
    )


def covering_entry_paths(entries: Iterable[str], managed_path: str) -> list[str]:
    """Return exact/prefix projection entries covering one managed path."""
    return sorted(
        entry
        for entry in entries
        if managed_path == entry or managed_path.startswith(entry + "/")
    )
