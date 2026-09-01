from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from project_status import set_project_status_with_runner
from workflow_common import WorkflowToolError, is_sha, read_json_text, safe_text
from workflow_evidence import _find_project_status

from .effects import DEFAULT_EFFECT_EXECUTOR_REGISTRY, EffectExecutorRegistry
from .eligibility import PhaseEligibilityResolver
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
    _merge_commit_sha,
    _pr_base_sha,
    _pr_head_sha,
    _remote_refs,
    branch_matches_profile,
)
from .profile_policies import (
    DEFAULT_PROFILE_POLICY_REGISTRY,
    PolicyContext,
    ProfileEvidenceEnvelope,
    ProfilePolicyError,
    ProfilePolicyRegistry,
    ProfileResolver,
    profile_cleanup_label,
    resolve_issue_policy,
    validate_profile_completion,
)
from .review_workspace import ReviewInvocationStore, _identity_from_mapping
from .state import LiveStateResolver, OperationSnapshotBuilder


def __getattr__(name: str) -> Any:
    """Resolve the removed Research helper only for legacy callers."""
    if name == "ResearchOutcomeEffect":
        from .profile_policies import ResearchOutcomeEffect

        return ResearchOutcomeEffect
    raise AttributeError(name)


def _label_names(issue: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(issue, Mapping):
        return set()
    raw = issue.get("labels")
    if isinstance(raw, Mapping):
        raw = raw.get("items")
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def _pending_receipt(
    effect: str,
    action: str,
    *,
    reason: str,
    details: Mapping[str, Any] | None = None,
) -> EffectReceipt:
    payload = {"reason": reason}
    if details:
        payload.update(details)
    return EffectReceipt(effect=effect, action=action, details=payload)


class MainSynchronizationEffect:
    """Fast-forward the local main to origin/main and prove merge reachability."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        state: LiveState,
        *,
        merge_sha: str | None,
    ) -> EffectReceipt:
        if not is_sha(merge_sha):
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="merged PR merge commit identity is unavailable",
            )
        if state.git.get("clean") is not True:
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="current worktree is not clean",
            )
        if state.git.get("branch") != BASE_BRANCH:
            switched = self.resolver.runner.run(
                ["git", "switch", BASE_BRANCH],
                command_id="lck-closeout-switch-main",
            )
            if switched.returncode != 0:
                return _pending_receipt(
                    "synchronize_main",
                    "pending",
                    reason="cannot switch to main",
                )
        fetched = self.resolver.runner.run(
            ["git", "fetch", "--prune", "origin"],
            command_id="lck-closeout-fetch-origin",
        )
        if fetched.returncode != 0:
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="cannot refresh origin/main",
            )
        merged = self.resolver.runner.run(
            ["git", "merge", "--ff-only", f"refs/remotes/origin/{BASE_BRANCH}"],
            command_id="lck-closeout-fast-forward-main",
        )
        if merged.returncode != 0:
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="local main cannot fast-forward to origin/main",
            )
        head = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-closeout-main-head",
        )
        origin = self.resolver.runner.run(
            ["git", "rev-parse", f"refs/remotes/origin/{BASE_BRANCH}"],
            command_id="lck-closeout-origin-main-head",
        )
        ancestry = self.resolver.runner.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                merge_sha,
                f"refs/remotes/origin/{BASE_BRANCH}",
            ],
            command_id="lck-closeout-merge-reachable",
        )
        if (
            head.returncode != 0
            or origin.returncode != 0
            or not is_sha(head.stdout.strip())
            or head.stdout.strip() != origin.stdout.strip()
            or ancestry.returncode != 0
        ):
            return _pending_receipt(
                "synchronize_main",
                "pending",
                reason="main synchronization postcondition is not proven",
            )
        return EffectReceipt(
            effect="synchronize_main",
            action="synchronized",
            details={"main_sha": head.stdout.strip(), "merge_sha": merge_sha},
        )


class CloseoutMetadataEffect:
    """Converge only the exact closed Task's Project status and lifecycle labels."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _query_metadata(
        self, repository: str, task_number: int
    ) -> tuple[str | None, str | None, set[str]]:
        result = self.resolver.runner.run(
            [
                "gh",
                "issue",
                "view",
                str(task_number),
                "--repo",
                repository,
                "--json",
                "state,labels,projectItems",
            ],
            command_id="lck-closeout-metadata-postcondition",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None, None, set()
        value = read_json_text(
            result.stdout, field="lck-closeout-metadata-postcondition"
        )
        if not isinstance(value, Mapping):
            return None, None, set()
        raw_labels = value.get("labels")
        labels: set[str] = set()
        if isinstance(raw_labels, list):
            for item in raw_labels:
                if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                    labels.add(str(item["name"]))
                elif isinstance(item, str):
                    labels.add(item)
        return (
            safe_text(value.get("state")),
            _find_project_status(value.get("projectItems")),
            labels,
        )

    def execute(self, state: LiveState) -> EffectReceipt:
        if state.issue_state != "CLOSED":
            return _pending_receipt(
                "converge_task_metadata",
                "pending",
                reason="Issue is not closed by authoritative GitHub state",
            )
        if state.repository is None:
            return _pending_receipt(
                "converge_task_metadata",
                "pending",
                reason="repository identity is unavailable",
            )
        actions: list[str] = []
        try:
            if state.project_status != "Done":
                set_project_status_with_runner(
                    self.resolver.runner,
                    state.repository,
                    state.task_number,
                    value="Done",
                )
                actions.append("project-status-done")
            labels = _label_names(state.issue)
            if "codex:ready" not in labels or "codex:blocked" in labels:
                label_result = self.resolver.runner.run(
                    [
                        "gh",
                        "issue",
                        "edit",
                        str(state.task_number),
                        "--repo",
                        state.repository,
                        "--add-label",
                        "codex:ready",
                        "--remove-label",
                        "codex:blocked",
                    ],
                    command_id="lck-closeout-lifecycle-labels",
                )
                if label_result.returncode != 0:
                    return _pending_receipt(
                        "converge_task_metadata",
                        "pending",
                        reason="lifecycle label convergence failed",
                        details={"actions": actions},
                    )
                actions.append("lifecycle-labels-converged")
        except WorkflowToolError:
            return _pending_receipt(
                "converge_task_metadata",
                "pending",
                reason="Project Status convergence failed",
                details={"actions": actions},
            )
        final_state, final_project_status, final_labels = self._query_metadata(
            state.repository, state.task_number
        )
        if (
            str(final_state or "").upper() != "CLOSED"
            or final_project_status != "Done"
            or "codex:ready" not in final_labels
            or "codex:blocked" in final_labels
        ):
            return _pending_receipt(
                "converge_task_metadata",
                "pending",
                reason="metadata convergence postcondition is not proven",
                details={"actions": actions},
            )
        return EffectReceipt(
            effect="converge_task_metadata",
            action="already-converged" if not actions else "updated",
            details={"actions": actions},
        )


class CleanupTaskRefsEffect:
    """Clean only the verified Task branch and recognize GitHub auto-deletion."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def execute(
        self,
        state: LiveState,
        *,
        expected_head_sha: str | None,
        merge_sha: str | None,
    ) -> EffectReceipt:
        branch = state.target_branch
        profile = resolve_leaf_issue_profile(state.issue).profile
        if (
            branch == BASE_BRANCH
            or profile is None
            or not branch_matches_profile(branch, state.task_number, profile)
        ):
            label = (
                profile_cleanup_label(profile) if profile is not None else "leaf Issue"
            )
            raise LckStopError(f"Cleanup target is not the verified {label} branch")
        worktrees = self.resolver.runner.run(
            ["git", "worktree", "list", "--porcelain"],
            command_id="lck-closeout-worktree-precondition",
        )
        if worktrees.returncode != 0:
            return _pending_receipt(
                "cleanup_task_refs",
                "pending",
                reason="current worktree ownership cannot be verified",
            )
        worktree_branches = {
            line.removeprefix("branch refs/heads/")
            for line in worktrees.stdout.splitlines()
            if line.startswith("branch refs/heads/")
        }
        if branch in worktree_branches:
            return _pending_receipt(
                "cleanup_task_refs",
                "pending",
                reason="verified Task branch is still used by a worktree",
            )

        if not is_sha(expected_head_sha) or not is_sha(merge_sha):
            raise LckStopError(
                "Cleanup STOP: merged PR head and squash merge identities are required"
            )
        tree_result = self.resolver.runner.run(
            ["git", "diff", "--quiet", expected_head_sha, merge_sha],
            command_id="lck-closeout-squash-tree-equality",
        )
        if tree_result.returncode != 0:
            raise LckStopError(
                "Cleanup STOP: PR head tree does not equal squash merge tree"
            )

        if state.local_task_branch is None and state.remote_task_branch is None:
            return EffectReceipt(
                effect="cleanup_task_refs",
                action="already-clean",
                details={"branch": branch, "remote_branch": "already-deleted"},
            )
        if state.local_task_branch is not None:
            local_result = self.resolver.runner.run(
                ["git", "rev-parse", f"refs/heads/{branch}"],
                command_id="lck-closeout-local-branch-tip",
            )
            local_tip = local_result.stdout.strip()
            if local_result.returncode != 0 or local_tip != expected_head_sha:
                raise LckStopError(
                    "Cleanup STOP: local Task branch diverges from merged PR head"
                )
            deleted = self.resolver.runner.run(
                ["git", "branch", "-d", branch],
                command_id="lck-closeout-local-branch-delete",
            )
            if deleted.returncode != 0:
                deleted = self.resolver.runner.run(
                    ["git", "branch", "-D", branch],
                    command_id="lck-closeout-local-branch-force-delete-after-proof",
                )
            if deleted.returncode != 0:
                return _pending_receipt(
                    "cleanup_task_refs",
                    "pending",
                    reason="verified local Task branch could not be deleted",
                )
        if state.remote_task_branch is not None:
            if state.remote_task_oid != expected_head_sha:
                raise LckStopError(
                    "Cleanup STOP: remote Task branch diverges from merged PR head"
                )
            remote_ref = self.resolver.runner.run(
                ["git", "ls-remote", "--heads", "origin", branch],
                command_id="lck-closeout-remote-branch-precondition",
            )
            if remote_ref.returncode != 0:
                return _pending_receipt(
                    "cleanup_task_refs",
                    "pending",
                    reason="remote Task branch precondition could not be verified",
                )
            remote_refs = _remote_refs(remote_ref.stdout)
            observed_remote_oid = remote_refs.get(branch)
            if observed_remote_oid is None:
                if remote_ref.stdout.strip():
                    raise LckStopError(
                        "Cleanup STOP: remote Task branch identity is malformed"
                    )
                return EffectReceipt(
                    effect="cleanup_task_refs",
                    action="already-clean",
                    details={"branch": branch, "remote_branch": "already-deleted"},
                )
            if observed_remote_oid != expected_head_sha:
                raise LckStopError(
                    "Cleanup STOP: remote Task branch diverges from merged PR head"
                )
            removed = self.resolver.runner.run(
                [
                    "git",
                    "push",
                    "origin",
                    "--delete",
                    branch,
                ],
                command_id="lck-closeout-remote-branch-delete",
            )
            if removed.returncode != 0:
                return _pending_receipt(
                    "cleanup_task_refs",
                    "pending",
                    reason="verified remote Task branch could not be deleted",
                )
            verified = self.resolver.runner.run(
                ["git", "ls-remote", "--heads", "origin", branch],
                command_id="lck-closeout-remote-branch-verify",
            )
            if verified.returncode != 0 or verified.stdout.strip():
                return _pending_receipt(
                    "cleanup_task_refs",
                    "pending",
                    reason="remote Task branch deletion postcondition is not proven",
                )
        return EffectReceipt(
            effect="cleanup_task_refs",
            action="cleaned",
            details={"branch": branch, "expected_head_sha": expected_head_sha},
        )


@dataclass(frozen=True)
class CloseoutResult:
    task_number: int
    status: str
    business_delivery: str
    cleanup: str
    effects: tuple[EffectReceipt, ...]
    operation_snapshot: OperationSnapshot
    research_outcome: str | None = None
    profile_evidence: ProfileEvidenceEnvelope | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "closeout",
            "task_number": self.task_number,
            "status": self.status,
            "issue_profile": _jsonable(self.operation_snapshot.state.issue_profile),
            "business_delivery": self.business_delivery,
            "cleanup": self.cleanup,
            "research_outcome": self.research_outcome,
            "profile_evidence": (
                self.profile_evidence.to_dict() if self.profile_evidence else None
            ),
            "effects": [item.to_dict() for item in self.effects],
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "automatic_merge": False,
            "manual_issue_close": False,
        }


class CloseoutCompleter:
    """Resolve and converge post-merge state without trusting prior snapshots."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        main_effect: MainSynchronizationEffect | None = None,
        metadata_effect: CloseoutMetadataEffect | None = None,
        cleanup_effect: CleanupTaskRefsEffect | None = None,
        review_store: ReviewInvocationStore | None = None,
        effect_registry: EffectExecutorRegistry | None = None,
        policy_registry: ProfilePolicyRegistry | None = None,
        profile_resolver: ProfileResolver | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.policy_registry = policy_registry or DEFAULT_PROFILE_POLICY_REGISTRY
        self.profile_resolver = (
            profile_resolver
            or getattr(eligibility, "profile_resolver", None)
            or resolve_leaf_issue_profile
        )
        self.eligibility = eligibility or PhaseEligibilityResolver(
            registry=self.policy_registry,
            profile_resolver=self.profile_resolver,
        )
        self.main_effect = main_effect or MainSynchronizationEffect(resolver)
        self.metadata_effect = metadata_effect or CloseoutMetadataEffect(resolver)
        self.cleanup_effect = cleanup_effect or CleanupTaskRefsEffect(resolver)
        self.effect_registry = effect_registry or DEFAULT_EFFECT_EXECUTOR_REGISTRY
        self.review_store = review_store or ReviewInvocationStore(resolver.repo_root)
        self.last_snapshot: OperationSnapshot | None = None
        self.last_effects: list[EffectReceipt] = []
        self.last_profile_evidence: ProfileEvidenceEnvelope | None = None

    @staticmethod
    def _validate_merged_identity(state: LiveState) -> tuple[str, str]:
        pr = state.merged_pr
        if not isinstance(pr, Mapping):
            raise LckStopError("Closeout STOP: merged PR identity is unavailable")
        if str(pr.get("state", "")).upper() != "MERGED":
            raise LckStopError("Closeout STOP: merged PR state is not MERGED")
        number = pr.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise LckStopError("Closeout STOP: merged PR number is unavailable")
        if pr.get("baseRefName") != BASE_BRANCH:
            raise LckStopError("Closeout STOP: merged PR base branch is not main")
        if pr.get("headRefName") != state.target_branch:
            raise LckStopError(
                "Closeout STOP: merged PR head branch is not the resolved Task branch"
            )
        expected_head = _pr_head_sha(pr)
        base_sha = _pr_base_sha(pr)
        merge_sha = _merge_commit_sha(pr)
        merged_at = pr.get("mergedAt", pr.get("merged_at"))
        if (
            expected_head is None
            or base_sha is None
            or merge_sha is None
            or not isinstance(merged_at, str)
            or not merged_at
        ):
            raise LckStopError("Closeout STOP: merged PR identity is incomplete")
        closing = pr.get("closingIssuesReferences")
        if not isinstance(closing, list):
            raise LckStopError(
                "Closeout STOP: merged PR closing-Task linkage is unavailable"
            )
        closing_numbers = [
            item.get("number")
            for item in closing
            if isinstance(item, Mapping) and isinstance(item.get("number"), int)
        ]
        if len(closing_numbers) != 1 or closing_numbers[0] != state.task_number:
            raise LckStopError(
                "Closeout STOP: merged PR does not close exactly this Task"
            )
        if state.issue_state == "CLOSED":
            closure = (
                state.issue.get("issue_closure")
                if isinstance(state.issue, Mapping)
                else None
            )
            if (
                not isinstance(closure, Mapping)
                or closure.get("evidence_status") != "complete"
                or closure.get("status") != "closed-by-pr"
                or closure.get("closer_repository") != state.repository
                or closure.get("closer_number") != number
            ):
                raise LckStopError(
                    "Closeout STOP: closed Task is not proven closed by the merged PR"
                )
        if state.local_task_head is not None and state.local_task_head != expected_head:
            raise LckStopError(
                "Closeout STOP: local Task branch diverges from merged PR head"
            )
        if state.remote_task_oid is not None and state.remote_task_oid != expected_head:
            raise LckStopError(
                "Closeout STOP: remote Task branch diverges from merged PR head"
            )
        return expected_head, merge_sha

    def _validate_reviewed_identity(
        self, state: LiveState, merged_pr: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        latest = self.review_store.read_latest_review(state.task_number)
        if not isinstance(latest, Mapping) or latest.get("verdict") != "PASS":
            raise LckStopError(
                "Closeout STOP: latest Independent Review PASS is unavailable"
            )
        review_id = latest.get("review_id")
        if not isinstance(review_id, str):
            raise LckStopError("Closeout STOP: latest Review PASS has no review id")
        record = self.review_store.read_record(state.task_number, review_id)
        if (
            record.get("task_number") != state.task_number
            or record.get("review_id") != review_id
            or record.get("verdict") != "PASS"
            or record.get("status") != "READY_FOR_MERGE_PREFLIGHT"
        ):
            raise LckStopError("Closeout STOP: latest Review PASS record is invalid")
        raw_identity = record.get("identity")
        if not isinstance(raw_identity, Mapping):
            raise LckStopError("Closeout STOP: Review PASS identity is unavailable")
        reviewed = _identity_from_mapping(raw_identity)
        current_contract = state.task_contract
        current_body_sha256 = (
            current_contract.get("body_sha256")
            if isinstance(current_contract, Mapping)
            else None
        )
        if current_body_sha256 != reviewed.task_body_sha256:
            raise LckStopError(
                "Closeout STOP: Review PASS is stale: Task Contract changed"
            )
        if (
            reviewed.task_number != state.task_number
            or reviewed.pr_number != merged_pr.get("number")
            or reviewed.base_sha != _pr_base_sha(merged_pr)
            or reviewed.head_sha != _pr_head_sha(merged_pr)
        ):
            raise LckStopError(
                "Closeout STOP: merged PR/head does not match the latest Review PASS"
            )
        return record

    def complete(self, task_number: int) -> CloseoutResult:
        effects: list[EffectReceipt] = []
        # Keep the same list object while effects run so any effect failure
        # still leaves already-completed effects visible to the failure path.
        self.last_effects = effects
        self.last_profile_evidence = None
        snapshot = self.snapshots.acquire(task_number, operation="closeout")
        self.last_snapshot = snapshot
        state = snapshot.state
        decision = self.eligibility.resolve(state, Phase.CLOSEOUT)
        if not decision.eligible:
            raise LckStopError(
                f"Closeout STOP for Task #{task_number}: " + "; ".join(decision.reasons)
            )
        expected_head, merge_sha = self._validate_merged_identity(state)
        if not isinstance(state.merged_pr, Mapping):
            raise LckStopError("Closeout STOP: merged PR identity is unavailable")
        review_record = self._validate_reviewed_identity(state, state.merged_pr)
        main = self.main_effect.execute(state, merge_sha=merge_sha)
        effects.append(main)

        metadata = self.metadata_effect.execute(state)
        effects.append(metadata)

        try:
            profile, _policy = resolve_issue_policy(
                state.issue or {},
                registry=self.policy_registry,
                profile_resolver=self.profile_resolver or resolve_leaf_issue_profile,
            )
            raw_envelope = review_record.get("profile_evidence")
            review_evidence = (
                raw_envelope.get("review")
                if isinstance(raw_envelope, Mapping)
                else None
            )
            completion = validate_profile_completion(
                profile,
                state.issue or {},
                {
                    "task_number": state.task_number,
                    "repository": state.repository,
                    "merged_pr": state.merged_pr,
                    "review_record": review_record,
                    "review_evidence": review_evidence,
                },
                registry=self.policy_registry,
                context=PolicyContext(
                    profile=profile,
                    phase="completion",
                    issue=state.issue,
                    runner=self.resolver.runner,
                    review_record=review_record,
                    merged_pr=state.merged_pr,
                ),
            )
        except (ProfilePolicyError, TypeError, ValueError) as exc:
            raise LckStopError(
                f"profile completion capability rejected the merge: {exc}"
            ) from exc

        self.last_profile_evidence = completion.profile_evidence
        completion_effect: EffectReceipt | None = None
        if completion.effect is not None:
            if main.action in {"synchronized", "already-synced"}:
                completion_effect = self.effect_registry.execute(
                    completion.effect,
                    resolver=self.resolver,
                    state=state,
                )
            else:
                completion_effect = _pending_receipt(
                    completion.effect.effect_kind,
                    "pending",
                    reason="main synchronization is incomplete",
                )
            effects.append(completion_effect)

        if main.action in {"synchronized", "already-synced"}:
            cleanup = self.cleanup_effect.execute(
                state,
                expected_head_sha=expected_head,
                merge_sha=merge_sha,
            )
        else:
            cleanup = _pending_receipt(
                "cleanup_task_refs",
                "pending",
                reason="main synchronization is incomplete",
            )
        effects.append(cleanup)
        cleanup_complete = (
            main.action in {"synchronized", "already-synced"}
            and metadata.action in {"updated", "already-converged"}
            and cleanup.action in {"cleaned", "already-clean"}
        )
        completion_complete = completion_effect is None or completion_effect.action in {
            "updated",
            "already-set",
            "not-applicable",
        }
        return CloseoutResult(
            task_number=task_number,
            status="BUSINESS_DELIVERY_COMPLETE",
            business_delivery="COMPLETE" if completion_complete else "PENDING",
            cleanup="COMPLETE" if cleanup_complete else "PENDING",
            effects=tuple(effects),
            operation_snapshot=snapshot,
            research_outcome=(
                completion_effect.details.get("outcome")
                if completion_effect is not None
                else None
            ),
            profile_evidence=completion.profile_evidence,
        )
