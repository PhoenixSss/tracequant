# ruff: noqa: E402, I001

from __future__ import annotations

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
    delivery as lck_delivery,
    effects as lck_effects,
    eligibility as lck_eligibility,
    models as lck_models,
    profile_policies as lck_profile_policies,
    remediation as lck_remediation,
    review as lck_review,
    review_workspace as lck_review_workspace,
    validation as lck_validation,
)
from lck_test_support import (  # noqa: E402
    FakeRunner,
    FakeReviewChecks,
    FakeReviewWorkspace,
    OwnedCandidateRunner,
    StaticResolver,
    SHA,
    _install_facts,
    _issue,
    _open_pr,
    _required_policy,
    _resolver,
    _review_guard,
    _review_identity_value,
    _review_state,
    _write_owned_candidate_session,
)


def test_review_prepare_rejects_open_remediation_session(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(clean=True)))
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

    with pytest.raises(lck_models.LckStopError, match="remediation no-change"):
        lck_review.ReviewPreparer(resolver, store=store).prepare(159)

    assert resolver.calls == 0


def test_remediation_prepare_rejects_draft_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch, local_branches={branch}, remote_branches={branch: SHA}
    )
    issue = _issue()
    issue["project_status"] = "Review"
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        open_pr=_open_pr(branch, is_draft=True),
    )

    state = _resolver(fake).resolve(159)
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state,
        lck_models.Phase.REMEDIATION_PREPARE,
    )

    assert not decision.eligible
    assert any("non-Draft" in reason for reason in decision.reasons)


def test_remediation_requires_pr_base_to_match_current_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch, local_branches={branch}, remote_branches={branch: SHA}
    )
    issue = _issue()
    issue["project_status"] = "Review"
    pr = _open_pr(branch)
    pr["baseRefOid"] = "b" * 40
    _install_facts(monkeypatch, fake, issue=issue, open_pr=pr)

    state = _resolver(fake).resolve(159)
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state,
        lck_models.Phase.REMEDIATION_PREPARE,
    )

    assert not decision.eligible
    assert any(
        "base must match current origin/main" in reason for reason in decision.reasons
    )


def test_review_fail_returns_stop_required_without_starting_remediation(
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
    findings = tmp_path / "findings.md"
    findings.write_text("[F1][Medium] Repair this behavior.\n", encoding="utf-8")

    result = lck_review.ReviewCompleter(
        resolver,
        checks_gate=cast(Any, FakeReviewChecks()),
        store=store,
        workspace=cast(Any, FakeReviewWorkspace(review_root)),
    ).complete(159, review_id, verdict="FAIL", findings_file=findings)

    assert result.status == "STOP_REQUIRED"
    assert result.to_dict()["automatic_remediation"] is False
    assert resolver.calls == 1
    record = store.read_record(159, review_id)
    assert record["findings"].startswith("[F1][Medium]")
    assert "fresh Review Complete snapshot matched" in record["authority_note"]


def test_remediation_prepare_uses_live_head_not_review_record_identity(
    tmp_path: Path,
) -> None:
    live_head = "b" * 40
    state = _review_state(head=live_head, base=SHA)
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_record(
        159,
        review_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value(head=SHA).to_dict(),
            "findings": "[F1][Medium] Semantic repair input.",
        },
    )
    store.write_latest_review(159, review_id, "FAIL")

    context = lck_remediation.RemediationPreparer(resolver, store=store).prepare(
        159, review_id
    )

    assert context.to_dict()["live_target"]["head_sha"] == live_head
    assert context.task_contract["body"] == "Task Contract"
    assert context.to_dict()["task_contract"]["body"] == "Task Contract"
    assert context.findings == "[F1][Medium] Semantic repair input."
    assert (
        "operation snapshot acquired at Remediation entry"
        in context.to_dict()["mechanical_authority"]
    )
    assert (
        "do not block Remediation Complete" in context.to_dict()["acceptance_boundary"]
    )
    assert "must not be fabricated" in context.to_dict()["acceptance_boundary"]


def test_remediation_prepare_accepts_portable_findings_when_local_record_missing(
    tmp_path: Path,
) -> None:
    live_head = "b" * 40
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(head=live_head)))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    findings = tmp_path / "review-findings.md"
    findings.write_text("[F1][High] Portable semantic finding.\n", encoding="utf-8")

    context = lck_remediation.RemediationPreparer(resolver, store=store).prepare(
        159,
        review_id,
        findings_file=findings,
    )

    assert context.findings == "[F1][High] Portable semantic finding.\n"
    assert context.findings_source == "portable-findings-file"
    assert context.to_dict()["findings_source"] == "portable-findings-file"
    session = store.read_remediation_session(159)
    assert session is not None
    assert session["review_id"] == review_id
    assert session["start_head_sha"] == live_head
    assert session["findings_source"] == "portable-findings-file"
    assert resolver.calls == 1


