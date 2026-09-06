# ruff: noqa: E402, I001

"""Regression coverage for the formal typed-profile LCK architecture."""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Mapping
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import Any

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
    eligibility as lck_eligibility,
    models as lck_models,
)
from lck_core.issue_profiles import (  # type: ignore[import-not-found]  # noqa: E402
    BUG_PROFILE,
    DOCUMENTATION_PROFILE,
    TASK_PROFILE,
    LeafIssueWorkflowProfile,
)
from lck_core.profile_policies import (  # type: ignore[import-not-found]  # noqa: E402
    DEFAULT_PROFILE_POLICY_REGISTRY,
    PolicyBlocker,
    PolicyContext,
    ProfileEffectDescriptor,
    ProfileEvidenceEnvelope,
    ProfileEvidenceRecord,
    ProfilePolicyError,
    ProfilePolicyRegistry,
    validate_profile_candidate,
    validate_profile_contract,
    validate_profile_review,
)
from research_policy import research_contract_snapshot  # type: ignore[import-not-found]  # noqa: E402
from lck_test_support import _review_state, _task_contract  # noqa: E402


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.rsplit(".", 1)[-1])
    return modules


def _referenced_symbols(tree: ast.AST) -> set[str]:
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
    return symbols


def _profile_identity_references(tree: ast.AST) -> list[ast.AST]:
    identity = {"profile_id", "issue_kind", "canonical_type_label"}
    references: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in identity:
            references.append(node)
        elif isinstance(node, ast.Attribute) and node.attr in identity:
            references.append(node)
        elif isinstance(node, ast.Constant) and node.value in identity:
            references.append(node)
    return references


def test_all_phase_controllers_use_only_generic_policy_capabilities() -> None:
    controller_names = (
        "eligibility.py",
        "delivery.py",
        "review.py",
        "review_workspace.py",
        "remediation.py",
        "validation.py",
        "closeout.py",
    )
    forbidden_modules = {
        "critical_outcome",
        "bug_policy",
        "documentation_policy",
        "research_policy",
    }
    forbidden_symbols = {
        "CriticalOutcomeGate",
        "DocumentationReclassificationRequired",
        "DocumentationValidationGate",
        "ResearchOutcomeEffect",
        "ResearchOutcomeRequired",
        "ResearchReclassificationRequired",
        "ResearchValidationGate",
        "_run_critical_outcome",
    }
    controller_root = ROOT / "tools/agent_workflow/lck_core"
    for name in controller_names:
        tree = ast.parse((controller_root / name).read_text(encoding="utf-8"))
        assert _imported_modules(tree).isdisjoint(forbidden_modules), name
        assert _referenced_symbols(tree).isdisjoint(forbidden_symbols), name

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
    controller_root = ROOT / "tools/agent_workflow/lck_core"
    for name in (
        "eligibility.py",
        "delivery.py",
        "review.py",
        "review_workspace.py",
        "remediation.py",
        "closeout.py",
        "validation.py",
    ):
        tree = ast.parse((controller_root / name).read_text(encoding="utf-8"))
        assert not _profile_identity_references(tree), name

    probes = (
        'if state.issue_profile["profile"]["profile_id"] == "task": pass',
        'if getattr(state, "profile_id", None) == "task": pass',
        'if state.issue_profile.get("canonical_type_label") == "type:task": pass',
    )
    for source in probes:
        assert _profile_identity_references(ast.parse(source)), source


def test_typed_policies_share_one_issue_form_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    ]

    generic_source = (
        (ROOT / "tools/agent_workflow/issue_form_contract.py")
        .read_text(encoding="utf-8")
        .casefold()
    )
    assert not any(
        name in generic_source for name in ("bug", "documentation", "research")
    )
    for policy_name in (
        "bug_policy.py",
        "documentation_policy.py",
        "research_policy.py",
    ):
        policy_source = (ROOT / "tools/agent_workflow" / policy_name).read_text(
            encoding="utf-8"
        )
        assert "parse_issue_form_contract" in policy_source
        assert "yaml.safe_load" not in policy_source


def test_shared_facts_are_mechanical_and_blockers_are_policy_owned() -> None:
    shared_path = ROOT / "tools/agent_workflow/lck_core/shared_facts.py"
    shared_tree = ast.parse(shared_path.read_text(encoding="utf-8"))
    assert not _profile_identity_references(shared_tree)
    assert _imported_modules(shared_tree).isdisjoint(
        {"issue_profiles", "profile_policies", "eligibility", "workflow_evidence"}
    )

    core_root = ROOT / "tools/agent_workflow/lck_core"
    for path in core_root.glob("*.py"):
        if path.name not in {"shared_facts.py", "__init__.py"}:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            assert "workflow_evidence" not in _imported_modules(tree), path.name

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


@pytest.mark.parametrize(
    "source",
    (
        'facts["research_outcome"] = "IMPLEMENT"',
        'facts.get("critical_outcome")',
        "def normalize_profile_specific_fact(value): return value",
    ),
)
def test_shared_facts_guard_rejects_profile_owned_semantics(source: str) -> None:
    markers = {"research_outcome", "critical_outcome", "profile_specific"}
    text = ast.unparse(ast.parse(source))
    assert any(marker in text for marker in markers), source


