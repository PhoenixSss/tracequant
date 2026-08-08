"""Focused validation for the Agent-neutral workflow / instruction architecture.

Task #120: shared semantic owner docs (docs/development/issue-workflow.md,
docs/development/pr-review.md), natural-language routing, dual-layer
source-of-truth model, and Codex / Claude skill semantic parity.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]

PATH_AUDIT = ROOT / "tools/agent_workflow/skill_path_audit.py"

ISSUE_WORKFLOW = ROOT / "docs/development/issue-workflow.md"
PR_REVIEW = ROOT / "docs/development/pr-review.md"
AGENTS = ROOT / "AGENTS.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
AGENT_SKILLS_GUIDE = ROOT / "docs/workflows/agent-skills.md"

ACTIVE_SKILL_ROOTS = (
    ROOT / ".agents" / "skills",
    ROOT / ".claude" / "skills",
)

REVIEW_SKILLS = ("task-pr-review-runner",)
LIFECYCLE_SKILLS = ("task-delivery-runner", "task-closeout", "feature-completion-audit")


def _skill_text(name: str) -> tuple[str, str]:
    return (
        (ROOT / ".agents" / "skills" / name / "SKILL.md").read_text(encoding="utf-8"),
        (ROOT / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8"),
    )


def test_shared_semantic_owner_docs_exist() -> None:
    assert ISSUE_WORKFLOW.is_file()
    assert PR_REVIEW.is_file()


def test_nl_routing_contract_resolves_to_dual_skill_paths() -> None:
    text = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    for entry in ("实现 Issue #N", "审查 PR #N", "PR #N 已人工合并，请完成 closeout"):
        assert entry in text
    for path in (
        "docs/development/pr-review.md",
        ".agents/skills/task-delivery-runner/",
        ".claude/skills/task-delivery-runner/",
        ".agents/skills/task-pr-review-runner/",
        ".claude/skills/task-pr-review-runner/",
        ".agents/skills/task-closeout/",
        ".claude/skills/task-closeout/",
        ".agents/skills/feature-completion-audit/",
        ".claude/skills/feature-completion-audit/",
    ):
        assert path in text
    assert "解析失败 / 歧义" in text
    assert "不要猜" in text
    assert "Human Gate" in text


def test_agents_md_routing_principle_is_shared_entry() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    assert "## Natural-language workflow entry" in text
    for entry in ("实现 Issue #N", "审查 PR #N", "PR #N 已人工合并，请完成 closeout"):
        assert entry in text
    assert "docs/development/issue-workflow.md" in text


def test_source_of_truth_is_dual_layer_not_linear_precedence() -> None:
    text = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    assert "Normative / semantic authority" in text
    assert "Mechanical / factual authority" in text
    assert "current leaf Issue body" in text
    assert "不得成为 business specification" in text
    assert "不得覆盖 current Issue requirement" in text


def test_failure_and_ambiguity_handling_is_defined() -> None:
    text = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    assert "Failure / Ambiguity handling" in text
    assert "fail closed" in text
    assert "invalidate review" in text
    assert "block closeout" in text
    assert "bounded remediation handoff" in text


def test_closeout_entry_owns_merge_identity_and_convergence() -> None:
    text = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    assert "Closeout" in text
    assert "merge identity" in text
    assert "reviewed head == 实际 merged head" in text


def test_pr_review_doc_owns_review_semantics() -> None:
    text = PR_REVIEW.read_text(encoding="utf-8")
    for marker in (
        "fresh session",
        "完全只读",
        "Head lock",
        "Effective diff",
        "Verdict semantics",
        "Remediation handoff",
        "block closeout",
    ):
        assert marker in text


def test_every_active_skill_references_its_shared_semantic_owner() -> None:
    for name in LIFECYCLE_SKILLS:
        codex, claude = _skill_text(name)
        assert "docs/development/issue-workflow.md" in codex
        assert "docs/development/issue-workflow.md" in claude
    for name in REVIEW_SKILLS:
        codex, claude = _skill_text(name)
        assert "docs/development/pr-review.md" in codex
        assert "docs/development/pr-review.md" in claude


def test_skill_path_audit_covers_both_roots_and_shared_docs() -> None:
    result = subprocess.run(
        [sys.executable, str(PATH_AUDIT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "pass"
    assert set(value["claude_skills"]) == set(value["active_skills"])
    for entry in value["active_skills"].values():
        assert entry["missing_shared_doc_refs"] == []
    for entry in value["claude_skills"].values():
        assert entry["missing_shared_doc_refs"] == []
        assert entry["direct_command_paths"] == []
        assert entry["evolution_traces"] == []
    assert value["shared_docs"] == {"issue-workflow": True, "pr-review": True}
    assert value["totals"]["shared_doc_ref_violations"] == 0
    assert value["totals"]["shared_doc_existence_violations"] == 0


def test_claude_md_is_thin_adapter_with_shared_navigation() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "docs/development/issue-workflow.md" in text
    assert "README" in text
    assert "### Permissions" in text
    assert "## Natural-language workflow entry" not in text


def test_agent_skills_guide_is_registry_and_navigation_only() -> None:
    text = AGENT_SKILLS_GUIDE.read_text(encoding="utf-8")
    assert "导航" in text
    assert ".agents/skills/task-delivery-runner/SKILL.md" in text
    assert ".claude/skills/task-delivery-runner/SKILL.md" in text
    assert "docs/development/issue-workflow.md" in text
    assert "docs/development/pr-review.md" in text
    assert "仓库外 Token 消耗分析边界" in text
    assert "## Review remediation" not in text
    assert "## Runner Delivery" not in text
    assert "## Independent Review" not in text


def test_legacy_skills_are_preserved_not_deleted() -> None:
    for relative in (
        ".agents/skills/task-delivery/SKILL.md",
        ".agents/skills/task-pr-review/SKILL.md",
    ):
        assert (ROOT / relative).is_file(), relative
    guide = AGENT_SKILLS_GUIDE.read_text(encoding="utf-8")
    assert "task-delivery" in guide
    assert "task-pr-review" in guide
