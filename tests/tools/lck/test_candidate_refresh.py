from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from tools.lck import closeout as lck_closeout
from tools.lck import delivery as lck_delivery
from tools.lck import models as lck_models
from tools.lck import refresh as lck_refresh
from tools.lck import remediation as lck_remediation
from tools.lck import review as lck_review
from tools.lck import review_workspace as lck_review_workspace
from tools.lck.common import CommandRunner

from .support import StaticResolver, _issue, _open_pr, _relationships


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _repository(
    tmp_path: Path, *, conflict: bool = False
) -> tuple[Path, str, str, str]:
    root = tmp_path / "repo"
    branch = "task/159-lck-core-live-state-resolution"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "lck@example.test")
    _git(root, "config", "user.name", "LCK Test")
    _write(root / ".gitignore", ".workflow.local/\n")
    _write(root / "shared.txt", "base\n")
    _git(root, "add", ".gitignore", "shared.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "switch", "-c", branch)
    _write(root / "task.txt", "task behavior\n")
    if conflict:
        _write(root / "shared.txt", "task version\n")
    _git(root, "add", "task.txt", "shared.txt")
    _git(root, "commit", "-m", "task")
    start_head = _git(root, "rev-parse", "HEAD")
    _git(root, "switch", "main")
    _write(root / "dependency.txt", "dependency contract\n")
    if conflict:
        _write(root / "shared.txt", "main version\n")
    _git(root, "add", "dependency.txt", "shared.txt")
    _git(root, "commit", "-m", "dependency")
    main_head = _git(root, "rev-parse", "HEAD")
    _git(root, "switch", branch)
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "origin", "main", branch)
    return root, branch, start_head, main_head


def _state(
    branch: str,
    start_head: str,
    main_head: str,
    *,
    clean: bool,
    blocked: bool = False,
) -> lck_models.LiveState:
    issue = _issue()
    issue.update({"project_status": "Review", "body_sha256": "d" * 64})
    pr = _open_pr(branch)
    pr.update({"headRefOid": start_head, "baseRefOid": main_head})
    relationships = _relationships(
        blocked_by={
            "items": ([{"number": 88, "state": "OPEN"}] if blocked else []),
            "count": 1 if blocked else 0,
            "truncated": False,
        }
    )
    return lck_models.LiveState(
        task_number=159,
        repository="owner/repo",
        issue=issue,
        relationships=relationships,
        git={
            "branch": branch,
            "head_sha": start_head,
            "local_main_sha": main_head,
            "remote_main_sha": main_head,
            "origin_fetch": "pass",
            "clean": clean,
        },
        target_branch=branch,
        local_task_branch=branch,
        local_task_head=start_head,
        remote_task_branch=branch,
        remote_task_oid=start_head,
        open_pr=pr,
        merged_pr_numbers=(),
        merged=False,
        checks={"count": 0, "all_success": True},
        cleanup={},
        task_contract={
            "number": 159,
            "title": issue["title"],
            "body": "Task Contract",
            "body_sha256": "d" * 64,
            "critical_outcome": issue["critical_outcome"],
        },
    )


def _resolver(root: Path, state: lck_models.LiveState) -> StaticResolver:
    resolver = StaticResolver(root, state)
    resolver.runner = cast(Any, CommandRunner(root))
    return resolver


class _Snapshots:
    def __init__(self, state: lck_models.LiveState) -> None:
        self.state = state

    def acquire(
        self, _task: int, *, operation: str, include_required_checks: bool = False
    ) -> lck_models.OperationSnapshot:
        return lck_models.OperationSnapshot(
            operation=operation,
            state=self.state,
            required_checks=(
                {
                    "status": "pass",
                    "names": ["quality"],
                    "source_sha": self.state.git["remote_main_sha"],
                }
                if include_required_checks
                else None
            ),
            fact_profile=operation.casefold().replace(" ", "-"),
        )


