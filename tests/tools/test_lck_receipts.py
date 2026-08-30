# ruff: noqa: E402, I001

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import (
    Any,
    cast,
)

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    cli as lck_cli,
    closeout as lck_closeout,
    eligibility as lck_eligibility,
    models as lck_models,
    receipts as lck_receipts,
    remediation as lck_remediation,
    review as lck_review,
    review_workspace as lck_review_workspace,
)
from lck_test_support import (  # noqa: E402
    FakeReviewChecks,
    FakeReviewWorkspace,
    StaticResolver,
    SHA,
    _review_guard,
    _review_identity_value,
    _review_state,
)


def test_closeout_failure_receipt_preserves_completed_effects(
    tmp_path: Path,
) -> None:
    state = replace(
        _review_state(),
        merged_pr={"number": 200},
    )
    resolver = cast(Any, StaticResolver(tmp_path, state))

    class Eligible:
        def resolve(
            self,
            _state: lck_models.LiveState,
            phase: lck_models.Phase,
        ) -> lck_eligibility.PhaseDecision:
            return lck_eligibility.PhaseDecision(phase=phase, eligible=True)

    class Main:
        def execute(
            self,
            _state: lck_models.LiveState,
            *,
            merge_sha: str,
        ) -> lck_models.EffectReceipt:
            return lck_models.EffectReceipt(
                "synchronize_main",
                "synchronized",
                {"merge_sha": merge_sha},
            )

    class FailingMetadata:
        def execute(self, _state: lck_models.LiveState) -> lck_models.EffectReceipt:
            raise lck_models.LckStopError("metadata convergence failed")

    handler = lck_closeout.CloseoutCompleter(
        resolver,
        eligibility=cast(Any, Eligible()),
        main_effect=cast(Any, Main()),
        metadata_effect=cast(Any, FailingMetadata()),
    )
    handler._validate_merged_identity = lambda _state: (SHA, "b" * 40)
    handler._validate_reviewed_identity = lambda _state, _pr: None

    with pytest.raises(lck_models.LckStopError, match="metadata convergence failed"):
        handler.complete(159)

    assert [item.effect for item in handler.last_effects] == ["synchronize_main"]
    store = lck_receipts.AuditReceiptStore(tmp_path)
    payload = lck_receipts._write_failure_receipt(
        operation="closeout",
        task_number=159,
        operation_id="e" * 32,
        status="stop",
        code=None,
        error="metadata convergence failed",
        handler=handler,
        store=store,
    )
    receipt = store.read(payload["receipt_reference"])

    assert receipt["audit"]["effects"] == [
        {
            "effect": "synchronize_main",
            "action": "synchronized",
            "details": {"merge_sha": "b" * 40},
        }
    ]


