from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
TASK_SKILLS = {
    "task-delivery": ROOT / ".agents/skills/task-delivery/SKILL.md",
    "task-pr-review": ROOT / ".agents/skills/task-pr-review/SKILL.md",
    "task-closeout": ROOT / ".agents/skills/task-closeout/SKILL.md",
}
AUDIT_SKILL = ROOT / ".agents/skills/feature-completion-audit/SKILL.md"
PATH_AUDIT = ROOT / "tools/agent_workflow/skill_path_audit.py"


def test_task_skills_use_fixed_runners_without_legacy_command_chain() -> None:
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
    )
    for name, path in TASK_SKILLS.items():
        text = path.read_text(encoding="utf-8")
        assert f"name: {name}" in text
        assert ".agents/policies/workflow-evidence.md" in text
        assert (
            "wsl2_github_evidence_runner.py" in text
            or "--tool evidence-runner" in text
        )
        assert (
            "wsl2_validation_runner.py" in text
            or "--tool validation-runner" in text
        )
        assert len(text) < 12_000
        assert len(text.splitlines()) < 320
        for fragment in forbidden:
            assert fragment not in text
        assert "git add ." not in text or "never use `git add .`" in text.casefold()
        assert "telemetry" not in text.casefold()
        assert "active run" not in text.casefold()


def test_delivery_uses_distinct_preflight_readiness_and_phase_validation() -> None:
    text = TASK_SKILLS["task-delivery"].read_text(encoding="utf-8")
    assert "--expected-main-sha" in text
    assert "delivery-readiness" in text
    assert "workflow-delivery" in text
    assert "targeted:workflow-tests" in text
    assert "Do not also run" in text
    assert "full direct `uv` validation chain" in text


def test_review_uses_trusted_base_fixed_front_doors_and_fixed_verdicts() -> None:
    text = TASK_SKILLS["task-pr-review"].read_text(encoding="utf-8")
    assert "--tool evidence-runner" in text
    assert "--tool validation-runner" in text
    assert "workflow-review" in text
    assert "recheck --snapshot-id" in text
    assert "predecessor base control plane" in text
    assert "通过，可以人工合并" in text
    assert "有条件通过，不得合并" in text
    assert "不通过，需要修复" in text
    assert "new session" in text.casefold() or "新会话" in text
    assert "never fixes files" in text


def test_closeout_uses_readonly_evidence_and_closeout_validation_only() -> None:
    text = TASK_SKILLS["task-closeout"].read_text(encoding="utf-8")
    assert "closeout-readonly" in text
    assert "workflow-closeout" in text
    assert "recheck --snapshot-id" in text
    assert "This Skill never merges" in text
    assert "manual Issue close" in text
    assert "exact verified remote/local Task branch" in text


def test_failure_unknown_and_drift_have_bounded_expansion_contract() -> None:
    for path in TASK_SKILLS.values():
        text = path.read_text(encoding="utf-8").casefold()
        assert "partial" in text
        assert "unknown" in text
        assert "drift" in text
        assert "bounded" in text
        assert "legacy path" in text or "legacy" in text


def test_path_audit_reports_zero_legacy_task_skill_paths() -> None:
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
    assert value["totals"]["legacy_command_path_count"] == 0
    assert value["totals"]["fixed_evidence_runner_mentions"] >= 3
    assert value["totals"]["fixed_validation_runner_mentions"] >= 3


def test_feature_audit_remains_outside_task85_migration() -> None:
    text = AUDIT_SKILL.read_text(encoding="utf-8")
    assert "name: feature-completion-audit" in text
    assert ".agents/policies/workflow-evidence.md" in text
    assert "workflow_validation.py" in text
    assert "Feature 已完成，可以由维护者人工收尾" in text
    assert "Feature 尚未完成，需要补充或修复 Task" in text
    assert "证据不足，暂不能判定 Feature 完成" in text
    assert "direct-child" in text
    assert "Audited main SHA" in text


def test_merge_and_feature_closeout_remain_manual_gates() -> None:
    delivery = TASK_SKILLS["task-delivery"].read_text(encoding="utf-8")
    review = TASK_SKILLS["task-pr-review"].read_text(encoding="utf-8")
    closeout = TASK_SKILLS["task-closeout"].read_text(encoding="utf-8")
    audit = AUDIT_SKILL.read_text(encoding="utf-8")
    assert "must not perform" in delivery
    assert "never fixes files" in review
    assert "This Skill never merges" in closeout
    assert "maintainer" in audit and "performs none" in audit


def test_local_workflow_artifact_directories_are_exactly_ignored() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".agents/evidence.local/" in patterns
    assert ".agents/validation.local/" in patterns
