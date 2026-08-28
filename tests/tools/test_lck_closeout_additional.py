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
    closeout as lck_closeout,
    eligibility as lck_eligibility,
    models as lck_models,
)
from pr_resolve import list_matching_prs  # type: ignore[import-not-found]  # noqa: E402
from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
)
from lck_test_support import (  # noqa: E402
    FakeRunner,
    StaticResolver,
    SHA,
    _install_facts,
    _issue,
    _open_pr,
    _relationships,
    _resolver,
    _review_state,
)


@pytest.mark.parametrize(
    ("phase", "project_status", "open_pr"),
    [
        (lck_models.Phase.DELIVERY_PREPARE, "Ready", None),
        (lck_models.Phase.REVIEW_PREPARE, "Review", "review"),
        (lck_models.Phase.REMEDIATION_PREPARE, "Review", "remediation"),
    ],
)
def test_all_non_closeout_phases_require_task_type(
    monkeypatch: pytest.MonkeyPatch,
    phase: lck_models.Phase,
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
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(state, phase)

    assert not decision.eligible
    assert any("type:task" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    ("phase", "project_status", "open_pr"),
    [
        (lck_models.Phase.DELIVERY_PREPARE, "Ready", None),
        (lck_models.Phase.REVIEW_PREPARE, "Review", "review"),
        (lck_models.Phase.REMEDIATION_PREPARE, "Review", "remediation"),
    ],
)
def test_all_non_closeout_phases_reject_lifecycle_label_conflict(
    monkeypatch: pytest.MonkeyPatch,
    phase: lck_models.Phase,
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
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(state, phase)

    assert not decision.eligible
    assert any("lifecycle labels" in reason for reason in decision.reasons)


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
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state, lck_models.Phase.CLOSEOUT
    )

    assert state.merged is True
    assert decision.eligible


def test_closeout_cleanup_keeps_exact_worktree_safety_precondition(
    tmp_path: Path,
) -> None:
    state = _review_state()

    class WorktreeInUseRunner:
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
            assert command_id == "lck-closeout-worktree-precondition"
            assert command == ("git", "worktree", "list", "--porcelain")
            return CommandResult(
                command_id,
                command,
                0,
                f"worktree /tmp/task\nbranch refs/heads/{state.target_branch}\n",
                "",
            )

    resolver = cast(Any, StaticResolver(tmp_path, state))
    runner = WorktreeInUseRunner()
    resolver.runner = runner

    receipt = lck_closeout.CleanupTaskRefsEffect(resolver).execute(
        state,
        expected_head_sha=SHA,
        merge_sha="b" * 40,
    )

    assert receipt.action == "pending"
    assert (
        receipt.details["reason"] == "verified Task branch is still used by a worktree"
    )
    assert runner.commands == [("git", "worktree", "list", "--porcelain")]


def test_closeout_history_query_retains_required_identity_details() -> None:
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
            include_history_details=True,
        )
        == []
    )

    command = fake.commands[-1]
    fields = set(command[command.index("--json") + 1].split(","))
    assert {
        "number",
        "state",
        "baseRefName",
        "baseRefOid",
        "headRefName",
        "headRefOid",
        "mergeCommit",
        "mergedAt",
        "closingIssuesReferences",
    } <= fields
    assert {"statusCheckRollup", "mergeable"}.isdisjoint(fields)
