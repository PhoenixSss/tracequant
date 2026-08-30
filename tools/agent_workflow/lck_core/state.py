from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import yaml
from pr_resolve import PrResolveError, list_matching_prs, resolve_open_pr
from workflow_common import (
    CommandRunner,
    WorkflowToolError,
    command_warning,
    is_sha,
    read_json_text,
    sha256_json,
)
from workflow_evidence import (
    _git_snapshot,
    _issue_view_with_contract,
    _normalize_checks,
    _relationship_snapshot,
    _repository_slug,
)

from .issue_profiles import (
    LeafIssueKind,
    LeafIssueWorkflowProfile,
    canonical_branch_for_profile,
    resolve_issue_profile,
)
from .models import (
    _DIAGNOSTIC_FACT_PROFILE,
    BASE_BRANCH,
    RECOVERY_PR_FIELDS,
    REQUIRED_CHECKS_WORKFLOW,
    FactProfile,
    LckStopError,
    LiveState,
    OperationSnapshot,
    Phase,
    ResolutionStatus,
    _authoritative_remote_main_sha,
    _branch_matches_task,
    _pr_base_sha,
    _remote_refs,
    branch_matches_profile,
    canonical_task_branch,
    fact_profile_for_operation,
)


class LiveStateResolver:
    """Resolve one Task from current local and GitHub authority."""

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: CommandRunner | None = None,
        repository: str | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runner = runner or CommandRunner(self.repo_root)
        self.repository = repository

    def _git_lines(
        self,
        args: Sequence[str],
        *,
        command_id: str,
        warnings: list[dict[str, Any]],
    ) -> list[str]:
        result = self.runner.run(["git", *args], command_id=command_id)
        if result.returncode != 0:
            warnings.append(command_warning(result))
            return []
        return [line for line in result.stdout.splitlines() if line]

    def _task_branches(
        self,
        task_number: int,
        warnings: list[dict[str, Any]],
        *,
        include_local: bool = True,
        include_remote: bool = True,
        profile: LeafIssueWorkflowProfile | None = None,
    ) -> tuple[set[str], dict[str, str], bool]:
        matcher = (
            (lambda branch: branch_matches_profile(branch, task_number, profile))
            if profile is not None
            else (lambda branch: _branch_matches_task(branch, task_number))
        )
        local_lines = (
            self._git_lines(
                ["for-each-ref", "--format=%(refname:short)", "refs/heads"],
                command_id="lck-local-task-branches",
                warnings=warnings,
            )
            if include_local
            else []
        )
        local = {branch for branch in local_lines if matcher(branch)}
        if not include_remote:
            return local, {}, True
        remote_result = self.runner.run(
            ["git", "ls-remote", "--heads", "origin"],
            command_id="lck-remote-task-branches",
            retries=1,
        )
        if remote_result.returncode != 0:
            warnings.append(command_warning(remote_result))
            return local, {}, False
        remote_all = _remote_refs(remote_result.stdout)
        remote = {branch: oid for branch, oid in remote_all.items() if matcher(branch)}
        return local, remote, True

    def _recover_merged_pr_branch(
        self,
        task_number: int,
        repository: str,
        issue: Mapping[str, Any] | None,
        warnings: list[dict[str, Any]],
        profile: LeafIssueWorkflowProfile | None = None,
    ) -> tuple[str | None, Mapping[str, Any] | None, str | None]:
        """Recover a deleted Task ref from authoritative closing-PR facts."""
        if not isinstance(issue, Mapping):
            return None, None, None
        if str(issue.get("state", "")).upper() != "CLOSED":
            return None, None, None

        closure = issue.get("issue_closure")
        if not isinstance(closure, Mapping):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "Issue closure facts are unavailable",
            )
        if closure.get("evidence_status") != "complete":
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "Issue closure facts are incomplete",
            )
        refs = closure.get("closed_by_pull_requests")
        if not isinstance(refs, Mapping) or refs.get("truncated") is True:
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "closing PR references are incomplete",
            )
        items = refs.get("items")
        if not isinstance(items, list):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "closing PR references are malformed",
            )
        merged_refs = [
            item
            for item in items
            if isinstance(item, Mapping)
            and item.get("repository") == repository
            and item.get("merged") is True
            and str(item.get("state", "")).upper() == "MERGED"
            and isinstance(item.get("number"), int)
            and not isinstance(item.get("number"), bool)
        ]
        if len(merged_refs) != 1:
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                f"expected one merged closing PR, found {len(merged_refs)}",
            )

        pr_number = merged_refs[0]["number"]
        result = self.runner.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repository,
                "--json",
                RECOVERY_PR_FIELDS,
            ],
            command_id="lck-recover-merged-pr",
        )
        if result.returncode != 0 or not result.stdout.strip():
            warnings.append(command_warning(result))
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR identity cannot be read",
            )
        try:
            value = read_json_text(result.stdout, field="lck-recover-merged-pr")
        except WorkflowToolError as exc:
            warnings.append(
                {
                    "command_id": result.command_id,
                    "exit_code": result.returncode,
                    "error": str(exc),
                }
            )
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR identity is malformed",
            )
        if not isinstance(value, Mapping):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR identity is not an object",
            )
        if (
            value.get("number") != pr_number
            or str(value.get("state", "")).upper() != "MERGED"
            or value.get("baseRefName") != BASE_BRANCH
            or not (
                branch_matches_profile(
                    str(value.get("headRefName", "")),
                    task_number,
                    profile,
                )
                if profile is not None
                else _branch_matches_task(
                    str(value.get("headRefName", "")), task_number
                )
            )
        ):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR identity does not match this Task",
            )
        head_repository = value.get("headRepository")
        if (
            not isinstance(head_repository, Mapping)
            or head_repository.get("nameWithOwner") != repository
        ):
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR head repository is not this repository",
            )
        closing = value.get("closingIssuesReferences")
        closing_numbers = (
            [
                item.get("number")
                for item in closing
                if isinstance(item, Mapping) and isinstance(item.get("number"), int)
            ]
            if isinstance(closing, list)
            else []
        )
        if task_number not in closing_numbers:
            return (
                None,
                None,
                "Task branch identity unavailable after ref deletion: "
                "merged PR does not close this Task",
            )
        head_branch = value.get("headRefName")
        return str(head_branch), dict(value), None

    def resolve(self, task_number: int) -> LiveState:
        """Return the intentionally broad diagnostic status view."""
        return self._resolve(task_number, _DIAGNOSTIC_FACT_PROFILE)

    def resolve_for_operation(self, task_number: int, operation: str) -> LiveState:
        """Acquire exactly one operation-bound current authority fact profile."""
        return self._resolve(task_number, fact_profile_for_operation(operation))

    def _resolve(self, task_number: int, profile: FactProfile) -> LiveState:
        if task_number <= 0:
            raise LckStopError(f"Task number must be positive: {task_number}")

        warnings: list[dict[str, Any]] = []
        reasons: list[str] = []
        repository = _repository_slug(self.runner, self.repository, warnings)
        issue: Mapping[str, Any] | None = None
        task_contract: Mapping[str, Any] | None = None
        relationships: Mapping[str, Any] = {"available": False}
        if repository is not None:
            issue, task_contract = _issue_view_with_contract(
                self.runner,
                repository,
                task_number,
                warnings,
                profile.include_comments,
                profile.include_issue_closure,
                profile.include_task_contract,
            )
            relationships = _relationship_snapshot(
                self.runner, repository, task_number, warnings
            )
        else:
            reasons.append("repository identity unavailable")

        profile_resolution = resolve_issue_profile(issue)
        issue_profile = profile_resolution.to_dict()
        if not profile_resolution.resolved:
            reasons.append(profile_resolution.error_message)

        git: Mapping[str, Any]
        if profile.include_git:
            if profile.include_workspace_inventory:
                git = _git_snapshot(
                    self.runner,
                    warnings,
                    read_only_local_refs=True,
                )
            else:
                git = _git_snapshot(
                    self.runner,
                    warnings,
                    read_only_local_refs=True,
                    include_workspace_inventory=False,
                )
            if not isinstance(git.get("branch"), str) or not git.get("branch"):
                reasons.append("current local branch is unavailable")
            if not is_sha(git.get("head_sha")):
                reasons.append("current local HEAD is unavailable")
            if not is_sha(git.get("local_main_sha")):
                reasons.append("local main ref unavailable")
            if not is_sha(_authoritative_remote_main_sha(git)):
                reasons.append("remote main query failed")
            if git.get("clean") not in {True, False}:
                reasons.append("current worktree cleanliness is unavailable")
        else:
            git = {}
        if issue is None:
            reasons.append("Task metadata unavailable")
        if relationships.get("available") is not True:
            reasons.append("Task relationship facts unavailable")
        branch_warning_count = len(warnings)
        branch_profile = profile_resolution.profile
        if profile.include_local_task_branches and profile.include_remote_task_branches:
            if (
                branch_profile is None
                or branch_profile.issue_kind is LeafIssueKind.TASK
            ):
                local_branches, remote_branches, remote_available = self._task_branches(
                    task_number,
                    warnings,
                )
            else:
                local_branches, remote_branches, remote_available = self._task_branches(
                    task_number,
                    warnings,
                    profile=branch_profile,
                )
        else:
            if (
                branch_profile is not None
                and branch_profile.issue_kind is not LeafIssueKind.TASK
            ):
                local_branches, remote_branches, remote_available = self._task_branches(
                    task_number,
                    warnings,
                    include_local=profile.include_local_task_branches,
                    include_remote=profile.include_remote_task_branches,
                    profile=branch_profile,
                )
            else:
                local_branches, remote_branches, remote_available = self._task_branches(
                    task_number,
                    warnings,
                    include_local=profile.include_local_task_branches,
                    include_remote=profile.include_remote_task_branches,
                )
        if len(warnings) > branch_warning_count:
            reasons.append("Task branch inventory contains unavailable facts")
        if not remote_available:
            reasons.append("remote Task branch facts unavailable")
        title = issue.get("title") if isinstance(issue, Mapping) else None
        title_text = title if isinstance(title, str) else f"Task {task_number}"
        canonical = (
            canonical_branch_for_profile(
                profile_resolution.profile,
                task_number,
                title_text,
            )
            if profile_resolution.profile is not None
            else canonical_task_branch(task_number, title_text)
        )
        candidates = sorted(local_branches | set(remote_branches))
        if len(candidates) > 1:
            reasons.append(f"multiple Task branch candidates: {candidates}")
        recovered_branch: str | None = None
        recovered_pr: Mapping[str, Any] | None = None
        if not candidates and repository is not None:
            recovery_reason: str | None
            recovered_branch, recovered_pr, recovery_reason = (
                self._recover_merged_pr_branch(
                    task_number,
                    repository,
                    issue,
                    warnings,
                    branch_profile,
                )
            )
            if recovery_reason:
                reasons.append(recovery_reason)
        target_branch = (
            candidates[0] if len(candidates) == 1 else recovered_branch or canonical
        )
        local_branch = target_branch if target_branch in local_branches else None
        remote_branch = target_branch if target_branch in remote_branches else None

        local_head: str | None = None
        if local_branch is not None:
            result = self.runner.run(
                ["git", "rev-parse", f"refs/heads/{local_branch}"],
                command_id="lck-local-task-head",
            )
            if result.returncode == 0 and is_sha(result.stdout.strip()):
                local_head = result.stdout.strip()
            else:
                warnings.append(command_warning(result))
                reasons.append("local Task branch tip unavailable")
        remote_oid = remote_branches.get(target_branch)
        # Tip differences are live facts, not global resolver ambiguity.
        # Delivery Prepare rejects them; Delivery Complete may legitimately
        # observe a local validated commit that is not pushed yet.

        open_pr: Mapping[str, Any] | None = None
        merged_pr: Mapping[str, Any] | None = None
        merged_numbers: tuple[int, ...] = ()
        if repository is not None and profile.include_open_pr:
            try:
                if profile is _DIAGNOSTIC_FACT_PROFILE:
                    open_pr = resolve_open_pr(
                        self.runner,
                        repository,
                        target_branch,
                        BASE_BRANCH,
                        warnings,
                    )
                else:
                    open_pr = resolve_open_pr(
                        self.runner,
                        repository,
                        target_branch,
                        BASE_BRANCH,
                        warnings,
                        profile.include_checks,
                        profile.include_mergeability,
                        False,
                    )
            except PrResolveError as exc:
                reasons.append(str(exc))
        if repository is not None and profile.include_pr_history:
            try:
                if profile is _DIAGNOSTIC_FACT_PROFILE:
                    history = list_matching_prs(
                        self.runner,
                        repository,
                        target_branch,
                        BASE_BRANCH,
                        warnings,
                        include_history_details=True,
                    )
                else:
                    history = list_matching_prs(
                        self.runner,
                        repository,
                        target_branch,
                        BASE_BRANCH,
                        warnings,
                        include_checks=False,
                        include_mergeability=False,
                        include_history_details=profile.include_pr_history_details,
                    )
                if not history and recovered_pr is not None:
                    history = [dict(recovered_pr)]
                merged_items = [
                    item
                    for item in history
                    if str(item.get("state", "")).upper() == "MERGED"
                    and isinstance(item.get("number"), int)
                ]
                merged_numbers = tuple(
                    sorted(int(item["number"]) for item in merged_items)
                )
                if len(merged_items) == 1:
                    merged_pr = dict(merged_items[0])
            except PrResolveError as exc:
                reasons.append(str(exc))

        if open_pr is not None and profile.include_checks:
            checks = _normalize_checks(open_pr.get("statusCheckRollup"))
        else:
            checks = _normalize_checks([])
        if len(merged_numbers) > 1:
            reasons.append(
                f"multiple merged PRs for the Task branch: {list(merged_numbers)}"
            )
        if open_pr is not None:
            pr_head_oid = open_pr.get("headRefOid")
            if not is_sha(pr_head_oid):
                reasons.append("current OPEN PR head OID is unavailable")
            elif local_branch is None and remote_branch is None:
                reasons.append("current OPEN PR has no local or remote Task branch")
            else:
                if remote_oid is not None and remote_oid != pr_head_oid:
                    reasons.append(
                        "current OPEN PR head OID differs from remote Task branch tip"
                    )
        if open_pr is not None:
            merged: bool | None = False
        elif len(merged_numbers) == 1:
            merged = True
        elif len(merged_numbers) == 0:
            merged = False
        else:
            merged = None

        cleanup = {
            "business_delivery": "complete" if merged is True else "pending",
            "cleanup": "pending" if merged is True else "not-applicable",
            "local_branch_present": local_branch is not None,
            "remote_branch_present": remote_branch is not None,
            "open_pr_number": (
                open_pr.get("number") if isinstance(open_pr, Mapping) else None
            ),
            "merged_pr_numbers": merged_numbers,
            "merged_pr_number": (
                merged_pr.get("number") if isinstance(merged_pr, Mapping) else None
            ),
        }
        status = ResolutionStatus.STOP if reasons else ResolutionStatus.RESOLVED
        return LiveState(
            task_number=task_number,
            repository=repository,
            issue=issue,
            task_contract=task_contract,
            relationships=relationships,
            git=git,
            target_branch=target_branch,
            local_task_branch=local_branch,
            local_task_head=local_head,
            remote_task_branch=remote_branch,
            remote_task_oid=remote_oid,
            open_pr=open_pr,
            merged_pr_numbers=merged_numbers,
            merged=merged,
            checks=checks,
            cleanup=cleanup,
            status=status,
            stop_reasons=tuple(reasons),
            warnings=tuple(warnings),
            merged_pr=merged_pr,
            issue_profile=issue_profile,
        )


