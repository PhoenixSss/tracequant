from __future__ import annotations

from tools.lck import (
    models as lck_models,
)


def test_canonical_branch_is_derived_from_current_issue_title() -> None:
    assert (
        lck_models.canonical_task_branch(
            159, "[Task] 建立 LCK Core 与 Live State Resolution"
        )
        == "task/159-lck-core-live-state-resolution"
    )
