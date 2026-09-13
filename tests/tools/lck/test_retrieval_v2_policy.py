"""Guard the isolated LCK context-retrieval policy."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / ".agents" / "policies" / "context-retrieval.md"


def _policy() -> str:
    return POLICY.read_text(encoding="utf-8")


def test_leaf_issue_is_the_default_full_text_context() -> None:
    text = _policy()
    assert "current leaf Issue body" in text
    assert "default full-text business input" in text


def test_eager_hierarchy_and_comment_reads_are_excluded() -> None:
    text = _policy()
    for marker in ("Issue comments", "Parent Feature", "Epic bodies", "all ADRs"):
        assert marker in text
    assert "Do not eagerly load" in text


def test_context_expansion_is_bounded_and_fail_closed() -> None:
    text = _policy()
    assert "Expansion is progressive" in text
    assert "Unbounded recursive expansion" in text
    assert "Human Gate" in text


def test_deterministic_facts_do_not_imply_full_text_retrieval() -> None:
    text = _policy()
    for marker in ("labels", "blockers", "PR identity", "SHAs", "checks"):
        assert marker in text
    assert "without injecting the corresponding full text" in text


def test_feature_audit_is_the_bounded_hierarchy_exception() -> None:
    text = _policy()
    assert "feature-completion-audit" in text
    assert "bounded exception" in text


def test_policy_is_owned_outside_product_code_and_docs() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert ".agents/policies/context-retrieval.md" in agents
    assert "AGENTS.md" in claude
    assert not any(
        "context-retrieval" in path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "tracequant").rglob("*.py")
    )
