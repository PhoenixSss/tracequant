# ruff: noqa: E402, I001

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

import lck  # type: ignore[import-not-found]  # noqa: E402
from pr_resolve import (  # type: ignore[import-not-found]
    PrResolveError,
    resolve_open_pr,
)  # noqa: E402
from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
    CommandRunner,
)


SHA = "a" * 40


class FakeRunner:
    def __init__(
        self,
        *,
        branch: str = "main",
        local_branches: set[str] | None = None,
        remote_branches: dict[str, str] | None = None,
        clean: bool = True,
        head_sha: str = SHA,
        local_main_sha: str = SHA,
        origin_main_sha: str = SHA,
        open_pr: dict[str, Any] | None = None,
    ) -> None:
        self.branch = branch
        self.local_branches = local_branches or set()
        self.remote_branches = remote_branches or {}
        self.clean = clean
        self.head_sha = head_sha
        self.local_main_sha = local_main_sha
        self.origin_main_sha = origin_main_sha
        self.open_pr = open_pr
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command_id: str,
        **_: Any,
    ) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        args = list(command[1:]) if command and command[0] == "git" else list(command)
        stdout = ""
        returncode = 0
        if args[:2] == ["for-each-ref", "--format=%(refname:short)"]:
            stdout = "\n".join(sorted(self.local_branches))
        elif args[:3] == ["ls-remote", "--heads", "origin"]:
            stdout = "\n".join(
                f"{oid}\trefs/heads/{branch}"
                for branch, oid in sorted(self.remote_branches.items())
            )
        elif args[:2] == ["rev-parse", "refs/heads/main"]:
            stdout = self.local_main_sha
        elif args[:1] == ["rev-parse"] and len(args) == 2:
            branch = args[1].removeprefix("refs/heads/")
            if branch not in self.local_branches:
                returncode = 1
            else:
                stdout = SHA
        elif args[:3] == ["gh", "pr", "list"]:
            if self.open_pr is not None:
                stdout = json.dumps([self.open_pr])
            else:
                stdout = "[]"
        elif args[:3] == ["gh", "pr", "view"]:
            if self.open_pr is None:
                returncode = 1
            else:
                stdout = json.dumps(self.open_pr)
        elif args[:2] == ["switch", "-c"]:
            branch = args[2]
            self.local_branches.add(branch)
            self.branch = branch
        elif args[:2] == ["switch", "--track"]:
            branch = args[3]
            self.local_branches.add(branch)
            self.branch = branch
        elif args[:1] == ["switch"]:
            branch = args[1]
            if branch not in self.local_branches:
                returncode = 1
            else:
                self.branch = branch
        else:
            returncode = 1
        return CommandResult(
            command_id=command_id,
            argv=command,
            returncode=returncode,
            stdout=f"{stdout}\n" if stdout else "",
            stderr="" if returncode == 0 else "unsupported fake command",
        )


def _issue() -> dict[str, Any]:
    return {
        "number": 159,
        "title": "[Task] 建立 LCK Core 与 Live State Resolution",
        "state": "OPEN",
        "labels": {"items": ["type:task", "codex:ready"]},
        "project_status": "Ready",
        "critical_outcome": {
            "status": "valid",
            "contract": {
                "caller": "task-delivery-runner initial Delivery",
                "capability": "LCK Delivery",
                "observable_result": "READY_FOR_REVIEW",
                "verification_test": "tests/tools/test_lck.py::test_canonical_branch_is_derived_from_current_issue_title",
            },
        },
    }


def _relationships(
    *,
    blocked_by: dict[str, Any] | None = None,
    issue_type: str | None = "Task",
) -> dict[str, Any]:
    return {
        "available": True,
        "issue_type": issue_type,
        "blocked_by": blocked_by
        if blocked_by is not None
        else {"items": [], "count": 0, "truncated": False},
    }


def _git_snapshot(fake: FakeRunner) -> dict[str, Any]:
    return {
        "branch": fake.branch,
        "head_sha": fake.head_sha,
        "local_main_sha": fake.local_main_sha,
        "tracking_main_sha": fake.origin_main_sha,
        "remote_main_sha": fake.origin_main_sha,
        "origin_main_sha": fake.origin_main_sha,
        "remote_main_query": "pass",
        "clean": fake.clean,
        "status_entries": 0 if fake.clean else 1,
        "worktree_branches": {"items": [fake.branch], "count": 1, "truncated": False},
    }


def _install_facts(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeRunner,
    *,
    issue: dict[str, Any] | None = None,
    relationships: dict[str, Any] | None = None,
    open_pr: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> None:
    monkeypatch.setattr(lck, "_repository_slug", lambda *_args: "owner/repo")
    monkeypatch.setattr(lck, "_issue_view", lambda *_args: issue or _issue())
    monkeypatch.setattr(
        lck,
        "_relationship_snapshot",
        lambda *_args: relationships if relationships is not None else _relationships(),
    )
    monkeypatch.setattr(
        lck,
        "_git_snapshot",
        lambda *_args, **_kwargs: _git_snapshot(fake),
    )
    monkeypatch.setattr(lck, "resolve_open_pr", lambda *_args: open_pr)
    monkeypatch.setattr(
        lck, "list_matching_prs", lambda *_args, **_kwargs: history or []
    )


def _resolver(fake: FakeRunner) -> lck.LiveStateResolver:
    return lck.LiveStateResolver(Path.cwd(), runner=cast(Any, fake))


def _open_pr(
    branch: str,
    *,
    is_draft: bool = False,
) -> dict[str, Any]:
    return {
        "number": 200,
        "state": "OPEN",
        "isDraft": is_draft,
        "baseRefName": "main",
        "baseRefOid": SHA,
        "headRefName": branch,
        "headRefOid": SHA,
        "url": "https://github.com/owner/repo/pull/200",
        "statusCheckRollup": [],
    }


def test_canonical_branch_is_derived_from_current_issue_title() -> None:
    assert (
        lck.canonical_task_branch(159, "[Task] 建立 LCK Core 与 Live State Resolution")
        == "task/159-lck-core-live-state-resolution"
    )


def test_closed_pr_does_not_block_current_open_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch,
        local_branches={branch},
        remote_branches={branch: SHA},
    )
    open_pr = {
        "number": 200,
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "baseRefOid": SHA,
        "headRefName": branch,
        "headRefOid": SHA,
        "url": "https://github.com/owner/repo/pull/200",
        "statusCheckRollup": [],
    }
    _install_facts(
        monkeypatch,
        fake,
        open_pr=open_pr,
        history=[
            {"number": 199, "state": "CLOSED"},
            {"number": 200, "state": "OPEN"},
        ],
    )

    state = _resolver(fake).resolve(159)

    assert state.status is lck.ResolutionStatus.RESOLVED
    assert state.open_pr is not None
    assert state.open_pr["number"] == 200
    assert state.merged_pr_numbers == ()


