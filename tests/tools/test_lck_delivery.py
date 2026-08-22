from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

import lck  # type: ignore[import-not-found]  # noqa: E402
from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
    CommandRunner,
)

SHA = "a" * 40


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    origin = tmp_path / "origin.git"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-m", "seed")
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo, origin


def _resolver_for_repo(repo: Path) -> lck.LiveStateResolver:
    return lck.LiveStateResolver(repo, runner=CommandRunner(repo), repository="owner/repo")


def test_commit_current_tree_binds_validated_tree_to_commit(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "seed.txt").write_text("changed\n", encoding="utf-8")
    effect = lck.CommitCurrentTreeEffect(_resolver_for_repo(repo))

    tree = effect.stage_candidate_tree()
    effect.verify_tree_unchanged(tree)
    receipt = effect.execute(tree, "Implement delivery cutover")

    assert receipt.action == "committed"
    assert _git(repo, "rev-parse", "HEAD^{tree}") == tree
    assert _git(repo, "status", "--porcelain") == ""


def test_commit_current_tree_rejects_tree_change_after_validation(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "seed.txt").write_text("candidate\n", encoding="utf-8")
    effect = lck.CommitCurrentTreeEffect(_resolver_for_repo(repo))
    tree = effect.stage_candidate_tree()
    (repo / "seed.txt").write_text("changed-after-validation\n", encoding="utf-8")

    with pytest.raises(lck.LckStopError, match="changed during formal validation"):
        effect.verify_tree_unchanged(tree)


