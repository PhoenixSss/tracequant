from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from tools.lck import models as lck_models
from tools.lck import refresh as lck_refresh
from tools.lck import review_workspace as lck_review_workspace
from tools.lck.common import CommandResult, CommandRunner
from tools.lck.operation_lock import TaskOperationLock

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
    tmp_path: Path, *, conflict: bool = False, advance_main: bool = True
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
    if advance_main:
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
    _git(root, "push", "-u", "origin", "main", branch)
    return root, branch, start_head, main_head


def _state(
    branch: str,
    start_head: str,
    main_head: str,
    *,
    clean: bool = True,
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


def _remote_head(root: Path, branch: str) -> str:
    value = _git(root, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    return value.split()[0]


class _PrObservingRunner(CommandRunner):
    def __init__(
        self,
        root: Path,
        branch: str,
        *,
        pr_head_override: str | None = None,
        drift_main_before_push: bool = False,
        drift_remote_before_push: bool = False,
    ) -> None:
        super().__init__(root)
        self.root = root
        self.branch = branch
        self.pr_head_override = pr_head_override
        self.drift_main_before_push = drift_main_before_push
        self.drift_remote_before_push = drift_remote_before_push
        self.commands: list[tuple[str, ...]] = []

    def run(self, argv: Any, *, command_id: str, **kwargs: Any) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        if command_id == "lck-refresh-main-before-push" and self.drift_main_before_push:
            remote = self.root.parent / "remote.git"
            subprocess.run(
                [
                    "git",
                    f"--git-dir={remote}",
                    "update-ref",
                    "refs/heads/main",
                    _remote_head(self.root, self.branch),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
        if (
            command_id == "lck-refresh-remote-before-push"
            and self.drift_remote_before_push
        ):
            remote = self.root.parent / "remote.git"
            subprocess.run(
                [
                    "git",
                    f"--git-dir={remote}",
                    "update-ref",
                    f"refs/heads/{self.branch}",
                    _git(self.root, "rev-parse", "main"),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
        if command[:3] == ("gh", "pr", "view"):
            payload = {
                "number": 200,
                "url": "https://github.com/owner/repo/pull/200",
                "state": "OPEN",
                "isDraft": False,
                "baseRefName": "main",
                "baseRefOid": _git(self.root, "rev-parse", "main"),
                "headRefName": self.branch,
                "headRefOid": self.pr_head_override
                or _remote_head(self.root, self.branch),
            }
            return CommandResult(command_id, command, 0, json.dumps(payload), "")
        return super().run(argv, command_id=command_id, **kwargs)


class _PassValidation:
    def run(self, base_sha: str) -> dict[str, Any]:
        return {
            "status": "pass",
            "command_count": 1,
            "validated_base_sha": base_sha,
        }


class _FailValidation:
    last_payload = {"status": "fail", "command_count": 1}

    def run(self, _base_sha: str) -> dict[str, Any]:
        raise lck_models.LckStopError("simulated formal validation failure")


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


class _ControlledRefresher(lck_refresh.CandidateRefresher):
    def _run_profile_gates(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        result = {"status": "pass", "test": "controlled-critical-outcome"}
        self.last_critical_outcome = result
        return result


def _refresher(
    root: Path,
    branch: str,
    state: lck_models.LiveState,
    *,
    validation: Any | None = None,
    runner: _PrObservingRunner | None = None,
) -> tuple[_ControlledRefresher, _PrObservingRunner]:
    selected = runner or _PrObservingRunner(root, branch)
    resolver = StaticResolver(root, state)
    resolver.runner = cast(Any, selected)
    refresher = _ControlledRefresher(
        cast(Any, resolver),
        formal_validation=cast(Any, validation or _PassValidation()),
    )
    refresher.snapshots = cast(Any, _Snapshots(state))
    return refresher, selected


def test_candidate_refresh_integrates_advanced_main_and_requires_fresh_review(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head)
    refresher, runner = _refresher(root, branch, state)

    result = refresher.refresh(159)

    assert result.status == "READY_FOR_FRESH_REVIEW"
    assert result.start_head_sha == start_head
    assert result.frozen_main_sha == main_head
    assert result.head_sha != start_head
    assert _git(root, "rev-parse", "HEAD") == result.head_sha
    assert _git(root, "rev-parse", "HEAD^") == main_head
    assert _remote_head(root, branch) == result.head_sha
    assert any(
        command[:2] == ("git", "push")
        and any(part.startswith("--force-with-lease=refs/heads/") for part in command)
        for command in runner.commands
    )
    required = lck_review_workspace.ReviewInvocationStore(root).read_review_required(
        159
    )
    assert required is not None
    assert required["refreshed_head"] == result.head_sha
    assert required["source_refresh_operation_id"] == result.operation_id


def test_refresh_already_current_is_a_noop(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, advance_main=False)
    refresher, runner = _refresher(root, branch, _state(branch, start_head, main_head))

    result = refresher.refresh(159)

    assert result.status == "ALREADY_CURRENT"
    assert result.head_sha == start_head
    assert _remote_head(root, branch) == start_head
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


def test_refresh_conflict_restores_exact_original_state(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, conflict=True)
    refresher, _runner = _refresher(root, branch, _state(branch, start_head, main_head))

    with pytest.raises(lck_models.LckStopError, match="original Task head restored"):
        refresher.refresh(159)

    assert refresher.last_conflict_files == ("shared.txt",)
    assert _git(root, "branch", "--show-current") == branch
    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _git(root, "status", "--porcelain=v1") == ""
    assert _remote_head(root, branch) == start_head


def test_refresh_validation_failure_restores_exact_original_state(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    store = lck_review_workspace.ReviewInvocationStore(root)
    prior_review_id = store.new_id()
    store.write_review_required(159, prior_review_id, start_head)
    prior_boundary = store.read_review_required(159)
    refresher, _runner = _refresher(
        root,
        branch,
        _state(branch, start_head, main_head),
        validation=_FailValidation(),
    )

    with pytest.raises(lck_models.LckStopError, match="formal validation failure"):
        refresher.refresh(159)

    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _git(root, "status", "--porcelain=v1") == ""
    assert _remote_head(root, branch) == start_head
    assert store.read_review_required(159) == prior_boundary


def test_refresh_rejects_blocker_before_rebase(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    refresher, runner = _refresher(
        root, branch, _state(branch, start_head, main_head, blocked=True)
    )

    with pytest.raises(lck_models.LckStopError, match="formal blocker gate"):
        refresher.refresh(159)

    assert _git(root, "rev-parse", "HEAD") == start_head
    assert not any(command[:2] == ("git", "rebase") for command in runner.commands)


def test_refresh_pr_drift_rolls_back_before_push(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    runner = _PrObservingRunner(root, branch, pr_head_override=main_head)
    refresher, runner = _refresher(
        root, branch, _state(branch, start_head, main_head), runner=runner
    )

    with pytest.raises(lck_models.LckStopError, match="PR/base/head identity changed"):
        refresher.refresh(159)

    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _remote_head(root, branch) == start_head
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


def test_refresh_main_drift_rolls_back_before_push(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    runner = _PrObservingRunner(root, branch, drift_main_before_push=True)
    refresher, runner = _refresher(
        root, branch, _state(branch, start_head, main_head), runner=runner
    )

    with pytest.raises(lck_models.LckStopError, match="origin/main changed"):
        refresher.refresh(159)

    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _remote_head(root, branch) == start_head
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


def test_refresh_exact_lease_failure_rolls_back_local_candidate(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    runner = _PrObservingRunner(root, branch, drift_remote_before_push=True)
    refresher, _runner = _refresher(
        root, branch, _state(branch, start_head, main_head), runner=runner
    )

    with pytest.raises(lck_models.LckStopError, match="remote Task head changed"):
        refresher.refresh(159)

    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _git(root, "status", "--porcelain=v1") == ""
    assert (
        lck_review_workspace.ReviewInvocationStore(root).read_review_required(159)
        is None
    )


def test_task_operation_lock_serializes_lifecycle_operations(tmp_path: Path) -> None:
    first = TaskOperationLock.acquire(tmp_path, 159, "review-prepare")
    try:
        with pytest.raises(
            lck_models.LckStopError, match="already has an active LCK operation"
        ):
            TaskOperationLock.acquire(tmp_path, 159, "refresh")
        other = TaskOperationLock.acquire(tmp_path, 160, "refresh")
        other.release()
    finally:
        first.release()


def test_refresh_has_no_persistent_session_or_abort_surface() -> None:
    source = Path(lck_refresh.__file__).read_text(encoding="utf-8")
    assert "RefreshPreparer" not in source
    assert "RefreshCompleter" not in source
    assert "RefreshAborter" not in source
    assert "write_refresh_session" not in source
    assert '["git", "push", "--force"' not in source
    assert '"--force-with-lease=' in source
