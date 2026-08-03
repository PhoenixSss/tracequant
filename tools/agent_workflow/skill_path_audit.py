#!/usr/bin/env python3
"""Audit Task workflow Skills for fixed-runner integration and legacy paths."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 1
TASK_SKILLS: Final = {
    "task-delivery": Path(".agents/skills/task-delivery/SKILL.md"),
    "task-pr-review": Path(".agents/skills/task-pr-review/SKILL.md"),
    "task-closeout": Path(".agents/skills/task-closeout/SKILL.md"),
}
REQUIRED: Final = {
    "task-delivery": (
        "wsl2_github_evidence_runner.py",
        "wsl2_validation_runner.py",
        "delivery-readiness",
        "workflow-delivery",
    ),
    "task-pr-review": (
        "trusted_runner.py",
        "--tool evidence-runner",
        "--tool validation-runner",
        "workflow-review",
        "recheck",
    ),
    "task-closeout": (
        "wsl2_github_evidence_runner.py",
        "wsl2_validation_runner.py",
        "closeout-readonly",
        "workflow-closeout",
        "recheck",
    ),
}
LEGACY_COMMAND_FRAGMENTS: Final = (
    "python tools/agent_workflow/workflow_evidence.py",
    "python -X utf8 tools/agent_workflow/workflow_evidence.py",
    "python tools/agent_workflow/workflow_validation.py",
    "python -X utf8 tools/agent_workflow/workflow_validation.py",
    "gh pr view",
    "gh issue view",
    "gh api graphql",
    "uv lock --check",
    "uv run --frozen pytest",
    "uv run --frozen ruff",
    "uv run --frozen mypy",
    "git status --short",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit(repo_root: Path) -> tuple[dict[str, object], int]:
    results: dict[str, object] = {}
    violations: list[str] = []
    totals = {
        "lines": 0,
        "fixed_evidence_runner_mentions": 0,
        "fixed_validation_runner_mentions": 0,
        "trusted_runner_mentions": 0,
        "legacy_command_path_count": 0,
    }
    for name, relative in TASK_SKILLS.items():
        path = repo_root / relative
        text = path.read_text(encoding="utf-8")
        missing = [token for token in REQUIRED[name] if token not in text]
        legacy = [fragment for fragment in LEGACY_COMMAND_FRAGMENTS if fragment in text]
        if missing:
            violations.append(f"{name}: missing {', '.join(missing)}")
        if legacy:
            violations.append(f"{name}: legacy path {', '.join(legacy)}")
        entry = {
            "path": relative.as_posix(),
            "lines": len(text.splitlines()),
            "bytes": len(text.encode("utf-8")),
            "sha256": _sha256(text),
            "fixed_evidence_runner_mentions": (
                text.count("wsl2_github_evidence_runner.py")
                + text.count("--tool evidence-runner")
            ),
            "fixed_validation_runner_mentions": (
                text.count("wsl2_validation_runner.py")
                + text.count("--tool validation-runner")
            ),
            "trusted_runner_mentions": text.count("trusted_runner.py"),
            "legacy_command_paths": legacy,
            "missing_contract_tokens": missing,
        }
        results[name] = entry
        totals["lines"] += int(entry["lines"])
        totals["fixed_evidence_runner_mentions"] += int(
            entry["fixed_evidence_runner_mentions"]
        )
        totals["fixed_validation_runner_mentions"] += int(
            entry["fixed_validation_runner_mentions"]
        )
        totals["trusted_runner_mentions"] += int(entry["trusted_runner_mentions"])
        totals["legacy_command_path_count"] += len(legacy)

    output: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if not violations else "fail",
        "skills": results,
        "totals": totals,
        "violations": violations,
    }
    return output, 0 if not violations else 1


def main() -> int:
    repo_root = Path.cwd().resolve()
    output, returncode = audit(repo_root)
    print(
        json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
