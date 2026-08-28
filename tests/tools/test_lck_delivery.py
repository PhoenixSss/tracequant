# ruff: noqa: E402

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    cli as lck_cli,
)
from lck_core import (
    delivery as lck_delivery,
)
from lck_core import (
    effects as lck_effects,
)
from lck_core import (
    models as lck_models,
)
from lck_core import (
    receipts as lck_receipts,
)
from lck_core import (
    review_workspace as lck_review_workspace,
)
from lck_core import (
    state as lck_state,
)
from lck_core import (
    validation as lck_validation,
)
from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
    CommandRunner,
    sha256_json,
)

SHA = "a" * 40


def _without_route_contract(text: str) -> str:
    """Remove the Codex-only `## Execution route contract` section.

    The sandbox route (sandbox-first / elevated-first) is a Codex
    execution-profile concept; Claude Code permissions come from
    `.claude/settings.json`, so the Claude Skills deliberately omit it.
    """
    marker = "## Execution route contract"
    start = text.find(marker)
    assert start != -1
    ends = [
        index
        for probe in ("\n## ", "\nIt must contain")
        if (index := text.find(probe, start)) != -1
    ]
    assert ends
    return text[:start] + text[min(ends) + 1 :]


REQUIRED_CHECKS_WORKFLOW_TEXT = """name: CI
on:
  pull_request:
    branches: [main]
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""


def _write_required_checks_workflow(root: Path) -> None:
    path = root / ".github" / "workflows" / "ci.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(REQUIRED_CHECKS_WORKFLOW_TEXT, encoding="utf-8")


@pytest.fixture(autouse=True)
def _repository_required_checks_contract(tmp_path: Path) -> None:
    _write_required_checks_workflow(tmp_path)


def _required_policy(*names: str, source_sha: str = SHA) -> dict[str, Any]:
    items = list(names)
    return {
        "configuration": "repository-base-ci",
        "source": f"git:{source_sha}:.github/workflows/ci.yml:jobs",
        "source_sha": source_sha,
        "workflow_path": ".github/workflows/ci.yml",
        "contract_sha256": sha256_json(
            {"workflow": ".github/workflows/ci.yml", "required-checks": items}
        ),
        "contexts": {"items": items, "count": len(items), "truncated": False},
    }


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
    _write_required_checks_workflow(repo)
    _git(repo, "add", "seed.txt", ".github/workflows/ci.yml")
    _git(repo, "commit", "-m", "seed")
    subprocess.run(
        ["git", "init", "--bare", str(origin)], check=True, capture_output=True
    )
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo, origin


def _resolver_for_repo(repo: Path) -> lck_state.LiveStateResolver:
    return lck_state.LiveStateResolver(
        repo, runner=CommandRunner(repo), repository="owner/repo"
    )


def test_commit_current_tree_binds_validated_tree_to_commit(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "seed.txt").write_text("changed\n", encoding="utf-8")
    effect = lck_effects.CommitCurrentTreeEffect(_resolver_for_repo(repo))

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
    effect = lck_effects.CommitCurrentTreeEffect(_resolver_for_repo(repo))
    tree = effect.stage_candidate_tree()
    (repo / "seed.txt").write_text("changed-after-validation\n", encoding="utf-8")

    with pytest.raises(
        lck_models.LckStopError, match="changed during formal validation"
    ):
        effect.verify_tree_unchanged(tree)


def test_commit_current_tree_rejects_head_change_after_validation(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "seed.txt").write_text("candidate\n", encoding="utf-8")
    effect = lck_effects.CommitCurrentTreeEffect(_resolver_for_repo(repo))
    original_head = _git(repo, "rev-parse", "HEAD")
    tree = effect.stage_candidate_tree()
    _git(repo, "commit", "-m", "concurrent")

    with pytest.raises(
        lck_models.LckStopError, match="HEAD changed during formal validation"
    ):
        effect.verify_tree_unchanged(tree, expected_head_sha=original_head)


def test_ensure_remote_branch_create_then_idempotent(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "switch", "-c", "task/160-delivery")
    (repo / "task.txt").write_text("task\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "task")
    effect = lck_effects.EnsureRemoteBranchEffect(_resolver_for_repo(repo))

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
    effect = lck_effects.EnsureRemoteBranchEffect(_resolver_for_repo(repo))
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
    effect = lck_effects.EnsureRemoteBranchEffect(_resolver_for_repo(repo))

    with pytest.raises(
        lck_models.LckStopError, match="validated local Task HEAD changed"
    ):
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
    effect = lck_effects.EnsureRemoteBranchEffect(_resolver_for_repo(repo))
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
    effect = lck_effects.EnsureRemoteBranchEffect(_resolver_for_repo(repo))
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

    with pytest.raises(lck_models.LckStopError, match="REMOTE_BRANCH_DIVERGED"):
        effect.execute("task/160-delivery")


class RecordingGitRunner:
    def __init__(
        self,
        repo: Path,
        *,
        fail_fetch: bool = False,
        force_missing_object: bool = False,
    ) -> None:
        self.delegate = CommandRunner(repo)
        self.commands: list[tuple[str, ...]] = []
        self.fail_fetch = fail_fetch
        self.force_missing_object = force_missing_object

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command_id: str,
        **kwargs: Any,
    ) -> CommandResult:
        command = tuple(str(item) for item in argv)
        self.commands.append(command)
        if self.force_missing_object and command[:3] == ("git", "cat-file", "-e"):
            return CommandResult(
                command_id=command_id,
                argv=command,
                returncode=1,
                stdout="",
                stderr="object unavailable",
            )
        if self.fail_fetch and command[:3] == ("git", "fetch", "--no-tags"):
            return CommandResult(
                command_id=command_id,
                argv=command,
                returncode=23,
                stdout="",
                stderr="fetch failed\n" + ("x" * 4000),
            )
        return self.delegate.run(argv, command_id=command_id, **kwargs)


def test_ensure_remote_branch_uses_local_exact_remote_oid_without_fetch(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    branch = "task/160-delivery"
    _git(repo, "switch", "-c", branch)
    (repo / "task.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "one")
    _git(repo, "push", "-u", "origin", branch)
    (repo / "task.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-am", "two")
    runner = RecordingGitRunner(repo)
    resolver = lck_state.LiveStateResolver(repo, runner=runner, repository="owner/repo")

    receipt = lck_effects.EnsureRemoteBranchEffect(resolver).execute(branch)

    assert receipt.action == "fast-forwarded"
    assert not any(command[:2] == ("git", "fetch") for command in runner.commands)


def test_ensure_remote_branch_fetches_only_when_exact_remote_oid_is_missing(
    tmp_path: Path,
) -> None:
    repo, origin = _repo(tmp_path)
    branch = "task/160-delivery"
    _git(repo, "switch", "-c", branch)
    (repo / "task.txt").write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "candidate")

    other = tmp_path / "other-missing-object"
    subprocess.run(
        ["git", "clone", str(origin), str(other)], check=True, capture_output=True
    )
    _git(other, "config", "user.name", "Other")
    _git(other, "config", "user.email", "other@example.invalid")
    _git(other, "switch", "-c", branch, "origin/main")
    _git(other, "push", "-u", "origin", branch)

    runner = RecordingGitRunner(repo, force_missing_object=True)
    resolver = lck_state.LiveStateResolver(repo, runner=runner, repository="owner/repo")
    receipt = lck_effects.EnsureRemoteBranchEffect(resolver).execute(branch)

    assert receipt.action == "fast-forwarded"
    assert any(command[:2] == ("git", "fetch") for command in runner.commands)


def test_ensure_remote_branch_fetch_failure_has_bounded_classified_diagnostic(
    tmp_path: Path,
) -> None:
    repo, origin = _repo(tmp_path)
    branch = "task/160-delivery"
    _git(repo, "switch", "-c", branch)
    (repo / "task.txt").write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", "task.txt")
    _git(repo, "commit", "-m", "candidate")

    other = tmp_path / "other-fetch-failure"
    subprocess.run(
        ["git", "clone", str(origin), str(other)], check=True, capture_output=True
    )
    _git(other, "config", "user.name", "Other")
    _git(other, "config", "user.email", "other@example.invalid")
    _git(other, "switch", "-c", branch, "origin/main")
    _git(other, "push", "-u", "origin", branch)

    runner = RecordingGitRunner(repo, fail_fetch=True, force_missing_object=True)
    resolver = lck_state.LiveStateResolver(repo, runner=runner, repository="owner/repo")
    with pytest.raises(
        lck_models.LckStopError, match="REMOTE_HEAD_FETCH_FAILED"
    ) as error:
        lck_effects.EnsureRemoteBranchEffect(resolver).execute(branch)

    diagnostic = str(error.value)
    assert "local_object_available=false" in diagnostic
    assert "git_exit_code=23" in diagnostic
    assert len(diagnostic) < 1800
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


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
    resolver = lck_state.LiveStateResolver(
        tmp_path, runner=cast(Any, runner), repository="owner/repo"
    )

    payload = lck_validation.FormalValidationGate(resolver).run(SHA)

    assert payload["status"] == "pass"
    assert "--phase" in runner.commands[0]
    assert "delivery" in runner.commands[0]


@pytest.mark.parametrize(("status", "returncode"), [("fail", 1), ("pass", 1)])
def test_formal_validation_gate_fails_closed(
    tmp_path: Path, status: str, returncode: int
) -> None:
    runner = ValidationRunner(status=status, returncode=returncode)
    resolver = lck_state.LiveStateResolver(
        tmp_path, runner=cast(Any, runner), repository="owner/repo"
    )
    gate = lck_validation.FormalValidationGate(resolver)

    with pytest.raises(
        lck_models.LckStopError, match="formal Delivery validation failed"
    ):
        gate.run(SHA)

    assert gate.last_payload == {"status": status}


@pytest.mark.parametrize("mutation", ["merged", "invalid-target"])
def test_ensure_open_pr_uses_operation_snapshot_preconditions(
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
        state = lck_models.LiveState(**{**state.__dict__, "merged": True})
        expected = "already merged"
    else:
        state = lck_models.LiveState(**{**state.__dict__, "target_branch": "main"})
        expected = "Task branch is invalid"
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [state])

    with pytest.raises(lck_models.LckStopError, match=expected):
        lck_effects.EnsureOpenPrEffect(cast(Any, resolver)).execute(
            state,
            head_sha=head,
            summary="Move initial Delivery mechanics into LCK.",
            risks="None.",
            critical_outcome=_critical_snapshot(),
            validation={"status": "pass"},
            expected_base_sha=SHA,
            expected_body_sha256="e" * 64,
        )

    assert resolver.calls == 0
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
) -> lck_models.LiveState:
    branch = "task/160-delivery-cutover"
    return lck_models.LiveState(
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
            "remote_main_sha": SHA,
            "remote_main_query": "pass",
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
        task_contract={
            "number": 160,
            "title": "[Task] 将 Delivery lifecycle control 迁移至 LCK",
            "url": "https://github.com/owner/repo/issues/160",
            "body": "Task Contract",
            "body_sha256": "e" * 64,
            "critical_outcome": _critical_snapshot(),
        },
    )


def _snapshot(
    state: lck_models.LiveState,
    *,
    operation: str = lck_models.Phase.DELIVERY_COMPLETE.value,
    required: dict[str, Any] | None = None,
) -> lck_models.OperationSnapshot:
    return lck_models.OperationSnapshot(
        operation=operation,
        state=state,
        required_checks=required or _required_policy("quality"),
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
        elif command[:2] == ("git", "branch") and command[2:] == ("--show-current",):
            returncode = 0
            stdout = "task/160-delivery-cutover\n"
        elif command[:3] == ("git", "rev-parse", "HEAD"):
            returncode = 0
            stdout = "b" * 40 + "\n"
        elif command[:2] == ("git", "status"):
            returncode = 0
            stdout = ""
        elif command[:2] == ("git", "show") and command[2].endswith(
            ":.github/workflows/ci.yml"
        ):
            returncode = 0
            stdout = REQUIRED_CHECKS_WORKFLOW_TEXT
        elif command[:2] == ("gh", "api") and "required_status_checks" in command[2]:
            returncode = 0
            stdout = json.dumps({"contexts": []})
        elif command and Path(command[0]).name == "uv":
            returncode = self.critical_returncode
            stdout = "1 passed\n" if returncode == 0 else "1 failed\n"
        else:
            returncode = 0
            stdout = ""
        return CommandResult(command_id, command, returncode, stdout, "")


class SequenceResolver:
    def __init__(
        self,
        repo_root: Path,
        runner: CompletionRunner,
        states: list[lck_models.LiveState],
    ) -> None:
        self.repo_root = repo_root
        self.runner = cast(Any, runner)
        self._states = states
        self.calls = 0

    def resolve(self, _task: int) -> lck_models.LiveState:
        index = min(self.calls, len(self._states) - 1)
        self.calls += 1
        return self._states[index]


class StubValidation:
    def run(self, _base_sha: str) -> dict[str, Any]:
        return {"status": "pass", "command_count": 6}


class StubChecks:
    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.calls = 0
        self.last_result: dict[str, Any] | None = None

    def _result(self, number: int, head: str, base: str) -> dict[str, Any]:
        if self.fail:
            raise lck_models.LckStopError(self.fail)
        result = {
            "status": "pass",
            "configuration": "repository",
            "required": ["quality"],
            "checks": {
                "count": 0,
                "failed": 0,
                "pending": 0,
                "skipped_or_unknown": 0,
                "all_success": True,
            },
            "pr": {"number": number, "head_sha": head, "base_sha": base},
        }
        self.last_result = dict(result)
        return result

    def evaluate(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
        self.calls += 1
        pr = snapshot.state.open_pr or {}
        return self._result(
            int(pr.get("number", 10)),
            str(pr.get("headRefOid", "b" * 40)),
            str(pr.get("baseRefOid", SHA)),
        )

    def observe(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
        self.calls += 1
        pr = snapshot.state.open_pr or {}
        result = self._result(
            int(pr.get("number", 10)),
            str(pr.get("headRefOid", "b" * 40)),
            str(pr.get("baseRefOid", SHA)),
        )
        result["status"] = "observed"
        result["gate"] = "non-blocking"
        result["check_state"] = "pass"
        self.last_result = dict(result)
        return result

    def observe_exact_pr(
        self,
        _repository: str,
        pr_number: int,
        *,
        expected_head_sha: str,
        expected_base_sha: str,
    ) -> dict[str, Any]:
        self.calls += 1
        result = self._result(pr_number, expected_head_sha, expected_base_sha)
        result["status"] = "observed"
        result["gate"] = "non-blocking"
        result["check_state"] = "pass"
        self.last_result = dict(result)
        return result

    def query_exact_pr(
        self,
        _repository: str,
        pr_number: int,
        *,
        expected_head_sha: str,
        expected_base_sha: str,
        required_checks: dict[str, Any],
    ) -> dict[str, Any]:
        del required_checks
        self.calls += 1
        return self._result(pr_number, expected_head_sha, expected_base_sha)


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
    ) -> lck_models.EffectReceipt:
        self.calls.append("execute")
        return lck_models.EffectReceipt(
            "commit_current_tree",
            "committed",
            {"head_sha": "b" * 40, "tree_oid": tree},
        )


class StubEffect:
    def __init__(self, name: str, action: str) -> None:
        self.name = name
        self.action = action
        self.calls = 0

    def execute(self, *_args: Any, **kwargs: Any) -> lck_models.EffectReceipt:
        self.calls += 1
        details: dict[str, Any] = {}
        if self.name == "ensure_remote_branch":
            details["remote_oid"] = kwargs.get("expected_head_sha")
        elif self.name in {"ensure_open_pr", "reuse_open_pr"}:
            details.update(
                {
                    "number": 10,
                    "head_sha": kwargs.get("head_sha"),
                    "base_sha": kwargs.get("expected_base_sha"),
                }
            )
        return lck_models.EffectReceipt(self.name, self.action, details)


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
        if (
            args[:1] == ["show"]
            and len(args) == 2
            and args[1].endswith(":.github/workflows/ci.yml")
        ):
            return self._result(
                command_id,
                command,
                stdout=REQUIRED_CHECKS_WORKFLOW_TEXT,
            )
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
        if args == ["ls-remote", "origin", "refs/heads/main"]:
            return self._result(
                command_id,
                command,
                stdout=f"{self.base_sha}\trefs/heads/main",
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
            if (
                "--json" in command
                and command[command.index("--json") + 1] == "projectItems"
            ):
                return self._result(
                    command_id,
                    command,
                    stdout=json.dumps(
                        {"projectItems": [{"status": {"name": self.project_status}}]}
                    ),
                )
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
        if command[:3] == (
            "gh",
            "api",
            "repos/owner/repo/branches/main/protection/required_status_checks",
        ):
            return self._result(
                command_id, command, stdout=json.dumps({"contexts": []})
            )
        if command[:3] == ("gh", "issue", "view") and command[-1] == "projectItems":
            return self._result(
                command_id,
                command,
                stdout=json.dumps(
                    {"projectItems": [{"status": {"name": self.project_status}}]}
                ),
            )
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

    result = lck_delivery.DeliveryCompleter(
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
    assert _without_route_contract(agent_skill) == claude_skill
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
    monkeypatch.setattr(lck_state, "CommandRunner", lambda _repo_root: runner)

    exit_code = lck_cli.main(
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
    assert "operation_snapshot" not in payload
    receipt = lck_receipts.AuditReceiptStore(tmp_path).read(
        payload["receipt_reference"]
    )
    assert (
        receipt["operation_snapshot"]["operation"]
        == lck_models.Phase.DELIVERY_COMPLETE.value
    )
    assert (
        receipt["operation_snapshot"]["state"]["issue"]["project_status"]
        == "In Progress"
    )
    assert payload["effects"][-1]["effect"] == "set_review_status"
    assert payload["effects"][-1]["action"] in {"updated", "already-review"}
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


def test_lck_rollback_procedure_reverts_candidate_and_requires_fresh_review(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    last_reviewed_head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "-c", "task/163-activation")
    (repo / "activation.txt").write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", "activation.txt")
    _git(repo, "commit", "-m", "prepare Task 163 activation")
    candidate_head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "main")
    _git(repo, "merge", "--squash", candidate_head)
    _git(repo, "commit", "-m", "squash Task 163 candidate")
    activated_head = _git(repo, "rev-parse", "HEAD")
    assert activated_head != last_reviewed_head

    # Simulate activation failure recovery with one controlled revert of the
    # candidate merge, without reactivating any legacy workflow path.
    _git(repo, "revert", "--no-edit", activated_head)
    rollback_head = _git(repo, "rev-parse", "HEAD")
    assert rollback_head != activated_head
    assert _git(repo, "show", "-s", "--format=%P", rollback_head) == activated_head
    assert _git(repo, "diff", "--quiet", last_reviewed_head, "HEAD") == ""
    assert _git(repo, "status", "--porcelain") == ""

    # A later activation candidate must have a new reviewed head; the old
    # Review identity cannot authorize it after rollback.
    _git(repo, "switch", "-c", "task/163-activation-retry")
    (repo / "activation.txt").write_text("retry\n", encoding="utf-8")
    _git(repo, "add", "activation.txt")
    _git(repo, "commit", "-m", "prepare fresh Task 163 activation")
    fresh_head = _git(repo, "rev-parse", "HEAD")
    assert fresh_head not in {last_reviewed_head, activated_head, rollback_head}

    reviewed_identity = lck_review_workspace.ReviewIdentity(
        task_number=163,
        pr_number=168,
        base_sha=last_reviewed_head,
        head_sha=activated_head,
        task_body_sha256="a" * 64,
        merge_base_sha=last_reviewed_head,
        effective_diff_sha256="b" * 64,
        changed_files=("activation.txt",),
    )
    current_identity = lck_review_workspace.ReviewIdentity(
        task_number=163,
        pr_number=168,
        base_sha=last_reviewed_head,
        head_sha=fresh_head,
        task_body_sha256="a" * 64,
        merge_base_sha=last_reviewed_head,
        effective_diff_sha256="b" * 64,
        changed_files=("activation.txt",),
    )
    with pytest.raises(lck_models.ReviewStaleError, match="REVIEW_STALE_HEAD"):
        lck_review_workspace._assert_review_applicable(
            reviewed_identity, current_identity
        )


def _with_checks(
    state: lck_models.LiveState, checks: dict[str, Any]
) -> lck_models.LiveState:
    pr = dict(state.open_pr or {})
    items = (
        checks.get("items", {}).get("items", [])
        if isinstance(checks.get("items"), dict)
        else []
    )
    pr["statusCheckRollup"] = [
        {"name": item.get("name"), "conclusion": item.get("state")}
        for item in items
        if isinstance(item, dict)
    ]
    return lck_models.LiveState(
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
        open_pr=pr,
        merged_pr_numbers=state.merged_pr_numbers,
        merged=state.merged,
        checks=checks,
        cleanup=state.cleanup,
        status=state.status,
        stop_reasons=state.stop_reasons,
        warnings=state.warnings,
        task_contract=state.task_contract,
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


def test_repository_required_checks_policy_is_bound_to_exact_base_commit(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD")
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\njobs:\n  candidate-only:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo candidate\n",
        encoding="utf-8",
    )
    resolver = _resolver_for_repo(repo)

    policy = lck_state._repository_required_checks_at_commit(resolver, base_sha)

    assert policy["configuration"] == "repository-base-ci"
    assert policy["source_sha"] == base_sha
    assert policy["source"] == f"git:{base_sha}:.github/workflows/ci.yml:jobs"
    assert policy["contexts"]["items"] == ["quality"]
    assert policy["workflow_path"] == ".github/workflows/ci.yml"
    assert policy["contract_sha256"] == sha256_json(
        {"workflow": ".github/workflows/ci.yml", "required-checks": ["quality"]}
    )


def test_required_checks_policy_never_falls_back_to_working_tree(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    _git(repo, "rm", ".github/workflows/ci.yml")
    _git(repo, "commit", "-m", "remove canonical CI")
    base_sha = _git(repo, "rev-parse", "HEAD")
    _write_required_checks_workflow(repo)
    resolver = _resolver_for_repo(repo)

    with pytest.raises(lck_models.LckStopError, match="unavailable at trusted base"):
        lck_state._repository_required_checks_at_commit(resolver, base_sha)


def test_review_required_checks_policy_is_governed_by_pr_base_not_candidate_head(
    tmp_path: Path,
) -> None:
    repo, _ = _repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD")
    branch = "task/160-delivery-cutover"
    _git(repo, "switch", "-c", branch)
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.write_text(
        "name: CI\njobs:\n  self-approved:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo candidate\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".github/workflows/ci.yml")
    _git(repo, "commit", "-m", "candidate attempts to weaken checks")
    head_sha = _git(repo, "rev-parse", "HEAD")

    original = _live_state(
        head=head_sha,
        clean=True,
        project_status="Review",
        open_pr={
            "number": 10,
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": head_sha,
            "baseRefOid": base_sha,
        },
        remote_oid=head_sha,
    )
    state = replace(
        original,
        git={
            **original.git,
            "local_main_sha": base_sha,
            "origin_main_sha": base_sha,
            "remote_main_sha": base_sha,
        },
    )

    class ExactResolver:
        repo_root = repo
        runner = CommandRunner(repo)

        def resolve(self, _task: int) -> lck_models.LiveState:
            return state

    snapshot = lck_state.OperationSnapshotBuilder(cast(Any, ExactResolver())).acquire(
        160,
        operation="review-complete",
        include_required_checks=True,
    )

    assert snapshot.required_checks is not None
    assert snapshot.required_checks["source_sha"] == base_sha
    assert snapshot.required_checks["contexts"]["items"] == ["quality"]
    assert "self-approved" in _git(repo, "show", f"{head_sha}:.github/workflows/ci.yml")


def test_delivery_does_not_require_required_check_policy_before_effects(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\n", encoding="utf-8")
    head = "b" * 40
    state = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    snapshot = _snapshot(
        state,
        required={
            "configuration": "plan-limited-403",
            "contexts": {"items": [], "count": 0, "truncated": False},
        },
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [state])
    commit = StubCommit(dirty=False)
    remote = StubEffect("ensure_remote_branch", "created")
    pr = StubEffect("ensure_open_pr", "created")
    status = StubEffect("set_review_status", "updated")

    result = lck_delivery.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, StubValidation()),
        commit_effect=cast(Any, commit),
        remote_effect=cast(Any, remote),
        pr_effect=cast(Any, pr),
        status_effect=cast(Any, status),
        checks_gate=cast(Any, StubChecks()),
    ).complete(
        160,
        commit_message="candidate",
        summary="candidate",
        operation_snapshot=snapshot,
    )

    assert result.status == "READY_FOR_REVIEW"
    assert commit.calls == ["current_head_tree", "verify_tree_unchanged"]
    assert remote.calls == 1
    assert pr.calls == 1
    assert status.calls == 1


def test_strict_checks_gate_requires_named_required_check_success(
    tmp_path: Path,
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
    snapshot = _snapshot(
        state,
        required={
            **_required_policy("quality"),
        },
    )

    result = lck_validation.DeliveryChecksGate(cast(Any, resolver)).evaluate(snapshot)

    assert result["status"] == "pass"
    assert result["required"] == ["quality"]
    assert resolver.calls == 0


def test_strict_checks_gate_stops_on_failed_check(tmp_path: Path) -> None:
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

    gate = lck_validation.DeliveryChecksGate(cast(Any, resolver))
    with pytest.raises(lck_models.LckStopError, match="checks failed"):
        gate.evaluate(
            _snapshot(
                state,
                required={
                    **_required_policy("quality"),
                },
            )
        )
    assert gate.last_result is not None
    assert gate.last_result["status"] == "fail"
    assert gate.last_result["check_state"] == "failed"
    assert resolver.calls == 0


def test_strict_checks_gate_pending_stops_without_polling(tmp_path: Path) -> None:
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
    state = _with_checks(base, _checks(category="pending", state_name="IN_PROGRESS"))
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [state])

    with pytest.raises(lck_models.LckStopError, match="strict check gate"):
        lck_validation.DeliveryChecksGate(cast(Any, resolver)).evaluate(
            _snapshot(
                state,
                required={
                    **_required_policy("quality"),
                },
            )
        )
    assert resolver.calls == 0


def test_strict_checks_gate_rejects_legacy_plan_limited_policy(
    tmp_path: Path,
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
    snapshot = _snapshot(
        state,
        required={
            "configuration": "plan-limited-403",
            "failure": {"category": "plan-limit"},
            "contexts": {"items": [], "count": 0, "truncated": False},
        },
    )

    with pytest.raises(lck_models.LckStopError, match="canonical-CI check contract"):
        lck_validation.DeliveryChecksGate(cast(Any, resolver)).evaluate(snapshot)
    assert resolver.calls == 0


class FailingChecks:
    def observe(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
        pr = snapshot.state.open_pr or {}
        return {
            "status": "observed",
            "gate": "non-blocking",
            "check_state": "failed",
            "pr": {
                "number": pr.get("number", 10),
                "head_sha": pr.get("headRefOid", "b" * 40),
                "base_sha": pr.get("baseRefOid", SHA),
            },
        }

    def observe_exact_pr(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "observed",
            "gate": "non-blocking",
            "check_state": "failed",
            "pr": {
                "number": 10,
                "head_sha": "b" * 40,
                "base_sha": SHA,
            },
        }

    def evaluate(self, _snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
        raise lck_models.LckStopError("checks failed")

    def query_exact_pr(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise lck_models.LckStopError("checks failed")


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
    handler = lck_delivery.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, StubValidation()),
        commit_effect=cast(Any, commit),
        remote_effect=cast(Any, remote),
        pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
        status_effect=cast(Any, StubEffect("set_review_status", "updated")),
        checks_gate=cast(Any, StubChecks()),
    )

    with pytest.raises(lck_models.LckStopError, match="Critical Outcome FAIL"):
        handler.complete(
            160,
            commit_message="Implement LCK Delivery cutover",
            summary="Move initial Delivery mechanics into LCK.",
        )

    assert commit.calls == ["stage_candidate_tree"]
    assert remote.calls == 0

    store = lck_receipts.AuditReceiptStore(tmp_path)
    payload = lck_receipts._write_failure_receipt(
        operation="delivery-complete",
        task_number=160,
        operation_id="c" * 32,
        status="stop",
        code=None,
        error="Critical Outcome FAIL: test exited 1",
        handler=handler,
        store=store,
    )
    receipt = store.read(payload["receipt_reference"])

    assert handler.last_critical_outcome is not None
    assert receipt["audit"]["critical_outcome"]["status"] == "fail"
    assert receipt["audit"]["critical_outcome"]["exit_code"] == 1
    assert receipt["audit"]["critical_outcome"]["summary"] == "1 failed"


def test_delivery_failure_receipt_preserves_failed_formal_validation_payload(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\n", encoding="utf-8")
    state = _live_state(
        head="d" * 40,
        clean=False,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    validation_payload = {
        "schema_version": 1,
        "operation": "workflow-validation",
        "run_id": "val-delivery-receipt",
        "phase": "delivery",
        "base_sha": SHA,
        "status": "fail",
        "command_count": 1,
        "passed": 0,
        "failed": 1,
        "commands": [
            {
                "command_id": "pytest",
                "status": "fail",
                "exit_code": 1,
                "duration_ms": 37,
                "summary": "1 failed",
                "log_path": ".agents/validation.local/pytest.log",
                "diagnostic": "assertion failed",
            }
        ],
        "limitations": [],
        "output_dir": ".agents/validation.local",
    }

    class FailingFormalValidationRunner(CompletionRunner):
        def run(
            self,
            argv: list[str] | tuple[str, ...],
            *,
            command_id: str,
            **kwargs: Any,
        ) -> CommandResult:
            if command_id == "lck-formal-delivery-validation":
                command = tuple(str(item) for item in argv)
                self.commands.append(command)
                return CommandResult(
                    command_id,
                    command,
                    1,
                    json.dumps(validation_payload),
                    "",
                )
            return super().run(argv, command_id=command_id, **kwargs)

    runner = FailingFormalValidationRunner()
    resolver = SequenceResolver(tmp_path, runner, [state])
    handler = lck_delivery.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, lck_validation.FormalValidationGate(resolver)),
        commit_effect=cast(Any, StubCommit(dirty=True)),
        remote_effect=cast(Any, StubEffect("ensure_remote_branch", "created")),
        pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
        status_effect=cast(Any, StubEffect("set_review_status", "updated")),
        checks_gate=cast(Any, StubChecks()),
    )

    with pytest.raises(
        lck_models.LckStopError, match="formal Delivery validation failed"
    ):
        handler.complete(
            160,
            commit_message="Implement LCK Delivery cutover",
            summary="Move initial Delivery mechanics into LCK.",
        )

    store = lck_receipts.AuditReceiptStore(tmp_path)
    payload = lck_receipts._write_failure_receipt(
        operation="delivery-complete",
        task_number=160,
        operation_id="e" * 32,
        status="stop",
        code=None,
        error="formal Delivery validation failed: fail",
        handler=handler,
        store=store,
    )
    receipt = store.read(payload["receipt_reference"])

    assert receipt["audit"]["validation"] == validation_payload


def test_delivery_failure_receipt_preserves_completed_operation_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\n", encoding="utf-8")
    head = "b" * 40
    state = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )

    class FailingFinalRunner(CompletionRunner):
        def run(
            self,
            argv: list[str] | tuple[str, ...],
            *,
            command_id: str,
            **kwargs: Any,
        ) -> CommandResult:
            result = super().run(argv, command_id=command_id, **kwargs)
            if tuple(argv[:2]) == ("git", "status"):
                return CommandResult(command_id, tuple(argv), 0, " M candidate\n", "")
            return result

    runner = FailingFinalRunner()
    resolver = SequenceResolver(tmp_path, runner, [state])
    checks = StubChecks()
    handler = lck_delivery.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, StubValidation()),
        commit_effect=cast(Any, StubCommit(dirty=False)),
        remote_effect=cast(Any, StubEffect("ensure_remote_branch", "created")),
        pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
        status_effect=cast(Any, StubEffect("set_review_status", "updated")),
        checks_gate=cast(Any, checks),
    )

    with pytest.raises(lck_models.LckStopError, match="local postcondition failed"):
        handler.complete(
            160,
            commit_message="Implement LCK Delivery cutover",
            summary="Move initial Delivery mechanics into LCK.",
        )

    store = lck_receipts.AuditReceiptStore(tmp_path)
    payload = lck_receipts._write_failure_receipt(
        operation="delivery-complete",
        task_number=160,
        operation_id="d" * 32,
        status="stop",
        code=None,
        error="Delivery local postcondition failed",
        handler=handler,
        store=store,
    )
    receipt = store.read(payload["receipt_reference"])

    assert handler.last_snapshot is not None
    assert receipt["operation_snapshot"] == handler.last_snapshot.to_dict()
    assert receipt["audit"]["critical_outcome"]["status"] == "pass"
    assert receipt["audit"]["validation"]["status"] == "pass"
    assert receipt["audit"]["checks"] == handler.last_checks
    assert receipt["audit"]["checks"]["pr"] == {
        "number": 10,
        "head_sha": "b" * 40,
        "base_sha": SHA,
    }
    assert [item["effect"] for item in receipt["audit"]["effects"]] == [
        "commit_current_tree",
        "ensure_remote_branch",
        "ensure_open_pr",
        "set_review_status",
    ]


def test_delivery_complete_freezes_authority_for_the_operation(tmp_path: Path) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\n", encoding="utf-8")
    pre = _live_state(
        head="d" * 40,
        clean=False,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    changed_issue = dict(pre.issue or {})
    changed_issue["body_sha256"] = "f" * 64
    changed = lck_models.LiveState(
        **{
            **pre.__dict__,
            "issue": changed_issue,
            "git": {**pre.git, "remote_main_sha": "f" * 40},
        }
    )
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [pre, changed])

    result = lck_delivery.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, StubValidation()),
        commit_effect=cast(Any, StubCommit(dirty=True)),
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
    assert result.operation_snapshot.state.issue["body_sha256"] == "e" * 64
    assert result.operation_snapshot.state.git["remote_main_sha"] == SHA
    assert resolver.calls == 1


def test_failed_checks_do_not_block_delivery_project_status_transition(
    tmp_path: Path,
) -> None:
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

    result = lck_delivery.DeliveryCompleter(
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

    assert result.status == "READY_FOR_REVIEW"
    assert status.calls == 1


def test_initial_delivery_reaches_ready_for_review_with_pending_checks(
    tmp_path: Path,
) -> None:
    """Critical Outcome: pending CI is observed but is not a Delivery veto."""
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\n", encoding="utf-8")
    head = "b" * 40
    pr = {
        "number": 10,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": head,
        "baseRefOid": SHA,
        "statusCheckRollup": [
            {"name": "quality", "status": "IN_PROGRESS", "conclusion": None}
        ],
    }
    state = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=pr,
        remote_oid=head,
    )
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [state])
    status = StubEffect("set_review_status", "updated")

    result = lck_delivery.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, StubValidation()),
        commit_effect=cast(Any, StubCommit(dirty=False)),
        remote_effect=cast(Any, StubEffect("ensure_remote_branch", "already-present")),
        pr_effect=cast(Any, StubEffect("ensure_open_pr", "already-present")),
        status_effect=cast(Any, status),
    ).complete(
        160,
        commit_message="Move required checks to Review and Merge gates",
        summary="Publish the validated candidate without waiting for CI.",
    )

    assert result.status == "READY_FOR_REVIEW"
    assert result.checks["status"] == "observed"
    assert result.checks["check_state"] == "pending"
    assert status.calls == 1


def test_delivery_complete_does_not_requery_checks_after_gate(tmp_path: Path) -> None:
    target = tmp_path / "tests" / "test_critical_path.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_critical_path(): pass\n", encoding="utf-8")
    head = "b" * 40
    snapshot_state = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=None,
    )
    later_pr = {
        "number": 10,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": head,
        "baseRefOid": SHA,
    }
    later_state = _with_checks(
        _live_state(
            head=head,
            clean=True,
            project_status="Review",
            open_pr=later_pr,
            remote_oid=head,
        ),
        _checks(category="failed", state_name="FAILURE"),
    )
    resolver = SequenceResolver(
        tmp_path, CompletionRunner(), [snapshot_state, later_state]
    )
    checks = StubChecks()

    result = lck_delivery.DeliveryCompleter(
        cast(Any, resolver),
        formal_validation=cast(Any, StubValidation()),
        commit_effect=cast(Any, StubCommit(dirty=False)),
        remote_effect=cast(Any, StubEffect("ensure_remote_branch", "created")),
        pr_effect=cast(Any, StubEffect("ensure_open_pr", "created")),
        status_effect=cast(Any, StubEffect("set_review_status", "updated")),
        checks_gate=cast(Any, checks),
    ).complete(
        160,
        commit_message="Implement LCK Delivery cutover",
        summary="Move initial Delivery mechanics into LCK.",
    )

    assert result.status == "READY_FOR_REVIEW"
    assert checks.calls == 1
    assert resolver.calls == 1


def test_set_review_status_requires_matching_checks_receipt(tmp_path: Path) -> None:
    head = "b" * 40
    state = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=head,
    )
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [state])
    effect = lck_effects.SetReviewStatusEffect(cast(Any, resolver))

    with pytest.raises(
        lck_models.LckStopError, match="checks receipt PR identity mismatch"
    ):
        effect.execute(
            state,
            expected_pr={"number": 10, "head_sha": head, "base_sha": SHA},
            checks_result={
                "status": "pass",
                "pr": {"number": 11, "head_sha": head, "base_sha": SHA},
            },
        )

    assert resolver.calls == 0


def test_set_review_status_rejects_failed_checks_receipt_before_mutation(
    tmp_path: Path,
) -> None:
    head = "b" * 40
    state = _live_state(
        head=head,
        clean=True,
        project_status="In Progress",
        open_pr=None,
        remote_oid=head,
    )
    runner = CompletionRunner()
    resolver = SequenceResolver(tmp_path, runner, [state])
    identity = {"number": 10, "head_sha": head, "base_sha": SHA}

    with pytest.raises(lck_models.LckStopError, match="PR checks receipt is invalid"):
        lck_effects.SetReviewStatusEffect(cast(Any, resolver)).execute(
            state,
            expected_pr=identity,
            checks_result={"status": "stop", "pr": identity},
        )

    assert not any(command[:2] == ("gh", "project") for command in runner.commands)
    assert resolver.calls == 0


def test_set_review_status_accepts_nonblocking_checks_observation(
    tmp_path: Path,
) -> None:
    head = "b" * 40
    state = _live_state(
        head=head,
        clean=True,
        project_status="Review",
        open_pr=None,
        remote_oid=head,
    )
    resolver = SequenceResolver(tmp_path, CompletionRunner(), [state])
    identity = {"number": 10, "head_sha": head, "base_sha": SHA}

    receipt = lck_effects.SetReviewStatusEffect(cast(Any, resolver)).execute(
        state,
        expected_pr=identity,
        checks_result={
            "status": "observed",
            "gate": "non-blocking",
            "check_state": "pending",
            "pr": identity,
        },
    )

    assert receipt.action == "already-review"
