"""Focused validation for the Agent-neutral workflow / instruction architecture."""

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
ACTIVE_SKILL_ROOTS = (ROOT / ".agents/skills", ROOT / ".claude/skills")
REVIEW_SKILLS = ("task-pr-review-runner",)
LIFECYCLE_SKILLS = ("task-delivery-runner", "task-closeout", "feature-completion-audit")


def _skill_text(name: str) -> tuple[str, str]:
    return (
        (ROOT / ".agents/skills" / name / "SKILL.md").read_text(encoding="utf-8"),
        (ROOT / ".claude/skills" / name / "SKILL.md").read_text(encoding="utf-8"),
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


def test_failure_and_ambiguity_handling_has_review_stop_and_local_stale_results() -> (
    None
):
    text = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    assert "Failure / Ambiguity handling" in text
    assert "fail closed" in text
    assert "STOP_REQUIRED" in text
    assert "Human explicit remediation" in text
    assert "REVIEW_STALE_HEAD" in text
    assert "REVIEW_STALE_BASE" in text
    assert "block closeout" in text
    assert "bounded remediation handoff" not in text


def test_delivery_remediation_is_explicit_and_live_for_both_agents() -> None:
    for text in _skill_text("task-delivery-runner"):
        assert "tools/agent_workflow/lck.py remediation prepare" in text
        assert "tools/agent_workflow/lck.py remediation complete" in text
        assert "failed `review_id` locates semantic findings only" in text
        assert (
            "mechanical facts from the Review\nrecord are not write authorization"
            in text
        )
        assert "READY_FOR_NEW_REVIEW" in text
        assert (
            "archived evidence snapshots or\nlegacy command paths as a fallback" in text
        )
        assert "bounded mechanical handoff" in text
        assert "MUST NOT accept" in text


def test_closeout_entry_owns_merge_identity_and_convergence() -> None:
    text = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    assert "Closeout" in text
    assert "merge identity" in text
    assert "reviewed head == 实际 merged head" in text


def test_pr_review_doc_owns_lck_review_semantics() -> None:
    text = PR_REVIEW.read_text(encoding="utf-8")
    for marker in (
        "fresh",
        "read-only",
        "LCK live target resolution",
        "current effective diff",
        "current Task Contract",
        "fresh `ReviewCompleteSnapshot`",
        "REVIEW_STALE_HEAD",
        "REVIEW_STALE_BASE",
        "Verdict semantics",
        "Explicit Remediation",
        "Provider neutrality",
    ):
        assert marker in text


def test_pr_review_doc_has_binary_pass_fail_stop_contract() -> None:
    text = PR_REVIEW.read_text(encoding="utf-8")
    assert "READY_FOR_MERGE_PREFLIGHT" in text
    assert "FAIL / `不通过，需要修复`" in text
    assert "READY_FOR_HUMAN_MERGE" in text
    assert "STOP_REQUIRED" in text
    assert "不自动进入 Remediation" in text
    assert "Review → Delivery → Review 自动循环" in text
    assert "CONDITIONAL" in text  # named only as a forbidden bypass
    assert "有条件通过，不得合并" not in text


def test_pr_review_doc_removes_cross_phase_mechanical_handoff_authority() -> None:
    text = PR_REVIEW.read_text(encoding="utf-8")
    assert "Delivery 输出、旧 snapshot、expected SHA" in text
    assert "不能授权后续 Review / Remediation" in text
    assert "不是 Review Complete 的当前" in text
    assert "fresh Git / GitHub facts" in text
    assert "audit / diagnostic record" in text
    assert "不是当前机械授权" in text
    assert "semantic findings only" in text


def test_review_skills_share_binary_lck_contract() -> None:
    codex, claude = _skill_text("task-pr-review-runner")
    assert codex == claude
    for text in (codex, claude):
        assert "docs/development/pr-review.md" in text
        assert "tools/agent_workflow/lck.py review prepare" in text
        assert "tools/agent_workflow/lck.py review complete" in text
        assert "READY_FOR_SEMANTIC_REVIEW" in text
        assert "READY_FOR_MERGE_PREFLIGHT" in text
        assert "tools/agent_workflow/lck.py merge preflight" in text
        assert "READY_FOR_HUMAN_MERGE" in text
        assert "STOP_REQUIRED" in text
        assert "REVIEW_STALE_HEAD" in text
        assert "REVIEW_STALE_BASE" in text
        assert "通过，可以人工合并" in text
        assert "不通过，需要修复" in text
        assert "有条件通过，不得合并" not in text
        assert "workflow-review" not in text
        assert "recheck --snapshot-id" not in text
        assert "Remediation handoff" not in text


def test_issue_workflow_doc_keeps_phase_identity_preconditions_and_no_version_gate() -> (
    None
):
    text = ISSUE_WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "Workflow identity locking",
        "必须锁定",
        "不得静默继续",
        "reviewed head",
        "merge SHA",
    ):
        assert marker in text
    for marker in (
        "Skill / Runner version is not itself a workflow gate",
        "不要求",
        "重新加载",
    ):
        assert marker in text
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
    assert value["schema_version"] == 6
    assert set(value["claude_skills"]) == set(value["active_skills"])
    for entry in (*value["active_skills"].values(), *value["claude_skills"].values()):
        assert entry["missing_shared_doc_refs"] == []
        assert entry["direct_command_paths"] == []
        assert entry["evolution_traces"] == []
        assert entry["legacy_control_paths"] == []
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


def test_legacy_skills_are_retired_from_active_namespace() -> None:
    for relative in (".agents/skills/task-delivery", ".agents/skills/task-pr-review"):
        assert not (ROOT / relative).exists(), relative
    agents = AGENTS.read_text(encoding="utf-8")
    claude = CLAUDE_MD.read_text(encoding="utf-8")
    assert ".agents/skills/task-delivery/SKILL.md" not in agents
    assert ".agents/skills/task-pr-review/SKILL.md" not in agents
    assert ".agents/skills/task-delivery/" not in claude
    assert ".agents/skills/task-pr-review/" not in claude


def test_current_skills_do_not_reference_missing_agents_override() -> None:
    for skill_root in ACTIVE_SKILL_ROOTS:
        for name in (*REVIEW_SKILLS, *LIFECYCLE_SKILLS):
            text = (skill_root / name / "SKILL.md").read_text(encoding="utf-8")
            assert "AGENTS.override.md" not in text, (skill_root, name)


def test_workflow_evidence_policy_is_the_single_current_owner() -> None:
    assert (ROOT / ".agents/policies/workflow-evidence.md").is_file()
    assert not (ROOT / "docs/workflows/workflow-evidence.md").exists()
    for path in (AGENTS, CLAUDE_MD, ROOT / "README.md", AGENT_SKILLS_GUIDE):
        text = path.read_text(encoding="utf-8")
        assert "docs/workflows/workflow-evidence.md" not in text, path
