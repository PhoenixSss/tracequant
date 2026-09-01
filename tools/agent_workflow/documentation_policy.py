"""Repository-owned policy for Documentation leaf contracts and changes.

The policy is intentionally data-driven and side-effect free.  In particular,
Issue-provided documentation context is never interpreted as a command plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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


class DocumentationPolicyStatus(StrEnum):
    PASS = "pass"
    RECLASSIFICATION_REQUIRED = "reclassification_required"


DOCUMENTATION_POLICY_ID: Final = "repository-documentation-safe-v1"
DOCUMENTATION_TEMPLATE_PATH: Final = Path(".github/ISSUE_TEMPLATE/documentation.yml")
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


DocumentationTemplateContract = IssueFormTemplateContract


class DocumentationTemplateError(IssueFormTemplateError):
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
    return load_issue_form_template(
        path,
        form_name="Documentation",
        template_display_path=DOCUMENTATION_TEMPLATE_PATH.as_posix(),
        error_type=DocumentationTemplateError,
    )


def documentation_contract_snapshot(
    body: str | None,
    *,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Return a bounded typed snapshot of the Documentation Issue contract."""

    parsed = parse_issue_form_contract(
        body,
        template_path=template_path or _default_template_path(),
        form_name="Documentation",
        template_display_path=DOCUMENTATION_TEMPLATE_PATH.as_posix(),
        error_type=DocumentationTemplateError,
        valid_status=DocumentationPolicyStatus.PASS.value,
        invalid_status=DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED.value,
    )
    return DocumentationContract(
        DocumentationPolicyStatus(parsed.status),
        required_sections=parsed.required_sections,
        missing_sections=parsed.missing_sections,
        duplicate_sections=parsed.duplicate_sections,
        empty_sections=parsed.empty_sections,
        template_path=parsed.template_path,
        detail=parsed.detail,
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
