"""Authoritative effective-diff calculations shared by LCK phases."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflow_common import is_sha

from .models import LckStopError


@dataclass(frozen=True)
class EffectiveDiff:
    """The file scope and patch identity for one effective candidate diff."""

    merge_base_sha: str
    effective_diff_sha256: str
    changed_files: tuple[str, ...]


def calculate_effective_diff(
    runner: Any,
    *,
    base_sha: str,
    head_ref: str,
    command_id_prefix: str,
    cwd: Path | None = None,
    include_index: bool = False,
) -> EffectiveDiff:
    """Calculate one effective diff for Review or a staged Delivery candidate.

    Review compares ``base...head``.  Delivery stages its candidate first, so
    it compares the same merge base to the index (whose tree contains both the
    committed HEAD and staged candidate content).
    """

    if not is_sha(base_sha):
        raise LckStopError("effective-diff base SHA is unavailable")
    if not include_index and not is_sha(head_ref):
        raise LckStopError("effective-diff head SHA is unavailable")

    merge_base = runner.run(
        ["git", "merge-base", base_sha, head_ref],
        command_id=f"{command_id_prefix}-merge-base",
        cwd=cwd,
    )
    merge_base_sha = merge_base.stdout.strip()
    if merge_base.returncode != 0 or not is_sha(merge_base_sha):
        raise LckStopError("effective-diff merge base is unavailable")

    diff_ref = merge_base_sha if include_index else f"{base_sha}...{head_ref}"
    diff_args = ["git", "diff"]
    if include_index:
        diff_args.append("--cached")
    diff_args.extend(
        ["--binary", "--full-index", "--no-ext-diff", "--no-textconv", diff_ref]
    )
    diff = runner.run(
        diff_args,
        command_id=f"{command_id_prefix}-effective-diff",
        cwd=cwd,
    )
    if diff.returncode != 0:
        raise LckStopError(
            "effective diff is unavailable: "
            + (diff.stderr.strip() or f"exit {diff.returncode}")
        )

    names_args = ["git", "diff"]
    if include_index:
        names_args.append("--cached")
    names_args.extend(["--name-only", diff_ref])
    names = runner.run(
        names_args,
        command_id=f"{command_id_prefix}-changed-files",
        cwd=cwd,
    )
    if names.returncode != 0:
        raise LckStopError("effective-diff changed-file inventory is unavailable")

    return EffectiveDiff(
        merge_base_sha=merge_base_sha,
        effective_diff_sha256=hashlib.sha256(
            diff.stdout.encode("utf-8", errors="replace")
        ).hexdigest(),
        changed_files=tuple(line for line in names.stdout.splitlines() if line),
    )
