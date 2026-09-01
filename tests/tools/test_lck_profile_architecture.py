# ruff: noqa: E402, I001

"""Acceptance tests for the typed profile policy/evidence boundary."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Mapping
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    delivery as lck_delivery,
    issue_profiles,
    models as lck_models,
    receipts as lck_receipts,
    remediation as lck_remediation,
)
from lck_core.issue_profiles import (  # type: ignore[import-not-found]  # noqa: E402
    IssueProfileResolution,
    IssueProfileResolutionStatus,
)
from lck_core.profile_policies import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_PROFILE_POLICY_REGISTRY,
    PolicyBlocker,
    PolicyContext,
    ProfileEvidenceEnvelope,
    ProfileEvidenceRecord,
    ProfilePolicyRegistry,
    evaluate_profile_blockers,
    validate_profile_candidate,
    validate_profile_contract,
    run_profile_delivery_gates,
)
from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
    sha256_json,
)
from lck_test_support import (  # noqa: E402
    StaticResolver,
    _review_state,
)


@dataclass(frozen=True)
class SyntheticPolicy:
    """A fifth policy proving the injection seam is independent of production."""

    profile_id: str = "synthetic"
    canonical_type_label: str = "type:synthetic"
    contract_kind: str = "synthetic.contract.v1"
    candidate_kind: str = "synthetic.candidate.v1"

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord:
        del context
        return ProfileEvidenceRecord(
            self.contract_kind,
            1,
            {
                "policy_id": self.profile_id,
                "contract_ref": {
                    "number": leaf_contract["number"],
                    "body_sha256": leaf_contract["body_sha256"],
                },
                "contract": {"status": "pass"},
                "contract_digest": sha256_json(
                    {
                        "number": leaf_contract.get("number"),
                        "body_sha256": leaf_contract.get("body_sha256"),
                    }
                ),
            },
        )

    def evaluate_blockers(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> tuple[PolicyBlocker, ...]:
        del context, leaf_contract, contract_evidence
        return ()

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord:
        del context, leaf_contract
        return ProfileEvidenceRecord(
            self.candidate_kind,
            1,
            {
                "policy_id": self.profile_id,
                "contract_ref": contract_evidence.payload["contract_ref"],
                "contract_digest": contract_evidence.payload["contract_digest"],
                "result": {"status": "pass"},
                "status": "pass",
            },
        )

    def validate_evidence(self, record: ProfileEvidenceRecord) -> bool:
        if (
            record.schema_version != 1
            or record.payload.get("policy_id") != self.profile_id
        ):
            return False
        if record.kind == self.contract_kind:
            contract = record.payload.get("contract")
            return isinstance(contract, Mapping) and dict(contract) == {
                "status": "pass"
            }
        if record.kind == self.candidate_kind:
            result = record.payload.get("result")
            return (
                record.payload.get("status") == "pass"
                and isinstance(result, Mapping)
                and dict(result) == {"status": "pass"}
            )
        return False


def test_synthetic_policy_contract_blocker_candidate_use_frozen_registry_and_envelope() -> (
    None
):
    policy = SyntheticPolicy()
    registry = ProfilePolicyRegistry.from_policies(policy)
    profile = replace(
        issue_profiles.TASK_PROFILE,
        profile_id=policy.profile_id,
        canonical_type_label=policy.canonical_type_label,
        candidate_capability="synthetic_candidate",
    )
    leaf_contract = {
        "number": 217,
        "body": "canonical input must not be copied into evidence",
        "body_sha256": "a" * 64,
    }
    context = PolicyContext(profile=profile, issue=leaf_contract)

    contract_check = validate_profile_contract(
        profile,
        leaf_contract,
        registry=registry,
        context=context,
    )
    assert contract_check.valid
    assert contract_check.evidence is not None
    contract = contract_check.evidence
    blockers = evaluate_profile_blockers(
        profile,
        leaf_contract,
        contract_evidence=contract,
        registry=registry,
        context=context,
    )
    candidate = validate_profile_candidate(
        profile,
        leaf_contract,
        contract_evidence=contract,
        registry=registry,
        context=context,
    )
    envelope = ProfileEvidenceEnvelope(
        profile_id=policy.profile_id,
        contract=contract,
        candidate=candidate,
    ).validated(registry, leaf_contract=leaf_contract)

    assert registry.resolve(profile).profile_id == policy.profile_id
    assert "synthetic" not in DEFAULT_PROFILE_POLICY_REGISTRY.policies
    with pytest.raises(TypeError):
        registry.policies["other"] = policy
    assert blockers == ()
    assert set(envelope.to_dict()) == {
        "profile_id",
        "schema_version",
        "contract",
        "candidate",
        "review",
        "completion",
    }
    assert envelope.to_dict()["review"] is None
    assert envelope.to_dict()["completion"] is None
    assert "body" not in envelope.to_dict()["contract"]["payload"]
    assert envelope.serialize() == envelope.serialize()

    gates = run_profile_delivery_gates(
        profile,
        base_sha="b" * 40,
        head_sha="c" * 40,
        include_index=False,
        progress=None,
        documentation_validation=None,
        research_validation=None,
        critical_outcome=lambda: {"status": "unused"},
        issue=leaf_contract,
        registry=registry,
    )
    assert gates.profile_evidence == envelope


def test_profile_evidence_rejects_wrong_stage_malformed_payload_and_stale_contract() -> (
    None
):
    policy = SyntheticPolicy()
    registry = ProfilePolicyRegistry.from_policies(policy)
    profile = replace(
        issue_profiles.TASK_PROFILE,
        profile_id=policy.profile_id,
        canonical_type_label=policy.canonical_type_label,
        candidate_capability="synthetic_candidate",
    )
    first_contract = {
        "number": 217,
        "body": "first canonical input",
        "body_sha256": "a" * 64,
    }
    second_contract = {**first_contract, "body_sha256": "b" * 64}
    context = PolicyContext(profile=profile, issue=first_contract)
    contract = validate_profile_contract(
        profile, first_contract, registry=registry, context=context
    ).evidence
    assert contract is not None

    with pytest.raises(ValueError, match="contract reference"):
        validate_profile_candidate(
            profile,
            second_contract,
            contract_evidence=contract,
            registry=registry,
            context=replace(context, issue=second_contract),
        )

    malformed_contract = ProfileEvidenceRecord(
        policy.contract_kind,
        1,
        {
            "policy_id": policy.profile_id,
            "contract_ref": contract.payload["contract_ref"],
            "contract": {},
        },
    )
    with pytest.raises(ValueError, match="policy rejected evidence"):
        ProfileEvidenceEnvelope(
            profile_id=policy.profile_id,
            contract=malformed_contract,
        ).validated(registry, leaf_contract=first_contract)

    wrong_stage = replace(
        contract,
        kind=policy.candidate_kind,
        payload={
            **contract.payload,
            "status": "not-a-result",
            "result": {},
        },
    )
    with pytest.raises(ValueError, match="contract stage"):
        ProfileEvidenceEnvelope(
            profile_id=policy.profile_id,
            contract=wrong_stage,
        ).validated(registry, leaf_contract=first_contract)


def test_profile_registry_rejects_conflicting_canonical_labels() -> None:
    first = SyntheticPolicy(profile_id="synthetic-a")
    second = SyntheticPolicy(profile_id="synthetic-b")
    with pytest.raises(ValueError, match="duplicate canonical type label"):
        ProfilePolicyRegistry.from_policies(first, second)


def test_synthetic_policy_dispatches_through_delivery_remediation_and_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = SyntheticPolicy()
    registry = ProfilePolicyRegistry.from_policies(policy)
    profile = replace(
        issue_profiles.TASK_PROFILE,
        profile_id=policy.profile_id,
        canonical_type_label=policy.canonical_type_label,
        candidate_capability="synthetic_candidate",
        requires_critical_outcome=False,
    )

    def resolve_synthetic(_issue: Mapping[str, Any] | None) -> IssueProfileResolution:
        return IssueProfileResolution(
            status=IssueProfileResolutionStatus.RESOLVED,
            profile=profile,
            type_labels=(profile.canonical_type_label,),
        )

    state = _review_state(clean=False)
    resolver = StaticResolver(tmp_path, state)
    start_head = "a" * 40
    candidate_head = "e" * 40
    candidate_tree = "f" * 40

    class ControllerRunner:
        def __init__(self) -> None:
            self.head = start_head

        def run(
            self,
            argv: list[str] | tuple[str, ...],
            *,
            command_id: str,
            **_: Any,
        ) -> CommandResult:
            args = tuple(str(item) for item in argv)
            if args == ("git", "branch", "--show-current"):
                return CommandResult(command_id, args, 0, state.target_branch, "")
            if args == ("git", "rev-parse", "HEAD"):
                return CommandResult(command_id, args, 0, self.head, "")
            if args == ("git", "status", "--porcelain=v1", "--untracked-files=all"):
                return CommandResult(command_id, args, 0, "", "")
            return CommandResult(command_id, args, 1, "", "unsupported fake command")

    runner = ControllerRunner()
    resolver.runner = runner

    class CommitEffect:
        def current_head_tree(self) -> str:
            return candidate_tree

        def stage_candidate_tree(self) -> str:
            return candidate_tree

        def verify_tree_unchanged(
            self, _tree: str, *, expected_head_sha: str | None = None
        ) -> None:
            assert expected_head_sha == start_head

        def execute(
            self,
            _tree: str,
            _message: str,
            *,
            expected_parent_sha: str | None = None,
        ) -> lck_models.EffectReceipt:
            assert expected_parent_sha == start_head
            runner.head = candidate_head
            return lck_models.EffectReceipt(
                "commit_current_tree",
                "committed",
                {"head_sha": candidate_head, "tree_oid": candidate_tree},
            )

    class RemoteEffect:
        def execute(
            self, _branch: str, *, expected_head_sha: str
        ) -> lck_models.EffectReceipt:
            return lck_models.EffectReceipt(
                "ensure_remote_branch",
                "fast-forwarded",
                {"remote_oid": expected_head_sha},
            )

    class PrEffect:
        def execute(self, _state: Any, **kwargs: Any) -> lck_models.EffectReceipt:
            return lck_models.EffectReceipt(
                "ensure_open_pr",
                "reused-current-open-pr",
                {
                    "number": 200,
                    "head_sha": kwargs["head_sha"],
                    "base_sha": kwargs["expected_base_sha"],
                    "url": "https://example.invalid/pr/200",
                },
            )

    class ChecksGate:
        def observe_exact_pr(
            self,
            _repository: str,
            _pr_number: int,
            *,
            expected_head_sha: str,
            expected_base_sha: str,
        ) -> dict[str, Any]:
            return {
                "status": "observed",
                "pr": {
                    "number": 200,
                    "head_sha": expected_head_sha,
                    "base_sha": expected_base_sha,
                },
            }

    class StatusEffect:
        def execute(self, *_args: Any, **_kwargs: Any) -> lck_models.EffectReceipt:
            return lck_models.EffectReceipt("set_review_status", "already-review", {})

    class Validation:
        def run(self, _base_sha: str) -> dict[str, Any]:
            return {"status": "pass"}

    delivery = lck_delivery.DeliveryCompleter(
        resolver,
        formal_validation=Validation(),
        commit_effect=CommitEffect(),
        remote_effect=RemoteEffect(),
        pr_effect=PrEffect(),
        status_effect=StatusEffect(),
        checks_gate=ChecksGate(),
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    ).complete(
        state.task_number,
        commit_message="synthetic repair",
        summary="exercise synthetic policy dispatch",
        operation_snapshot=lck_models.OperationSnapshot(
            operation=lck_models.Phase.DELIVERY_COMPLETE.value,
            state=state,
        ),
    )

    assert delivery.profile_evidence is not None
    assert delivery.profile_evidence.profile_id == policy.profile_id
    delivery_receipt = lck_receipts._write_success_receipt(
        delivery,
        operation="delivery-complete",
        task_number=state.task_number,
        operation_id="a" * 32,
        store=lck_receipts.AuditReceiptStore(tmp_path),
    )
    assert (
        delivery_receipt["profile_evidence"]["candidate"]["kind"]
        == policy.candidate_kind
    )

    review_id = "b" * 32
    store = lck_remediation.ReviewInvocationStore(tmp_path)
    store.write_remediation_session(
        state.task_number,
        {
            "schema_version": 1,
            "kind": "remediation-session",
            "task_number": state.task_number,
            "review_id": review_id,
            "operation_id": "c" * 32,
            "start_head_sha": start_head,
            "pr_number": 200,
            "base_sha": start_head,
            "findings_sha256": "d" * 64,
            "findings_source": "local-review-record",
        },
    )
    captured: dict[str, Any] = {}

    class RemediationDelivery:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.last_profile_evidence = delivery.profile_evidence

        def complete(
            self, *_args: Any, **_kwargs: Any
        ) -> lck_delivery.DeliveryCompletionResult:
            return delivery

    monkeypatch.setattr(lck_remediation, "DeliveryCompleter", RemediationDelivery)
    remediation = lck_remediation.RemediationCompleter(
        resolver,
        store=store,
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    ).complete(
        state.task_number,
        review_id,
        commit_message="synthetic remediation",
        summary="exercise remediation evidence propagation",
    )
    assert captured["profile_resolver"] is resolve_synthetic
    remediation_receipt = lck_receipts._write_success_receipt(
        remediation,
        operation="remediation-complete",
        task_number=state.task_number,
        operation_id="e" * 32,
        store=lck_receipts.AuditReceiptStore(tmp_path),
    )
    assert remediation_receipt["profile_evidence"]["profile_id"] == policy.profile_id
