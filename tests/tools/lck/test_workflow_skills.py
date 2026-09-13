"""Contracts exposed by the canonical LCK workflow Skills."""

from __future__ import annotations

import json
from pathlib import Path

from tools.lck.skill_audit import SKILLS, audit

ROOT = Path(__file__).resolve().parents[3]


def _skill(name: str) -> str:
    return (ROOT / ".agents" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_lifecycle_commands_use_the_locked_project_python() -> None:
    combined = "\n".join(_skill(name) for name in SKILLS)
    assert "uv run --frozen python -m tools.lck delivery prepare" in combined
    assert "uv run --frozen python -m tools.lck review prepare" in combined
    assert "uv run --frozen python -m tools.lck merge preflight" in combined
    assert "tools/agent_workflow" not in combined


def test_delivery_skill_delegates_git_and_github_effects_to_lck() -> None:
    delivery = _skill("task-delivery-runner")
    assert "delivery prepare" in delivery
    assert "delivery complete" in delivery
    assert "remediation prepare" in delivery
    assert "remediation complete" in delivery
    for direct in ("git commit", "git push", "gh pr create"):
        assert direct not in delivery


def test_review_skill_is_read_only_and_requires_fresh_review() -> None:
    review = _skill("task-pr-review-runner")
    assert "review prepare" in review
    assert "review complete" in review
    assert "implementation-read-only" in review
    assert "Independent Review never modifies implementation" in review
    assert "REVIEW_STALE_HEAD" in review
    assert "REVIEW_STALE_BASE" in review


def test_closeout_never_merges_and_feature_audit_is_separate() -> None:
    closeout = _skill("task-closeout")
    feature = _skill("feature-completion-audit")
    assert "This Skill never merges" in closeout
    assert "merge preflight" in closeout
    assert "feature-audit-snapshot" in feature
    assert "feature-audit-recheck" in feature


def test_agent_permission_adapters_only_allow_the_stable_front_doors() -> None:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    serialized = json.dumps(settings, sort_keys=True)
    assert "python -m tools.lck" in serialized
    assert "tools/agent_workflow" not in serialized

    rules = (ROOT / ".codex/rules/tracequant-wsl-validation.rules").read_text(
        encoding="utf-8"
    )
    assert "tools.lck.wsl2_validation_runner" in rules


def test_path_audit_reports_clean_canonical_skills_and_adapters() -> None:
    report, returncode = audit(ROOT)
    assert returncode == 0, report["violations"]
    assert report["status"] == "pass"
