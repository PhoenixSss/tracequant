from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from workflow_common import (
    WorkflowToolError,
    atomic_write_json,
    read_json_file,
    safe_text,
    sha256_json,
)

from .closeout import CloseoutResult
from .delivery import DeliveryCompletionResult, DeliveryContext
from .models import (
    LCK_SCHEMA_VERSION,
    EffectReceipt,
    LckStopError,
    LiveState,
    OperationSnapshot,
    _authoritative_remote_main_sha,
    _checks_agent_view,
    _critical_outcome_agent_view,
    _jsonable,
    _pr_agent_view,
    _pr_base_sha,
    _pr_head_sha,
    _validation_agent_view,
)
from .profile_policies import ProfileEvidenceEnvelope
from .remediation import (
    RemediationCompletionResult,
    RemediationContext,
    RemediationNoChangeResult,
)
from .review import MergePreflightResult, ReviewCompletionResult, ReviewContext
from .review_workspace import ReviewInvocationStore
from .state import _leaf_contract_from_state


@dataclass(frozen=True)
class AuditReceiptReference:
    """Stable, bounded locator for one operation-owned audit receipt."""

    operation_id: str
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "operation_id": self.operation_id,
            "path": self.path,
            "sha256": self.sha256,
        }


class AuditReceiptStore:
    """Persist complete operation evidence below the ignored LCK runtime root.

    Agent-facing results contain only a compact view and this reference.  The
    receipt is deliberately content-addressed in its reference and refuses to
    silently overwrite an existing operation with a different payload.
    """

    _OPERATION = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    _ID = re.compile(r"^[0-9a-f]{32}$")

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.root = self.repo_root / ".workflow.local" / "lck" / "audit-receipts"

    def receipt_path(self, task_number: int, operation: str, operation_id: str) -> Path:
        operation = operation.strip().casefold()
        if self._OPERATION.fullmatch(operation) is None:
            raise LckStopError("invalid LCK audit receipt operation")
        if self._ID.fullmatch(operation_id) is None:
            raise LckStopError("invalid LCK audit receipt operation id")
        if not isinstance(task_number, int) or isinstance(task_number, bool):
            raise LckStopError("invalid LCK audit receipt Task number")
        return self.root / operation / f"task-{task_number}-{operation_id}.json"

    def reference_for(
        self, task_number: int, operation: str, operation_id: str, sha256: str
    ) -> AuditReceiptReference:
        path = self.receipt_path(task_number, operation, operation_id)
        return AuditReceiptReference(
            operation_id=operation_id,
            path=path.relative_to(self.repo_root).as_posix(),
            sha256=sha256,
        )

    def write(
        self,
        task_number: int,
        operation: str,
        operation_id: str,
        payload: Mapping[str, Any],
    ) -> AuditReceiptReference:
        path = self.receipt_path(task_number, operation, operation_id)
        normalized = cast(dict[str, Any], _jsonable(payload))
        digest = sha256_json(normalized)
        if path.exists():
            existing = read_json_file(path)
            if existing != normalized:
                raise LckStopError(
                    "existing LCK audit receipt does not match this operation"
                )
        else:
            atomic_write_json(path, normalized)
        return self.reference_for(task_number, operation, operation_id, digest)

    def existing_reference(
        self, task_number: int, operation: str, operation_id: str
    ) -> AuditReceiptReference | None:
        """Return the reference for an existing, identity-matching receipt."""
        path = self.receipt_path(task_number, operation, operation_id)
        if not path.exists():
            return None
        existing = read_json_file(path)
        if (
            not isinstance(existing, dict)
            or existing.get("kind") != "lck-audit-receipt"
            or existing.get("operation") != operation
            or existing.get("operation_id") != operation_id
            or existing.get("task_number") != task_number
        ):
            raise LckStopError("existing LCK audit receipt identity is invalid")
        return self.reference_for(
            task_number,
            operation,
            operation_id,
            sha256_json(existing),
        )

    def read(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        path_value = reference.get("path")
        expected_digest = reference.get("sha256")
        operation_id = reference.get("operation_id")
        if (
            not isinstance(path_value, str)
            or not isinstance(expected_digest, str)
            or not isinstance(operation_id, str)
            or self._ID.fullmatch(operation_id) is None
        ):
            raise LckStopError("LCK audit receipt reference is malformed")
        path = (self.repo_root / path_value).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise LckStopError(
                "LCK audit receipt reference escapes its owned root"
            ) from exc
        if not path.name.endswith(f"-{operation_id}.json"):
            raise LckStopError(
                "LCK audit receipt reference identity does not match its path"
            )
        value = read_json_file(path)
        if (
            not isinstance(value, dict)
            or value.get("operation_id") != operation_id
            or sha256_json(value) != expected_digest
        ):
            raise LckStopError("LCK audit receipt digest does not match its reference")
        return value


def _effect_agent_view(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, EffectReceipt):
            result.append({"effect": item.effect, "action": item.action})
        elif isinstance(item, Mapping):
            compact = {key: item[key] for key in ("effect", "action") if key in item}
            if compact:
                result.append(compact)
    return result


def _delivery_pr_agent_view(effects: Any) -> dict[str, Any] | None:
    if not isinstance(effects, (list, tuple)):
        return None
    for item in reversed(effects):
        details: Mapping[str, Any] | None = None
        if isinstance(item, EffectReceipt):
            details = item.details
        elif isinstance(item, Mapping) and isinstance(item.get("details"), Mapping):
            details = cast(Mapping[str, Any], item["details"])
        if details is not None and "number" in details:
            return {
                key: details[key]
                for key in ("number", "url", "base_sha", "head_sha")
                if key in details
            }
    return None


def _issue_profile_agent_view(value: Any) -> Any:
    """Return the bounded profile selected by the operation's live snapshot."""
    if isinstance(value, ReviewContext):
        return _jsonable(value.issue_profile)
    if isinstance(value, ReviewCompletionResult):
        return _jsonable(value.issue_profile)
    snapshot = getattr(value, "operation_snapshot", None)
    if isinstance(snapshot, OperationSnapshot):
        return _jsonable(snapshot.state.issue_profile)
    return None


def _profile_evidence(value: Any) -> Any:
    """Serialize the generic profile envelope without profile-specific fields."""
    if isinstance(value, ProfileEvidenceEnvelope):
        return value.to_dict()
    if isinstance(value, Mapping):
        return _jsonable(value)
    return None


def _agent_view_for_result(value: Any) -> dict[str, Any]:
    """Convert one internal LCK result into the bounded Agent-facing view."""
    if isinstance(value, LiveState):
        return value.agent_view()
    if isinstance(value, DeliveryContext):
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "delivery-prepare",
            "task_number": value.task_number,
            "repository": value.repository,
            "issue_profile": _issue_profile_agent_view(value),
            "status": "READY_FOR_DELIVERY",
            "branch": value.branch,
            "base_sha": value.base_sha,
            "action": value.action,
            "task_contract": _jsonable(_leaf_contract_from_state(value.state)),
            "eligibility": {
                "eligible": value.eligibility.eligible,
                "reasons": list(value.eligibility.reasons),
            },
            "human_boundary": "implement the Task before LCK Delivery Complete",
            "next_action": "implement the Task and run LCK Delivery Complete",
        }
    if isinstance(value, ReviewContext):
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "review-prepare",
            "status": "READY_FOR_SEMANTIC_REVIEW",
            "task_number": value.identity.task_number,
            "review_id": value.review_id,
            "issue_profile": _issue_profile_agent_view(value),
            "task_contract": _jsonable(value.task_contract),
            "review_target": value.identity.to_dict(),
            "profile_evidence": _profile_evidence(value.profile_evidence),
            "checks": _checks_agent_view(value.checks),
            "validation": _validation_agent_view(value.validation),
            "review_root": str(value.review_root),
            "workspace_mode": "implementation-read-only",
            "agent_role": ["Inspect", "Reason", "Judge", "Report"],
            "mechanical_authority": "live Git/GitHub state resolved by LCK",
            "human_boundary": "semantic Review must be completed before Review Complete",
            "next_action": "perform an independent semantic Review, then run Review Complete",
        }
    if isinstance(value, ReviewCompletionResult):
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "review-complete",
            "task_number": value.task_number,
            "review_id": value.review_id,
            "verdict": value.verdict,
            "status": value.status,
            "issue_profile": _issue_profile_agent_view(value),
            "review_target": value.identity.to_dict(),
            "profile_evidence": _profile_evidence(value.profile_evidence),
            "human_boundary": (
                "STOP; run deterministic Merge Preflight before any manual merge"
                if value.verdict == "PASS"
                else "STOP; Human must explicitly choose remediation, redesign, or abandon"
            ),
            "next_action": (
                "run LCK Merge Preflight"
                if value.verdict == "PASS"
                else "stop; Human must explicitly choose remediation, redesign, or abandon"
            ),
        }
    if isinstance(value, DeliveryCompletionResult):
        base_sha = _authoritative_remote_main_sha(value.operation_snapshot.state.git)
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "delivery-complete",
            "task_number": value.task_number,
            "issue_profile": _issue_profile_agent_view(value),
            "status": value.status,
            "branch": value.branch,
            "base_sha": base_sha,
            "head_sha": value.head_sha,
            "pr": _delivery_pr_agent_view(value.effects),
            "critical_outcome": _critical_outcome_agent_view(value.critical_outcome),
            "research_artifact": _jsonable(value.research_artifact),
            "profile_evidence": _profile_evidence(value.profile_evidence),
            "validation": _validation_agent_view(value.validation),
            "checks": _checks_agent_view(value.checks),
            "effects": _effect_agent_view(value.effects),
            "human_boundary": "Independent Review must be started separately",
            "next_action": "start an independent Review in a fresh invocation",
        }
    if isinstance(value, MergePreflightResult):
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "merge-preflight",
            "task_number": value.task_number,
            "issue_profile": _issue_profile_agent_view(value),
            "status": value.status,
            "pr": _pr_agent_view(value.pr),
            "review": {
                key: value.review[key]
                for key in ("status", "review_id", "identity")
                if key in value.review
            },
            "checks": _checks_agent_view(value.checks),
            "blockers": {
                key: value.blockers[key]
                for key in ("status", "detail", "count")
                if key in value.blockers
            },
            "mergeability": value.mergeability,
            "human_boundary": "STOP — maintainer must perform the manual Squash Merge; LCK has no auto-merge path",
            "next_action": "maintainer must perform the manual Squash Merge",
        }
    if isinstance(value, CloseoutResult):
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "closeout",
            "task_number": value.task_number,
            "issue_profile": _issue_profile_agent_view(value),
            "status": value.status,
            "business_delivery": value.business_delivery,
            "cleanup": value.cleanup,
            "research_outcome": value.research_outcome,
            "profile_evidence": _profile_evidence(value.profile_evidence),
            "effects": _effect_agent_view(value.effects),
            "automatic_merge": False,
            "manual_issue_close": False,
            "next_action": (
                "stop; closeout is complete"
                if value.business_delivery == "COMPLETE" and value.cleanup == "COMPLETE"
                else "resolve pending Research Outcome and closeout effects"
                if value.business_delivery != "COMPLETE"
                else "resolve pending closeout cleanup"
            ),
        }
    if isinstance(value, RemediationContext):
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "remediation-prepare",
            "task_number": value.task_number,
            "review_id": value.review_id,
            "issue_profile": _issue_profile_agent_view(value),
            "status": "READY_FOR_REMEDIATION",
            "action": value.action,
            "findings": value.findings,
            "findings_source": value.findings_source,
            "task_contract": _jsonable(value.task_contract),
            "live_target": {
                "pr_number": (value.state.open_pr or {}).get("number"),
                "base_sha": _pr_base_sha(value.state.open_pr),
                "head_sha": _pr_head_sha(value.state.open_pr),
                "branch": value.state.target_branch,
            },
            "next_action": "repair the implementation and run Remediation Complete",
        }
    if isinstance(value, RemediationNoChangeResult):
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "remediation-no-change",
            "task_number": value.task_number,
            "review_id": value.review_id,
            "issue_profile": _issue_profile_agent_view(value),
            "status": "NO_IMPLEMENTATION_CHANGE",
            "head_sha": value.head_sha,
            "pr_number": value.pr_number,
            "base_sha": value.base_sha,
            "summary": value.summary,
            "candidate_changed": False,
            "fresh_review_required": False,
            "session_released": True,
            "replayed": value.replayed,
            "human_boundary": "STOP — continue external acceptance work on the unchanged head",
            "next_action": "continue external acceptance work on the unchanged head",
        }
    if isinstance(value, RemediationCompletionResult):
        delivery = value.delivery
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "lck-agent-view",
            "operation": "remediation-complete",
            "task_number": value.task_number,
            "review_id": value.review_id,
            "issue_profile": _issue_profile_agent_view(delivery),
            "status": "READY_FOR_NEW_REVIEW",
            "head_sha": delivery.head_sha,
            "critical_outcome": _critical_outcome_agent_view(delivery.critical_outcome),
            "profile_evidence": _profile_evidence(delivery.profile_evidence),
            "validation": _validation_agent_view(delivery.validation),
            "checks": _checks_agent_view(delivery.checks),
            "effects": _effect_agent_view(delivery.effects),
            "human_boundary": "STOP — a new Independent Review must be started explicitly in a fresh invocation",
            "next_action": "start a new independent Review in a fresh invocation",
        }
    raise LckStopError(f"unsupported LCK result type: {type(value).__name__}")