def test_remediation_prepare_missing_local_record_requires_portable_findings(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()

    with pytest.raises(lck_models.LckStopError, match="provide --findings-file"):
        lck_remediation.RemediationPreparer(resolver, store=store).prepare(
            159, review_id
        )

    assert resolver.calls == 0


def test_remediation_prepare_local_record_path_remains_primary(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    local_findings = "[F1][Medium] Local Codex review finding."
    store.write_record(
        159,
        review_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value().to_dict(),
            "findings": local_findings,
        },
    )
    store.write_latest_review(159, review_id, "FAIL")
    portable = tmp_path / "portable.md"
    portable.write_text("different portable text", encoding="utf-8")

    context = lck_remediation.RemediationPreparer(resolver, store=store).prepare(
        159, review_id, findings_file=portable
    )

    assert context.findings == local_findings
    assert context.findings_source == "local-review-record"


def test_remediation_complete_uses_prepared_session_without_review_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_head = SHA
    repaired_head = "b" * 40
    state = _review_state(head=start_head, clean=False)
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
            "start_head_sha": start_head,
            "pr_number": 200,
            "base_sha": SHA,
            "findings_sha256": "f" * 64,
            "findings_source": "portable-findings-file",
            "authority": "test",
        },
    )

    def fake_delivery_complete(self: Any, task_number: int, **kwargs: Any) -> Any:
        snapshot = kwargs["operation_snapshot"]
        return lck_delivery.DeliveryCompletionResult(
            task_number=task_number,
            status="READY_FOR_REVIEW",
            branch=state.target_branch,
            head_sha=repaired_head,
            critical_outcome={"status": "valid"},
            validation={"status": "pass"},
            checks={"status": "pass"},
            effects=(),
            operation_snapshot=snapshot,
        )

    monkeypatch.setattr(
        lck_delivery.DeliveryCompleter, "complete", fake_delivery_complete
    )

    result = lck_remediation.RemediationCompleter(resolver, store=store).complete(
        159,
        review_id,
        commit_message="repair",
        summary="repair",
    )

    assert result.delivery.head_sha == repaired_head
    assert store.read_remediation_session(159) is None
    required = store.read_review_required(159)
    assert required is not None
    assert required["remediated_head"] == repaired_head


def test_remediation_no_change_closes_prepared_session_without_new_head(
    tmp_path: Path,
) -> None:
    head = SHA
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(head=head, clean=True)))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_remediation_session(
        159,
        {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "kind": "remediation-session",
            "task_number": 159,
            "review_id": review_id,
            "start_head_sha": head,
            "pr_number": 200,
            "base_sha": SHA,
            "findings_sha256": "f" * 64,
            "findings_source": "local-review-record",
            "authority": "test",
        },
    )

    result = lck_remediation.RemediationNoChangeCompleter(
        resolver, store=store
    ).complete(
        159,
        review_id,
        summary="Only deferred external acceptance evidence remains.",
    )

    payload = result.to_dict()
    assert payload["status"] == "NO_IMPLEMENTATION_CHANGE"
    assert payload["head_sha"] == head
    assert payload["candidate_changed"] is False
    assert payload["fresh_review_required"] is False
    assert payload["session_released"] is True
    assert payload["replayed"] is False
    assert store.read_remediation_session(159) is None
    assert store.read_review_required(159) is None
    receipt = store.read_remediation_no_change_receipt(159, review_id)
    assert receipt is not None
    assert receipt["start_head_sha"] == head
    assert receipt["candidate_changed"] is False
    assert resolver.calls == 1


