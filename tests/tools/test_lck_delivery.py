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
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo, origin


def _resolver_for_repo(repo: Path) -> lck.LiveStateResolver:
    return lck.LiveStateResolver(
        repo, runner=CommandRunner(repo), repository="owner/repo"
    )


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


def test_commit_current_tree_rejects_tree_change_after_validation(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "seed.txt").write_text("candidate\n", encoding="utf-8")
    effect = lck.CommitCurrentTreeEffect(_resolver_for_repo(repo))
    tree = effect.stage_candidate_tree()
    (repo / "seed.txt").write_text("changed-after-validation\n", encoding="utf-8")

    with pytest.raises(lck.LckStopError, match="changed during formal validation"):
        effect.verify_tree_unchanged(tree)


def test_commit_current_tree_rejects_head_change_after_validation(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "seed.txt").write_text("candidate\n", encoding="utf-8")
    effect = lck.CommitCurrentTreeEffect(_resolver_for_repo(repo))
    original_head = _git(repo, "rev-parse", "HEAD")
    tree = effect.stage_candidate_tree()
    _git(repo, "commit", "-m", "concurrent")

    with pytest.raises(lck.LckStopError, match="HEAD changed during formal validation"):
        effect.verify_tree_unchanged(tree, expected_head_sha=original_head)


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


