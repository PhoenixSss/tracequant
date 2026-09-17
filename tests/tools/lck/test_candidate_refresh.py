from __future__ import annotations

import json
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
from tools.lck.common import CommandResult, CommandRunner

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
    tmp_path: Path, *, conflict: bool = False, history_only_main: bool = False
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
    if history_only_main:
        _git(root, "commit", "--allow-empty", "-m", "dependency history")
    else:
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


class _PrObservingRunner(CommandRunner):
    def __init__(self, root: Path, *, fail_pr_views: int = 0) -> None:
        super().__init__(root)
        self.root = root
        self.fail_pr_views = fail_pr_views
        self.commands: list[tuple[str, ...]] = []

    def run(self, argv: Any, *, command_id: str, **kwargs: Any) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        if command[:3] == ("gh", "pr", "view"):
            if self.fail_pr_views:
                self.fail_pr_views -= 1
                return CommandResult(command_id, command, 1, "", "interrupted")
            branch = _git(self.root, "branch", "--show-current")
            payload = {
                "number": 200,
                "url": "https://github.com/owner/repo/pull/200",
                "state": "OPEN",
                "isDraft": False,
                "baseRefName": "main",
                "baseRefOid": _git(self.root, "rev-parse", "main"),
                "headRefName": branch,
                "headRefOid": _git(self.root, "rev-parse", "HEAD"),
            }
            return CommandResult(command_id, command, 0, json.dumps(payload), "")
        return super().run(argv, command_id=command_id, **kwargs)


class _PassValidation:
    def run(self, _base_sha: str) -> dict[str, Any]:
        return {"status": "pass", "command_count": 1}


