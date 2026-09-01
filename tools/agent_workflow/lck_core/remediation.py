from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from workflow_common import is_sha

from .delivery import DeliveryCompleter, DeliveryCompletionResult
from .effects import ReuseExistingOpenPrEffect
from .eligibility import PhaseEligibilityResolver
from .models import (
    LCK_SCHEMA_VERSION,
    EffectReceipt,
    LckStopError,
    LiveState,
    OperationSnapshot,
    Phase,
    _jsonable,
)
from .profile_policies import (
    DEFAULT_PROFILE_POLICY_REGISTRY,
    ProfileEvidenceEnvelope,
    ProfilePolicyRegistry,
    ProfileResolver,
)
from .review_workspace import ReviewInvocationStore, _identity_from_mapping
from .state import (
    LiveStateResolver,
    OperationSnapshotBuilder,
    _leaf_contract_from_state,
)
from .validation import DeliveryChecksGate


@dataclass(frozen=True)
class RemediationContext:
    task_number: int
    review_id: str
    findings: str
    findings_source: str
    operation_snapshot: OperationSnapshot
    action: str

    @property
    def state(self) -> LiveState:
        return self.operation_snapshot.state

    @property
    def task_contract(self) -> dict[str, Any]:
        """Return the contract bound to this Remediation Prepare snapshot."""
        return _leaf_contract_from_state(self.state)

    def to_dict(self) -> dict[str, Any]:
        pr = self.state.open_pr or {}
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "remediation-prepare",
            "status": "READY_FOR_REMEDIATION",
            "task_number": self.task_number,
            "review_id": self.review_id,
            "action": self.action,
            "issue_profile": _jsonable(self.state.issue_profile),
            "findings": self.findings,
            "findings_source": self.findings_source,
            "task_contract": _jsonable(self.task_contract),
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "live_target": {
                "pr_number": pr.get("number"),
                "base_sha": pr.get("baseRefOid"),
                "head_sha": pr.get("headRefOid"),
                "branch": self.state.target_branch,
                "task_body_sha256": (
                    self.state.issue.get("body_sha256")
                    if isinstance(self.state.issue, Mapping)
                    else None
                ),
            },
            "mechanical_authority": (
                "operation snapshot acquired at Remediation entry; Review findings are semantic "
                "input only, whether loaded from the local audit record or an explicitly supplied "
                "portable findings file"
            ),
            "acceptance_boundary": (
                "Requirements whose evidence can only be truthfully produced after the repaired "
                "candidate head exists, or by a separate provider/fresh Review invocation, do not "
                "block Remediation Complete. They remain unsatisfied Review-acceptance requirements "
                "and must not be fabricated or treated as satisfied by remediation."
            ),
        }


def _failed_review_record(
    store: ReviewInvocationStore, task_number: int, review_id: str
) -> dict[str, Any]:
    record = store.read_record(task_number, review_id)
    if record.get("task_number") != task_number or record.get("verdict") != "FAIL":
        raise LckStopError("Remediation requires a failed Independent Review record")
    latest = store.read_latest_review(task_number)
    if (
        not isinstance(latest, Mapping)
        or latest.get("review_id") != review_id
        or latest.get("verdict") != "FAIL"
    ):
        raise LckStopError(
            "Remediation requires the latest completed Independent Review to be this FAIL"
        )
    findings = record.get("findings")
    if not isinstance(findings, str) or not findings.strip():
        raise LckStopError(
            "failed Independent Review record has no remediation findings"
        )
    identity = record.get("identity")
    if not isinstance(identity, Mapping):
        raise LckStopError("failed Independent Review record has no reviewed identity")
    _identity_from_mapping(identity)
    return record


