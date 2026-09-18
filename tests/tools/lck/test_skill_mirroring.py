"""Provider Skill mirroring contract.

The Claude provider Skills are self-contained mirrors of their canonical Skill
package with exactly one declared provider-specific difference. These tests
enforce that mechanically: a v1-style "subtract the single Codex-only section"
rule for the subtractive Skills, and a "append the single Claude-only section"
rule for the additive Skill. Anything else is an undeclared divergence.
"""

from __future__ import annotations

from pathlib import Path

from tools.lck.skill_audit import (
    ADDITIVE_SKILLS,
    CANONICAL_PREFIX,
    CLAUDE_SECTION,
    PROVIDER_VOCABULARY,
    ROUTE_SECTION,
    SKILLS,
    SUBTRACTIVE_SKILLS,
    audit,
    canonical_package,
    expected_provider_skill,
    provider_skill_text,
)

ROOT = Path(__file__).resolve().parents[3]


def test_provider_skill_mirroring_is_mechanically_enforced() -> None:
    for skill in SKILLS:
        adapter = provider_skill_text(ROOT, skill)
        expected, difference = expected_provider_skill(ROOT, skill)
        assert adapter == expected, f"{skill} is not the declared mirror"
        assert difference in {ROUTE_SECTION, CLAUDE_SECTION}


def test_subtractive_skills_omit_only_the_codex_only_route_section() -> None:
    for skill in SUBTRACTIVE_SKILLS:
        package = canonical_package(ROOT, skill)
        adapter = provider_skill_text(ROOT, skill)
        assert ROUTE_SECTION in package
        assert ROUTE_SECTION not in adapter
        assert expected_provider_skill(ROOT, skill)[0] == adapter


def test_additive_skill_adds_only_the_claude_execution_model_section() -> None:
    for skill in ADDITIVE_SKILLS:
        package = canonical_package(ROOT, skill)
        adapter = provider_skill_text(ROOT, skill)
        assert ROUTE_SECTION not in package
        assert adapter.startswith(package)
        remainder = adapter[len(package) :].strip()
        assert remainder.startswith(CLAUDE_SECTION)
        assert remainder.count("## ") == 1


def test_provider_skills_expose_no_codex_execution_mechanics() -> None:
    for skill in SKILLS:
        adapter = provider_skill_text(ROOT, skill)
        assert CANONICAL_PREFIX not in adapter
        _, difference = expected_provider_skill(ROOT, skill)
        declared_only = (
            adapter
            if difference == ROUTE_SECTION
            else adapter[: len(canonical_package(ROOT, skill))]
        )
        for token in PROVIDER_VOCABULARY:
            assert token not in declared_only, f"{skill} leaks {token}"


def test_shared_policy_carries_no_provider_execution_mechanics() -> None:
    policy = (ROOT / ".agents/policies/command-execution.md").read_text(
        encoding="utf-8"
    )
    for token in PROVIDER_VOCABULARY:
        assert token not in policy, f"command-execution policy leaks {token}"


def test_mirror_check_detects_an_undeclared_difference() -> None:
    for skill in SKILLS:
        adapter = provider_skill_text(ROOT, skill)
        expected, _difference = expected_provider_skill(ROOT, skill)
        mutated = adapter.replace("the ", "teh ", 1)
        assert mutated != adapter
        assert mutated != expected, f"{skill} mirror check is vacuous"


def test_provider_vocabulary_covers_route_family_leakage() -> None:
    """A dangling route reference outside the container must fail closed."""

    assert "route" in PROVIDER_VOCABULARY
    for skill in SKILLS:
        adapter = provider_skill_text(ROOT, skill)
        _, difference = expected_provider_skill(ROOT, skill)
        declared_only = (
            adapter
            if difference == ROUTE_SECTION
            else adapter[: len(canonical_package(ROOT, skill))]
        )
        assert "route" not in declared_only, f"{skill} references a route contract"


def test_agent_entry_routing_names_both_provider_paths() -> None:
    """A Claude session reading AGENTS.md must not be sent to the canonical tree."""

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for skill in SKILLS:
        assert f".agents/skills/{skill}/SKILL.md" in agents
    assert ".claude/skills/" in agents


def test_audit_reports_mirrors_and_passes() -> None:
    report, returncode = audit(ROOT)
    assert returncode == 0, report["violations"]
    assert report["status"] == "pass"
    adapters = report["adapters"]
    assert isinstance(adapters, dict)
    for skill in SKILLS:
        entry = adapters[skill]
        assert entry["mirrored"] is True
        assert entry["declared_difference"] in {ROUTE_SECTION, CLAUDE_SECTION}
        assert entry["provider_vocabulary"] == []