def _fake_delivery_factory(root: Path, *, fail_after_commit: bool = False) -> Any:
    class FakeDelivery:
        def __init__(
            self, _resolver: Any, *, candidate_recorder: Any, **_kwargs: Any
        ) -> None:
            self.candidate_recorder = candidate_recorder
            self.last_effects: list[lck_models.EffectReceipt] = []
            self.last_validation: dict[str, Any] | None = None
            self.last_checks: dict[str, Any] | None = None
            self.last_critical_outcome: dict[str, Any] | None = None

        def complete(
            self,
            task_number: int,
            *,
            operation_snapshot: lck_models.OperationSnapshot,
            commit_message: str,
            **_kwargs: Any,
        ) -> lck_delivery.DeliveryCompletionResult:
            if _git(root, "status", "--porcelain=v1"):
                _git(root, "commit", "-m", commit_message)
            head = _git(root, "rev-parse", "HEAD")
            tree = _git(root, "rev-parse", "HEAD^{tree}")
            self.candidate_recorder(head, tree)
            if fail_after_commit:
                raise lck_models.LckStopError("simulated push interruption")
            base = str(operation_snapshot.state.git["remote_main_sha"])
            effects = [
                lck_models.EffectReceipt(
                    effect="ensure_remote_branch",
                    action="fast-forwarded",
                    details={"head_sha": head, "remote_oid": head},
                ),
                lck_models.EffectReceipt(
                    effect="reuse_open_pr",
                    action="reused-current-open-pr",
                    details={"number": 200, "head_sha": head, "base_sha": base},
                ),
            ]
            self.last_effects = effects
            self.last_validation = {"status": "pass"}
            self.last_checks = {"status": "pass"}
            self.last_critical_outcome = {"status": "pass"}
            return lck_delivery.DeliveryCompletionResult(
                task_number=task_number,
                status="READY_FOR_REVIEW",
                branch=str(operation_snapshot.state.target_branch),
                head_sha=head,
                critical_outcome=self.last_critical_outcome,
                validation=self.last_validation,
                checks=self.last_checks,
                effects=tuple(effects),
                operation_snapshot=operation_snapshot,
            )

    return FakeDelivery


def test_candidate_refresh_integrates_advanced_main_and_requires_fresh_review(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    initial = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    prepared = lck_refresh.RefreshPreparer(
        cast(Any, _resolver(root, initial)), store=store
    ).prepare(159)

    assert prepared.status == "READY_FOR_REFRESH_COMPLETE"
    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _git(root, "rev-parse", "MERGE_HEAD") == main_head
    assert (root / "dependency.txt").read_text(
        encoding="utf-8"
    ) == "dependency contract\n"

    dirty = replace(initial, git={**initial.git, "clean": False})
    completer = lck_refresh.RefreshCompleter(
        cast(Any, _resolver(root, dirty)),
        store=store,
        delivery_factory=_fake_delivery_factory(root),
    )
    completer.snapshots = cast(Any, _Snapshots(dirty))
    result = completer.complete(
        159,
        commit_message="Merge current main into Task #159",
        summary="Integrate the completed dependency",
    )

    parents = _git(
        root, "rev-list", "--parents", "-n", "1", result.delivery.head_sha
    ).split()
    assert parents == [result.delivery.head_sha, start_head, main_head]
    assert result.to_dict()["status"] == "READY_FOR_FRESH_REVIEW"
    assert result.delivery.effects[1].details["number"] == 200
    assert store.read_refresh_session(159) is None
    required = store.read_review_required(159)
    assert required is not None
    assert required["refreshed_head"] == result.delivery.head_sha


def test_refresh_prepare_reports_conflicts_and_keeps_owned_session(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, conflict=True)
    state = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)

    result = lck_refresh.RefreshPreparer(
        cast(Any, _resolver(root, state)), store=store
    ).prepare(159)

    assert result.status == "REFRESH_CONFLICTS"
    assert result.conflict_files == ("shared.txt",)
    assert store.read_refresh_session(159)["prepared_state"] == "conflicts"  # type: ignore[index]