class RegistryExtensionPolicy:
    """Minimal test double proving the registry remains an explicit seam."""

    profile_id = "extension"
    canonical_type_label = "type:extension"

    def validate_contract(
        self, context: PolicyContext, leaf_contract: Mapping[str, Any]
    ) -> ProfileEvidenceRecord:
        del context, leaf_contract
        return ProfileEvidenceRecord("extension.contract.v1", 1, {})

    def evaluate_blockers(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> Iterable[PolicyBlocker]:
        del context, leaf_contract, contract_evidence
        return ()

    def validate_candidate(
        self,
        context: PolicyContext,
        leaf_contract: Mapping[str, Any],
        contract_evidence: ProfileEvidenceRecord,
    ) -> ProfileEvidenceRecord:
        del context, leaf_contract, contract_evidence
        return ProfileEvidenceRecord("extension.candidate.v1", 1, {})

    def validate_evidence(self, record: ProfileEvidenceRecord) -> bool:
        return bool(record.schema_version == 1)


def test_registry_contains_only_formal_profiles_and_preserves_extension_seam() -> None:
    expected = {"task", "bug", "documentation", "research"}
    assert set(DEFAULT_PROFILE_POLICY_REGISTRY.policies) == expected
    assert set(DEFAULT_PROFILE_POLICY_REGISTRY.policies_by_type_label) == {
        f"type:{profile_id}" for profile_id in expected
    }
    assert all(
        DEFAULT_PROFILE_POLICY_REGISTRY.resolve(profile_id).profile_id == profile_id
        for profile_id in expected
    )

    extension = RegistryExtensionPolicy()
    registry = ProfilePolicyRegistry.from_policies(extension)
    profile = replace(
        TASK_PROFILE,
        profile_id=extension.profile_id,
        canonical_type_label=extension.canonical_type_label,
    )
    assert registry.resolve(profile) is extension
    assert extension.profile_id not in DEFAULT_PROFILE_POLICY_REGISTRY.policies
    with pytest.raises(ProfilePolicyError, match="not registered"):
        DEFAULT_PROFILE_POLICY_REGISTRY.resolve("type:retired")
    with pytest.raises(TypeError):
        registry.policies["other"] = extension


def test_generic_kernel_models_have_no_profile_specific_fixed_slots() -> None:
    assert {field.name for field in fields(ProfileEffectDescriptor)} == {
        "effect_kind",
        "schema_version",
        "parameters",
        "postcondition",
        "receipt",
    }
    assert {field.name for field in fields(ProfileEvidenceEnvelope)} == {
        "profile_id",
        "schema_version",
        "contract",
        "candidate",
        "review",
        "completion",
    }
    for model in (lck_models.LiveState, lck_models.OperationSnapshot):
        names = {field.name.casefold() for field in fields(model)}
        assert not {"task_profile", "bug_profile", "documentation_profile"} & names
        assert "profile_id" not in names


def test_kernel_snapshots_are_frozen_at_the_model_boundary() -> None:
    state = _review_state()
    snapshots = (
        (lck_models.LiveState, state, "issue_number", 160),
        (
            lck_models.OperationSnapshot,
            lck_models.OperationSnapshot(operation="test", state=state),
            "operation",
            "changed",
        ),
    )
    for model, snapshot, field_name, replacement in snapshots:
        assert model.__dataclass_params__.frozen, model
        with pytest.raises(FrozenInstanceError):
            setattr(snapshot, field_name, replacement)


def test_leaf_contract_is_separate_from_profile_evidence_envelope() -> None:
    leaf_contract = _task_contract()
    state = lck_models.LiveState(
        issue_number=159,
        issue={"number": 159, "body_sha256": leaf_contract["body_sha256"]},
        target_branch="task/159-lck-core-live-state-resolution",
        leaf_contract=leaf_contract,
    )
    contract_result = validate_profile_contract(
        TASK_PROFILE,
        leaf_contract,
        context=PolicyContext(profile=TASK_PROFILE, issue=leaf_contract),
    )
    assert contract_result.valid
    assert contract_result.evidence is not None
    envelope = ProfileEvidenceEnvelope(
        profile_id=TASK_PROFILE.profile_id,
        contract=contract_result.evidence,
    ).validated(leaf_contract=state.leaf_contract)

    assert state.leaf_contract == leaf_contract
    assert "body" not in envelope.to_dict()["contract"]["payload"]
    assert "leaf_contract" not in envelope.to_dict()
    assert envelope.to_dict()["contract"]["payload"]["contract_ref"] == {
        "number": 159,
        "body_sha256": leaf_contract["body_sha256"],
    }


def test_formal_candidate_and_review_stages_use_the_policy_registry() -> None:
    leaf_contract = _task_contract()
    context = PolicyContext(
        profile=TASK_PROFILE,
        issue=leaf_contract,
        critical_outcome=lambda: {"status": "pass"},
    )
    contract = validate_profile_contract(
        TASK_PROFILE, leaf_contract, context=context
    ).evidence
    assert contract is not None
    candidate = validate_profile_candidate(
        TASK_PROFILE,
        leaf_contract,
        contract_evidence=contract,
        context=context,
    )
    assert candidate.kind == "task.candidate.v1"
    review = validate_profile_review(
        TASK_PROFILE,
        leaf_contract,
        {"verdict": "PASS"},
    )
    assert review.evidence is not None
    assert review.evidence.kind == "task.review.v1"


@pytest.mark.parametrize("profile", (TASK_PROFILE, BUG_PROFILE, DOCUMENTATION_PROFILE))
def test_non_research_review_rejects_research_outcome_at_policy_boundary(
    profile: LeafIssueWorkflowProfile,
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
