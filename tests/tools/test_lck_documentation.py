# ruff: noqa: E402, I001

"""Acceptance tests for the enabled Documentation LCK profile."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from documentation_policy import (  # type: ignore[import-not-found]  # noqa: E402
    DocumentationPolicyStatus,
    documentation_contract_snapshot,
    evaluate_documentation_changes,
)
from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    delivery as lck_delivery,
    issue_profiles as lck_profiles,
)
from lck_core.models import Phase  # type: ignore[import-not-found]  # noqa: E402
from lck_core.validation import DocumentationValidationGate  # type: ignore[import-not-found]  # noqa: E402
from lck_test_support import (  # noqa: E402
    FakeRunner,
    _install_facts,
    _issue,
    _relationships,
    _resolver,
)


DOCUMENTATION_BODY = """### Documentation Goal

Explain the supported contract.

### Requirements

- Keep the documented behavior factual.

### Acceptance Criteria

- A reader can follow the contract.
"""


def test_documentation_profile_uses_shared_lifecycle_without_critical_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supported Documentation Delivery path selects shared profile gates."""

    resolution = lck_profiles.resolve_leaf_issue_profile(
        {"labels": ["type:documentation"]}
    )
    assert resolution.resolved
    assert resolution.profile is lck_profiles.DOCUMENTATION_PROFILE
    assert resolution.profile.lifecycle_enabled
    assert not resolution.profile.requires_critical_outcome
    assert resolution.profile.branch_namespace == "documentation/"

    issue = _issue()
    issue.update(
        {
            "labels": {"items": ["type:documentation", "codex:ready"]},
            "body": DOCUMENTATION_BODY,
            "documentation_contract": documentation_contract_snapshot(
                DOCUMENTATION_BODY
            ),
        }
    )
    fake = FakeRunner(branch="main")
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        relationships=_relationships(issue_type="Documentation"),
    )

    # The profile resolver and eligibility are the same shared controller
    # entrypoints used by Task; only the profile-specific gate is different.
    state = _resolver(fake).resolve(159)
    decision = lck_delivery.PhaseEligibilityResolver().resolve(
        state, Phase.DELIVERY_PREPARE
    )
    assert decision.eligible
    assert "verify_critical_outcome" not in decision.capabilities

    # This is intentionally a policy-only probe: no Critical Outcome verifier
    # is invoked for a Documentation candidate.
    resolver = cast(Any, type("Resolver", (), {"repo_root": tmp_path})())
    resolver.runner = fake

    class DocumentationGate:
        def run(self, _base_sha: str) -> dict[str, Any]:
            return {
                "status": "pass",
                "policy_id": "test-documentation-policy",
            }

    completer = lck_delivery.DeliveryCompleter(
        resolver,
        documentation_validation=cast(Any, DocumentationGate()),
    )
    completer._run_critical_outcome = lambda *_args, **_kwargs: pytest.fail(
        "Documentation must not invoke Critical Outcome"
    )
    result = completer._run_profile_gates(
        state,
        "a" * 40,
        progress=cast(Any, type("Progress", (), {"running": lambda *_args: None})()),
    )
    assert result is None


def test_documentation_contract_is_typed_and_does_not_execute_context() -> None:
    valid = documentation_contract_snapshot(DOCUMENTATION_BODY)
    assert valid["status"] == DocumentationPolicyStatus.PASS
    assert valid["required_sections"] == [
        "Documentation Goal",
        "Requirements",
        "Acceptance Criteria",
    ]

    invalid = documentation_contract_snapshot(
        DOCUMENTATION_BODY + "\n### Documentation Goal\n"
    )
    assert invalid["status"] == DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED
    assert "duplicate sections" in str(invalid["detail"])


@pytest.mark.parametrize(
    "changed_files",
    [
        ("src/tracequant/config.py",),
        ("tests/test_runtime.py",),
        ("tools/agent_workflow/lck.py",),
        ("AGENTS.md",),
        (".agents/policies/workflow-evidence.md",),
        ("docs/development/issue-workflow.md",),
        ("docs/workflows/lck.md",),
    ],
)
def test_documentation_safe_change_policy_requires_reclassification(
    changed_files: tuple[str, ...],
) -> None:
    result = evaluate_documentation_changes(changed_files)
    assert result.status is DocumentationPolicyStatus.RECLASSIFICATION_REQUIRED
    assert result.disallowed_files == changed_files
    assert "reclassification or split required" in result.detail


def test_documentation_safe_change_policy_allows_readme_and_reference_docs() -> None:
    result = evaluate_documentation_changes(
        ("README.md", "docs/architecture/domain-models.md")
    )
    assert result.status is DocumentationPolicyStatus.PASS
    assert result.disallowed_files == ()


def test_documentation_validation_gate_uses_typed_policy_without_issue_commands(
    tmp_path: Path,
) -> None:
    class Runner:
        def run(self, argv: Any, *, command_id: str, **_: Any) -> Any:
            assert tuple(argv) == ("git", "diff", "--name-only", "a" * 40, "--")
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": "README.md\n", "stderr": ""},
            )()

    resolver = type("Resolver", (), {"runner": Runner()})()
    result = DocumentationValidationGate(cast(Any, resolver)).run("a" * 40)
    assert result["status"] == DocumentationPolicyStatus.PASS
    assert result["policy_id"] == "repository-documentation-safe-v1"
