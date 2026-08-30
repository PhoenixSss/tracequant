"""Repository-owned policy for Documentation leaf contracts and changes.

The policy is intentionally data-driven and side-effect free.  In particular,
Issue-provided documentation context is never interpreted as a command plan.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import yaml


class DocumentationPolicyStatus(StrEnum):
    PASS = "pass"
    RECLASSIFICATION_REQUIRED = "reclassification_required"


DOCUMENTATION_POLICY_ID: Final = "repository-documentation-safe-v1"
DOCUMENTATION_TEMPLATE_PATH: Final = Path(".github/ISSUE_TEMPLATE/documentation.yml")
_SECTION_RE: Final = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)

# Documentation deliberately has a narrow path allow-list.  Workflow and
# development-policy prose is excluded because changing it changes the
# repository's control contract even when the file happens to end in .md.
_ALLOWED_EXACT_FILES: Final = frozenset({"README.md"})
_ALLOWED_PREFIXES: Final = ("docs/",)
_EXCLUDED_AGENT_CONTROL_BASENAMES: Final = frozenset({"agents.md", "claude.md"})
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
    empty_sections: tuple[str, ...] = ()
    template_path: str = DOCUMENTATION_TEMPLATE_PATH.as_posix()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "required_sections": list(self.required_sections),
            "missing_sections": list(self.missing_sections),
            "duplicate_sections": list(self.duplicate_sections),
            "empty_sections": list(self.empty_sections),
            "template_path": self.template_path,
            **({"detail": self.detail} if self.detail else {}),
        }


@dataclass(frozen=True)
class DocumentationTemplateContract:
    """The required textarea fields declared by the formal Issue form."""

    field_ids: tuple[str, ...]
    section_labels: tuple[str, ...]


class DocumentationTemplateError(ValueError):
    """The repository-owned Documentation Issue form is not usable."""


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


def _default_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / DOCUMENTATION_TEMPLATE_PATH


def documentation_template_contract(
    template_path: Path | None = None,
) -> DocumentationTemplateContract:
    """Load required Documentation fields from the formal Issue form."""

    path = template_path or _default_template_path()
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DocumentationTemplateError(
            f"cannot read {DOCUMENTATION_TEMPLATE_PATH.as_posix()}: {exc}"
        ) from exc
    if not isinstance(parsed, Mapping) or not isinstance(parsed.get("body"), list):
        raise DocumentationTemplateError(
            f"{DOCUMENTATION_TEMPLATE_PATH.as_posix()} must define a body list"
        )

    field_ids: list[str] = []
    section_labels: list[str] = []
    for field in parsed["body"]:
        if not isinstance(field, Mapping):
            raise DocumentationTemplateError("Documentation form body item is invalid")
        validations = field.get("validations")
        if (
            not isinstance(validations, Mapping)
            or validations.get("required") is not True
        ):
            continue
        if field.get("type") != "textarea":
            raise DocumentationTemplateError(
                "required Documentation fields must be textarea sections"
            )
        field_id = field.get("id")
        attributes = field.get("attributes")
        label = attributes.get("label") if isinstance(attributes, Mapping) else None
        if (
            not isinstance(field_id, str)
            or not field_id.strip()
            or not isinstance(label, str)
            or not label.strip()
        ):
            raise DocumentationTemplateError(
                "required Documentation textarea must have an id and label"
            )
        normalized_id = field_id.strip()
        normalized_label = " ".join(label.split())
        if normalized_id in field_ids or normalized_label.casefold() in {
            item.casefold() for item in section_labels
        }:
            raise DocumentationTemplateError(
                "required Documentation fields must have unique ids and labels"
            )
        field_ids.append(normalized_id)
        section_labels.append(normalized_label)

    if not section_labels:
        raise DocumentationTemplateError(
            f"{DOCUMENTATION_TEMPLATE_PATH.as_posix()} has no required sections"
        )
    return DocumentationTemplateContract(tuple(field_ids), tuple(section_labels))


def _section_details(body: str) -> tuple[tuple[str, str], ...]:
    matches = tuple(_SECTION_RE.finditer(body))
    return tuple(
        (
            " ".join(match.group(1).split()),
            body[
                match.end() : matches[index + 1].start()
                if index + 1 < len(matches)
                else None
            ].strip(),
        )
        for index, match in enumerate(matches)
    )


def documentation_contract_snapshot(
    body: str | None,
    *,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Return a bounded typed snapshot of the Documentation Issue contract."""

    try:
        template = documentation_template_contract(template_path)
    except DocumentationTemplateError as exc:
        return DocumentationContract(
            DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED,
            detail=f"Documentation template contract unavailable: {exc}",
        ).to_dict()

    if not isinstance(body, str) or not body.strip():
        return DocumentationContract(
            DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED,
            required_sections=template.section_labels,
            detail="Documentation body is unavailable",
        ).to_dict()

    section_details = _section_details(body)
    section_keys = tuple(section.casefold() for section, _content in section_details)
    required_keys = tuple(section.casefold() for section in template.section_labels)
    missing = tuple(
        required
        for required, key in zip(template.section_labels, required_keys, strict=True)
        if key not in section_keys
    )
    duplicates = tuple(
        required
        for required, key in zip(template.section_labels, required_keys, strict=True)
        if section_keys.count(key) > 1
    )
    empty = tuple(
        required
        for required, key in zip(template.section_labels, required_keys, strict=True)
        if any(
            section.casefold() == key and not content
            for section, content in section_details
        )
    )
    if missing or duplicates or empty:
        parts: list[str] = []
        if missing:
            parts.append("missing sections: " + ", ".join(missing))
        if duplicates:
            parts.append("duplicate sections: " + ", ".join(duplicates))
        if empty:
            parts.append("empty sections: " + ", ".join(empty))
        return DocumentationContract(
            DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED,
            required_sections=template.section_labels,
            missing_sections=missing,
            duplicate_sections=duplicates,
            empty_sections=empty,
            detail="; ".join(parts),
        ).to_dict()
    return DocumentationContract(
        DocumentationPolicyStatus.PASS,
        required_sections=template.section_labels,
    ).to_dict()


def is_valid_documentation_contract(value: object) -> bool:
    """Validate the bounded shape consumed by LCK eligibility."""

    if not isinstance(value, Mapping):
        return False
    try:
        template = documentation_template_contract()
    except DocumentationTemplateError:
        return False
    return (
        value.get("status") == DocumentationPolicyStatus.PASS.value
        and value.get("required_sections") == list(template.section_labels)
        and value.get("missing_sections") == []
        and value.get("duplicate_sections") == []
        and value.get("empty_sections") == []
        and value.get("template_path") == DOCUMENTATION_TEMPLATE_PATH.as_posix()
    )


def _is_allowed_path(path: str) -> bool:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        return False
    if path.rsplit("/", 1)[-1].casefold() in _EXCLUDED_AGENT_CONTROL_BASENAMES:
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
