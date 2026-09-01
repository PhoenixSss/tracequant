# ruff: noqa: E402, I001

"""Acceptance tests for the typed profile policy/evidence boundary."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from collections.abc import Mapping
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

import issue_form_contract  # type: ignore[import-not-found]  # noqa: E402
from bug_policy import bug_contract_snapshot  # type: ignore[import-not-found]  # noqa: E402
from documentation_policy import (  # type: ignore[import-not-found]  # noqa: E402
    documentation_contract_snapshot,
)
from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    closeout as lck_closeout,
    delivery as lck_delivery,
    effects as lck_effects,
    eligibility as lck_eligibility,
    issue_profiles,
    models as lck_models,
    receipts as lck_receipts,
    remediation as lck_remediation,
    review as lck_review,
    review_workspace as lck_review_workspace,
)
from lck_core.issue_profiles import (  # type: ignore[import-not-found]  # noqa: E402
    IssueProfileResolution,
    IssueProfileResolutionStatus,
)
from lck_core.profile_policies import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_PROFILE_POLICY_REGISTRY,
    PolicyBlocker,
    PolicyContext,
    ProfileEffectDescriptor,
    ProfileEvidenceEnvelope,
    ProfileEvidenceRecord,
    ProfileGateResults,
    ProfilePolicyError,
    ProfilePolicyRegistry,
    evaluate_profile_blockers,
    validate_profile_completion,
    validate_profile_candidate,
    validate_profile_contract,
    validate_profile_review,
    run_profile_delivery_gates,
)
from research_policy import research_contract_snapshot  # type: ignore[import-not-found]  # noqa: E402
from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
    sha256_json,
)
from lck_test_support import (  # noqa: E402
    StaticResolver,
    _review_state,
)


def _imported_modules(tree: ast.AST) -> set[str]:
    """Return normalized module names from an AST import graph."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").rsplit(".", 1)[-1])
    return modules


def _referenced_symbols(tree: ast.AST) -> set[str]:
    """Return names used by code, independent of comments/string literals."""
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
    return symbols


def test_all_phase_controllers_use_only_generic_policy_capabilities() -> None:
    """Shared lifecycle controllers must not dispatch concrete profile capabilities."""
    controller_names = (
        "delivery.py",
        "review.py",
        "review_workspace.py",
        "validation.py",
        "closeout.py",
    )
    forbidden_modules = {
        "critical_outcome",
        "documentation_policy",
        "research_policy",
    }
    forbidden_capability_names = {
        "CriticalOutcomeGate",
        "DocumentationReclassificationRequired",
        "DocumentationValidationGate",
        "ResearchOutcomeEffect",
        "ResearchOutcomeRequired",
        "ResearchReclassificationRequired",
        "ResearchValidationGate",
        "_run_critical_outcome",
    }
    controller_root = ROOT / "tools" / "agent_workflow" / "lck_core"

    for name in controller_names:
        tree = ast.parse((controller_root / name).read_text(encoding="utf-8"))
        assert _imported_modules(tree).isdisjoint(forbidden_modules), name
        assert _referenced_symbols(tree).isdisjoint(forbidden_capability_names), name
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name != "__getattr__", name

    policy_tree = ast.parse(
        (controller_root / "profile_policies.py").read_text(encoding="utf-8")
    )
    assert {
        "critical_outcome",
        "bug_policy",
        "documentation_policy",
        "research_policy",
    } <= _imported_modules(policy_tree)


