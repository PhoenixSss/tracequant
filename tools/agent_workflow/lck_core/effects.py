from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from pr_resolve import PrResolveError, resolve_or_create_pr
from project_status import set_project_status_with_runner
from workflow_common import (
    WorkflowToolError,
    is_sha,
    read_json_text,
    safe_text,
    stderr_tail,
)
from workflow_evidence import _find_project_status

from .models import (
    BASE_BRANCH,
    EffectReceipt,
    LckStopError,
    LiveState,
    _authoritative_remote_main_sha,
    _remote_refs,
)
from .profile_policies import ProfileEffectDescriptor
from .state import LiveStateResolver


class EffectExecutorRegistry:
    """Immutable kernel allowlist for policy-declared effect kinds."""

    def __init__(self, executors: Mapping[str, Any]) -> None:
        selected: dict[str, Any] = {}
        for kind, executor in executors.items():
            if not isinstance(kind, str) or not kind or not callable(executor):
                raise ValueError("effect executor registry entry is malformed")
            if kind in selected:
                raise ValueError(f"duplicate effect executor: {kind}")
            selected[kind] = executor
        self._executors = MappingProxyType(selected)

    @property
    def executors(self) -> Mapping[str, Any]:
        return self._executors

    def execute(
        self,
        descriptor: ProfileEffectDescriptor,
        *,
        resolver: LiveStateResolver,
        state: LiveState,
    ) -> EffectReceipt:
        if not isinstance(descriptor, ProfileEffectDescriptor):
            raise LckStopError("completion effect descriptor is malformed")
        try:
            executor = self._executors[descriptor.effect_kind]
        except KeyError as exc:
            raise LckStopError(
                f"unknown completion effect kind: {descriptor.effect_kind}"
            ) from exc
        try:
            receipt = executor(descriptor, resolver=resolver, state=state)
            if not isinstance(receipt, EffectReceipt):
                raise LckStopError("completion effect executor returned no receipt")
            return receipt
        except LckStopError:
            raise
        except Exception as exc:
            raise LckStopError(f"completion effect execution failed: {exc}") from exc


def _pending_effect(
    effect: str, *, reason: str, details: Mapping[str, Any]
) -> EffectReceipt:
    payload = {"reason": reason, **dict(details)}
    return EffectReceipt(effect=effect, action="pending", details=payload)


