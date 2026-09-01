"""Generic parsing for repository-owned typed Issue Form contracts.

This module owns the structural contract shared by typed Issue policies:
required textarea definitions, field uniqueness, and rendered Markdown section
shape.  It deliberately knows nothing about a concrete Issue profile or its
domain-specific evidence rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]
from markdown_sections import extract_markdown_sections


class IssueFormTemplateError(ValueError):
    """A repository-owned Issue Form cannot define a usable contract."""


@dataclass(frozen=True)
class IssueFormTemplateContract:
    """Required textarea fields declared by an Issue Form."""

    field_ids: tuple[str, ...]
    section_labels: tuple[str, ...]


@dataclass(frozen=True)
class IssueFormContractParseResult:
    """Structural result plus private section details for domain policies."""

    status: str
    required_sections: tuple[str, ...] = ()
    missing_sections: tuple[str, ...] = ()
    duplicate_sections: tuple[str, ...] = ()
    empty_sections: tuple[str, ...] = ()
    template_path: str = ""
    detail: str = ""
    template: IssueFormTemplateContract | None = None
    sections: tuple[tuple[str, str], ...] = ()

    @property
    def structurally_valid(self) -> bool:
        """Return whether the Issue Form and rendered sections are usable."""

        return (
            self.template is not None
            and self.status == "pass"
            and not self.missing_sections
            and not self.duplicate_sections
            and not self.empty_sections
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the bounded, profile-neutral structural snapshot."""

        return {
            "status": self.status,
            "required_sections": list(self.required_sections),
            "missing_sections": list(self.missing_sections),
            "duplicate_sections": list(self.duplicate_sections),
            "empty_sections": list(self.empty_sections),
            "template_path": self.template_path,
            **({"detail": self.detail} if self.detail else {}),
        }


_RECLASSIFICATION_REQUIRED: Final = "reclassification_required"
_PASS: Final = "pass"


def load_issue_form_template(
    path: Path,
    *,
    form_name: str = "Issue",
    template_display_path: str | None = None,
    error_type: type[IssueFormTemplateError] = IssueFormTemplateError,
) -> IssueFormTemplateContract:
    """Load and validate required textarea fields from one Issue Form YAML."""

    display_path = template_display_path or path.as_posix()
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise error_type(f"cannot read {display_path}: {exc}") from exc
    if not isinstance(parsed, Mapping) or not isinstance(parsed.get("body"), list):
        raise error_type(f"{display_path} must define a body list")

    field_ids: list[str] = []
    section_labels: list[str] = []
    normalized_ids: set[str] = set()
    normalized_labels: set[str] = set()
    for field in parsed["body"]:
        if not isinstance(field, Mapping):
            raise error_type(f"{form_name} form body item is invalid")
        validations = field.get("validations")
        if (
            not isinstance(validations, Mapping)
            or validations.get("required") is not True
        ):
            continue
        if field.get("type") != "textarea":
            raise error_type(f"required {form_name} fields must be textarea sections")
        field_id = field.get("id")
        attributes = field.get("attributes")
        label = attributes.get("label") if isinstance(attributes, Mapping) else None
        if (
            not isinstance(field_id, str)
            or not field_id.strip()
            or not isinstance(label, str)
            or not label.strip()
        ):
            raise error_type(f"required {form_name} textarea must have an id and label")

        normalized_id = field_id.strip()
        normalized_label = " ".join(label.split())
        normalized_id_key = normalized_id.casefold()
        normalized_label_key = normalized_label.casefold()
        if (
            normalized_id_key in normalized_ids
            or normalized_label_key in normalized_labels
        ):
            raise error_type(
                f"required {form_name} fields must have unique ids and labels"
            )
        normalized_ids.add(normalized_id_key)
        normalized_labels.add(normalized_label_key)
        field_ids.append(normalized_id)
        section_labels.append(normalized_label)

    if not section_labels:
        raise error_type(f"{display_path} has no required sections")
    return IssueFormTemplateContract(tuple(field_ids), tuple(section_labels))


def parse_issue_form_contract(
    body: str | None,
    *,
    template_path: Path,
    form_name: str = "Issue",
    template_display_path: str | None = None,
    error_type: type[IssueFormTemplateError] = IssueFormTemplateError,
    valid_status: str = _PASS,
    invalid_status: str = _RECLASSIFICATION_REQUIRED,
) -> IssueFormContractParseResult:
    """Parse one Issue Form and its rendered Markdown structural contract.

    The returned ``sections`` are available to a typed policy for additional
    domain validation but are intentionally omitted from ``to_dict``.
    """

    display_path = template_display_path or template_path.as_posix()
    try:
        template = load_issue_form_template(
            template_path,
            form_name=form_name,
            template_display_path=display_path,
            error_type=error_type,
        )
    except IssueFormTemplateError as exc:
        return IssueFormContractParseResult(
            invalid_status,
            template_path=display_path,
            detail=f"{form_name} template contract unavailable: {exc}",
        )

    if not isinstance(body, str) or not body.strip():
        return IssueFormContractParseResult(
            invalid_status,
            required_sections=template.section_labels,
            template_path=display_path,
            template=template,
            detail=f"{form_name} body is unavailable",
        )

    sections = tuple(
        (section.name, section.content)
        for section in extract_markdown_sections(
            body, canonical_names=template.section_labels
        )
    )
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
    if missing or duplicates or empty:
        parts: list[str] = []
        if missing:
            parts.append("missing sections: " + ", ".join(missing))
        if duplicates:
            parts.append("duplicate sections: " + ", ".join(duplicates))
        if empty:
            parts.append("empty sections: " + ", ".join(empty))
        return IssueFormContractParseResult(
            invalid_status,
            required_sections=template.section_labels,
            missing_sections=missing,
            duplicate_sections=duplicates,
            empty_sections=empty,
            template_path=display_path,
            template=template,
            sections=sections,
            detail="; ".join(parts),
        )
    return IssueFormContractParseResult(
        valid_status,
        required_sections=template.section_labels,
        template_path=display_path,
        template=template,
        sections=sections,
    )


def issue_form_contract_snapshot(
    body: str | None,
    *,
    template_path: Path,
    form_name: str = "Issue",
    template_display_path: str | None = None,
    error_type: type[IssueFormTemplateError] = IssueFormTemplateError,
    valid_status: str = _PASS,
    invalid_status: str = _RECLASSIFICATION_REQUIRED,
) -> dict[str, Any]:
    """Return only the bounded structural snapshot for one Issue Form."""

    return parse_issue_form_contract(
        body,
        template_path=template_path,
        form_name=form_name,
        template_display_path=template_display_path,
        error_type=error_type,
        valid_status=valid_status,
        invalid_status=invalid_status,
    ).to_dict()


# Readability aliases for callers that distinguish template loading from the
# complete rendered contract parse.
parse_issue_form_template = load_issue_form_template
issue_form_template_contract = load_issue_form_template