def test_refresh_prepare_already_current_is_noop(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    _git(root, "merge", "--no-ff", "-m", "already integrated", main_head)
    current = _git(root, "rev-parse", "HEAD")
    state = _state(branch, current, main_head, clean=True)
    state = replace(
        state,
        remote_issue_oid=current,
        open_pr={**cast(dict[str, Any], state.open_pr), "headRefOid": current},
    )
    store = lck_review_workspace.ReviewInvocationStore(root)

    result = lck_refresh.RefreshPreparer(
        cast(Any, _resolver(root, state)), store=store
    ).prepare(159)

    assert result.status == "ALREADY_CURRENT"
    assert store.read_refresh_session(159) is None
    assert _git(root, "status", "--porcelain=v1") == ""


def test_refresh_prepare_stops_before_merge_for_open_blocker(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head, clean=True, blocked=True)
    store = lck_review_workspace.ReviewInvocationStore(root)

    with pytest.raises(lck_models.LckStopError, match="unresolved=1"):
        lck_refresh.RefreshPreparer(
            cast(Any, _resolver(root, state)), store=store
        ).prepare(159)

    assert store.read_refresh_session(159) is None
    assert _git(root, "status", "--porcelain=v1") == ""


def test_refresh_prepare_does_not_bypass_applicable_review_fail(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    review_id = store.new_id()
    store.write_record(
        159,
        review_id,
        {
            "task_number": 159,
            "review_id": review_id,
            "verdict": "FAIL",
            "identity": {"head_sha": start_head},
            "findings": "[F1] Repair first",
        },
    )
    store.write_latest_review(159, review_id, "FAIL")

    with pytest.raises(lck_models.LckStopError, match="cannot bypass"):
        lck_refresh.RefreshPreparer(
            cast(Any, _resolver(root, state)), store=store
        ).prepare(159)

    assert store.read_refresh_session(159) is None


def test_refresh_abort_restores_start_head_without_discarding_untracked_input(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    lck_refresh.RefreshPreparer(cast(Any, _resolver(root, state)), store=store).prepare(
        159
    )
    _write(root / "user-note.txt", "not refresh-owned\n")

    with pytest.raises(lck_models.LckStopError, match="untracked user input"):
        lck_refresh.RefreshAborter(
            cast(Any, _resolver(root, state)), store=store
        ).abort(159)

    (root / "user-note.txt").unlink()
    result = lck_refresh.RefreshAborter(
        cast(Any, _resolver(root, state)), store=store
    ).abort(159)
    assert result.status == "REFRESH_ABORTED"
    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _git(root, "status", "--porcelain=v1") == ""
    assert store.read_refresh_session(159) is None


def test_refresh_complete_rejects_main_drift_and_preserves_session(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    lck_refresh.RefreshPreparer(cast(Any, _resolver(root, state)), store=store).prepare(
        159
    )
    drifted = replace(
        state,
        git={**state.git, "clean": False, "remote_main_sha": "f" * 40},
    )
    completer = lck_refresh.RefreshCompleter(
        cast(Any, _resolver(root, drifted)),
        store=store,
        delivery_factory=_fake_delivery_factory(root),
    )
    completer.snapshots = cast(Any, _Snapshots(drifted))

    with pytest.raises(lck_models.LckStopError, match="authority changed"):
        completer.complete(159, commit_message="refresh", summary="refresh")

    assert store.read_refresh_session(159) is not None


def test_refresh_partial_effect_recovery_accepts_only_recorded_merge_commit(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    initial = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    lck_refresh.RefreshPreparer(
        cast(Any, _resolver(root, initial)), store=store
    ).prepare(159)
    dirty = replace(initial, git={**initial.git, "clean": False})
    interrupted = lck_refresh.RefreshCompleter(
        cast(Any, _resolver(root, dirty)),
        store=store,
        delivery_factory=_fake_delivery_factory(root, fail_after_commit=True),
    )
    interrupted.snapshots = cast(Any, _Snapshots(dirty))
    with pytest.raises(lck_models.LckStopError, match="push interruption"):
        interrupted.complete(159, commit_message="refresh", summary="refresh")

    session = store.read_refresh_session(159)
    assert session is not None and isinstance(session["candidate"], dict)
    candidate_head = cast(dict[str, Any], session["candidate"])["head_sha"]
    recovery_state = replace(
        initial,
        git={**initial.git, "head_sha": candidate_head, "clean": True},
        local_issue_head=candidate_head,
    )
    recovered = lck_refresh.RefreshCompleter(
        cast(Any, _resolver(root, recovery_state)),
        store=store,
        delivery_factory=_fake_delivery_factory(root),
    )
    recovered.snapshots = cast(Any, _Snapshots(recovery_state))
    result = recovered.complete(159, commit_message="refresh", summary="refresh")

    assert result.delivery.head_sha == candidate_head
    assert store.read_refresh_session(159) is None


def test_refresh_complete_keeps_session_when_validation_path_fails(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    initial = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    lck_refresh.RefreshPreparer(
        cast(Any, _resolver(root, initial)), store=store
    ).prepare(159)
    dirty = replace(initial, git={**initial.git, "clean": False})

    class FailingDelivery:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.last_effects: list[Any] = []
            self.last_validation = {"status": "fail"}
            self.last_checks = None
            self.last_critical_outcome = None

        def complete(self, *_args: Any, **_kwargs: Any) -> Any:
            raise lck_models.LckStopError("formal validation failed")

    completer = lck_refresh.RefreshCompleter(
        cast(Any, _resolver(root, dirty)),
        store=store,
        delivery_factory=FailingDelivery,
    )
    completer.snapshots = cast(Any, _Snapshots(dirty))
    with pytest.raises(lck_models.LckStopError, match="formal validation failed"):
        completer.complete(159, commit_message="refresh", summary="refresh")

    assert store.read_refresh_session(159) is not None
    assert _git(root, "rev-parse", "HEAD") == start_head


def test_active_refresh_session_blocks_every_overlapping_lifecycle_entry(
    tmp_path: Path,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    start_head = "a" * 40
    main_head = "b" * 40
    state = _state(branch, start_head, main_head, clean=False)
    resolver = StaticResolver(tmp_path, state)
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    store.write_refresh_session(
        159,
        {
            "schema_version": 1,
            "kind": "candidate-refresh-session",
            "operation_id": store.new_id(),
            "task_number": 159,
            "start_head_sha": start_head,
            "frozen_main_sha": main_head,
        },
    )
    review_id = store.new_id()
    entries = (
        lambda: lck_delivery.DeliveryPreparer(cast(Any, resolver), store=store).prepare(
            159
        ),
        lambda: lck_delivery.DeliveryCompleter(
            cast(Any, resolver), store=store
        ).complete(159, commit_message="x", summary="x"),
        lambda: lck_review.ReviewPreparer(cast(Any, resolver), store=store).prepare(
            159
        ),
        lambda: lck_remediation.RemediationPreparer(
            cast(Any, resolver), store=store
        ).prepare(159, review_id),
        lambda: lck_remediation.RemediationNoChangeCompleter(
            cast(Any, resolver), store=store
        ).complete(159, review_id, summary="no change"),
        lambda: lck_remediation.RemediationCompleter(
            cast(Any, resolver), store=store
        ).complete(159, review_id, commit_message="x", summary="x"),
        lambda: lck_review.MergePreflight(cast(Any, resolver), store=store).run(159),
        lambda: lck_closeout.CloseoutCompleter(
            cast(Any, resolver), review_store=store
        ).complete(159),
    )

    for entry in entries:
        with pytest.raises(lck_models.LckStopError, match="Candidate Refresh"):
            entry()
    assert resolver.calls == 0


def test_refresh_implementation_has_no_force_push_or_new_pr_path() -> None:
    source = (Path(lck_refresh.__file__)).read_text(encoding="utf-8")
    assert '"--force"' not in source
    assert "EnsureOpenPrEffect" not in source
    assert "ReuseExistingOpenPrEffect" in source