def test_live_state_resolver_reads_non_empty_pr_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    open_pr = {
        "number": 200,
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "baseRefOid": SHA,
        "headRefName": branch,
        "headRefOid": SHA,
        "url": "https://github.com/owner/repo/pull/200",
        "statusCheckRollup": [
            {"name": "quality", "conclusion": "SUCCESS"},
        ],
    }
    fake = FakeRunner(
        branch=branch,
        local_branches={branch},
        remote_branches={branch: SHA},
        open_pr=open_pr,
    )
    monkeypatch.setattr(lck, "_repository_slug", lambda *_args: "owner/repo")
    monkeypatch.setattr(lck, "_issue_view", lambda *_args: _issue())
    monkeypatch.setattr(lck, "_relationship_snapshot", lambda *_args: _relationships())
    monkeypatch.setattr(
        lck,
        "_git_snapshot",
        lambda *_args, **_kwargs: _git_snapshot(fake),
    )

    state = _resolver(fake).resolve(159)

    assert state.status is lck.ResolutionStatus.RESOLVED
    assert state.checks["count"] == 1
    assert state.checks["success"] == 1
    view_commands = [
        command for command in fake.commands if command[:3] == ("gh", "pr", "view")
    ]
    assert len(view_commands) == 1
    fields = view_commands[0][view_commands[0].index("--json") + 1].split(",")
    assert "statusCheckRollup" in fields


def test_lck_live_snapshot_overrides_legacy_read_only_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake)
    monkeypatch.setenv("WORKFLOW_EVIDENCE_READ_ONLY", "1")
    observed: dict[str, Any] = {}

    def live_snapshot(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return _git_snapshot(fake)

    monkeypatch.setattr(lck, "_git_snapshot", live_snapshot)
    _resolver(fake).resolve(159)

    assert observed == {"read_only_local_refs": True}


def test_live_git_snapshot_separates_remote_main_from_tracking_cache() -> None:
    class ReadOnlyGitRunner:
        def __init__(self) -> None:
            self.commands: list[tuple[str, ...]] = []

        def run(
            self,
            argv: list[str] | tuple[str, ...],
            *,
            command_id: str,
            **_: Any,
        ) -> CommandResult:
            command = tuple(str(item) for item in argv)
            self.commands.append(command)
            outputs = {
                ("git", "branch", "--show-current"): "task/159-live",
                ("git", "rev-parse", "HEAD"): SHA,
                ("git", "rev-parse", "refs/heads/main"): SHA,
                ("git", "rev-parse", "refs/remotes/origin/main"): "b" * 40,
                (
                    "git",
                    "ls-remote",
                    "origin",
                    "refs/heads/main",
                ): f"{SHA}\trefs/heads/main",
                (
                    "git",
                    "status",
                    "--short",
                    "--untracked-files=all",
                ): "",
                ("git", "diff", "--cached", "--name-only"): "",
                ("git", "diff", "--name-only"): "",
                ("git", "worktree", "list", "--porcelain"): "worktree /repo\n",
            }
            if command == ("git", "fetch", "--prune", "origin"):
                return CommandResult(command_id, command, 1, "", "fetch forbidden")
            return CommandResult(
                command_id,
                command,
                0 if command in outputs else 1,
                outputs.get(command, ""),
                "" if command in outputs else "unsupported",
            )

    runner = ReadOnlyGitRunner()
    warnings: list[dict[str, Any]] = []
    snapshot = lck._git_snapshot(cast(Any, runner), warnings, read_only_local_refs=True)

    assert snapshot["local_main_sha"] == SHA
    assert snapshot["tracking_main_sha"] == "b" * 40
    assert snapshot["remote_main_sha"] == SHA
    assert snapshot["tracking_main_stale"] is True
    assert not any(
        command[1:4] == ("fetch", "--prune", "origin") for command in runner.commands
    )


def test_ambiguous_open_pr_stops_phase_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(branch=branch, local_branches={branch})
    _install_facts(monkeypatch, fake)

    def ambiguous(*_args: Any) -> dict[str, Any] | None:
        raise PrResolveError("multiple OPEN PRs for the Task branch")

    monkeypatch.setattr(lck, "resolve_open_pr", ambiguous)
    state = _resolver(fake).resolve(159)
    decision = lck.PhaseEligibilityResolver().resolve(state, lck.Phase.DELIVERY_PREPARE)

    assert state.status is lck.ResolutionStatus.STOP
    assert not decision.eligible
    assert any("multiple OPEN PRs" in reason for reason in decision.reasons)


def test_multiple_merged_prs_stop_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    _install_facts(
        monkeypatch,
        fake,
        history=[
            {"number": 200, "state": "MERGED"},
            {"number": 201, "state": "MERGED"},
        ],
    )

    with pytest.raises(lck.LckStopError, match="multiple merged PRs"):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_open_pr_without_task_branch_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake, open_pr=_open_pr(branch))

    with pytest.raises(
        lck.LckStopError,
        match="current OPEN PR has no local or remote Task branch",
    ):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_open_pr_head_mismatch_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch="main",
        local_branches={branch},
        remote_branches={branch: SHA},
    )
    open_pr = _open_pr(branch)
    open_pr["headRefOid"] = "b" * 40
    _install_facts(monkeypatch, fake, open_pr=open_pr)

    with pytest.raises(
        lck.LckStopError,
        match="current OPEN PR head OID differs",
    ):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_optional_git_snapshot_warning_does_not_stop_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake)

    def unavailable_git_snapshot(
        _runner: Any,
        warnings: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        warnings.append(
            {
                "command_id": "git-status",
                "exit_code": 1,
                "error": "git status unavailable",
            }
        )
        return _git_snapshot(fake)

    monkeypatch.setattr(lck, "_git_snapshot", unavailable_git_snapshot)

    state = _resolver(fake).resolve(159)

    assert state.status is lck.ResolutionStatus.RESOLVED


def test_remote_main_query_failure_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake)

    def unavailable_remote_main(
        _runner: Any,
        _warnings: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        value = _git_snapshot(fake)
        value["remote_main_sha"] = None
        value["remote_main_query"] = "unknown"
        value.pop("origin_main_sha", None)
        return value

    monkeypatch.setattr(lck, "_git_snapshot", unavailable_remote_main)

    with pytest.raises(lck.LckStopError, match="remote main query failed"):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_task_branch_inventory_warning_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake)

    def unavailable_task_branches(
        _resolver: Any,
        _task_number: int,
        warnings: list[dict[str, Any]],
    ) -> tuple[set[str], dict[str, str], bool]:
        warnings.append(
            {
                "command_id": "lck-local-task-branches",
                "exit_code": 1,
                "error": "local branch inventory unavailable",
            }
        )
        return set(), {}, True

    monkeypatch.setattr(
        lck.LiveStateResolver,
        "_task_branches",
        unavailable_task_branches,
    )

    with pytest.raises(lck.LckStopError, match="Task branch inventory"):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