def test_phase_controllers_do_not_branch_on_profile_identity() -> None:
    """Profile-specific behavior belongs behind the generic policy seam."""
    controller_root = ROOT / "tools" / "agent_workflow" / "lck_core"
    controller_names = (
        "eligibility.py",
        "delivery.py",
        "review.py",
        "review_workspace.py",
        "remediation.py",
        "closeout.py",
        "validation.py",
    )
    identity_attributes = {
        "profile_id",
        "issue_kind",
        "canonical_type_label",
    }
    for name in controller_names:
        tree = ast.parse((controller_root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            attributes = {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
            }
            if not attributes & identity_attributes:
                continue
            string_literals = [
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            ]
            assert not string_literals, (name, string_literals)

    production_root = ROOT / "tools" / "agent_workflow" / "lck_core"
    for path in production_root.glob("*.py"):
        assert "synthetic" not in path.read_text(encoding="utf-8").casefold(), path


def test_typed_policies_share_one_issue_form_parser_over_markdown_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All typed forms use one structural parser, including a new profile."""

    calls: list[tuple[str, ...]] = []
    original = issue_form_contract.extract_markdown_sections

    def observe(body: str, *, canonical_names: Any = None) -> Any:
        calls.append(tuple(canonical_names or ()))
        return original(body, canonical_names=canonical_names)

    monkeypatch.setattr(issue_form_contract, "extract_markdown_sections", observe)
    bug_body = "\n\n".join(
        f"### {heading}\n\ncontent"
        for heading in (
            "Observed",
            "Expected",
            "Reproduction / Evidence",
            "Acceptance Criteria",
        )
    )
    documentation_body = "\n\n".join(
        f"# {heading}\n\ncontent"
        for heading in ("Documentation Goal", "Requirements", "Acceptance Criteria")
    )
    research_body = "\n\n".join(
        f"###### {heading}\n\ncontent"
        for heading in (
            "Question / Decision Needed",
            "Context",
            "Scope",
            "Non-goals",
            "Evidence / Evaluation Criteria",
            "Expected Outcome / Artifact",
        )
    )

    assert bug_contract_snapshot(bug_body)["status"] == "pass"
    assert documentation_contract_snapshot(documentation_body)["status"] == "pass"
    assert research_contract_snapshot(research_body)["status"] == "pass"

    synthetic_template = tmp_path / "synthetic.yml"
    synthetic_template.write_text(
        """body:
  - type: textarea
    id: premise
    attributes:
      label: Premise
    validations:
      required: true
""",
        encoding="utf-8",
    )
    synthetic = issue_form_contract.issue_form_contract_snapshot(
        "## Premise\n\nsynthetic contract",
        template_path=synthetic_template,
        form_name="Synthetic",
    )
    assert synthetic["status"] == "pass"
    assert synthetic["required_sections"] == ["Premise"]

    assert calls == [
        ("Observed", "Expected", "Reproduction / Evidence", "Acceptance Criteria"),
        ("Documentation Goal", "Requirements", "Acceptance Criteria"),
        (
            "Question / Decision Needed",
            "Context",
            "Scope",
            "Non-goals",
            "Evidence / Evaluation Criteria",
            "Expected Outcome / Artifact",
        ),
        ("Premise",),
    ]
    generic_source = (
        (ROOT / "tools" / "agent_workflow" / "issue_form_contract.py")
        .read_text(encoding="utf-8")
        .casefold()
    )
    assert not any(
        profile_name in generic_source
        for profile_name in ("bug", "documentation", "research")
    )
    for policy_name in (
        "bug_policy.py",
        "documentation_policy.py",
        "research_policy.py",
    ):
        policy_source = (ROOT / "tools" / "agent_workflow" / policy_name).read_text(
            encoding="utf-8"
        )
        assert "parse_issue_form_contract" in policy_source
        assert "yaml.safe_load" not in policy_source


def test_shared_facts_and_policy_blockers_follow_frozen_one_way_contract() -> None:
    """Shared facts stay mechanical while blockers use policy capabilities."""
    shared_source = (
        ROOT / "tools" / "agent_workflow" / "lck_core" / "shared_facts.py"
    ).read_text(encoding="utf-8")
    shared_tree = ast.parse(shared_source)
    forbidden_imports = {
        "issue_profiles",
        "profile_policies",
        "eligibility",
        "workflow_evidence",
    }
    assert _imported_modules(shared_tree).isdisjoint(forbidden_imports)

    core_root = ROOT / "tools" / "agent_workflow" / "lck_core"
    for path in core_root.glob("*.py"):
        if path.name not in {"shared_facts.py", "__init__.py"}:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            assert "workflow_evidence" not in _imported_modules(tree), path.name

    evidence_tree = ast.parse(
        (ROOT / "tools" / "agent_workflow" / "workflow_evidence.py").read_text(
            encoding="utf-8"
        )
    )
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "lck_core"
        and any(alias.name == "shared_facts" for alias in node.names)
        for node in ast.walk(evidence_tree)
    )

    open_gate = lck_eligibility.evaluate_shared_blockers(
        {
            "available": True,
            "blocked_by": {
                "items": [{"number": 1, "state": "OPEN"}],
                "count": 1,
                "truncated": False,
            },
        }
    )
    closed_gate = lck_eligibility.evaluate_shared_blockers(
        {
            "available": True,
            "blocked_by": {
                "items": [{"number": 1, "state": "CLOSED"}],
                "count": 1,
                "truncated": False,
            },
        }
    )
    assert open_gate["status"] == "fail"
    assert closed_gate["status"] == "pass"
    assert all(
        callable(getattr(policy, "evaluate_blockers", None))
        for policy in DEFAULT_PROFILE_POLICY_REGISTRY.policies.values()
    )
    assert callable(SyntheticPolicy().evaluate_blockers)


@dataclass(frozen=True)
class SyntheticPolicy:
    """A fifth policy proving the injection seam is independent of production."""

    profile_id: str = "synthetic"
    canonical_type_label: str = "type:synthetic"
    contract_kind: str = "synthetic.contract.v1"
    candidate_kind: str = "synthetic.candidate.v1"
    review_kind: str = "synthetic.review.v1"
    completion_kind: str = "synthetic.completion.v1"
    blocker: PolicyBlocker | None = None
    blocker_numbers: tuple[int, ...] = ()
    form_template_path: Path | None = None
    events: list[str] | None = None

    def _mark(self, stage: str) -> None:
        if self.events is not None:
            self.events.append(stage)

    def _issue_form_snapshot(
        self, leaf_contract: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self.form_template_path is None:
            return {"status": "pass", "required_sections": ["Premise"]}
        snapshot = issue_form_contract.issue_form_contract_snapshot(
            leaf_contract.get("body")
            if isinstance(leaf_contract.get("body"), str)
            else None,
            template_path=self.form_template_path,
            template_display_path="synthetic.yml",
            form_name="Synthetic",
        )
        if snapshot.get("status") != "pass":
            raise ProfilePolicyError(
                str(
                    snapshot.get("detail") or "Synthetic Issue Form contract is invalid"
                )
            )
        return cast(Mapping[str, Any], snapshot)

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord:
        del context
        self._mark("contract")
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
                "issue_form": self._issue_form_snapshot(leaf_contract),
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
        del context, contract_evidence
        self._mark("blocker")
        if (
            self.blocker is None
            or leaf_contract.get("number") not in self.blocker_numbers
        ):
            return ()
        return (self.blocker,)

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord:
        del context, leaf_contract
        self._mark("candidate")
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

    def validate_review(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        review_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord:
        del context
        self._mark("review")
        return ProfileEvidenceRecord(
            self.review_kind,
            1,
            {
                "policy_id": self.profile_id,
                "contract_ref": {
                    "number": leaf_contract["number"],
                    "body_sha256": leaf_contract["body_sha256"],
                },
                "result": {"status": "pass"},
                "status": "pass",
                "review_input": dict(review_input),
            },
        )

    def validate_completion(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        completion_input: Mapping[str, Any],
    ) -> ProfileEvidenceRecord:
        assert context.runner is None
        del completion_input
        self._mark("completion")
        descriptor = ProfileEffectDescriptor(
            effect_kind="synthetic.noop.v1",
            schema_version=1,
            parameters={"value": "complete"},
            postcondition={"kind": "synthetic.completed", "value": "complete"},
            receipt={"source": "synthetic-policy"},
        )
        return ProfileEvidenceRecord(
            self.completion_kind,
            1,
            {
                "policy_id": self.profile_id,
                "contract_ref": {
                    "number": leaf_contract["number"],
                    "body_sha256": leaf_contract["body_sha256"],
                },
                "result": {"status": "pass"},
                "status": "pass",
                "effect": descriptor.to_dict(),
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
            issue_form = record.payload.get("issue_form")
            return (
                isinstance(contract, Mapping)
                and dict(contract) == {"status": "pass"}
                and isinstance(issue_form, Mapping)
                and issue_form.get("status") == "pass"
            )
        if record.kind == self.candidate_kind:
            result = record.payload.get("result")
            return (
                record.payload.get("status") == "pass"
                and isinstance(result, Mapping)
                and dict(result) == {"status": "pass"}
            )
        if record.kind == self.review_kind:
            return (
                record.payload.get("status") == "pass"
                and isinstance(record.payload.get("result"), Mapping)
                and record.payload["result"].get("status") == "pass"
            )
        if record.kind == self.completion_kind:
            return (
                record.payload.get("status") == "pass"
                and isinstance(record.payload.get("result"), Mapping)
                and record.payload["result"].get("status") == "pass"
                and isinstance(record.payload.get("effect"), Mapping)
            )
        return False


def test_synthetic_fifth_profile_requires_only_allowed_extension_points(
    tmp_path: Path,
) -> None:
    """A test-only profile must use every existing generic policy capability."""
    template = tmp_path / "synthetic.yml"
    template.write_text(
        """body:
  - type: textarea
    id: premise
    attributes:
      label: Premise
    validations:
      required: true
""",
        encoding="utf-8",
    )
    events: list[str] = []
    policy = SyntheticPolicy(form_template_path=template, events=events)
    registry = ProfilePolicyRegistry.from_policies(policy)
    profile = replace(
        issue_profiles.TASK_PROFILE,
        profile_id=policy.profile_id,
        canonical_type_label=policy.canonical_type_label,
        candidate_capability="synthetic_candidate",
        requires_critical_outcome=False,
    )
    leaf_contract = {
        "number": 223,
        "title": "Synthetic fifth profile",
        "body": "## Premise\n\nThe generic parser is part of this contract.",
        "body_sha256": "a" * 64,
    }
    context = PolicyContext(profile=profile, issue=leaf_contract)

    contract_check = validate_profile_contract(
        profile, leaf_contract, registry=registry, context=context
    )
    assert contract_check.valid
    assert contract_check.evidence is not None
    assert contract_check.evidence.payload["issue_form"] == {
        "status": "pass",
        "required_sections": ["Premise"],
        "missing_sections": [],
        "duplicate_sections": [],
        "empty_sections": [],
        "template_path": "synthetic.yml",
    }
    contract = contract_check.evidence

    assert (
        evaluate_profile_blockers(
            profile,
            leaf_contract,
            contract_evidence=contract,
            registry=registry,
            context=context,
        )
        == ()
    )
    candidate = validate_profile_candidate(
        profile,
        leaf_contract,
        contract_evidence=contract,
        registry=registry,
        context=context,
    )
    review = validate_profile_review(
        profile,
        leaf_contract,
        {"verdict": "PASS", "review_input": "bounded"},
        registry=registry,
        context=context,
    )
    assert review.evidence is not None
    completion = validate_profile_completion(
        profile,
        leaf_contract,
        {"task_number": 223, "review_evidence": review.evidence},
        registry=registry,
        context=replace(context, runner=object()),
    )
    assert completion.evidence is not None
    assert completion.effect is not None

    envelope = ProfileEvidenceEnvelope(
        profile_id=policy.profile_id,
        contract=contract,
        candidate=candidate,
        review=review.evidence,
        completion=completion.evidence,
    ).validated(registry, leaf_contract=leaf_contract)

    assert {
        stage
        for stage in ("contract", "blocker", "candidate", "review", "completion")
        if stage in events
    } == {"contract", "blocker", "candidate", "review", "completion"}
    assert envelope.profile_id == policy.profile_id
    assert envelope.contract is contract
    assert envelope.candidate is candidate
    assert envelope.review is review.evidence
    assert envelope.completion is completion.evidence
    assert '"body":' not in envelope.serialize()
    assert policy.profile_id in registry.policies
    assert policy.canonical_type_label in registry.policies_by_type_label
    assert policy.profile_id not in DEFAULT_PROFILE_POLICY_REGISTRY.policies


def test_generic_kernel_models_have_no_synthetic_fixed_slots() -> None:
    """Profile extensions add registrations/evidence, never shared schema fields."""
    assert {field.name for field in fields(ProfileEvidenceEnvelope)} == {
        "profile_id",
        "schema_version",
        "contract",
        "candidate",
        "review",
        "completion",
    }
    for model in (
        lck_models.LiveState,
        lck_models.OperationSnapshot,
        lck_models.EffectReceipt,
        ProfileEvidenceRecord,
        PolicyBlocker,
    ):
        assert all("synthetic" not in field.name.casefold() for field in fields(model))

    result_fields = {field.name for field in fields(ProfileGateResults)}
    assert "profile_evidence" in result_fields
    assert not any("synthetic" in name.casefold() for name in result_fields)


def test_leaf_contract_is_canonical_input_not_profile_evidence_envelope() -> None:
    """Keep acquired Issue input separate from #217 lifecycle evidence."""
    policy = SyntheticPolicy()
    registry = ProfilePolicyRegistry.from_policies(policy)
    profile = replace(
        issue_profiles.TASK_PROFILE,
        profile_id=policy.profile_id,
        canonical_type_label=policy.canonical_type_label,
        requires_critical_outcome=False,
    )
    leaf_contract = {
        "number": 217,
        "title": "Synthetic leaf Issue",
        "body": "the acquired Issue body is not lifecycle evidence",
        "body_sha256": "a" * 64,
    }
    state = lck_models.LiveState(
        issue_number=217,
        issue={"number": 217, "body_sha256": leaf_contract["body_sha256"]},
        target_branch="synthetic/217-leaf-contract",
        leaf_contract=leaf_contract,
    )
    snapshot = lck_models.OperationSnapshot(operation="test", state=state)

    contract_result = validate_profile_contract(
        profile,
        snapshot.leaf_contract or {},
        registry=registry,
        context=PolicyContext(profile=profile, issue=snapshot.leaf_contract),
    )
    assert contract_result.valid
    assert contract_result.evidence is not None
    envelope = ProfileEvidenceEnvelope(
        profile_id=policy.profile_id,
        contract=contract_result.evidence,
    ).validated(registry, leaf_contract=snapshot.leaf_contract)

    assert snapshot.issue_number == 217
    assert snapshot.leaf_contract == leaf_contract
    assert "body" not in envelope.to_dict()["contract"]["payload"]
    assert "leaf_contract" not in envelope.to_dict()
    assert envelope.to_dict()["contract"]["payload"]["contract_ref"] == {
        "number": 217,
        "body_sha256": "a" * 64,
    }


@pytest.mark.parametrize(
    "profile",
    (
        issue_profiles.TASK_PROFILE,
        issue_profiles.BUG_PROFILE,
        issue_profiles.DOCUMENTATION_PROFILE,
    ),
)
def test_non_research_review_rejects_research_outcome_at_generic_policy_boundary(
    profile: issue_profiles.LeafIssueWorkflowProfile,
) -> None:
    with pytest.raises(
        ProfilePolicyError,
        match="--research-outcome is supported only for Research Issues",
    ):
        validate_profile_review(
            profile,
            {"number": 219, "body_sha256": "a" * 64},
            {"verdict": "PASS", "research_outcome": "IMPLEMENT"},
        )


def test_review_and_closeout_propagate_policy_injection_to_eligibility(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    policy = SyntheticPolicy(
        blocker=PolicyBlocker(
            code="SYNTHETIC_BLOCKED",
            kind="synthetic",
            detail="synthetic policy rejected this lifecycle operation",
        ),
        blocker_numbers=(159,),
        events=events,
    )
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

    resolver = StaticResolver(tmp_path, _review_state())
    preparer = lck_review.ReviewPreparer(
        resolver,
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    )
    completer = lck_review.ReviewCompleter(
        resolver,
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    )
    closeout = lck_closeout.CloseoutCompleter(
        resolver,
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    )

    for controller, phase in (
        (preparer, lck_models.Phase.REVIEW_PREPARE),
        (completer, lck_models.Phase.REVIEW_COMPLETE),
        (closeout, lck_models.Phase.CLOSEOUT),
    ):
        assert controller.eligibility.registry is registry
        assert controller.eligibility.profile_resolver is resolve_synthetic
        decision = controller.eligibility.resolve(resolver.state, phase)
        assert not decision.eligible
        assert decision.issue_profile["profile"]["profile_id"] == policy.profile_id
        assert (
            "policy blocker [SYNTHETIC_BLOCKED]: synthetic policy rejected this "
            "lifecycle operation" in decision.reasons
        )
    assert "blocker" in events


def test_dependency_policy_blocker_is_dispatched_without_contract_policy_filter(
    tmp_path: Path,
) -> None:
    policy = SyntheticPolicy(
        blocker=PolicyBlocker(
            code="SYNTHETIC_DEPENDENCY_BLOCKED",
            kind="synthetic",
            detail="synthetic dependency policy rejected this dependency",
        ),
        blocker_numbers=(217,),
    )
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

    state = _review_state()
    state = replace(
        state,
        relationships={
            "available": True,
            "blocked_by": {
                "items": [
                    {
                        "number": 217,
                        "state": "CLOSED",
                        "labels": ["type:synthetic"],
                        "labels_complete": True,
                        "body_sha256": "a" * 64,
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        },
    )
    resolver = StaticResolver(tmp_path, state)
    eligibility = lck_eligibility.PhaseEligibilityResolver(
        registry=registry,
        profile_resolver=resolve_synthetic,
    )

    reasons = eligibility.blocker_reasons(
        resolver.state,
        phase=lck_models.Phase.REVIEW_PREPARE,
    )

    assert reasons == (
        "policy blocker [SYNTHETIC_DEPENDENCY_BLOCKED]: "
        "synthetic dependency policy rejected this dependency",
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


def test_synthetic_policy_review_completion_and_effect_are_generic_capabilities() -> (
    None
):
    policy = SyntheticPolicy()
    registry = ProfilePolicyRegistry.from_policies(policy)
    profile = replace(
        issue_profiles.TASK_PROFILE,
        profile_id=policy.profile_id,
        canonical_type_label=policy.canonical_type_label,
        candidate_capability="synthetic_candidate",
        requires_critical_outcome=False,
    )
    leaf_contract = {
        "number": 219,
        "body": "synthetic lifecycle contract",
        "body_sha256": "a" * 64,
    }

    review = validate_profile_review(
        profile,
        leaf_contract,
        {"verdict": "PASS", "review_input": "bounded"},
        registry=registry,
    )
    assert review.evidence is not None
    assert review.profile_evidence is not None
    assert review.profile_evidence.review == review.evidence

    completion = validate_profile_completion(
        profile,
        leaf_contract,
        {
            "task_number": 219,
            "repository": "PhoenixSss/tracequant",
            "review_evidence": review.evidence,
        },
        registry=registry,
        context=PolicyContext(
            profile=profile,
            issue=leaf_contract,
            runner=object(),
        ),
    )
    assert completion.effect is not None
    assert completion.profile_evidence is not None
    assert completion.profile_evidence.completion == completion.evidence

    def execute_effect(
        descriptor: ProfileEffectDescriptor, **_kwargs: Any
    ) -> lck_models.EffectReceipt:
        assert descriptor.postcondition == {
            "kind": "synthetic.completed",
            "value": "complete",
        }
        return lck_models.EffectReceipt(
            "synthetic.noop.v1", "executed", dict(descriptor.receipt)
        )

    effects = lck_effects.EffectExecutorRegistry({"synthetic.noop.v1": execute_effect})
    with pytest.raises(TypeError):
        effects.executors["other"] = execute_effect
    receipt = effects.execute(
        completion.effect,
        resolver=object(),
        state=object(),
    )
    assert receipt.action == "executed"
    assert receipt.details == {"source": "synthetic-policy"}
    assert receipt.is_complete
    assert not lck_models.EffectReceipt("synthetic.noop.v1", "pending", {}).is_complete
    with pytest.raises(ProfilePolicyError, match="must be JSON data"):
        ProfileEffectDescriptor(
            effect_kind="synthetic.noop.v1",
            schema_version=1,
            parameters={"callable": execute_effect},
            postcondition={},
            receipt={},
        )
    with pytest.raises(lck_models.LckStopError, match="unknown completion effect"):
        effects.execute(
            replace(completion.effect, effect_kind="synthetic.unknown.v1"),
            resolver=object(),
            state=object(),
        )


def test_synthetic_profile_evidence_round_trips_through_all_phase_receipts(
    tmp_path: Path,
) -> None:
    """Delivery, Review, Closeout, and Remediation share one receipt envelope."""
    policy = SyntheticPolicy()
    registry = ProfilePolicyRegistry.from_policies(policy)
    profile = replace(
        issue_profiles.TASK_PROFILE,
        profile_id=policy.profile_id,
        canonical_type_label=policy.canonical_type_label,
        candidate_capability="synthetic_candidate",
        requires_critical_outcome=False,
    )
    leaf_contract = {
        "number": 159,
        "body": "synthetic lifecycle contract",
        "body_sha256": "a" * 64,
    }
    contract = validate_profile_contract(
        profile,
        leaf_contract,
        registry=registry,
    ).evidence
    assert contract is not None
    candidate = validate_profile_candidate(
        profile,
        leaf_contract,
        contract_evidence=contract,
        registry=registry,
    )
    review = validate_profile_review(
        profile,
        leaf_contract,
        {"verdict": "PASS"},
        registry=registry,
    )
    assert review.evidence is not None
    completion = validate_profile_completion(
        profile,
        leaf_contract,
        {"task_number": 159, "review_evidence": review.evidence},
        registry=registry,
    )
    assert completion.evidence is not None
    assert completion.effect is not None
    envelope = ProfileEvidenceEnvelope(
        profile_id=policy.profile_id,
        contract=contract,
        candidate=candidate,
        review=review.evidence,
        completion=completion.evidence,
    ).validated(registry, leaf_contract=leaf_contract)

    state = replace(
        _review_state(),
        issue_profile={"profile": profile.to_dict()},
    )
    snapshot = lck_models.OperationSnapshot(operation="synthetic", state=state)
    delivery = lck_delivery.DeliveryCompletionResult(
        task_number=159,
        status="READY_FOR_REVIEW",
        branch=state.target_branch,
        head_sha="e" * 40,
        critical_outcome=None,
        validation={"status": "pass"},
        checks={"status": "observed"},
        effects=(
            lck_models.EffectReceipt(
                "ensure_open_pr",
                "reused-current-open-pr",
                {"number": 200, "head_sha": "e" * 40},
            ),
        ),
        operation_snapshot=snapshot,
        profile_evidence=envelope,
    )
    review_result = lck_review.ReviewCompletionResult(
        review_id="b" * 32,
        task_number=159,
        verdict="PASS",
        status="READY_FOR_MERGE_PREFLIGHT",
        identity=lck_review_workspace.ReviewIdentity(
            task_number=159,
            pr_number=200,
            base_sha="c" * 40,
            head_sha="e" * 40,
            task_body_sha256="a" * 64,
            merge_base_sha="c" * 40,
            effective_diff_sha256="d" * 64,
            changed_files=("tests/tools/test_lck_profile_architecture.py",),
        ),
        record_path=tmp_path / "review-record.json",
        issue_profile={"profile": profile.to_dict()},
        profile_evidence=envelope,
    )
    closeout = lck_closeout.CloseoutResult(
        task_number=159,
        status="BUSINESS_DELIVERY_COMPLETE",
        business_delivery="COMPLETE",
        cleanup="COMPLETE",
        effects=(lck_models.EffectReceipt("cleanup_task_refs", "cleaned", {}),),
        operation_snapshot=snapshot,
        profile_evidence=envelope,
    )

    store = lck_receipts.AuditReceiptStore(tmp_path)
    for result, operation, operation_id in (
        (delivery, "delivery-complete", "a" * 32),
        (review_result, "review-complete", "b" * 32),
        (closeout, "closeout", "c" * 32),
        (
            lck_remediation.RemediationCompletionResult(
                task_number=159,
                review_id="b" * 32,
                delivery=delivery,
            ),
            "remediation-complete",
            "d" * 32,
        ),
    ):
        view = lck_receipts._write_success_receipt(
            result,
            operation=operation,
            task_number=159,
            operation_id=operation_id,
            store=store,
        )
        receipt = store.read(view["receipt_reference"])
        stored = receipt["audit"]["profile_evidence"]
        assert stored["profile_id"] == policy.profile_id
        assert {
            stored[stage]["kind"]
            for stage in ("contract", "candidate", "review", "completion")
        } == {
            policy.contract_kind,
            policy.candidate_kind,
            policy.review_kind,
            policy.completion_kind,
        }


def test_merge_preflight_propagates_policy_injection_to_default_review_gate(
    tmp_path: Path,
) -> None:
    policy = SyntheticPolicy()
    registry = ProfilePolicyRegistry.from_policies(policy)

    def resolve_synthetic(_issue: Mapping[str, Any] | None) -> IssueProfileResolution:
        return IssueProfileResolution(
            status=IssueProfileResolutionStatus.RESOLVED,
            profile=replace(
                issue_profiles.TASK_PROFILE,
                profile_id=policy.profile_id,
                canonical_type_label=policy.canonical_type_label,
            ),
            type_labels=(policy.canonical_type_label,),
        )

    preflight = lck_review.MergePreflight(
        StaticResolver(tmp_path, _review_state()),
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    )

    assert isinstance(preflight.review_gate, lck_review.ReviewPassGate)
    assert preflight.review_gate.policy_registry is registry
    assert preflight.review_gate.profile_resolver is resolve_synthetic


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
