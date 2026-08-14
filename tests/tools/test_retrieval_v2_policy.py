"""Focused tests for Issue #87 — Agent Context / Retrieval v2 policy.

These tests pin the retrieval semantics that #87 introduces in AGENTS.md,
CLAUDE.md, and the eight workflow Skills: leaf-Issue-first default context,
default exclusions, trigger-based expansion, bounded progressive retrieval,
comments default-off, Parent/Epic on demand, deterministic-metadata vs
full-text separation, preserved safety hard rules, the
feature-completion-audit hierarchy-aware exception, and the before/after
evidence document. They intentionally avoid a new harness: they reuse the
existing tests/tools/ convention of reading the policy sources directly.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]

AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"
EVIDENCE = ROOT / "docs/workflows/context-retrieval-v2/before-after-retrieval.md"

SKILLS: dict[str, dict[str, Path]] = {
    "delivery": {
        "agents": ROOT / ".agents/skills/task-delivery-runner/SKILL.md",
        "claude": ROOT / ".claude/skills/task-delivery-runner/SKILL.md",
    },
    "review": {
        "agents": ROOT / ".agents/skills/task-pr-review-runner/SKILL.md",
        "claude": ROOT / ".claude/skills/task-pr-review-runner/SKILL.md",
    },
    "closeout": {
        "agents": ROOT / ".agents/skills/task-closeout/SKILL.md",
        "claude": ROOT / ".claude/skills/task-closeout/SKILL.md",
    },
    "audit": {
        "agents": ROOT / ".agents/skills/feature-completion-audit/SKILL.md",
        "claude": ROOT / ".claude/skills/feature-completion-audit/SKILL.md",
    },
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    return " ".join(_read(path).split())


# --- AGENTS.md: leaf-Issue-first default context ---


def test_agents_leaf_first_default_context() -> None:
    text = _flat(AGENTS)
    assert "leaf-Issue-first" in text
    assert "Default context (leaf-Issue-first)" in text
    assert "The current leaf Issue body is the primary source" in text


def test_agents_default_exclusions_list_eager_full_read_sources() -> None:
    text = _flat(AGENTS)
    assert "Default exclusions" in text
    for source in (
        "Issue comments (all history)",
        "the complete Parent Feature body",
        "the complete Parent Epic body",
        "sibling or descendant Issues",
        "complete blocking / related Issue bodies",
        "all linked documentation",
        "all ADRs",
        "architecture documentation unrelated to the current change",
    ):
        assert source in text
    assert (
        "A link's existence does not by itself require reading the target's full text"
        in text
    )


def test_agents_eager_hierarchy_mandate_removed() -> None:
    text = _read(AGENTS)
    assert "including comments" not in text
    assert (
        "Read its parent Issue, blocking Issues, linked documentation, and ADRs"
        not in text
    )
    assert "Read the complete assigned GitHub Issue" not in text


def test_agents_defines_all_expansion_triggers() -> None:
    text = _flat(AGENTS)
    for trigger in (
        "Explicit reference",
        "Missing or ambiguous specification",
        "Conflict",
        "Hard dependency",
        "Safety / architecture",
        "Verification",
    ):
        assert trigger in text


def test_agents_progressive_retrieval_is_bounded() -> None:
    text = _flat(AGENTS)
    assert "Progressive retrieval" in text
    assert "Read the minimum relevant source or section" in text
    assert "Evaluate whether the information is sufficient" in text
    assert "Unbounded recursive expansion is forbidden" in text


def test_agents_comments_policy_default_off() -> None:
    text = _flat(AGENTS)
    assert "Comments policy" in text
    assert (
        "Issue comments are discussion / decision history, not default startup context"
        in text
    )
    assert "Never load all comments just because the Issue has comments" in text
    assert "Historical comments never silently override the current Issue body" in text


def test_agents_parent_epic_policy_on_demand() -> None:
    text = _flat(AGENTS)
    assert "Parent / Epic policy" in text
    assert "A normal Task does not default to reading them completely" in text
    assert "not the complete Parent specification" in text
    assert (
        "A single Parent constraint never requires the Parent full body plus the Epic full body plus all siblings"
        in text
    )


def test_agents_deterministic_facts_separated_from_full_text() -> None:
    text = _flat(AGENTS)
    assert "Deterministic facts vs model context" in text
    assert (
        "Verifying a mechanical fact with a deterministic tool is not the same as the model consuming the full original text"
        in text
    )
    assert (
        "The current leaf Issue body is the only default full-text business source"
        in text
    )


def test_agents_feature_audit_exception() -> None:
    text = _flat(AGENTS)
    assert "feature-completion-audit exception" in text
    assert "`feature-completion-audit` is a hierarchy-aware audit" in text
    assert "The leaf-Issue-first default does not apply to it" in text


def test_agents_safety_and_data_hard_rules_preserved() -> None:
    text = _flat(AGENTS)
    for rule in (
        "Never enable live trading by default",
        "Never submit live orders from tests",
        "Never print, log, commit, or expose credentials",
        "The risk module has final authority to reject or reduce an order",
        "Raw data must remain immutable",
        "Use chronological walk-forward validation with purging and embargo rather than random train/test splitting",
        "Modules must not perform I/O, read env vars, create directories, or cache global singletons on import",
        "A strategy must never submit an exchange order directly",
    ):
        assert rule in text


def test_agents_safety_trigger_fails_closed() -> None:
    text = _flat(AGENTS)
    assert "Never skip safety constraints to reduce context" in text
    assert "Fail closed / Human Gate when it cannot be safely resolved" in text


# --- CLAUDE.md: Requirement 7 ownership cleanup ---


def test_claude_references_agents_instead_of_duplicating() -> None:
    text = _flat(CLAUDE)
    assert (
        "The `AGENTS.md` file at the repo root is the primary behavior rule source"
        in text
    )
    assert "it does not duplicate AGENTS.md rules" in text
    assert "are defined in `AGENTS.md` and are binding here" in text


def test_claude_duplicate_workflow_and_rule_maintenance_removed() -> None:
    text = _read(CLAUDE)
    assert "Read parent/blocking Issues, linked docs, and ADRs" not in text
    assert "Read the complete assigned Issue" not in text
    assert "Never use random train/test splits" not in text
    assert "Raw data must never be overwritten" not in text
    assert "The risk module has final authority" not in text


def test_claude_stale_current_focus_removed_and_claude_specific_kept() -> None:
    text = _read(CLAUDE)
    assert "Current focus is Feature #2" not in text
    for kept in (
        "uv run pytest",
        "uv run mypy src tests",
        "### Permissions",
        ".claude/settings.json",
        "Claude Code 专用的四个 Skill",
    ):
        assert kept in text


# --- Delivery Skills: leaf-Issue-first + trigger-based expansion ---


def test_delivery_skills_leaf_first_and_trigger_expansion() -> None:
    for path in SKILLS["delivery"].values():
        text = _flat(path)
        assert "The current Task body is the business specification" in text
        assert (
            "Do not default to reading comments, complete Parent/Epic bodies, dependency bodies, templates, workflows, validation sources, or linked docs/ADRs"
            in text
        )
        assert (
            "Verifying these mechanical facts does not require reading the full text of any source into the model context"
            in text
        )
        assert (
            "Read the minimum relevant source/section, evaluate sufficiency, and expand further only if still insufficient"
            in text
        )


def test_delivery_skills_eager_phase1_verification_list_removed() -> None:
    for path in SKILLS["delivery"].values():
        text = _read(path)
        assert "Task type/title/body/comments" not in text
        assert (
            "templates, workflows, validation sources, and affected architecture"
            not in text
        )


# --- Review Skills: independent + lazy hierarchy ---


def test_review_skills_independent_and_lazy_hierarchy() -> None:
    for path in SKILLS["review"].values():
        text = _flat(path)
        assert (
            "Independently read the current Task body, PR body and effective diff"
            in text
        )
        assert (
            "Comments, Parent/Epic bodies, and other hierarchy/history are not default review input"
            in text
        )
        assert "Expansion is bounded" in text
        assert "Do not inherit Delivery conclusions" in text
        assert "fresh session" in text.casefold() or "new session" in text.casefold()
        assert "read-only" in text


def test_review_skills_no_longer_require_complete_comments() -> None:
    for path in SKILLS["review"].values():
        text = _read(path)
        assert "complete Task specification/comments" not in text


# --- Closeout Skills: minimal business context ---


def test_closeout_skills_minimal_business_context() -> None:
    for path in SKILLS["closeout"].values():
        text = _flat(path)
        assert "Default context" in text
        assert (
            "It does not default to re-reading the complete business Issue hierarchy (Parent/Epic bodies), business comments, or full implementation context"
            in text
        )
        assert "explicit anomaly trigger" in text


# --- feature-completion-audit: hierarchy-aware exception ---


def test_feature_audit_skills_hierarchy_aware_exception() -> None:
    for path in SKILLS["audit"].values():
        text = _flat(path)
        assert "Context acquisition (hierarchy-aware exception)" in text
        assert "not a leaf-Issue-first default" in text
        assert "direct-child Issue hierarchy" in text
        assert (
            "Do not default to historical comments, unrelated docs/ADRs, the roadmap, sibling Feature history, or general workflow reports"
            in text
        )


# --- Codex / Claude semantic parity ---


def test_codex_claude_retrieval_semantics_parity() -> None:
    markers = {
        "delivery": "Do not default to reading comments, complete Parent/Epic bodies",
        "review": "hierarchy/history are not default review input",
        "closeout": "does not default to re-reading the complete business Issue hierarchy",
        "audit": "not a leaf-Issue-first default",
    }
    for group, marker in markers.items():
        agents_text = _flat(SKILLS[group]["agents"])
        claude_text = _flat(SKILLS[group]["claude"])
        assert marker in agents_text
        assert marker in claude_text


# --- Before / after evidence document ---


def test_retrieval_evidence_document_has_before_after_sections() -> None:
    text = _read(EVIDENCE)
    assert "## BEFORE" in text
    assert "## AFTER" in text
    for scenario in ("Scenario A", "Scenario B", "Scenario C", "Scenario D"):
        assert scenario in text
    assert "Before → after comparison" in text
    assert "not reliably obtainable" in text
    assert "no fabricated Token data" in text or "not fabricated" in text