def test_ensure_remote_branch_create_then_idempotent(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "task.txt").write_text("task\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "task")
    effect = lck.EnsureRemoteBranchEffect(_resolver_for_repo(repo))

    created = effect.execute("task/160-delivery")
    repeated = effect.execute("task/160-delivery")

    assert created.action == "created"
    assert repeated.action == "already-synced"
    assert created.details["head_sha"] == repeated.details["remote_oid"]


def test_ensure_remote_branch_fast_forwards_only_when_remote_is_ancestor(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "task.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "one")
    effect = lck.EnsureRemoteBranchEffect(_resolver_for_repo(repo))
    effect.execute("task/160-delivery")

    (repo / "task.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-am", "two")
    advanced = effect.execute("task/160-delivery")

    assert advanced.action == "fast-forwarded"


def test_ensure_remote_branch_stops_on_divergence(tmp_path: Path) -> None:
    repo, origin = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "task.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "one")
    effect = lck.EnsureRemoteBranchEffect(_resolver_for_repo(repo))
    effect.execute("task/160-delivery")
    shared = _git(repo, "rev-parse", "HEAD")

    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(origin), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.name", "Other")
    _git(other, "config", "user.email", "other@example.invalid")
    _git(other, "switch", "task/160-delivery")
    (other / "remote.txt").write_text("remote\n", encoding="utf-8")
    _git(other, "add", "remote.txt")
    _git(other, "commit", "-m", "remote")
    _git(other, "push", "origin", "task/160-delivery")

    _git(repo, "reset", "--hard", shared)
    (repo / "local.txt").write_text("local\n", encoding="utf-8")
    _git(repo, "add", "local.txt")
    _git(repo, "commit", "-m", "local")

    with pytest.raises(lck.LckStopError, match="ahead/diverged"):
        effect.execute("task/160-delivery")


class ValidationRunner:
    def __init__(self, *, status: str = "pass", returncode: int = 0) -> None:
        self.status = status
        self.returncode = returncode
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
        return CommandResult(
            command_id=command_id,
            argv=command,
            returncode=self.returncode,
            stdout=json.dumps({"status": self.status}),
            stderr="",
        )


def test_formal_validation_gate_requires_structured_pass(tmp_path: Path) -> None:
    runner = ValidationRunner()
    resolver = lck.LiveStateResolver(tmp_path, runner=cast(Any, runner), repository="owner/repo")

    payload = lck.FormalValidationGate(resolver).run(SHA)

    assert payload["status"] == "pass"
    assert "--phase" in runner.commands[0]
    assert "delivery" in runner.commands[0]


@pytest.mark.parametrize(("status", "returncode"), [("fail", 1), ("pass", 1)])
def test_formal_validation_gate_fails_closed(
    tmp_path: Path, status: str, returncode: int
) -> None:
    runner = ValidationRunner(status=status, returncode=returncode)
    resolver = lck.LiveStateResolver(tmp_path, runner=cast(Any, runner), repository="owner/repo")

    with pytest.raises(lck.LckStopError, match="formal Delivery validation failed"):
        lck.FormalValidationGate(resolver).run(SHA)


def _critical_snapshot() -> dict[str, Any]:
    return {
        "status": "valid",
        "contract": {
            "caller": "Delivery Skill",
            "capability": "LCK Delivery complete",
            "observable_result": "READY_FOR_REVIEW",
            "verification_test": "tests/test_critical_path.py::test_critical_path",
        },
    }


def _live_state(
    *,
    head: str,
    clean: bool,
    project_status: str,
    open_pr: dict[str, Any] | None,
    remote_oid: str | None,
) -> lck.LiveState:
    branch = "task/160-delivery-cutover"
    return lck.LiveState(
        task_number=160,
        repository="owner/repo",
        issue={
            "number": 160,
            "title": "[Task] 将 Delivery lifecycle control 迁移至 LCK",
            "state": "OPEN",
            "labels": {"items": ["type:task", "codex:ready"]},
            "project_status": project_status,
            "body_sha256": "e" * 64,
            "critical_outcome": _critical_snapshot(),
        },
        relationships={
            "available": True,
            "issue_type": "Task",
            "blocked_by": {"items": [], "count": 0, "truncated": False},
        },
        git={
            "branch": branch,
            "head_sha": head,
            "local_main_sha": SHA,
            "origin_main_sha": SHA,
            "origin_fetch": "pass",
            "clean": clean,
        },
        target_branch=branch,
        local_task_branch=branch,
        local_task_head=head,
        remote_task_branch=branch if remote_oid else None,
        remote_task_oid=remote_oid,
        open_pr=open_pr,
        merged_pr_numbers=(),
        merged=False,
        checks={"count": 0, "all_success": None},
        cleanup={},
    )


class CompletionRunner:
    def __init__(self, *, critical_returncode: int = 0) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.critical_returncode = critical_returncode

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command_id: str,
        **_: Any,
    ) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        if command[:3] == ("git", "diff", "--quiet"):
            returncode = 1
            stdout = ""
        elif command and Path(command[0]).name == "uv":
            returncode = self.critical_returncode
            stdout = "1 passed\n" if returncode == 0 else "1 failed\n"
        else:
            returncode = 0
            stdout = ""
        return CommandResult(command_id, command, returncode, stdout, "")


class SequenceResolver:
    def __init__(self, repo_root: Path, runner: CompletionRunner, states: list[lck.LiveState]) -> None:
        self.repo_root = repo_root
        self.runner = cast(Any, runner)
        self._states = states
        self.calls = 0

    def resolve(self, _task: int) -> lck.LiveState:
        index = min(self.calls, len(self._states) - 1)
        self.calls += 1
        return self._states[index]


class StubValidation:
    def run(self, _base_sha: str) -> dict[str, Any]:
        return {"status": "pass", "command_count": 6}


class StubChecks:
    def run(self, _task: int) -> dict[str, Any]:
        return {"status": "pass", "configuration": "available"}


class StubCommit:
    def __init__(self, *, dirty: bool) -> None:
        self.dirty = dirty
        self.calls: list[str] = []

    def current_head_tree(self) -> str:
        self.calls.append("current_head_tree")
        return "c" * 40

    def stage_candidate_tree(self) -> str:
        self.calls.append("stage_candidate_tree")
        return "c" * 40

    def verify_tree_unchanged(self, _tree: str) -> None:
        self.calls.append("verify_tree_unchanged")

    def execute(self, tree: str, _message: str) -> lck.EffectReceipt:
        self.calls.append("execute")
        return lck.EffectReceipt(
            "commit_current_tree",
            "committed",
            {"head_sha": "b" * 40, "tree_oid": tree},
        )


class StubEffect:
    def __init__(self, name: str, action: str) -> None:
        self.name = name
        self.action = action
        self.calls = 0

    def execute(self, *_args: Any, **_kwargs: Any) -> lck.EffectReceipt:
        self.calls += 1
        return lck.EffectReceipt(self.name, self.action, {})


def test_delivery_complete_revalidates_clean_committed_head_and_stops_at_review_boundary(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\n", encoding="utf-8")
    head = "b" * 40
    pre = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    final_pr = {
        "number": 10,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": head,
        "baseRefOid": SHA,
    }
    final = _live_state(
        head=head,
        clean=True,
        project_status="Review",
        open_pr=final_pr,
        remote_oid=head,
    )
    remote = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=head,
    )
    with_pr = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=final_pr,
        remote_oid=head,
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(
        tmp_path,
        runner,
        [pre, pre, remote, with_pr, with_pr, final],
    )
    commit = StubCommit(dirty=False)
    remote = StubEffect("ensure_remote_branch", "created")
    pr = StubEffect("ensure_open_pr", "created")
    status = StubEffect("set_review_status", "updated")

    result = lck.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, StubValidation()),
        commit_effect=cast(Any, commit),
        remote_effect=cast(Any, remote),
        pr_effect=cast(Any, pr),
        status_effect=cast(Any, status),
        checks_gate=cast(Any, StubChecks()),
    ).complete(
        160,
        commit_message="Implement LCK Delivery cutover",
        summary="Move initial Delivery mechanics into LCK.",
    )

    assert result.status == "READY_FOR_REVIEW"
    assert result.to_dict()["human_boundary"] == "Independent Review must be started separately"
    assert commit.calls == ["current_head_tree"]
    assert remote.calls == pr.calls == status.calls == 1
    assert not any("review" in " ".join(command).casefold() for command in runner.commands)


