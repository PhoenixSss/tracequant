#!/usr/bin/env python3
"""Extract and execute workflow tooling from a trusted Git commit.

Run this bootstrap from a trusted base/main context. It never changes refs,
GitHub state, tracked files, or branches; it writes only below the exact ignored
`.agents/evidence.local/` directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

SHA: Final = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
FILES: Final = {
    "evidence": (
        "tools/agent_workflow/workflow_common.py",
        "tools/agent_workflow/workflow_evidence.py",
    ),
    "validation": (
        "tools/agent_workflow/workflow_common.py",
        "tools/agent_workflow/workflow_validation.py",
    ),
    "evidence-runner": (
        "tools/agent_workflow/workflow_common.py",
        "tools/agent_workflow/workflow_evidence.py",
        "tools/agent_workflow/wsl2_github_evidence_runner.py",
        "tools/agent_workflow/wsl2_github_evidence_profiles.json",
        ".codex/rules/quant-system-wsl-evidence.rules",
    ),
    "validation-runner": (
        "tools/agent_workflow/workflow_common.py",
        "tools/agent_workflow/workflow_validation.py",
        "tools/agent_workflow/wsl2_validation_runner.py",
        "tools/agent_workflow/wsl2_validation_profiles.json",
        ".codex/rules/quant-system-wsl-validation.rules",
    ),
}
ENTRY: Final = {
    "evidence": "tools/agent_workflow/workflow_evidence.py",
    "validation": "tools/agent_workflow/workflow_validation.py",
    "evidence-runner": "tools/agent_workflow/wsl2_github_evidence_runner.py",
    "validation-runner": "tools/agent_workflow/wsl2_validation_runner.py",
}
FRONT_DOOR_TOOLS: Final = frozenset({"evidence-runner", "validation-runner"})


def _gitignore_patterns(repo_root: Path) -> set[str]:
    path = repo_root / ".gitignore"
    if not path.exists():
        return set()
    return {
        line.strip().removeprefix("./")
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "!"))
    }


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )


def _extract(
    repo_root: Path, trusted_sha: str, tool: str
) -> tuple[Path, Path, dict[str, Any]]:
    if ".agents/evidence.local/" not in _gitignore_patterns(repo_root):
        raise RuntimeError(".agents/evidence.local/ must be exactly Git ignored")
    verified = _run_git(repo_root, "rev-parse", "--verify", f"{trusted_sha}^{{commit}}")
    if verified.returncode != 0:
        raise RuntimeError("trusted SHA is not a valid commit")
    full_sha = verified.stdout.decode("utf-8", errors="replace").strip()
    root = repo_root / ".agents" / "evidence.local" / "trusted" / full_sha[:16] / tool
    root.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for repository_path in FILES[tool]:
        shown = _run_git(repo_root, "show", f"{full_sha}:{repository_path}")
        if shown.returncode != 0:
            raise RuntimeError(f"trusted commit does not contain {repository_path}")
        target = root / repository_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(shown.stdout)
        hashes[repository_path] = hashlib.sha256(shown.stdout).hexdigest()
    manifest = {
        "schema_version": 2,
        "trusted_sha": full_sha,
        "tool": tool,
        "entry": ENTRY[tool],
        "files": hashes,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return root, root / ENTRY[tool], manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute workflow evidence, validation, or their fixed front-door "
            "runners from a trusted Git commit."
        )
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--trusted-sha", required=True)
    parser.add_argument("--tool", choices=tuple(FILES), required=True)
    parser.add_argument("tool_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not SHA.fullmatch(args.trusted_sha):
        print("trusted runner error: invalid trusted SHA", file=sys.stderr)
        return 2
    repo_root = Path(args.repo_root).resolve()
    if args.tool in FRONT_DOOR_TOOLS and repo_root != Path.cwd().resolve():
        print(
            "trusted runner error: fixed front-door runners require the "
            "current repository root",
            file=sys.stderr,
        )
        return 2
    try:
        bundle_root, entry, manifest = _extract(repo_root, args.trusted_sha, args.tool)
    except RuntimeError as exc:
        print(f"trusted runner error: {exc}", file=sys.stderr)
        return 2
    tool_args = list(args.tool_args)
    if tool_args and tool_args[0] == "--":
        tool_args = tool_args[1:]
    env = os.environ.copy()
    env["WORKFLOW_TRUSTED_RUNNER_SHA"] = manifest["trusted_sha"]
    env["WORKFLOW_TRUSTED_TOOL_CONTENT_SHA256"] = manifest["files"][ENTRY[args.tool]]
    if args.tool in FRONT_DOOR_TOOLS:
        env["WORKFLOW_TRUSTED_BUNDLE_ROOT"] = str(bundle_root)
        env["WORKFLOW_TARGET_REPO_ROOT"] = str(repo_root)
    completed = subprocess.run(
        [sys.executable, str(entry), *tool_args],
        cwd=repo_root,
        env=env,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
