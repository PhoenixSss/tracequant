#!/usr/bin/env python3
"""Audit current Runner Skills for one-path contracts and evolution traces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 2
ACTIVE_SKILLS: Final = {
    "task-delivery-runner": Path(".agents/skills/task-delivery-runner/SKILL.md"),
    "task-pr-review-runner": Path(".agents/skills/task-pr-review-runner/SKILL.md"),
    "task-closeout": Path(".agents/skills/task-closeout/SKILL.md"),
    "feature-completion-audit": Path(".agents/skills/feature-completion-audit/SKILL.md"),
}
BASELINE_SKILLS: Final = {
    "task-delivery": Path(".agents/skills/task-delivery/SKILL.md"),
    "task-pr-review": Path(".agents/skills/task-pr-review/SKILL.md"),
}
REQUIRED: Final = {
    "task-delivery-runner": (
        "wsl2_github_evidence_runner.py",
        "wsl2_validation_runner.py",
        "delivery-readiness",
        "workflow-delivery",
        "Review remediation",
    ),
    "task-pr-review-runner": (
        "wsl2_github_evidence_runner.py",
        "wsl2_validation_runner.py",
        "workflow-review",
        "recheck",
        "Remediation handoff",
    ),
    "task-closeout": (
        "wsl2_github_evidence_runner.py",
        "wsl2_validation_runner.py",
        "closeout-readonly",
        "workflow-closeout",
        "recheck",
    ),
    "feature-completion-audit": (
        "feature-audit-snapshot",
        "feature-audit-recheck",
        "Audited main SHA",
    ),
}
DIRECT_COMMAND_FRAGMENTS: Final = (
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
EVOLUTION_TRACES: Final = (
    "trusted_runner.py",
    "--trusted-sha",
    "trusted base",
    "trusted-base",
    "trusted control",
    "predecessor",
    "bootstrap",
    "old chain",
    "old path",
    "legacy path",
    "task #85",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit(repo_root: Path) -> tuple[dict[str, object], int]:
    results: dict[str, object] = {}
    violations: list[str] = []
    totals = {
        "lines": 0,
        "evidence_runner_mentions": 0,
        "validation_runner_mentions": 0,
        "direct_command_path_count": 0,
        "evolution_trace_count": 0,
    }

    for name, relative in ACTIVE_SKILLS.items():
        path = repo_root / relative
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        missing = [token for token in REQUIRED[name] if token not in text]
        direct = [item for item in DIRECT_COMMAND_FRAGMENTS if item in text]
        traces = [item for item in EVOLUTION_TRACES if item in lowered]
        if missing:
            violations.append(f"{name}: missing {', '.join(missing)}")
        if direct:
            violations.append(f"{name}: direct command path {', '.join(direct)}")
        if traces:
            violations.append(f"{name}: evolution trace {', '.join(traces)}")
        entry = {
            "path": relative.as_posix(),
            "lines": len(text.splitlines()),
            "bytes": len(text.encode("utf-8")),
            "sha256": _sha256(text),
            "evidence_runner_mentions": text.count("wsl2_github_evidence_runner.py"),
            "validation_runner_mentions": text.count("wsl2_validation_runner.py"),
            "direct_command_paths": direct,
            "evolution_traces": traces,
            "missing_contract_tokens": missing,
        }
        results[name] = entry
        totals["lines"] += entry["lines"]
        totals["evidence_runner_mentions"] += entry["evidence_runner_mentions"]
        totals["validation_runner_mentions"] += entry["validation_runner_mentions"]
        totals["direct_command_path_count"] += len(direct)
        totals["evolution_trace_count"] += len(traces)

    baseline: dict[str, object] = {}
    for name, relative in BASELINE_SKILLS.items():
        path = repo_root / relative
        text = path.read_text(encoding="utf-8")
        if f"name: {name}" not in text:
            violations.append(f"{name}: baseline name changed")
        baseline[name] = {
            "path": relative.as_posix(),
            "lines": len(text.splitlines()),
            "bytes": len(text.encode("utf-8")),
            "sha256": _sha256(text),
        }

    output: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if not violations else "fail",
        "active_skills": results,
        "baseline_skills": baseline,
        "totals": totals,
        "violations": violations,
    }
    return output, 0 if not violations else 1


def main() -> int:
    output, returncode = audit(Path.cwd().resolve())
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