def test_task_160_critical_outcome_initial_delivery_is_lck_owned(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[2]
    agent_skill = (root / ".agents/skills/task-delivery-runner/SKILL.md").read_text(
        encoding="utf-8"
    )
    claude_skill = (root / ".claude/skills/task-delivery-runner/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert agent_skill == claude_skill
    assert "lck.py delivery prepare" in agent_skill
    assert "lck.py delivery complete" in agent_skill
    initial_section = agent_skill.split("## Review remediation", 1)[0]
    for direct_write in ("git commit", "git push", "gh pr create"):
        assert direct_write not in initial_section
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\n", encoding="utf-8")
    old_head = "d" * 40
    new_head = "b" * 40
    pre = _live_state(
        head=old_head,
        clean=False,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    final = _live_state(
        head=new_head,
        clean=True,
        project_status="Review",
        open_pr={
            "number": 10,
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": new_head,
            "baseRefOid": SHA,
        },
        remote_oid=new_head,
    )
    after_commit = _live_state(
        head=new_head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    after_remote = _live_state(
        head=new_head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=new_head,
    )
    with_pr = _live_state(
        head=new_head,
        clean=True,
        project_status="In Progress",
        open_pr={
            "number": 10,
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": new_head,
            "baseRefOid": SHA,
        },
        remote_oid=new_head,
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(
        tmp_path,
        runner,
        [pre, pre, after_commit, after_remote, with_pr, with_pr, final],
    )
    commit = StubCommit(dirty=True)

    result = lck.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, StubValidation()),
        commit_effect=cast(Any, commit),
        remote_effect=cast(Any, StubEffect("ensure_remote_branch", "created")),
        pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
        status_effect=cast(Any, StubEffect("set_review_status", "updated")),
        checks_gate=cast(Any, StubChecks()),
    ).complete(
        160,
        commit_message="Implement LCK Delivery cutover",
        summary="Move initial Delivery mechanics into LCK.",
    )

    assert result.status == "READY_FOR_REVIEW"
    assert commit.calls == [
        "stage_candidate_tree",
        "verify_tree_unchanged",
        "execute",
    ]


def _with_checks(state: lck.LiveState, checks: dict[str, Any]) -> lck.LiveState:
    return lck.LiveState(
        task_number=state.task_number,
        repository=state.repository,
        issue=state.issue,
        relationships=state.relationships,
        git=state.git,
        target_branch=state.target_branch,
        local_task_branch=state.local_task_branch,
        local_task_head=state.local_task_head,
        remote_task_branch=state.remote_task_branch,
        remote_task_oid=state.remote_task_oid,
        open_pr=state.open_pr,
        merged_pr_numbers=state.merged_pr_numbers,
        merged=state.merged,
        checks=checks,
        cleanup=state.cleanup,
        status=state.status,
        stop_reasons=state.stop_reasons,
        warnings=state.warnings,
    )


def _checks(*, category: str, state_name: str) -> dict[str, Any]:
    return {
        "count": 1,
        "success": 1 if category == "success" else 0,
        "pending": 1 if category == "pending" else 0,
        "failed": 1 if category == "failed" else 0,
        "skipped_or_unknown": 1 if category == "skipped-or-unknown" else 0,
        "all_success": category == "success",
        "items": {
            "items": [{"name": "quality", "state": state_name, "category": category}],
            "count": 1,
            "truncated": False,
        },
    }


def test_delivery_checks_gate_requires_named_required_check_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = "b" * 40
    base = _live_state(
        head=head,
        clean=True,
        project_status="Review",
        open_pr={
            "number": 10,
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": head,
            "baseRefOid": SHA,
        },
        remote_oid=head,
    )
    state = _with_checks(base, _checks(category="success", state_name="SUCCESS"))
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [state])
    monkeypatch.setattr(
        lck,
        "_required_checks",
        lambda *_args, **_kwargs: {
            "configuration": "available",
            "contexts": {"items": ["quality"], "count": 1, "truncated": False},
        },
    )

    result = lck.DeliveryChecksGate(cast(Any, resolver), timeout_seconds=0).run(160)

    assert result["status"] == "pass"
    assert result["required"] == ["quality"]


def test_delivery_checks_gate_stops_on_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = "b" * 40
    base = _live_state(
        head=head,
        clean=True,
        project_status="Review",
        open_pr={
            "number": 10,
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": head,
            "baseRefOid": SHA,
        },
        remote_oid=head,
    )
    state = _with_checks(base, _checks(category="failed", state_name="FAILURE"))
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [state])
    monkeypatch.setattr(
        lck,
        "_required_checks",
        lambda *_args, **_kwargs: {
            "configuration": "available",
            "contexts": {"items": ["quality"], "count": 1, "truncated": False},
        },
    )

    with pytest.raises(lck.LckStopError, match="checks failed"):
        lck.DeliveryChecksGate(cast(Any, resolver), timeout_seconds=0).run(160)


def test_delivery_checks_gate_preserves_plan_limit_as_limitation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = "b" * 40
    base = _live_state(
        head=head,
        clean=True,
        project_status="Review",
        open_pr={
            "number": 10,
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": head,
            "baseRefOid": SHA,
        },
        remote_oid=head,
    )
    state = _with_checks(base, _checks(category="success", state_name="SUCCESS"))
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [state])
    monkeypatch.setattr(
        lck,
        "_required_checks",
        lambda *_args, **_kwargs: {
            "configuration": "plan-limited-403",
            "contexts": {"items": [], "count": 0, "truncated": False},
        },
    )

    result = lck.DeliveryChecksGate(cast(Any, resolver), timeout_seconds=0).run(160)

    assert result["status"] == "pass"
    assert result["limitation"] == "required-check configuration unavailable"


