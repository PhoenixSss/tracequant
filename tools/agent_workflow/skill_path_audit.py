#!/usr/bin/env python3
"""Audit current Runner Skills for one-path contracts and evolution traces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 6
ACTIVE_SKILLS: Final = {
    "task-delivery-runner": Path(".agents/skills/task-delivery-runner/SKILL.md"),
    "task-pr-review-runner": Path(".agents/skills/task-pr-review-runner/SKILL.md"),
    "task-closeout": Path(".agents/skills/task-closeout/SKILL.md"),
    "feature-completion-audit": Path(
        ".agents/skills/feature-completion-audit/SKILL.md"
    ),
}
CLAUDE_SKILLS: Final = {
    "task-delivery-runner": Path(".claude/skills/task-delivery-runner/SKILL.md"),
    "task-pr-review-runner": Path(".claude/skills/task-pr-review-runner/SKILL.md"),
    "task-closeout": Path(".claude/skills/task-closeout/SKILL.md"),
    "feature-completion-audit": Path(
        ".claude/skills/feature-completion-audit/SKILL.md"
    ),
}
REQUIRED: Final = {
    "task-delivery-runner": (
        "tools/agent_workflow/lck.py",
        "delivery prepare",
        "delivery complete",
        "Critical Outcome",
        "remediation prepare",
        "remediation complete",
        "READY_FOR_NEW_REVIEW",
    ),
    "task-pr-review-runner": (
        "tools/agent_workflow/lck.py",
        "review prepare",
        "review complete",
        "READY_FOR_SEMANTIC_REVIEW",
        "REVIEW_STALE_HEAD",
        "REVIEW_STALE_BASE",
        "STOP_REQUIRED",
    ),
    "task-closeout": (
        "tools/agent_workflow/lck.py merge preflight",
        "tools/agent_workflow/lck.py closeout",
        "Business Delivery",
        "Cleanup",
        "This Skill never merges",
    ),
    "feature-completion-audit": (
        "feature-audit-snapshot",
        "feature-audit-recheck",
        "Audited main SHA",
    ),
}
SHARED_DOCS: Final = {
    "issue-workflow": Path("docs/development/issue-workflow.md"),
    "pr-review": Path("docs/development/pr-review.md"),
}
SHARED_DOC_REFS: Final = {
    "task-delivery-runner": ("docs/development/issue-workflow.md",),
    "task-pr-review-runner": ("docs/development/pr-review.md",),
    "task-closeout": ("docs/development/issue-workflow.md",),
    "feature-completion-audit": ("docs/development/issue-workflow.md",),
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
TASK_LEGACY_CONTROL_FRAGMENTS: Final = (
    "wsl2_github_evidence_runner.py",
    "closeout-readonly",
    "workflow-closeout",
    "review-remediation",
    "write_actions_allowed",
    "snapshot_id",
)
EVOLUTION_TRACES: Final = (
    "trusted_runner.py",
    "--trusted-sha",
    "trusted base",
    "trusted-base",
    "trusted control",
    "predecessor",
    "old chain",
    "old path",
    "legacy path",
    "task #85",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit_skill(
    name: str,
    relative: Path,
    repo_root: Path,
    violations: list[str],
    totals: dict[str, int],
) -> dict[str, object]:
    path = repo_root / relative
    text = path.read_text(encoding="utf-8")
    lowered = text.casefold()
    missing = [token for token in REQUIRED[name] if token not in text]
    direct = [item for item in DIRECT_COMMAND_FRAGMENTS if item in text]
    traces = [item for item in EVOLUTION_TRACES if item in lowered]
    legacy = (
        [item for item in TASK_LEGACY_CONTROL_FRAGMENTS if item in text]
        if name != "feature-completion-audit"
        else []
    )
    missing_refs = [ref for ref in SHARED_DOC_REFS[name] if ref not in text]
    if missing:
        violations.append(f"{relative}: missing {', '.join(missing)}")
    if direct:
        violations.append(f"{relative}: direct command path {', '.join(direct)}")
    if traces:
        violations.append(f"{relative}: evolution trace {', '.join(traces)}")
    if legacy:
        violations.append(f"{relative}: legacy control path {', '.join(legacy)}")
    if missing_refs:
        violations.append(
            f"{relative}: missing shared-doc reference {', '.join(missing_refs)}"
        )
    entry = {
        "path": relative.as_posix(),
        "lines": len(text.splitlines()),
        "bytes": len(text.encode("utf-8")),
        "sha256": _sha256(text),
        "evidence_runner_mentions": text.count("wsl2_github_evidence_runner.py"),
        "validation_runner_mentions": text.count("wsl2_validation_runner.py"),
        "direct_command_paths": direct,
        "evolution_traces": traces,
        "legacy_control_paths": legacy,
        "missing_contract_tokens": missing,
        "missing_shared_doc_refs": missing_refs,
    }
    totals["lines"] += entry["lines"]
    totals["evidence_runner_mentions"] += entry["evidence_runner_mentions"]
    totals["validation_runner_mentions"] += entry["validation_runner_mentions"]
    totals["direct_command_path_count"] += len(direct)
    totals["evolution_trace_count"] += len(traces)
    totals.setdefault("legacy_control_path_count", 0)
    totals["legacy_control_path_count"] += len(legacy)
    totals["shared_doc_ref_violations"] += len(missing_refs)
    return entry


def audit(repo_root: Path) -> tuple[dict[str, object], int]:
    results: dict[str, object] = {}
    claude_results: dict[str, object] = {}
    violations: list[str] = []
    totals = {
        "lines": 0,
        "evidence_runner_mentions": 0,
        "validation_runner_mentions": 0,
        "direct_command_path_count": 0,
        "evolution_trace_count": 0,
        "shared_doc_ref_violations": 0,
        "shared_doc_existence_violations": 0,
    }

    for name, relative in ACTIVE_SKILLS.items():
        results[name] = _audit_skill(name, relative, repo_root, violations, totals)
    for name, relative in CLAUDE_SKILLS.items():
        claude_results[name] = _audit_skill(
            name, relative, repo_root, violations, totals
        )

    shared_docs: dict[str, bool] = {}
    for key, relative in SHARED_DOCS.items():
        present = (repo_root / relative).is_file()
        shared_docs[key] = present
        if not present:
            violations.append(f"missing shared doc {relative}")
            totals["shared_doc_existence_violations"] += 1

    output: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if not violations else "fail",
        "active_skills": results,
        "claude_skills": claude_results,
        "shared_docs": shared_docs,
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