@pytest.mark.parametrize(
    ("blocked_by", "expected_detail"),
    [
        (
            {"items": [], "count": 0, "truncated": True},
            "truncated",
        ),
        (
            {"items": []},
            "malformed",
        ),
        (
            {"items": [], "count": 1, "truncated": False},
            "count mismatch",
        ),
        (
            {
                "items": [{"number": 300, "state": "UNKNOWN"}],
                "count": 1,
                "truncated": False,
            },
            "unknown_state",
        ),
        (
            {
                "items": [{"number": 301, "state": "OPEN"}],
                "count": 1,
                "truncated": False,
            },
            "unresolved",
        ),
    ],
)
def test_formal_blocker_gate_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
    blocked_by: dict[str, Any],
    expected_detail: str,
) -> None:
    fake = FakeRunner(branch="main")
    _install_facts(
        monkeypatch,
        fake,
        relationships=_relationships(blocked_by=blocked_by),
    )

    with pytest.raises(lck.LckStopError, match="formal blocker gate") as error:
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert expected_detail in str(error.value)
    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


@pytest.mark.parametrize(
    ("phase", "project_status", "open_pr"),
    [
        (lck.Phase.DELIVERY_PREPARE, "Ready", None),
        (lck.Phase.REVIEW_PREPARE, "Review", "review"),
        (lck.Phase.REMEDIATION_PREPARE, "Review", "remediation"),
    ],
)
def test_all_non_closeout_phases_require_task_type(
    monkeypatch: pytest.MonkeyPatch,
    phase: lck.Phase,
    project_status: str,
    open_pr: str | None,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch, local_branches={branch}, remote_branches={branch: SHA}
    )
    issue = _issue()
    issue["project_status"] = project_status
    issue["labels"] = {"items": ["type:feature", "codex:ready"]}
    phase_pr = _open_pr(branch) if open_pr is not None else None
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        relationships=_relationships(issue_type="Feature"),
        open_pr=phase_pr,
    )

    state = _resolver(fake).resolve(159)
    decision = lck.PhaseEligibilityResolver().resolve(state, phase)

    assert not decision.eligible
    assert any("type:task" in reason for reason in decision.reasons)


def test_non_task_delivery_prepare_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    issue = _issue()
    issue["labels"] = {"items": ["type:feature", "codex:ready"]}
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        relationships=_relationships(issue_type="Feature"),
    )

    with pytest.raises(lck.LckStopError, match="type:task"):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


@pytest.mark.parametrize(
    ("phase", "project_status", "open_pr"),
    [
        (lck.Phase.DELIVERY_PREPARE, "Ready", None),
        (lck.Phase.REVIEW_PREPARE, "Review", "review"),
        (lck.Phase.REMEDIATION_PREPARE, "Review", "remediation"),
    ],
)
def test_all_non_closeout_phases_reject_lifecycle_label_conflict(
    monkeypatch: pytest.MonkeyPatch,
    phase: lck.Phase,
    project_status: str,
    open_pr: str | None,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch, local_branches={branch}, remote_branches={branch: SHA}
    )
    issue = _issue()
    issue["project_status"] = project_status
    issue["labels"] = {"items": ["type:task", "codex:ready", "codex:needs-spec"]}
    phase_pr = _open_pr(branch) if open_pr is not None else None
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        open_pr=phase_pr,
    )

    state = _resolver(fake).resolve(159)
    decision = lck.PhaseEligibilityResolver().resolve(state, phase)

    assert not decision.eligible
    assert any("lifecycle labels" in reason for reason in decision.reasons)


def test_lifecycle_label_conflict_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    issue = _issue()
    issue["labels"] = {"items": ["type:task", "codex:ready", "codex:needs-spec"]}
    _install_facts(monkeypatch, fake, issue=issue)

    with pytest.raises(lck.LckStopError, match="lifecycle labels"):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_resolve_open_pr_observes_draft_pr() -> None:
    branch = "task/159-lck-core-live-state-resolution"
    draft_pr = _open_pr(branch, is_draft=True)
    fake = FakeRunner(open_pr=draft_pr)

    observed = resolve_open_pr(
        cast(Any, fake),
        "owner/repo",
        branch,
        "main",
        [],
    )

    assert observed is not None
    assert observed["isDraft"] is True


def test_review_prepare_rejects_draft_open_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch, local_branches={branch}, remote_branches={branch: SHA}
    )
    issue = _issue()
    issue["project_status"] = "Review"
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        open_pr=_open_pr(branch, is_draft=True),
    )

    state = _resolver(fake).resolve(159)
    decision = lck.PhaseEligibilityResolver().resolve(
        state,
        lck.Phase.REVIEW_PREPARE,
    )

    assert not decision.eligible
    assert any("non-Draft" in reason for reason in decision.reasons)


def test_delivery_complete_allows_review_status_for_partial_effect_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch,
        local_branches={branch},
        remote_branches={branch: SHA},
    )
    issue = _issue()
    issue["project_status"] = "Review"
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        open_pr=_open_pr(branch),
    )

    state = _resolver(fake).resolve(159)
    decision = lck.PhaseEligibilityResolver().resolve(
        state,
        lck.Phase.DELIVERY_COMPLETE,
    )

    assert decision.eligible


def test_remediation_prepare_rejects_draft_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch, local_branches={branch}, remote_branches={branch: SHA}
    )
    issue = _issue()
    issue["project_status"] = "Review"
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        open_pr=_open_pr(branch, is_draft=True),
    )

    state = _resolver(fake).resolve(159)
    decision = lck.PhaseEligibilityResolver().resolve(
        state,
        lck.Phase.REMEDIATION_PREPARE,
    )

    assert not decision.eligible
    assert any("non-Draft" in reason for reason in decision.reasons)