_PROJECT_FIELD_QUERY: Final = r"""
query($owner:String!, $projectNumber:Int!, $fieldName:String!, $userAfter:String, $organizationAfter:String) {
  user(login:$owner) {
    projectV2(number:$projectNumber) {
      items(first:100, after:$userAfter) {
        nodes {
          content { ... on Issue { number repository { nameWithOwner } } }
          fieldValueByName(name:$fieldName) {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
  organization(login:$owner) {
    projectV2(number:$projectNumber) {
      items(first:100, after:$organizationAfter) {
        nodes {
          content { ... on Issue { number repository { nameWithOwner } } }
          fieldValueByName(name:$fieldName) {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


class ProjectSingleSelectEffectExecutor:
    """Execute the one generic Project single-select effect kind."""

    effect_kind = "project.single_select.set.v1"
    schema_version = 1

    @staticmethod
    def _parameters(descriptor: ProfileEffectDescriptor) -> dict[str, Any]:
        params = dict(descriptor.parameters)
        required = {"repository", "task_number", "project_number", "field", "value"}
        if set(params) != required:
            raise LckStopError("project field effect parameters are invalid")
        if (
            not isinstance(params["repository"], str)
            or not params["repository"]
            or not isinstance(params["task_number"], int)
            or isinstance(params["task_number"], bool)
            or params["task_number"] <= 0
            or not isinstance(params["project_number"], int)
            or isinstance(params["project_number"], bool)
            or params["project_number"] <= 0
            or not isinstance(params["field"], str)
            or not params["field"]
            or not isinstance(params["value"], str)
            or not params["value"]
        ):
            raise LckStopError("project field effect parameters are invalid")
        return params

    @classmethod
    def _validate(cls, descriptor: ProfileEffectDescriptor) -> dict[str, Any]:
        if (
            descriptor.effect_kind != cls.effect_kind
            or descriptor.schema_version != cls.schema_version
        ):
            raise LckStopError("unsupported project field effect schema")
        params = cls._parameters(descriptor)
        expected_postcondition = {
            "kind": "project.single_select.equals",
            **params,
        }
        if dict(descriptor.postcondition) != expected_postcondition:
            raise LckStopError("project field effect postcondition is invalid")
        return params

    @staticmethod
    def _query(
        resolver: LiveStateResolver,
        *,
        repository: str,
        project_number: int,
        task_number: int,
        field: str,
    ) -> str | None:
        owner, separator, _name = repository.partition("/")
        if not separator or not owner:
            return None
        cursors: dict[str, str | None] = {"user": None, "organization": None}
        seen: dict[str, set[str]] = {"user": set(), "organization": set()}
        complete: set[str] = set()
        while len(complete) < 2:
            argv = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={' '.join(_PROJECT_FIELD_QUERY.split())}",
                "-F",
                f"owner={owner}",
                "-F",
                f"projectNumber={project_number}",
                "-F",
                f"fieldName={field}",
            ]
            if cursors["user"] is not None:
                argv.extend(("-F", f"userAfter={cursors['user']}"))
            if cursors["organization"] is not None:
                argv.extend(("-F", f"organizationAfter={cursors['organization']}"))
            result = resolver.runner.run(
                argv, command_id="lck-profile-effect-postcondition", retries=1
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            value = read_json_text(
                result.stdout, field="lck-profile-effect-postcondition"
            )
            if not isinstance(value, Mapping) or value.get("errors"):
                return None
            data = value.get("data")
            if not isinstance(data, Mapping):
                return None
            for scope in ("user", "organization"):
                if scope in complete:
                    continue
                owner_data = data.get(scope)
                if owner_data is None:
                    complete.add(scope)
                    continue
                if not isinstance(owner_data, Mapping):
                    return None
                project = owner_data.get("projectV2")
                if project is None:
                    complete.add(scope)
                    continue
                if not isinstance(project, Mapping):
                    return None
                items = project.get("items")
                if not isinstance(items, Mapping):
                    return None
                nodes = items.get("nodes")
                page_info = items.get("pageInfo")
                if not isinstance(nodes, list) or not isinstance(page_info, Mapping):
                    return None
                for item in nodes:
                    if not isinstance(item, Mapping):
                        continue
                    content = item.get("content")
                    repo = (
                        content.get("repository")
                        if isinstance(content, Mapping)
                        else None
                    )
                    if (
                        isinstance(content, Mapping)
                        and content.get("number") == task_number
                        and isinstance(repo, Mapping)
                        and repo.get("nameWithOwner") == repository
                    ):
                        field_value = item.get("fieldValueByName")
                        if not isinstance(field_value, Mapping):
                            return None
                        return safe_text(field_value.get("name"))
                if page_info.get("hasNextPage") is False:
                    complete.add(scope)
                elif page_info.get("hasNextPage") is True:
                    cursor = page_info.get("endCursor")
                    if (
                        not isinstance(cursor, str)
                        or not cursor
                        or cursor in seen[scope]
                    ):
                        return None
                    seen[scope].add(cursor)
                    cursors[scope] = cursor
                else:
                    return None
        return None

    @classmethod
    def execute(
        cls,
        descriptor: ProfileEffectDescriptor,
        *,
        resolver: LiveStateResolver,
        state: LiveState,
    ) -> EffectReceipt:
        params = cls._validate(descriptor)
        if (
            state.repository != params["repository"]
            or state.task_number != params["task_number"]
        ):
            raise LckStopError(
                "project field effect identity does not match live state"
            )
        current = (
            state.issue.get(params["field"])
            if isinstance(state.issue, Mapping)
            else None
        )
        action = "already-set" if current == params["value"] else "updated"
        if action == "updated":
            try:
                set_project_status_with_runner(
                    resolver.runner,
                    params["repository"],
                    params["task_number"],
                    project_number=params["project_number"],
                    field=params["field"],
                    value=params["value"],
                )
            except WorkflowToolError as exc:
                raise LckStopError(f"project field effect write failed: {exc}") from exc
        observed = cls._query(
            resolver,
            repository=params["repository"],
            project_number=params["project_number"],
            task_number=params["task_number"],
            field=params["field"],
        )
        if observed != params["value"]:
            return _pending_effect(
                cls.effect_kind,
                reason="project field effect postcondition is not proven",
                details={"field": params["field"], "value": params["value"]},
            )
        details = {
            "field": params["field"],
            "value": params["value"],
            "effect_kind": descriptor.effect_kind,
            **dict(descriptor.receipt),
        }
        return EffectReceipt(
            effect=cls.effect_kind,
            action=action,
            details=details,
        )


DEFAULT_EFFECT_EXECUTOR_REGISTRY: Final = EffectExecutorRegistry(
    {
        ProjectSingleSelectEffectExecutor.effect_kind: ProjectSingleSelectEffectExecutor.execute
    }
)
PROFILE_EFFECT_EXECUTOR_REGISTRY: Final = DEFAULT_EFFECT_EXECUTOR_REGISTRY
ProfileEffectExecutorRegistry = EffectExecutorRegistry


class CommitCurrentTreeEffect:
    """Commit exactly the staged tree that passed Critical Outcome + validation."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _run(self, argv: Sequence[str], command_id: str) -> Any:
        result = self.resolver.runner.run(argv, command_id=command_id)
        if result.returncode != 0:
            raise LckStopError(
                f"{command_id} failed with exit code {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def stage_candidate_tree(self) -> str:
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-delivery-status-before-stage",
        )
        if not status.stdout.strip():
            raise LckStopError("Delivery Complete found no uncommitted Task changes")
        self._run(["git", "add", "-A", "--", ":/"], "lck-stage-current-tree")
        cached = self.resolver.runner.run(
            ["git", "diff", "--cached", "--quiet"],
            command_id="lck-staged-diff-present",
        )
        if cached.returncode == 0:
            raise LckStopError("Delivery candidate contains no staged changes")
        if cached.returncode not in {0, 1}:
            raise LckStopError("unable to determine staged Delivery diff")
        check = self.resolver.runner.run(
            ["git", "diff", "--cached", "--check"],
            command_id="lck-staged-diff-check",
        )
        if check.returncode != 0:
            raise LckStopError(
                "staged Delivery diff failed git diff --check: "
                + (check.stderr.strip() or check.stdout.strip())
            )
        tree = self._run(
            ["git", "write-tree"], "lck-write-candidate-tree"
        ).stdout.strip()
        if not is_sha(tree):
            raise LckStopError("candidate tree OID is unavailable")
        return tree

    def current_head_tree(self) -> str:
        tree = self._run(
            ["git", "rev-parse", "HEAD^{tree}"],
            "lck-current-head-tree",
        ).stdout.strip()
        if not is_sha(tree):
            raise LckStopError("current HEAD tree OID is unavailable")
        return tree

    def verify_tree_unchanged(
        self,
        expected_tree: str,
        *,
        expected_head_sha: str | None = None,
    ) -> None:
        unstaged = self.resolver.runner.run(
            ["git", "diff", "--quiet"],
            command_id="lck-post-validation-unstaged-check",
        )
        if unstaged.returncode != 0:
            if unstaged.returncode == 1:
                raise LckStopError("working tree changed during formal validation")
            raise LckStopError("unable to verify post-validation working tree")
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-post-validation-status",
        )
        # Staged entries are expected. Any untracked/unstaged entry makes status
        # contain a line not represented by the staged tree; git diff --quiet
        # already rejects tracked unstaged changes, and untracked paths start '??'.
        if any(line.startswith("??") for line in status.stdout.splitlines()):
            raise LckStopError("untracked files appeared during formal validation")
        current_tree = self._run(
            ["git", "write-tree"], "lck-write-tree-post-validation"
        ).stdout.strip()
        if current_tree != expected_tree:
            raise LckStopError("validated candidate tree changed before commit")
        if expected_head_sha is not None:
            current_head = self._run(
                ["git", "rev-parse", "HEAD"],
                "lck-post-validation-head",
            ).stdout.strip()
            if current_head != expected_head_sha:
                raise LckStopError("local Task HEAD changed during formal validation")

    def execute(
        self,
        expected_tree: str,
        message: str,
        *,
        expected_parent_sha: str | None = None,
    ) -> EffectReceipt:
        if not message.strip() or "\x00" in message or len(message) > 240:
            raise LckStopError("commit message must be non-empty and <= 240 characters")
        current_tree = self._run(
            ["git", "write-tree"], "lck-write-tree-pre-commit"
        ).stdout.strip()
        if current_tree != expected_tree:
            raise LckStopError(
                "commit precondition failed: staged tree is not validated tree"
            )
        if expected_parent_sha is not None:
            current_parent = self._run(
                ["git", "rev-parse", "HEAD"],
                "lck-commit-parent-head",
            ).stdout.strip()
            if current_parent != expected_parent_sha:
                raise LckStopError(
                    "commit precondition failed: local Task HEAD changed"
                )
        self._run(["git", "commit", "-m", message], "lck-commit-current-tree")
        head = self._run(
            ["git", "rev-parse", "HEAD"], "lck-head-after-commit"
        ).stdout.strip()
        if expected_parent_sha is not None:
            parent = self._run(
                ["git", "rev-parse", "HEAD^"],
                "lck-parent-after-commit",
            ).stdout.strip()
            if parent != expected_parent_sha:
                raise LckStopError(
                    "commit postcondition failed: commit parent HEAD changed"
                )
        tree = self._run(
            ["git", "rev-parse", "HEAD^{tree}"], "lck-tree-after-commit"
        ).stdout.strip()
        if not is_sha(head) or tree != expected_tree:
            raise LckStopError(
                "commit postcondition failed: committed tree != validated tree"
            )
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-status-after-commit",
        )
        if status.stdout.strip():
            raise LckStopError("commit postcondition failed: worktree is not clean")
        return EffectReceipt(
            effect="commit_current_tree",
            action="committed",
            details={"head_sha": head, "tree_oid": tree},
        )