class _PassChecks:
    def _result(self, number: int, head: str, base: str) -> dict[str, Any]:
        return {
            "status": "observed",
            "gate": "non-blocking",
            "check_state": "pass",
            "pr": {"number": number, "head_sha": head, "base_sha": base},
        }

    def observe(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
        pr = cast(dict[str, Any], snapshot.state.open_pr)
        return self._result(pr["number"], pr["headRefOid"], pr["baseRefOid"])

    def evaluate(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
        return self.observe(snapshot)

    def observe_exact_pr(
        self,
        _repository: str,
        pr_number: int,
        *,
        expected_head_sha: str,
        expected_base_sha: str,
    ) -> dict[str, Any]:
        return self._result(pr_number, expected_head_sha, expected_base_sha)


class _AlreadyReview:
    def execute(self, *_args: Any, **_kwargs: Any) -> lck_models.EffectReceipt:
        return lck_models.EffectReceipt("set_review_status", "already-review", {})


class _ControlledDelivery(lck_delivery.DeliveryCompleter):
    """Real completion/effects path with only validation boundaries controlled."""

    def __init__(self, resolver: Any, **kwargs: Any) -> None:
        super().__init__(
            resolver,
            formal_validation=cast(Any, _PassValidation()),
            checks_gate=cast(Any, _PassChecks()),
            status_effect=cast(Any, _AlreadyReview()),
            **kwargs,
        )

    def _run_profile_gates(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        result = {"status": "pass", "test": "controlled-critical-outcome"}
        self.last_critical_outcome = result
        return result


def _real_resolver(
    root: Path,
    state: lck_models.LiveState,
    *,
    runner: _PrObservingRunner | None = None,
) -> tuple[StaticResolver, _PrObservingRunner]:
    selected = runner or _PrObservingRunner(root)
    resolver = StaticResolver(root, state)
    resolver.runner = cast(Any, selected)
    return resolver, selected


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
    prepare_resolver, runner = _real_resolver(root, initial)
    prepared = lck_refresh.RefreshPreparer(
        cast(Any, prepare_resolver), store=store
    ).prepare(159)

    assert prepared.status == "READY_FOR_REFRESH_COMPLETE"
    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _git(root, "rev-parse", "MERGE_HEAD") == main_head
    assert (root / "dependency.txt").read_text(
        encoding="utf-8"
    ) == "dependency contract\n"

    dirty = replace(initial, git={**initial.git, "clean": False})
    complete_resolver, _ = _real_resolver(root, dirty, runner=runner)
    completer = lck_refresh.RefreshCompleter(
        cast(Any, complete_resolver),
        store=store,
        delivery_factory=_ControlledDelivery,
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
    assert result.delivery.effects[0].effect == "commit_current_tree"
    assert result.delivery.effects[1].effect == "ensure_remote_branch"
    assert result.delivery.effects[1].action == "fast-forwarded"
    assert result.delivery.effects[2].effect == "reuse_open_pr"
    assert result.delivery.effects[2].details["number"] == 200
    assert _git(root, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == (
        result.delivery.head_sha
    )
    assert any(command[:3] == ("gh", "pr", "view") for command in runner.commands)
    assert not any("--force" in command for command in runner.commands)
    assert not any(command[:3] == ("gh", "pr", "create") for command in runner.commands)
    assert store.read_refresh_session(159) is None
    required = store.read_review_required(159)
    assert required is not None
    assert required["refreshed_head"] == result.delivery.head_sha


def test_refresh_history_only_main_still_creates_exact_merge_commit(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, history_only_main=True)
    initial = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    prepare_resolver, runner = _real_resolver(root, initial)

    prepared = lck_refresh.RefreshPreparer(
        cast(Any, prepare_resolver), store=store
    ).prepare(159)

    assert prepared.status == "READY_FOR_REFRESH_COMPLETE"
    assert _git(root, "status", "--porcelain=v1") == ""
    complete_resolver, _ = _real_resolver(root, initial, runner=runner)
    completer = lck_refresh.RefreshCompleter(
        cast(Any, complete_resolver),
        store=store,
        delivery_factory=_ControlledDelivery,
    )
    completer.snapshots = cast(Any, _Snapshots(initial))
    result = completer.complete(159, commit_message="refresh", summary="refresh")

    assert _git(
        root, "rev-list", "--parents", "-n", "1", result.delivery.head_sha
    ).split() == [result.delivery.head_sha, start_head, main_head]
    assert _git(root, "rev-parse", f"{result.delivery.head_sha}^{{tree}}") == _git(
        root, "rev-parse", f"{start_head}^{{tree}}"
    )


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


def test_refresh_complete_accepts_resolved_owned_conflict(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, conflict=True)
    initial = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    prepare_resolver, runner = _real_resolver(root, initial)
    lck_refresh.RefreshPreparer(cast(Any, prepare_resolver), store=store).prepare(159)
    _write(root / "shared.txt", "resolved task and main\n")
    _git(root, "add", "shared.txt")
    dirty = replace(initial, git={**initial.git, "clean": False})
    complete_resolver, _ = _real_resolver(root, dirty, runner=runner)
    completer = lck_refresh.RefreshCompleter(
        cast(Any, complete_resolver),
        store=store,
        delivery_factory=_ControlledDelivery,
    )
    completer.snapshots = cast(Any, _Snapshots(dirty))

    result = completer.complete(159, commit_message="resolve", summary="resolve")

    assert _git(
        root, "rev-list", "--parents", "-n", "1", result.delivery.head_sha
    ).split() == [result.delivery.head_sha, start_head, main_head]
    assert (root / "shared.txt").read_text(encoding="utf-8") == (
        "resolved task and main\n"
    )


def test_refresh_prepare_preserves_preexisting_history_only_merge(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, history_only_main=True)
    _git(root, "merge", "--no-commit", "--no-ff", main_head)
    assert _git(root, "status", "--porcelain=v1") == ""
    state = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)

    with pytest.raises(lck_models.LckStopError, match="pre-existing Git operation"):
        lck_refresh.RefreshPreparer(
            cast(Any, _resolver(root, state)), store=store
        ).prepare(159)

    assert _git(root, "rev-parse", "MERGE_HEAD") == main_head
    assert store.read_refresh_session(159) is None


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


@pytest.mark.parametrize("overlap", ["review", "remediation"])
def test_refresh_prepare_rejects_active_review_or_remediation_session(
    tmp_path: Path, overlap: str
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    if overlap == "review":
        store.review_prepare_inflight_path(159).parent.mkdir(
            parents=True, exist_ok=True
        )
        store.review_prepare_inflight_path(159).write_text("{}", encoding="utf-8")
    else:
        store.write_remediation_session(
            159,
            {
                "task_number": 159,
                "review_id": store.new_id(),
                "start_head_sha": start_head,
            },
        )

    with pytest.raises(lck_models.LckStopError, match="active"):
        lck_refresh.RefreshPreparer(
            cast(Any, _resolver(root, state)), store=store
        ).prepare(159)

    assert store.read_refresh_session(159) is None
    assert _git(root, "status", "--porcelain=v1") == ""


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


def test_refresh_abort_releases_interrupted_session_before_merge_started(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    operation_id = store.new_id()
    store.write_refresh_session(
        159,
        {
            "schema_version": 1,
            "kind": "candidate-refresh-session",
            "operation_id": operation_id,
            "task_number": 159,
            "repository": state.repository,
            "branch": branch,
            "pr_number": 200,
            "task_body_sha256": "d" * 64,
            "start_head_sha": start_head,
            "frozen_main_sha": main_head,
            "remote_head_sha": start_head,
            "pr_head_sha": start_head,
            "pr_base_sha": main_head,
            "merge_parents": [start_head, main_head],
            "candidate_paths": ["dependency.txt"],
            "prepared_state": "merge-starting",
            "candidate": None,
        },
    )
    resolver, runner = _real_resolver(root, state)

    result = lck_refresh.RefreshAborter(cast(Any, resolver), store=store).abort(159)

    assert result.status == "REFRESH_ABORTED"
    assert store.read_refresh_session(159) is None
    assert not any(
        command[:3] == ("git", "merge", "--abort") for command in runner.commands
    )


@pytest.mark.parametrize("drift", ["dirty", "divergent"])
def test_refresh_prepare_rejects_dirty_or_divergent_workspace_before_writes(
    tmp_path: Path, drift: str
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head, clean=drift != "dirty")
    if drift == "divergent":
        remote_head = "e" * 40
        state = replace(
            state,
            remote_issue_oid=remote_head,
            open_pr={
                **cast(dict[str, Any], state.open_pr),
                "headRefOid": remote_head,
            },
        )
    store = lck_review_workspace.ReviewInvocationStore(root)

    with pytest.raises(lck_models.LckStopError):
        lck_refresh.RefreshPreparer(
            cast(Any, _resolver(root, state)), store=store
        ).prepare(159)

    assert store.read_refresh_session(159) is None
    assert _git(root, "rev-parse", "HEAD") == start_head


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


@pytest.mark.parametrize("drift", ["pr", "head"])
def test_refresh_complete_rejects_pr_or_head_drift(tmp_path: Path, drift: str) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    initial = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    lck_refresh.RefreshPreparer(
        cast(Any, _resolver(root, initial)), store=store
    ).prepare(159)
    dirty = replace(initial, git={**initial.git, "clean": False})
    if drift == "pr":
        dirty = replace(
            dirty,
            open_pr={**cast(dict[str, Any], dirty.open_pr), "number": 201},
        )
    else:
        changed_head = "e" * 40
        dirty = replace(
            dirty,
            remote_issue_oid=changed_head,
            open_pr={
                **cast(dict[str, Any], dirty.open_pr),
                "headRefOid": changed_head,
            },
        )
    completer = lck_refresh.RefreshCompleter(
        cast(Any, _resolver(root, dirty)),
        store=store,
        delivery_factory=_fake_delivery_factory(root),
    )
    completer.snapshots = cast(Any, _Snapshots(dirty))

    with pytest.raises(lck_models.LckStopError, match="authority changed|drifted"):
        completer.complete(159, commit_message="refresh", summary="refresh")

    assert store.read_refresh_session(159) is not None


def test_refresh_partial_effect_recovery_after_push_reuses_recorded_merge_commit(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    initial = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    runner = _PrObservingRunner(root, fail_pr_views=1)
    prepare_resolver, _ = _real_resolver(root, initial, runner=runner)
    lck_refresh.RefreshPreparer(cast(Any, prepare_resolver), store=store).prepare(159)
    dirty = replace(initial, git={**initial.git, "clean": False})
    interrupted_resolver, _ = _real_resolver(root, dirty, runner=runner)
    interrupted = lck_refresh.RefreshCompleter(
        cast(Any, interrupted_resolver),
        store=store,
        delivery_factory=_ControlledDelivery,
    )
    interrupted.snapshots = cast(Any, _Snapshots(dirty))
    with pytest.raises(lck_models.LckStopError, match="cannot be queried"):
        interrupted.complete(159, commit_message="refresh", summary="refresh")

    session = store.read_refresh_session(159)
    assert session is not None and isinstance(session["candidate"], dict)
    candidate_head = cast(dict[str, Any], session["candidate"])["head_sha"]
    assert _git(root, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == (
        candidate_head
    )
    recovery_state = replace(
        initial,
        git={**initial.git, "head_sha": candidate_head, "clean": True},
        local_issue_head=candidate_head,
        remote_issue_oid=candidate_head,
        open_pr={
            **cast(dict[str, Any], initial.open_pr),
            "headRefOid": candidate_head,
        },
    )
    recovery_resolver, _ = _real_resolver(root, recovery_state, runner=runner)
    recovered = lck_refresh.RefreshCompleter(
        cast(Any, recovery_resolver),
        store=store,
        delivery_factory=_ControlledDelivery,
    )
    recovered.snapshots = cast(Any, _Snapshots(recovery_state))
    result = recovered.complete(159, commit_message="refresh", summary="refresh")

    assert result.delivery.head_sha == candidate_head
    assert store.read_refresh_session(159) is None


@pytest.mark.parametrize("invalid_identity", ["parents", "tree"])
def test_refresh_recovery_rejects_wrong_recorded_parents_or_tree(
    tmp_path: Path, invalid_identity: str
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    initial = _state(branch, start_head, main_head, clean=True)
    store = lck_review_workspace.ReviewInvocationStore(root)
    prepared = lck_refresh.RefreshPreparer(
        cast(Any, _resolver(root, initial)), store=store
    ).prepare(159)
    if invalid_identity == "parents":
        _git(root, "merge", "--abort")
        _git(root, "commit", "--allow-empty", "-m", "not a merge")
    else:
        _git(root, "commit", "-m", "owned merge")
    candidate_head = _git(root, "rev-parse", "HEAD")
    candidate_tree = _git(root, "rev-parse", "HEAD^{tree}")
    store.record_refresh_candidate(
        159,
        cast(str, prepared.operation_id),
        start_head_sha=start_head,
        frozen_main_sha=main_head,
        candidate_head_sha=candidate_head,
        candidate_tree_oid=("f" * 40 if invalid_identity == "tree" else candidate_tree),
    )
    recovery_state = replace(
        initial,
        git={**initial.git, "head_sha": candidate_head, "clean": True},
        local_issue_head=candidate_head,
    )
    completer = lck_refresh.RefreshCompleter(
        cast(Any, _resolver(root, recovery_state)),
        store=store,
        delivery_factory=_fake_delivery_factory(root),
    )
    completer.snapshots = cast(Any, _Snapshots(recovery_state))

    with pytest.raises(lck_models.LckStopError, match="exact owned parents/tree"):
        completer.complete(159, commit_message="refresh", summary="refresh")

    assert store.read_refresh_session(159) is not None


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


def test_refresh_invalidates_old_pass_until_fresh_review_is_recorded(
    tmp_path: Path,
) -> None:
    root, branch, old_head, main_head = _repository(tmp_path)
    _git(root, "merge", "--no-ff", "-m", "refreshed", main_head)
    refreshed_head = _git(root, "rev-parse", "HEAD")
    state = _state(branch, refreshed_head, main_head, clean=True)
    state = replace(
        state,
        remote_issue_oid=refreshed_head,
        open_pr={
            **cast(dict[str, Any], state.open_pr),
            "headRefOid": refreshed_head,
            "mergeable": "MERGEABLE",
        },
    )
    store = lck_review_workspace.ReviewInvocationStore(root)
    old_review_id = store.new_id()
    old_identity = lck_review_workspace.ReviewIdentity(
        task_number=159,
        pr_number=200,
        base_sha=main_head,
        head_sha=old_head,
        task_body_sha256="d" * 64,
        merge_base_sha=main_head,
        effective_diff_sha256="e" * 64,
        changed_files=("task.txt",),
    )
    store.write_record(
        159,
        old_review_id,
        {
            "task_number": 159,
            "review_id": old_review_id,
            "verdict": "PASS",
            "status": "READY_FOR_MERGE_PREFLIGHT",
            "identity": old_identity.to_dict(),
        },
    )
    store.write_latest_review(159, old_review_id, "PASS")
    store.write_refresh_review_required(159, store.new_id(), refreshed_head)
    preflight = lck_review.MergePreflight(
        cast(Any, _resolver(root, state)),
        checks_gate=cast(Any, _PassChecks()),
        store=store,
    )
    preflight.snapshots = cast(Any, _Snapshots(state))

    with pytest.raises(lck_models.LckStopError, match="Review PASS is stale"):
        preflight.run(159)

    fresh_review_id = store.new_id()
    fresh_identity = lck_review_workspace.ReviewIdentity(
        task_number=159,
        pr_number=200,
        base_sha=main_head,
        head_sha=refreshed_head,
        task_body_sha256="d" * 64,
        merge_base_sha=main_head,
        effective_diff_sha256="f" * 64,
        changed_files=("task.txt",),
    )
    store.write_record(
        159,
        fresh_review_id,
        {
            "task_number": 159,
            "review_id": fresh_review_id,
            "verdict": "PASS",
            "status": "READY_FOR_MERGE_PREFLIGHT",
            "identity": fresh_identity.to_dict(),
        },
    )
    store.write_latest_review(159, fresh_review_id, "PASS")
    store.clear_review_required(159)

    result = preflight.run(159)

    assert result.status == "READY_FOR_HUMAN_MERGE"


def test_refresh_implementation_has_no_force_push_or_new_pr_path() -> None:
    source = (Path(lck_refresh.__file__)).read_text(encoding="utf-8")
    assert '"--force"' not in source
    assert "EnsureOpenPrEffect" not in source
    assert "ReuseExistingOpenPrEffect" in source