def test_remediation_requires_pr_base_to_match_current_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch, local_branches={branch}, remote_branches={branch: SHA}
    )
    issue = _issue()
    issue["project_status"] = "Review"
    pr = _open_pr(branch)
    pr["baseRefOid"] = "b" * 40
    _install_facts(monkeypatch, fake, issue=issue, open_pr=pr)

    state = _resolver(fake).resolve(159)
    decision = lck.PhaseEligibilityResolver().resolve(
        state,
        lck.Phase.REMEDIATION_PREPARE,
    )

    assert not decision.eligible
    assert any(
        "base must match current origin/main" in reason for reason in decision.reasons
    )


def test_multiple_task_branches_stop_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(
        branch="main",
        local_branches={
            "task/159-lck-core-live-state-resolution",
            "task-159",
        },
    )
    _install_facts(monkeypatch, fake)

    state = _resolver(fake).resolve(159)

    assert state.status is lck.ResolutionStatus.STOP
    assert any(
        "multiple Task branch candidates" in reason for reason in state.stop_reasons
    )


def test_delivery_prepare_creates_then_reuses_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake)
    preparer = lck.DeliveryPreparer(_resolver(fake))

    created = preparer.prepare(159)
    reused = preparer.prepare(159)

    assert created.action == "created-from-main"
    assert reused.action == "already-prepared"
    assert fake.branch == "task/159-lck-core-live-state-resolution"
    assert sum(command[:2] == ("git", "switch") for command in fake.commands) == 1
    assert not any(
        command[:2] in {("git", "commit"), ("git", "push")}
        or (command[:3] == ("gh", "pr", "create"))
        for command in fake.commands
    )


def test_delivery_prepare_stops_on_divergent_main_without_branch_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(
        branch="main",
        local_main_sha=SHA,
        origin_main_sha="b" * 40,
    )
    _install_facts(monkeypatch, fake)

    with pytest.raises(lck.LckStopError, match="HEAD == local main == origin/main"):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:3] == ("git", "switch", "-c") for command in fake.commands)


def test_delivery_prepare_restores_remote_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(branch="main", remote_branches={branch: SHA})
    _install_facts(monkeypatch, fake)

    context = lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert context.action == "restored-from-remote"
    assert fake.branch == branch
    assert branch in fake.local_branches


def test_dirty_unrelated_worktree_is_not_switched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch="main",
        local_branches={branch},
        clean=False,
    )
    _install_facts(monkeypatch, fake)

    with pytest.raises(lck.LckStopError, match="dirty unrelated worktree"):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)


def test_dirty_current_task_worktree_is_not_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch,
        local_branches={branch},
        clean=False,
    )
    _install_facts(monkeypatch, fake)

    with pytest.raises(lck.LckStopError, match="clean worktree"):
        lck.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == branch
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_closeout_eligibility_uses_merged_live_pr_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(branch=branch, local_branches={branch})
    closed_issue = _issue()
    closed_issue.update({"state": "CLOSED", "project_status": "Done"})
    _install_facts(
        monkeypatch,
        fake,
        issue=closed_issue,
        history=[{"number": 200, "state": "MERGED"}],
    )

    state = _resolver(fake).resolve(159)
    decision = lck.PhaseEligibilityResolver().resolve(state, lck.Phase.CLOSEOUT)

    assert state.merged is True
    assert decision.eligible


def test_delivery_prepare_requires_valid_critical_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner()
    issue = _issue()
    issue["critical_outcome"] = {"status": "invalid", "detail": "missing"}
    _install_facts(monkeypatch, fake, issue=issue)
    state = _resolver(fake).resolve(159)

    decision = lck.PhaseEligibilityResolver().resolve(state, lck.Phase.DELIVERY_PREPARE)

    assert decision.eligible is False
    assert any(
        "Critical Outcome contract invalid" in reason for reason in decision.reasons
    )


def _review_state(
    *,
    head: str = SHA,
    base: str = SHA,
    clean: bool = True,
) -> lck.LiveState:
    branch = "task/159-lck-core-live-state-resolution"
    issue = _issue()
    issue.update(
        {
            "project_status": "Review",
            "body_sha256": "d" * 64,
        }
    )
    pr = _open_pr(branch)
    pr.update({"headRefOid": head, "baseRefOid": base})
    return lck.LiveState(
        task_number=159,
        repository="owner/repo",
        issue=issue,
        relationships=_relationships(),
        git={
            "branch": branch,
            "head_sha": head,
            "local_main_sha": base,
            "origin_main_sha": base,
            "origin_fetch": "pass",
            "clean": clean,
        },
        target_branch=branch,
        local_task_branch=branch,
        local_task_head=head,
        remote_task_branch=branch,
        remote_task_oid=head,
        open_pr=pr,
        merged_pr_numbers=(),
        merged=False,
        checks={
            "count": 1,
            "failed": 0,
            "pending": 0,
            "skipped_or_unknown": 0,
            "all_success": True,
        },
        cleanup={},
    )


def _review_identity_value(*, head: str = SHA, base: str = SHA) -> lck.ReviewIdentity:
    return lck.ReviewIdentity(
        task_number=159,
        pr_number=200,
        base_sha=base,
        head_sha=head,
        task_body_sha256="d" * 64,
        merge_base_sha=base,
        effective_diff_sha256="e" * 64,
        changed_files=("tools/agent_workflow/lck.py",),
    )


class StaticResolver:
    def __init__(self, repo_root: Path, state: lck.LiveState) -> None:
        self.repo_root = repo_root
        self.state = state
        self.runner = cast(Any, FakeRunner())

    def resolve(self, _task_number: int) -> lck.LiveState:
        return self.state


class FakeWorkspaceRunner:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.status_output = ""
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command_id: str,
        **_: Any,
    ) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        args = list(command[1:]) if command and command[0] == "git" else list(command)
        stdout = ""
        if args == ["worktree", "list", "--porcelain"]:
            stdout = f"worktree {self.root}\n"
        elif args == ["status", "--porcelain=v1", "--untracked-files=all"]:
            stdout = self.status_output
        elif args == ["rev-parse", "HEAD"]:
            stdout = f"{SHA}\n"
        else:
            return CommandResult(
                command_id=command_id,
                argv=command,
                returncode=1,
                stdout="",
                stderr=f"unsupported fake command: {args}",
            )
        return CommandResult(
            command_id=command_id,
            argv=command,
            returncode=0,
            stdout=stdout,
            stderr="",
        )


class FakeReviewWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sealed: list[Path] = []
        self.ready_checked: list[Path] = []
        self.removed: list[Path] = []

    def path_for(self, _task: int, _operation_id: str) -> Path:
        return self.root

    def create(self, _task: int, _head: str, path: Path | None = None) -> Path:
        root = path or self.root
        root.mkdir(parents=True, exist_ok=True)
        return root

    def seal_read_only(self, path: Path) -> None:
        self.sealed.append(path)

    def seal_for_review(self, path: Path, _head: str) -> None:
        self.seal_read_only(path)

    def assert_ready_for_completion(self, path: Path, _head: str) -> None:
        self.ready_checked.append(path)

    def remove(self, path: Path) -> None:
        self.removed.append(path)

    def remove_recovered(self, path: Path) -> None:
        self.remove(path)


class FakeReviewChecks:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, _task: int) -> dict[str, Any]:
        self.calls += 1
        return {
            "status": "pass",
            "pr": {"number": 200, "head_sha": SHA, "base_sha": SHA},
        }


class FakeReviewValidation:
    def run(self, _root: Path, _base: str, _head: str) -> dict[str, Any]:
        return {"status": "pass", "phase": "review"}


def test_review_prepare_builds_context_only_from_live_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    identity = _review_identity_value()
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {
            "number": 159,
            "title": _issue()["title"],
            "body": "Task Contract",
            "body_sha256": "d" * 64,
        },
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: identity)
    workspace = FakeReviewWorkspace(tmp_path / "review-root")
    checks = FakeReviewChecks()
    store = lck.ReviewInvocationStore(tmp_path)

    context = lck.ReviewPreparer(
        resolver,
        validation=cast(Any, FakeReviewValidation()),
        checks_gate=cast(Any, checks),
        workspace=cast(Any, workspace),
        store=store,
    ).prepare(159)

    value = context.to_dict()
    assert value["review_target"]["head_sha"] == SHA
    assert value["review_target"]["base_sha"] == SHA
    assert value["task_contract"]["body"] == "Task Contract"
    assert value["workspace_mode"] == "implementation-read-only"
    assert value["mechanical_authority"].startswith("live Git/GitHub")
    assert workspace.sealed == [tmp_path / "review-root"]
    assert store.guard_path(context.review_id).is_file()
    inflight = json.loads(
        store.review_prepare_inflight_path(159).read_text(encoding="utf-8")
    )
    assert inflight["state"] == "handed-off"
    assert inflight["review_id"] == context.review_id
    assert checks.calls == 2


def test_review_prepare_rechecks_eligibility_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    identity = _review_identity_value()
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"number": 159, "body_sha256": "d" * 64},
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: identity)

    class DraftingValidation(FakeReviewValidation):
        def run(self, _root: Path, _base: str, _head: str) -> dict[str, Any]:
            assert state.open_pr is not None
            state.open_pr["isDraft"] = True
            return super().run(_root, _base, _head)

    workspace = FakeReviewWorkspace(tmp_path / "review-root")
    with pytest.raises(lck.LckStopError, match="post-validation STOP"):
        lck.ReviewPreparer(
            resolver,
            validation=cast(Any, DraftingValidation()),
            checks_gate=cast(Any, FakeReviewChecks()),
            workspace=cast(Any, workspace),
            store=lck.ReviewInvocationStore(tmp_path),
        ).prepare(159)

    assert workspace.removed == [tmp_path / "review-root"]


def test_review_complete_head_change_is_review_stale_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _review_identity_value()
    current = _review_identity_value(head="b" * 40)
    state = _review_state(head="b" * 40)
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_guard(
        review_id,
        {
            "task_number": 159,
            "identity": start.to_dict(),
            "review_root": str(tmp_path / "review-root"),
            "checks": {"status": "pass"},
            "validation": {"status": "pass"},
        },
    )
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"body_sha256": "d" * 64},
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: current)
    workspace = FakeReviewWorkspace(tmp_path / "review-root")

    with pytest.raises(lck.ReviewStaleError) as exc_info:
        lck.ReviewCompleter(
            resolver,
            store=store,
            workspace=cast(Any, workspace),
            checks_gate=cast(Any, FakeReviewChecks()),
        ).complete(159, review_id, verdict="PASS")

    assert exc_info.value.code == "REVIEW_STALE_HEAD"
    assert workspace.removed == [tmp_path / "review-root"]
    assert not store.guard_path(review_id).exists()


def test_review_complete_base_change_is_review_stale_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _review_identity_value()
    current = _review_identity_value(base="b" * 40)
    state = _review_state(base="b" * 40)
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_guard(
        review_id,
        {
            "task_number": 159,
            "identity": start.to_dict(),
            "review_root": str(tmp_path / "review-root"),
            "checks": {"status": "pass"},
            "validation": {"status": "pass"},
        },
    )
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"body_sha256": "d" * 64},
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: current)

    with pytest.raises(lck.ReviewStaleError) as exc_info:
        lck.ReviewCompleter(
            resolver,
            store=store,
            workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
        ).complete(159, review_id, verdict="PASS")

    assert exc_info.value.code == "REVIEW_STALE_BASE"


def test_review_fail_returns_stop_required_without_starting_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value()
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_guard(
        review_id,
        {
            "task_number": 159,
            "identity": identity.to_dict(),
            "review_root": str(tmp_path / "review-root"),
            "checks": {"status": "pass"},
            "validation": {"status": "pass"},
        },
    )
    findings = tmp_path / "findings.md"
    findings.write_text("[F1][Medium] Repair this behavior.\n", encoding="utf-8")
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"body_sha256": "d" * 64},
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: identity)
    checks = FakeReviewChecks()

    result = lck.ReviewCompleter(
        resolver,
        store=store,
        workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
        checks_gate=cast(Any, checks),
    ).complete(159, review_id, verdict="FAIL", findings_file=findings)

    assert result.status == "STOP_REQUIRED"
    assert result.to_dict()["automatic_remediation"] is False
    assert checks.calls == 0
    record = store.read_record(159, review_id)
    assert record["findings"].startswith("[F1][Medium]")
    assert record["authority_note"].startswith("record is diagnostic")


def test_review_pass_stops_at_human_merge_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value()
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_guard(
        review_id,
        {
            "task_number": 159,
            "identity": identity.to_dict(),
            "review_root": str(tmp_path / "review-root"),
            "checks": {"status": "pass"},
            "validation": {"status": "pass"},
        },
    )
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"body_sha256": "d" * 64},
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: identity)
    checks = FakeReviewChecks()

    result = lck.ReviewCompleter(
        resolver,
        store=store,
        workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
        checks_gate=cast(Any, checks),
    ).complete(159, review_id, verdict="PASS")

    assert result.status == "READY_FOR_HUMAN_MERGE"
    assert "manual merge" in result.to_dict()["human_boundary"]
    assert checks.calls == 1


