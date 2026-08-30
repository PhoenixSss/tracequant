from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from critical_outcome import (  # type: ignore[import-not-found]  # noqa: E402
    CriticalOutcomeError,
    critical_outcome_snapshot,
    parse_critical_outcome,
)


def _body(
    verification: str = "tests/tools/test_lck.py::test_canonical_branch_is_derived_from_current_issue_title",
) -> str:
    return f"""### Objective
Do one thing.

### Critical Outcome
Caller: task-delivery-runner initial Delivery
Capability: LCK owns deterministic Delivery completion
Observable result: validated Task head is committed, pushed and attached to one OPEN PR
Verification test: {verification}

### Acceptance Criteria
- [ ] works
"""


def test_parse_critical_outcome_contract() -> None:
    contract = parse_critical_outcome(_body())

    assert contract.caller == "task-delivery-runner initial Delivery"
    assert contract.capability == "LCK owns deterministic Delivery completion"
    assert contract.observable_result.startswith("validated Task head")
    assert contract.verification_test.startswith("tests/tools/test_lck.py::")


@pytest.mark.parametrize("heading_level", range(1, 7))
def test_parser_accepts_all_standard_markdown_heading_levels(
    heading_level: int,
) -> None:
    body = _body().replace(
        "### Critical Outcome",
        f"{'#' * heading_level} Critical Outcome",
    )

    assert parse_critical_outcome(body).capability == (
        "LCK owns deterministic Delivery completion"
    )


def test_parser_rejects_plain_text_critical_outcome_marker() -> None:
    body = _body().replace("### Critical Outcome", "Critical Outcome")

    with pytest.raises(CriticalOutcomeError, match="exactly one"):
        parse_critical_outcome(body)


def test_parser_rejects_duplicate_critical_outcome_headings_across_levels() -> None:
    body = _body().replace(
        "### Acceptance Criteria",
        "## Critical Outcome\n\n### Acceptance Criteria",
    )

    with pytest.raises(CriticalOutcomeError, match="exactly one"):
        parse_critical_outcome(body)


def test_parser_ignores_fenced_critical_outcome_heading() -> None:
    body = """### Objective
Do one thing.

```markdown
## Critical Outcome
Caller: fake
Capability: fake
Observable result: fake
Verification test: tests/tools/test_lck.py::test_fake
```

### Critical Outcome
Caller: real
Capability: bounded effect
Observable result: observable result
Verification test: tests/tools/test_lck.py::test_canonical_branch_is_derived_from_current_issue_title
"""

    assert parse_critical_outcome(body).caller == "real"


def test_parser_accepts_markdown_bullet_and_bold_labels() -> None:
    body = """### Critical Outcome
- **Caller:** CLI
- **Capability:** bounded effect
- **Observable result:** observable result
- **Verification test:** `tests/tools/test_lck.py::test_canonical_branch_is_derived_from_current_issue_title`
"""
    assert parse_critical_outcome(body).caller == "CLI"


@pytest.mark.parametrize(
    "body",
    [
        "### Objective\nmissing",
        "### Critical Outcome\nCaller: x\nCapability: y\nObservable result: z",
        _body("pytest tests/tools/test_lck.py"),
        _body("../../tmp/test_bad.py::test_bad"),
        _body("tests/tools/test_lck.py::test_x && rm -rf /"),
    ],
)
def test_invalid_or_unsafe_contract_fails_closed(body: str) -> None:
    with pytest.raises(CriticalOutcomeError):
        parse_critical_outcome(body)


def test_snapshot_is_compact_and_does_not_copy_issue_body() -> None:
    snapshot = critical_outcome_snapshot(_body())

    assert snapshot["status"] == "valid"
    assert "body" not in snapshot
    assert snapshot["contract"]["verification_test"].startswith("tests/")


def test_task_issue_template_requires_critical_outcome() -> None:
    root = Path(__file__).parents[2]
    text = (root / ".github/ISSUE_TEMPLATE/task.yml").read_text(encoding="utf-8")
    marker = "id: critical_outcome"
    assert marker in text
    section = text.split(marker, 1)[1].split("  - type:", 1)[0]
    assert "label: Critical Outcome" in section
    assert "required: true" in section
    assert "Verification test: tests/.../test_*.py::test_*" in section
