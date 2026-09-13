# ruff: noqa: E402, I001

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import (
    Any,
    cast,
)

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[3] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from tools.lck import (  # type: ignore[import-not-found]  # noqa: E402
    delivery as lck_delivery,
    effects as lck_effects,
    eligibility as lck_eligibility,
    models as lck_models,
    state as lck_state,
)
from tools.lck.github_prs import resolve_open_pr  # type: ignore[import-not-found]  # noqa: E402
from tools.lck.common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
    WorkflowToolError,
)
from .support import (  # noqa: E402
    FakeRunner,
    SHA,
    _git_snapshot,
    _install_facts,
    _issue,
    _open_pr,
    _relationships,
    _resolver,
)


class ProjectStatusRunner(FakeRunner):
    def __init__(self, *, project_status: str = "Ready", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.project_status = project_status

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command_id: str,
        **kwargs: Any,
    ) -> CommandResult:
        command = tuple(str(item) for item in argv)
        if command[:3] == ("gh", "issue", "view") and command[-1] == "projectItems":
            self.commands.append(command)
            return CommandResult(
                command_id=command_id,
                argv=command,
                returncode=0,
                stdout=json.dumps(
                    {"projectItems": [{"status": {"name": self.project_status}}]}
                ),
                stderr="",
            )
        return super().run(argv, command_id=command_id, **kwargs)