def test_review_complete_rechecks_identity_after_pass_checks_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _review_identity_value()
    current = _review_identity_value(head="b" * 40)
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_guard(
        review_id,
        {
            "task_number": 159,
            "identity": start.to_dict(),
            "review_root": str(tmp_path / "review-root"),
            "checks": {"status": "pass"},
            "validation": {"status": "pass"},
        },
    )

    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"body_sha256": "d" * 64},
    )
    identities = iter((start, current))
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: next(identities))

    class AdvancingChecks(FakeReviewChecks):
        def run(self, _task: int) -> dict[str, Any]:
            assert state.open_pr is not None
            state.open_pr["headRefOid"] = "b" * 40
            return super().run(_task)

    with pytest.raises(lck.ReviewStaleError) as exc_info:
        lck.ReviewCompleter(
            resolver,
            store=store,
            workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
            checks_gate=cast(Any, AdvancingChecks()),
        ).complete(159, review_id, verdict="PASS")

    assert exc_info.value.code == "REVIEW_STALE_HEAD"


def test_review_complete_rejects_checks_drift_after_pass_checks_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    identity = _review_identity_value()
    store.write_guard(
        review_id,
        {
            "task_number": 159,
            "identity": identity.to_dict(),
            "review_root": str(tmp_path / "review-root"),
            "checks": {"status": "pass"},
            "validation": {"status": "pass"},
        },
    )
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"body_sha256": "d" * 64},
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: identity)

    class ChecksDriftAfterGate(FakeReviewChecks):
        def run(self, _task: int) -> dict[str, Any]:
            result = {
                "status": "pass",
                "pr": {"number": 200, "head_sha": SHA, "base_sha": SHA},
            }
            state.checks["failed"] = 1
            state.checks["all_success"] = False
            return result

    with pytest.raises(lck.LckStopError, match="current PR checks"):
        lck.ReviewCompleter(
            resolver,
            store=store,
            workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
            checks_gate=cast(Any, ChecksDriftAfterGate()),
        ).complete(159, review_id, verdict="PASS")

    assert not store.record_path(159, review_id).exists()


def test_review_complete_rechecks_current_review_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    assert state.open_pr is not None
    state.open_pr["isDraft"] = True
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_guard(
        review_id,
        {
            "task_number": 159,
            "identity": _review_identity_value().to_dict(),
            "review_root": str(tmp_path / "review-root"),
            "checks": {"status": "pass"},
            "validation": {"status": "pass"},
        },
    )
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"body_sha256": "d" * 64},
    )
    monkeypatch.setattr(
        lck, "_review_identity", lambda *_args: _review_identity_value()
    )
    workspace = FakeReviewWorkspace(tmp_path / "review-root")

    with pytest.raises(lck.LckStopError, match="Review Complete STOP"):
        lck.ReviewCompleter(
            resolver,
            store=store,
            workspace=cast(Any, workspace),
            checks_gate=cast(Any, FakeReviewChecks()),
        ).complete(159, review_id, verdict="PASS")

    assert workspace.removed == [tmp_path / "review-root"]
    assert not store.guard_path(review_id).exists()


def test_review_workspace_seal_removes_write_bits(tmp_path: Path) -> None:
    root = tmp_path / "review"
    nested = root / "pkg"
    nested.mkdir(parents=True)
    target = nested / "file.py"
    target.write_text("print('read only')\n", encoding="utf-8")
    executable = nested / "tool.py"
    executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    executable.chmod(0o755)

    lck.ReviewWorkspaceManager.seal_read_only(root)

    assert root.stat().st_mode & 0o222 == 0
    assert nested.stat().st_mode & 0o222 == 0
    assert target.stat().st_mode & 0o222 == 0
    assert executable.stat().st_mode & 0o222 == 0
    assert executable.stat().st_mode & 0o111 != 0
    lck.ReviewWorkspaceManager._make_removable(root)


def test_review_workspace_seal_preserves_clean_status_and_rejects_mutation(
    tmp_path: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="tracequant-lck-review-test-") as raw:
        root = Path(raw)
        (root / "tracked.py").write_text("print('review')\n", encoding="utf-8")
        resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
        runner = FakeWorkspaceRunner(root)
        resolver.runner = runner
        manager = lck.ReviewWorkspaceManager(resolver)

        manager.seal_for_review(root, SHA)

        assert not any(
            command[:3] == ("git", "config", "--worktree")
            for command in runner.commands
        )
        assert root.stat().st_mode & 0o222 == 0
        assert (root / "tracked.py").stat().st_mode & 0o222 == 0
        manager.assert_ready_for_completion(root, SHA)

        runner.status_output = " M tracked.py\n"
        with pytest.raises(lck.LckStopError, match="changed the isolated worktree"):
            manager.assert_ready_for_completion(root, SHA)
        manager._make_removable(root)