class EnsureRemoteBranchEffect:
    """Ensure origin Task branch equals current local HEAD without force push."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _identity(
        self,
        branch: str,
        *,
        expected_head_sha: str | None = None,
    ) -> tuple[str, str | None]:
        branch_result = self.resolver.runner.run(
            ["git", "branch", "--show-current"], command_id="lck-push-current-branch"
        )
        head_result = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"], command_id="lck-push-current-head"
        )
        if branch_result.returncode != 0 or head_result.returncode != 0:
            raise LckStopError("cannot resolve local branch identity before push")
        current_branch = branch_result.stdout.strip()
        head = head_result.stdout.strip()
        if current_branch != branch or not is_sha(head):
            raise LckStopError("push precondition failed: local Task identity changed")
        if expected_head_sha is not None and head != expected_head_sha:
            raise LckStopError(
                "push precondition failed: validated local Task HEAD changed"
            )
        remote_result = self.resolver.runner.run(
            ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
            command_id="lck-push-remote-head",
        )
        if remote_result.returncode != 0:
            raise LckStopError("cannot resolve remote Task branch before push")
        refs = _remote_refs(remote_result.stdout)
        remote_oid = refs.get(branch)
        return head, remote_oid

    def _ensure_upstream(self, branch: str) -> str:
        expected = f"origin/{branch}"
        upstream = self.resolver.runner.run(
            [
                "git",
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ],
            command_id="lck-task-branch-upstream",
        )
        current = upstream.stdout.strip() if upstream.returncode == 0 else ""
        if current != expected:
            set_upstream = self.resolver.runner.run(
                [
                    "git",
                    "branch",
                    "--set-upstream-to",
                    expected,
                    branch,
                ],
                command_id="lck-set-task-branch-upstream",
            )
            if set_upstream.returncode != 0:
                raise LckStopError(
                    "cannot establish Task branch upstream: "
                    + (
                        set_upstream.stderr.strip()
                        or set_upstream.stdout.strip()
                        or f"exit {set_upstream.returncode}"
                    )
                )
            upstream = self.resolver.runner.run(
                [
                    "git",
                    "rev-parse",
                    "--abbrev-ref",
                    "--symbolic-full-name",
                    "@{upstream}",
                ],
                command_id="lck-verify-task-branch-upstream",
            )
            current = upstream.stdout.strip() if upstream.returncode == 0 else ""
        if current != expected:
            raise LckStopError(
                "Task branch upstream postcondition failed: "
                f"expected {expected!r}, observed {current or 'unavailable'!r}"
            )
        return current

    def execute(
        self,
        branch: str,
        *,
        expected_head_sha: str | None = None,
    ) -> EffectReceipt:
        head, remote_oid = self._identity(
            branch,
            expected_head_sha=expected_head_sha,
        )
        if remote_oid == head:
            upstream = self._ensure_upstream(branch)
            return EffectReceipt(
                effect="ensure_remote_branch",
                action="already-synced",
                details={
                    "head_sha": head,
                    "remote_oid": remote_oid,
                    "upstream": upstream,
                },
            )
        if remote_oid is not None:
            remote_ref = f"refs/heads/{branch}"
            available = self.resolver.runner.run(
                ["git", "cat-file", "-e", f"{remote_oid}^{{commit}}"],
                command_id="lck-observed-remote-object-available",
            )
            local_object_available = available.returncode == 0
            if not local_object_available:
                fetch = self.resolver.runner.run(
                    ["git", "fetch", "--no-tags", "origin", remote_ref],
                    command_id="lck-fetch-observed-remote-task-branch",
                )
                if fetch.returncode != 0:
                    diagnostic = stderr_tail(fetch.stderr or fetch.stdout, limit=1200)
                    raise LckStopError(
                        "REMOTE_HEAD_FETCH_FAILED: "
                        f"remote_ref={remote_ref}; observed_remote_oid={remote_oid}; "
                        f"candidate_oid={head}; local_object_available=false; "
                        f"git_exit_code={fetch.returncode}; stderr={diagnostic or 'empty'}"
                    )
                fetched = self.resolver.runner.run(
                    ["git", "rev-parse", "FETCH_HEAD"],
                    command_id="lck-fetched-remote-task-head",
                )
                if fetched.returncode != 0 or fetched.stdout.strip() != remote_oid:
                    observed = (
                        fetched.stdout.strip()
                        if fetched.returncode == 0
                        else "unavailable"
                    )
                    raise LckStopError(
                        "REMOTE_HEAD_CHANGED: "
                        f"remote_ref={remote_ref}; observed_remote_oid={remote_oid}; "
                        f"materialized_oid={observed}; candidate_oid={head}"
                    )
            ancestor = self.resolver.runner.run(
                ["git", "merge-base", "--is-ancestor", remote_oid, head],
                command_id="lck-remote-fast-forward-check",
            )
            if ancestor.returncode != 0:
                raise LckStopError(
                    "REMOTE_BRANCH_DIVERGED: "
                    f"remote_oid={remote_oid}; candidate_oid={head}; "
                    "LCK will not rebase or force push"
                )
            action = "fast-forwarded"
        else:
            action = "created"

        push = self.resolver.runner.run(
            [
                "git",
                "push",
                "-u",
                "origin",
                f"{head}:refs/heads/{branch}",
            ],
            command_id="lck-push-task-branch",
        )
        if push.returncode != 0:
            diagnostic = stderr_tail(push.stderr or push.stdout, limit=1200)
            raise LckStopError(
                "REMOTE_PUSH_REJECTED: "
                f"remote_ref=refs/heads/{branch}; candidate_oid={head}; "
                f"git_exit_code={push.returncode}; stderr={diagnostic or 'empty'}"
            )
        final_head, final_remote = self._identity(
            branch,
            expected_head_sha=expected_head_sha,
        )
        if final_head != head or final_remote != head:
            raise LckStopError(
                "REMOTE_HEAD_CHANGED: push postcondition failed: "
                f"candidate_oid={head}; local_oid={final_head}; remote_oid={final_remote}"
            )
        upstream = self._ensure_upstream(branch)
        return EffectReceipt(
            effect="ensure_remote_branch",
            action=action,
            details={
                "head_sha": head,
                "remote_oid": final_remote,
                "upstream": upstream,
            },
        )


class EnsureOpenPrEffect:
    """Ensure one OPEN PR using only operation-snapshot authority.

    The PR helper may query/create the exact PR as the bounded effect itself,
    but it never re-runs lifecycle resolution.
    """

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        state: LiveState,
        *,
        head_sha: str,
        summary: str,
        risks: str,
        critical_outcome: Mapping[str, Any] | None,
        validation: Mapping[str, Any],
        expected_base_sha: str,
        expected_body_sha256: str,
    ) -> EffectReceipt:
        repository = state.repository
        base = _authoritative_remote_main_sha(state.git)
        issue = state.issue
        if (
            not isinstance(repository, str)
            or not is_sha(head_sha)
            or not is_sha(base)
            or not isinstance(issue, Mapping)
        ):
            raise LckStopError("OPEN PR snapshot preconditions are incomplete")
        if state.merged is not False:
            raise LckStopError(
                "OPEN PR precondition failed: Task merge state is unavailable "
                "or already merged"
            )
        if state.target_branch == BASE_BRANCH:
            raise LckStopError("OPEN PR precondition failed: Task branch is invalid")
        if base != expected_base_sha:
            raise LckStopError("OPEN PR precondition failed: snapshot base mismatch")
        if issue.get("body_sha256") != expected_body_sha256:
            raise LckStopError(
                "OPEN PR precondition failed: snapshot Task body mismatch"
            )
        title = issue.get("title")
        if not isinstance(title, str) or not title.strip():
            raise LckStopError("Task title is unavailable for PR creation")
        validation_lines = [f"- Formal Delivery validation: {validation.get('status')}"]
        if critical_outcome is not None:
            validation_lines.insert(
                0,
                f"- Critical Outcome: {critical_outcome.get('status')}",
            )
        body = (
            f"Closes #{state.task_number}\n\n"
            "## Summary\n"
            f"{summary.strip()}\n\n"
            "## Validation\n" + "\n".join(validation_lines) + "\n\n"
            "## Risks / limitations\n"
            f"{risks.strip() or 'None identified.'}\n"
        )
        warnings: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="lck-pr-") as temp_dir:
            body_file = Path(temp_dir) / "body.md"
            body_file.write_text(body, encoding="utf-8", newline="\n")
            try:
                result = resolve_or_create_pr(
                    self.resolver.runner,
                    repository,
                    state.target_branch,
                    BASE_BRANCH,
                    title,
                    body_file,
                    head_sha,
                    expected_base_sha,
                    warnings,
                )
            except PrResolveError as exc:
                raise LckStopError(str(exc)) from exc
        if (
            result.get("head_sha") != head_sha
            or result.get("base_sha") != expected_base_sha
            or not isinstance(result.get("number"), int)
        ):
            raise LckStopError("OPEN PR postcondition returned an unexpected identity")
        return EffectReceipt(
            effect="ensure_open_pr",
            action=str(result.get("action")),
            details={
                "number": result.get("number"),
                "url": result.get("url"),
                "head_sha": result.get("head_sha"),
                "base_sha": result.get("base_sha"),
                "warnings": warnings,
            },
        )


class ReuseExistingOpenPrEffect:
    """Verify the pushed repair is attached to the snapshot's existing OPEN PR."""

    PR_IDENTITY_FIELDS: Final = (
        "number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid"
    )

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        state: LiveState,
        *,
        head_sha: str,
        summary: str,
        risks: str,
        critical_outcome: Mapping[str, Any] | None,
        validation: Mapping[str, Any],
        expected_base_sha: str,
        expected_body_sha256: str,
    ) -> EffectReceipt:
        del summary, risks, critical_outcome, validation
        pr = state.open_pr
        issue = state.issue
        repository = state.repository
        if (
            not isinstance(pr, Mapping)
            or pr.get("isDraft") is not False
            or not isinstance(repository, str)
        ):
            raise LckStopError("Remediation requires the existing non-Draft OPEN PR")
        if _authoritative_remote_main_sha(state.git) != expected_base_sha:
            raise LckStopError(
                "Remediation PR precondition failed: snapshot base mismatch"
            )
        if (
            not isinstance(issue, Mapping)
            or issue.get("body_sha256") != expected_body_sha256
        ):
            raise LckStopError(
                "Remediation PR precondition failed: snapshot Task body mismatch"
            )
        pr_number = pr.get("number")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool):
            raise LckStopError("Remediation PR number is unavailable")
        result = self.resolver.runner.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repository,
                "--json",
                self.PR_IDENTITY_FIELDS,
            ],
            command_id="lck-remediation-pr-postcondition",
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise LckStopError("Remediation PR postcondition cannot be queried")
        current = read_json_text(
            result.stdout, field="lck-remediation-pr-postcondition"
        )
        if not isinstance(current, Mapping):
            raise LckStopError("Remediation PR postcondition is malformed")
        if (
            current.get("number") != pr_number
            or str(current.get("state", "")).upper() != "OPEN"
            or current.get("isDraft") is not False
            or current.get("headRefOid") != head_sha
            or current.get("baseRefOid") != expected_base_sha
            or current.get("headRefName") != state.target_branch
            or current.get("baseRefName") != BASE_BRANCH
        ):
            raise LckStopError(
                "Remediation PR postcondition failed: existing PR is not on the pushed head"
            )
        return EffectReceipt(
            effect="reuse_open_pr",
            action="reused-current-open-pr",
            details={
                "number": pr_number,
                "url": current.get("url"),
                "head_sha": head_sha,
                "base_sha": expected_base_sha,
            },
        )


