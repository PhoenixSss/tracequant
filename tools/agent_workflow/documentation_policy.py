"""Repository-owned policy for Documentation leaf contracts and changes.

The policy is intentionally data-driven and side-effect free.  In particular,
Issue-provided documentation context is never interpreted as a command plan.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final


class DocumentationPolicyStatus(StrEnum):
    PASS = "pass"
    RECLASSIFICATION_REQUIRED = "reclassification_required"


DOCUMENTATION_POLICY_ID: Final = "repository-documentation-safe-v1"
_REQUIRED_SECTIONS: Final = (
    "Documentation Goal",
    "Requirements",
    "Acceptance Criteria",
)
_SECTION_RE: Final = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)

# Documentation deliberately has a narrow path allow-list.  Workflow and
# development-policy prose is excluded because changing it changes the
# repository's control contract even when the file happens to end in .md.
_ALLOWED_EXACT_FILES: Final = frozenset({"README.md"})
_ALLOWED_PREFIXES: Final = ("docs/",)
_EXCLUDED_EXACT_FILES: Final = frozenset({"AGENTS.md", "CLAUDE.md"})
_EXCLUDED_PREFIXES: Final = (
    ".agents/",
    ".claude/",
    ".github/",
    "docs/development/",
    "docs/workflows/",
    "src/",
    "tests/",
    "tools/",
)


@dataclass(frozen=True)
class DocumentationContract:
    status: DocumentationPolicyStatus
    required_sections: tuple[str, ...] = ()
    missing_sections: tuple[str, ...] = ()
    duplicate_sections: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "required_sections": list(self.required_sections),
            "missing_sections": list(self.missing_sections),
            "duplicate_sections": list(self.duplicate_sections),
            **({"detail": self.detail} if self.detail else {}),
        }


@dataclass(frozen=True)
class DocumentationChangeResult:
    status: DocumentationPolicyStatus
    policy_id: str
    changed_files: tuple[str, ...]
    disallowed_files: tuple[str, ...] = ()
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status is DocumentationPolicyStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "policy_id": self.policy_id,
            "changed_files": list(self.changed_files),
            "disallowed_files": list(self.disallowed_files),
            **({"detail": self.detail} if self.detail else {}),
        }


def _normalized_sections(body: str) -> tuple[str, ...]:
    return tuple(
        " ".join(match.group(1).split()) for match in _SECTION_RE.finditer(body)
    )


def documentation_contract_snapshot(body: str | None) -> dict[str, Any]:
    """Return a bounded typed snapshot of the Documentation Issue contract."""

    if not isinstance(body, str) or not body.strip():
        return DocumentationContract(
            DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED,
            detail="Documentation body is unavailable",
        ).to_dict()

    sections = _normalized_sections(body)
    section_keys = tuple(section.casefold() for section in sections)
    required_keys = tuple(section.casefold() for section in _REQUIRED_SECTIONS)
    missing = tuple(
        required
        for required, key in zip(_REQUIRED_SECTIONS, required_keys, strict=True)
        if key not in section_keys
    )
    duplicates = tuple(
        required
        for required, key in zip(_REQUIRED_SECTIONS, required_keys, strict=True)
        if section_keys.count(key) > 1
    )
    if missing or duplicates:
        parts: list[str] = []
        if missing:
            parts.append("missing sections: " + ", ".join(missing))
        if duplicates:
            parts.append("duplicate sections: " + ", ".join(duplicates))
        return DocumentationContract(
            DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED,
            missing_sections=missing,
            duplicate_sections=duplicates,
            detail="; ".join(parts),
        ).to_dict()
    return DocumentationContract(
        DocumentationPolicyStatus.PASS,
        required_sections=_REQUIRED_SECTIONS,
    ).to_dict()


def is_valid_documentation_contract(value: object) -> bool:
    """Validate the bounded shape consumed by LCK eligibility."""

    if not isinstance(value, Mapping):
        return False
    return (
        value.get("status") == DocumentationPolicyStatus.PASS.value
        and value.get("required_sections") == list(_REQUIRED_SECTIONS)
        and value.get("missing_sections") == []
        and value.get("duplicate_sections") == []
    )


def _is_allowed_path(path: str) -> bool:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        return False
    if path in _EXCLUDED_EXACT_FILES:
        return False
    if any(path.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
        return False
    return path in _ALLOWED_EXACT_FILES or (
        path.startswith("docs/")
        and path != "docs/"
        and any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES)
    )


def evaluate_documentation_changes(
    changed_files: Iterable[str],
) -> DocumentationChangeResult:
    """Evaluate a candidate against the fixed Documentation safe-change policy."""

    normalized = tuple(sorted({path.strip() for path in changed_files if path.strip()}))
    disallowed = tuple(path for path in normalized if not _is_allowed_path(path))
    if disallowed:
        return DocumentationChangeResult(
            DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED,
            DOCUMENTATION_POLICY_ID,
            normalized,
            disallowed,
            detail=(
                "Documentation candidate exceeds the repository-owned safe-change "
                "policy; reclassification or split required for: "
                + ", ".join(disallowed)
            ),
        )
    if not normalized:
        return DocumentationChangeResult(
            DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED,
            DOCUMENTATION_POLICY_ID,
            normalized,
            detail="Documentation candidate contains no changed files",
        )
    return DocumentationChangeResult(
        DocumentationPolicyStatus.PASS,
        DOCUMENTATION_POLICY_ID,
        normalized,
    )
