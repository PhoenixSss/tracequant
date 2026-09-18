"""Provider Skill mirroring contract.

Each Claude provider Skill under ``.claude/skills/<name>`` is a self-contained
mirror of the whole canonical Skill package (``SKILL.md`` plus
``references/*.md``) with exactly one declared provider-specific difference: the
subtractive Skills omit the single Codex-only ``## Execution route contract``
section, and the additive Skill appends the single Claude-only ``## Execution
model`` section. These tests enforce that package-to-package, and they prove
that the audit reacts to an undeclared difference instead of merely re-asserting
equality next to it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tools.lck.skill_audit import (
    ADDITIVE_SKILLS,
    CANONICAL_PREFIX,
    CLAUDE_SECTION,
    PROVIDER_VOCABULARY,
    ROUTE_SECTION,
    SHARED_DOCS,
    SKILL_FILE,
    SKILLS,
    SUBTRACTIVE_SKILLS,
    audit,
    canonical_package,
    dangling_links,
    expected_provider_package,
    package_text,
    provider_package,
    strip_route_section,
)

ROOT = Path(__file__).resolve().parents[3]


def _outside_declared_section(skill: str) -> str:
    """Return the provider content that must stay free of provider mechanics."""

    package = provider_package(ROOT, skill)
    if skill in SUBTRACTIVE_SKILLS:
        return package_text(package)
    declared_start = len(canonical_package(ROOT, skill)[SKILL_FILE])
    return package[SKILL_FILE][:declared_start]


def _mirror_repo_root(tmp_path: Path) -> Path:
    """A throwaway repository root holding both Skill trees and the shared docs."""

    root = tmp_path / "repo"
    for skill in SKILLS:
        shutil.copytree(
            ROOT / ".agents" / "skills" / skill, root / ".agents" / "skills" / skill
        )
        shutil.copytree(
            ROOT / ".claude" / "skills" / skill, root / ".claude" / "skills" / skill
        )
    for doc in SHARED_DOCS:
        target = root / doc
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / doc, target)
    return root


def test_provider_skill_mirroring_is_mechanically_enforced() -> None:
    for skill in SKILLS:
        adapter = provider_package(ROOT, skill)
        expected, difference = expected_provider_package(ROOT, skill)
        assert adapter == expected, f"{skill} is not the declared mirror"
        assert difference in {ROUTE_SECTION, CLAUDE_SECTION}


def test_subtractive_skills_omit_only_the_codex_only_route_section() -> None:
    for skill in SUBTRACTIVE_SKILLS:
        package = canonical_package(ROOT, skill)
        adapter = provider_package(ROOT, skill)
        assert ROUTE_SECTION in package[SKILL_FILE]
        assert ROUTE_SECTION not in adapter[SKILL_FILE]
        assert set(adapter) == set(package)
        assert strip_route_section(package[SKILL_FILE]) == adapter[SKILL_FILE]
        for relative in package:
            if relative != SKILL_FILE:
                assert adapter[relative] == package[relative]


def test_additive_skill_adds_only_the_claude_execution_model_section() -> None:
    for skill in ADDITIVE_SKILLS:
        package = canonical_package(ROOT, skill)
        adapter = provider_package(ROOT, skill)
        assert set(adapter) == set(package)
        assert ROUTE_SECTION not in package[SKILL_FILE]
        assert adapter[SKILL_FILE].startswith(package[SKILL_FILE])
        remainder = adapter[SKILL_FILE][len(package[SKILL_FILE]) :].strip()
        assert remainder.startswith(CLAUDE_SECTION)
        assert remainder.count("## ") == 1


def test_provider_skills_expose_no_codex_execution_mechanics() -> None:
    for skill in SKILLS:
        adapter_text = package_text(provider_package(ROOT, skill))
        assert CANONICAL_PREFIX not in adapter_text
        declared_only = _outside_declared_section(skill)
        for token in PROVIDER_VOCABULARY:
            assert token not in declared_only, f"{skill} leaks {token}"


def test_provider_vocabulary_covers_route_family_leakage() -> None:
    """A dangling route reference outside the container must fail closed."""

    assert "route" in PROVIDER_VOCABULARY
    for skill in SKILLS:
        assert "route" not in _outside_declared_section(skill), (
            f"{skill} references a route contract"
        )


def test_mirrored_provider_packages_keep_their_relative_links_resolvable() -> None:
    """Task #377 M1: a provider copy must not link to a path it does not ship."""

    for tree in (ROOT / ".agents" / "skills", ROOT / ".claude" / "skills"):
        for path in sorted(tree.rglob("*.md")):
            assert dangling_links(path, ROOT) == [], f"{path} has a dangling link"


def test_shared_policy_carries_no_provider_execution_mechanics() -> None:
    policy = (ROOT / ".agents/policies/command-execution.md").read_text(
        encoding="utf-8"
    )
    for token in PROVIDER_VOCABULARY:
        assert token not in policy, f"command-execution policy leaks {token}"


def test_audit_rejects_an_undeclared_provider_difference(tmp_path: Path) -> None:
    """A one-character provider change must fail the audit itself."""

    repo = _mirror_repo_root(tmp_path)
    clean, clean_returncode = audit(repo)
    assert clean_returncode == 0, clean["violations"]

    mutated = (
        repo / ".claude/skills/task-delivery-runner/references/initial-delivery.md"
    )
    mutated.write_text(
        mutated.read_text(encoding="utf-8").replace("the ", "teh ", 1),
        encoding="utf-8",
    )

    report, returncode = audit(repo)
    assert returncode == 1
    assert report["status"] == "fail"
    violations = report["violations"]
    assert isinstance(violations, list)
    assert any(
        "references/initial-delivery.md" in violation for violation in violations
    ), violations


def test_audit_rejects_a_dangling_provider_package_link(tmp_path: Path) -> None:
    """A provider copy must not instruct a session to read a path it lacks."""

    repo = _mirror_repo_root(tmp_path)
    reference = "See [the branch reference](references/missing.md).\n"
    for relative in (
        Path(".agents/skills/task-delivery-runner/references/initial-delivery.md"),
        Path(".claude/skills/task-delivery-runner/references/initial-delivery.md"),
    ):
        path = repo / relative
        path.write_text(
            f"{path.read_text(encoding='utf-8').rstrip()}\n\n{reference}",
            encoding="utf-8",
        )

    report, returncode = audit(repo)
    assert returncode == 1
    violations = report["violations"]
    assert isinstance(violations, list)
    assert any(
        "dangling provider-package links" in violation for violation in violations
    ), violations
    assert not any("not a mirror" in violation for violation in violations), violations


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
        assert entry["exact"] is True
        assert entry["declared_difference"] in {ROUTE_SECTION, CLAUDE_SECTION}
        assert entry["provider_vocabulary"] == []
        assert entry["dangling_links"] == {}
        assert entry["files"] == sorted(canonical_package(ROOT, skill))
