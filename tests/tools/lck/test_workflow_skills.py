"""Contracts exposed by the canonical LCK workflow Skills."""

from __future__ import annotations

import json
from pathlib import Path

from tools.lck.skill_audit import SKILLS, audit

ROOT = Path(__file__).resolve().parents[3]
LOCKED_PYTHON = "uv run --frozen python"


def _skill(name: str) -> str:
    return (ROOT / ".agents" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def _skill_package(name: str) -> str:
    skill_root = ROOT / ".agents" / "skills" / name
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(skill_root.rglob("*.md"))
    )


def _description(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("description: "):
            return line.removeprefix("description: ")
    raise AssertionError("Skill has no description")


def test_lifecycle_commands_use_the_locked_project_python() -> None:
    combined = "\n".join(_skill_package(name) for name in SKILLS)
    assert f"{LOCKED_PYTHON} -m tools.lck delivery prepare" in combined
    assert f"{LOCKED_PYTHON} -m tools.lck review prepare" in combined
    assert f"{LOCKED_PYTHON} -m tools.lck refresh <TASK>" in combined
    assert f"{LOCKED_PYTHON} -m tools.lck merge preflight" in combined
    assert "tools/agent_workflow" not in combined


def test_feature_audit_documents_complete_locked_module_commands() -> None:
    feature = _skill("feature-completion-audit")
    assert (
        f"{LOCKED_PYTHON} -m tools.lck.feature_audit feature-audit-snapshot \\\n"
        "  --feature <FEATURE> --expected-main-sha <SHA>"
    ) in feature
    assert (
        f"{LOCKED_PYTHON} -m tools.lck.validation_runner run \\\n"
        "  --phase feature-audit --include-skill-validators "
        "--require-skill-validator"
    ) in feature
    assert (
        f"{LOCKED_PYTHON} -m tools.lck.feature_audit feature-audit-recheck \\\n"
        "  --snapshot-id <SNAPSHOT_ID>"
    ) in feature
    assert "\npython -m tools.lck" not in feature


def test_active_evidence_policy_uses_locked_module_front_doors() -> None:
    policy = (ROOT / ".agents" / "policies" / "workflow-evidence.md").read_text(
        encoding="utf-8"
    )
    for command in (
        "-m tools.lck delivery prepare|complete",
        "-m tools.lck review prepare|complete",
        "-m tools.lck remediation prepare|no-change|complete",
        "-m tools.lck refresh <TASK>",
        "-m tools.lck.wsl2_validation_runner <PROFILE>",
        "-m tools.lck.feature_audit feature-audit-snapshot",
        "-m tools.lck.feature_audit feature-audit-recheck",
    ):
        assert f"{LOCKED_PYTHON} {command}" in policy
    assert "\npython -m tools.lck" not in policy


def test_agent_skills_guide_documents_module_audit_and_current_fields() -> None:
    guide = (ROOT / "docs" / "workflows" / "lck" / "agent-skills.md").read_text(
        encoding="utf-8"
    )
    assert f"{LOCKED_PYTHON} -m tools.lck.skill_audit" in guide
    assert "`canonical_skills` 与 `adapters`" in guide
    assert "tools/lck/skill_audit.py\n" not in guide
    assert "`active_skills` 与 `claude_skills`" not in guide


def test_delivery_skill_delegates_git_and_github_effects_to_lck() -> None:
    delivery = _skill("task-delivery-runner")
    assert "delivery prepare" in delivery
    assert "delivery complete" in delivery
    assert "remediation prepare" in delivery
    assert "remediation complete" in delivery
    assert "refresh <TASK>" in delivery
    assert "refresh prepare" not in delivery
    assert "refresh complete" not in delivery
    assert "refresh abort" not in delivery
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
    assert f"{LOCKED_PYTHON} -m tools.lck.wsl2_validation_runner" in serialized
    assert "tools/agent_workflow" not in serialized

    rules = (ROOT / ".codex/rules/tracequant-wsl-validation.rules").read_text(
        encoding="utf-8"
    )
    assert "tools.lck.wsl2_validation_runner" in rules


def test_path_audit_reports_clean_canonical_skills_and_adapters() -> None:
    report, returncode = audit(ROOT)
    assert returncode == 0, report["violations"]
    assert report["status"] == "pass"


def test_workflow_skills_follow_astra_content_guidance() -> None:
    descriptions = {
        "task-delivery-runner": (
            "Deliver a ready leaf Issue, remediate an explicitly identified failed Review, "
            "or refresh an existing Review candidate onto current main when the maintainer "
            "explicitly requests it."
        ),
        "task-pr-review-runner": (
            "Independently review the current open PR for a maintainer-specified leaf "
            "Issue in a fresh session."
        ),
        "task-closeout": (
            "Complete post-merge closeout for a maintainer-specified leaf Issue after "
            "the maintainer says its PR was manually Squash Merged."
        ),
        "feature-completion-audit": (
            "Independently audit whether a maintainer-specified open Feature is complete "
            "on current main before manual Feature closeout."
        ),
    }
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for name, expected_description in descriptions.items():
        canonical = _skill(name)
        adapter = (ROOT / ".claude" / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert _description(canonical) == expected_description
        assert _description(adapter) == expected_description

    for intent, skill in (
        ("implementing an Issue", "task-delivery-runner"),
        (
            "refreshing an existing Review candidate onto current main",
            "task-delivery-runner",
        ),
        ("reviewing a PR", "task-pr-review-runner"),
        ("closing out a manually merged PR", "task-closeout"),
        ("auditing Feature completion", "feature-completion-audit"),
    ):
        assert f"{intent}: `.agents/skills/{skill}/SKILL.md`" in agents

    delivery_root = _skill("task-delivery-runner")
    initial_path = (
        ROOT
        / ".agents"
        / "skills"
        / "task-delivery-runner"
        / "references"
        / "initial-delivery.md"
    )
    remediation_path = initial_path.with_name("remediation.md")
    initial = initial_path.read_text(encoding="utf-8")
    remediation = remediation_path.read_text(encoding="utf-8")
    assert (
        "Choose exactly one branch and read only its linked instructions"
        in delivery_root
    )
    assert "references/initial-delivery.md" in delivery_root
    assert "references/remediation.md" in delivery_root
    assert "references/remediation.md" not in initial
    assert "references/initial-delivery.md" not in remediation
    assert "READY_FOR_REVIEW" in initial
    assert "READY_FOR_NEW_REVIEW" in remediation
    assert "remediation no-change" in remediation

    assert "unresolved diagnostic concern remains, proceed" in agents
    assert "directly to LCK Delivery Complete" in agents
    assert "Broaden or repeat validation only after" in agents
    assert "Long-operation wait and progress contract" not in delivery_root

    review = _skill("task-pr-review-runner")
    closeout = _skill("task-closeout")
    feature = _skill("feature-completion-audit")
    assert "implementation-read-only" in review
    assert "READY_FOR_HUMAN_MERGE" in review
    assert "This Skill never merges" in closeout
    assert "Feature 已完成，可以由维护者人工收尾" in feature
    assert "Feature 尚未完成，需要补充或修复 Task" in feature
    assert "证据不足，暂不能判定 Feature 完成" in feature