def test_ensure_remote_branch_repairs_missing_upstream_when_already_synced(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    branch = "task/160-delivery"
    _git(repo, "switch", "-c", branch)
    (repo / "task.txt").write_text("task\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "task")
    effect = lck.EnsureRemoteBranchEffect(_resolver_for_repo(repo))
    effect.execute(branch)
    _git(repo, "branch", "--unset-upstream")

    repaired = effect.execute(branch)

    assert repaired.action == "already-synced"
    assert repaired.details["upstream"] == f"origin/{branch}"
    assert (
        _git(
            repo,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        )
        == f"origin/{branch}"
    )


def test_ensure_remote_branch_rejects_unvalidated_local_head(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "task.txt").write_text("task\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "task")
    effect = lck.EnsureRemoteBranchEffect(_resolver_for_repo(repo))

    with pytest.raises(lck.LckStopError, match="validated local Task HEAD changed"):
        effect.execute("task/160-delivery", expected_head_sha="b" * 40)

    assert _git(repo, "ls-remote", "--heads", "origin", "task/160-delivery") == ""


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
    subprocess.run(
        ["git", "clone", str(origin), str(other)], check=True, capture_output=True
    )
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
    resolver = lck.LiveStateResolver(
        tmp_path, runner=cast(Any, runner), repository="owner/repo"
    )

    payload = lck.FormalValidationGate(resolver).run(SHA)

    assert payload["status"] == "pass"
    assert "--phase" in runner.commands[0]
    assert "delivery" in runner.commands[0]


@pytest.mark.parametrize(("status", "returncode"), [("fail", 1), ("pass", 1)])
def test_formal_validation_gate_fails_closed(
    tmp_path: Path, status: str, returncode: int
) -> None:
    runner = ValidationRunner(status=status, returncode=returncode)
    resolver = lck.LiveStateResolver(
        tmp_path, runner=cast(Any, runner), repository="owner/repo"
    )

    with pytest.raises(lck.LckStopError, match="formal Delivery validation failed"):
        lck.FormalValidationGate(resolver).run(SHA)


@pytest.mark.parametrize("mutation", ["merged", "wrong-branch"])
def test_ensure_open_pr_rechecks_live_task_identity_before_resolution(
    tmp_path: Path,
    mutation: str,
) -> None:
    head = "b" * 40
    state = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=head,
    )
    if mutation == "merged":
        state = lck.LiveState(**{**state.__dict__, "merged": True})
        expected = "already merged"
    else:
        state = lck.LiveState(
            **{
                **state.__dict__,
                "git": {**state.git, "branch": "main"},
            }
        )
        expected = "not selected"
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [state])

    with pytest.raises(lck.LckStopError, match=expected):
        lck.EnsureOpenPrEffect(cast(Any, resolver)).execute(
            160,
            summary="Move initial Delivery mechanics into LCK.",
            risks="None.",
            critical_outcome=_critical_snapshot(),
            validation={"status": "pass"},
            expected_base_sha=SHA,
            expected_body_sha256="e" * 64,
        )

    assert runner.commands == []


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
    def __init__(
        self, repo_root: Path, runner: CompletionRunner, states: list[lck.LiveState]
    ) -> None:
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
        return {
            "status": "pass",
            "configuration": "not-configured",
            "required": [],
            "observed": {
                "count": 0,
                "failed": 0,
                "pending": 0,
                "skipped_or_unknown": 0,
                "all_success": None,
            },
            "pr": {"number": 10, "head_sha": "b" * 40, "base_sha": SHA},
        }


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

    def verify_tree_unchanged(
        self, _tree: str, *, expected_head_sha: str | None = None
    ) -> None:
        self.calls.append("verify_tree_unchanged")

    def execute(
        self,
        tree: str,
        _message: str,
        *,
        expected_parent_sha: str | None = None,
    ) -> lck.EffectReceipt:
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


class CliDeliveryRunner:
    """Deterministic external boundary for the real LCK Delivery CLI path."""

    branch = "task/160-delivery-lifecycle-control-lck"
    base_sha = "a" * 40
    old_head = "d" * 40
    new_head = "b" * 40
    tree_oid = "c" * 40

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.command_ids: list[str] = []
        self.remote_oid: str | None = None
        self.upstream: str | None = None
        self.open_pr = False
        self.project_status = "In Progress"
        self.dirty = True

    def _result(
        self,
        command_id: str,
        command: tuple[str, ...],
        *,
        stdout: str = "",
        returncode: int = 0,
        stderr: str = "",
    ) -> CommandResult:
        return CommandResult(
            command_id=command_id,
            argv=command,
            returncode=returncode,
            stdout=f"{stdout}\n" if stdout else "",
            stderr=stderr,
        )

    def _issue_payload(self) -> dict[str, Any]:
        body = """### Critical Outcome
Caller: task-delivery-runner initial Delivery
Capability: LCK owns deterministic initial Delivery completion
Observable result: a semantically completed Task reaches READY_FOR_REVIEW
Verification test: tests/tools/test_lck_delivery.py::test_task_160_critical_outcome_initial_delivery_is_lck_owned
"""
        return {
            "number": 160,
            "title": "[Task] 将 Delivery lifecycle control 迁移至 LCK",
            "body": body,
            "comments": [],
            "state": "OPEN",
            "labels": [
                {"name": "type:task"},
                {"name": "codex:ready"},
            ],
            "projectItems": [{"status": {"name": self.project_status}}],
            "url": "https://github.com/owner/repo/issues/160",
            "closedAt": None,
            "closedByPullRequestsReferences": [],
        }

    def _pr_payload(self) -> dict[str, Any]:
        return {
            "number": 165,
            "url": "https://github.com/owner/repo/pull/165",
            "state": "OPEN",
            "isDraft": False,
            "baseRefName": "main",
            "baseRefOid": self.base_sha,
            "headRefName": self.branch,
            "headRefOid": self.new_head,
            "statusCheckRollup": [
                {"name": "quality", "conclusion": "SUCCESS"},
            ],
        }

    def _graphql_payload(self, command: tuple[str, ...]) -> str:
        query = command[4] if len(command) > 4 else ""
        if "closedByPullRequestsReferences" in query:
            issue: dict[str, Any] = {
                "state": "OPEN",
                "closedAt": None,
                "closedByPullRequestsReferences": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                },
                "timelineItems": {
                    "nodes": [],
                    "pageInfo": {"hasPreviousPage": False},
                },
            }
        else:
            issue = {
                "issueType": {"name": "Task"},
                "parent": None,
                "subIssues": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                },
                "blockedBy": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                },
                "blocking": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                },
            }
        return json.dumps({"data": {"repository": {"issue": issue}}})

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command_id: str,
        **_: Any,
    ) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        self.command_ids.append(command_id)
        args = list(command[1:]) if command and command[0] == "git" else list(command)

        if command and command[0] == "uv":
            return self._result(command_id, command, stdout="1 passed")
        if command_id == "lck-formal-delivery-validation":
            return self._result(
                command_id, command, stdout=json.dumps({"status": "pass"})
            )

        if args[:1] == ["fetch"]:
            return self._result(command_id, command)
        if args == ["branch", "--show-current"]:
            return self._result(command_id, command, stdout=self.branch)
        if args == ["rev-parse", "HEAD"]:
            return self._result(
                command_id,
                command,
                stdout=self.new_head if not self.dirty else self.old_head,
            )
        if args == ["rev-parse", "HEAD^"]:
            return self._result(command_id, command, stdout=self.old_head)
        if args == ["rev-parse", "HEAD^{tree}"]:
            return self._result(command_id, command, stdout=self.tree_oid)
        if args == ["rev-parse", "FETCH_HEAD"]:
            return self._result(command_id, command, stdout=self.remote_oid or "")
        if args == [
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ]:
            return self._result(command_id, command, stdout=self.upstream or "")
        if args == ["rev-parse", "refs/heads/main"]:
            return self._result(command_id, command, stdout=self.base_sha)
        if args == ["rev-parse", "refs/remotes/origin/main"]:
            return self._result(command_id, command, stdout=self.base_sha)
        if args[:1] == ["rev-parse"] and len(args) == 2:
            return self._result(
                command_id,
                command,
                stdout=self.new_head if not self.dirty else self.old_head,
            )
        if args[:2] == ["for-each-ref", "--format=%(refname:short)"]:
            return self._result(command_id, command, stdout=self.branch)
        if args[:3] == ["ls-remote", "--heads", "origin"]:
            if self.remote_oid is None:
                return self._result(command_id, command)
            return self._result(
                command_id,
                command,
                stdout=f"{self.remote_oid}\trefs/heads/{self.branch}",
            )
        if args[:2] == ["status", "--short"] or args[:2] == [
            "status",
            "--porcelain=v1",
        ]:
            return self._result(
                command_id, command, stdout=" M candidate.py" if self.dirty else ""
            )
        if args[:2] == ["diff", "--cached"] and "--name-only" in args:
            return self._result(command_id, command, stdout="candidate.py")
        if args[:2] == ["diff", "--cached"] and "--quiet" in args:
            return self._result(command_id, command, returncode=1)
        if args == ["diff", "--quiet"] or (
            len(args) == 3 and args[:2] == ["diff", "--quiet"]
        ):
            return self._result(command_id, command)
        if args == ["diff", "--cached", "--check"]:
            return self._result(command_id, command)
        if args == ["diff", "--name-only"]:
            return self._result(command_id, command, stdout="candidate.py")
        if args[:2] == ["branch", "--set-upstream-to"]:
            self.upstream = args[2]
            return self._result(command_id, command)
        if args == ["add", "-A", "--", ":/"]:
            return self._result(command_id, command)
        if args == ["write-tree"]:
            return self._result(command_id, command, stdout=self.tree_oid)
        if args[:2] == ["commit", "-m"]:
            self.dirty = False
            return self._result(command_id, command)
        if args[:1] == ["push"]:
            self.remote_oid = self.new_head
            if "-u" in args:
                self.upstream = f"origin/{self.branch}"
            return self._result(command_id, command)
        if args[:3] == ["merge-base", "--is-ancestor", self.remote_oid or ""]:
            return self._result(command_id, command)
        if args[:2] == ["worktree", "list"]:
            return self._result(
                command_id,
                command,
                stdout=f"worktree /tmp/test-repo\nbranch refs/heads/{self.branch}",
            )

        if command[:3] == ("gh", "issue", "view"):
            return self._result(
                command_id, command, stdout=json.dumps(self._issue_payload())
            )
        if command[:3] == ("gh", "api", "graphql"):
            return self._result(
                command_id, command, stdout=self._graphql_payload(command)
            )
        if command[:3] == ("gh", "pr", "list"):
            state_index = command.index("--state") + 1
            state = command[state_index]
            if not self.open_pr:
                return self._result(command_id, command, stdout="[]")
            if state == "merged":
                return self._result(command_id, command, stdout="[]")
            return self._result(
                command_id, command, stdout=json.dumps([self._pr_payload()])
            )
        if command[:3] == ("gh", "pr", "view"):
            return self._result(
                command_id, command, stdout=json.dumps(self._pr_payload())
            )
        if command[:3] == ("gh", "pr", "create"):
            self.open_pr = True
            return self._result(
                command_id, command, stdout="https://github.com/owner/repo/pull/165"
            )
        if command[:3] == ("gh", "project", "item-edit"):
            self.project_status = "Review"
            return self._result(command_id, command)
        if command[:2] == ("gh", "api"):
            return self._result(
                command_id, command, returncode=1, stderr="404 not configured"
            )

        return self._result(command_id, command)


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
    assert (
        result.to_dict()["human_boundary"]
        == "Independent Review must be started separately"
    )
    assert commit.calls == ["current_head_tree", "verify_tree_unchanged"]
    assert remote.calls == pr.calls == status.calls == 1
    assert not any(
        "review" in " ".join(command).casefold() for command in runner.commands
    )


