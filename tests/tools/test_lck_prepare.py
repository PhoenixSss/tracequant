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
from pr_resolve import resolve_open_pr  # type: ignore[import-not-found]  # noqa: E402
from lck_test_support import (  # noqa: E402
    FakeRunner,
    SHA,
    _git_snapshot,
    _install_facts,
    _issue,
    _open_pr,
    _relationships,
    _resolver,
)


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
    fake = FakeRunner(branch="main")
    _install_facts(monkeypatch, fake)
    preparer = lck_delivery.DeliveryPreparer(_resolver(fake))

    created = preparer.prepare(159)
    reused = preparer.prepare(159)

    assert created.action == "created-from-main"
    assert reused.action == "already-prepared"
    assert created.to_dict()["task_contract"]["body"] == "Task Contract"
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
    fake = FakeRunner(branch="main", remote_branches={branch: SHA})
    _install_facts(monkeypatch, fake)

    context = lck_delivery.DeliveryPreparer(_resolver(fake)).prepare(159)

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