class SetReviewStatusEffect:
    """Move the Task to Review and verify only that metadata effect."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _query_project_status(self, repository: str, task_number: int) -> str | None:
        result = self.resolver.runner.run(
            [
                "gh",
                "issue",
                "view",
                str(task_number),
                "--repo",
                repository,
                "--json",
                "projectItems",
            ],
            command_id="lck-review-status-postcondition",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        value = read_json_text(result.stdout, field="lck-review-status-postcondition")
        if not isinstance(value, Mapping):
            return None
        return _find_project_status(value.get("projectItems"))

    def execute(
        self,
        state: LiveState,
        *,
        expected_pr: Mapping[str, Any] | None = None,
        checks_result: Mapping[str, Any] | None = None,
    ) -> EffectReceipt:
        if state.repository is None:
            raise LckStopError("cannot set Review status without repository identity")
        if expected_pr is None or checks_result is None:
            raise LckStopError(
                "Project Status Review requires the exact PR checks observation"
            )
        gated_pr = checks_result.get("pr")
        expected_identity = {
            "number": expected_pr.get("number"),
            "head_sha": expected_pr.get("head_sha"),
            "base_sha": expected_pr.get("base_sha"),
        }
        if (
            not isinstance(gated_pr, Mapping)
            or {
                "number": gated_pr.get("number"),
                "head_sha": gated_pr.get("head_sha"),
                "base_sha": gated_pr.get("base_sha"),
            }
            != expected_identity
        ):
            raise LckStopError(
                "Project Status precondition failed: checks receipt PR identity mismatch"
            )
        if checks_result.get("status") not in {"pass", "observed"}:
            raise LckStopError(
                "Project Status precondition failed: PR checks receipt is invalid"
            )
        if state.project_status == "Review":
            return EffectReceipt(
                effect="set_review_status", action="already-review", details={}
            )
        previous_status = state.project_status
        if previous_status not in {"Ready", "In Progress"}:
            raise LckStopError(
                "Project Status precondition failed: prior status cannot be restored"
            )
        set_project_status_with_runner(
            self.resolver.runner,
            state.repository,
            state.task_number,
            value="Review",
        )
        observed = self._query_project_status(state.repository, state.task_number)
        if observed == "Review":
            return EffectReceipt(
                effect="set_review_status",
                action="updated",
                details={"status": "Review"},
            )

        try:
            set_project_status_with_runner(
                self.resolver.runner,
                state.repository,
                state.task_number,
                value=previous_status,
            )
        except Exception as restore_exc:
            raise LckStopError(
                "Project Status postcondition failed and compensation could not "
                f"restore {previous_status!r}"
            ) from restore_exc
        restored = self._query_project_status(state.repository, state.task_number)
        if restored != previous_status:
            raise LckStopError(
                "Project Status postcondition failed and compensation did not "
                f"restore {previous_status!r}"
            )
        raise LckStopError(
            "Project Status postcondition failed; restored Project Status to "
            f"{previous_status!r}"
        )
