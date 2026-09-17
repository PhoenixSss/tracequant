from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from tools.lck import cli as lck_cli
from tools.lck import models as lck_models
from tools.lck import receipts as lck_receipts
from tools.lck import refresh as lck_refresh
from tools.lck import remediation as lck_remediation
from tools.lck import review as lck_review
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
        report_unknown_after_successful_push: bool = False,
    ) -> None:
        super().__init__(root)
        self.root = root
        self.branch = branch
        self.pr_head_override = pr_head_override
        self.drift_main_before_push = drift_main_before_push
        self.drift_remote_before_push = drift_remote_before_push
        self.report_unknown_after_successful_push = report_unknown_after_successful_push
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
        if (
            command_id == "lck-refresh-push-exact-lease"
            and self.report_unknown_after_successful_push
        ):
            completed = super().run(argv, command_id=command_id, **kwargs)
            assert completed.returncode == 0
            return CommandResult(command_id, command, 1, "", "connection lost")
        if (
            command_id == "lck-refresh-remote-after-failed-push"
            and self.report_unknown_after_successful_push
        ):
            return CommandResult(command_id, command, 1, "", "network unavailable")
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
    def __init__(
        self,
        state: lck_models.LiveState,
        root: Path,
        *,
        pre_push_project_status: str = "Review",
        final_project_status: str = "Review",
        pre_push_body_sha: str | None = None,
        pre_push_blocked: bool = False,
    ) -> None:
        self.state = state
        self.root = root
        self.pre_push_project_status = pre_push_project_status
        self.final_project_status = final_project_status
        self.pre_push_body_sha = pre_push_body_sha
        self.pre_push_blocked = pre_push_blocked
        self.calls = 0

    def acquire(
        self, _task: int, *, operation: str, include_required_checks: bool = False
    ) -> lck_models.OperationSnapshot:
        self.calls += 1
        state = self.state
        if self.calls > 1:
            head = _git(self.root, "rev-parse", "HEAD")
            remote_head = _remote_head(self.root, state.target_branch)
            issue = dict(state.issue or {})
            issue["project_status"] = (
                self.pre_push_project_status
                if self.calls == 2
                else self.final_project_status
            )
            task_contract = dict(state.task_contract or {})
            relationships = state.relationships
            if self.calls == 2 and self.pre_push_body_sha is not None:
                issue["body_sha256"] = self.pre_push_body_sha
                task_contract["body_sha256"] = self.pre_push_body_sha
            if self.calls == 2 and self.pre_push_blocked:
                relationships = _relationships(
                    blocked_by={
                        "items": [{"number": 88, "state": "OPEN"}],
                        "count": 1,
                        "truncated": False,
                    }
                )
            pr = dict(state.open_pr or {})
            pr["headRefOid"] = remote_head
            state = replace(
                state,
                issue=issue,
                leaf_contract=task_contract,
                relationships=relationships,
                git={**state.git, "head_sha": head, "clean": True},
                local_issue_head=head,
                remote_issue_oid=remote_head,
                open_pr=pr,
            )
        return lck_models.OperationSnapshot(
            operation=operation,
            state=state,
            required_checks=(
                {
                    "status": "pass",
                    "names": ["quality"],
                    "source_sha": state.git["remote_main_sha"],
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
    pre_push_project_status: str = "Review",
    final_project_status: str = "Review",
    pre_push_body_sha: str | None = None,
    pre_push_blocked: bool = False,
    real_snapshot: bool = False,
) -> tuple[_ControlledRefresher, _PrObservingRunner]:
    selected = runner or _PrObservingRunner(root, branch)
    resolver = StaticResolver(root, state)
    resolver.runner = cast(Any, selected)
    refresher = _ControlledRefresher(
        cast(Any, resolver),
        formal_validation=cast(Any, validation or _PassValidation()),
    )
    if not real_snapshot:
        refresher.snapshots = cast(
            Any,
            _Snapshots(
                state,
                root,
                pre_push_project_status=pre_push_project_status,
                final_project_status=final_project_status,
                pre_push_body_sha=pre_push_body_sha,
                pre_push_blocked=pre_push_blocked,
            ),
        )
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
    assert result.old_base_sha == _git(root, "rev-parse", f"{start_head}^")
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
    assert result.fresh_review_required is True

    store = lck_receipts.AuditReceiptStore(root)
    agent_view = lck_receipts._write_success_receipt(
        result,
        operation="refresh",
        task_number=159,
        operation_id=result.operation_id,
        store=store,
    )
    receipt = store.read(agent_view["receipt_reference"])
    assert agent_view["old_base_sha"] == result.old_base_sha
    assert receipt["audit"]["old_base_sha"] == result.old_base_sha


def test_refresh_already_current_is_a_noop(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, advance_main=False)
    refresher, runner = _refresher(root, branch, _state(branch, start_head, main_head))

    result = refresher.refresh(159)

    assert result.status == "ALREADY_CURRENT"
    assert result.head_sha == start_head
    assert _remote_head(root, branch) == start_head
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


def test_refresh_already_current_preserves_existing_fresh_review_boundary(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, advance_main=False)
    store = lck_review_workspace.ReviewInvocationStore(root)
    operation_id = store.new_id()
    store.write_refresh_review_required(159, operation_id, start_head)
    refresher, runner = _refresher(root, branch, _state(branch, start_head, main_head))

    result = refresher.refresh(159)
    agent_view = lck_receipts._agent_view_for_result(result)

    assert result.status == "ALREADY_CURRENT"
    assert result.fresh_review_required is True
    assert agent_view["fresh_review_required"] is True
    assert (
        agent_view["next_action"]
        == "start a fresh independent Review in a new invocation"
    )
    assert store.read_review_required(159) is not None
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


def test_refresh_front_door_uses_registered_operation_snapshot_profile(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path, advance_main=False)
    refresher, _runner = _refresher(
        root,
        branch,
        _state(branch, start_head, main_head),
        real_snapshot=True,
    )

    result = refresher.refresh(159)

    assert result.status == "ALREADY_CURRENT"
    assert result.operation_snapshot.operation == "refresh"
    assert result.operation_snapshot.fact_profile == "refresh"


def test_refresh_post_push_drift_stops_with_boundary_and_updated_candidate(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    refresher, _runner = _refresher(
        root,
        branch,
        _state(branch, start_head, main_head),
        pre_push_project_status="Review",
        final_project_status="In Progress",
    )

    with pytest.raises(
        lck_models.LckStopError,
        match="push completed but final identity verification failed",
    ):
        refresher.refresh(159)

    new_head = _git(root, "rev-parse", "HEAD")
    assert new_head != start_head
    assert _remote_head(root, branch) == new_head
    assert refresher.last_effects[0].effect == "refresh_remote_branch"
    boundary = lck_review_workspace.ReviewInvocationStore(root).read_review_required(
        159
    )
    assert boundary is not None
    assert boundary["refreshed_head"] == new_head
    assert refresher.last_snapshot is not None
    assert refresher.last_snapshot.state.project_status == "In Progress"

    receipt_store = lck_receipts.AuditReceiptStore(root)
    payload = lck_receipts._write_failure_receipt(
        operation="refresh",
        task_number=159,
        operation_id=cast(str, refresher.operation_id),
        status="stop",
        code=None,
        error="post-push identity drift",
        handler=refresher,
        store=receipt_store,
    )
    receipt = receipt_store.read(payload["receipt_reference"])
    assert receipt["operation_id"] == boundary["source_refresh_operation_id"]
    assert receipt["operation_snapshot"] == refresher.last_snapshot.to_dict()
    assert receipt["audit"]["old_base_sha"] == refresher.last_old_base_sha
    assert receipt["audit"]["validated_tree_oid"] == (refresher.last_validated_tree_oid)
    assert receipt["audit"]["head_sha"] == new_head
    assert receipt["audit"]["fresh_review_required"] is True


def test_refresh_task_contract_drift_stops_before_push(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    refresher, runner = _refresher(
        root,
        branch,
        _state(branch, start_head, main_head),
        pre_push_body_sha="e" * 64,
    )

    with pytest.raises(
        lck_models.LckStopError,
        match="workflow identity changed before push",
    ):
        refresher.refresh(159)

    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _remote_head(root, branch) == start_head
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


def test_refresh_new_blocker_stops_before_push(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    refresher, runner = _refresher(
        root,
        branch,
        _state(branch, start_head, main_head),
        pre_push_blocked=True,
    )

    with pytest.raises(
        lck_models.LckStopError,
        match="formal blocker gate",
    ):
        refresher.refresh(159)

    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _remote_head(root, branch) == start_head
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


def test_refresh_unknown_push_outcome_retains_candidate_and_boundary(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    runner = _PrObservingRunner(
        root,
        branch,
        report_unknown_after_successful_push=True,
    )
    refresher, _runner = _refresher(
        root,
        branch,
        _state(branch, start_head, main_head),
        runner=runner,
    )

    with pytest.raises(lck_models.LckStopError, match="push outcome is unknown"):
        refresher.refresh(159)

    new_head = _git(root, "rev-parse", "HEAD")
    assert new_head != start_head
    assert _remote_head(root, branch) == new_head
    boundary = lck_review_workspace.ReviewInvocationStore(root).read_review_required(
        159
    )
    assert boundary is not None
    assert boundary["refreshed_head"] == new_head
    assert boundary["source_refresh_operation_id"] == refresher.operation_id
    assert refresher.last_push_outcome == "unknown"
    assert refresher.last_effects[-1].action == "outcome-unknown"


def test_refresh_disables_ambient_rebase_update_refs(tmp_path: Path) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    _git(root, "branch", "unrelated", start_head)
    _git(root, "config", "rebase.updateRefs", "true")
    refresher, runner = _refresher(root, branch, _state(branch, start_head, main_head))

    result = refresher.refresh(159)

    assert result.status == "READY_FOR_FRESH_REVIEW"
    assert _git(root, "rev-parse", "unrelated") == start_head
    assert any(
        command[:3] == ("git", "rebase", "--no-update-refs")
        for command in runner.commands
    )


def test_refresh_invalidates_old_review_and_blocks_remediation(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head)
    store = lck_review_workspace.ReviewInvocationStore(root)
    old_review_id = store.new_id()
    old_identity = lck_review_workspace.ReviewIdentity(
        task_number=159,
        pr_number=200,
        base_sha=main_head,
        head_sha=start_head,
        task_body_sha256="d" * 64,
        merge_base_sha=_git(root, "rev-parse", f"{start_head}^"),
        effective_diff_sha256="e" * 64,
        changed_files=("task.txt",),
    )
    store.write_record(
        159,
        old_review_id,
        {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "kind": "independent-review-record",
            "review_id": old_review_id,
            "task_number": 159,
            "verdict": "PASS",
            "status": "READY_FOR_MERGE_PREFLIGHT",
            "identity": old_identity.to_dict(),
        },
    )
    store.write_latest_review(159, old_review_id, "PASS")
    refresher, _runner = _refresher(root, branch, state)

    result = refresher.refresh(159)

    refreshed_state = _state(branch, result.head_sha, main_head)
    with pytest.raises(lck_models.LckStopError, match="Review PASS is stale"):
        lck_review.ReviewPassGate(
            cast(Any, StaticResolver(root, refreshed_state)), store=store
        ).run(159, refreshed_state)
    with pytest.raises(lck_models.LckStopError, match="fresh Independent Review"):
        lck_remediation.RemediationPreparer(
            cast(Any, StaticResolver(root, refreshed_state)), store=store
        ).prepare(159, old_review_id)


def test_refresh_cli_failure_receipt_uses_refresh_operation_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operation_id = "a" * 32
    state = _state("task/159-test", "b" * 40, "c" * 40)

    class FailingRefresher:
        def __init__(self, _resolver: Any) -> None:
            self.operation_id = operation_id
            self.last_snapshot = lck_models.OperationSnapshot(
                operation="refresh", state=state
            )
            self.last_effects: list[lck_models.EffectReceipt] = []
            self.last_conflict_files: tuple[str, ...] = ()
            self.last_branch = state.target_branch
            self.last_pr_number = 200
            self.last_old_base_sha = "d" * 40
            self.last_start_head_sha = "b" * 40
            self.last_frozen_main_sha = "c" * 40
            self.last_head_sha = "e" * 40
            self.last_validated_tree_oid = "f" * 40
            self.last_push_outcome = "updated"
            self.last_fresh_review_required = True

        def refresh(self, _task_number: int) -> Any:
            raise lck_models.LckStopError("post-push verification failed")

    resolver = StaticResolver(tmp_path, state)
    monkeypatch.setattr(
        lck_cli,
        "LiveStateResolver",
        lambda _root, repository=None: resolver,
    )
    monkeypatch.setattr(lck_cli, "CandidateRefresher", FailingRefresher)

    exit_code = lck_cli.main(["--repo-root", str(tmp_path), "refresh", "159"])
    payload = json.loads(capsys.readouterr().out)
    receipt = lck_receipts.AuditReceiptStore(tmp_path).read(
        payload["receipt_reference"]
    )

    assert exit_code == 2
    assert payload["operation_id"] == operation_id
    assert receipt["operation_id"] == operation_id
    assert receipt["audit"]["validated_tree_oid"] == "f" * 40
    assert receipt["audit"]["fresh_review_required"] is True


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


@pytest.mark.parametrize(
    ("state_change", "match"),
    [
        (
            lambda state, main: replace(state, git={**state.git, "clean": False}),
            "clean",
        ),
        (
            lambda state, main: replace(state, remote_issue_oid=main),
            "remote Task branch must match",
        ),
        (
            lambda state, main: replace(
                state,
                relationships={
                    "available": True,
                    "blocked_by": {"items": [], "count": 1, "truncated": True},
                },
            ),
            "formal blocker gate",
        ),
        (
            lambda state, main: replace(
                state,
                status=lck_models.ResolutionStatus.STOP,
                stop_reasons=("multiple OPEN PRs for the Task branch",),
            ),
            "multiple OPEN PRs",
        ),
    ],
)
def test_refresh_prohibited_admission_states_stop_before_rebase(
    tmp_path: Path,
    state_change: Any,
    match: str,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = state_change(_state(branch, start_head, main_head), main_head)
    refresher, runner = _refresher(root, branch, state)

    with pytest.raises(lck_models.LckStopError, match=match):
        refresher.refresh(159)

    assert _git(root, "rev-parse", "HEAD") == start_head
    assert _remote_head(root, branch) == start_head
    assert not any(command[:2] == ("git", "rebase") for command in runner.commands)


def test_refresh_rejects_active_review_and_remediation_handoffs(
    tmp_path: Path,
) -> None:
    root, branch, start_head, main_head = _repository(tmp_path)
    state = _state(branch, start_head, main_head)
    store = lck_review_workspace.ReviewInvocationStore(root)
    review_invocation = store.begin_review_prepare(159)
    try:
        refresher, runner = _refresher(root, branch, state)
        with pytest.raises(lck_models.LckStopError, match="Review Prepare/handoff"):
            refresher.refresh(159)
        assert not any(command[:2] == ("git", "rebase") for command in runner.commands)
    finally:
        review_invocation.finish()

    review_id = store.new_id()
    store.write_remediation_session(
        159,
        {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "kind": "remediation-session",
            "task_number": 159,
            "review_id": review_id,
            "start_head_sha": start_head,
        },
    )
    refresher, runner = _refresher(root, branch, state)
    with pytest.raises(lck_models.LckStopError, match="Remediation session"):
        refresher.refresh(159)
    assert not any(command[:2] == ("git", "rebase") for command in runner.commands)


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
    claude_adapter = (
        Path(__file__).parents[3] / ".claude/skills/task-delivery-runner/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "RefreshPreparer" not in source
    assert "RefreshCompleter" not in source
    assert "RefreshAborter" not in source
    assert "write_refresh_session" not in source
    assert '["git", "push", "--force"' not in source
    assert '"--force-with-lease=' in source
    assert "one-shot `refresh <TASK>`" in claude_adapter
    assert "prepare/complete/abort Human boundaries" not in claude_adapter
