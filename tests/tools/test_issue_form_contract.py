# ruff: noqa: E402

"""Tests for the profile-neutral Issue Form contract parser."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from issue_form_contract import (  # type: ignore[import-not-found]
    IssueFormTemplateError,
    issue_form_contract_snapshot,
    load_issue_form_template,
)

TEMPLATE = """\
name: Synthetic form
body:
  - type: markdown
    attributes:
      value: ignored
  - type: textarea
    id: first_field
    attributes:
      label: First Field
    validations:
      required: true
  - type: textarea
    id: second_field
    attributes:
      label: Second Field
    validations:
      required: true
  - type: textarea
    id: optional_field
    attributes:
      label: Optional Field
    validations:
      required: false
"""


def _write_template(tmp_path: Path, content: str = TEMPLATE) -> Path:
    path = tmp_path / "synthetic.yml"
    path.write_text(content, encoding="utf-8")
    return path


def _body(*, first: str = "first content", second: str = "second content") -> str:
    return f"# First Field\n\n{first}\n\n###    Second Field\n\n{second}"


def test_generic_parser_reads_required_textareas_and_equivalent_heading_spacing(
    tmp_path: Path,
) -> None:
    path = _write_template(tmp_path)

    template = load_issue_form_template(path, form_name="Synthetic")
    snapshot = issue_form_contract_snapshot(
        _body(),
        template_path=path,
        form_name="Synthetic",
        template_display_path="synthetic.yml",
    )

    assert template.field_ids == ("first_field", "second_field")
    assert template.section_labels == ("First Field", "Second Field")
    assert snapshot == {
        "status": "pass",
        "required_sections": ["First Field", "Second Field"],
        "missing_sections": [],
        "duplicate_sections": [],
        "empty_sections": [],
        "template_path": "synthetic.yml",
    }


@pytest.mark.parametrize(
    "template",
    (
        "body: [",
        "body: []",
        "body:\n  - type: textarea\n    id: one\n    attributes:\n      label: One\n    validations:\n      required: true\n  - type: textarea\n    id: one\n    attributes:\n      label: Two\n    validations:\n      required: true\n",
        "body:\n  - type: textarea\n    id: one\n    attributes:\n      label: One\n    validations:\n      required: true\n  - type: textarea\n    id: two\n    attributes:\n      label:  One  \n    validations:\n      required: true\n",
    ),
)
def test_template_failures_are_rejected_before_rendered_contract_parsing(
    tmp_path: Path,
    template: str,
) -> None:
    with pytest.raises(IssueFormTemplateError):
        load_issue_form_template(_write_template(tmp_path, template))


@pytest.mark.parametrize(
    ("body", "field", "detail"),
    (
        (_body(second=""), "Second Field", "empty sections: Second Field"),
        ("# First Field\n\nfirst content", "Second Field", "missing sections"),
        (
            _body() + "\n\n## First Field\n\nconflict",
            "First Field",
            "duplicate sections: First Field",
        ),
    ),
)
def test_rendered_contract_failures_are_bounded_and_fail_closed(
    tmp_path: Path,
    body: str,
    field: str,
    detail: str,
) -> None:
    snapshot = issue_form_contract_snapshot(
        body,
        template_path=_write_template(tmp_path),
        form_name="Synthetic",
        template_display_path="synthetic.yml",
    )

    assert snapshot["status"] == "reclassification_required"
    assert field in (
        snapshot["missing_sections"]
        + snapshot["duplicate_sections"]
        + snapshot["empty_sections"]
    )
    assert detail in snapshot["detail"]
