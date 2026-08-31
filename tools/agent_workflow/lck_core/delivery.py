from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from critical_outcome import (
    CriticalOutcomeError,
    contract_from_snapshot,
    verify_critical_outcome,
)
from workflow_common import ProgressReporter, is_sha

from .effects import (
    CommitCurrentTreeEffect,
    EnsureOpenPrEffect,
    EnsureRemoteBranchEffect,
    ReuseExistingOpenPrEffect,
    SetReviewStatusEffect,
)
from .eligibility import PhaseDecision, PhaseEligibilityResolver
from .issue_profiles import resolve_issue_profile
from .models import (
    BASE_BRANCH,
    LCK_SCHEMA_VERSION,
    EffectReceipt,
    LckStopError,
    LiveState,
    OperationSnapshot,
    Phase,
    _authoritative_remote_main_sha,
    _is_clean_current_main,
    _jsonable,
)
from .profile_policies import run_profile_delivery_gates
from .state import (
    LiveStateResolver,
    OperationSnapshotBuilder,
    _task_contract_from_state,
)
from .validation import (
    DeliveryChecksGate,
    DocumentationValidationGate,
    FormalValidationGate,
    ResearchValidationGate,
)


@dataclass(frozen=True)
class DeliveryContext:
    task_number: int
    repository: str | None
    branch: str
    base_sha: str | None
    action: str
    operation_snapshot: OperationSnapshot
    eligibility: PhaseDecision

    @property
    def state(self) -> LiveState:
        return self.operation_snapshot.state

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "delivery-prepare",
            "task_number": self.task_number,
            "repository": self.repository,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "action": self.action,
            "issue_profile": _jsonable(self.state.issue_profile),
            "task_contract": _jsonable(_task_contract_from_state(self.state)),
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "eligibility": self.eligibility.to_dict(),
        }


