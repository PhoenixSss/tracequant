# ruff: noqa: E402, I001

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
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
        "issue_closure": {
            "evidence_status": "complete",
            "status": "closed-by-pr",
            "closer_repository": "owner/repo",
            "closer_number": 262,
        },
    }
    merged_pr = {
        "number": 262,
        "state": "MERGED",
        "baseRefName": "main",
        "baseRefOid": SHA,
        "headRefName": BRANCH,
        "headRefOid": merged_head,
        "mergeCommit": {"oid": MERGE_SHA},
        "mergedAt": "2026-08-23T00:00:00Z",
        "closingIssuesReferences": [{"number": 162}],
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
        checks={
            "count": 0,
            "failed": 0,
            "pending": 0,
            "skipped_or_unknown": 0,
            "all_success": True,
        },
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


class SequenceResolver:
    def __init__(self, *states: lck.LiveState) -> None:
        self.repo_root = Path.cwd()
        self.states = states
        self.index = 0
        self.runner = cast(Any, object())

    def resolve(self, _task_number: int) -> lck.LiveState:
        state = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        return state


class StaticReviewStore:
    def __init__(self, *, head_sha: str = SHA) -> None:
        self.latest = {"task_number": 162, "review_id": "review", "verdict": "PASS"}
        self.record = {
            "task_number": 162,
            "review_id": "review",
            "verdict": "PASS",
            "status": "READY_FOR_HUMAN_MERGE",
            "identity": {
                "task_number": 162,
                "pr_number": 262,
                "base_sha": SHA,
                "head_sha": head_sha,
                "task_body_sha256": "d" * 64,
                "merge_base_sha": SHA,
                "effective_diff_sha256": "e" * 64,
                "changed_files": [],
            },
        }

    def read_latest_review(self, _task_number: int) -> dict[str, Any]:
        return self.latest

    def read_record(self, _task_number: int, _review_id: str) -> dict[str, Any]:
        return self.record


class RecordingRunner:
    def __init__(
        self,
        returncode: int = 0,
        *,
        pr_view: str = "",
        remote_outputs: list[str] | None = None,
    ) -> None:
        self.returncode = returncode
        self.pr_view = pr_view
        self.remote_outputs = list(remote_outputs or [])
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: list[str], **_kwargs: Any) -> Any:
        self.calls.append(tuple(argv))
        if argv[:3] == ["gh", "pr", "view"]:
            stdout = self.pr_view
        elif argv[:3] == ["git", "ls-remote", "--heads"] and self.remote_outputs:
            stdout = self.remote_outputs.pop(0)
        else:
            stdout = ""
        return SimpleNamespace(returncode=self.returncode, stdout=stdout, stderr="")


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
        review_store=cast(Any, StaticReviewStore()),
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
        review_store=cast(Any, StaticReviewStore()),
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
            review_store=cast(Any, StaticReviewStore()),
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
        checks={
            "count": 0,
            "failed": 0,
            "pending": 0,
            "skipped_or_unknown": 0,
            "all_success": True,
        },
        cleanup={},
    )

    class Review:
        def run(self, _task: int, _state: lck.LiveState) -> dict[str, Any]:
            return {"status": "pass", "review_id": "review"}

    class Checks:
        def run(self, _task: int) -> dict[str, Any]:
            return {
                "status": "pass",
                "configuration": "not-configured",
                "required": [],
                "observed": {},
                "pr": {"number": 262, "head_sha": SHA, "base_sha": SHA},
            }

    result = lck.MergePreflight(
        cast(Any, StaticResolver(state)),
        review_gate=cast(Any, Review()),
        checks_gate=cast(Any, Checks()),
    ).run(162)

    assert result.status == "READY_FOR_HUMAN_MERGE"
    assert result.to_dict()["automatic_merge"] is False


def test_merge_preflight_rechecks_project_status_after_checks() -> None:
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
    }
    state = lck.LiveState(
        task_number=162,
        repository="owner/repo",
        issue=issue,
        relationships={
            "available": True,
            "blocked_by": {"items": [], "count": 0, "truncated": False},
        },
        git={"branch": BRANCH, "clean": True},
        target_branch=BRANCH,
        local_task_branch=BRANCH,
        local_task_head=SHA,
        remote_task_branch=BRANCH,
        remote_task_oid=SHA,
        open_pr=pr,
        merged_pr_numbers=(),
        merged=False,
        checks={
            "count": 0,
            "failed": 0,
            "pending": 0,
            "skipped_or_unknown": 0,
            "all_success": True,
        },
        cleanup={},
    )
    changed = replace(state, issue={**issue, "project_status": "In Progress"})

    class Review:
        def run(self, _task: int, _state: lck.LiveState) -> dict[str, Any]:
            return {"status": "pass", "review_id": "review"}

    class Checks:
        def run(self, _task: int) -> dict[str, Any]:
            return {
                "status": "pass",
                "configuration": "not-configured",
                "required": [],
                "observed": {},
                "pr": {"number": 262, "head_sha": SHA, "base_sha": SHA},
            }

    with pytest.raises(lck.LckStopError, match="PR identity changed"):
        lck.MergePreflight(
            cast(Any, SequenceResolver(state, changed)),
            review_gate=cast(Any, Review()),
            checks_gate=cast(Any, Checks()),
        ).run(162)


def test_closeout_requires_reviewed_head_identity() -> None:
    state = _state()
    with pytest.raises(lck.LckStopError, match="does not match the latest Review PASS"):
        lck.CloseoutCompleter(
            cast(Any, StaticResolver(state)),
            review_store=cast(Any, StaticReviewStore(head_sha="c" * 40)),
        ).complete(162)