def _audit_payload_for_result(
    value: Any,
    *,
    store: AuditReceiptStore,
) -> dict[str, Any]:
    """Build the complete receipt payload without using it as Agent output."""
    payload = value.to_dict()
    if isinstance(value, LiveState):
        state_payload = dict(payload)
        payload["operation_snapshot"] = {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "status",
            "state": state_payload,
        }
    elif isinstance(value, ReviewContext):
        guard = ReviewInvocationStore(store.repo_root).read_guard(value.review_id)
        payload["review_guard"] = guard
        payload["operation_snapshot"] = guard.get("snapshot")
    elif isinstance(value, ReviewCompletionResult):
        if value.record_path.exists():
            record = read_json_file(value.record_path)
            payload["review_record"] = record
            if isinstance(record, Mapping):
                payload["operation_snapshot"] = record.get("completion_snapshot")
    elif isinstance(value, MergePreflightResult):
        if value.operation_snapshot is not None:
            payload["operation_snapshot"] = value.operation_snapshot.to_dict()
    return cast(dict[str, Any], _jsonable(payload))


def _result_operation_id(value: Any, fallback: str) -> str:
    for attribute in ("review_id",):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str) and AuditReceiptStore._ID.fullmatch(candidate):
            return candidate
    return fallback


def _write_success_receipt(
    value: Any,
    *,
    operation: str,
    task_number: int,
    operation_id: str,
    store: AuditReceiptStore,
) -> dict[str, Any]:
    agent_view = cast(dict[str, Any], _jsonable(_agent_view_for_result(value)))
    if isinstance(value, RemediationNoChangeResult) and value.replayed:
        existing = store.existing_reference(task_number, operation, operation_id)
        if existing is not None:
            agent_view["receipt_reference"] = existing.to_dict()
            return agent_view
    audit_payload = _audit_payload_for_result(value, store=store)
    receipt_agent_view = dict(agent_view)
    if isinstance(value, RemediationNoChangeResult):
        # ``replayed`` describes this CLI invocation, not the durable operation.
        # Keep the original operation receipt immutable across valid replays.
        receipt_agent_view["replayed"] = False
        audit_payload = dict(audit_payload)
        audit_payload["replayed"] = False
    receipt_payload = {
        "schema_version": LCK_SCHEMA_VERSION,
        "kind": "lck-audit-receipt",
        "operation": operation,
        "operation_id": operation_id,
        "task_number": task_number,
        "outcome": {"status": agent_view.get("status")},
        "agent_view": receipt_agent_view,
        "operation_snapshot": audit_payload.get("operation_snapshot"),
        "audit": audit_payload,
    }
    reference = store.write(
        task_number,
        operation,
        operation_id,
        receipt_payload,
    )
    agent_view["receipt_reference"] = reference.to_dict()
    return agent_view