def test_remediation_no_change_releases_old_session_for_new_review_prepare(
    tmp_path: Path,
) -> None:
    state = _review_state(clean=True)
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    old_review_id = store.new_id()
    store.write_remediation_session(
        159,
        {
            "schema_version": lck_models.LCK_SCHEMA_VERSION,
            "kind": "remediation-session",
            "task_number": 159,
            "review_id": old_review_id,
            "start_head_sha": SHA,
            "pr_number": 200,
            "base_sha": SHA,
            "findings_sha256": "a" * 64,
            "findings_source": "local-review-record",
            "authority": "test",
        },
    )

    lck_remediation.RemediationNoChangeCompleter(resolver, store=store).complete(
        159, old_review_id, summary="No implementation defect remained."
    )

    new_review_id = store.new_id()
    findings = tmp_path / "new-review-findings.md"
    findings.write_text(
        "[F1][Blocking] External acceptance evidence.\n", encoding="utf-8"
    )
    context = lck_remediation.RemediationPreparer(resolver, store=store).prepare(
        159, new_review_id, findings_file=findings
    )

    assert context.review_id == new_review_id
    session = store.read_remediation_session(159)
    assert session is not None
    assert session["review_id"] == new_review_id
    assert session["start_head_sha"] == SHA


def test_remediation_no_change_rejects_wrong_review_or_dirty_tree(
    tmp_path: Path,
) -> None:
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
            "findings_sha256": "b" * 64,
            "findings_source": "local-review-record",
            "authority": "test",
        },
    )
    clean_resolver = cast(Any, StaticResolver(tmp_path, _review_state(clean=True)))

    with pytest.raises(lck_models.LckStopError, match="review id does not match"):
        lck_remediation.RemediationNoChangeCompleter(
            clean_resolver, store=store
        ).complete(159, store.new_id(), summary="wrong review")

    dirty_resolver = cast(Any, StaticResolver(tmp_path, _review_state(clean=False)))
    with pytest.raises(
        lck_models.LckStopError, match="clean tracked and staged worktree"
    ):
        lck_remediation.RemediationNoChangeCompleter(
            dirty_resolver, store=store
        ).complete(159, review_id, summary="no change")

    assert store.read_remediation_session(159) is not None


def test_remediation_no_change_rejects_head_drift(
    tmp_path: Path,
) -> None:
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
            "findings_sha256": "d" * 64,
            "findings_source": "local-review-record",
            "authority": "test",
        },
    )
    moved_head = "b" * 40
    resolver = cast(
        Any, StaticResolver(tmp_path, _review_state(head=moved_head, clean=True))
    )

    with pytest.raises(lck_models.LckStopError, match="target no longer matches"):
        lck_remediation.RemediationNoChangeCompleter(resolver, store=store).complete(
            159, review_id, summary="no change"
        )

    assert store.read_remediation_session(159) is not None
    assert store.read_remediation_no_change_receipt(159, review_id) is None


def test_remediation_no_change_is_idempotent_for_same_unchanged_target(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(clean=True)))
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
            "findings_sha256": "c" * 64,
            "findings_source": "local-review-record",
            "authority": "test",
        },
    )

    first = lck_remediation.RemediationNoChangeCompleter(
        resolver, store=store
    ).complete(159, review_id, summary="No implementation change required.")
    second = lck_remediation.RemediationNoChangeCompleter(
        resolver, store=store
    ).complete(159, review_id, summary="ignored on replay")

    assert first.to_dict()["replayed"] is False
    assert second.to_dict()["replayed"] is True
    assert second.summary == "No implementation change required."
    assert store.read_remediation_session(159) is None


def test_remediation_complete_requires_actual_repair_changes(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(clean=True)))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_record(
        159,
        review_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value().to_dict(),
            "findings": "[F1][Medium] Repair required.",
        },
    )
    store.write_latest_review(159, review_id, "FAIL")

    with pytest.raises(
        lck_models.LckStopError, match="repaired head or uncommitted repair changes"
    ):
        lck_remediation.RemediationCompleter(resolver, store=store).complete(
            159,
            review_id,
            commit_message="Repair review finding",
            summary="Repair finding",
        )


def test_reuse_existing_open_pr_never_creates_a_replacement(tmp_path: Path) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))

    receipt = lck_effects.ReuseExistingOpenPrEffect(resolver).execute(
        state,
        head_sha=SHA,
        summary="ignored",
        risks="ignored",
        critical_outcome={"status": "valid"},
        validation={"status": "pass"},
        expected_base_sha=SHA,
        expected_body_sha256="d" * 64,
    )

    assert receipt.effect == "reuse_open_pr"
    assert receipt.action == "reused-current-open-pr"
    assert any(
        command[:3] == ("gh", "pr", "view") for command in resolver.runner.commands
    )


