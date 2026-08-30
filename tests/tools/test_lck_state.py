# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from pathlib import Path
from typing import (
    Any,
    cast,
)

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    delivery as lck_delivery,
    eligibility as lck_eligibility,
    models as lck_models,
    state as lck_state,
)
from pr_resolve import (  # type: ignore[import-not-found]  # noqa: E402
    PrResolveError,
    list_matching_prs,
)
from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
)
from workflow_evidence import (  # type: ignore[import-not-found]  # noqa: E402
    _git_snapshot as workflow_git_snapshot,
)
from lck_test_support import (  # noqa: E402
    FakeRunner,
    SHA,
    _git_snapshot,
    _install_facts,
    _issue,
    _open_pr,
    _relationships,
    _resolver,
    _task_contract,
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

    assert state.status is lck_models.ResolutionStatus.RESOLVED
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
    monkeypatch.setattr(lck_state, "_repository_slug", lambda *_args: "owner/repo")
    monkeypatch.setattr(
        lck_state,
        "_issue_view_with_contract",
        lambda *_args: (_issue(), _task_contract()),
    )
    monkeypatch.setattr(
        lck_state, "_relationship_snapshot", lambda *_args: _relationships()
    )
    monkeypatch.setattr(
        lck_state,
        "_git_snapshot",
        lambda *_args, **_kwargs: _git_snapshot(fake),
    )

    state = _resolver(fake).resolve(159)

    assert state.status is lck_models.ResolutionStatus.RESOLVED
    assert state.checks["count"] == 1
    assert state.checks["success"] == 1
    view_commands = [
        command for command in fake.commands if command[:3] == ("gh", "pr", "view")
    ]
    assert len(view_commands) == 1
    fields = view_commands[0][view_commands[0].index("--json") + 1].split(",")
    assert "statusCheckRollup" in fields


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
    snapshot = workflow_git_snapshot(
        cast(Any, runner), warnings, read_only_local_refs=True
    )

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

    monkeypatch.setattr(lck_state, "resolve_open_pr", ambiguous)
    state = _resolver(fake).resolve(159)
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state, lck_models.Phase.DELIVERY_PREPARE
    )

    assert state.status is lck_models.ResolutionStatus.STOP
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

    with pytest.raises(lck_models.LckStopError, match="multiple merged PRs"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_open_pr_without_task_branch_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake, open_pr=_open_pr(branch))

    with pytest.raises(
        lck_models.LckStopError,
        match="current OPEN PR has no local or remote Task branch",
    ):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

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
        lck_models.LckStopError,
        match="current OPEN PR head OID differs",
    ):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

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

    monkeypatch.setattr(lck_state, "_git_snapshot", unavailable_git_snapshot)

    state = _resolver(fake).resolve(159)

    assert state.status is lck_models.ResolutionStatus.RESOLVED


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

    monkeypatch.setattr(lck_state, "_git_snapshot", unavailable_remote_main)

    with pytest.raises(lck_models.LckStopError, match="remote main query failed"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

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
        lck_state.LiveStateResolver,
        "_task_branches",
        unavailable_task_branches,
    )

    with pytest.raises(lck_models.LckStopError, match="Task branch inventory"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (
            "Delivery Prepare",
            {
                "issue": (False, False, True),
                "git": {
                    "read_only_local_refs": True,
                    "include_workspace_inventory": False,
                },
                "branches": (True, True),
                "pr": (False, False, False),
                "history": {
                    "include_checks": False,
                    "include_mergeability": False,
                    "include_history_details": False,
                },
                "included": {
                    "task_contract",
                    "git",
                    "pr_history",
                },
                "excluded": {
                    "comments",
                    "issue_closure",
                    "workspace_inventory",
                    "checks",
                },
            },
        ),
        (
            "Delivery Complete",
            {
                "issue": (False, False, True),
                "git": {
                    "read_only_local_refs": True,
                    "include_workspace_inventory": False,
                },
                "branches": (True, True),
                "pr": (True, False, False),
                "history": {
                    "include_checks": False,
                    "include_mergeability": False,
                    "include_history_details": False,
                },
                "included": {"task_contract", "git", "checks", "pr_history"},
                "excluded": {"comments", "issue_closure", "workspace_inventory"},
            },
        ),
        (
            "review-prepare",
            {
                "issue": (False, False, True),
                "git": None,
                "branches": (False, True),
                "pr": (True, False, False),
                "history": None,
                "included": {
                    "task_contract",
                    "remote_task_branches",
                    "open_pr",
                    "checks",
                },
                "excluded": {
                    "comments",
                    "issue_closure",
                    "git",
                    "workspace_inventory",
                    "local_task_branches",
                    "pr_history",
                },
            },
        ),
        (
            "Remediation Prepare",
            {
                "issue": (False, False, True),
                "git": {
                    "read_only_local_refs": True,
                    "include_workspace_inventory": False,
                },
                "branches": (True, True),
                "pr": (False, False, False),
                "history": None,
                "included": {
                    "task_contract",
                    "git",
                    "local_task_branches",
                    "remote_task_branches",
                    "open_pr",
                },
                "excluded": {
                    "comments",
                    "issue_closure",
                    "workspace_inventory",
                    "checks",
                    "pr_history",
                },
            },
        ),
        (
            "Remediation No Change",
            {
                "issue": (False, False, True),
                "git": {
                    "read_only_local_refs": True,
                    "include_workspace_inventory": False,
                },
                "branches": (True, True),
                "pr": (False, False, False),
                "history": None,
                "included": {
                    "git",
                    "local_task_branches",
                    "remote_task_branches",
                    "open_pr",
                },
                "excluded": {
                    "comments",
                    "issue_closure",
                    "workspace_inventory",
                    "checks",
                    "pr_history",
                },
            },
        ),
        (
            "merge-preflight",
            {
                "issue": (False, False, True),
                "git": None,
                "branches": (False, True),
                "pr": (True, True, False),
                "history": None,
                "included": {
                    "task_contract",
                    "remote_task_branches",
                    "open_pr",
                    "checks",
                    "mergeability",
                },
                "excluded": {
                    "comments",
                    "issue_closure",
                    "git",
                    "workspace_inventory",
                    "local_task_branches",
                    "pr_history",
                },
            },
        ),
        (
            "closeout",
            {
                "issue": (False, True, True),
                "git": {
                    "read_only_local_refs": True,
                    "include_workspace_inventory": False,
                },
                "branches": (True, True),
                "pr": (False, False, False),
                "history": {
                    "include_checks": False,
                    "include_mergeability": False,
                    "include_history_details": True,
                },
                "included": {
                    "issue_closure",
                    "git",
                    "pr_history",
                },
                "excluded": {
                    "comments",
                    "workspace_inventory",
                    "checks",
                },
            },
        ),
    ],
)
def test_authoritative_operation_resolver_queries_only_its_fact_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expected: dict[str, Any],
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    issue = _issue()
    contract = _task_contract(issue)
    pr = _open_pr(branch)
    pr["statusCheckRollup"] = [{"name": "quality", "conclusion": "SUCCESS"}]
    observations: dict[str, list[Any]] = {
        "issue": [],
        "relationships": [],
        "git": [],
        "branches": [],
        "pr": [],
        "history": [],
    }

    monkeypatch.setattr(lck_state, "_repository_slug", lambda *_args: "owner/repo")

    def issue_query(*args: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        observations["issue"].append(tuple(args[-3:]))
        return issue, contract

    def relationship_query(*_args: Any) -> dict[str, Any]:
        observations["relationships"].append(True)
        return _relationships()

    def git_query(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        if expected["git"] is None:
            pytest.fail(f"{operation} queried source workspace Git facts")
        if operation in {"Delivery Prepare", "closeout"} and (
            kwargs.get("include_workspace_inventory") is not False
        ):
            pytest.fail(
                f"{operation} queried forbidden staged/changed/worktree inventory"
            )
        observations["git"].append(kwargs)
        return _git_snapshot(FakeRunner(branch=branch))

    def task_branches(
        _self: Any,
        _task: int,
        _warnings: list[dict[str, Any]],
        *,
        include_local: bool = True,
        include_remote: bool = True,
    ) -> tuple[set[str], dict[str, str], bool]:
        observations["branches"].append((include_local, include_remote))
        return set(), {branch: SHA} if include_remote else {}, True

    def open_pr_query(*args: Any) -> dict[str, Any]:
        observations["pr"].append(tuple(args[-3:]))
        return pr

    def history_query(*_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if expected["history"] is None:
            pytest.fail(f"{operation} queried PR history")
        observations["history"].append(kwargs)
        return []

    monkeypatch.setattr(lck_state, "_issue_view_with_contract", issue_query)
    monkeypatch.setattr(lck_state, "_relationship_snapshot", relationship_query)
    monkeypatch.setattr(lck_state, "_git_snapshot", git_query)
    monkeypatch.setattr(lck_state.LiveStateResolver, "_task_branches", task_branches)
    monkeypatch.setattr(lck_state, "resolve_open_pr", open_pr_query)
    monkeypatch.setattr(lck_state, "list_matching_prs", history_query)

    snapshot = lck_state.OperationSnapshotBuilder(
        lck_state.LiveStateResolver(tmp_path, runner=cast(Any, FakeRunner()))
    ).acquire(159, operation=operation)

    assert snapshot.state.status is lck_models.ResolutionStatus.RESOLVED
    assert observations["issue"] == [expected["issue"]]
    assert observations["relationships"] == [True]
    assert observations["git"] == ([] if expected["git"] is None else [expected["git"]])
    assert observations["branches"] == [expected["branches"]]
    assert observations["pr"] == [expected["pr"]]
    assert observations["history"] == (
        [] if expected["history"] is None else [expected["history"]]
    )
    facts = set(snapshot.acquired_facts)
    assert expected["included"] <= facts
    assert expected["excluded"].isdisjoint(facts)


@pytest.mark.parametrize(
    ("operation", "included", "excluded"),
    [
        (
            "Delivery Prepare",
            {"task_contract", "git", "pr_history"},
            {"comments", "issue_closure", "workspace_inventory", "checks"},
        ),
        (
            "Delivery Complete",
            {"task_contract", "git", "checks", "pr_history"},
            {"comments", "issue_closure", "workspace_inventory"},
        ),
        (
            "review-prepare",
            {"task_contract", "remote_task_branches", "open_pr", "checks"},
            {"comments", "issue_closure", "git", "pr_history"},
        ),
        (
            "review-complete",
            {"task_contract", "remote_task_branches", "open_pr", "checks"},
            {"comments", "issue_closure", "git", "pr_history"},
        ),
        (
            "Remediation Prepare",
            {"task_contract", "git", "local_task_branches", "open_pr"},
            {"comments", "issue_closure", "checks", "pr_history"},
        ),
        (
            "Remediation No Change",
            {"git", "local_task_branches", "open_pr"},
            {"comments", "issue_closure", "checks", "pr_history"},
        ),
        (
            "Remediation Complete",
            {"task_contract", "git", "local_task_branches", "checks"},
            {"comments", "issue_closure", "workspace_inventory", "pr_history"},
        ),
        (
            "merge-preflight",
            {"task_contract", "remote_task_branches", "checks", "mergeability"},
            {"comments", "issue_closure", "git", "pr_history"},
        ),
        (
            "closeout",
            {"issue_closure", "git", "pr_history"},
            {"comments", "workspace_inventory", "checks"},
        ),
    ],
)
def test_authoritative_operations_bind_stable_fact_profiles(
    operation: str,
    included: set[str],
    excluded: set[str],
) -> None:
    facts = set(lck_models.fact_profile_for_operation(operation).facts())

    assert included <= facts
    assert excluded.isdisjoint(facts)


def test_delivery_history_query_requests_only_merged_detection_fields() -> None:
    fake = FakeRunner()

    assert (
        list_matching_prs(
            cast(Any, fake),
            "owner/repo",
            "task/159-lck-core-live-state-resolution",
            "main",
            [],
            include_checks=False,
            include_mergeability=False,
            include_history_details=False,
        )
        == []
    )

    command = fake.commands[-1]
    assert command[command.index("--json") + 1] == "number,state"
