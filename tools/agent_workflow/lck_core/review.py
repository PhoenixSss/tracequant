from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from research_policy import (
    ResearchPolicyError,
    bind_research_outcome,
    require_typed_research_outcome,
)
from workflow_common import ProgressReporter, is_sha, safe_text
from workflow_evidence import _formal_blockers_gate

from .eligibility import PhaseEligibilityResolver
from .issue_profiles import resolve_issue_profile
from .models import (
    BASE_BRANCH,
    LCK_SCHEMA_VERSION,
    LckStopError,
    LiveState,
    OperationSnapshot,
    Phase,
    ResolutionStatus,
    ReviewStaleError,
    _jsonable,
    _pr_base_sha,
    _pr_head_sha,
)
from .profile_policies import (
    evaluate_profile_changes,
    profile_research_outcome_supported,
)
from .review_workspace import (
    ReviewIdentity,
    ReviewInvocationStore,
    ReviewWorkspaceManager,
    _assert_review_applicable,
    _assert_review_target_facts_applicable,
    _identity_from_mapping,
    _review_identity,
    _review_target_refs,
)
from .state import (
    LiveStateResolver,
    OperationSnapshotBuilder,
    _task_contract_from_state,
)
from .validation import (
    DeliveryChecksGate,
    DocumentationReclassificationRequired,
    ReviewValidationGate,
)


@dataclass(frozen=True)
class ReviewContext:
    review_id: str
    task_contract: Mapping[str, Any]
    identity: ReviewIdentity
    checks: Mapping[str, Any]
    validation: Mapping[str, Any]
    review_root: Path
    issue_profile: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "review-prepare",
            "status": "READY_FOR_SEMANTIC_REVIEW",
            "review_id": self.review_id,
            "issue_profile": _jsonable(self.issue_profile),
            "task_contract": _jsonable(self.task_contract),
            "review_target": self.identity.to_dict(),
            "checks": _jsonable(self.checks),
            "validation": _jsonable(self.validation),
            "review_root": str(self.review_root),
            "workspace_mode": "implementation-read-only",
            "agent_role": ["Inspect", "Reason", "Judge", "Report"],
            "mechanical_authority": "live Git/GitHub state resolved by LCK",
            "forbidden_handoff_authority": [
                "Delivery SHA",
                "Delivery base SHA",
                "Delivery PR identity",
                "Delivery checks snapshot",
                "Delivery validation snapshot",
            ],
        }


def _review_validation_failure(payload: Mapping[str, Any]) -> str:
    failed_command: Mapping[str, Any] | None = None
    commands = payload.get("commands")
    if isinstance(commands, list):
        failed_command = next(
            (
                item
                for item in commands
                if isinstance(item, Mapping) and item.get("status") == "fail"
            ),
            None,
        )
    evidence = payload.get("evidence_path") or payload.get("output_dir")
    detail = [f"formal Review validation failed: {payload.get('status', 'fail')}"]
    if failed_command is not None:
        command_id = failed_command.get("command_id", "unknown")
        exit_code = failed_command.get("exit_code", "unknown")
        diagnostic = safe_text(failed_command.get("diagnostic"), limit=1200)
        detail.append(f"failed command {command_id} (exit {exit_code})")
        if diagnostic:
            detail.append(f"diagnostic: {diagnostic}")
    base_sha = payload.get("validated_base_sha")
    head_sha = payload.get("validated_head_sha")
    if is_sha(base_sha) and is_sha(head_sha):
        detail.append(f"validated base {base_sha}, head {head_sha}")
    if isinstance(evidence, str) and evidence:
        detail.append(f"evidence: {evidence}")
    return "; ".join(detail)