def test_task_160_critical_outcome_initial_delivery_is_lck_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    target = tmp_path / "tests" / "tools" / "test_lck_delivery.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "def test_task_160_critical_outcome_initial_delivery_is_lck_owned(): pass\n",
        encoding="utf-8",
    )
    tool = tmp_path / "tools" / "agent_workflow" / "workflow_validation.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("# deterministic external validation boundary\n", encoding="utf-8")

    runner = CliDeliveryRunner()
    monkeypatch.setattr(lck, "CommandRunner", lambda _repo_root: runner)

    exit_code = lck.main(
        [
            "--repo-root",
            str(tmp_path),
            "--repository",
            "owner/repo",
            "delivery",
            "complete",
            "160",
            "--commit-message",
            "Implement LCK Delivery cutover",
            "--summary",
            "Move initial Delivery mechanics into LCK.",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0, output
    assert payload["status"] == "READY_FOR_REVIEW"
    assert payload["human_boundary"] == "Independent Review must be started separately"
    assert payload["final_state"]["issue"]["project_status"] == "Review"
    assert runner.remote_oid == runner.new_head
    assert runner.open_pr is True
    assert "lck-critical-outcome" in runner.command_ids
    assert "lck-formal-delivery-validation" in runner.command_ids
    assert "lck-commit-current-tree" in runner.command_ids
    assert "lck-push-task-branch" in runner.command_ids
    assert any(
        command[0] == "gh" and command[1:3] == ("pr", "create")
        for command in runner.commands
    )
    assert any(
        command[0] == "gh" and command[1:3] == ("project", "item-edit")
        for command in runner.commands
    )
    assert [receipt["effect"] for receipt in payload["effects"]] == [
        "commit_current_tree",
        "ensure_remote_branch",
        "ensure_open_pr",
        "set_review_status",
    ]


def test_lck_migration_matrix_records_activation_rollback_procedure() -> None:
    matrix = (
        Path(__file__).parents[2] / "docs" / "workflows" / "lck-v1-migration-matrix.md"
    ).read_text(encoding="utf-8")

    assert "## Mainline activation and rollback procedure" in matrix
    assert "independent Review" in matrix
    assert "required Squash Merge" in matrix
    assert "revert the candidate" in matrix
    assert "last reviewed/merged LCK v1 state" in matrix
    assert "No Legacy Task control path is" in matrix
    assert "pre-cutover Current Workflow remains the authority" not in matrix
    assert "fresh maintainer merge" in matrix
    assert "decision; no Agent or Skill" in matrix


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
    target.write_text(
        "def test_critical_path(): pass\
",
        encoding="utf-8",
    )
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
    target.write_text(
        "def test_critical_path(): pass\
",
        encoding="utf-8",
    )
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
    target.write_text(
        "def test_critical_path(): pass\
",
        encoding="utf-8",
    )
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
    target.write_text(
        "def test_critical_path(): pass\
",
        encoding="utf-8",
    )
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


@pytest.mark.parametrize(
    ("category", "state_name"),
    [
        ("failed", "FAILURE"),
        ("pending", "IN_PROGRESS"),
        ("skipped-or-unknown", "CANCELLED"),
    ],
)
def test_final_verification_stops_when_checks_regress_after_gate(
    tmp_path: Path,
    category: str,
    state_name: str,
) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "def test_critical_path(): pass\n",
        encoding="utf-8",
    )
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
    restored = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=pr,
        remote_oid=head,
    )
    failed_final = _with_checks(
        _live_state(
            head=head,
            clean=True,
            project_status="Review",
            open_pr={**pr},
            remote_oid=head,
        ),
        _checks(category=category, state_name=state_name),
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(
        tmp_path,
        runner,
        [pre, pre, remote, with_pr, with_pr, with_pr, failed_final, restored],
    )
    status = lck.SetReviewStatusEffect(cast(Any, resolver))

    with pytest.raises(lck.LckStopError, match="check/lifecycle state is not aligned"):
        lck.DeliveryCompleter(
            cast(Any, resolver),
            formal_validation=cast(Any, StubValidation()),
            commit_effect=cast(Any, StubCommit(dirty=False)),
            remote_effect=cast(Any, StubEffect("ensure_remote_branch", "created")),
            pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
            status_effect=cast(Any, status),
            checks_gate=cast(Any, StubChecks()),
        ).complete(
            160,
            commit_message="Implement LCK Delivery cutover",
            summary="Move initial Delivery mechanics into LCK.",
        )

    project_status_values = [
        command[command.index("--value") + 1]
        for command in runner.commands
        if command[:3] == ("gh", "project", "item-edit")
    ]
    assert project_status_values == ["Review", "In Progress"]


def test_final_verification_requires_same_pr_as_checks_gate(tmp_path: Path) -> None:
    head = "b" * 40
    final_state = _live_state(
        head=head,
        clean=True,
        project_status="Review",
        open_pr={
            "number": 11,
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": head,
            "baseRefOid": SHA,
        },
        remote_oid=head,
    )
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [final_state])

    with pytest.raises(lck.LckStopError, match="check/lifecycle state is not aligned"):
        lck.DeliveryCompleter(cast(Any, resolver))._final_verify(
            final_state,
            head,
            base_sha=SHA,
            body_sha256="e" * 64,
            branch="task/160-delivery-cutover",
            checks_result={
                "status": "pass",
                "configuration": "not-configured",
                "required": [],
                "pr": {"number": 10, "head_sha": head, "base_sha": SHA},
            },
        )


