# ruff: noqa: E402

"""Regression tests for shared typed Issue Markdown section extraction."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from bug_policy import bug_contract_snapshot  # type: ignore[import-not-found]
from documentation_policy import (  # type: ignore[import-not-found]
    documentation_contract_snapshot,
)
from markdown_sections import (  # type: ignore[import-not-found]
    extract_markdown_sections,
)
from research_policy import research_contract_snapshot  # type: ignore[import-not-found]

SECTION_BODIES = {
    "bug": (
        "Observed",
        "Expected",
        "Reproduction / Evidence",
        "Acceptance Criteria",
    ),
    "documentation": (
        "Documentation Goal",
        "Requirements",
        "Acceptance Criteria",
    ),
    "research": (
        "Question / Decision Needed",
        "Context",
        "Scope",
        "Non-goals",
        "Evidence / Evaluation Criteria",
        "Expected Outcome / Artifact",
    ),
}


def _body(headings: tuple[str, ...], level: int = 3) -> str:
    return "\n\n".join(
        f"{'#' * level} {heading}\n\ncontent for {heading}" for heading in headings
    )


@pytest.mark.parametrize("level", (1, 2, 3, 6))
@pytest.mark.parametrize("profile", ("bug", "documentation", "research"))
def test_typed_contracts_accept_equivalent_heading_levels(
    profile: str,
    level: int,
) -> None:
    body = _body(SECTION_BODIES[profile], level)
    snapshots = {
        "bug": bug_contract_snapshot,
        "documentation": documentation_contract_snapshot,
        "research": research_contract_snapshot,
    }

    assert snapshots[profile](body)["status"] == "pass"


def test_extractor_ignores_plain_text_and_fenced_headings() -> None:
    sections = extract_markdown_sections(
        """A plain Section Name does not count.

```markdown
# Section Name
### Section Name
```

## Section Name

real content
"""
    )

    assert [(section.name, section.level) for section in sections] == [
        ("Section Name", 2)
    ]
    assert sections[0].content == "real content"


def test_extractor_ignores_tilde_fenced_headings() -> None:
    sections = extract_markdown_sections(
        """~~~text
## Section Name
~~~

### Other
content
"""
    )

    assert [section.name for section in sections] == ["Other"]


def test_sections_end_at_next_heading_regardless_of_level() -> None:
    sections = extract_markdown_sections(
        """### Reproduction / Evidence
introductory evidence

#### Confirmed regression
nested evidence

### Acceptance Criteria
criterion
        """
    )

    assert [(section.name, section.level) for section in sections] == [
        ("Reproduction / Evidence", 3),
        ("Confirmed regression", 4),
        ("Acceptance Criteria", 3),
    ]
    assert sections[0].content == "introductory evidence"


def test_named_sections_keep_deeper_noncanonical_headings_as_content() -> None:
    sections = extract_markdown_sections(
        """# Observed

#### Confirmed regression
evidence

### Expected
expected behavior
""",
        canonical_names=("Observed", "Expected"),
    )

    assert [section.name for section in sections] == ["Observed", "Expected"]
    assert sections[0].content == "#### Confirmed regression\nevidence"


def test_named_sections_use_peer_noncanonical_headings_as_boundaries() -> None:
    sections = extract_markdown_sections(
        """### Observed

observed content

### Optional Context

context must not be part of Observed

### Expected
expected behavior
""",
        canonical_names=("Observed", "Expected"),
    )

    assert [section.name for section in sections] == ["Observed", "Expected"]
    assert sections[0].content == "observed content"


@pytest.mark.parametrize("profile", ("bug", "documentation", "research"))
def test_typed_contracts_accept_mixed_heading_levels(profile: str) -> None:
    headings = SECTION_BODIES[profile]
    levels = (1, 3, 2, 6, 4, 5)
    body = "\n\n".join(
        f"{'#' * levels[index]} {heading}\n\ncontent for {heading}"
        for index, heading in enumerate(headings)
    )
    snapshots = {
        "bug": bug_contract_snapshot,
        "documentation": documentation_contract_snapshot,
        "research": research_contract_snapshot,
    }

    assert snapshots[profile](body)["status"] == "pass"


@pytest.mark.parametrize("first_content", ("", "..."))
def test_bug_contract_rejects_mixed_level_empty_or_placeholder_first_section(
    first_content: str,
) -> None:
    body = "\n\n".join(
        (
            f"{'#' * level} {heading}\n\n{first_content if index == 0 else f'valid {heading}'}"
        )
        for index, (heading, level) in enumerate(
            zip(SECTION_BODIES["bug"], (1, 3, 2, 6), strict=True)
        )
    )

    contract = bug_contract_snapshot(body)

    assert contract["status"] == "reclassification_required"
    assert "Observed" in (
        contract["empty_sections"] + contract["insufficient_sections"]
    )


@pytest.mark.parametrize("profile", ("bug", "documentation", "research"))
def test_typed_contracts_fail_closed_for_duplicate_missing_and_fenced_sections(
    profile: str,
) -> None:
    headings = SECTION_BODIES[profile]
    snapshots = {
        "bug": bug_contract_snapshot,
        "documentation": documentation_contract_snapshot,
        "research": research_contract_snapshot,
    }
    snapshot = snapshots[profile]

    duplicate = _body(headings) + f"\n\n# {headings[0]}\n\nconflict"
    missing = _body(headings[1:])
    fenced = f"```markdown\n# {headings[0]}\n\nexample\n```\n\n" + _body(headings[1:])

    assert snapshot(duplicate)["status"] == "reclassification_required"
    assert snapshot(missing)["status"] == "reclassification_required"
    assert snapshot(fenced)["status"] == "reclassification_required"


@pytest.mark.parametrize("profile", ("bug", "documentation", "research"))
def test_typed_contracts_do_not_absorb_optional_context_after_empty_section(
    profile: str,
) -> None:
    headings = SECTION_BODIES[profile]
    body = "\n\n".join(
        f"### {heading}\n\n{f'content for {heading}' if index < len(headings) - 1 else ''}"
        for index, heading in enumerate(headings)
    )
    body += "\n\n### Optional Context\n\ncontext must not satisfy a required section"
    snapshots = {
        "bug": bug_contract_snapshot,
        "documentation": documentation_contract_snapshot,
        "research": research_contract_snapshot,
    }

    contract = snapshots[profile](body)

    assert contract["status"] == "reclassification_required"
    assert contract["empty_sections"] == [headings[-1]]