def test_review_workspace_seal_real_git_multi_worktree_without_worktree_config(
    tmp_path: Path,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required for Review worktree integration test")

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str, cwd: Path = repo) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    git("init")
    git("config", "user.name", "TraceQuant Test")
    git("config", "user.email", "tracequant-test@example.invalid")
    tracked = repo / "tracked.py"
    tracked.write_text("print('review')\n", encoding="utf-8")
    executable = repo / "tool.py"
    executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    executable.chmod(0o755)
    git("add", "tracked.py", "tool.py")
    git("commit", "-m", "initial")
    head_sha = git("rev-parse", "HEAD").stdout.strip()

    unset = subprocess.run(
        ["git", "config", "--unset-all", "extensions.worktreeConfig"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert unset.returncode in {0, 5}

    resolver = cast(Any, StaticResolver(repo, _review_state()))
    resolver.runner = CommandRunner(repo)
    manager = lck.ReviewWorkspaceManager(resolver)
    review_root = manager.create(159, head_sha)
    worktrees = git("worktree", "list", "--porcelain").stdout
    assert worktrees.count("worktree ") == 2
    config_before_seal = (repo / ".git" / "config").read_text(encoding="utf-8")

    try:
        manager.seal_for_review(review_root, head_sha)

        assert (repo / ".git" / "config").read_text(
            encoding="utf-8"
        ) == config_before_seal
        status = git(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            cwd=review_root,
        )
        assert status.stdout.strip() == ""
        assert git("rev-parse", "HEAD", cwd=review_root).stdout.strip() == head_sha
        assert review_root.stat().st_mode & 0o222 == 0
        assert (review_root / "tracked.py").stat().st_mode & 0o222 == 0
        sealed_executable = review_root / "tool.py"
        assert sealed_executable.stat().st_mode & 0o222 == 0
        assert sealed_executable.stat().st_mode & 0o111 != 0

        configured = subprocess.run(
            ["git", "config", "--get", "extensions.worktreeConfig"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert configured.returncode == 1
        assert configured.stdout.strip() == ""
    finally:
        manager.remove(review_root)


def test_review_workspace_remove_rejects_unvalidated_path(tmp_path: Path) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    manager = lck.ReviewWorkspaceManager(resolver)

    with pytest.raises(lck.LckStopError, match="cleanup path"):
        manager.remove(tmp_path / "not-an-lck-worktree")


def test_review_validation_artifacts_are_preserved_outside_review_worktree(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    review_root = tmp_path / "review-root"
    tool = review_root / "tools" / "agent_workflow" / "workflow_validation.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("# validation stub\n", encoding="utf-8")
    output_dir = review_root / ".agents" / "validation.local" / "run"
    output_dir.mkdir(parents=True)
    (output_dir / "pytest.log").write_text("pass\n", encoding="utf-8")

    class ValidationRunner:
        def run(self, argv: Any, *, command_id: str, **_: Any) -> CommandResult:
            payload = {
                "status": "pass",
                "output_dir": ".agents/validation.local/run",
                "commands": [
                    {
                        "status": "pass",
                        "log_path": ".agents/validation.local/run/pytest.log",
                    }
                ],
            }
            return CommandResult(
                command_id=command_id,
                argv=tuple(str(item) for item in argv),
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )

    resolver = cast(Any, StaticResolver(repo_root, _review_state()))
    resolver.runner = ValidationRunner()
    validation = lck.ReviewValidationGate(resolver).run(review_root, SHA, SHA)

    durable_output = repo_root / validation["output_dir"]
    durable_log = repo_root / validation["commands"][0]["log_path"]
    assert validation["output_dir"].startswith(".agents/validation.local/lck-review-")
    assert validation["evidence_path"] == validation["output_dir"]
    assert validation["validated_base_sha"] == SHA
    assert validation["validated_head_sha"] == SHA
    assert durable_output.is_dir()
    assert durable_log.read_text(encoding="utf-8") == "pass\n"
    evidence_file = repo_root / validation["evidence_file"]
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert evidence["validated_base_sha"] == SHA
    assert evidence["commands"][0]["status"] == "pass"


def test_review_validation_failure_is_persisted_before_prepare_stops(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    review_root = tmp_path / "review-root"
    tool = review_root / "tools" / "agent_workflow" / "workflow_validation.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("# validation stub\n", encoding="utf-8")
    output_dir = review_root / ".agents" / "validation.local" / "run"
    output_dir.mkdir(parents=True)
    (output_dir / "ruff-check.log").write_text("format mismatch\n", encoding="utf-8")

    class ValidationRunner:
        def run(self, argv: Any, *, command_id: str, **_: Any) -> CommandResult:
            payload = {
                "status": "fail",
                "output_dir": ".agents/validation.local/run",
                "commands": [
                    {
                        "command_id": "ruff-check",
                        "status": "fail",
                        "exit_code": 1,
                        "diagnostic": "format mismatch",
                        "log_path": ".agents/validation.local/run/ruff-check.log",
                    }
                ],
            }
            return CommandResult(
                command_id=command_id,
                argv=tuple(str(item) for item in argv),
                returncode=1,
                stdout=json.dumps(payload),
                stderr="",
            )

    resolver = cast(Any, StaticResolver(repo_root, _review_state()))
    resolver.runner = ValidationRunner()
    validation = lck.ReviewValidationGate(resolver).run(review_root, SHA, "b" * 40)

    assert validation["status"] == "fail"
    assert validation["validated_base_sha"] == SHA
    assert validation["validated_head_sha"] == "b" * 40
    assert validation["commands"][0]["command_id"] == "ruff-check"
    assert validation["commands"][0]["exit_code"] == 1
    evidence = repo_root / validation["evidence_path"]
    assert evidence.is_dir()
    assert (evidence / "ruff-check.log").read_text(encoding="utf-8") == (
        "format mismatch\n"
    )
    stored = json.loads(
        (repo_root / validation["evidence_file"]).read_text(encoding="utf-8")
    )
    assert stored["commands"][0]["command_id"] == "ruff-check"
    assert stored["commands"][0]["exit_code"] == 1
    assert stored["commands"][0]["diagnostic"] == "format mismatch"
    assert stored["validated_head_sha"] == "b" * 40


def test_review_prepare_claims_operation_before_validation_and_has_no_review_id_on_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    identity = _review_identity_value()
    store = lck.ReviewInvocationStore(tmp_path)
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"number": 159, "body_sha256": "d" * 64},
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: identity)

    class FailingValidation:
        def run(self, _root: Path, base: str, head: str) -> dict[str, Any]:
            assert store.review_prepare_inflight_path(159).is_file()
            return {
                "status": "fail",
                "validated_base_sha": base,
                "validated_head_sha": head,
                "commands": [
                    {
                        "command_id": "ruff-check",
                        "status": "fail",
                        "exit_code": 1,
                        "diagnostic": "format mismatch",
                    }
                ],
                "evidence_path": ".agents/validation.local/lck-review-failure",
            }

    workspace = FakeReviewWorkspace(tmp_path / "review-root")
    with pytest.raises(
        lck.LckStopError,
        match="failed command ruff-check.*evidence",
    ) as exc_info:
        lck.ReviewPreparer(
            resolver,
            validation=cast(Any, FailingValidation()),
            checks_gate=cast(Any, FakeReviewChecks()),
            workspace=cast(Any, workspace),
            store=store,
        ).prepare(159)

    assert "validated base" in str(exc_info.value)
    assert workspace.removed == [tmp_path / "review-root"]
    assert not (store.root / "review-invocations").exists()
    assert not store.review_prepare_inflight_path(159).exists()


def test_review_prepare_inflight_guard_blocks_second_operation(tmp_path: Path) -> None:
    store = lck.ReviewInvocationStore(tmp_path)
    first = store.begin_review_prepare(159)
    try:
        with pytest.raises(lck.LckStopError, match="already in flight"):
            store.begin_review_prepare(159)
    finally:
        first.finish()

    assert not store.review_prepare_inflight_path(159).exists()


def test_review_prepare_handoff_keeps_explicit_ownership_until_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "tracequant-lck-review-159-handoff"
    review_root.mkdir()
    store.write_guard(review_id, {"review_id": review_id})
    store.review_prepare_inflight_path(159).parent.mkdir(parents=True, exist_ok=True)
    store.review_prepare_inflight_path(159).write_text(
        json.dumps(
            {
                "task_number": 159,
                "pid": 1,
                "operation_id": store.new_id(),
                "state": "handed-off",
                "review_id": review_id,
                "review_root": str(review_root),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        lck.ReviewInvocationStore,
        "_pid_is_alive",
        staticmethod(lambda _pid: False),
    )

    with pytest.raises(lck.LckStopError, match="handoff is still owned"):
        store.begin_review_prepare(159)

    assert store.review_prepare_inflight_path(159).is_file()


def test_remediation_prepare_uses_live_head_not_review_record_identity(
    tmp_path: Path,
) -> None:
    live_head = "b" * 40
    state = _review_state(head=live_head, base=SHA)
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_record(
        159,
        review_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value(head=SHA).to_dict(),
            "findings": "[F1][Medium] Semantic repair input.",
        },
    )
    store.write_latest_review(159, review_id, "FAIL")

    context = lck.RemediationPreparer(resolver, store=store).prepare(159, review_id)

    assert context.to_dict()["live_target"]["head_sha"] == live_head
    assert context.findings == "[F1][Medium] Semantic repair input."
    assert "current live state" in context.to_dict()["mechanical_authority"]


def test_remediation_complete_requires_actual_repair_changes(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(clean=True)))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_record(
        159,
        review_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value().to_dict(),
            "findings": "[F1][Medium] Repair required.",
        },
    )
    store.write_latest_review(159, review_id, "FAIL")

    with pytest.raises(
        lck.LckStopError, match="repaired head or uncommitted repair changes"
    ):
        lck.RemediationCompleter(resolver, store=store).complete(
            159,
            review_id,
            commit_message="Repair review finding",
            summary="Repair finding",
        )


def test_reuse_existing_open_pr_never_creates_a_replacement(tmp_path: Path) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))

    receipt = lck.ReuseExistingOpenPrEffect(resolver).execute(
        159,
        summary="ignored",
        risks="ignored",
        critical_outcome={"status": "valid"},
        validation={"status": "pass"},
        expected_base_sha=SHA,
        expected_body_sha256="d" * 64,
    )

    assert receipt.effect == "reuse_open_pr"
    assert receipt.action == "reused-current-open-pr"
    assert resolver.runner.commands == []


def test_remediation_requires_latest_failed_review(tmp_path: Path) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    store = lck.ReviewInvocationStore(tmp_path)
    failed_id = store.new_id()
    newer_id = store.new_id()
    store.write_record(
        159,
        failed_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value().to_dict(),
            "findings": "[F1][Medium] Old finding.",
        },
    )
    store.write_latest_review(159, newer_id, "PASS")

    with pytest.raises(lck.LckStopError, match="latest completed Independent Review"):
        lck.RemediationPreparer(resolver, store=store).prepare(159, failed_id)


