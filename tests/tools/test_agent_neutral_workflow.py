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


def test_pr_review_doc_has_tri_state_verdict_contract() -> None:
    text = PR_REVIEW.read_text(encoding="utf-8")
    # Tri-state verdict contract (aligned with the executable Review Skills).
    for verdict in ("通过，可以人工合并", "有条件通过，不得合并", "不通过，需要修复"):
        assert verdict in text
    # Conditional is explicitly not merge approval.
    assert "DO NOT MERGE" in text
    assert "CONDITIONAL" in text
    # partial / unknown objective gates map to conditional, not to pass.
    assert "partial" in text
    assert "unknown" in text
    # Semantic failure / gate fail / identity drift must not map to conditional.
    assert "identity drift" in text
    assert "FAIL" in text
    # Head change during review is invalidation, not conditional pass.
    assert "REVIEW INVALIDATED" in text
    # The binary-era claim that conditional pass does not exist must not return.
    assert "不产出「conditional pass」" not in text


def test_pr_review_doc_owns_full_mapping_and_skills_do_not_duplicate() -> None:
    owner = PR_REVIEW.read_text(encoding="utf-8")
    codex, claude = _skill_text("task-pr-review-runner")
    # The shared owner holds the full verdict mapping (incl. plan-limit 403).
    assert "Deterministic mapping principle" in owner
    assert "gate = `pass`" in owner
    assert "plan-limit `403`" in owner
    assert "REVIEW INVALIDATED — HEAD CHANGED" in owner
    # Both Skills reference the owner sections for verdict / remediation.
    for text in (codex, claude):
        assert "docs/development/pr-review.md" in text
        assert "§8" in text
        assert "§9" in text
    # Exact executable verdict tokens and the invalidation token are retained.
    for text in (codex, claude):
        for verdict in (
            "通过，可以人工合并",
            "有条件通过，不得合并",
            "不通过，需要修复",
        ):
            assert verdict in text
        assert "REVIEW INVALIDATED — HEAD CHANGED" in text
    # Neither Skill duplicates the full shared mapping table.
    for text in (codex, claude):
        assert "### Deterministic mapping" not in text
        assert "| Evidence `status` | Permitted verdict ceiling |" not in text
        assert "### Verdict rules" not in text


def test_issue_workflow_doc_keeps_identity_locking_and_no_version_gate() -> None:
    text = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    # Workflow identity locking is owned by the shared doc.
    for marker in (
        "Workflow identity locking",
        "必须锁定",
        "不得静默继续",
        "reviewed head",
        "merge SHA",
    ):
        assert marker in text
    # Skill / Runner version is not a default reload gate.
    for marker in (
        "Skill / Runner version is not itself a workflow gate",
        "不要求",
        "重新加载",
    ):
        assert marker in text
    # Old trusted-version mechanisms are named only as explicitly rejected.
    assert "不重新引入" in text
    assert "trusted Skill" in text
    assert "main-only Skill" in text
    assert "Skill hash as workflow state" in text
    assert "trusted_runner.py" not in text
    assert "--trusted-sha" not in text


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
    assert "tools/agent_workflow/skill_path_audit.py" in text
    assert "Final source-of-truth matrix" in text
    assert "task-skill-" + "variants.json" not in text
    assert "skill_variant_" + "provenance.py" not in text
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