def test_remediation_requires_latest_failed_review(tmp_path: Path) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    failed_id = store.new_id()
    newer_id = store.new_id()
    store.write_record(
        159,
        failed_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value().to_dict(),
            "findings": "[F1][Medium] Old finding.",
        },
    )
    store.write_latest_review(159, newer_id, "PASS")

    with pytest.raises(
        lck_models.LckStopError, match="latest completed Independent Review"
    ):
        lck_remediation.RemediationPreparer(resolver, store=store).prepare(
            159, failed_id
        )


def test_post_remediation_boundary_blocks_another_remediation_until_review(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(head="b" * 40)))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    failed_id = store.new_id()
    store.write_record(
        159,
        failed_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value().to_dict(),
            "findings": "[F1][Medium] Repair required.",
        },
    )
    store.write_latest_review(159, failed_id, "FAIL")
    store.write_review_required(159, failed_id, "b" * 40)

    with pytest.raises(
        lck_models.LckStopError, match="fresh Independent Review is required"
    ):
        lck_remediation.RemediationPreparer(resolver, store=store).prepare(
            159, failed_id
        )


def test_accepted_fresh_review_releases_post_remediation_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value(head="b" * 40)
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(head="b" * 40)))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    old_review_id = store.new_id()
    store.write_review_required(159, old_review_id, "b" * 40)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(
        review_id,
        _review_guard(identity, review_root=review_root),
    )
    findings = tmp_path / "findings-new.md"
    findings.write_text("[F2][Medium] New review finding.\n", encoding="utf-8")

    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    result = lck_review.ReviewCompleter(
        resolver,
        checks_gate=cast(Any, FakeReviewChecks()),
        store=store,
        workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
    ).complete(159, review_id, verdict="FAIL", findings_file=findings)

    assert result.status == "STOP_REQUIRED"
    assert store.read_review_required(159) is None
    latest = store.read_latest_review(159)
    assert latest is not None
    assert latest["review_id"] == review_id
    assert latest["verdict"] == "FAIL"


def test_remediation_complete_can_resume_committed_new_head_and_requires_re_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repaired_head = "b" * 40
    state = _review_state(head=repaired_head, clean=True)
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    store.write_record(
        159,
        review_id,
        {
            "task_number": 159,
            "verdict": "FAIL",
            "identity": _review_identity_value(head=SHA).to_dict(),
            "findings": "[F1][Medium] Repair required.",
        },
    )
    store.write_latest_review(159, review_id, "FAIL")

    delivery_result = lck_delivery.DeliveryCompletionResult(
        task_number=159,
        status="READY_FOR_REVIEW",
        branch=state.target_branch,
        head_sha=repaired_head,
        critical_outcome={"status": "pass"},
        validation={"status": "pass"},
        checks={"status": "pass"},
        effects=(),
        operation_snapshot=lck_models.OperationSnapshot(
            operation=lck_models.Phase.REMEDIATION_COMPLETE.value,
            state=state,
            required_checks=_required_policy("quality"),
        ),
    )

    class FakeDeliveryCompleter:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def complete(
            self, *_args: Any, **_kwargs: Any
        ) -> lck_delivery.DeliveryCompletionResult:
            return delivery_result

    monkeypatch.setattr(lck_remediation, "DeliveryCompleter", FakeDeliveryCompleter)

    result = lck_remediation.RemediationCompleter(resolver, store=store).complete(
        159,
        review_id,
        commit_message="Repair review finding",
        summary="Repair finding",
    )

    assert result.to_dict()["status"] == "READY_FOR_NEW_REVIEW"
    assert (
        "remains pending for the next Independent Review"
        in result.to_dict()["deferred_review_acceptance"]
    )
    assert result.to_dict()["automatic_review"] is False
    required = store.read_review_required(159)
    assert required is not None
    assert required["remediated_head"] == repaired_head
    with pytest.raises(
        lck_models.LckStopError, match="fresh Independent Review is required"
    ):
        lck_remediation.RemediationPreparer(resolver, store=store).prepare(
            159, review_id
        )