def test_post_remediation_boundary_blocks_another_remediation_until_review(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(head="b" * 40)))
    store = lck.ReviewInvocationStore(tmp_path)
    failed_id = store.new_id()
    store.write_record(
        159,
        failed_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value().to_dict(),
            "findings": "[F1][Medium] Repair required.",
        },
    )
    store.write_latest_review(159, failed_id, "FAIL")
    store.write_review_required(159, failed_id, "b" * 40)

    with pytest.raises(lck.LckStopError, match="fresh Independent Review is required"):
        lck.RemediationPreparer(resolver, store=store).prepare(159, failed_id)


def test_accepted_fresh_review_releases_post_remediation_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value(head="b" * 40)
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(head="b" * 40)))
    store = lck.ReviewInvocationStore(tmp_path)
    old_review_id = store.new_id()
    store.write_review_required(159, old_review_id, "b" * 40)
    review_id = store.new_id()
    store.write_guard(
        review_id,
        {
            "task_number": 159,
            "identity": identity.to_dict(),
            "review_root": str(tmp_path / "review-root"),
            "checks": {"status": "pass"},
            "validation": {"status": "pass"},
        },
    )
    findings = tmp_path / "findings-new.md"
    findings.write_text("[F2][Medium] New review finding.\n", encoding="utf-8")
    monkeypatch.setattr(
        lck,
        "_live_task_contract",
        lambda *_args: {"body_sha256": "d" * 64},
    )
    monkeypatch.setattr(lck, "_review_identity", lambda *_args: identity)

    result = lck.ReviewCompleter(
        resolver,
        store=store,
        workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
    ).complete(159, review_id, verdict="FAIL", findings_file=findings)

    assert result.status == "STOP_REQUIRED"
    assert store.read_review_required(159) is None
    latest = store.read_latest_review(159)
    assert latest is not None
    assert latest["review_id"] == review_id
    assert latest["verdict"] == "FAIL"


def test_remediation_complete_can_resume_committed_new_head_and_requires_re_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired_head = "b" * 40
    state = _review_state(head=repaired_head, clean=True)
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_record(
        159,
        review_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value(head=SHA).to_dict(),
            "findings": "[F1][Medium] Repair required.",
        },
    )
    store.write_latest_review(159, review_id, "FAIL")

    delivery_result = lck.DeliveryCompletionResult(
        task_number=159,
        status="READY_FOR_REVIEW",
        branch=state.target_branch,
        head_sha=repaired_head,
        critical_outcome={"status": "pass"},
        validation={"status": "pass"},
        checks={"status": "pass"},
        effects=(),
        final_state=state,
    )

    class FakeDeliveryCompleter:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def complete(self, *_args: Any, **_kwargs: Any) -> lck.DeliveryCompletionResult:
            return delivery_result

    monkeypatch.setattr(lck, "DeliveryCompleter", FakeDeliveryCompleter)

    result = lck.RemediationCompleter(resolver, store=store).complete(
        159,
        review_id,
        commit_message="Repair review finding",
        summary="Repair finding",
    )

    assert result.to_dict()["status"] == "READY_FOR_NEW_REVIEW"
    assert result.to_dict()["automatic_review"] is False
    required = store.read_review_required(159)
    assert required is not None
    assert required["remediated_head"] == repaired_head
    with pytest.raises(lck.LckStopError, match="fresh Independent Review is required"):
        lck.RemediationPreparer(resolver, store=store).prepare(159, review_id)