def test_closeout_stops_on_incomplete_merge_identity() -> None:
    state = _state()
    incomplete = dict(cast(dict[str, Any], state.merged_pr))
    incomplete.pop("mergeCommit")
    with pytest.raises(lck.LckStopError, match="identity is incomplete"):
        lck.CloseoutCompleter(
            cast(Any, StaticResolver(replace(state, merged_pr=incomplete))),
            review_store=cast(Any, StaticReviewStore()),
        ).complete(162)


def test_cleanup_proves_squash_tree_when_refs_are_already_deleted() -> None:
    runner = RecordingRunner()
    resolver = StaticResolver(_state())
    resolver.runner = cast(Any, runner)
    result = lck.CleanupTaskRefsEffect(cast(Any, resolver)).execute(
        _state(),
        expected_head_sha=SHA,
        merge_sha=MERGE_SHA,
    )

    assert result.action == "already-clean"
    assert ("git", "diff", "--quiet", SHA, MERGE_SHA) in runner.calls


def test_cleanup_rechecks_tip_before_non_force_remote_deletion() -> None:
    runner = RecordingRunner(
        remote_outputs=[f"{SHA}\trefs/heads/{BRANCH}\n", ""],
    )
    resolver = StaticResolver(_state(remote_branch=BRANCH, remote_oid=SHA))
    resolver.runner = cast(Any, runner)

    result = lck.CleanupTaskRefsEffect(cast(Any, resolver)).execute(
        _state(remote_branch=BRANCH, remote_oid=SHA),
        expected_head_sha=SHA,
        merge_sha=MERGE_SHA,
    )

    assert result.action == "cleaned"
    delete_call = next(call for call in runner.calls if "--delete" in call)
    assert delete_call == ("git", "push", "origin", "--delete", BRANCH)
    assert all("force" not in item for item in delete_call)


def test_cleanup_stops_when_final_live_state_is_unresolved() -> None:
    state = _state()
    unresolved = replace(
        state,
        status=lck.ResolutionStatus.STOP,
        stop_reasons=("ambiguous recovery",),
    )
    resolver = SequenceResolver(state, state, state, unresolved)

    with pytest.raises(lck.LckStopError, match="final live state is unresolved"):
        lck.CloseoutCompleter(
            cast(Any, resolver),
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
            review_store=cast(Any, StaticReviewStore()),
        ).complete(162)


def test_resolver_recovers_deleted_noncanonical_branch_from_closing_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = "PhoenixSss/tracequant"
    branch = "task/162-merge-preflightcloseout-recovery-lck"
    pr = {
        "number": 167,
        "url": f"https://github.com/{repository}/pull/167",
        "state": "MERGED",
        "isDraft": False,
        "baseRefName": "main",
        "baseRefOid": SHA,
        "headRefName": branch,
        "headRefOid": SHA,
        "mergeCommit": {"oid": MERGE_SHA},
        "mergedAt": "2026-08-23T00:00:00Z",
        "headRepository": {"nameWithOwner": repository},
        "closingIssuesReferences": [{"number": 162}],
    }
    issue = {
        "number": 162,
        "title": "[Task] 将 Merge Preflight、Closeout 与 Recovery 迁移至 LCK",
        "state": "CLOSED",
        "issue_closure": {
            "evidence_status": "complete",
            "closed_by_pull_requests": {
                "items": [
                    {
                        "number": 167,
                        "state": "MERGED",
                        "merged": True,
                        "url": pr["url"],
                        "repository": repository,
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        },
    }
    runner = RecordingRunner(pr_view=json.dumps(pr))
    resolver = lck.LiveStateResolver(
        Path.cwd(), runner=cast(Any, runner), repository=repository
    )

    monkeypatch.setattr(lck, "_issue_view", lambda *_args: issue)
    monkeypatch.setattr(
        lck,
        "_relationship_snapshot",
        lambda *_args: {
            "available": True,
            "blocked_by": {"items": [], "count": 0, "truncated": False},
        },
    )
    monkeypatch.setattr(
        lck,
        "_git_snapshot",
        lambda *_args, **_kwargs: {
            "origin_fetch": "pass",
            "branch": "main",
            "head_sha": MERGE_SHA,
            "local_main_sha": MERGE_SHA,
            "origin_main_sha": MERGE_SHA,
            "clean": True,
            "worktree_branches": {"items": ["main"], "count": 1},
        },
    )
    monkeypatch.setattr(
        resolver,
        "_task_branches",
        lambda *_args: (set(), {}, True),
    )
    open_pr_branches: list[str] = []
    history_branches: list[str] = []

    def resolve_open(
        _runner: Any,
        _repository: str,
        current_branch: str,
        _base_branch: str,
        _warnings: list[dict[str, Any]],
    ) -> None:
        open_pr_branches.append(current_branch)
        return None

    def list_history(
        _runner: Any,
        _repository: str,
        current_branch: str,
        _base_branch: str,
        _warnings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        history_branches.append(current_branch)
        return [pr]

    monkeypatch.setattr(lck, "resolve_open_pr", resolve_open)
    monkeypatch.setattr(lck, "list_matching_prs", list_history)

    state = resolver.resolve(162)

    assert state.status is lck.ResolutionStatus.RESOLVED
    assert state.target_branch == branch
    assert state.merged is True
    assert state.merged_pr == pr
    assert open_pr_branches == [branch]
    assert history_branches == [branch]