class ReviewPreparer:
    """Resolve a fresh review target and construct one bounded read-only context."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        validation: ReviewValidationGate | None = None,
        checks_gate: DeliveryChecksGate | None = None,
        workspace: ReviewWorkspaceManager | None = None,
        store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.validation = validation or ReviewValidationGate(resolver)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)
        self.workspace = workspace or ReviewWorkspaceManager(resolver)
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.last_snapshot: OperationSnapshot | None = None
        self.last_validation: dict[str, Any] | None = None
        self.last_documentation_validation: dict[str, Any] | None = None

    def prepare(self, task_number: int) -> ReviewContext:
        self.last_validation = None
        if self.store.read_remediation_session(task_number) is not None:
            raise LckStopError(
                "Review Prepare STOP: a prepared Remediation session must be completed "
                "or closed with remediation no-change first"
            )
        invocation = self.store.begin_review_prepare(task_number)
        review_root: Path | None = None
        progress = ProgressReporter("review-prepare")
        last_stage = "initializing"

        def mark(state: str, **fields: Any) -> None:
            nonlocal last_stage
            payload: dict[str, Any] = {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "review-prepare-in-flight",
                "operation_id": invocation.operation_id,
                "task_number": task_number,
                "pid": os.getpid(),
                "state": state,
                "review_root": str(review_root) if review_root else None,
                "authority": "operation-local in-flight protection only",
            }
            payload.update(fields)
            invocation.update(payload)
            if state == "failed":
                progress.failed(last_stage)
            elif state == "handed-off":
                progress.completed("handoff")
            elif state == "started":
                progress.started("initializing")
            else:
                progress.running(state)
            last_stage = state

        try:
            mark("started")
            recovered = invocation.recovered
            if recovered is not None:
                previous_root = recovered.get("review_root")
                if previous_root is not None:
                    if not isinstance(previous_root, str) or not previous_root:
                        raise LckStopError(
                            "stale Review Prepare has an invalid isolated clone path"
                        )
                    previous_path = Path(previous_root)
                    if previous_path.exists():
                        self.workspace.remove_recovered(previous_path)
                previous_review_id = recovered.get("review_id")
                if isinstance(previous_review_id, str):
                    self.store.delete_guard(previous_review_id)
            mark("resolving-live-state")
            snapshot = self.snapshots.acquire(
                task_number,
                operation="review-prepare",
            )
            self.last_snapshot = snapshot
            self.last_documentation_validation = None
            state = snapshot.state
            decision = self.eligibility.resolve(state, Phase.REVIEW_PREPARE)
            if not decision.eligible:
                raise LckStopError(
                    f"Review Prepare STOP for Task #{task_number}: "
                    + "; ".join(decision.reasons)
                )
            task_contract = _task_contract_from_state(state)
            target = _review_target_refs(state, task_contract)
            review_root = self.workspace.path_for(task_number, invocation.operation_id)
            mark(
                "clone-reserved",
                target=target.to_dict(),
                review_root=str(review_root),
            )
            self.workspace.create(
                task_number, target.base_sha, target.head_sha, review_root
            )
            mark(
                "clone-created",
                target=target.to_dict(),
                review_root=str(review_root),
            )
            mark(
                "checking-current-pr",
                target=target.to_dict(),
            )
            checks = self.checks_gate.observe(snapshot)
            identity = _review_identity(
                self.resolver,
                state,
                task_contract,
                repo_root=review_root,
            )
            profile = resolve_issue_profile(state.issue).profile
            documentation_policy: dict[str, Any] | None = None
            if profile is not None:
                policy = evaluate_profile_changes(profile, identity.changed_files)
            else:
                policy = None
            if policy is not None:
                documentation_policy = policy.to_dict()
                self.last_documentation_validation = documentation_policy
                if policy.status.value != "pass":
                    raise DocumentationReclassificationRequired(
                        "DOCUMENTATION_RECLASSIFICATION_REQUIRED: " + policy.detail
                    )
            mark(
                "review-target-derived",
                identity=identity.to_dict(),
                review_root=str(review_root),
            )
            mark("formal-validation", identity=identity.to_dict())
            validation = self.validation.run(
                review_root, identity.base_sha, identity.head_sha
            )
            if documentation_policy is not None:
                validation = dict(validation)
                validation["documentation_policy"] = documentation_policy
            # Preserve the structured validation result before applying the
            # pass gate so a rejected Review Prepare has complete evidence.
            self.last_validation = validation
            mark(
                "validation-persisted",
                identity=identity.to_dict(),
                validation=validation,
            )
            if validation.get("status") != "pass":
                raise LckStopError(_review_validation_failure(validation))
            mark(
                "sealing-review-context",
                identity=identity.to_dict(),
                validation=validation,
                checks=checks,
            )
            self.workspace.seal_for_review(review_root, identity.head_sha)
            review_id = self.store.new_id()
            guard = {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "review-invocation-guard",
                "review_id": review_id,
                "task_number": task_number,
                "identity": identity.to_dict(),
                "review_root": str(review_root),
                "validation": validation,
                "checks": checks,
                "snapshot": snapshot.to_dict(),
                "authority": (
                    "sealed Review Prepare target; historical identity for Review Complete "
                    "applicability comparison, never current authority"
                ),
            }
            self.store.write_guard(review_id, guard)
            mark(
                "handed-off",
                identity=identity.to_dict(),
                validation=validation,
                checks=checks,
                review_id=review_id,
            )
            context = ReviewContext(
                review_id=review_id,
                task_contract=task_contract,
                identity=identity,
                checks=checks,
                validation=validation,
                review_root=review_root,
                issue_profile=state.issue_profile,
            )
            invocation.release_lock()
        except BaseException as exc:
            cleanup_error: BaseException | None = None
            try:
                mark("failed", error=safe_text(str(exc), limit=1200))
            except BaseException:
                pass
            if review_root is not None and review_root.exists():
                try:
                    self.workspace.remove(review_root)
                except BaseException as remove_exc:
                    cleanup_error = remove_exc
            if cleanup_error is not None:
                raise cleanup_error from exc
            invocation.finish()
            raise
        return context


@dataclass(frozen=True)
class ReviewCompletionResult:
    review_id: str
    task_number: int
    verdict: str
    status: str
    identity: ReviewIdentity
    record_path: Path
    issue_profile: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        human_boundary = (
            "STOP; run deterministic Merge Preflight before any manual merge"
            if self.verdict == "PASS"
            else "STOP; Human must explicitly choose remediation, redesign, or abandon"
        )
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "review-complete",
            "review_id": self.review_id,
            "task_number": self.task_number,
            "verdict": self.verdict,
            "status": self.status,
            "issue_profile": _jsonable(self.issue_profile),
            "review_target": self.identity.to_dict(),
            "research_artifact": _jsonable(self.identity.research_artifact),
            "record_path": str(self.record_path),
            "human_boundary": human_boundary,
            "automatic_remediation": False,
        }


class ReviewCompleter:
    """Accept a semantic verdict only if a fresh completion snapshot still applies.

    ``review prepare`` and ``review complete`` are separate LCK operations.
    Prepare seals the exact target that the semantic reviewer inspected. Complete
    acquires one fresh, phase-specific authoritative snapshot, compares it with
    that sealed target, and accepts the verdict only when the PR, base, head,
    Task Contract, and effective diff are still identical. Downstream helpers do
    not reacquire authority inside the Review Complete operation.
    """

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        checks_gate: DeliveryChecksGate | None = None,
        store: ReviewInvocationStore | None = None,
        workspace: ReviewWorkspaceManager | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.workspace = workspace or ReviewWorkspaceManager(resolver)
        self.last_snapshot: OperationSnapshot | None = None
        self.last_checks: dict[str, Any] | None = None
        self.last_documentation_validation: dict[str, Any] | None = None

    def _capture_checks_from_gate(self) -> None:
        """Retain a check gate's result when strict evaluation raises."""
        for attribute in ("last_result", "last_checks"):
            value = getattr(self.checks_gate, attribute, None)
            if isinstance(value, Mapping):
                self.last_checks = dict(value)
                return

    @staticmethod
    def _read_findings(path: Path | None, verdict: str) -> str:
        if path is None:
            if verdict == "FAIL":
                raise LckStopError("FAIL verdict requires --findings-file")
            return ""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LckStopError(f"cannot read findings file: {exc}") from exc
        if verdict == "FAIL" and not text.strip():
            raise LckStopError("FAIL verdict requires non-empty findings")
        return text

    def complete(
        self,
        task_number: int,
        review_id: str,
        *,
        verdict: str,
        findings_file: Path | None = None,
        research_outcome: str | None = None,
    ) -> ReviewCompletionResult:
        self.last_checks = None
        self.last_documentation_validation = None
        verdict = verdict.upper()
        if verdict not in {"PASS", "FAIL"}:
            raise LckStopError("Review verdict must be PASS or FAIL")
        findings = self._read_findings(findings_file, verdict)
        guard = self.store.read_guard(review_id)
        if guard.get("task_number") != task_number:
            raise LckStopError("Review invocation does not belong to this Task")
        raw_identity = guard.get("identity")
        if not isinstance(raw_identity, Mapping):
            raise LckStopError("Review invocation guard has no identity")
        reviewed_identity = _identity_from_mapping(raw_identity)
        review_root_value = guard.get("review_root")
        if not isinstance(review_root_value, str) or not review_root_value:
            raise LckStopError("Review invocation guard has no review root")
        review_root = Path(review_root_value)

        completion_terminal = False
        try:
            validation = guard.get("validation")
            if (
                not isinstance(validation, Mapping)
                or validation.get("status") != "pass"
            ):
                raise LckStopError(
                    "Review invocation has no successful formal validation"
                )
            if reviewed_identity.task_number != task_number:
                raise LckStopError(
                    "Review invocation identity does not belong to this Task"
                )
            prepared_checks = guard.get("checks")
            if not isinstance(prepared_checks, Mapping):
                raise LckStopError("Review invocation has no PR check observation")
            self.last_checks = dict(prepared_checks)
            prepared_snapshot = guard.get("snapshot")
            if not isinstance(prepared_snapshot, Mapping):
                raise LckStopError("Review invocation has no sealed operation snapshot")
            if prepared_snapshot.get("operation") != "review-prepare":
                raise LckStopError("Review invocation snapshot has the wrong operation")

            completion_snapshot = self.snapshots.acquire(
                task_number,
                operation="review-complete",
            )
            self.last_snapshot = completion_snapshot
            state = completion_snapshot.state
            decision = self.eligibility.resolve(state, Phase.REVIEW_COMPLETE)
            if not decision.eligible:
                raise LckStopError(
                    f"Review Complete STOP for Task #{task_number}: "
                    + "; ".join(decision.reasons)
                )
            current_contract = _task_contract_from_state(state)
            _assert_review_target_facts_applicable(
                reviewed_identity,
                state,
                current_contract,
            )
            self.workspace.assert_ready_for_completion(
                review_root, reviewed_identity.head_sha
            )
            current_identity = _review_identity(
                self.resolver,
                state,
                current_contract,
                repo_root=review_root,
            )
            _assert_review_applicable(reviewed_identity, current_identity)
            profile = resolve_issue_profile(state.issue).profile
            research_artifact = current_identity.research_artifact
            if profile is not None and profile_research_outcome_supported(profile):
                if verdict == "PASS":
                    if not isinstance(research_artifact, Mapping):
                        raise LckStopError(
                            "Research Review Complete requires a reviewed artifact binding"
                        )
                    try:
                        if research_outcome is not None:
                            research_artifact = bind_research_outcome(
                                research_artifact, research_outcome
                            )
                        require_typed_research_outcome(research_artifact)
                    except ResearchPolicyError as exc:
                        raise LckStopError(
                            f"Research Review Complete requires a typed outcome: {exc}"
                        ) from exc
                    current_identity = replace(
                        current_identity, research_artifact=research_artifact
                    )
                elif research_outcome is not None:
                    try:
                        bind_research_outcome(research_artifact or {}, research_outcome)
                    except ResearchPolicyError as exc:
                        raise LckStopError(f"invalid Research Outcome: {exc}") from exc
            elif research_outcome is not None:
                raise LckStopError(
                    "--research-outcome is supported only for Research Issues"
                )
            if profile is not None:
                policy = evaluate_profile_changes(
                    profile, current_identity.changed_files
                )
            else:
                policy = None
            if policy is not None:
                self.last_documentation_validation = policy.to_dict()
                if policy.status.value != "pass":
                    raise DocumentationReclassificationRequired(
                        "DOCUMENTATION_RECLASSIFICATION_REQUIRED: " + policy.detail
                    )
            if verdict == "PASS":
                completion_snapshot = self.snapshots.bind_required_checks(
                    completion_snapshot, repo_root=review_root
                )
                # Binding may return a new immutable snapshot. Publish it
                # before the strict gate so a later failure receipt contains
                # the policy bound to this Review Complete operation.
                self.last_snapshot = completion_snapshot
                try:
                    completion_checks = self.checks_gate.evaluate(completion_snapshot)
                except BaseException:
                    self._capture_checks_from_gate()
                    raise
            else:
                try:
                    completion_checks = self.checks_gate.observe(completion_snapshot)
                except BaseException:
                    self._capture_checks_from_gate()
                    raise
            self.last_checks = dict(completion_checks)
            record = {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "independent-review-record",
                "review_id": review_id,
                "task_number": task_number,
                "verdict": verdict,
                "status": (
                    "READY_FOR_MERGE_PREFLIGHT"
                    if verdict == "PASS"
                    else "STOP_REQUIRED"
                ),
                "identity": current_identity.to_dict(),
                "research_artifact": _jsonable(current_identity.research_artifact),
                "research_outcome": (
                    current_identity.research_artifact.get("outcome")
                    if isinstance(current_identity.research_artifact, Mapping)
                    else None
                ),
                "findings": findings,
                "findings_sha256": hashlib.sha256(findings.encode("utf-8")).hexdigest(),
                "validation": validation,
                "checks": dict(prepared_checks),
                "completion_checks": completion_checks,
                "review_snapshot": dict(prepared_snapshot),
                "completion_snapshot": completion_snapshot.to_dict(),
                "authority_note": (
                    "Review Prepare target and fresh Review Complete snapshot matched; "
                    "this record is audit evidence, while Merge Preflight must reacquire "
                    "current Git/GitHub authority before human merge"
                ),
            }
            record_path = self.store.write_record(task_number, review_id, record)
            self.store.write_latest_review(task_number, review_id, verdict)
            self.store.clear_review_required(task_number)
            completion_terminal = True
            return ReviewCompletionResult(
                review_id=review_id,
                task_number=task_number,
                verdict=verdict,
                status=cast(str, record["status"]),
                identity=current_identity,
                record_path=record_path,
                issue_profile=completion_snapshot.state.issue_profile,
            )
        except ReviewStaleError:
            # Stale is a formal terminal outcome for this prepared target.  It
            # cannot be retried safely with the old target, so a fresh Prepare
            # must reclaim this operation-owned state.
            completion_terminal = True
            raise
        finally:
            if completion_terminal:
                cleanup_error: BaseException | None = None
                try:
                    self.workspace.remove(review_root)
                except BaseException as exc:
                    cleanup_error = exc
                if cleanup_error is None:
                    self.store.delete_guard(review_id)
                    self.store.release_review_prepare(task_number, review_id)
                else:
                    raise cleanup_error


