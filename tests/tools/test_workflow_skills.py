from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
SKILLS = {
    "task-delivery": ROOT / ".agents/skills/task-delivery/SKILL.md",
    "task-pr-review": ROOT / ".agents/skills/task-pr-review/SKILL.md",
    "task-closeout": ROOT / ".agents/skills/task-closeout/SKILL.md",
    "feature-completion-audit": ROOT
    / ".agents/skills/feature-completion-audit/SKILL.md",
}


def test_skills_use_shared_evidence_and_validation_without_legacy_command_chain() -> None:
    for name, path in SKILLS.items():
        text = path.read_text(encoding="utf-8")
        assert f"name: {name}" in text
        assert ".agents/policies/workflow-evidence.md" in text
        assert "workflow_validation.py" in text
        assert len(text) < 12_000
        assert len(text.splitlines()) < 320
        assert "git status --short" not in text
        assert "gh pr view" not in text
        assert "git add ." not in text or "never use `git add .`" in text.casefold()


def test_review_and_audit_preserve_fixed_verdicts_and_trusted_control_plane() -> None:
    review = SKILLS["task-pr-review"].read_text(encoding="utf-8")
    assert "通过，可以人工合并" in review
    assert "有条件通过，不得合并" in review
    assert "不通过，需要修复" in review
    assert "trusted_runner.py" in review
    assert "new session" in review.casefold() or "新会话" in review

    audit = SKILLS["feature-completion-audit"].read_text(encoding="utf-8")
    assert "Feature 已完成，可以由维护者人工收尾" in audit
    assert "Feature 尚未完成，需要补充或修复 Task" in audit
    assert "证据不足，暂不能判定 Feature 完成" in audit
    assert "direct-child" in audit
    assert "Audited main SHA" in audit


def test_merge_and_feature_closeout_remain_manual_gates() -> None:
    delivery = SKILLS["task-delivery"].read_text(encoding="utf-8")
    review = SKILLS["task-pr-review"].read_text(encoding="utf-8")
    closeout = SKILLS["task-closeout"].read_text(encoding="utf-8")
    audit = SKILLS["feature-completion-audit"].read_text(encoding="utf-8")
    assert "This Skill must not perform" in delivery or "must not perform" in delivery
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
