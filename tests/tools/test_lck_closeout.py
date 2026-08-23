# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

import lck  # type: ignore[import-not-found]  # noqa: E402


SHA = "a" * 40
MERGE_SHA = "b" * 40
BRANCH = "task/162-closeout"


def _state(
    *,
    local_branch: str | None = None,
    remote_branch: str | None = None,
    remote_oid: str | None = None,
    merged_head: str = SHA,
    issue_state: str = "CLOSED",
) -> lck.LiveState:
    issue = {
        "number": 162,
        "title": "[Task] closeout",
        "state": issue_state,
        "labels": {"items": ["type:task", "codex:ready"]},
        "project_status": "Done",
    }
    merged_pr = {
        "number": 262,
        "state": "MERGED",
        "baseRefName": "main",
        "baseRefOid": SHA,
        "headRefName": BRANCH,
        "headRefOid": merged_head,
        "mergeCommit": {"oid": MERGE_SHA},
    }
    return lck.LiveState(
        task_number=162,
        repository="owner/repo",
        issue=issue,
        relationships={
            "available": True,
            "blocked_by": {"items": [], "count": 0, "truncated": False},
        },
        git={
            "branch": "main",
            "head_sha": MERGE_SHA,
            "local_main_sha": MERGE_SHA,
            "origin_main_sha": MERGE_SHA,
            "origin_fetch": "pass",
            "clean": True,
            "worktree_branches": {"items": ["main"], "count": 1},
        },
        target_branch=BRANCH,
        local_task_branch=local_branch,
        local_task_head=merged_head if local_branch else None,
        remote_task_branch=remote_branch,
        remote_task_oid=remote_oid,
        open_pr=None,
        merged_pr_numbers=(262,),
        merged=True,
        merged_pr=merged_pr,
        checks={},
        cleanup={
            "business_delivery": "complete",
            "cleanup": "pending",
        },
    )


class StaticResolver:
    def __init__(self, state: lck.LiveState) -> None:
        self.repo_root = Path.cwd()
        self.state = state
        self.runner = cast(Any, object())

    def resolve(self, _task_number: int) -> lck.LiveState:
        return self.state


class FixedEffect:
    def __init__(self, receipt: lck.EffectReceipt) -> None:
        self.receipt = receipt

    def execute(self, *_args: Any, **_kwargs: Any) -> lck.EffectReceipt:
        return self.receipt


def _receipt(effect: str, action: str) -> lck.EffectReceipt:
    return lck.EffectReceipt(effect=effect, action=action, details={})


def test_closeout_resolves_business_delivery_and_cleanup_from_live_state() -> None:
    resolver = StaticResolver(_state())
    result = lck.CloseoutCompleter(
        cast(Any, resolver),
        main_effect=cast(
            Any, FixedEffect(_receipt("synchronize_main", "synchronized"))
        ),
        metadata_effect=cast(
            Any,
            FixedEffect(_receipt("converge_task_metadata", "already-converged")),
        ),
        cleanup_effect=cast(
            Any,
            FixedEffect(_receipt("cleanup_task_refs", "already-clean")),
        ),
    ).complete(162)

    assert result.business_delivery == "COMPLETE"
    assert result.cleanup == "COMPLETE"
    assert result.status == "BUSINESS_DELIVERY_COMPLETE"


def test_closeout_keeps_business_complete_when_cleanup_is_pending() -> None:
    resolver = StaticResolver(_state())
    result = lck.CloseoutCompleter(
        cast(Any, resolver),
        main_effect=cast(Any, FixedEffect(_receipt("synchronize_main", "pending"))),
        metadata_effect=cast(
            Any,
            FixedEffect(_receipt("converge_task_metadata", "pending")),
        ),
        cleanup_effect=cast(
            Any,
            FixedEffect(_receipt("cleanup_task_refs", "pending")),
        ),
    ).complete(162)

    assert result.business_delivery == "COMPLETE"
    assert result.cleanup == "PENDING"


def test_closeout_stops_on_remote_divergence() -> None:
    state = _state(
        remote_branch=BRANCH,
        remote_oid="c" * 40,
    )
    with pytest.raises(lck.LckStopError, match="remote Task branch diverges"):
        lck.CloseoutCompleter(
            cast(Any, StaticResolver(state)),
            main_effect=cast(
                Any,
                FixedEffect(_receipt("synchronize_main", "synchronized")),
            ),
            metadata_effect=cast(
                Any,
                FixedEffect(_receipt("converge_task_metadata", "already-converged")),
            ),
            cleanup_effect=cast(
                Any,
                FixedEffect(_receipt("cleanup_task_refs", "already-clean")),
            ),
        ).complete(162)


def test_merge_preflight_has_manual_merge_boundary() -> None:
    pr = {
        "number": 262,
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "baseRefOid": SHA,
        "headRefName": BRANCH,
        "headRefOid": SHA,
        "mergeable": "MERGEABLE",
    }
    issue = {
        "number": 162,
        "title": "[Task] closeout",
        "state": "OPEN",
        "labels": {"items": ["type:task", "codex:ready"]},
        "project_status": "Review",
        "body_sha256": "d" * 64,
    }
    state = lck.LiveState(
        task_number=162,
        repository="owner/repo",
        issue=issue,
        relationships={
            "available": True,
            "blocked_by": {"items": [], "count": 0, "truncated": False},
        },
        git={
            "branch": BRANCH,
            "head_sha": SHA,
            "local_main_sha": SHA,
            "origin_main_sha": SHA,
            "origin_fetch": "pass",
            "clean": True,
        },
        target_branch=BRANCH,
        local_task_branch=BRANCH,
        local_task_head=SHA,
        remote_task_branch=BRANCH,
        remote_task_oid=SHA,
        open_pr=pr,
        merged_pr_numbers=(),
        merged=False,
        checks={},
        cleanup={},
    )

    class Review:
        def run(self, _task: int, _state: lck.LiveState) -> dict[str, Any]:
            return {"status": "pass", "review_id": "review"}

    class Checks:
        def run(self, _task: int) -> dict[str, Any]:
            return {
                "status": "pass",
                "pr": {"number": 262, "head_sha": SHA, "base_sha": SHA},
            }

    result = lck.MergePreflight(
        cast(Any, StaticResolver(state)),
        review_gate=cast(Any, Review()),
        checks_gate=cast(Any, Checks()),
    ).run(162)

    assert result.status == "READY_FOR_HUMAN_MERGE"
    assert result.to_dict()["automatic_merge"] is False
