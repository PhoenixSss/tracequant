from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .common import CommandResult, is_sha, stderr_tail
from .delivery import DeliveryCompleter, DeliveryCompletionResult
from .effects import ReuseExistingOpenPrEffect
from .eligibility import PhaseDecision, PhaseEligibilityResolver
from .models import (
    LCK_SCHEMA_VERSION,
    LckStopError,
    LiveState,
    OperationSnapshot,
    Phase,
    _jsonable,
    _pr_base_sha,
    _pr_head_sha,
    _remote_main_sha,
)
from .review_workspace import ReviewInvocationStore
from .state import (
    LiveStateResolver,
    OperationSnapshotBuilder,
    _leaf_contract_from_state,
)


def _lines_nul(value: str) -> tuple[str, ...]:
    items = tuple(item for item in value.split("\0") if item)
    if len(items) > 500:
        raise LckStopError("Candidate Refresh path inventory exceeds the bounded limit")
    return items


_GIT_OPERATION_MARKERS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "REBASE_HEAD",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "BISECT_START",
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
class RefreshContext:
    task_number: int
    status: str
    action: str
    operation_id: str | None
    operation_snapshot: OperationSnapshot
    eligibility: PhaseDecision
    start_head_sha: str
    frozen_main_sha: str
    pr_number: int
    conflict_files: tuple[str, ...] = ()
    candidate_paths: tuple[str, ...] = ()

    @property
    def state(self) -> LiveState:
        return self.operation_snapshot.state

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "refresh-prepare",
            "status": self.status,
            "action": self.action,
            "task_number": self.task_number,
            "operation_id": self.operation_id,
            "issue_profile": _jsonable(self.state.issue_profile),
            "task_contract": _jsonable(_leaf_contract_from_state(self.state)),
            "branch": self.state.target_branch,
            "pr_number": self.pr_number,
            "start_head_sha": self.start_head_sha,
            "frozen_main_sha": self.frozen_main_sha,
            "merge_parents": [self.start_head_sha, self.frozen_main_sha],
            "conflict_files": list(self.conflict_files),
            "candidate_paths": list(self.candidate_paths),
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "eligibility": self.eligibility.to_dict(),
            "human_boundary": (
                "resolve only the reported integration conflicts, inspect the candidate, "
                "and run change-relevant targeted feedback before Refresh Complete"
                if self.conflict_files
                else "inspect the integrated candidate and run change-relevant targeted "
                "feedback before Refresh Complete"
            ),
        }


