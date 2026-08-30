"""Shared Markdown section extraction for typed Issue contracts.

Issue contract headings are semantic markers.  Their Markdown level is only
presentation, while fenced code examples are content and must not become
sections.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

_ATX_HEADING: Final = re.compile(
    r"^[ \t]{0,3}(?P<marks>#{1,6})(?:[ \t]+(?P<name>.+?)[ \t]*|[ \t]*)$"
)
_FENCE: Final = re.compile(r"^[ \t]{0,3}(?P<marker>`{3,}|~{3,})(?P<remainder>.*)$")


@dataclass(frozen=True)
class MarkdownSection:
    """One ATX heading and the content before the next real heading."""

    name: str
    level: int
    content: str


def _fence_marker(line: str) -> str | None:
    match = _FENCE.fullmatch(line)
    if match is None:
        return None
    marker = match.group("marker")
    remainder = match.group("remainder")
    # Backtick fences cannot have backticks in their info string.  Tilde
    # fences have no equivalent restriction.
    if marker[0] == "`" and "`" in remainder:
        return None
    return marker


def _is_fence_close(line: str, opening_marker: str) -> bool:
    match = _FENCE.fullmatch(line)
    if match is None or match.group("remainder").strip():
        return False
    marker = match.group("marker")
    return marker[0] == opening_marker[0] and len(marker) >= len(opening_marker)


def extract_markdown_sections(
    body: str,
    *,
    canonical_names: Collection[str] | None = None,
) -> tuple[MarkdownSection, ...]:
    """Extract real H1-H6 sections while ignoring fenced code blocks.

    Only ATX headings with one to six markers are recognized.  Plain text,
    headings indented as code, and headings inside backtick/tilde fences are
    deliberately excluded.  Contract callers may provide canonical section
    names so non-canonical nested headings remain content while canonical
    sections still end at the next canonical heading regardless of level.
    """
    lines = body.splitlines()
    sections: list[tuple[int, str, int]] = []
    opening_fence: str | None = None

    for index, line in enumerate(lines):
        if opening_fence is not None:
            if _is_fence_close(line, opening_fence):
                opening_fence = None
            continue

        fence = _fence_marker(line)
        if fence is not None:
            opening_fence = fence
            continue

        heading = _ATX_HEADING.fullmatch(line)
        if heading is None or not heading.group("name"):
            continue
        sections.append(
            (
                index,
                " ".join(heading.group("name").split()),
                len(heading.group("marks")),
            )
        )

    if canonical_names is not None:
        canonical_keys = {" ".join(name.split()).casefold() for name in canonical_names}
        sections = [
            section for section in sections if section[1].casefold() in canonical_keys
        ]

    extracted: list[MarkdownSection] = []
    for position, (line_index, name, level) in enumerate(sections):
        # Contract heading levels are presentation only.  A later canonical
        # section may use a deeper level than the current one, so hierarchy
        # based boundaries would incorrectly absorb it into the current
        # section and hide empty or placeholder content.
        next_line_index = (
            sections[position + 1][0] if position + 1 < len(sections) else len(lines)
        )
        extracted.append(
            MarkdownSection(
                name=name,
                level=level,
                content="\n".join(lines[line_index + 1 : next_line_index]).strip(),
            )
        )
    return tuple(extracted)
