"""Shared Markdown section extraction for typed Issue contracts.

Issue contract headings are semantic markers.  Their Markdown level is only
presentation, while fenced code examples are content and must not become
sections.
"""

from __future__ import annotations

import re
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


def extract_markdown_sections(body: str) -> tuple[MarkdownSection, ...]:
    """Extract real H1-H6 sections while ignoring fenced code blocks.

    Only ATX headings with one to six markers are recognized.  Plain text,
    headings indented as code, and headings inside backtick/tilde fences are
    deliberately excluded.
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

    extracted: list[MarkdownSection] = []
    for position, (line_index, name, level) in enumerate(sections):
        next_line_index = len(lines)
        for next_position in range(position + 1, len(sections)):
            candidate_line_index, _candidate_name, candidate_level = sections[
                next_position
            ]
            if candidate_level <= level:
                next_line_index = candidate_line_index
                break
        extracted.append(
            MarkdownSection(
                name=name,
                level=level,
                content="\n".join(lines[line_index + 1 : next_line_index]).strip(),
            )
        )
    return tuple(extracted)