class DeliveryPreparer:
    """Prepare or restore one local Task workspace with bounded Git effects."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.last_snapshot: OperationSnapshot | None = None

    def _run_git(self, args: Sequence[str], command_id: str) -> None:
        result = self.resolver.runner.run(
            ["git", *args],
            command_id=command_id,
        )
        if result.returncode != 0:
            raise LckStopError(
                f"{command_id} failed with exit code {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def _verify_workspace(self, branch: str, expected_head: str | None) -> None:
        current = self.resolver.runner.run(
            ["git", "branch", "--show-current"],
            command_id="lck-delivery-prepare-post-branch",
        )
        head = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-delivery-prepare-post-head",
        )
        if (
            current.returncode != 0
            or current.stdout.strip() != branch
            or head.returncode != 0
            or not is_sha(head.stdout.strip())
            or (expected_head is not None and head.stdout.strip() != expected_head)
        ):
            raise LckStopError(
                "workspace postcondition failed: selected branch/head is not the "
                "operation snapshot target"
            )

    def prepare(self, task_number: int) -> DeliveryContext:
        snapshot = self.snapshots.acquire(
            task_number,
            operation=Phase.DELIVERY_PREPARE.value,
        )
        self.last_snapshot = snapshot
        state = snapshot.state
        decision = self.eligibility.resolve(state, Phase.DELIVERY_PREPARE)
        if not decision.eligible:
            raise LckStopError(
                f"Delivery Prepare STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )

        branch = state.target_branch
        current_branch = state.git.get("branch")
        clean = state.git.get("clean") is True
        base_sha = state.git.get("local_main_sha")
        action = "already-prepared"
        local_exists = state.local_task_branch is not None

        if local_exists:
            if current_branch != branch:
                if not clean:
                    raise LckStopError(
                        "cannot switch to Task branch with a dirty unrelated worktree"
                    )
                self._run_git(
                    ["switch", branch],
                    "lck-select-existing-task-branch",
                )
                action = "selected-existing"
        elif state.remote_task_branch is not None:
            if current_branch != BASE_BRANCH or not clean:
                raise LckStopError(
                    "restoring a remote Task branch requires a clean main worktree"
                )
            self._run_git(
                ["switch", "--track", "-c", branch, f"origin/{branch}"],
                "lck-restore-remote-task-branch",
            )
            action = "restored-from-remote"
        else:
            if not _is_clean_current_main(state.git):
                raise LckStopError(
                    "new Task workspace requires clean main with "
                    "HEAD == local main == origin/main"
                )
            self._run_git(
                ["switch", "-c", branch, base_sha],
                "lck-create-task-branch",
            )
            action = "created-from-main"

        expected_head = (
            state.local_task_head
            if local_exists
            else state.remote_task_oid
            if state.remote_task_branch is not None
            else base_sha
        )
        self._verify_workspace(branch, expected_head)
        return DeliveryContext(
            task_number=task_number,
            repository=state.repository,
            branch=branch,
            base_sha=base_sha if isinstance(base_sha, str) else None,
            action=action,
            operation_snapshot=snapshot,
            eligibility=decision,
        )


@dataclass(frozen=True)
class DeliveryCompletionResult:
    task_number: int
    status: str
    branch: str
    head_sha: str
    critical_outcome: Mapping[str, Any] | None
    validation: Mapping[str, Any]
    checks: Mapping[str, Any]
    effects: tuple[EffectReceipt, ...]
    operation_snapshot: OperationSnapshot
    research_artifact: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "delivery-complete",
            "task_number": self.task_number,
            "status": self.status,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "issue_profile": _jsonable(self.operation_snapshot.state.issue_profile),
            "critical_outcome": _jsonable(self.critical_outcome),
            "validation": _jsonable(self.validation),
            "checks": _jsonable(self.checks),
            "research_artifact": _jsonable(self.research_artifact),
            "effects": [item.to_dict() for item in self.effects],
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "human_boundary": "Independent Review must be started separately",
        }


class DeliveryCompleter:
    """Complete Delivery from one immutable operation-start snapshot.

    Lifecycle authority is acquired once.  Local/remote/PR/metadata mutations
    then prove only their own exact postconditions.  Asynchronous PR checks are
    observed without blocking Delivery; pending checks are reported while the
    operation continues through Project Status and final identity verification.
    Strict check evaluation remains owned by Review Complete and Merge Preflight.
    """

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        formal_validation: FormalValidationGate | None = None,
        commit_effect: CommitCurrentTreeEffect | None = None,
        remote_effect: EnsureRemoteBranchEffect | None = None,
        pr_effect: EnsureOpenPrEffect | ReuseExistingOpenPrEffect | None = None,
        status_effect: SetReviewStatusEffect | None = None,
        checks_gate: DeliveryChecksGate | None = None,
        documentation_validation: DocumentationValidationGate | None = None,
        research_validation: ResearchValidationGate | None = None,
        require_existing_open_pr: bool = False,
        candidate_recorder: Callable[[str, str], None] | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.eligibility = eligibility or PhaseEligibilityResolver()
        self.formal_validation = formal_validation or FormalValidationGate(resolver)
        self.commit_effect = commit_effect or CommitCurrentTreeEffect(resolver)
        self.remote_effect = remote_effect or EnsureRemoteBranchEffect(resolver)
        self.pr_effect = pr_effect or EnsureOpenPrEffect(resolver)
        self.status_effect = status_effect or SetReviewStatusEffect(resolver)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)
        self.documentation_validation = (
            documentation_validation or DocumentationValidationGate(resolver)
        )
        self.research_validation = research_validation or ResearchValidationGate(
            resolver
        )
        self.require_existing_open_pr = require_existing_open_pr
        self.candidate_recorder = candidate_recorder
        self.last_snapshot: OperationSnapshot | None = None
        self.last_critical_outcome: dict[str, Any] | None = None
        self.last_documentation_validation: dict[str, Any] | None = None
        self.last_research_validation: dict[str, Any] | None = None
        self.last_validation: dict[str, Any] | None = None
        self.last_checks: dict[str, Any] | None = None
        self.last_effects: list[EffectReceipt] = []

    def _capture_checks_from_gate(self) -> None:
        """Retain a check gate's result before a later failure is reported."""
        for attribute in ("last_result", "last_checks"):
            value = getattr(self.checks_gate, attribute, None)
            if isinstance(value, Mapping):
                self.last_checks = dict(value)
                return

    def _run_critical_outcome(
        self,
        state: LiveState,
        *,
        progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        issue = state.issue
        contract_snapshot = (
            issue.get("critical_outcome") if isinstance(issue, Mapping) else None
        )
        try:
            contract = contract_from_snapshot(contract_snapshot)
            result = verify_critical_outcome(
                self.resolver.repo_root,
                self.resolver.runner,
                contract,
                progress=progress,
            )
        except CriticalOutcomeError as exc:
            raise LckStopError(f"Critical Outcome contract invalid: {exc}") from exc
        payload = result.to_dict()
        self.last_critical_outcome = payload
        if result.status != "pass":
            raise LckStopError(
                "Critical Outcome FAIL: "
                f"{contract.verification_test} exited {result.exit_code}"
            )
        return payload

    def _run_formal_validation(self, base_sha: str) -> dict[str, Any]:
        """Retain a parsed validation payload when the gate rejects it."""
        try:
            validation = self.formal_validation.run(base_sha)
        except BaseException:
            payload = getattr(self.formal_validation, "last_payload", None)
            if isinstance(payload, dict):
                self.last_validation = payload
            raise
        self.last_validation = validation
        return validation

    def _run_profile_gates(
        self,
        state: LiveState,
        base_sha: str,
        *,
        progress: ProgressReporter,
        include_index: bool = False,
        head_sha: str | None = None,
    ) -> dict[str, Any] | None:
        profile_resolution = resolve_issue_profile(state.issue)
        profile = profile_resolution.profile
        if profile is None or not profile_resolution.resolved:
            raise LckStopError("current leaf Issue workflow profile is unavailable")
        self.last_documentation_validation = None
        self.last_research_validation = None
        try:
            results = run_profile_delivery_gates(
                profile,
                base_sha=base_sha,
                head_sha=head_sha,
                include_index=include_index,
                progress=progress,
                documentation_validation=self.documentation_validation,
                research_validation=self.research_validation,
                critical_outcome=lambda: self._run_critical_outcome(
                    state, progress=progress
                ),
            )
        except LckStopError:
            self.last_research_validation = getattr(
                self.research_validation, "last_result", None
            )
            raise
        self.last_documentation_validation = results.documentation_validation
        self.last_research_validation = results.research_validation
        self.last_critical_outcome = results.critical_outcome
        return results.critical_outcome

    def _has_task_diff(self, base_sha: str) -> bool:
        result = self.resolver.runner.run(
            ["git", "diff", "--quiet", f"{base_sha}...HEAD"],
            command_id="lck-task-diff-present",
        )
        if result.returncode == 1:
            return True
        if result.returncode == 0:
            return False
        raise LckStopError("unable to verify Task diff against operation base")

    def _verify_local_completion(self, branch: str, head_sha: str) -> None:
        branch_result = self.resolver.runner.run(
            ["git", "branch", "--show-current"],
            command_id="lck-delivery-final-branch",
        )
        head_result = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-delivery-final-head",
        )
        status = self.resolver.runner.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            command_id="lck-delivery-final-status",
        )
        if (
            branch_result.returncode != 0
            or branch_result.stdout.strip() != branch
            or head_result.returncode != 0
            or head_result.stdout.strip() != head_sha
            or status.returncode != 0
            or status.stdout.strip()
        ):
            raise LckStopError(
                "Delivery local postcondition failed: branch/head/worktree is not "
                "the validated operation result"
            )

    @staticmethod
    def _receipt_pr_identity(receipt: EffectReceipt) -> dict[str, Any]:
        return {
            "number": receipt.details.get("number"),
            "head_sha": receipt.details.get("head_sha"),
            "base_sha": receipt.details.get("base_sha"),
        }

    def _complete_from_snapshot(
        self,
        snapshot: OperationSnapshot,
        *,
        phase: Phase,
        owned_remediation_candidate: bool = False,
        commit_message: str,
        summary: str,
        risks: str,
        progress: ProgressReporter,
    ) -> DeliveryCompletionResult:
        state = snapshot.state
        task_number = state.task_number
        effects: list[EffectReceipt] = []
        self.last_effects = effects
        self.last_critical_outcome = None
        self.last_documentation_validation = None
        self.last_validation = None
        self.last_checks = None
        decision = self.eligibility.resolve(
            state,
            phase,
            owned_remediation_candidate=owned_remediation_candidate,
        )
        if not decision.eligible:
            raise LckStopError(
                f"{phase.value} STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )
        base_sha_value = _authoritative_remote_main_sha(state.git)
        if not is_sha(base_sha_value):
            raise LckStopError("current remote main identity is unavailable")
        base_sha = str(base_sha_value)
        issue = state.issue
        body_sha256 = issue.get("body_sha256") if isinstance(issue, Mapping) else None
        if not isinstance(body_sha256, str) or not body_sha256:
            raise LckStopError("current Task body identity is unavailable")
        branch = state.target_branch
        if self.require_existing_open_pr:
            pr = state.open_pr
            if (
                not isinstance(pr, Mapping)
                or pr.get("isDraft") is not False
                or pr.get("baseRefOid") != base_sha
                or pr.get("headRefName") != branch
            ):
                raise LckStopError(
                    "Remediation requires the snapshot's existing OPEN PR on the "
                    "resolved Task branch/base"
                )

        if state.git.get("clean") is True:
            progress.running("revalidating-candidate")
            if not self._has_task_diff(base_sha):
                raise LckStopError(
                    "Delivery Complete found no Task diff against operation base"
                )
            validated_tree = self.commit_effect.current_head_tree()
            critical = self._run_profile_gates(
                state,
                base_sha,
                progress=progress,
                head_sha=state.local_task_head,
            )
            progress.running("formal-validation")
            validation = self._run_formal_validation(base_sha)
            if self.last_documentation_validation is not None:
                validation = dict(validation)
                validation["documentation_policy"] = self.last_documentation_validation
                self.last_validation = validation
            validated_head = state.local_task_head
            if not is_sha(validated_head):
                raise LckStopError("current Task head is unavailable")
            self.commit_effect.verify_tree_unchanged(
                validated_tree,
                expected_head_sha=validated_head,
            )
            head = validated_head
            effects.append(
                EffectReceipt(
                    effect="commit_current_tree",
                    action="already-committed-revalidated",
                    details={"head_sha": head, "tree_oid": validated_tree},
                )
            )
        else:
            progress.running("staging-candidate")
            validated_tree = self.commit_effect.stage_candidate_tree()
            critical = self._run_profile_gates(
                state,
                base_sha,
                progress=progress,
                include_index=True,
                head_sha=state.local_task_head,
            )
            progress.running("formal-validation")
            validation = self._run_formal_validation(base_sha)
            if self.last_documentation_validation is not None:
                validation = dict(validation)
                validation["documentation_policy"] = self.last_documentation_validation
                self.last_validation = validation
            self.commit_effect.verify_tree_unchanged(
                validated_tree,
                expected_head_sha=state.local_task_head,
            )
            commit = self.commit_effect.execute(
                validated_tree,
                commit_message,
                expected_parent_sha=state.local_task_head,
            )
            effects.append(commit)
            head = commit.details.get("head_sha")
            if not is_sha(head):
                raise LckStopError("commit receipt did not contain a valid head SHA")

        if self.candidate_recorder is not None:
            self.candidate_recorder(str(head), validated_tree)

        progress.running("remote-branch")
        remote = self.remote_effect.execute(branch, expected_head_sha=str(head))
        effects.append(remote)
        if remote.details.get("remote_oid") != head:
            raise LckStopError("remote branch effect did not prove the validated head")

        progress.running("open-pr")
        pr_receipt = self.pr_effect.execute(
            state,
            head_sha=str(head),
            summary=summary,
            risks=risks,
            critical_outcome=critical,
            validation=validation,
            expected_base_sha=base_sha,
            expected_body_sha256=body_sha256,
        )
        effects.append(pr_receipt)
        pr_identity = self._receipt_pr_identity(pr_receipt)
        pr_number = pr_identity.get("number")
        if (
            not isinstance(pr_number, int)
            or pr_identity.get("head_sha") != head
            or pr_identity.get("base_sha") != base_sha
        ):
            raise LckStopError("PR effect did not prove the validated head/base")

        progress.running("checks")
        snapshot_pr = state.open_pr
        try:
            if (
                isinstance(snapshot_pr, Mapping)
                and snapshot_pr.get("number") == pr_number
                and snapshot_pr.get("headRefOid") == head
                and snapshot_pr.get("baseRefOid") == base_sha
            ):
                checks = self.checks_gate.observe(snapshot)
            else:
                if not isinstance(state.repository, str):
                    raise LckStopError(
                        "repository identity is unavailable for PR checks"
                    )
                checks = self.checks_gate.observe_exact_pr(
                    state.repository,
                    pr_number,
                    expected_head_sha=str(head),
                    expected_base_sha=base_sha,
                )
        except BaseException:
            self._capture_checks_from_gate()
            raise
        self.last_checks = dict(checks)

        checks_pr = checks.get("pr")
        if not isinstance(checks_pr, Mapping):
            raise LckStopError("checks result did not contain PR identity")
        progress.running("project-status")
        status_receipt = self.status_effect.execute(
            state,
            expected_pr=checks_pr,
            checks_result=checks,
        )
        effects.append(status_receipt)
        if status_receipt.action not in {"updated", "already-review"}:
            raise LckStopError("Project Status effect did not reach Review")

        self._verify_local_completion(branch, str(head))
        return DeliveryCompletionResult(
            task_number=task_number,
            status="READY_FOR_REVIEW",
            branch=branch,
            head_sha=str(head),
            critical_outcome=critical,
            validation=validation,
            checks=checks,
            effects=tuple(effects),
            operation_snapshot=snapshot,
            research_artifact=self.last_research_validation,
        )

    def complete(
        self,
        task_number: int,
        *,
        commit_message: str,
        summary: str,
        risks: str = "",
        operation_snapshot: OperationSnapshot | None = None,
        phase: Phase = Phase.DELIVERY_COMPLETE,
        owned_remediation_candidate: bool = False,
    ) -> DeliveryCompletionResult:
        self.last_checks = None
        if not summary.strip():
            raise LckStopError("Delivery summary must be non-empty")
        operation = (
            "remediation-complete"
            if phase is Phase.REMEDIATION_COMPLETE
            else "delivery-complete"
        )
        progress = ProgressReporter(operation)
        progress.started("initializing")
        try:
            progress.running("resolving-live-state")
            snapshot = operation_snapshot or self.snapshots.acquire(
                task_number,
                operation=phase.value,
            )
            self.last_snapshot = snapshot
            if snapshot.state.task_number != task_number:
                raise LckStopError("operation snapshot belongs to another Task")
            result = self._complete_from_snapshot(
                snapshot,
                phase=phase,
                owned_remediation_candidate=owned_remediation_candidate,
                commit_message=commit_message,
                summary=summary,
                risks=risks,
                progress=progress,
            )
        except BaseException:
            progress.failed()
            raise
        progress.completed("handoff")
        return result
