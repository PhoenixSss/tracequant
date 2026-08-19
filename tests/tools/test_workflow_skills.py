from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
ACTIVE_SKILLS = {
    "task-delivery-runner": ROOT / ".agents/skills/task-delivery-runner/SKILL.md",
    "task-pr-review-runner": ROOT / ".agents/skills/task-pr-review-runner/SKILL.md",
    "task-closeout": ROOT / ".agents/skills/task-closeout/SKILL.md",
    "feature-completion-audit": ROOT
    / ".agents/skills/feature-completion-audit/SKILL.md",
}
PATH_AUDIT = ROOT / "tools/agent_workflow/skill_path_audit.py"


def test_current_runner_skills_use_one_mechanical_path() -> None:
    forbidden = (
        "python tools/agent_workflow/workflow_evidence.py",
        "python -X utf8 tools/agent_workflow/workflow_evidence.py",
        "python tools/agent_workflow/workflow_validation.py",
        "python -X utf8 tools/agent_workflow/workflow_validation.py",
        "gh pr view",
        "gh issue view",
        "uv lock --check",
        "uv run --frozen pytest",
        "git status --short",
        "trusted_runner.py",
        "--trusted-sha",
    )
    for name, path in ACTIVE_SKILLS.items():
        text = path.read_text(encoding="utf-8")
        assert f"name: {name}" in text
        assert ".agents/policies/workflow-evidence.md" in text
        assert len(text) < 28_000
        assert len(text.splitlines()) < 550
        for fragment in forbidden:
            assert fragment not in text
        assert "telemetry" not in text.casefold()


def test_delivery_runner_has_current_profiles_and_remediation_loop() -> None:
    text = ACTIVE_SKILLS["task-delivery-runner"].read_text(encoding="utf-8")
    assert "--expected-main-sha" in text
    assert "--entry-point" in text
    assert "delivery-readiness" in text
    assert "workflow-delivery" in text
    assert "targeted:workflow-tests" in text
    assert "## Review remediation" in text
    assert "reviewed head SHA" in text
    assert "Low or Nit" in text
    assert "new independent review" in text
    assert "git add ." in text and "do not use" in text
    assert "lifecycle conflict" in text.casefold()
    assert "`review-remediation` requires the bounded handoff" in text
    assert "stop before Runner or repair writes" in text
    assert "branch_bootstrap" in text
    assert "--bootstrap-verify" in text


def test_delivery_branch_bootstrap_contract_is_shared_by_both_skills() -> None:
    for relative in (
        ".agents/skills/task-delivery-runner/SKILL.md",
        ".claude/skills/task-delivery-runner/SKILL.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8").casefold()
        for phrase in (
            "branch_bootstrap = pass",
            "task/<issue number>-<slug>",
            "--bootstrap-verify",
            "fail closed",
            "existing numeric",
            "branch forms may be reused",
        ):
            assert phrase in text, (relative, phrase)


def test_review_runner_is_read_only_and_emits_bounded_remediation_handoff() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "workflow-review" in text
    assert "recheck --snapshot-id" in text
    assert "通过，可以人工合并" in text
    assert "有条件通过，不得合并" in text
    assert "不通过，需要修复" in text
    assert "new session" in text.casefold()
    assert "strictly read-only" in text
    assert "## Remediation handoff" in text
    assert "Required remediation:" in text
    assert "Objective gates:" in text
    assert "Maintainer decision required:" in text
    assert "task-delivery-runner 修复" in text


def test_closeout_and_feature_audit_keep_manual_gates() -> None:
    closeout = ACTIVE_SKILLS["task-closeout"].read_text(encoding="utf-8")
    audit = ACTIVE_SKILLS["feature-completion-audit"].read_text(encoding="utf-8")
    assert "closeout-readonly" in closeout
    assert "workflow-closeout" in closeout
    assert "This Skill never merges" in closeout
    assert "cleanup-only" in closeout
    assert "eligible-under-capability-limited-policy" in closeout
    assert "Feature 已完成，可以由维护者人工收尾" in audit
    assert "Feature 尚未完成，需要补充或修复 Task" in audit
    assert "证据不足，暂不能判定 Feature 完成" in audit
    assert "Audited main SHA" in audit
    assert "performs none" in audit