def test_merge_preflight_failure_receipt_preserves_strict_check_evidence(
    tmp_path: Path,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))

    class PassingReview:
        def run(
            self, _task_number: int, _state: lck_models.LiveState
        ) -> dict[str, Any]:
            return {"status": "pass", "review_id": "r" * 32}

    class FailingChecks:
        last_result: dict[str, Any] | None = None

        def evaluate(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
            pr = snapshot.state.open_pr or {}
            self.last_result = {
                "status": "fail",
                "check_state": "pending",
                "required": ["quality"],
                "pr": {
                    "number": pr["number"],
                    "head_sha": pr["headRefOid"],
                    "base_sha": pr["baseRefOid"],
                },
            }
            raise lck_models.LckStopError("PR checks are pending")

    checks = FailingChecks()
    handler = lck_review.MergePreflight(
        resolver,
        review_gate=cast(Any, PassingReview()),
        checks_gate=cast(Any, checks),
    )

    with pytest.raises(lck_models.LckStopError, match="PR checks are pending"):
        handler.run(159)

    assert handler.last_checks == checks.last_result
    store = lck_receipts.AuditReceiptStore(tmp_path)
    payload = lck_receipts._write_failure_receipt(
        operation="merge-preflight",
        task_number=159,
        operation_id="a" * 32,
        status="stop",
        code=None,
        error="PR checks are pending",
        handler=handler,
        store=store,
    )
    receipt = store.read(payload["receipt_reference"])

    assert receipt["audit"]["checks"] == checks.last_result


def test_review_prepare_returns_compact_agent_view_and_full_audit_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Critical Outcome: Review Prepare separates Agent output from evidence."""
    state = _review_state()
    identity = _review_identity_value()
    review_id = "b" * 32
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    snapshot = lck_models.OperationSnapshot(
        operation="review-prepare",
        state=state,
        fact_profile="review-prepare",
        acquired_facts=("task_contract", "remote_task_branches", "open_pr", "checks"),
    )
    guard = _review_guard(identity, review_root=review_root)
    guard.update(
        {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "kind": "review-invocation-guard",
            "review_id": review_id,
            "snapshot": snapshot.to_dict(),
        }
    )
    store.write_guard(review_id, guard)
    context = lck_review.ReviewContext(
        review_id=review_id,
        task_contract=state.task_contract or {},
        identity=identity,
        checks={
            "status": "observed",
            "check_state": "pending",
            "pr": {"number": 200, "head_sha": SHA, "base_sha": SHA},
            "observed": {"quality": {"status": "pending"}},
        },
        validation={"status": "pass", "commands": [{"command_id": "pytest"}]},
        review_root=review_root,
    )

    class FakePreparer:
        def __init__(self, _resolver: Any) -> None:
            pass

        def prepare(self, _task_number: int) -> lck_review.ReviewContext:
            return context

    resolver = StaticResolver(tmp_path, state)
    monkeypatch.setattr(
        lck_cli,
        "LiveStateResolver",
        lambda _root, repository=None: resolver,
    )
    monkeypatch.setattr(lck_cli, "ReviewPreparer", FakePreparer)

    exit_code = lck_cli.main(
        [
            "--repo-root",
            str(tmp_path),
            "--repository",
            "owner/repo",
            "review",
            "prepare",
            "159",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["kind"] == "lck-agent-view"
    assert payload["status"] == "READY_FOR_SEMANTIC_REVIEW"
    assert payload["review_target"] == identity.to_dict()
    assert payload["task_contract"]["body"] == "Task Contract"
    assert "operation_snapshot" not in payload
    assert "observed" not in payload["checks"]

    reference = payload["receipt_reference"]
    receipt = lck_receipts.AuditReceiptStore(tmp_path).read(reference)
    assert receipt["kind"] == "lck-audit-receipt"
    assert receipt["operation"] == "review-prepare"
    assert receipt["operation_snapshot"] == snapshot.to_dict()
    assert receipt["agent_view"]["review_target"] == payload["review_target"]
    assert receipt["audit"]["review_guard"]["review_id"] == review_id


def test_review_prepare_failure_receipt_preserves_validation_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    identity = _review_identity_value()
    validation = {
        "status": "fail",
        "phase": "review",
        "commands": [
            {
                "command_id": "pytest",
                "status": "fail",
                "exit_code": 1,
                "diagnostic": "review validation failed",
            }
        ],
        "evidence_path": ".workflow.local/lck/review-validation/run",
    }

    class FailedValidation:
        def run(self, _root: Path, _base: str, _head: str) -> dict[str, Any]:
            return validation

    resolver = cast(Any, StaticResolver(tmp_path, state))
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    handler = lck_review.ReviewPreparer(
        resolver,
        validation=cast(Any, FailedValidation()),
        checks_gate=cast(Any, FakeReviewChecks()),
        workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
        store=lck_review_workspace.ReviewInvocationStore(tmp_path),
    )

    with pytest.raises(
        lck_models.LckStopError, match="formal Review validation failed"
    ):
        handler.prepare(159)

    store = lck_receipts.AuditReceiptStore(tmp_path)
    payload = lck_receipts._write_failure_receipt(
        operation="review-prepare",
        task_number=159,
        operation_id="c" * 32,
        status="stop",
        code=None,
        error="formal Review validation failed: fail",
        handler=handler,
        store=store,
    )
    receipt = store.read(payload["receipt_reference"])

    assert receipt["audit"]["validation"] == validation


def test_lck_stop_result_has_structured_receipt_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingResolver:
        repo_root = tmp_path

        def resolve(self, _task_number: int) -> lck_models.LiveState:
            raise lck_models.LckStopError("live state is unavailable")

    resolver = FailingResolver()
    monkeypatch.setattr(
        lck_cli,
        "LiveStateResolver",
        lambda _root, repository=None: resolver,
    )

    exit_code = lck_cli.main(["--repo-root", str(tmp_path), "status", "159"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["kind"] == "lck-agent-view"
    assert payload["operation"] == "status"
    assert payload["status"] == "stop"
    assert payload["next_action"]
    receipt = lck_receipts.AuditReceiptStore(tmp_path).read(
        payload["receipt_reference"]
    )
    assert receipt["outcome"]["status"] == "stop"
    assert receipt["agent_view"]["error"] == "live state is unavailable"


def test_remediation_no_change_cli_replay_reuses_audit_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_remediation_session(
        159,
        {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "kind": "remediation-session",
            "task_number": 159,
            "review_id": review_id,
            "start_head_sha": SHA,
            "pr_number": 200,
            "base_sha": SHA,
            "findings_sha256": "f" * 64,
            "findings_source": "local-review-record",
            "authority": "test",
        },
    )
    monkeypatch.setattr(
        lck_cli,
        "LiveStateResolver",
        lambda _root, repository=None: resolver,
    )
    argv = [
        "--repo-root",
        str(tmp_path),
        "remediation",
        "no-change",
        "159",
        "--review-id",
        review_id,
        "--summary",
        "No implementation change required.",
    ]

    first_exit = lck_cli.main(argv)
    first = json.loads(capsys.readouterr().out)
    second_exit = lck_cli.main(argv)
    second = json.loads(capsys.readouterr().out)

    assert first_exit == second_exit == 0
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["receipt_reference"] == second["receipt_reference"]
    assert second["next_action"]
    receipt = lck_receipts.AuditReceiptStore(tmp_path).read(second["receipt_reference"])
    assert receipt["agent_view"]["replayed"] is False
    assert receipt["audit"]["replayed"] is False


def test_closeout_agent_view_does_not_stop_when_research_outcome_is_pending() -> None:
    result = lck_closeout.CloseoutResult(
        task_number=159,
        status="BUSINESS_DELIVERY_COMPLETE",
        business_delivery="PENDING",
        cleanup="COMPLETE",
        effects=(),
        operation_snapshot=lck_models.OperationSnapshot(
            operation="closeout",
            state=_review_state(),
        ),
        research_outcome=None,
    )

    view = lck_receipts._agent_view_for_result(result)

    assert "Research Outcome" in view["next_action"]
    assert view["next_action"] != "stop; closeout is complete"


def test_review_complete_failure_receipt_preserves_bound_snapshot_and_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value()
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(review_id, _review_guard(identity, review_root=review_root))

    class FailingChecks:
        last_result: dict[str, Any] | None = None

        def evaluate(self, snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
            self.last_result = {
                "status": "fail",
                "check_state": "pending",
                "required": snapshot.required_checks["contexts"]["items"]
                if snapshot.required_checks is not None
                else [],
                "pr": {"number": 200, "head_sha": SHA, "base_sha": SHA},
            }
            raise lck_models.LckStopError("PR checks are pending")

    handler = lck_review.ReviewCompleter(
        resolver,
        checks_gate=cast(Any, FailingChecks()),
        store=store,
        workspace=cast(Any, FakeReviewWorkspace(review_root)),
    )

    with pytest.raises(lck_models.LckStopError, match="PR checks are pending"):
        handler.complete(159, review_id, verdict="PASS")

    assert handler.last_snapshot is not None
    assert handler.last_snapshot.required_checks is not None
    assert handler.last_snapshot.required_checks["source_sha"] == SHA
    assert handler.last_snapshot.required_checks["contexts"]["items"] == ["quality"]
    assert handler.last_checks == {
        "status": "fail",
        "check_state": "pending",
        "required": ["quality"],
        "pr": {"number": 200, "head_sha": SHA, "base_sha": SHA},
    }

    audit_store = lck_receipts.AuditReceiptStore(tmp_path)
    payload = lck_receipts._write_failure_receipt(
        operation="review-complete",
        task_number=159,
        operation_id="d" * 32,
        status="stop",
        code=None,
        error="PR checks are pending",
        handler=handler,
        store=audit_store,
    )
    receipt = audit_store.read(payload["receipt_reference"])

    assert receipt["operation_snapshot"]["required_checks"]["source_sha"] == SHA
    assert receipt["operation_snapshot"]["required_checks"]["contexts"]["items"] == [
        "quality"
    ]
    assert receipt["audit"]["checks"] == handler.last_checks


def test_remediation_failure_receipt_preserves_nested_delivery_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state(clean=False)
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_remediation_session(
        159,
        {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "kind": "remediation-session",
            "task_number": 159,
            "review_id": review_id,
            "start_head_sha": SHA,
            "pr_number": 200,
            "base_sha": SHA,
            "findings_sha256": "f" * 64,
            "findings_source": "local-review-record",
            "authority": "test",
        },
    )
    failed_validation = {
        "status": "fail",
        "commands": [
            {
                "command_id": "pytest",
                "exit_code": 1,
                "log_path": ".agents/validation.local/pytest.log",
                "duration_ms": 23,
                "diagnostic": "assertion failed",
            }
        ],
    }

    class FailingDeliveryCompleter:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.last_snapshot: lck_models.OperationSnapshot | None = None
            self.last_critical_outcome: dict[str, Any] | None = None
            self.last_validation: dict[str, Any] | None = None
            self.last_checks: dict[str, Any] | None = {
                "status": "observed",
                "check_state": "pending",
                "pr": {"number": 200, "head_sha": SHA, "base_sha": SHA},
            }
            self.last_effects: list[lck_models.EffectReceipt] = []

        def complete(self, *_args: Any, **kwargs: Any) -> Any:
            self.last_snapshot = kwargs["operation_snapshot"]
            self.last_critical_outcome = {"status": "pass"}
            self.last_validation = failed_validation
            self.last_effects = [
                lck_models.EffectReceipt(
                    "commit_current_tree",
                    "committed",
                    {"head_sha": "b" * 40},
                )
            ]
            raise lck_models.LckStopError("remote branch effect failed")

    monkeypatch.setattr(lck_remediation, "DeliveryCompleter", FailingDeliveryCompleter)
    handler = lck_remediation.RemediationCompleter(resolver, store=store)

    with pytest.raises(lck_models.LckStopError, match="remote branch effect failed"):
        handler.complete(
            159,
            review_id,
            commit_message="repair",
            summary="repair",
        )

    audit_store = lck_receipts.AuditReceiptStore(tmp_path)
    payload = lck_receipts._write_failure_receipt(
        operation="remediation-complete",
        task_number=159,
        operation_id="e" * 32,
        status="stop",
        code=None,
        error="remote branch effect failed",
        handler=handler,
        store=audit_store,
    )
    receipt = audit_store.read(payload["receipt_reference"])

    assert handler.last_snapshot is not None
    assert receipt["operation_snapshot"] == handler.last_snapshot.to_dict()
    assert receipt["audit"]["validation"] == failed_validation
    assert receipt["audit"]["checks"] == handler.last_checks
    assert receipt["audit"]["effects"][0]["effect"] == "commit_current_tree"