class FailingChecks:
    def run(self, _task: int) -> dict[str, Any]:
        raise lck.LckStopError("checks failed")


def test_delivery_complete_critical_outcome_failure_blocks_commit_and_remote(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\
", encoding="utf-8")
    pre = _live_state(
        head="d" * 40,
        clean=False,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    runner = CompletionRunner(critical_returncode=1)
    resolver = SequenceResolver(tmp_path, runner, [pre])
    commit = StubCommit(dirty=True)
    remote = StubEffect("ensure_remote_branch", "created")

    with pytest.raises(lck.LckStopError, match="Critical Outcome FAIL"):
        lck.DeliveryCompleter(
            cast(Any, resolver),
            formal_validation=cast(Any, StubValidation()),
            commit_effect=cast(Any, commit),
            remote_effect=cast(Any, remote),
            pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
            status_effect=cast(Any, StubEffect("set_review_status", "updated")),
            checks_gate=cast(Any, StubChecks()),
        ).complete(
            160,
            commit_message="Implement LCK Delivery cutover",
            summary="Move initial Delivery mechanics into LCK.",
        )

    assert commit.calls == ["stage_candidate_tree"]
    assert remote.calls == 0


def test_delivery_complete_stops_before_commit_when_base_changes_during_validation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\
", encoding="utf-8")
    pre = _live_state(
        head="d" * 40,
        clean=False,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    changed = lck.LiveState(
        **{
            **pre.__dict__,
            "git": {**pre.git, "origin_main_sha": "f" * 40},
        }
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [pre, changed])
    commit = StubCommit(dirty=True)
    remote = StubEffect("ensure_remote_branch", "created")

    with pytest.raises(lck.LckStopError, match="origin/main changed"):
        lck.DeliveryCompleter(
            cast(Any, resolver),
            formal_validation=cast(Any, StubValidation()),
            commit_effect=cast(Any, commit),
            remote_effect=cast(Any, remote),
            pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
            status_effect=cast(Any, StubEffect("set_review_status", "updated")),
            checks_gate=cast(Any, StubChecks()),
        ).complete(
            160,
            commit_message="Implement LCK Delivery cutover",
            summary="Move initial Delivery mechanics into LCK.",
        )

    assert commit.calls == ["stage_candidate_tree"]
    assert remote.calls == 0


def test_delivery_complete_stops_before_commit_when_task_body_changes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\
", encoding="utf-8")
    pre = _live_state(
        head="d" * 40,
        clean=False,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    changed_issue = dict(pre.issue or {})
    changed_issue["body_sha256"] = "f" * 64
    changed = lck.LiveState(**{**pre.__dict__, "issue": changed_issue})
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [pre, changed])
    commit = StubCommit(dirty=True)

    with pytest.raises(lck.LckStopError, match="Task body changed"):
        lck.DeliveryCompleter(
            cast(Any, resolver),
            formal_validation=cast(Any, StubValidation()),
            commit_effect=cast(Any, commit),
            remote_effect=cast(Any, StubEffect("ensure_remote_branch", "created")),
            pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
            status_effect=cast(Any, StubEffect("set_review_status", "updated")),
            checks_gate=cast(Any, StubChecks()),
        ).complete(
            160,
            commit_message="Implement LCK Delivery cutover",
            summary="Move initial Delivery mechanics into LCK.",
        )

    assert commit.calls == ["stage_candidate_tree"]


def test_failed_checks_do_not_move_project_status_to_review(tmp_path: Path) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\
", encoding="utf-8")
    head = "b" * 40
    pre = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    remote = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=head,
    )
    pr = {
        "number": 10,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": head,
        "baseRefOid": SHA,
    }
    with_pr = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=pr,
        remote_oid=head,
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [pre, pre, remote, with_pr])
    status = StubEffect("set_review_status", "updated")

    with pytest.raises(lck.LckStopError, match="checks failed"):
        lck.DeliveryCompleter(
            cast(Any, resolver),
            formal_validation=cast(Any, StubValidation()),
            commit_effect=cast(Any, StubCommit(dirty=False)),
            remote_effect=cast(Any, StubEffect("ensure_remote_branch", "created")),
            pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
            status_effect=cast(Any, status),
            checks_gate=cast(Any, FailingChecks()),
        ).complete(
            160,
            commit_message="Implement LCK Delivery cutover",
            summary="Move initial Delivery mechanics into LCK.",
        )

    assert status.calls == 0