def test_remediation_complete_recovers_exact_owned_partial_effect_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_head = SHA
    candidate_head = "b" * 40
    candidate_tree = "c" * 40
    initial = _review_state(head=start_head, clean=True)
    partial = replace(
        initial,
        git={**initial.git, "head_sha": candidate_head},
        local_task_head=candidate_head,
    )
    resolver = cast(Any, StaticResolver(tmp_path, partial))
    runner = OwnedCandidateRunner(head_sha=candidate_head, tree_oid=candidate_tree)
    resolver.runner = runner
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    _write_owned_candidate_session(
        store,
        review_id,
        start_head=start_head,
        candidate_head=candidate_head,
        candidate_tree=candidate_tree,
    )
    calls = {"critical": 0, "validation": 0}

    class CriticalResult:
        status = "pass"
        exit_code = 0

        def to_dict(self) -> dict[str, Any]:
            return {"status": self.status, "exit_code": self.exit_code}

    def verify_critical_outcome(*_args: Any, **_kwargs: Any) -> CriticalResult:
        calls["critical"] += 1
        return CriticalResult()

    def formal_validation(
        _self: lck_validation.FormalValidationGate, base_sha: str
    ) -> dict[str, Any]:
        assert base_sha == SHA
        calls["validation"] += 1
        return {"status": "pass"}

    monkeypatch.setattr(
        lck_profile_policies, "verify_critical_outcome", verify_critical_outcome
    )
    monkeypatch.setattr(lck_validation.FormalValidationGate, "run", formal_validation)

    result = lck_remediation.RemediationCompleter(resolver, store=store).complete(
        159, review_id, commit_message="repair", summary="repair"
    )

    assert result.to_dict()["status"] == "READY_FOR_NEW_REVIEW"
    assert calls == {"critical": 1, "validation": 1}
    assert resolver.calls == 1
    assert [effect.action for effect in result.delivery.effects] == [
        "already-committed-revalidated",
        "fast-forwarded",
        "reused-current-open-pr",
        "already-review",
    ]
    assert not any(command[:2] == ("git", "commit") for command in runner.commands)
    assert any(command[:3] == ("git", "push", "-u") for command in runner.commands)
    assert store.read_remediation_session(159) is None
    required = store.read_review_required(159)
    assert required is not None
    assert required["remediated_head"] == candidate_head


def test_real_delivery_completer_rejects_unowned_local_ahead_remediation_candidate(
    tmp_path: Path,
) -> None:
    candidate_head = "b" * 40
    state = _review_state(head=SHA, clean=True)
    state = replace(
        state,
        git={**state.git, "head_sha": candidate_head},
        local_task_head=candidate_head,
    )
    resolver = cast(Any, StaticResolver(tmp_path, state))
    snapshot = lck_models.OperationSnapshot(
        operation=lck_models.Phase.REMEDIATION_COMPLETE.value,
        state=state,
    )

    with pytest.raises(
        lck_models.LckStopError,
        match="local Task branch must match current OPEN PR head",
    ):
        lck_delivery.DeliveryCompleter(
            resolver,
            require_existing_open_pr=True,
        ).complete(
            159,
            commit_message="repair",
            summary="repair",
            operation_snapshot=snapshot,
            phase=lck_models.Phase.REMEDIATION_COMPLETE,
        )

    assert resolver.calls == 0


def test_remediation_owned_candidate_recovery_rejects_replaced_local_head(
    tmp_path: Path,
) -> None:
    candidate_head = "b" * 40
    replacement_head = "d" * 40
    state = _review_state(head=SHA, clean=True)
    state = replace(
        state,
        git={**state.git, "head_sha": replacement_head},
        local_task_head=replacement_head,
    )
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    _write_owned_candidate_session(store, review_id, candidate_head=candidate_head)

    with pytest.raises(
        lck_models.LckStopError, match="exactly match its owned session"
    ):
        lck_remediation.RemediationCompleter(resolver, store=store).complete(
            159, review_id, commit_message="repair", summary="repair"
        )


@pytest.mark.parametrize("moved", ["pr", "remote"])
def test_remediation_owned_candidate_recovery_rejects_moved_remote_or_pr(
    tmp_path: Path,
    moved: str,
) -> None:
    candidate_head = "b" * 40
    moved_head = "d" * 40
    original = _review_state(head=SHA, clean=True)
    pr = dict(original.open_pr or {})
    if moved == "pr":
        pr["headRefOid"] = moved_head
    state = replace(
        original,
        git={**original.git, "head_sha": candidate_head},
        local_task_head=candidate_head,
        remote_task_oid=moved_head if moved == "remote" else SHA,
        open_pr=pr,
    )
    resolver = cast(Any, StaticResolver(tmp_path, state))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    _write_owned_candidate_session(store, review_id, candidate_head=candidate_head)

    with pytest.raises(
        lck_models.LckStopError, match="exactly match its owned session"
    ):
        lck_remediation.RemediationCompleter(resolver, store=store).complete(
            159, review_id, commit_message="repair", summary="repair"
        )
