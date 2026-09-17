from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .common import CommandResult, ProgressReporter, is_sha, read_json_text, stderr_tail
from .effects import CommitCurrentTreeEffect
from .eligibility import PhaseDecision, PhaseEligibilityResolver
from .issue_profiles import resolve_leaf_issue_profile
from .models import (
    BASE_BRANCH,
    LCK_SCHEMA_VERSION,
    EffectReceipt,
    LckStopError,
    LiveState,
    OperationSnapshot,
    Phase,
    _jsonable,
    _remote_main_sha,
)
from .profile_policies import (
    DEFAULT_PROFILE_POLICY_REGISTRY,
    ProfileEvidenceEnvelope,
    ProfileGateFailure,
    ProfilePolicyRegistry,
    ProfileResolver,
    resolve_issue_policy,
    run_profile_delivery_gates,
)
from .review_workspace import ReviewInvocationStore
from .state import LiveStateResolver, OperationSnapshotBuilder, _policy_issue_from_state
from .validation_gates import FormalValidationGate

_GIT_OPERATION_MARKERS: Final = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "REBASE_HEAD",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "BISECT_START",
)

_PR_IDENTITY_FIELDS: Final = (
    "number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid"
)


def _active_git_operations(
    resolver: LiveStateResolver, *, command_prefix: str
) -> tuple[str, ...]:
    active: list[str] = []
    for marker in _GIT_OPERATION_MARKERS:
        result = resolver.runner.run(
            ["git", "rev-parse", "--git-path", marker],
            command_id=f"{command_prefix}-{marker.casefold().replace('_', '-')}",
        )
        raw_path = result.stdout.strip()
        if result.returncode != 0 or not raw_path:
            raise LckStopError("cannot inspect current Git operation state")
        path = Path(raw_path)
        if not path.is_absolute():
            path = resolver.repo_root / path
        if path.exists():
            active.append(marker)
    return tuple(active)


@dataclass(frozen=True)
class RefreshResult:
    task_number: int
    operation_id: str
    status: str
    action: str
    operation_snapshot: OperationSnapshot
    eligibility: PhaseDecision
    branch: str
    pr_number: int
    start_head_sha: str
    frozen_main_sha: str
    head_sha: str
    validated_tree_oid: str
    critical_outcome: Mapping[str, Any] | None = None
    profile_evidence: ProfileEvidenceEnvelope | None = None
    validation: Mapping[str, Any] | None = None
    effects: tuple[EffectReceipt, ...] = ()

    @property
    def state(self) -> LiveState:
        return self.operation_snapshot.state

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "refresh",
            "status": self.status,
            "action": self.action,
            "task_number": self.task_number,
            "operation_id": self.operation_id,
            "issue_profile": _jsonable(self.state.issue_profile),
            "branch": self.branch,
            "pr_number": self.pr_number,
            "start_head_sha": self.start_head_sha,
            "frozen_main_sha": self.frozen_main_sha,
            "head_sha": self.head_sha,
            "validated_tree_oid": self.validated_tree_oid,
            "critical_outcome": _jsonable(self.critical_outcome),
            "profile_evidence": (
                self.profile_evidence.to_dict() if self.profile_evidence else None
            ),
            "validation": _jsonable(self.validation),
            "effects": [effect.to_dict() for effect in self.effects],
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "fresh_review_required": self.status == "READY_FOR_FRESH_REVIEW",
            "automatic_review": False,
            "automatic_merge": False,
        }


