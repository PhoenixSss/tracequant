# ruff: noqa: E402, I001

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from tracequant.contracts import (
    ALWAYS_ON_SURFACES,
    AssuranceResult,
    AssuranceStatus,
    ReviewRunReceipt,
    ReviewSurfacePlan,
    TokenUsage,
)

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    models as lck_models,
    structured_review as lck_structured_review,
    review_workspace as lck_review_workspace,
    state as lck_state,
)

from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
    sha256_json,
)


SHA = "a" * 40


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
        elif args == ["branch", "--show-current"]:
            stdout = self.branch
        elif args == ["rev-parse", "HEAD"]:
            stdout = self.head_sha
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
        elif (
            args[:1] == ["show"]
            and len(args) == 2
            and args[1].endswith(":.github/workflows/ci.yml")
        ):
            stdout = REQUIRED_CHECKS_WORKFLOW_TEXT
        elif args[:2] == ["gh", "api"] and "required_status_checks" in args[2]:
            stdout = json.dumps({"contexts": ["quality"]})
        elif args[:2] == ["switch", "-c"]:
            branch = args[2]
            self.local_branches.add(branch)
            self.branch = branch
            if len(args) >= 5 and args[3] == "--track":
                remote = args[4].removeprefix("origin/")
                self.head_sha = self.remote_branches.get(remote, self.head_sha)
        elif args[:2] == ["switch", "--track"]:
            branch = args[3]
            self.local_branches.add(branch)
            self.branch = branch
            remote = args[2].removeprefix("origin/")
            self.head_sha = self.remote_branches.get(remote, self.head_sha)
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
    body = "Task Contract"
    return {
        "number": 159,
        "title": "[Task] 建立 LCK Core 与 Live State Resolution",
        "body_sha256": sha256_json({"body": body}),
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


def _task_contract(issue: dict[str, Any] | None = None) -> dict[str, Any]:
    value = issue or _issue()
    body = "Task Contract"
    return {
        "number": value.get("number", 159),
        "title": value.get("title"),
        "url": value.get("url"),
        "body": body,
        "body_sha256": value.get("body_sha256") or sha256_json({"body": body}),
        "critical_outcome": value.get("critical_outcome"),
        "bug_contract": value.get("bug_contract"),
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
    monkeypatch.setattr(lck_state, "_repository_slug", lambda *_args: "owner/repo")
    issue_value = issue or _issue()
    monkeypatch.setattr(
        lck_state,
        "_issue_view_with_contract",
        lambda *_args: (issue_value, _task_contract(issue_value)),
    )
    monkeypatch.setattr(
        lck_state,
        "_relationship_snapshot",
        lambda *_args: relationships if relationships is not None else _relationships(),
    )
    monkeypatch.setattr(
        lck_state,
        "_git_snapshot",
        lambda *_args, **_kwargs: _git_snapshot(fake),
    )
    monkeypatch.setattr(lck_state, "resolve_open_pr", lambda *_args: open_pr)
    monkeypatch.setattr(
        lck_state, "list_matching_prs", lambda *_args, **_kwargs: history or []
    )


def _resolver(fake: FakeRunner) -> lck_state.LiveStateResolver:
    return lck_state.LiveStateResolver(Path.cwd(), runner=cast(Any, fake))


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


def _review_state(
    *,
    head: str = SHA,
    base: str = SHA,
    clean: bool = True,
) -> lck_models.LiveState:
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
    return lck_models.LiveState(
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
        task_contract={
            "number": 159,
            "title": issue["title"],
            "url": issue.get("url"),
            "body": "Task Contract",
            "body_sha256": "d" * 64,
            "critical_outcome": issue.get("critical_outcome"),
        },
    )


def _review_identity_value(
    *, head: str = SHA, base: str = SHA
) -> lck_review_workspace.ReviewIdentity:
    return lck_review_workspace.ReviewIdentity(
        task_number=159,
        pr_number=200,
        base_sha=base,
        head_sha=head,
        task_body_sha256="d" * 64,
        merge_base_sha=base,
        effective_diff_sha256="e" * 64,
        changed_files=("tools/agent_workflow/lck.py",),
    )


def structured_review_receipt(
    identity: lck_review_workspace.ReviewIdentity,
    *,
    repository: str = "owner/repo",
) -> ReviewRunReceipt:
    """Build a complete no-finding v2 receipt for lifecycle controller tests."""
    authority = lck_structured_review.expected_live_authority(
        repository=repository,
        task_number=identity.task_number,
        pr_number=identity.pr_number,
        base_sha=identity.base_sha,
        head_sha=identity.head_sha,
        diff_sha256=identity.effective_diff_sha256,
    )
    obligations = lck_structured_review.STRUCTURED_REVIEW_OBLIGATIONS
    results = tuple(
        AssuranceResult(
            obligation_id=item.obligation_id,
            status=(
                AssuranceStatus.NOT_APPLICABLE
                if item.obligation_id == "state-persistence-compatibility"
                else AssuranceStatus.PASS
            ),
            evidence_refs=(f"evidence://{item.obligation_id}",),
            summary="explicitly reviewed",
        )
        for item in obligations
    )
    matrix = {
        item.obligation_id: {
            "requirement": item.description,
            "implementation": "reviewed current effective diff",
            "evidence": [f"evidence://{item.obligation_id}"],
            "status": results[index].status.value,
        }
        for index, item in enumerate(obligations)
    }
    return ReviewRunReceipt(
        run_id="review-run-test",
        authority=authority,
        harness_config={"sealed_subject": True},
        protocol_config={
            "protocol_id": lck_structured_review.STRUCTURED_REVIEW_PROTOCOL_ID,
            "protocol_version": lck_structured_review.STRUCTURED_REVIEW_PROTOCOL_VERSION,
            "coverage_matrix": matrix,
            "falsification_attempts": (),
        },
        model_config={"temperature": 0},
        coverage=ReviewSurfacePlan(
            required=ALWAYS_ON_SURFACES,
            covered=ALWAYS_ON_SURFACES,
        ),
        candidate_findings=(),
        verified_findings=(),
        token_usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        wall_clock_ms=1,
        assurance_obligations=obligations,
        assurance_results=results,
    )


class StaticResolver:
    def __init__(self, repo_root: Path, state: lck_models.LiveState) -> None:
        self.repo_root = repo_root
        self.state = state
        branch = str(state.git.get("branch") or "main")
        head_sha = str(state.git.get("head_sha") or state.local_task_head or SHA)
        local_branches = {state.local_task_branch} if state.local_task_branch else set()
        remote_branches = (
            {state.remote_task_branch: str(state.remote_task_oid)}
            if state.remote_task_branch and state.remote_task_oid
            else {}
        )
        self.runner = cast(
            Any,
            FakeRunner(
                branch=branch,
                local_branches=local_branches,
                remote_branches=remote_branches,
                clean=state.git.get("clean") is True,
                head_sha=head_sha,
                local_main_sha=str(state.git.get("local_main_sha") or SHA),
                origin_main_sha=str(
                    state.git.get("remote_main_sha")
                    or state.git.get("origin_main_sha")
                    or SHA
                ),
                open_pr=dict(state.open_pr)
                if isinstance(state.open_pr, dict)
                else None,
            ),
        )
        self.calls = 0

    def resolve(self, _task_number: int) -> lck_models.LiveState:
        self.calls += 1
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

    def create(
        self,
        _task: int,
        _base: str,
        _head: str,
        path: Path | None = None,
    ) -> Path:
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

    def evaluate(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
        self.calls += 1
        pr = snapshot.state.open_pr or {}
        return {
            "status": "pass",
            "pr": {
                "number": pr.get("number", 200),
                "head_sha": pr.get("headRefOid", SHA),
                "base_sha": pr.get("baseRefOid", SHA),
            },
            "checks": snapshot.state.checks,
        }

    def observe(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
        self.calls += 1
        pr = snapshot.state.open_pr or {}
        return {
            "status": "observed",
            "gate": "non-blocking",
            "check_state": "pending",
            "pr": {
                "number": pr.get("number", 200),
                "head_sha": pr.get("headRefOid", SHA),
                "base_sha": pr.get("baseRefOid", SHA),
            },
            "checks": snapshot.state.checks,
        }


class FakeReviewValidation:
    def run(self, _root: Path, _base: str, _head: str) -> dict[str, Any]:
        return {"status": "pass", "phase": "review"}


def _review_guard(
    identity: lck_review_workspace.ReviewIdentity,
    *,
    review_root: Path | str = "review-root",
) -> dict[str, Any]:
    return {
        "task_number": 159,
        "identity": identity.to_dict(),
        "review_root": str(review_root),
        "checks": {
            "status": "pass",
            "pr": {
                "number": identity.pr_number,
                "head_sha": identity.head_sha,
                "base_sha": identity.base_sha,
            },
        },
        "validation": {"status": "pass"},
        "snapshot": {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "operation": "review-prepare",
            "state": {"task_number": identity.task_number},
        },
    }


def _write_owned_candidate_session(
    store: lck_review_workspace.ReviewInvocationStore,
    review_id: str,
    *,
    start_head: str = SHA,
    candidate_head: str = "b" * 40,
    candidate_tree: str = "c" * 40,
) -> None:
    operation_id = store.new_id()
    store.write_remediation_session(
        159,
        {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "kind": "remediation-session",
            "task_number": 159,
            "review_id": review_id,
            "operation_id": operation_id,
            "start_head_sha": start_head,
            "pr_number": 200,
            "base_sha": SHA,
            "findings_sha256": "f" * 64,
            "findings_source": "local-review-record",
            "candidate": {
                "operation_id": operation_id,
                "start_head_sha": start_head,
                "head_sha": candidate_head,
                "tree_oid": candidate_tree,
            },
            "authority": "test",
        },
    )


class OwnedCandidateRunner(FakeRunner):
    def __init__(self, *, head_sha: str, tree_oid: str) -> None:
        branch = "task/159-lck-core-live-state-resolution"
        open_pr = _open_pr(branch)
        super().__init__(
            branch=branch,
            local_branches={branch},
            remote_branches={branch: SHA},
            clean=True,
            head_sha=head_sha,
            open_pr=open_pr,
        )
        self.tree_oid = tree_oid

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        command_id: str,
        **kwargs: Any,
    ) -> CommandResult:
        command = tuple(str(item) for item in argv)
        if command == ("git", "rev-parse", "HEAD^{tree}"):
            self.commands.append(command)
            return CommandResult(command_id, command, 0, f"{self.tree_oid}\n", "")
        if command == ("git", "diff", "--quiet"):
            self.commands.append(command)
            return CommandResult(command_id, command, 0, "", "")
        if command[:3] == ("git", "diff", "--quiet"):
            self.commands.append(command)
            return CommandResult(command_id, command, 1, "", "")
        if command == (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ):
            self.commands.append(command)
            return CommandResult(command_id, command, 0, "", "")
        if command == ("git", "write-tree"):
            self.commands.append(command)
            return CommandResult(command_id, command, 0, f"{self.tree_oid}\n", "")
        if command[:3] == ("git", "cat-file", "-e"):
            self.commands.append(command)
            return CommandResult(command_id, command, 0, "", "")
        if command[:3] == ("git", "merge-base", "--is-ancestor"):
            self.commands.append(command)
            return CommandResult(command_id, command, 0, "", "")
        if command[:3] == ("git", "push", "-u"):
            self.commands.append(command)
            self.remote_branches[self.branch] = self.head_sha
            assert self.open_pr is not None
            self.open_pr["headRefOid"] = self.head_sha
            return CommandResult(command_id, command, 0, "", "")
        if command == (
            "git",
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ):
            self.commands.append(command)
            return CommandResult(command_id, command, 0, f"origin/{self.branch}\n", "")
        return super().run(argv, command_id=command_id, **kwargs)