class RefreshPreparer:
    """Prepare one LCK-owned, no-commit merge of current main into a Review branch."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.last_snapshot: OperationSnapshot | None = None
        self.last_session: dict[str, Any] | None = None

    def _run(self, argv: Sequence[str], command_id: str) -> CommandResult:
        return self.resolver.runner.run(argv, command_id=command_id)

    def _require_no_overlap(self, task_number: int) -> None:
        if self.store.read_refresh_session(task_number) is not None:
            raise LckStopError(
                "Candidate Refresh Prepare STOP: a Refresh session is active"
            )
        if self.store.read_remediation_session(task_number) is not None:
            raise LckStopError(
                "Candidate Refresh Prepare STOP: a Remediation session is active"
            )
        if self.store.review_prepare_active(task_number):
            raise LckStopError(
                "Candidate Refresh Prepare STOP: a Review Prepare/handoff is active"
            )

    def _reject_applicable_review_fail(self, task_number: int, head_sha: str) -> None:
        latest = self.store.read_latest_review(task_number)
        if not isinstance(latest, Mapping) or latest.get("verdict") != "FAIL":
            return
        review_id = latest.get("review_id")
        if not isinstance(review_id, str):
            raise LckStopError("latest Review FAIL identity is invalid")
        record = self.store.read_record(task_number, review_id)
        identity = record.get("identity")
        if not isinstance(identity, Mapping) or not is_sha(identity.get("head_sha")):
            raise LckStopError("latest Review FAIL applicability is unavailable")
        if identity.get("head_sha") == head_sha:
            raise LckStopError(
                "Candidate Refresh cannot bypass applicable Review FAIL findings; "
                "start explicit Remediation with the failed review id"
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

    def _require_no_git_operation(self) -> None:
        active = _active_git_operations(
            self.resolver, command_prefix="lck-refresh-prepare-git-operation"
        )
        if active:
            raise LckStopError(
                "Refresh Prepare requires no pre-existing Git operation: "
                + ", ".join(active)
            )

    def _recover_failed_merge_start(
        self,
        task_number: int,
        *,
        start_head: str,
        frozen_main: str,
    ) -> None:
        """Release only a failed merge start whose state is still provably ours."""
        active = _active_git_operations(
            self.resolver, command_prefix="lck-refresh-prepare-recovery-operation"
        )
        head = self._run(
            ["git", "rev-parse", "HEAD"], "lck-refresh-prepare-recovery-head"
        )
        if head.returncode != 0 or head.stdout.strip() != start_head:
            return
        if not active:
            status = self._run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                "lck-refresh-prepare-recovery-status",
            )
            if status.returncode == 0 and not status.stdout.strip():
                self.store.clear_refresh_session(task_number)
            return
        if active != ("MERGE_HEAD",):
            return
        merge_head = self._run(
            ["git", "rev-parse", "MERGE_HEAD"],
            "lck-refresh-prepare-recovery-merge-head",
        )
        if merge_head.returncode != 0 or merge_head.stdout.strip() != frozen_main:
            return
        aborted = self._run(["git", "merge", "--abort"], "lck-refresh-prepare-recover")
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-refresh-prepare-recovery-clean",
        )
        if (
            aborted.returncode == 0
            and status.returncode == 0
            and not status.stdout.strip()
        ):
            self.store.clear_refresh_session(task_number)

    def prepare(self, task_number: int) -> RefreshContext:
        self._require_no_overlap(task_number)
        snapshot = self.snapshots.acquire(
            task_number, operation=Phase.REFRESH_PREPARE.value
        )
        self.last_snapshot = snapshot
        state = snapshot.state
        decision = self.eligibility.resolve(state, Phase.REFRESH_PREPARE)
        if not decision.eligible:
            raise LckStopError(
                f"Refresh Prepare STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )
        if state.git.get("clean") is not True:
            raise LckStopError("Refresh Prepare requires a clean Task worktree")
        start_head = state.local_issue_head
        frozen_main = _remote_main_sha(state.git)
        pr = state.open_pr
        if (
            not is_sha(start_head)
            or not is_sha(frozen_main)
            or not isinstance(pr, Mapping)
        ):
            raise LckStopError("Refresh Prepare identity is incomplete")
        pr_number = pr.get("number")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool):
            raise LckStopError("Refresh Prepare PR number is unavailable")
        issue = state.issue
        body_sha = issue.get("body_sha256") if isinstance(issue, Mapping) else None
        if not isinstance(body_sha, str) or not body_sha:
            raise LckStopError("Refresh Prepare Task Contract identity is unavailable")
        self._reject_applicable_review_fail(task_number, start_head)
        # A clean porcelain view does not prove that no merge/rebase/cherry-pick
        # operation is active.  Establish this before fetching, writing a
        # session, or invoking merge so later recovery cannot touch prior state.
        self._require_no_git_operation()
        self._ensure_main_object(frozen_main)

        ancestor = self._run(
            ["git", "merge-base", "--is-ancestor", frozen_main, start_head],
            "lck-refresh-already-current",
        )
        if ancestor.returncode == 0:
            return RefreshContext(
                task_number=task_number,
                status="ALREADY_CURRENT",
                action="no-op",
                operation_id=None,
                operation_snapshot=snapshot,
                eligibility=decision,
                start_head_sha=start_head,
                frozen_main_sha=frozen_main,
                pr_number=pr_number,
            )
        if ancestor.returncode != 1:
            raise LckStopError(
                "cannot determine whether the Task branch contains current main"
            )

        paths_result = self._run(
            ["git", "diff", "--name-only", "-z", start_head, frozen_main],
            "lck-refresh-candidate-paths",
        )
        if paths_result.returncode != 0:
            raise LckStopError("cannot inventory the bounded main integration paths")
        candidate_paths = _lines_nul(paths_result.stdout)
        operation_id = self.store.new_id()
        session: dict[str, Any] = {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "candidate-refresh-session",
            "operation_id": operation_id,
            "task_number": task_number,
            "repository": state.repository,
            "branch": state.target_branch,
            "pr_number": pr_number,
            "task_body_sha256": body_sha,
            "start_head_sha": start_head,
            "frozen_main_sha": frozen_main,
            "remote_head_sha": state.remote_issue_oid,
            "pr_head_sha": _pr_head_sha(pr),
            "pr_base_sha": _pr_base_sha(pr),
            "merge_parents": [start_head, frozen_main],
            "candidate_paths": list(candidate_paths),
            "prepared_state": "merge-starting",
            "candidate": None,
            "authority": "LCK-owned Candidate Refresh session only",
        }
        self.store.write_refresh_session(task_number, session)
        self.last_session = session
        merged = self._run(
            ["git", "merge", "--no-commit", "--no-ff", frozen_main],
            "lck-refresh-merge-main",
        )
        conflicts_result = self._run(
            ["git", "diff", "--name-only", "--diff-filter=U", "-z"],
            "lck-refresh-conflict-inventory",
        )
        if conflicts_result.returncode != 0:
            raise LckStopError("cannot inventory Candidate Refresh conflicts")
        conflicts = _lines_nul(conflicts_result.stdout)
        if merged.returncode not in {0, 1} or (
            merged.returncode == 1 and not conflicts
        ):
            self._recover_failed_merge_start(
                task_number,
                start_head=start_head,
                frozen_main=frozen_main,
            )
            raise LckStopError(
                "Candidate Refresh merge failed without a bounded conflict candidate: "
                + (
                    stderr_tail(merged.stderr or merged.stdout)
                    or f"exit {merged.returncode}"
                )
            )
        merge_head = self._run(
            ["git", "rev-parse", "MERGE_HEAD"], "lck-refresh-merge-head"
        )
        current_head = self._run(["git", "rev-parse", "HEAD"], "lck-refresh-start-head")
        if (
            merge_head.returncode != 0
            or merge_head.stdout.strip() != frozen_main
            or current_head.returncode != 0
            or current_head.stdout.strip() != start_head
        ):
            raise LckStopError("prepared Candidate Refresh merge parents are not exact")
        session["prepared_state"] = "conflicts" if conflicts else "merged"
        session["conflict_files"] = list(conflicts)
        self.store.write_refresh_session(task_number, session)
        self.last_session = session
        return RefreshContext(
            task_number=task_number,
            status=("REFRESH_CONFLICTS" if conflicts else "READY_FOR_REFRESH_COMPLETE"),
            action="prepared-no-commit-merge",
            operation_id=operation_id,
            operation_snapshot=snapshot,
            eligibility=decision,
            start_head_sha=start_head,
            frozen_main_sha=frozen_main,
            pr_number=pr_number,
            conflict_files=conflicts,
            candidate_paths=candidate_paths,
        )


@dataclass(frozen=True)
class RefreshCompletionResult:
    task_number: int
    operation_id: str
    start_head_sha: str
    frozen_main_sha: str
    validated_tree_oid: str
    delivery: DeliveryCompletionResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "refresh-complete",
            "status": "READY_FOR_FRESH_REVIEW",
            "task_number": self.task_number,
            "operation_id": self.operation_id,
            "branch": self.delivery.branch,
            "start_head_sha": self.start_head_sha,
            "frozen_main_sha": self.frozen_main_sha,
            "merge_parents": [self.start_head_sha, self.frozen_main_sha],
            "validated_tree_oid": self.validated_tree_oid,
            "head_sha": self.delivery.head_sha,
            "critical_outcome": _jsonable(self.delivery.critical_outcome),
            "profile_evidence": (
                self.delivery.profile_evidence.to_dict()
                if self.delivery.profile_evidence is not None
                else None
            ),
            "validation": _jsonable(self.delivery.validation),
            "checks": _jsonable(self.delivery.checks),
            "effects": [effect.to_dict() for effect in self.delivery.effects],
            "operation_snapshot": self.delivery.operation_snapshot.to_dict(),
            "fresh_review_required": True,
            "automatic_review": False,
            "automatic_merge": False,
        }


class RefreshCompleter:
    """Validate, commit, and publish one exact prepared main merge candidate."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        store: ReviewInvocationStore | None = None,
        delivery_factory: Any = DeliveryCompleter,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.delivery_factory = delivery_factory
        self.last_snapshot: OperationSnapshot | None = None
        self.last_session: dict[str, Any] | None = None
        self.last_effects: list[Any] = []
        self.last_validation: dict[str, Any] | None = None
        self.last_checks: dict[str, Any] | None = None
        self.last_critical_outcome: dict[str, Any] | None = None
        self.last_documentation_validation: dict[str, Any] | None = None
        self.last_profile_evidence: Any = None

    def _capture_delivery_evidence(self, delivery: Any) -> None:
        snapshot = getattr(delivery, "last_snapshot", None)
        if isinstance(snapshot, OperationSnapshot):
            self.last_snapshot = snapshot
        for source, target in (
            ("last_validation", "last_validation"),
            ("last_checks", "last_checks"),
            ("last_critical_outcome", "last_critical_outcome"),
            ("last_documentation_validation", "last_documentation_validation"),
        ):
            value = getattr(delivery, source, None)
            if isinstance(value, Mapping):
                setattr(self, target, dict(value))
        self.last_profile_evidence = getattr(
            delivery, "last_profile_evidence", self.last_profile_evidence
        )
        effects = getattr(delivery, "last_effects", None)
        if isinstance(effects, list):
            self.last_effects = effects

    def _run(self, argv: Sequence[str], command_id: str) -> CommandResult:
        return self.resolver.runner.run(argv, command_id=command_id)

    def _verify_commit(
        self, head_sha: str, tree_oid: str, start_head: str, frozen_main: str
    ) -> None:
        parents = self._run(
            ["git", "rev-list", "--parents", "-n", "1", head_sha],
            "lck-refresh-commit-parents",
        )
        tree = self._run(
            ["git", "rev-parse", f"{head_sha}^{{tree}}"],
            "lck-refresh-commit-tree",
        )
        if (
            parents.returncode != 0
            or parents.stdout.strip().split() != [head_sha, start_head, frozen_main]
            or tree.returncode != 0
            or tree.stdout.strip() != tree_oid
        ):
            raise LckStopError(
                "Candidate Refresh commit does not have the exact owned parents/tree"
            )

    def _verify_remote_main_current(self, frozen_main: str) -> None:
        result = self._run(
            ["git", "ls-remote", "--heads", "origin", "refs/heads/main"],
            "lck-refresh-main-before-push",
        )
        lines = [line.split() for line in result.stdout.splitlines() if line.strip()]
        if (
            result.returncode != 0
            or len(lines) != 1
            or len(lines[0]) != 2
            or lines[0][0] != frozen_main
            or lines[0][1] != "refs/heads/main"
        ):
            raise LckStopError(
                "Candidate Refresh origin/main changed after validation and before push"
            )

    def _assert_session_live(
        self, task_number: int, session: Mapping[str, Any], state: LiveState
    ) -> tuple[str, str, str, int]:
        operation_id = session.get("operation_id")
        start_head = session.get("start_head_sha")
        frozen_main = session.get("frozen_main_sha")
        pr_number = session.get("pr_number")
        issue = state.issue
        body_sha = issue.get("body_sha256") if isinstance(issue, Mapping) else None
        pr = state.open_pr
        if (
            not isinstance(operation_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None
            or not is_sha(start_head)
            or not is_sha(frozen_main)
            or not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or not isinstance(pr, Mapping)
            or state.repository != session.get("repository")
            or body_sha != session.get("task_body_sha256")
            or state.target_branch != session.get("branch")
            or _remote_main_sha(state.git) != frozen_main
            or _pr_base_sha(pr) != frozen_main
            or pr.get("number") != pr_number
        ):
            raise LckStopError("Candidate Refresh live authority changed since Prepare")
        candidate = session.get("candidate")
        candidate_head = (
            candidate.get("head_sha") if isinstance(candidate, Mapping) else None
        )
        allowed_heads = {start_head}
        if is_sha(candidate_head):
            allowed_heads.add(candidate_head)
        if (
            state.local_issue_head not in allowed_heads
            or state.remote_issue_oid not in allowed_heads
            or _pr_head_sha(pr) not in allowed_heads
            or state.remote_issue_oid != _pr_head_sha(pr)
        ):
            raise LckStopError("Candidate Refresh Task/remote/PR head identity drifted")
        if (
            state.local_issue_head != start_head
            and state.local_issue_head != candidate_head
        ):
            raise LckStopError("local Candidate Refresh head is not LCK-owned")
        return operation_id, start_head, frozen_main, pr_number

    def _verify_precommit_candidate(
        self, session: Mapping[str, Any], start_head: str, frozen_main: str
    ) -> None:
        conflicts = self._run(
            ["git", "diff", "--name-only", "--diff-filter=U", "-z"],
            "lck-refresh-complete-conflicts",
        )
        if conflicts.returncode != 0 or _lines_nul(conflicts.stdout):
            raise LckStopError("Candidate Refresh has unresolved merge conflicts")
        merge_head = self._run(
            ["git", "rev-parse", "MERGE_HEAD"], "lck-refresh-complete-merge-head"
        )
        head = self._run(["git", "rev-parse", "HEAD"], "lck-refresh-complete-head")
        unstaged = self._run(
            ["git", "diff", "--quiet"], "lck-refresh-complete-unstaged"
        )
        status = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-refresh-complete-status",
        )
        if (
            merge_head.returncode != 0
            or merge_head.stdout.strip() != frozen_main
            or head.returncode != 0
            or head.stdout.strip() != start_head
            or unstaged.returncode != 0
            or status.returncode != 0
            or any(line.startswith("??") for line in status.stdout.splitlines())
        ):
            raise LckStopError(
                "Candidate Refresh merge state is unresolved, changed, or contains untracked input"
            )

    def complete(
        self,
        task_number: int,
        *,
        commit_message: str,
        summary: str,
        risks: str = "",
    ) -> RefreshCompletionResult:
        session = self.store.read_refresh_session(task_number)
        if session is None:
            raise LckStopError("Refresh Complete requires a prepared Refresh session")
        self.last_session = session
        if self.store.read_remediation_session(task_number) is not None:
            raise LckStopError("Refresh Complete STOP: a Remediation session is active")
        if self.store.review_prepare_active(task_number):
            raise LckStopError(
                "Refresh Complete STOP: a Review Prepare/handoff is active"
            )
        snapshot = self.snapshots.acquire(
            task_number,
            operation=Phase.REFRESH_COMPLETE.value,
            include_required_checks=True,
        )
        self.last_snapshot = snapshot
        operation_id, start_head, frozen_main, _pr_number = self._assert_session_live(
            task_number, session, snapshot.state
        )
        candidate = session.get("candidate")
        owned_refresh_candidate = isinstance(candidate, Mapping)
        if isinstance(candidate, Mapping):
            candidate_head = candidate.get("head_sha")
            candidate_tree = candidate.get("tree_oid")
            if not is_sha(candidate_head) or not is_sha(candidate_tree):
                raise LckStopError("owned Candidate Refresh commit identity is invalid")
            self._verify_commit(candidate_head, candidate_tree, start_head, frozen_main)
            if (
                snapshot.state.local_issue_head != candidate_head
                or snapshot.state.git.get("clean") is not True
            ):
                raise LckStopError(
                    "owned Candidate Refresh recovery workspace is not exact"
                )
        else:
            self._verify_precommit_candidate(session, start_head, frozen_main)

        validated_tree: dict[str, str] = {}

        def record_candidate(head_sha: str, tree_oid: str) -> None:
            self._verify_commit(head_sha, tree_oid, start_head, frozen_main)
            self.store.record_refresh_candidate(
                task_number,
                operation_id,
                start_head_sha=start_head,
                frozen_main_sha=frozen_main,
                candidate_head_sha=head_sha,
                candidate_tree_oid=tree_oid,
            )
            validated_tree["oid"] = tree_oid
            self._verify_remote_main_current(frozen_main)

        delivery = self.delivery_factory(
            self.resolver,
            pr_effect=ReuseExistingOpenPrEffect(
                self.resolver, operation_label="Candidate Refresh"
            ),
            require_existing_open_pr=True,
            candidate_recorder=record_candidate,
        )
        try:
            result = delivery.complete(
                task_number,
                commit_message=commit_message,
                summary=summary,
                risks=risks,
                operation_snapshot=snapshot,
                phase=Phase.REFRESH_COMPLETE,
                owned_refresh_candidate=owned_refresh_candidate,
            )
        except BaseException:
            self._capture_delivery_evidence(delivery)
            raise
        self._capture_delivery_evidence(delivery)
        tree_oid = validated_tree.get("oid")
        if not is_sha(tree_oid):
            raise LckStopError(
                "Refresh Complete did not record the validated merge tree"
            )
        self.store.write_refresh_review_required(
            task_number, operation_id, result.head_sha
        )
        self.store.clear_refresh_session(task_number)
        return RefreshCompletionResult(
            task_number=task_number,
            operation_id=operation_id,
            start_head_sha=start_head,
            frozen_main_sha=frozen_main,
            validated_tree_oid=tree_oid,
            delivery=result,
        )


