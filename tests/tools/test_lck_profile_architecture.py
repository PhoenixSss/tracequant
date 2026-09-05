# ruff: noqa: E402, I001

"""Acceptance tests for the typed profile policy/evidence boundary."""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import FrozenInstanceError, dataclass, fields, replace
from pathlib import Path
from collections.abc import Mapping
from types import SimpleNamespace
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
    ProfileCompletionResult,
    ProfileContractCheck,
    ProfileGateFailure,
    ProfileReviewResult,
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
    FakeReviewChecks,
    FakeReviewValidation,
    FakeReviewWorkspace,
    StaticResolver,
    _review_identity_value,
    _review_state,
    structured_review_receipt,
)


def _imported_modules(tree: ast.AST) -> set[str]:
    """Return normalized module names from an AST import graph."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.rsplit(".", 1)[-1])
            if node.level:
                modules.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
    return modules


def _referenced_symbols(tree: ast.AST) -> set[str]:
    """Return code and imported names, independent of comments/string literals."""
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
        elif isinstance(node, ast.alias):
            symbols.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                symbols.add(node.asname)
    return symbols


_PROFILE_SEMANTIC_MARKERS = frozenset(
    {
        "profile",
        "profile_id",
        "issue_profile",
        "issue_kind",
        "canonical_type_label",
        "critical_outcome",
        "research_outcome",
        "research_artifact",
        "bug_contract",
        "documentation_contract",
        "research_contract",
        "requires_critical_outcome",
        "supports_research_outcome",
        "candidate_capability",
        "contract_policy",
        "change_policy",
        "artifact_policy",
        "policy_entrypoints",
    }
)


def _profile_semantic_references(tree: ast.AST) -> list[ast.AST]:
    """Find profile-owned identifiers and mapping keys in a shared module."""
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    references: list[ast.AST] = []
    for node in ast.walk(tree):
        identifier: str | None = None
        if isinstance(node, ast.Name):
            identifier = node.id
        elif isinstance(node, ast.Attribute):
            identifier = node.attr
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifier = node.name
        elif isinstance(node, ast.arg):
            identifier = node.arg
        if identifier is not None:
            token = identifier.casefold()
            if token in _PROFILE_SEMANTIC_MARKERS or "profile" in token:
                references.append(node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            token = node.value.casefold()
            parent = parents.get(node)
            is_structural_string = isinstance(
                parent, (ast.Call, ast.Dict, ast.keyword, ast.Subscript)
            )
            if is_structural_string and (
                token in _PROFILE_SEMANTIC_MARKERS
                or any(
                    marker in token
                    for marker in (
                        "profile_",
                        "_profile",
                        "critical_outcome",
                        "research_outcome",
                        "research_artifact",
                    )
                )
            ):
                references.append(node)
    return references


def _profile_identity_references(tree: ast.AST) -> list[ast.AST]:
    """Find every direct or string-keyed profile identity access."""
    identity_attributes = {
        "profile_id",
        "issue_kind",
        "canonical_type_label",
    }
    references: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in identity_attributes:
            references.append(node)
        elif isinstance(node, ast.Attribute) and node.attr in identity_attributes:
            references.append(node)
        elif isinstance(node, ast.Constant) and node.value in identity_attributes:
            references.append(node)
    return references


def test_all_phase_controllers_use_only_generic_policy_capabilities() -> None:
    """Shared lifecycle controllers must not dispatch concrete profile capabilities."""
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


def test_controller_import_guard_catches_concrete_policy_aliases() -> None:
    """Concrete policy imports remain forbidden when imported under an alias."""
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
    probes = (
        "from profile_policies import ResearchOutcomeEffect",
        "from profile_policies import ResearchOutcomeEffect as OutcomeEffect",
        "from lck_core.profile_policies import ResearchOutcomeEffect",
        "from .profile_policies import ResearchOutcomeEffect as OutcomeEffect",
    )

    for source in probes:
        imported_symbols = _referenced_symbols(ast.parse(source))
        assert forbidden_capability_names & imported_symbols == {
            "ResearchOutcomeEffect"
        }, source


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
    for name in controller_names:
        tree = ast.parse((controller_root / name).read_text(encoding="utf-8"))
        identity_references = _profile_identity_references(tree)
        assert not identity_references, (name, identity_references)

    probes = (
        'if state.issue_profile["profile"]["profile_id"] == "task": pass',
        'if getattr(state, "profile_id", None) == "task": pass',
        'if state.issue_profile.get("canonical_type_label") == "type:task": pass',
    )
    for source in probes:
        assert _profile_identity_references(ast.parse(source)), source

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
    assert not _profile_semantic_references(shared_tree)
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


@pytest.mark.parametrize(
    "source",
    (
        'facts["research_outcome"] = "IMPLEMENT"',
        'facts.get("critical_outcome")',
        "def normalize_profile_specific_fact(value): return value",
    ),
)
def test_shared_facts_guard_rejects_profile_owned_semantics(source: str) -> None:
    assert _profile_semantic_references(ast.parse(source)), source


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
        self._mark("completion")
        repository = completion_input.get("repository")
        task_number = completion_input.get("task_number")
        if not isinstance(repository, str) or not isinstance(task_number, int):
            raise ProfilePolicyError("synthetic completion identity is invalid")
        parameters = {
            "repository": repository,
            "task_number": task_number,
            "project_number": 1,
            "field": "Status",
            "value": "Done",
        }
        descriptor = ProfileEffectDescriptor(
            effect_kind="project.single_select.set.v1",
            schema_version=1,
            parameters=parameters,
            postcondition={"kind": "project.single_select.equals", **parameters},
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
        {
            "task_number": 223,
            "repository": "owner/repo",
            "review_evidence": review.evidence,
        },
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
    expected_schemas = {
        ProfileEffectDescriptor: {
            "effect_kind",
            "schema_version",
            "parameters",
            "postcondition",
            "receipt",
        },
        PolicyContext: {
            "profile",
            "profile_id",
            "phase",
            "issue",
            "relationships",
            "repository",
            "downstream_contract",
            "repo_root",
            "runner",
            "base_sha",
            "head_sha",
            "include_index",
            "changed_files",
            "progress",
            "services",
            "review_identity",
            "review_record",
            "merged_pr",
            "review_verdict",
            "research_outcome",
            "critical_outcome",
            "documentation_validation",
            "research_validation",
        },
        ProfileEvidenceEnvelope: {
            "profile_id",
            "schema_version",
            "contract",
            "candidate",
            "review",
            "completion",
        },
        lck_models.LiveState: {
            "issue_number",
            "repository",
            "issue",
            "relationships",
            "git",
            "target_branch",
            "local_issue_branch",
            "local_issue_head",
            "remote_issue_branch",
            "remote_issue_oid",
            "open_pr",
            "merged_pr_numbers",
            "merged",
            "checks",
            "cleanup",
            "status",
            "stop_reasons",
            "warnings",
            "merged_pr",
            "leaf_contract",
            "issue_profile",
        },
        lck_models.OperationSnapshot: {
            "operation",
            "state",
            "required_checks",
            "acquisition_warnings",
            "fact_profile",
            "acquired_facts",
        },
        lck_models.EffectReceipt: {"effect", "action", "details"},
        ProfileEvidenceRecord: {"kind", "schema_version", "payload"},
        PolicyBlocker: {"code", "kind", "detail", "evidence_ref"},
        ProfileGateResults: {
            "critical_outcome",
            "documentation_validation",
            "research_validation",
            "profile_evidence",
        },
        ProfileGateFailure: {"detail", "profile_evidence", "legacy_results"},
        ProfileContractCheck: {
            "policy",
            "label",
            "valid",
            "contract",
            "detail",
            "evidence",
        },
        ProfileReviewResult: {"evidence", "artifact", "profile_evidence"},
        ProfileCompletionResult: {"evidence", "effect", "profile_evidence"},
        lck_review_workspace.ReviewTargetRefs: {
            "task_number",
            "pr_number",
            "base_sha",
            "head_sha",
            "task_body_sha256",
        },
        lck_review_workspace.ReviewIdentity: {
            "task_number",
            "pr_number",
            "base_sha",
            "head_sha",
            "task_body_sha256",
            "merge_base_sha",
            "effective_diff_sha256",
            "changed_files",
            "research_artifact",
        },
        lck_review.ReviewContext: {
            "review_id",
            "task_contract",
            "identity",
            "checks",
            "validation",
            "review_root",
            "issue_profile",
            "profile_evidence",
            "structured_review_protocol",
        },
        lck_delivery.DeliveryCompletionResult: {
            "task_number",
            "status",
            "branch",
            "head_sha",
            "critical_outcome",
            "validation",
            "checks",
            "effects",
            "operation_snapshot",
            "research_artifact",
            "profile_evidence",
        },
        lck_review.ReviewCompletionResult: {
            "review_id",
            "task_number",
            "verdict",
            "status",
            "identity",
            "record_path",
            "issue_profile",
            "profile_evidence",
            "structured_review",
        },
        lck_closeout.CloseoutResult: {
            "task_number",
            "status",
            "business_delivery",
            "cleanup",
            "effects",
            "operation_snapshot",
            "research_outcome",
            "profile_evidence",
        },
        lck_remediation.RemediationCompletionResult: {
            "task_number",
            "review_id",
            "delivery",
        },
    }
    for model, allowed_fields in expected_schemas.items():
        assert {field.name for field in fields(model)} == allowed_fields, model


def test_kernel_snapshots_are_frozen_at_the_model_boundary() -> None:
    """Live facts and operation snapshots cannot be mutated after acquisition."""
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

    class ProjectEffectRunner:
        def __init__(self, repository: str, task_number: int, value: str) -> None:
            self.repository = repository
            self.task_number = task_number
            self.value = value

        def run(
            self,
            argv: list[str] | tuple[str, ...],
            *,
            command_id: str,
            **_: Any,
        ) -> CommandResult:
            command = tuple(str(item) for item in argv)
            if command[:3] == ("gh", "api", "graphql"):
                return CommandResult(
                    command_id,
                    command,
                    0,
                    json.dumps(
                        {
                            "data": {
                                "user": {
                                    "projectV2": {
                                        "items": {
                                            "nodes": [
                                                {
                                                    "content": {
                                                        "number": self.task_number,
                                                        "repository": {
                                                            "nameWithOwner": self.repository
                                                        },
                                                    },
                                                    "fieldValueByName": {
                                                        "name": self.value
                                                    },
                                                }
                                            ],
                                            "pageInfo": {"hasNextPage": False},
                                        }
                                    }
                                },
                                "organization": None,
                            }
                        }
                    ),
                    "",
                )
            return CommandResult(
                command_id, command, 1, "", "unexpected effect command"
            )

    effect_resolver = SimpleNamespace(
        runner=ProjectEffectRunner("PhoenixSss/tracequant", 219, "Done")
    )
    effect_state = lck_models.LiveState(
        issue_number=219,
        repository="PhoenixSss/tracequant",
    )
    effects = lck_effects.DEFAULT_EFFECT_EXECUTOR_REGISTRY
    with pytest.raises(TypeError):
        effects.executors["other"] = lambda *_args, **_kwargs: None
    receipt = effects.execute(
        completion.effect,
        resolver=effect_resolver,
        state=effect_state,
    )
    assert receipt.effect == completion.effect.effect_kind
    assert receipt.action == "already-set"
    assert receipt.details["source"] == "synthetic-policy"
    assert receipt.is_complete
    assert not lck_models.EffectReceipt(
        "project.single_select.set.v1", "pending", {}
    ).is_complete

    with pytest.raises(lck_models.LckStopError, match="postcondition is invalid"):
        effects.execute(
            replace(completion.effect, postcondition={"kind": "invalid"}),
            resolver=effect_resolver,
            state=effect_state,
        )
    unauthorized_parameters = {
        **completion.effect.parameters,
        "task_number": 220,
    }
    with pytest.raises(lck_models.LckStopError, match="identity does not match"):
        effects.execute(
            replace(
                completion.effect,
                parameters=unauthorized_parameters,
                postcondition={
                    "kind": "project.single_select.equals",
                    **unauthorized_parameters,
                },
            ),
            resolver=effect_resolver,
            state=effect_state,
        )

    def mismatched_executor(
        _descriptor: ProfileEffectDescriptor, **_kwargs: Any
    ) -> lck_models.EffectReceipt:
        return lck_models.EffectReceipt("different.effect", "executed", {})

    mismatch_registry = lck_effects.EffectExecutorRegistry(
        {completion.effect.effect_kind: mismatched_executor}
    )
    with pytest.raises(
        lck_models.LckStopError,
        match="receipt does not match its descriptor",
    ):
        mismatch_registry.execute(
            completion.effect,
            resolver=effect_resolver,
            state=effect_state,
        )
    with pytest.raises(ProfilePolicyError, match="must be JSON data"):
        ProfileEffectDescriptor(
            effect_kind="project.single_select.set.v1",
            schema_version=1,
            parameters={"callable": mismatched_executor},
            postcondition={},
            receipt={},
        )
    with pytest.raises(lck_models.LckStopError, match="unknown completion effect"):
        effects.execute(
            replace(completion.effect, effect_kind="synthetic.unknown.v1"),
            resolver=effect_resolver,
            state=effect_state,
        )


def test_synthetic_profile_review_and_closeout_use_kernel_and_persist_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review and Closeout must execute policy stages through their controllers."""
    events: list[str] = []
    policy = SyntheticPolicy(events=events)
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
    resolver = StaticResolver(tmp_path, state)
    identity = _review_identity_value()
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    workspace = FakeReviewWorkspace(tmp_path / "review-root")

    review_context = lck_review.ReviewPreparer(
        resolver,
        validation=cast(Any, FakeReviewValidation()),
        checks_gate=cast(Any, FakeReviewChecks()),
        workspace=cast(Any, workspace),
        store=store,
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    ).prepare(159)
    assert review_context.profile_evidence is not None
    assert review_context.profile_evidence.review is not None
    assert review_context.profile_evidence.review.kind == policy.review_kind
    structured_review_file = tmp_path / "structured-review.json"
    structured_review_file.write_text(
        structured_review_receipt(identity).to_json(), encoding="utf-8"
    )

    review_result = lck_review.ReviewCompleter(
        resolver,
        checks_gate=cast(Any, FakeReviewChecks()),
        store=store,
        workspace=cast(Any, workspace),
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    ).complete(
        159,
        review_context.review_id,
        verdict="PASS",
        structured_review_file=structured_review_file,
    )
    assert review_result.status == "READY_FOR_MERGE_PREFLIGHT"
    assert review_result.profile_evidence is not None
    assert review_result.profile_evidence.review is not None
    assert review_result.profile_evidence.review.kind == policy.review_kind

    receipt_store = lck_receipts.AuditReceiptStore(tmp_path)
    review_view = lck_receipts._write_success_receipt(
        review_result,
        operation="review-complete",
        task_number=159,
        operation_id=review_result.review_id,
        store=receipt_store,
    )
    review_receipt = receipt_store.read(review_view["receipt_reference"])
    review_evidence = review_receipt["audit"]["profile_evidence"]
    assert review_evidence["profile_id"] == policy.profile_id
    assert review_evidence["review"]["kind"] == policy.review_kind

    merge_sha = "f" * 40
    closed_issue = dict(state.issue or {})
    closed_issue.update(
        {
            "state": "CLOSED",
            "project_status": "Done",
            "issue_closure": {
                "evidence_status": "complete",
                "status": "closed-by-pr",
                "closer_repository": state.repository,
                "closer_number": 200,
            },
        }
    )
    merged_pr = dict(state.open_pr or {})
    merged_pr.update(
        {
            "state": "MERGED",
            "mergeCommit": {"oid": merge_sha},
            "mergedAt": "2026-09-02T00:00:00Z",
            "closingIssuesReferences": [{"number": 159}],
        }
    )
    resolver.state = replace(
        state,
        issue=closed_issue,
        open_pr=None,
        merged=True,
        merged_pr=merged_pr,
        merged_pr_numbers=(200,),
    )

    class CloseoutEffectRunner:
        def __init__(self) -> None:
            self.remote_branch_exists = True
            self.commands: list[tuple[str, ...]] = []

        def run(
            self,
            argv: list[str] | tuple[str, ...],
            *,
            command_id: str,
            **_: Any,
        ) -> CommandResult:
            command = tuple(str(item) for item in argv)
            self.commands.append(command)
            if command[:3] == ("gh", "api", "graphql"):
                return CommandResult(
                    command_id,
                    command,
                    0,
                    json.dumps(
                        {
                            "data": {
                                "user": {
                                    "projectV2": {
                                        "items": {
                                            "nodes": [
                                                {
                                                    "content": {
                                                        "number": 159,
                                                        "repository": {
                                                            "nameWithOwner": "owner/repo"
                                                        },
                                                    },
                                                    "fieldValueByName": {
                                                        "name": "Done"
                                                    },
                                                }
                                            ],
                                            "pageInfo": {"hasNextPage": False},
                                        }
                                    }
                                },
                                "organization": None,
                            }
                        }
                    ),
                    "",
                )
            if command[:3] == ("gh", "issue", "view"):
                return CommandResult(
                    command_id,
                    command,
                    0,
                    json.dumps(
                        {
                            "state": "CLOSED",
                            "labels": [{"name": "codex:ready"}],
                            "projectItems": [{"fields": {"Status": "Done"}}],
                        }
                    ),
                    "",
                )
            if command == ("git", "rev-parse", "HEAD"):
                return CommandResult(command_id, command, 0, f"{merge_sha}\n", "")
            if command == ("git", "rev-parse", "refs/remotes/origin/main"):
                return CommandResult(command_id, command, 0, f"{merge_sha}\n", "")
            if command == (
                "git",
                "rev-parse",
                "refs/heads/task/159-lck-core-live-state-resolution",
            ):
                return CommandResult(command_id, command, 0, f"{'a' * 40}\n", "")
            if command == (
                "git",
                "ls-remote",
                "--heads",
                "origin",
                "task/159-lck-core-live-state-resolution",
            ):
                output = (
                    f"{'a' * 40}\trefs/heads/task/159-lck-core-live-state-resolution\n"
                    if self.remote_branch_exists
                    else ""
                )
                return CommandResult(command_id, command, 0, output, "")
            if command == (
                "git",
                "push",
                "origin",
                "--delete",
                "task/159-lck-core-live-state-resolution",
            ):
                self.remote_branch_exists = False
                return CommandResult(command_id, command, 0, "", "")
            if command[:3] in {
                ("git", "switch", "main"),
                ("git", "fetch", "--prune"),
            }:
                return CommandResult(command_id, command, 0, "", "")
            if command[:3] == ("git", "merge", "--ff-only"):
                return CommandResult(command_id, command, 0, "", "")
            if command[:2] == ("git", "merge-base"):
                return CommandResult(command_id, command, 0, "", "")
            if command[:3] == ("git", "worktree", "list"):
                return CommandResult(command_id, command, 0, "worktree /tmp/main\n", "")
            if command[:3] == ("git", "diff", "--quiet"):
                return CommandResult(command_id, command, 0, "", "")
            if command[:3] == ("git", "branch", "-d"):
                return CommandResult(command_id, command, 0, "", "")
            return CommandResult(
                command_id, command, 1, "", "unexpected closeout command"
            )

    closeout_runner = CloseoutEffectRunner()
    resolver.runner = closeout_runner

    closeout_result = lck_closeout.CloseoutCompleter(
        resolver,
        review_store=store,
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    ).complete(159)
    assert closeout_result.business_delivery == "COMPLETE"
    assert closeout_result.profile_evidence is not None
    assert closeout_result.profile_evidence.completion is not None
    assert closeout_result.profile_evidence.completion.kind == policy.completion_kind
    assert [effect.effect for effect in closeout_result.effects] == [
        "synchronize_main",
        "converge_task_metadata",
        "project.single_select.set.v1",
        "cleanup_task_refs",
    ]
    assert [effect.action for effect in closeout_result.effects] == [
        "synchronized",
        "already-converged",
        "already-set",
        "cleaned",
    ]
    assert closeout_result.effects[2].details["source"] == "synthetic-policy"

    closeout_view = lck_receipts._write_success_receipt(
        closeout_result,
        operation="closeout",
        task_number=159,
        operation_id="c" * 32,
        store=receipt_store,
    )
    closeout_receipt = receipt_store.read(closeout_view["receipt_reference"])
    closeout_evidence = closeout_receipt["audit"]["profile_evidence"]
    assert closeout_evidence["profile_id"] == policy.profile_id
    assert closeout_evidence["completion"]["kind"] == policy.completion_kind
    assert set(events) >= {"blocker", "contract", "review", "completion"}


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
    events: list[str] = []
    policy = SyntheticPolicy(events=events)
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
            if args[:3] == ("git", "diff", "--quiet"):
                return CommandResult(command_id, args, 1, "", "")
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
        def observe(self, _snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
            return {
                "status": "observed",
                "pr": {
                    "number": 200,
                    "head_sha": candidate_head,
                    "base_sha": start_head,
                },
            }

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

    delivery_calls: list[lck_models.Phase] = []
    original_delivery_complete = lck_delivery.DeliveryCompleter.complete

    def observe_delivery_complete(
        completer: lck_delivery.DeliveryCompleter,
        task_number: int,
        **kwargs: Any,
    ) -> lck_delivery.DeliveryCompletionResult:
        delivery_calls.append(kwargs.get("phase", lck_models.Phase.DELIVERY_COMPLETE))
        return original_delivery_complete(completer, task_number, **kwargs)

    monkeypatch.setattr(
        lck_delivery.DeliveryCompleter,
        "complete",
        observe_delivery_complete,
    )

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

    runner.head = start_head
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

    class ReusePrEffect:
        def __init__(self, _resolver: Any) -> None:
            pass

        def execute(self, _state: Any, **kwargs: Any) -> lck_models.EffectReceipt:
            return lck_models.EffectReceipt(
                "reuse_open_pr",
                "reused-current-open-pr",
                {
                    "number": 200,
                    "head_sha": kwargs["head_sha"],
                    "base_sha": kwargs["expected_base_sha"],
                },
            )

    monkeypatch.setattr(
        lck_delivery, "FormalValidationGate", lambda _resolver: Validation()
    )
    monkeypatch.setattr(
        lck_delivery, "CommitCurrentTreeEffect", lambda _resolver: CommitEffect()
    )
    monkeypatch.setattr(
        lck_delivery, "EnsureRemoteBranchEffect", lambda _resolver: RemoteEffect()
    )
    monkeypatch.setattr(
        lck_delivery, "SetReviewStatusEffect", lambda _resolver: StatusEffect()
    )
    monkeypatch.setattr(lck_remediation, "ReuseExistingOpenPrEffect", ReusePrEffect)

    remediation = lck_remediation.RemediationCompleter(
        resolver,
        store=store,
        checks_gate=ChecksGate(),
        policy_registry=registry,
        profile_resolver=resolve_synthetic,
    ).complete(
        state.task_number,
        review_id,
        commit_message="synthetic remediation",
        summary="exercise remediation evidence propagation",
    )
    assert delivery_calls == [
        lck_models.Phase.DELIVERY_COMPLETE,
        lck_models.Phase.REMEDIATION_COMPLETE,
    ]
    assert remediation.delivery.status == "READY_FOR_REVIEW"
    assert remediation.delivery.profile_evidence is not None
    assert remediation.delivery.profile_evidence.candidate is not None
    assert remediation.delivery.profile_evidence.candidate.kind == policy.candidate_kind
    remediation_receipt = lck_receipts._write_success_receipt(
        remediation,
        operation="remediation-complete",
        task_number=state.task_number,
        operation_id="e" * 32,
        store=lck_receipts.AuditReceiptStore(tmp_path),
    )
    assert remediation_receipt["profile_evidence"]["profile_id"] == policy.profile_id
    stored_remediation = lck_receipts.AuditReceiptStore(tmp_path).read(
        remediation_receipt["receipt_reference"]
    )
    assert (
        stored_remediation["audit"]["profile_evidence"]["candidate"]["kind"]
        == policy.candidate_kind
    )
    assert set(events) >= {"contract", "candidate"}
