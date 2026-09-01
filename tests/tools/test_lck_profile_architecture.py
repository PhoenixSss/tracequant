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

from lck_core import issue_profiles  # type: ignore[import-not-found]  # noqa: E402
from lck_core.profile_policies import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_PROFILE_POLICY_REGISTRY,
    PolicyBlocker,
    PolicyContext,
    ProfileEvidenceEnvelope,
    ProfileEvidenceRecord,
    ProfilePolicyRegistry,
    run_profile_delivery_gates,
)
from workflow_common import sha256_json  # type: ignore[import-not-found]  # noqa: E402


@dataclass(frozen=True)
class SyntheticPolicy:
    """A fifth policy proving the injection seam is independent of production."""

    profile_id: str = "synthetic"
    canonical_type_label: str = "type:synthetic"

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord:
        del context
        return ProfileEvidenceRecord(
            "synthetic.contract.v1",
            1,
            {
                "policy_id": self.profile_id,
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
            "synthetic.candidate.v1",
            1,
            {
                "policy_id": self.profile_id,
                "contract_digest": contract_evidence.payload["contract_digest"],
                "result": {"status": "pass"},
            },
        )

    def validate_evidence(self, record: ProfileEvidenceRecord) -> bool:
        return (
            record.kind in {"synthetic.contract.v1", "synthetic.candidate.v1"}
            and record.schema_version == 1
            and record.payload.get("policy_id") == self.profile_id
        )


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

    contract = policy.validate_contract(context, leaf_contract)
    blockers = tuple(policy.evaluate_blockers(context, leaf_contract, contract))
    candidate = policy.validate_candidate(context, leaf_contract, contract)
    envelope = ProfileEvidenceEnvelope(
        profile_id=policy.profile_id,
        contract=contract,
        candidate=candidate,
    ).validated(registry)

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