def _write_failure_receipt(
    *,
    operation: str,
    task_number: int,
    operation_id: str,
    status: str,
    code: str | None,
    error: str,
    handler: Any,
    store: AuditReceiptStore,
) -> dict[str, Any]:
    snapshot = getattr(handler, "last_snapshot", None)
    snapshot_payload = (
        snapshot.to_dict() if isinstance(snapshot, OperationSnapshot) else None
    )
    detail = {
        "status": status,
        "code": code,
        "error": safe_text(error, limit=2000),
    }
    next_action = _failure_next_action(status)
    agent_view = {
        "schema_version": LCK_SCHEMA_VERSION,
        "kind": "lck-agent-view",
        "operation": operation,
        "task_number": task_number,
        "issue_profile": _jsonable(
            snapshot.state.issue_profile
            if isinstance(snapshot, OperationSnapshot)
            else None
        ),
        **detail,
        "next_action": next_action,
    }
    receipt_payload = {
        "schema_version": LCK_SCHEMA_VERSION,
        "kind": "lck-audit-receipt",
        "operation": operation,
        "operation_id": operation_id,
        "task_number": task_number,
        "outcome": detail,
        "agent_view": agent_view,
        "operation_snapshot": snapshot_payload,
        "audit": {
            "outcome": detail,
            "operation_snapshot": snapshot_payload,
            "critical_outcome": _jsonable(
                getattr(handler, "last_critical_outcome", None)
            ),
            "documentation_policy": _jsonable(
                getattr(handler, "last_documentation_validation", None)
            ),
            "profile_evidence": _profile_evidence(
                getattr(handler, "last_profile_evidence", None)
            ),
            "validation": _jsonable(getattr(handler, "last_validation", None)),
            "checks": _jsonable(getattr(handler, "last_checks", None)),
            "effects": _jsonable(
                [
                    item.to_dict() if isinstance(item, EffectReceipt) else item
                    for item in getattr(handler, "last_effects", [])
                ]
            ),
        },
    }
    reference = store.write(
        task_number,
        operation,
        operation_id,
        receipt_payload,
    )
    view = cast(dict[str, Any], dict(agent_view))
    view["receipt_reference"] = reference.to_dict()
    return cast(dict[str, Any], _jsonable(view))


def _failure_next_action(status: str) -> str:
    return (
        "start a fresh Review Prepare for the current target"
        if status == "stale"
        else "inspect the receipt and resolve the STOP condition"
    )


def _failure_fallback(
    *,
    operation: str,
    task_number: int,
    operation_id: str,
    status: str,
    code: str | None,
    error: str,
    receipt_error: WorkflowToolError,
    store: AuditReceiptStore,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": LCK_SCHEMA_VERSION,
        "kind": "lck-agent-view",
        "operation": operation,
        "task_number": task_number,
        "status": status,
        "code": code,
        "error": safe_text(error, limit=2000),
        "receipt_error": safe_text(str(receipt_error), limit=1000),
        "next_action": _failure_next_action(status),
    }
    try:
        reference = store.existing_reference(task_number, operation, operation_id)
    except WorkflowToolError:
        reference = None
    if reference is not None:
        payload["receipt_reference"] = reference.to_dict()
    return payload