def test_active_skills_have_bounded_failure_contract_without_evolution_traces() -> None:
    forbidden_traces = (
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
    for path in ACTIVE_SKILLS.values():
        text = path.read_text(encoding="utf-8").casefold()
        assert "partial" in text
        assert "unknown" in text
        assert "drift" in text
        assert "bounded" in text or "only the named" in text
        for trace in forbidden_traces:
            assert trace not in text


def test_path_audit_reports_only_clean_current_skills() -> None:
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
    assert value["totals"]["direct_command_path_count"] == 0
    assert value["totals"]["evolution_trace_count"] == 0
    assert "baseline_skills" not in value


def test_legacy_skill_directories_are_absent() -> None:
    for relative in (
        ".agents/skills/task-delivery",
        ".agents/skills/task-pr-review",
    ):
        assert not (ROOT / relative).exists(), relative


def test_local_workflow_artifact_directories_are_exactly_ignored() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".agents/evidence.local/" in patterns
    assert ".agents/validation.local/" in patterns


# --- Evidence verdict matrix tests ---


def test_review_skill_has_shared_owner_contract_and_exact_tokens() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    # Shared semantic owner referenced for verdict and remediation conditions.
    assert "docs/development/pr-review.md" in text
    assert "§8" in text
    assert "§9" in text
    # Exact executable verdict output tokens are retained.
    assert "通过，可以人工合并" in text
    assert "有条件通过，不得合并" in text
    assert "不通过，需要修复" in text
    # Head-change invalidation retained as a hard guard.
    assert "REVIEW INVALIDATED — HEAD CHANGED" in text
    # Executable procedure retained.
    assert "workflow-review" in text
    assert "recheck --snapshot-id" in text
    # The full shared verdict mapping table is no longer duplicated in the Skill.
    assert "### Deterministic mapping" not in text
    assert "| Evidence `status` | Permitted verdict ceiling |" not in text


def test_review_skill_requires_remediation_handoff_for_non_pass() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "Enforcement" in text
    assert "non-passing verdict" in text.casefold()
    assert "Delivery prompt" in text
    assert "non-compliant" in text
    assert "task-delivery-runner 修复" in text


def test_review_skill_has_semantic_review_evidence_matrix() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "evidence matrix" in text.casefold()
    assert "changed_file_groups" in text
    assert "acceptance_criteria" in text
    assert "effective_diff_sha256" in text
    assert "overall" in text
    assert "verified | partial | not_verified" in text


def test_review_skill_requires_file_coverage_completeness() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "changed file" in text.casefold()
    assert "one group" in text
    assert "not covered" in text.casefold()


def test_review_skill_has_mechanical_assertions_table() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "Mechanical assertions" in text
    assert "Historical Skill matches source commit blob" in text
    assert "All target Skills are canonical-state" in text
    assert "byte-for-byte" in text.casefold()
    assert "must not be enlarged" in text


def test_review_skill_has_tool_discipline_section() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "## Tool discipline" in text
    assert "File existence" in text
    assert "Tool availability" in text
    assert "Runner result independence" in text
    assert "Search completeness" in text


def test_review_skill_has_minimal_verdict_summary_without_duplicated_rules() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    # Minimal semantic summary with authoritative reference, not a full rule set.
    assert "pr-review.md" in text
    assert "PASS is the only mergeable state" in text
    assert "CONDITIONAL is never mergeable" in text
    assert "### Verdict rules" not in text


def test_review_skill_runner_command_includes_skill_path() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "--skill-path" in text
    assert ".claude/skills/task-pr-review-runner/SKILL.md" in text


def test_claude_skill_differs_from_agents_skill_only_in_execution_details() -> None:
    """Both Skills share business semantics; only platform execution differs."""
    claude_text = (ROOT / ".claude/skills/task-pr-review-runner/SKILL.md").read_text(
        encoding="utf-8"
    )
    agents_text = (ROOT / ".agents/skills/task-pr-review-runner/SKILL.md").read_text(
        encoding="utf-8"
    )
    # Both have the same verdict structure
    for phrase in (
        "通过，可以人工合并",
        "有条件通过，不得合并",
        "不通过，需要修复",
        "Remediation handoff",
        "Required remediation:",
        "Objective gates:",
        "Maintainer decision required:",
        "evidence matrix",
        "changed_file_groups",
        "acceptance_criteria",
        "Conditional pass",
    ):
        assert phrase in claude_text
        assert phrase in agents_text
    # Both prohibit upgrading partial/unknown to unconditional pass
    assert "never" in claude_text.casefold()
    assert "never" in agents_text.casefold()
    # Claude Skill references .claude paths
    assert ".claude/skills/task-pr-review-runner/SKILL.md" in claude_text
    # Agents Skill references .agents paths
    assert ".agents/skills/task-pr-review-runner/SKILL.md" in agents_text