def _parse_required_checks_workflow(
    text: str,
    *,
    source_sha: str,
) -> dict[str, Any]:
    """Derive required GitHub check contexts from canonical CI at one base commit.

    Required-check policy is lifecycle control policy, so a candidate must not be
    able to weaken the checks that authorize its own transition. LCK therefore
    reads the canonical CI workflow from the immutable trusted base commit.
    Only statically named, non-matrix jobs are supported in v1; anything dynamic
    fails closed rather than guessing the check-run identity.
    """

    if not is_sha(source_sha):
        raise LckStopError("required PR check policy source SHA is invalid")
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LckStopError(
            "required PR check policy is malformed at trusted base "
            f"{source_sha}: {REQUIRED_CHECKS_WORKFLOW}: {exc}"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise LckStopError(
            "required PR check policy is unresolved at trusted base "
            f"{source_sha}: {REQUIRED_CHECKS_WORKFLOW} must contain a mapping"
        )
    jobs = parsed.get("jobs")
    if not isinstance(jobs, Mapping) or not jobs:
        raise LckStopError(
            "required PR check policy is unresolved at trusted base "
            f"{source_sha}: {REQUIRED_CHECKS_WORKFLOW} must define static jobs"
        )

    names: list[str] = []
    job_bindings: list[dict[str, str]] = []
    for raw_job_id, raw_job in jobs.items():
        if not isinstance(raw_job_id, str) or not raw_job_id.strip():
            raise LckStopError(
                "required PR check policy is invalid: CI job id must be a non-empty string"
            )
        job_id = raw_job_id.strip()
        if not isinstance(raw_job, Mapping):
            raise LckStopError(
                f"required PR check policy is invalid: CI job {job_id!r} is malformed"
            )
        strategy = raw_job.get("strategy")
        if isinstance(strategy, Mapping) and "matrix" in strategy:
            raise LckStopError(
                "required PR check policy is unresolved: matrix CI jobs are not "
                f"supported by LCK v1 ({job_id!r})"
            )
        raw_name = raw_job.get("name")
        if raw_name is None:
            check_name = job_id
        elif isinstance(raw_name, str) and raw_name.strip():
            check_name = raw_name.strip()
        else:
            raise LckStopError(
                f"required PR check policy is invalid: CI job {job_id!r} has an invalid name"
            )
        if "${{" in check_name:
            raise LckStopError(
                "required PR check policy is unresolved: dynamic CI job names are not "
                f"supported by LCK v1 ({job_id!r})"
            )
        if check_name in names:
            raise LckStopError(
                f"required PR check policy is invalid: duplicate check {check_name!r}"
            )
        names.append(check_name)
        job_bindings.append({"job_id": job_id, "check_name": check_name})

    contract_payload = {
        "workflow": REQUIRED_CHECKS_WORKFLOW,
        "required-checks": names,
    }
    return {
        "configuration": "repository-base-ci",
        "source": f"git:{source_sha}:{REQUIRED_CHECKS_WORKFLOW}:jobs",
        "source_sha": source_sha,
        "workflow_path": REQUIRED_CHECKS_WORKFLOW,
        "contract_sha256": sha256_json(contract_payload),
        "jobs": job_bindings,
        "contexts": {
            "items": names,
            "count": len(names),
            "truncated": False,
        },
    }


def _repository_required_checks_at_commit(
    resolver: LiveStateResolver,
    source_sha: str,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Read required checks from canonical CI at an exact trusted-base commit.

    ``repo_root`` may be the source repository or an isolated Review clone.
    Working-tree files and candidate-head policy are deliberately ignored.
    """

    if not is_sha(source_sha):
        raise LckStopError("required PR check policy trusted base is unavailable")
    result = resolver.runner.run(
        ["git", "show", f"{source_sha}:{REQUIRED_CHECKS_WORKFLOW}"],
        command_id="lck-required-check-policy-from-base-ci",
        cwd=repo_root or resolver.repo_root,
        env={"GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        raise LckStopError(
            "required PR check policy is unavailable at trusted base "
            f"{source_sha}: {REQUIRED_CHECKS_WORKFLOW}: {detail}"
        )
    return _parse_required_checks_workflow(result.stdout, source_sha=source_sha)


def _required_checks_policy_source_sha(
    state: LiveState,
    operation: str,
) -> str:
    """Return the immutable base commit that governs required-check policy.

    Candidate heads cannot authorize their own checks. Operations on an existing
    PR are governed by the exact PR base commit; the legacy Delivery branch is
    retained for callers that still request that policy source explicitly.
    """

    if operation == Phase.DELIVERY_COMPLETE.value:
        source_sha = _authoritative_remote_main_sha(state.git)
    else:
        pr = state.open_pr
        source_sha = _pr_base_sha(pr) if isinstance(pr, Mapping) else None
    if not is_sha(source_sha):
        raise LckStopError(
            f"required PR check policy trusted base is unavailable for {operation}"
        )
    return str(source_sha)


def _required_check_contract(required: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Validate and return the exact-base-bound required-check set.

    Legacy GitHub-discovery states and mutable working-tree configuration are
    diagnostic inputs only and can never authorize an LCK lifecycle effect.
    """

    if not isinstance(required, Mapping):
        raise LckStopError("required PR check policy was not acquired")
    if required.get("configuration") != "repository-base-ci":
        raise LckStopError(
            "required PR check policy is unresolved; LCK requires the "
            "exact trusted-base canonical-CI check contract before lifecycle effects"
        )
    source_sha = required.get("source_sha")
    if not is_sha(source_sha):
        raise LckStopError("required PR check policy source SHA is malformed")
    contract_sha256 = required.get("contract_sha256")
    if (
        not isinstance(contract_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", contract_sha256) is None
    ):
        raise LckStopError("required PR check policy contract hash is malformed")
    contexts = required.get("contexts")
    if not isinstance(contexts, Mapping):
        raise LckStopError("required PR check policy contexts are malformed")
    items = contexts.get("items")
    if not isinstance(items, list):
        raise LckStopError("required PR check policy items are malformed")
    names: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            raise LckStopError(
                "required PR check policy contains an invalid check name"
            )
        if item in names:
            raise LckStopError(
                f"required PR check policy contains duplicate check {item!r}"
            )
        names.append(item)
    workflow_path = required.get("workflow_path")
    if workflow_path != REQUIRED_CHECKS_WORKFLOW:
        raise LckStopError("required PR check policy workflow path is malformed")
    if (
        sha256_json({"workflow": REQUIRED_CHECKS_WORKFLOW, "required-checks": names})
        != contract_sha256
    ):
        raise LckStopError("required PR check policy contract hash does not match")
    return tuple(names)


def _required_check_contract_for_snapshot(
    snapshot: OperationSnapshot,
) -> tuple[str, ...]:
    """Validate that required-check policy is bound to this snapshot's base."""

    required = snapshot.required_checks
    names = _required_check_contract(required)
    required_mapping = cast(Mapping[str, Any], required)
    expected_sha = _required_checks_policy_source_sha(
        snapshot.state, snapshot.operation
    )
    if required_mapping.get("source_sha") != expected_sha:
        raise LckStopError(
            "required PR check policy is not bound to the operation trusted base: "
            "expected "
            f"{expected_sha}, got "
            f"{required_mapping.get('source_sha') or 'unavailable'}"
        )
    return names


class OperationSnapshotBuilder:
    """Acquire one phase-specific authoritative snapshot at operation entry."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def acquire(
        self,
        task_number: int,
        *,
        operation: str,
        include_required_checks: bool = False,
    ) -> OperationSnapshot:
        profile = fact_profile_for_operation(operation)
        operation_resolver = getattr(self.resolver, "resolve_for_operation", None)
        if callable(operation_resolver):
            state = copy.deepcopy(operation_resolver(task_number, operation))
        else:
            # Test/dedicated resolvers may already provide a pre-bounded state.
            state = copy.deepcopy(self.resolver.resolve(task_number))
        snapshot = OperationSnapshot(
            operation=operation,
            state=state,
            required_checks=None,
            acquisition_warnings=(),
            fact_profile=profile.name,
            acquired_facts=profile.facts(),
        )
        if include_required_checks:
            snapshot = self.bind_required_checks(snapshot)
        return snapshot

    def bind_required_checks(
        self,
        snapshot: OperationSnapshot,
        *,
        repo_root: Path | None = None,
    ) -> OperationSnapshot:
        """Bind one snapshot to the policy stored at its immutable trusted base.

        This method never re-resolves Git/GitHub state.  ``repo_root`` is only a
        repository object source; Review may pass its standalone clone after the
        exact base/head objects have been materialized there.
        """

        if snapshot.required_checks is not None:
            _required_check_contract(snapshot.required_checks)
            return snapshot
        source_sha = _required_checks_policy_source_sha(
            snapshot.state, snapshot.operation
        )
        required = _repository_required_checks_at_commit(
            self.resolver,
            source_sha,
            repo_root=repo_root,
        )
        _required_check_contract(required)
        return OperationSnapshot(
            operation=snapshot.operation,
            state=snapshot.state,
            required_checks=required,
            acquisition_warnings=snapshot.acquisition_warnings,
            fact_profile=snapshot.fact_profile,
            acquired_facts=snapshot.acquired_facts,
        )


def _task_contract_from_state(state: LiveState) -> dict[str, Any]:
    """Return the Task contract captured by the operation-start live snapshot."""
    if state.status is not ResolutionStatus.RESOLVED:
        raise LckStopError("cannot read Task Contract from unresolved live state")
    value = state.task_contract
    if not isinstance(value, Mapping):
        raise LckStopError("current Task Contract is unavailable in operation snapshot")
    body = value.get("body")
    body_sha256 = value.get("body_sha256")
    if not isinstance(body, str) or not isinstance(body_sha256, str):
        raise LckStopError("current Task Contract body is unavailable")
    issue = state.issue
    if (
        not isinstance(issue, Mapping)
        or issue.get("body_sha256") != body_sha256
        or value.get("number") != state.task_number
        or value.get("title") != issue.get("title")
        or str(issue.get("state", "")).upper() != "OPEN"
    ):
        raise LckStopError("Task Contract is inconsistent inside operation snapshot")
    return dict(value)
