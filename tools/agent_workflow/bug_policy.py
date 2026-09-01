"""Repository-owned policy for Bug leaf contracts.

The Bug Issue form is the authority for the minimum defect contract.  This
module validates the rendered Markdown shape and records only bounded contract
facts; it never interprets Issue content as a command or execution plan.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from issue_form_contract import (
    IssueFormTemplateContract,
    IssueFormTemplateError,
    load_issue_form_template,
    parse_issue_form_contract,
)

BUG_TEMPLATE_PATH: Final = Path(".github/ISSUE_TEMPLATE/bug.yml")
BUG_POLICY_ID: Final = "repository-bug-defect-contract-v1"


class BugPolicyStatus(StrEnum):
    """Outcomes of validating a Bug Issue defect contract."""

    PASS = "pass"
    RECLASSIFICATION_REQUIRED = "reclassification_required"


class BugTemplateError(IssueFormTemplateError):
    """The repository-owned Bug Issue form is not usable."""


BugTemplateContract = IssueFormTemplateContract


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
_NON_REPRODUCIBLE_EVIDENCE_VALUES: Final = frozenset(
    {
        "cannot reproduce",
        "cannot reproduce in current environment",
        "not reproducible",
        "not reproducible in current environment",
        "unable to reproduce",
        "无法复现",
        "无法在当前环境复现",
        "当前环境无法复现",
        "暂时无法复现",
        "暂时无法在当前环境复现",
    }
)
_FORM_PLACEHOLDER_RE: Final = re.compile(
    r"^(?:实际发生|正确行为应为|复现步骤或证据)\s*:\s*(?:[.…]+)?$",
    re.IGNORECASE,
)
_CHECKBOX_PLACEHOLDER_RE: Final = re.compile(
    r"^(?:[-*]\s*)?\[\s*(?:x| )?\s*\]\s*(?:[.…]+)?$",
    re.IGNORECASE,
)


def _default_bug_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / BUG_TEMPLATE_PATH


def bug_template_contract(
    template_path: Path | None = None,
) -> BugTemplateContract:
    """Load required Bug fields from the formal Issue form."""

    path = template_path or _default_bug_template_path()
    return load_issue_form_template(
        path,
        form_name="Bug",
        template_display_path=BUG_TEMPLATE_PATH.as_posix(),
        error_type=BugTemplateError,
    )


def _is_placeholder(content: str) -> bool:
    normalized = unicodedata.normalize("NFKC", " ".join(content.split())).casefold()
    if normalized in _PLACEHOLDER_VALUES or normalized in _VAGUE_VALUES:
        return True
    if _FORM_PLACEHOLDER_RE.fullmatch(normalized):
        return True
    # Markdown checkboxes with no actual criterion are not acceptance evidence.
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return bool(lines) and all(
        _CHECKBOX_PLACEHOLDER_RE.fullmatch(
            unicodedata.normalize("NFKC", line).casefold()
        )
        for line in lines
    )


def _is_non_reproducible_evidence(content: str) -> bool:
    """Reject evidence that only says the defect cannot currently be reproduced."""

    normalized = unicodedata.normalize("NFKC", " ".join(content.split())).casefold()
    if normalized in _NON_REPRODUCIBLE_EVIDENCE_VALUES:
        return True
    form_prefix = "复现步骤或证据:"
    return (
        normalized.startswith(form_prefix)
        and normalized.removeprefix(form_prefix).strip()
        in _NON_REPRODUCIBLE_EVIDENCE_VALUES
    )


def _insufficient_sections(
    sections: tuple[tuple[str, str], ...],
    required_labels: tuple[str, ...],
) -> tuple[str, ...]:
    """Reject required fields that contain no usable defect information."""

    required_keys = {label.casefold(): label for label in required_labels}
    insufficient: list[str] = []
    for heading, content in sections:
        label = required_keys.get(heading.casefold())
        if label is not None and (
            _is_placeholder(content)
            or (
                label.casefold() == "reproduction / evidence"
                and _is_non_reproducible_evidence(content)
            )
        ):
            insufficient.append(label)
    return tuple(insufficient)


def bug_contract_snapshot(
    body: str | None,
    *,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Return a bounded typed snapshot of the Bug Issue defect contract."""

    parsed = parse_issue_form_contract(
        body,
        template_path=template_path or _default_bug_template_path(),
        form_name="Bug",
        template_display_path=BUG_TEMPLATE_PATH.as_posix(),
        error_type=BugTemplateError,
        valid_status=BugPolicyStatus.PASS.value,
        invalid_status=BugPolicyStatus.RECLASSIFICATION_REQUIRED.value,
    )

    if parsed.template is None:
        return BugContract(
            BugPolicyStatus.RECLASSIFICATION_REQUIRED,
            detail=parsed.detail,
        ).to_dict()

    insufficient = _insufficient_sections(
        parsed.sections, parsed.template.section_labels
    )
    if not parsed.structurally_valid or insufficient:
        parts: list[str] = []
        if parsed.detail:
            parts.append(parsed.detail)
        if insufficient:
            parts.append("insufficient sections: " + ", ".join(insufficient))
        return BugContract(
            BugPolicyStatus.RECLASSIFICATION_REQUIRED,
            required_sections=parsed.required_sections,
            missing_sections=parsed.missing_sections,
            duplicate_sections=parsed.duplicate_sections,
            empty_sections=parsed.empty_sections,
            insufficient_sections=insufficient,
            detail="; ".join(parts),
        ).to_dict()
    return BugContract(
        BugPolicyStatus.PASS,
        required_sections=parsed.required_sections,
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
