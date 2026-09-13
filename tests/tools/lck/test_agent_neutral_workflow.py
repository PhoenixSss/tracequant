"""Tests for agent-neutral LCK routing and source ownership."""

from __future__ import annotations

from pathlib import Path

from tools.lck.skill_audit import SKILLS, audit

ROOT = Path(__file__).resolve().parents[3]


def test_shared_semantic_owner_docs_are_lck_only() -> None:
    for relative in (
        "docs/workflows/lck/lifecycle.md",
        "docs/workflows/lck/review-and-remediation.md",
        "docs/workflows/lck/issue-contracts.md",
        "docs/workflows/lck/typed-profiles.md",
        "docs/guides/lck/overview.md",
    ):
        assert (ROOT / relative).is_file()
    assert not (ROOT / "docs/development").exists()


def test_natural_language_routes_to_canonical_skills() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for skill in SKILLS:
        assert f".agents/skills/{skill}/SKILL.md" in agents
    assert "uv run --frozen python -m tools.lck --help" in agents


def test_claude_is_a_thin_agent_adapter() -> None:
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in claude
    assert ".claude/settings.json" in claude
    assert len(claude.splitlines()) < 20


def test_each_claude_skill_points_to_one_canonical_source() -> None:
    for skill in SKILLS:
        adapter = (ROOT / ".claude" / "skills" / skill / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert f".agents/skills/{skill}/SKILL.md" in adapter
        assert len(adapter.splitlines()) < 15


def test_skill_audit_accepts_the_current_layout() -> None:
    result, returncode = audit(ROOT)
    assert returncode == 0, result["violations"]
    assert result["status"] == "pass"
    assert set(result["canonical_skills"]) == set(SKILLS)
    assert set(result["adapters"]) == set(SKILLS)


def test_agent_skills_guide_is_navigation_not_product_documentation() -> None:
    guide = (ROOT / "docs/workflows/lck/agent-skills.md").read_text(encoding="utf-8")
    assert ".agents/skills/" in guide
    assert "tools/lck/" in guide
    assert "src/tracequant" not in guide