@dataclass(frozen=True)
class RefreshAbortResult:
    task_number: int
    operation_id: str
    start_head_sha: str
    frozen_main_sha: str
    operation_snapshot: OperationSnapshot
    aborted_session: Mapping[str, Any]
    status: str = "REFRESH_ABORTED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "refresh-abort",
            "status": self.status,
            "task_number": self.task_number,
            "operation_id": self.operation_id,
            "start_head_sha": self.start_head_sha,
            "frozen_main_sha": self.frozen_main_sha,
            "session_released": True,
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "aborted_session": _jsonable(self.aborted_session),
        }


class RefreshAborter:
    """Abort only the still-uncommitted merge owned by one Refresh session."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.last_snapshot: OperationSnapshot | None = None
        self.last_session: dict[str, Any] | None = None

    def _run(self, argv: Sequence[str], command_id: str) -> CommandResult:
        return self.resolver.runner.run(argv, command_id=command_id)

    def abort(self, task_number: int) -> RefreshAbortResult:
        session = self.store.read_refresh_session(task_number)
        if session is None:
            raise LckStopError("Refresh Abort requires a prepared Refresh session")
        self.last_session = session
        if session.get("candidate") is not None:
            raise LckStopError(
                "Refresh Abort cannot discard an already committed candidate"
            )
        snapshot = self.snapshots.acquire(
            task_number, operation=Phase.REFRESH_ABORT.value
        )
        self.last_snapshot = snapshot
        state = snapshot.state
        operation_id = session.get("operation_id")
        start_head = session.get("start_head_sha")
        frozen_main = session.get("frozen_main_sha")
        if (
            not isinstance(operation_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None
            or not is_sha(start_head)
            or not is_sha(frozen_main)
            or state.repository != session.get("repository")
            or state.target_branch != session.get("branch")
            or not isinstance(state.issue, Mapping)
            or state.issue.get("body_sha256") != session.get("task_body_sha256")
            or (state.open_pr or {}).get("number") != session.get("pr_number")
            or state.local_issue_head != start_head
            or state.remote_issue_oid != session.get("remote_head_sha")
            or _pr_head_sha(state.open_pr) != session.get("pr_head_sha")
        ):
            raise LckStopError("Refresh Abort live/session identity does not match")
        merge_head = self._run(
            ["git", "rev-parse", "MERGE_HEAD"], "lck-refresh-abort-merge-head"
        )
        active = _active_git_operations(
            self.resolver, command_prefix="lck-refresh-abort-git-operation"
        )
        if session.get("prepared_state") == "merge-starting" and not active:
            status = self._run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                "lck-refresh-abort-unstarted-status",
            )
            if status.returncode != 0 or status.stdout.strip():
                raise LckStopError(
                    "Refresh Abort cannot release an interrupted merge start with external input"
                )
            self.store.clear_refresh_session(task_number)
            return RefreshAbortResult(
                task_number=task_number,
                operation_id=operation_id,
                start_head_sha=start_head,
                frozen_main_sha=frozen_main,
                operation_snapshot=snapshot,
                aborted_session=dict(session),
            )
        if (
            active != ("MERGE_HEAD",)
            or merge_head.returncode != 0
            or merge_head.stdout.strip() != frozen_main
        ):
            raise LckStopError("Refresh Abort cannot prove the LCK-owned merge state")
        status = self._run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            "lck-refresh-abort-status",
        )
        if status.returncode != 0:
            raise LckStopError("Refresh Abort cannot inspect the current merge state")
        entries = _lines_nul(status.stdout)
        if any(entry.startswith("??") for entry in entries):
            raise LckStopError("Refresh Abort refuses to discard untracked user input")
        owned_paths = set(cast(list[str], session.get("candidate_paths", [])))
        observed_paths = {entry[3:] for entry in entries if len(entry) > 3}
        if not observed_paths.issubset(owned_paths):
            raise LckStopError(
                "Refresh Abort refuses to discard paths outside its owned merge"
            )
        aborted = self._run(["git", "merge", "--abort"], "lck-refresh-abort-merge")
        head = self._run(["git", "rev-parse", "HEAD"], "lck-refresh-abort-head")
        clean = self._run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            "lck-refresh-abort-clean",
        )
        if (
            aborted.returncode != 0
            or head.returncode != 0
            or head.stdout.strip() != start_head
            or clean.returncode != 0
            or clean.stdout.strip()
        ):
            raise LckStopError("Refresh Abort postcondition failed")
        self.store.clear_refresh_session(task_number)
        return RefreshAbortResult(
            task_number=task_number,
            operation_id=operation_id,
            start_head_sha=start_head,
            frozen_main_sha=frozen_main,
            operation_snapshot=snapshot,
            aborted_session=dict(session),
        )
