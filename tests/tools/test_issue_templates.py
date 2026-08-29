from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]  # PyYAML is an existing test dependency.

ROOT = Path(__file__).parents[2]


def _load_documentation_form() -> dict[str, Any]:
    path = ROOT / ".github" / "ISSUE_TEMPLATE" / "documentation.yml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_documentation_issue_form_is_minimum_sufficient_leaf_contract() -> None:
    form = _load_documentation_form()

    assert form["name"] == "Documentation"
    assert form["title"] == "[Documentation] "
    assert form["labels"] == ["type:documentation", "codex:needs-spec"]

    body = form["body"]
    assert isinstance(body, list)
    markdown = body[0]
    fields = body[1:]
    assert markdown["type"] == "markdown"
    guidance = markdown["attributes"]["value"]
    assert "新增、修正或收敛文档事实" in guidance
    assert "runtime、业务源码、测试、CI、Agent control behavior" in guidance
    assert "文件扩展名本身不能决定工作是否属于 Documentation" in guidance

    assert [field["type"] for field in fields] == ["textarea"] * 4
    assert [field["id"] for field in fields] == [
        "objective",
        "requirements",
        "acceptance_criteria",
        "additional_context",
    ]
    assert [field["attributes"]["label"] for field in fields] == [
        "Documentation Goal",
        "Requirements",
        "Acceptance Criteria",
        "Additional documentation context",
    ]
    assert [field["validations"]["required"] for field in fields] == [
        True,
        True,
        True,
        False,
    ]

    submitted = {
        "objective": "Clarify the current data contract.",
        "requirements": "Document the accepted timestamp and nullability rules.",
        "acceptance_criteria": "Readers can identify the contract without consulting source code.",
    }
    rendered = "\n\n".join(
        f"### {field['attributes']['label']}\n\n{submitted[field['id']]}"
        for field in fields
        if field["id"] in submitted
    )
    assert rendered == (
        "### Documentation Goal\n\nClarify the current data contract.\n\n"
        "### Requirements\n\nDocument the accepted timestamp and nullability rules.\n\n"
        "### Acceptance Criteria\n\nReaders can identify the contract without consulting source code."
    )
    assert "Additional documentation context" not in rendered
    assert "N/A" not in rendered