def _read_portable_review_findings(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LckStopError(f"cannot read portable Review findings file: {exc}") from exc
    if not text.strip():
        raise LckStopError("portable Review findings file is empty")
    return text


def _remediation_findings(
    store: ReviewInvocationStore,
    task_number: int,
    review_id: str,
    *,
    findings_file: Path | None = None,
) -> tuple[str, str]:
    # Constructing the path validates the caller-provided review id even when the
    # originating workspace-local audit record is unavailable.
    record_path = store.record_path(task_number, review_id)
    if record_path.exists():
        record = _failed_review_record(store, task_number, review_id)
        return cast(str, record["findings"]), "local-review-record"
    if findings_file is None:
        raise LckStopError(
            "failed Review audit record is unavailable in this workspace; "
            "for an explicit cross-workspace/provider Remediation handoff, provide "
            "--findings-file with the completed Review findings. The file is semantic "
            "input only; LCK still reacquires all mechanical authority from live state."
        )
    return _read_portable_review_findings(findings_file), "portable-findings-file"


class RemediationPreparer:
    """Explicitly enter repair using live mechanics plus semantic Review findings."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        store: ReviewInvocationStore | None = None,
        profile_resolver: ProfileResolver | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        selected_profile_resolver = profile_resolver or getattr(
            eligibility, "profile_resolver", None
        )
        self.eligibility = eligibility or PhaseEligibilityResolver(
            profile_resolver=selected_profile_resolver
        )
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.last_snapshot: OperationSnapshot | None = None

    def _run_git(self, args: Sequence[str], command_id: str) -> None:
        result = self.resolver.runner.run(["git", *args], command_id=command_id)
        if result.returncode != 0:
            raise LckStopError(
                f"{command_id} failed with exit code {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    def _verify_local_review_head(self, branch: str, expected_head: str) -> None:
        current = self.resolver.runner.run(
            ["git", "branch", "--show-current"],
            command_id="lck-remediation-post-branch",
        )
        head = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-remediation-post-head",
        )
        if (
            current.returncode != 0
            or current.stdout.strip() != branch
            or head.returncode != 0
            or head.stdout.strip() != expected_head
        ):
            raise LckStopError(
                "Remediation Prepare postcondition failed: workspace is not on "
                "the operation snapshot PR head"
            )

    def prepare(
        self,
        task_number: int,
        review_id: str,
        *,
        findings_file: Path | None = None,
    ) -> RemediationContext:
        required = self.store.read_review_required(task_number)
        if required is not None:
            raise LckStopError(
                "Remediation STOP: a fresh Independent Review is required after the previous remediation"
            )
        findings, findings_source = _remediation_findings(
            self.store,
            task_number,
            review_id,
            findings_file=findings_file,
        )
        snapshot = self.snapshots.acquire(
            task_number,
            operation=Phase.REMEDIATION_PREPARE.value,
        )
        self.last_snapshot = snapshot
        state = snapshot.state
        decision = self.eligibility.resolve(state, Phase.REMEDIATION_PREPARE)
        if not decision.eligible:
            raise LckStopError(
                f"Remediation Prepare STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )

        branch = state.target_branch
        current = state.git.get("branch")
        clean = state.git.get("clean") is True
        action = "already-prepared"
        if state.local_issue_branch is None:
            if not clean:
                raise LckStopError(
                    "restoring Remediation workspace requires a clean current worktree"
                )
            if state.remote_issue_branch != branch:
                raise LckStopError(
                    "current OPEN PR has no restorable remote Task branch"
                )
            self._run_git(
                ["switch", "-c", branch, "--track", f"origin/{branch}"],
                "lck-remediation-restore-branch",
            )
            action = "restored-remote-branch"
        elif current != branch:
            if not clean:
                raise LckStopError(
                    "cannot switch to Remediation branch with a dirty unrelated worktree"
                )
            self._run_git(["switch", branch], "lck-remediation-switch-branch")
            action = "selected-existing-branch"
        elif not clean:
            action = "resumed-dirty-remediation"

        pr_head = state.open_pr.get("headRefOid") if state.open_pr else None
        if not is_sha(pr_head):
            raise LckStopError("Remediation Prepare PR head is unavailable")
        self._verify_local_review_head(branch, pr_head)
        pr_number = state.open_pr.get("number") if state.open_pr else None
        pr_base = state.open_pr.get("baseRefOid") if state.open_pr else None
        if not isinstance(pr_number, int) or not is_sha(pr_base):
            raise LckStopError("Remediation Prepare PR identity is unavailable")
        self.store.write_remediation_session(
            task_number,
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "remediation-session",
                "task_number": task_number,
                "review_id": review_id,
                "operation_id": self.store.new_id(),
                "start_head_sha": pr_head,
                "pr_number": pr_number,
                "base_sha": pr_base,
                "findings_sha256": hashlib.sha256(findings.encode("utf-8")).hexdigest(),
                "findings_source": findings_source,
                "authority": (
                    "operation-local continuity only; current mechanical target must be "
                    "reacquired by the Remediation terminal operation"
                ),
            },
        )
        return RemediationContext(
            task_number=task_number,
            review_id=review_id,
            findings=findings,
            findings_source=findings_source,
            operation_snapshot=snapshot,
            action=action,
        )


@dataclass(frozen=True)
class RemediationNoChangeResult:
    task_number: int
    review_id: str
    head_sha: str
    pr_number: int
    base_sha: str
    summary: str
    operation_snapshot: OperationSnapshot
    receipt_path: Path
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "remediation-no-change",
            "task_number": self.task_number,
            "review_id": self.review_id,
            "status": "NO_IMPLEMENTATION_CHANGE",
            "issue_profile": _jsonable(self.operation_snapshot.state.issue_profile),
            "head_sha": self.head_sha,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "summary": self.summary,
            "candidate_changed": False,
            "fresh_review_required": False,
            "session_released": True,
            "replayed": self.replayed,
            "receipt_path": str(self.receipt_path),
            "operation_snapshot": self.operation_snapshot.to_dict(),
            "acceptance_boundary": (
                "This receipt closes one prepared Remediation session without changing the "
                "candidate. It records a real no-change implementation-path terminal state, "
                "but it does not satisfy deferred provider/cross-runtime Review acceptance by itself."
            ),
            "human_boundary": (
                "STOP — continue external acceptance work on the unchanged head; no fresh Review "
                "is required solely because this no-change session was closed"
            ),
        }


class RemediationNoChangeCompleter:
    """Close one prepared Remediation session when semantic repair needs no tree change."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        store: ReviewInvocationStore | None = None,
        profile_resolver: ProfileResolver | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        selected_profile_resolver = profile_resolver or getattr(
            eligibility, "profile_resolver", None
        )
        self.eligibility = eligibility or PhaseEligibilityResolver(
            profile_resolver=selected_profile_resolver
        )
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.last_snapshot: OperationSnapshot | None = None

    @staticmethod
    def _session_identity(
        session: Mapping[str, Any],
    ) -> tuple[str, int, str]:
        head_sha = session.get("start_head_sha")
        pr_number = session.get("pr_number")
        base_sha = session.get("base_sha")
        if (
            not is_sha(head_sha)
            or not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or not is_sha(base_sha)
        ):
            raise LckStopError("Remediation session identity is incomplete")
        return cast(str, head_sha), pr_number, cast(str, base_sha)

    @staticmethod
    def _verify_current_target(
        snapshot: OperationSnapshot,
        *,
        head_sha: str,
        pr_number: int,
        base_sha: str,
    ) -> None:
        state = snapshot.state
        pr = state.open_pr or {}
        if (
            pr.get("number") != pr_number
            or pr.get("headRefOid") != head_sha
            or pr.get("baseRefOid") != base_sha
            or state.local_issue_head != head_sha
            or state.remote_issue_oid != head_sha
        ):
            raise LckStopError(
                "Remediation No Change target no longer matches the prepared session"
            )
        if state.git.get("clean") is not True:
            raise LckStopError(
                "Remediation No Change requires a clean tracked and staged worktree"
            )

    def complete(
        self,
        task_number: int,
        review_id: str,
        *,
        summary: str,
    ) -> RemediationNoChangeResult:
        if self.store.read_review_required(task_number) is not None:
            raise LckStopError(
                "Remediation STOP: a fresh Independent Review is required after the previous remediation"
            )

        session = self.store.read_remediation_session(task_number)
        if session is None:
            prior = self.store.read_remediation_no_change_receipt(
                task_number, review_id
            )
            if prior is None:
                raise LckStopError(
                    "Remediation No Change requires a prepared Remediation session"
                )
            head_sha, pr_number, base_sha = self._session_identity(prior)
            snapshot = self.snapshots.acquire(
                task_number, operation=Phase.REMEDIATION_NO_CHANGE.value
            )
            self.last_snapshot = snapshot
            decision = self.eligibility.resolve(
                snapshot.state, Phase.REMEDIATION_NO_CHANGE
            )
            if not decision.eligible:
                raise LckStopError(
                    f"Remediation No Change STOP for Task #{task_number}: "
                    + "; ".join(decision.reasons)
                )
            self._verify_current_target(
                snapshot,
                head_sha=head_sha,
                pr_number=pr_number,
                base_sha=base_sha,
            )
            return RemediationNoChangeResult(
                task_number=task_number,
                review_id=review_id,
                head_sha=head_sha,
                pr_number=pr_number,
                base_sha=base_sha,
                summary=str(prior.get("summary") or ""),
                operation_snapshot=snapshot,
                receipt_path=self.store.remediation_no_change_receipt_path(
                    task_number, review_id
                ),
                replayed=True,
            )

        if session.get("review_id") != review_id:
            raise LckStopError(
                "Remediation No Change review id does not match the prepared Remediation session"
            )
        head_sha, pr_number, base_sha = self._session_identity(session)
        snapshot = self.snapshots.acquire(
            task_number, operation=Phase.REMEDIATION_NO_CHANGE.value
        )
        self.last_snapshot = snapshot
        decision = self.eligibility.resolve(snapshot.state, Phase.REMEDIATION_NO_CHANGE)
        if not decision.eligible:
            raise LckStopError(
                f"Remediation No Change STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )
        self._verify_current_target(
            snapshot,
            head_sha=head_sha,
            pr_number=pr_number,
            base_sha=base_sha,
        )

        receipt = {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "remediation-no-change-receipt",
            "task_number": task_number,
            "review_id": review_id,
            "start_head_sha": head_sha,
            "pr_number": pr_number,
            "base_sha": base_sha,
            "findings_sha256": session.get("findings_sha256"),
            "findings_source": session.get("findings_source"),
            "summary": summary,
            "candidate_changed": False,
            "fresh_review_required": False,
            "authority": (
                "formal no-change Remediation terminal receipt only; current mechanical "
                "target was reacquired at completion"
            ),
        }
        receipt_path = self.store.write_remediation_no_change_receipt(
            task_number, review_id, receipt
        )
        self.store.clear_remediation_session(task_number)
        return RemediationNoChangeResult(
            task_number=task_number,
            review_id=review_id,
            head_sha=head_sha,
            pr_number=pr_number,
            base_sha=base_sha,
            summary=summary,
            operation_snapshot=snapshot,
            receipt_path=receipt_path,
        )


@dataclass(frozen=True)
class RemediationCompletionResult:
    task_number: int
    review_id: str
    delivery: DeliveryCompletionResult

    def to_dict(self) -> dict[str, Any]:
        payload = self.delivery.to_dict()
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "remediation-complete",
            "task_number": self.task_number,
            "review_id": self.review_id,
            "status": "READY_FOR_NEW_REVIEW",
            "head_sha": self.delivery.head_sha,
            "issue_profile": _jsonable(
                self.delivery.operation_snapshot.state.issue_profile
            ),
            "critical_outcome": payload["critical_outcome"],
            "profile_evidence": payload["profile_evidence"],
            "validation": payload["validation"],
            "checks": payload["checks"],
            "effects": payload["effects"],
            "operation_snapshot": payload["operation_snapshot"],
            "human_boundary": (
                "STOP — a new Independent Review must be started explicitly in a fresh invocation"
            ),
            "deferred_review_acceptance": (
                "Any requirement whose evidence depends on this new head or on a separate provider/"
                "fresh Review remains pending for the next Independent Review. READY_FOR_NEW_REVIEW "
                "does not claim that such evidence is satisfied."
            ),
            "automatic_review": False,
        }


class RemediationCompleter:
    """Reuse Task-2 Delivery effects for one explicitly started repair."""

    def __init__(
        self,
        resolver: LiveStateResolver,
        *,
        eligibility: PhaseEligibilityResolver | None = None,
        store: ReviewInvocationStore | None = None,
        checks_gate: DeliveryChecksGate | None = None,
        policy_registry: ProfilePolicyRegistry | None = None,
        profile_resolver: ProfileResolver | None = None,
    ) -> None:
        self.resolver = resolver
        self.snapshots = OperationSnapshotBuilder(resolver)
        self.policy_registry = policy_registry or DEFAULT_PROFILE_POLICY_REGISTRY
        self.profile_resolver = profile_resolver or getattr(
            eligibility, "profile_resolver", None
        )
        self.eligibility = eligibility or PhaseEligibilityResolver(
            registry=self.policy_registry,
            profile_resolver=profile_resolver,
        )
        self.store = store or ReviewInvocationStore(resolver.repo_root)
        self.checks_gate = checks_gate or DeliveryChecksGate(resolver)
        self.last_snapshot: OperationSnapshot | None = None
        self.last_critical_outcome: dict[str, Any] | None = None
        self.last_profile_evidence: ProfileEvidenceEnvelope | None = None
        self.last_validation: dict[str, Any] | None = None
        self.last_checks: dict[str, Any] | None = None
        self.last_effects: list[EffectReceipt] = []

    def _capture_delivery_evidence(self, delivery: Any) -> None:
        """Expose nested Delivery evidence to the outer failure receipt."""
        snapshot = getattr(delivery, "last_snapshot", None)
        if isinstance(snapshot, OperationSnapshot):
            self.last_snapshot = snapshot
        critical = getattr(delivery, "last_critical_outcome", None)
        if isinstance(critical, dict):
            self.last_critical_outcome = critical
        profile_evidence = getattr(delivery, "last_profile_evidence", None)
        if isinstance(profile_evidence, ProfileEvidenceEnvelope):
            self.last_profile_evidence = profile_evidence
        validation = getattr(delivery, "last_validation", None)
        if isinstance(validation, dict):
            self.last_validation = validation
        checks = getattr(delivery, "last_checks", None)
        if isinstance(checks, Mapping):
            self.last_checks = dict(checks)
        effects = getattr(delivery, "last_effects", None)
        if isinstance(effects, list):
            self.last_effects = effects

    def _owned_candidate_recovery(
        self,
        session: Mapping[str, Any],
        state: LiveState,
        *,
        task_number: int,
        review_id: str,
    ) -> bool:
        candidate = session.get("candidate")
        if not isinstance(candidate, Mapping):
            return False
        operation_id = session.get("operation_id")
        start_head = session.get("start_head_sha")
        candidate_head = candidate.get("head_sha")
        candidate_tree = candidate.get("tree_oid")
        pr = state.open_pr or {}
        exact_identity = (
            session.get("task_number") == task_number
            and session.get("review_id") == review_id
            and isinstance(operation_id, str)
            and bool(operation_id)
            and candidate.get("operation_id") == operation_id
            and candidate.get("start_head_sha") == start_head
            and is_sha(start_head)
            and is_sha(candidate_head)
            and is_sha(candidate_tree)
            and pr.get("number") == session.get("pr_number")
            and pr.get("baseRefOid") == session.get("base_sha")
            and pr.get("headRefOid") == start_head
            and state.remote_issue_oid == start_head
            and state.local_issue_head == candidate_head
            and state.git.get("branch") == state.target_branch
            and state.git.get("clean") is True
        )
        if not exact_identity:
            raise LckStopError(
                "Remediation committed candidate does not exactly match its owned session target"
            )
        tree = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            command_id="lck-remediation-owned-candidate-tree",
        )
        if tree.returncode != 0 or tree.stdout.strip() != candidate_tree:
            raise LckStopError(
                "Remediation committed candidate tree does not match its owned session identity"
            )
        ancestor = self.resolver.runner.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                str(start_head),
                str(candidate_head),
            ],
            command_id="lck-remediation-owned-candidate-ancestry",
        )
        if ancestor.returncode != 0:
            raise LckStopError(
                "Remediation committed candidate is not a descendant of its session start head"
            )
        return True

    def complete(
        self,
        task_number: int,
        review_id: str,
        *,
        commit_message: str,
        summary: str,
        risks: str = "",
    ) -> RemediationCompletionResult:
        self.last_checks = None
        if self.store.read_review_required(task_number) is not None:
            raise LckStopError(
                "Remediation STOP: a fresh Independent Review is required after the previous remediation"
            )
        session = self.store.read_remediation_session(task_number)
        start_head: str | None = None
        if session is not None:
            if session.get("review_id") != review_id:
                raise LckStopError(
                    "Remediation Complete review id does not match the prepared Remediation session"
                )
            candidate = session.get("start_head_sha")
            if not is_sha(candidate):
                raise LckStopError("Remediation session start head is unavailable")
            start_head = cast(str, candidate)
        else:
            # Backward-compatible path for an already-prepared Codex workspace from
            # before remediation sessions were introduced. Existing local Review
            # records retain their previous behavior.
            record = _failed_review_record(self.store, task_number, review_id)
            reviewed_identity = _identity_from_mapping(
                cast(Mapping[str, Any], record["identity"])
            )
            start_head = reviewed_identity.head_sha
        snapshot = self.snapshots.acquire(
            task_number,
            operation=Phase.REMEDIATION_COMPLETE.value,
        )
        self.last_snapshot = snapshot
        state = snapshot.state
        owned_candidate = False
        pr_head = state.open_pr.get("headRefOid") if state.open_pr else None
        if (
            session is not None
            and session.get("candidate") is not None
            and state.local_issue_head != pr_head
        ):
            owned_candidate = self._owned_candidate_recovery(
                session,
                state,
                task_number=task_number,
                review_id=review_id,
            )
        decision = self.eligibility.resolve(
            state,
            Phase.REMEDIATION_COMPLETE,
            owned_remediation_candidate=owned_candidate,
        )
        if not decision.eligible:
            raise LckStopError(
                f"Remediation Complete STOP for Task #{task_number}: "
                + "; ".join(decision.reasons)
            )
        if state.git.get("clean") is True and state.local_issue_head == start_head:
            raise LckStopError(
                "Remediation Complete requires a repaired head or uncommitted repair changes"
            )

        # Initial Delivery already owns the bounded validate/commit/push/check
        # mechanics.  Remediation deliberately reuses those effects while
        # replacing PR create/resolve with a strict existing-PR postcondition.
        def record_candidate(head_sha: str, tree_oid: str) -> None:
            if session is None or start_head is None:
                return
            self.store.record_remediation_candidate(
                task_number,
                review_id,
                start_head_sha=start_head,
                candidate_head_sha=head_sha,
                candidate_tree_oid=tree_oid,
            )

        delivery_completer = DeliveryCompleter(
            self.resolver,
            pr_effect=cast(Any, ReuseExistingOpenPrEffect(self.resolver)),
            checks_gate=self.checks_gate,
            policy_registry=self.policy_registry,
            profile_resolver=self.profile_resolver,
            require_existing_open_pr=True,
            candidate_recorder=record_candidate,
        )
        try:
            delivery = delivery_completer.complete(
                task_number,
                commit_message=commit_message,
                summary=summary,
                risks=risks,
                operation_snapshot=snapshot,
                phase=Phase.REMEDIATION_COMPLETE,
                owned_remediation_candidate=owned_candidate,
            )
        except BaseException:
            self._capture_delivery_evidence(delivery_completer)
            raise
        self._capture_delivery_evidence(delivery_completer)
        if delivery.head_sha == start_head:
            raise LckStopError(
                "Remediation Complete did not produce a new head; fresh Review boundary cannot advance"
            )
        self.store.write_review_required(task_number, review_id, delivery.head_sha)
        self.store.clear_remediation_session(task_number)
        return RemediationCompletionResult(
            task_number=task_number,
            review_id=review_id,
            delivery=delivery,
        )