def test_set_review_status_requires_checks_gated_pr_identity(
    tmp_path: Path,
) -> None:
    head = "b" * 40
    current_pr = {
        "number": 11,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": head,
        "baseRefOid": SHA,
    }
    state = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=current_pr,
        remote_oid=head,
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [state])

    with pytest.raises(lck.LckStopError, match="checks-gated PR identity changed"):
        lck.SetReviewStatusEffect(cast(Any, resolver)).execute(
            160,
            expected_pr={"number": 10, "head_sha": head, "base_sha": SHA},
        )

    assert not any(command[:2] == ("gh", "project") for command in runner.commands)


def test_set_review_status_rejects_regressed_checks_before_mutation(
    tmp_path: Path,
) -> None:
    head = "b" * 40
    current_pr = {
        "number": 10,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": head,
        "baseRefOid": SHA,
    }
    state = _with_checks(
        _live_state(
            head=head,
            clean=True,
            project_status="In Progress",
            open_pr=current_pr,
            remote_oid=head,
        ),
        _checks(category="failed", state_name="FAILURE"),
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [state])
    checks_result = {
        "status": "pass",
        "configuration": "not-configured",
        "required": [],
        "pr": {"number": 10, "head_sha": head, "base_sha": SHA},
    }

    with pytest.raises(lck.LckStopError, match="PR checks are no longer passing"):
        lck.SetReviewStatusEffect(cast(Any, resolver)).execute(
            160,
            expected_pr=checks_result["pr"],
            checks_result=checks_result,
        )

    assert not any(command[:2] == ("gh", "project") for command in runner.commands)