def _install_project_status_write(
    monkeypatch: pytest.MonkeyPatch,
    fake: ProjectStatusRunner,
    issue: dict[str, Any],
    *,
    update_status: bool = True,
    fail: bool = False,
) -> list[str]:
    writes: list[str] = []

    def write(
        runner: Any,
        repository: str,
        task: int,
        *,
        value: str,
        **_kwargs: Any,
    ) -> None:
        assert runner is fake
        assert repository == "owner/repo"
        assert task == 159
        writes.append(value)
        if fail:
            raise WorkflowToolError("simulated status write failure")
        if update_status:
            fake.project_status = value
            issue["project_status"] = value

    monkeypatch.setattr(lck_effects, "set_project_status_with_runner", write)
    return writes


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

    monkeypatch.setattr(lck_state, "_git_snapshot", live_snapshot)
    _resolver(fake).resolve(159)

    assert observed == {"read_only_local_refs": True}


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

    with pytest.raises(lck_models.LckStopError, match="formal blocker gate") as error:
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert expected_detail in str(error.value)
    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


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

    with pytest.raises(lck_models.LckStopError, match="type:task"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_lifecycle_label_conflict_stops_before_workspace_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    issue = _issue()
    issue["labels"] = {"items": ["type:task", "codex:ready", "codex:needs-spec"]}
    _install_facts(monkeypatch, fake, issue=issue)

    with pytest.raises(lck_models.LckStopError, match="lifecycle labels"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

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

    assert state.status is lck_models.ResolutionStatus.STOP
    assert any(
        "multiple Task branch candidates" in reason for reason in state.stop_reasons
    )


def test_delivery_prepare_creates_then_reuses_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ProjectStatusRunner(branch="main")
    issue = _issue()
    writes = _install_project_status_write(monkeypatch, fake, issue)
    _install_facts(monkeypatch, fake, issue=issue)
    preparer = lck_delivery.DeliveryPreparer(_resolver(fake))

    created = preparer.prepare(159)
    reused = preparer.prepare(159)

    assert created.action == "created-from-main"
    assert reused.action == "already-prepared"
    assert created.to_dict()["task_contract"]["body"] == "Task Contract"
    assert created.effects[0].to_dict() == {
        "effect": "set_in_progress_status",
        "action": "updated",
        "details": {
            "previous_status": "Ready",
            "status": "In Progress",
            "postcondition": "verified",
        },
    }
    assert reused.effects[0].action == "already-in-progress"
    assert writes == ["In Progress"]
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
        remote_main_sha="b" * 40,
    )
    _install_facts(monkeypatch, fake)

    with pytest.raises(
        lck_models.LckStopError, match="HEAD == local main == origin/main"
    ):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == "main"
    assert not any(command[:3] == ("git", "switch", "-c") for command in fake.commands)


def test_delivery_prepare_restores_remote_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = ProjectStatusRunner(branch="main", remote_branches={branch: SHA})
    issue = _issue()
    writes = _install_project_status_write(monkeypatch, fake, issue)
    _install_facts(monkeypatch, fake, issue=issue)

    context = lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert context.action == "restored-from-remote"
    assert context.effects[0].action == "updated"
    assert writes == ["In Progress"]
    assert fake.branch == branch
    assert branch in fake.local_branches


def test_delivery_prepare_stops_before_status_write_when_workspace_postcondition_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ProjectStatusRunner(branch="main")
    issue = _issue()
    writes = _install_project_status_write(monkeypatch, fake, issue)
    _install_facts(monkeypatch, fake, issue=issue)
    preparer = lck_delivery.DeliveryPreparer(_resolver(fake))

    def fail_postcondition(_branch: str, _expected_head: str | None) -> None:
        raise lck_models.LckStopError("simulated workspace postcondition failure")

    monkeypatch.setattr(preparer, "_verify_workspace", fail_postcondition)

    with pytest.raises(
        lck_models.LckStopError, match="workspace postcondition failure"
    ):
        preparer.prepare(159)

    assert writes == []
    assert not any(command[:3] == ("gh", "issue", "view") for command in fake.commands)


def test_delivery_prepare_status_write_failure_stops_after_reusable_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ProjectStatusRunner(branch="main")
    issue = _issue()
    writes = _install_project_status_write(monkeypatch, fake, issue, fail=True)
    _install_facts(monkeypatch, fake, issue=issue)

    with pytest.raises(lck_models.LckStopError, match="status write failure"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert writes == ["In Progress"]
    assert fake.branch == "task/159-lck-core-live-state-resolution"
    assert fake.project_status == "Ready"


def test_delivery_prepare_status_postcondition_failure_stops_and_reuses_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ProjectStatusRunner(branch="main")
    issue = _issue()
    writes = _install_project_status_write(
        monkeypatch, fake, issue, update_status=False
    )
    _install_facts(monkeypatch, fake, issue=issue)

    with pytest.raises(lck_models.LckStopError, match="postcondition failed"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    branch = "task/159-lck-core-live-state-resolution"
    assert writes == ["In Progress"]
    assert fake.branch == branch
    assert branch in fake.local_branches

    fake.project_status = "In Progress"
    issue["project_status"] = "In Progress"
    recovered = lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert recovered.action == "already-prepared"
    assert recovered.effects[0].action == "already-in-progress"
    assert writes == ["In Progress"]


def test_delivery_complete_ready_requires_delivery_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner()
    _install_facts(monkeypatch, fake)
    state = _resolver(fake).resolve(159)

    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state, lck_models.Phase.DELIVERY_COMPLETE
    )

    assert decision.eligible is False
    assert (
        "Delivery Complete requires Project Status In Progress; "
        "run Delivery Prepare first"
    ) in decision.reasons
    assert "Project Status is unavailable or unknown" not in decision.reasons


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

    with pytest.raises(lck_models.LckStopError, match="dirty unrelated worktree"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)


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

    with pytest.raises(lck_models.LckStopError, match="clean worktree"):
        lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

    assert fake.branch == branch
    assert not any(command[:2] == ("git", "switch") for command in fake.commands)


def test_delivery_prepare_requires_valid_critical_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner()
    issue = _issue()
    issue["critical_outcome"] = {"status": "invalid", "detail": "missing"}
    _install_facts(monkeypatch, fake, issue=issue)
    state = _resolver(fake).resolve(159)

    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state, lck_models.Phase.DELIVERY_PREPARE
    )

    assert decision.eligible is False
    assert any(
        "Critical Outcome contract invalid" in reason for reason in decision.reasons
    )