class ReviewPassGate:
    """Prove that the latest accepted Review PASS still matches live facts."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        store: ReviewInvocationStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.store = store or ReviewInvocationStore(resolver.repo_root)

    def run(self, task_number: int, state: LiveState) -> dict[str, Any]:
        latest = self.store.read_latest_review(task_number)
        if not isinstance(latest, Mapping) or latest.get("verdict") != "PASS":
            raise LckStopError(
                "Merge Preflight requires the latest Independent Review PASS"
            )
        review_id = latest.get("review_id")
        if not isinstance(review_id, str):
            raise LckStopError("latest Independent Review PASS has no review id")
        record = self.store.read_record(task_number, review_id)
        if (
            record.get("task_number") != task_number
            or record.get("review_id") != review_id
            or record.get("verdict") != "PASS"
            or record.get("status") != "READY_FOR_MERGE_PREFLIGHT"
        ):
            raise LckStopError("latest Independent Review PASS record is invalid")
        raw_identity = record.get("identity")
        if not isinstance(raw_identity, Mapping):
            raise LckStopError("Independent Review PASS has no identity")
        recorded = _identity_from_mapping(raw_identity)
        current_contract = _task_contract_from_state(state)
        try:
            _assert_review_target_facts_applicable(
                recorded,
                state,
                current_contract,
            )
        except ReviewStaleError as exc:
            raise LckStopError(f"Review PASS is stale: {exc}") from exc
        profile = resolve_issue_profile(state.issue).profile
        if profile is not None:
            policy = evaluate_profile_changes(profile, recorded.changed_files)
        else:
            policy = None
        if policy is not None:
            if policy.status.value != "pass":
                raise DocumentationReclassificationRequired(
                    "Documentation Review PASS is outside the safe-change policy: "
                    + policy.detail
                )

        # Git commit objects are content-addressed. Once current PR/base/head and
        # Task Contract identity still match the accepted Review receipt, the
        # recorded merge-base/effective-diff identity remains mechanically bound
        # to those same commits. Merge Preflight therefore needs no source-repo
        # object materialization or duplicate local diff probe.
        return {
            "status": "pass",
            "review_id": review_id,
            "identity": recorded.to_dict(),
            "recorded_identity": recorded.to_dict(),
        }


@dataclass(frozen=True)
class MergePreflightResult:
    task_number: int
    status: str
    pr: Mapping[str, Any]
    review: Mapping[str, Any]
    checks: Mapping[str, Any]
    blockers: Mapping[str, Any]
    mergeability: str
    operation_snapshot: OperationSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "merge-preflight",
            "task_number": self.task_number,
            "status": self.status,
            "issue_profile": _jsonable(
                self.operation_snapshot.state.issue_profile
                if self.operation_snapshot is not None
                else None
            ),
            "pr": _jsonable(self.pr),
            "review": _jsonable(self.review),
            "checks": _jsonable(self.checks),
            "blockers": _jsonable(self.blockers),
            "mergeability": self.mergeability,
            "human_boundary": (
                "STOP — maintainer must perform the manual Squash Merge; "
                "LCK has no auto-merge path"
            ),
            "automatic_merge": False,
        }


class MergePreflight:
    """Run deterministic merge gates without mutating GitHub."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        review_gate: ReviewPassGate | None = None,
        checks_gate: DeliveryChecksGate | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.review_gate = review_gate or ReviewPassGate(resolver)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)
        self.last_snapshot: OperationSnapshot | None = None
        self.last_checks: dict[str, Any] | None = None

    def _capture_checks_from_gate(self) -> None:
        """Retain a check gate's result when strict evaluation raises."""
        for attribute in ("last_result", "last_checks"):
            value = getattr(self.checks_gate, attribute, None)
            if isinstance(value, Mapping):
                self.last_checks = dict(value)
                return

    def run(self, task_number: int) -> MergePreflightResult:
        self.last_checks = None
        snapshot = self.snapshots.acquire(
            task_number,
            operation="merge-preflight",
            include_required_checks=True,
        )
        self.last_snapshot = snapshot
        state = snapshot.state
        if state.status is not ResolutionStatus.RESOLVED:
            raise LckStopError("Merge Preflight STOP: " + "; ".join(state.stop_reasons))
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            raise LckStopError("Merge Preflight requires one current OPEN PR")
        if pr.get("isDraft") is not False:
            raise LckStopError("Merge Preflight requires a non-Draft OPEN PR")
        if str(pr.get("state", "")).upper() != "OPEN":
            raise LckStopError("Merge Preflight requires an OPEN PR")
        if pr.get("baseRefName") != BASE_BRANCH:
            raise LckStopError("Merge Preflight PR base branch is not main")
        if pr.get("headRefName") != state.target_branch:
            raise LckStopError(
                "Merge Preflight PR head branch is not the resolved Task branch"
            )
        head_sha = _pr_head_sha(pr)
        base_sha = _pr_base_sha(pr)
        if head_sha is None or base_sha is None:
            raise LckStopError("Merge Preflight PR head/base identity is unavailable")
        if state.remote_task_oid != head_sha:
            raise LckStopError(
                "Merge Preflight remote Task branch diverges from PR head"
            )
        if state.local_task_head is not None and state.local_task_head != head_sha:
            raise LckStopError(
                "Merge Preflight local Task branch diverges from PR head"
            )
        if state.project_status != "Review":
            raise LckStopError("Merge Preflight requires Project Status Review")

        downstream_contract = (
            state.task_contract
            if isinstance(state.task_contract, Mapping)
            else state.issue
        )
        blockers = _formal_blockers_gate(
            state.relationships,
            downstream_contract=downstream_contract,
        )
        if blockers.get("status") != "pass":
            raise LckStopError(
                "Merge Preflight unresolved blockers: "
                + str(blockers.get("detail") or blockers.get("status"))
            )
        review = self.review_gate.run(task_number, state)
        try:
            checks = self.checks_gate.evaluate(snapshot)
        except BaseException:
            self._capture_checks_from_gate()
            raise
        self.last_checks = dict(checks)
        if checks.get("limitation"):
            raise LckStopError(
                "Merge Preflight cannot prove required checks: "
                + str(checks["limitation"])
            )
        mergeability_value = pr.get("mergeable")
        mergeability = str(mergeability_value or "").upper()
        if mergeability not in {"MERGEABLE", "TRUE"}:
            raise LckStopError(
                "Merge Preflight mergeability is not proven: "
                + (mergeability or "UNKNOWN")
            )
        return MergePreflightResult(
            task_number=task_number,
            status="READY_FOR_HUMAN_MERGE",
            pr=pr,
            review=review,
            checks=checks,
            blockers=blockers,
            mergeability=mergeability,
            operation_snapshot=snapshot,
        )


MergePreflightRunner = MergePreflight
