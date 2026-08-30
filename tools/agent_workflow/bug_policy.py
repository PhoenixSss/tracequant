"""Repository-owned policy for Bug leaf contracts.

The Bug Issue form is the authority for the minimum defect contract.  This
module validates the rendered Markdown shape and records only bounded contract
facts; it never interprets Issue content as a command or execution plan.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import yaml

BUG_TEMPLATE_PATH: Final = Path(".github/ISSUE_TEMPLATE/bug.yml")
BUG_POLICY_ID: Final = "repository-bug-defect-contract-v1"


class BugPolicyStatus(StrEnum):
    """Outcomes of validating a Bug Issue defect contract."""

    PASS = "pass"
    RECLASSIFICATION_REQUIRED = "reclassification_required"


class BugTemplateError(ValueError):
    """The repository-owned Bug Issue form is not usable."""


@dataclass(frozen=True)
class BugTemplateContract:
    """Required Bug sections declared by the formal Issue form."""

    field_ids: tuple[str, ...]
    section_labels: tuple[str, ...]


@dataclass(frozen=True)
class BugContract:
    status: BugPolicyStatus
    required_sections: tuple[str, ...] = ()
    missing_sections: tuple[str, ...] = ()
    duplicate_sections: tuple[str, ...] = ()
    empty_sections: tuple[str, ...] = ()
    insufficient_sections: tuple[str, ...] = ()
    template_path: str = BUG_TEMPLATE_PATH.as_posix()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "required_sections": list(self.required_sections),
            "missing_sections": list(self.missing_sections),
            "duplicate_sections": list(self.duplicate_sections),
            "empty_sections": list(self.empty_sections),
            "insufficient_sections": list(self.insufficient_sections),
            "template_path": self.template_path,
            **({"detail": self.detail} if self.detail else {}),
        }


_SECTION_RE: Final = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_PLACEHOLDER_VALUES: Final = frozenset(
    {
        "...",
        "-",
        "n/a",
        "na",
        "none",
        "not applicable",
        "tbd",
        "todo",
        "unknown",
        "无",
        "未知",
        "待定",
    }
)
_VAGUE_VALUES: Final = frozenset(
    {
        "as expected",
        "behave correctly",
        "correct behavior",
        "it works",
        "it should work",
        "正常",
        "正常工作",
        "应该正常",
        "should work",
        "works",
        "works as expected",
    }
)


def _default_bug_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / BUG_TEMPLATE_PATH


def bug_template_contract(
    template_path: Path | None = None,
) -> BugTemplateContract:
    """Load required Bug fields from the formal Issue form."""

    path = template_path or _default_bug_template_path()
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BugTemplateError(
            f"cannot read {BUG_TEMPLATE_PATH.as_posix()}: {exc}"
        ) from exc
    if not isinstance(parsed, Mapping) or not isinstance(parsed.get("body"), list):
        raise BugTemplateError(
            f"{BUG_TEMPLATE_PATH.as_posix()} must define a body list"
        )

    field_ids: list[str] = []
    section_labels: list[str] = []
    for field in parsed["body"]:
        if not isinstance(field, Mapping):
            raise BugTemplateError("Bug form body item is invalid")
        validations = field.get("validations")
        if (
            not isinstance(validations, Mapping)
            or validations.get("required") is not True
        ):
            continue
        if field.get("type") != "textarea":
            raise BugTemplateError("required Bug fields must be textarea sections")
        field_id = field.get("id")
        attributes = field.get("attributes")
        label = attributes.get("label") if isinstance(attributes, Mapping) else None
        if (
            not isinstance(field_id, str)
            or not field_id.strip()
            or not isinstance(label, str)
            or not label.strip()
        ):
            raise BugTemplateError("required Bug textarea must have an id and label")
        normalized_id = field_id.strip()
        normalized_label = " ".join(label.split())
        if normalized_id in field_ids or normalized_label.casefold() in {
            item.casefold() for item in section_labels
        }:
            raise BugTemplateError(
                "required Bug fields must have unique ids and labels"
            )
        field_ids.append(normalized_id)
        section_labels.append(normalized_label)

    if not section_labels:
        raise BugTemplateError(
            f"{BUG_TEMPLATE_PATH.as_posix()} has no required sections"
        )
    return BugTemplateContract(tuple(field_ids), tuple(section_labels))


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


def _is_placeholder(content: str) -> bool:
    normalized = " ".join(content.split()).casefold()
    if normalized in _PLACEHOLDER_VALUES or normalized in _VAGUE_VALUES:
        return True
    # Markdown checkboxes with no actual criterion are not acceptance evidence.
    return bool(re.fullmatch(r"(?:[-*]\s*)?\[\s*[xX ]?\s*\]", normalized))


def _insufficient_sections(
    sections: tuple[tuple[str, str], ...],
    required_labels: tuple[str, ...],
) -> tuple[str, ...]:
    """Reject required fields that contain no usable defect information."""

    required_keys = {label.casefold(): label for label in required_labels}
    insufficient: list[str] = []
    for heading, content in sections:
        label = required_keys.get(heading.casefold())
        if label is not None and _is_placeholder(content):
            insufficient.append(label)
    return tuple(insufficient)


def bug_contract_snapshot(
    body: str | None,
    *,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Return a bounded typed snapshot of the Bug Issue defect contract."""

    try:
        template = bug_template_contract(template_path)
    except BugTemplateError as exc:
        return BugContract(
            BugPolicyStatus.RECLASSIFICATION_REQUIRED,
            detail=f"Bug template contract unavailable: {exc}",
        ).to_dict()

    if not isinstance(body, str) or not body.strip():
        return BugContract(
            BugPolicyStatus.RECLASSIFICATION_REQUIRED,
            required_sections=template.section_labels,
            detail="Bug body is unavailable",
        ).to_dict()

    sections = _section_details(body)
    section_keys = tuple(section.casefold() for section, _content in sections)
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
            section.casefold() == key and not content for section, content in sections
        )
    )
    insufficient = _insufficient_sections(sections, template.section_labels)
    if missing or duplicates or empty or insufficient:
        parts: list[str] = []
        if missing:
            parts.append("missing sections: " + ", ".join(missing))
        if duplicates:
            parts.append("duplicate sections: " + ", ".join(duplicates))
        if empty:
            parts.append("empty sections: " + ", ".join(empty))
        if insufficient:
            parts.append("insufficient sections: " + ", ".join(insufficient))
        return BugContract(
            BugPolicyStatus.RECLASSIFICATION_REQUIRED,
            required_sections=template.section_labels,
            missing_sections=missing,
            duplicate_sections=duplicates,
            empty_sections=empty,
            insufficient_sections=insufficient,
            detail="; ".join(parts),
        ).to_dict()
    return BugContract(
        BugPolicyStatus.PASS,
        required_sections=template.section_labels,
    ).to_dict()


def is_valid_bug_contract(value: object) -> bool:
    """Validate the bounded Bug contract consumed by LCK eligibility."""

    if not isinstance(value, Mapping):
        return False
    try:
        template = bug_template_contract()
    except BugTemplateError:
        return False
    return (
        value.get("status") == BugPolicyStatus.PASS.value
        and value.get("required_sections") == list(template.section_labels)
        and value.get("missing_sections") == []
        and value.get("duplicate_sections") == []
        and value.get("empty_sections") == []
        and value.get("insufficient_sections") == []
        and value.get("template_path") == BUG_TEMPLATE_PATH.as_posix()
    )
