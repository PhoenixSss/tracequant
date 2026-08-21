# ruff: noqa: E402, I001

from __future__ import annotations

import json
import sys
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
from workflow_common import CommandResult  # type: ignore[import-not-found]  # noqa: E402


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
        "origin_main_sha": fake.origin_main_sha,
        "origin_fetch": "pass",
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
    monkeypatch.setattr(lck, "_git_snapshot", lambda *_args: _git_snapshot(fake))
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
    monkeypatch.setattr(lck, "_git_snapshot", lambda *_args: _git_snapshot(fake))

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


def test_git_snapshot_warning_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake)

    def unavailable_git_snapshot(
        _runner: Any,
        warnings: list[dict[str, Any]],
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

    with pytest.raises(lck.LckStopError, match="local Git snapshot"):
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


def test_remediation_prepare_keeps_draft_pr_observable(
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

    assert decision.eligible


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