class CandidateRefresher:
    """Atomically rebase one Review candidate onto the current trusted main."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        formal_validation: FormalValidationGate | None = None,
        store: ReviewInvocationStore | None = None,
        policy_registry: ProfilePolicyRegistry | None = None,
        profile_resolver: ProfileResolver | None = None,
        services: Sequence[Any] = (),
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.policy_registry = policy_registry or DEFAULT_PROFILE_POLICY_REGISTRY
        self.profile_resolver = profile_resolver or resolve_leaf_issue_profile
        self.eligibility = eligibility or PhaseEligibilityResolver(
            registry=self.policy_registry,
            profile_resolver=self.profile_resolver,
        )
        self.formal_validation = formal_validation or FormalValidationGate(resolver)
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.services = tuple(services)
        self.tree_effect = CommitCurrentTreeEffect(resolver)
        self.last_snapshot: OperationSnapshot | None = None
        self.last_critical_outcome: dict[str, Any] | None = None
        self.last_documentation_validation: dict[str, Any] | None = None
        self.last_profile_evidence: ProfileEvidenceEnvelope | None = None
        self.last_validation: dict[str, Any] | None = None
        self.last_checks: dict[str, Any] | None = None
        self.last_effects: list[EffectReceipt] = []
        self.last_conflict_files: tuple[str, ...] = ()

    def _run(self, argv: Sequence[str], command_id: str) -> CommandResult:
        return self.resolver.runner.run(argv, command_id=command_id)

    def _require_no_durable_handoff(self, task_number: int) -> None:
        if self.store.read_remediation_session(task_number) is not None:
            raise LckStopError(
                "Candidate Refresh STOP: a Remediation session is active"
            )
        if self.store.review_prepare_active(task_number):
            raise LckStopError(
                "Candidate Refresh STOP: a Review Prepare/handoff is active"
            )

    def _ensure_main_object(self, frozen_main_sha: str) -> None:
        available = self._run(
            ["git", "cat-file", "-e", f"{frozen_main_sha}^{{commit}}"],
            "lck-refresh-main-object",
        )
        if available.returncode == 0:
            return
        fetched = self._run(
            ["git", "fetch", "--no-tags", "origin", "refs/heads/main"],
            "lck-refresh-fetch-main",
        )
        fetch_head = self._run(
            ["git", "rev-parse", "FETCH_HEAD"],
            "lck-refresh-fetch-main-head",
        )
        if (
            fetched.returncode != 0
            or fetch_head.returncode != 0
            or fetch_head.stdout.strip() != frozen_main_sha
        ):
            raise LckStopError("cannot materialize the frozen origin/main commit")

    def _current_head(self) -> str:
        result = self._run(["git", "rev-parse", "HEAD"], "lck-refresh-current-head")
        head = result.stdout.strip()
        if result.returncode != 0 or not is_sha(head):
            raise LckStopError("Candidate Refresh cannot resolve current HEAD")
        return head

    def _require_clean_branch(self, branch: str, head_sha: str) -> None:
        current_branch = self._run(
            ["git", "branch", "--show-current"], "lck-refresh-current-branch"
        )
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-refresh-current-status",
        )
        if (
            current_branch.returncode != 0
            or current_branch.stdout.strip() != branch
            or self._current_head() != head_sha
            or status.returncode != 0
            or status.stdout.strip()
        ):
            raise LckStopError(
                "Candidate Refresh requires the exact clean Task branch workspace"
            )

    def _restore_original(self, branch: str, start_head: str) -> None:
        active = _active_git_operations(
            self.resolver, command_prefix="lck-refresh-rollback-operation"
        )
        rebase_markers = {"REBASE_HEAD", "rebase-merge", "rebase-apply"}
        if active:
            if not set(active).issubset(rebase_markers):
                raise LckStopError(
                    "Candidate Refresh rollback found a non-rebase Git operation"
                )
            aborted = self._run(
                ["git", "rebase", "--abort"], "lck-refresh-rollback-rebase"
            )
            if aborted.returncode != 0:
                raise LckStopError("Candidate Refresh could not abort its rebase")
        if self._current_head() != start_head:
            status = self._run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                "lck-refresh-rollback-status",
            )
            if status.returncode != 0 or status.stdout.strip():
                raise LckStopError(
                    "Candidate Refresh cannot safely restore a changed candidate workspace"
                )
            commands = (
                (["git", "switch", "--detach", start_head], "detach-original"),
                (["git", "branch", "--force", branch, start_head], "restore-ref"),
                (["git", "switch", branch], "restore-branch"),
            )
            for argv, suffix in commands:
                result = self._run(argv, f"lck-refresh-rollback-{suffix}")
                if result.returncode != 0:
                    raise LckStopError(
                        "Candidate Refresh could not restore the original Task head"
                    )
        self._require_clean_branch(branch, start_head)

    def _run_profile_gates(
        self,
        state: LiveState,
        base_sha: str,
        head_sha: str,
        *,
        progress: ProgressReporter,
    ) -> Mapping[str, Any] | None:
        issue = _policy_issue_from_state(state)
        if not issue:
            raise LckStopError("current leaf Issue workflow profile is unavailable")
        try:
            profile, _policy = resolve_issue_policy(
                issue,
                registry=self.policy_registry,
                profile_resolver=self.profile_resolver,
            )
            results = run_profile_delivery_gates(
                profile,
                base_sha=base_sha,
                head_sha=head_sha,
                include_index=False,
                progress=progress,
                issue=issue,
                registry=self.policy_registry,
                repo_root=self.resolver.repo_root,
                runner=self.resolver.runner,
                services=self.services,
            )
        except ProfileGateFailure as exc:
            self.last_profile_evidence = exc.profile_evidence
            for field, value in exc.legacy_results.items():
                target = f"last_{field}"
                if isinstance(field, str) and hasattr(self, target):
                    setattr(self, target, value)
            raise
        except ValueError as exc:
            raise LckStopError(
                f"current leaf Issue workflow profile is unavailable: {exc}"
            ) from exc
        self.last_documentation_validation = results.documentation_validation
        self.last_critical_outcome = results.critical_outcome
        self.last_profile_evidence = results.profile_evidence
        return results.critical_outcome

    def _run_formal_validation(self, base_sha: str) -> dict[str, Any]:
        try:
            result = self.formal_validation.run(base_sha)
        except BaseException:
            payload = getattr(self.formal_validation, "last_payload", None)
            if isinstance(payload, dict):
                self.last_validation = payload
            raise
        self.last_validation = result
        return result

    def _remote_oid(self, ref: str, *, command_id: str) -> str | None:
        result = self._run(["git", "ls-remote", "--heads", "origin", ref], command_id)
        if result.returncode != 0:
            raise LckStopError(f"cannot resolve remote ref {ref}")
        lines = [line.split() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return None
        if len(lines) != 1 or len(lines[0]) != 2 or lines[0][1] != ref:
            raise LckStopError(f"remote ref {ref} is ambiguous")
        oid = lines[0][0]
        if not is_sha(oid):
            raise LckStopError(f"remote ref {ref} has an invalid object id")
        return oid

    def _verify_pr_current(
        self,
        state: LiveState,
        *,
        pr_number: int,
        branch: str,
        start_head: str,
        frozen_main: str,
    ) -> None:
        if not isinstance(state.repository, str):
            raise LckStopError("Candidate Refresh repository identity is unavailable")
        result = self._run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                state.repository,
                "--json",
                _PR_IDENTITY_FIELDS,
            ],
            "lck-refresh-pr-before-push",
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise LckStopError("Candidate Refresh cannot verify the existing OPEN PR")
        current = read_json_text(result.stdout, field="lck-refresh-pr-before-push")
        if (
            current.get("number") != pr_number
            or str(current.get("state", "")).upper() != "OPEN"
            or current.get("isDraft") is not False
            or current.get("baseRefName") != BASE_BRANCH
            or current.get("baseRefOid") != frozen_main
            or current.get("headRefName") != branch
            or current.get("headRefOid") != start_head
        ):
            raise LckStopError(
                "Candidate Refresh PR/base/head identity changed before push"
            )

    def _push_exact_lease(
        self, branch: str, start_head: str, new_head: str
    ) -> EffectReceipt:
        ref = f"refs/heads/{branch}"
        remote_before = self._remote_oid(
            ref, command_id="lck-refresh-remote-before-push"
        )
        if remote_before != start_head:
            raise LckStopError(
                "Candidate Refresh remote Task head changed before exact-lease push"
            )
        pushed = self._run(
            [
                "git",
                "push",
                f"--force-with-lease={ref}:{start_head}",
                "origin",
                f"{new_head}:{ref}",
            ],
            "lck-refresh-push-exact-lease",
        )
        if pushed.returncode != 0:
            observed = self._remote_oid(
                ref, command_id="lck-refresh-remote-after-failed-push"
            )
            if observed == new_head:
                action = "updated-observed-after-command-failure"
            elif observed == start_head:
                raise LckStopError(
                    "Candidate Refresh exact-lease push failed: "
                    + (
                        stderr_tail(pushed.stderr or pushed.stdout)
                        or f"exit {pushed.returncode}"
                    )
                )
            else:
                raise LckStopError(
                    "Candidate Refresh remote Task head became ambiguous during push"
                )
        else:
            action = "force-with-exact-lease"
        return EffectReceipt(
            effect="refresh_remote_branch",
            action=action,
            details={
                "branch": branch,
                "old_head_sha": start_head,
                "head_sha": new_head,
                "lease": f"{ref}:{start_head}",
            },
        )

    def refresh(self, task_number: int) -> RefreshResult:
        operation_id = self.store.new_id()
        progress = ProgressReporter("refresh")
        progress.started("initializing")
        self.last_effects = []
        self.last_conflict_files = ()
        candidate_active = False
        boundary_written = False
        prior_boundary: Mapping[str, Any] | None = None
        branch = ""
        start_head = ""
        try:
            self._require_no_durable_handoff(task_number)
            progress.running("resolving-live-state")
            snapshot = self.snapshots.acquire(
                task_number,
                operation=Phase.REFRESH.value,
            )
            self.last_snapshot = snapshot
            state = snapshot.state
            decision = self.eligibility.resolve(state, Phase.REFRESH)
            if not decision.eligible:
                raise LckStopError(
                    f"Candidate Refresh STOP for Task #{task_number}: "
                    + "; ".join(decision.reasons)
                )
            pr = state.open_pr
            start_head_value = state.local_issue_head
            frozen_main_value = _remote_main_sha(state.git)
            if (
                not isinstance(pr, Mapping)
                or not is_sha(start_head_value)
                or not is_sha(frozen_main_value)
            ):
                raise LckStopError("Candidate Refresh identity is incomplete")
            pr_number = pr.get("number")
            if not isinstance(pr_number, int) or isinstance(pr_number, bool):
                raise LckStopError("Candidate Refresh PR number is unavailable")
            branch = state.target_branch
            start_head = str(start_head_value)
            frozen_main = str(frozen_main_value)
            self._require_clean_branch(branch, start_head)
            active = _active_git_operations(
                self.resolver, command_prefix="lck-refresh-preflight-operation"
            )
            if active:
                raise LckStopError(
                    "Candidate Refresh requires no active Git operation: "
                    + ", ".join(active)
                )
            self._ensure_main_object(frozen_main)

            ancestor = self._run(
                ["git", "merge-base", "--is-ancestor", frozen_main, start_head],
                "lck-refresh-already-current",
            )
            if ancestor.returncode == 0:
                tree = self.tree_effect.current_head_tree()
                progress.completed("already-current")
                return RefreshResult(
                    task_number=task_number,
                    operation_id=operation_id,
                    status="ALREADY_CURRENT",
                    action="no-op",
                    operation_snapshot=snapshot,
                    eligibility=decision,
                    branch=branch,
                    pr_number=pr_number,
                    start_head_sha=start_head,
                    frozen_main_sha=frozen_main,
                    head_sha=start_head,
                    validated_tree_oid=tree,
                )
            if ancestor.returncode != 1:
                raise LckStopError(
                    "cannot determine whether the Task branch contains current main"
                )

            merge_base_result = self._run(
                ["git", "merge-base", start_head, frozen_main],
                "lck-refresh-merge-base",
            )
            merge_base = merge_base_result.stdout.strip()
            if merge_base_result.returncode != 0 or not is_sha(merge_base):
                raise LckStopError(
                    "Candidate Refresh cannot resolve an exact merge base"
                )

            progress.running("rebasing-candidate")
            rebased = self._run(
                ["git", "rebase", "--onto", frozen_main, merge_base],
                "lck-refresh-rebase-current-main",
            )
            candidate_active = True
            if rebased.returncode != 0:
                conflicts = self._run(
                    ["git", "diff", "--name-only", "--diff-filter=U", "-z"],
                    "lck-refresh-rebase-conflicts",
                )
                if conflicts.returncode == 0:
                    self.last_conflict_files = tuple(
                        item for item in conflicts.stdout.split("\0") if item
                    )[:500]
                detail = stderr_tail(rebased.stderr or rebased.stdout)
                self._restore_original(branch, start_head)
                candidate_active = False
                raise LckStopError(
                    "Candidate Refresh rebase did not complete; original Task head restored"
                    + (f": {detail}" if detail else "")
                )

            new_head = self._current_head()
            if new_head == start_head:
                raise LckStopError("Candidate Refresh rebase did not create a new head")
            self._require_clean_branch(branch, new_head)
            contains_main = self._run(
                ["git", "merge-base", "--is-ancestor", frozen_main, new_head],
                "lck-refresh-rebased-main-ancestor",
            )
            task_diff = self._run(
                ["git", "diff", "--quiet", f"{frozen_main}...{new_head}"],
                "lck-refresh-rebased-task-diff",
            )
            if contains_main.returncode != 0 or task_diff.returncode != 1:
                raise LckStopError(
                    "Candidate Refresh rebased head does not preserve a Task diff on current main"
                )
            validated_tree = self.tree_effect.current_head_tree()
            progress.running("profile-gates")
            critical = self._run_profile_gates(
                state, frozen_main, new_head, progress=progress
            )
            progress.running("formal-validation")
            validation = self._run_formal_validation(frozen_main)
            if self.last_documentation_validation is not None:
                validation = dict(validation)
                validation["documentation_policy"] = self.last_documentation_validation
                self.last_validation = validation
            self.tree_effect.verify_tree_unchanged(
                validated_tree, expected_head_sha=new_head
            )

            current_main = self._remote_oid(
                "refs/heads/main", command_id="lck-refresh-main-before-push"
            )
            if current_main != frozen_main:
                raise LckStopError(
                    "Candidate Refresh origin/main changed after validation"
                )
            self._verify_pr_current(
                state,
                pr_number=pr_number,
                branch=branch,
                start_head=start_head,
                frozen_main=frozen_main,
            )

            prior_boundary = self.store.read_review_required(task_number)
            self.store.write_refresh_review_required(
                task_number, operation_id, new_head
            )
            boundary_written = True
            progress.running("exact-lease-push")
            pushed = self._push_exact_lease(branch, start_head, new_head)
            self.last_effects.append(pushed)
            candidate_active = False
            progress.completed("fresh-review-required")
            return RefreshResult(
                task_number=task_number,
                operation_id=operation_id,
                status="READY_FOR_FRESH_REVIEW",
                action="rebased-and-pushed",
                operation_snapshot=snapshot,
                eligibility=decision,
                branch=branch,
                pr_number=pr_number,
                start_head_sha=start_head,
                frozen_main_sha=frozen_main,
                head_sha=new_head,
                validated_tree_oid=validated_tree,
                critical_outcome=critical,
                profile_evidence=self.last_profile_evidence,
                validation=validation,
                effects=tuple(self.last_effects),
            )
        except BaseException:
            try:
                if boundary_written:
                    self.store.restore_review_required(task_number, prior_boundary)
                if candidate_active and branch and is_sha(start_head):
                    self._restore_original(branch, start_head)
            except BaseException as rollback_error:
                progress.failed("rollback-failed")
                raise LckStopError(
                    "Candidate Refresh failed and could not prove exact rollback: "
                    f"{rollback_error}"
                ) from rollback_error
            progress.failed()
            raise
