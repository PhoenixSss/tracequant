# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    models as lck_models,
)


def test_canonical_branch_is_derived_from_current_issue_title() -> None:
    assert (
        lck_models.canonical_task_branch(
            159, "[Task] 建立 LCK Core 与 Live State Resolution"
        )
        == "task/159-lck-core-live-state-resolution"
    )
