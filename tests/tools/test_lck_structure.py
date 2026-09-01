"""Structural guardrails for the responsibility-decomposed LCK implementation."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = ROOT / "tools" / "agent_workflow"
CORE = AGENT_WORKFLOW / "lck_core"


def _top_level_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_lck_decomposition_preserves_cli_and_responsibility_boundaries() -> None:
    facade = AGENT_WORKFLOW / "lck.py"
    source = facade.read_text(encoding="utf-8")
    assert len(source.splitlines()) < 500
    assert "from lck_core.cli import main" in source
    assert _top_level_symbols(facade) == set()
    assert not (AGENT_WORKFLOW / "lck").exists()

    owners = {
        "shared_facts.py": {
            "_git_snapshot",
            "_issue_view_with_contract",
            "_relationship_snapshot",
            "_normalize_checks",
        },
        "state.py": {"LiveStateResolver", "OperationSnapshotBuilder"},
        "eligibility.py": {"PhaseEligibilityResolver", "evaluate_shared_blockers"},
        "validation.py": {"FormalValidationGate", "DeliveryChecksGate"},
        "effects.py": {"CommitCurrentTreeEffect", "EnsureRemoteBranchEffect"},
        "delivery.py": {"DeliveryPreparer", "DeliveryCompleter"},
        "review_workspace.py": {"ReviewWorkspaceManager", "ReviewInvocationStore"},
        "review.py": {"ReviewPreparer", "ReviewCompleter", "MergePreflight"},
        "remediation.py": {"RemediationPreparer", "RemediationCompleter"},
        "closeout.py": {"CloseoutCompleter"},
        "receipts.py": {"AuditReceiptStore"},
    }
    for filename, expected in owners.items():
        assert expected <= _top_level_symbols(CORE / filename)

    # Shared/core modules must not import phase orchestration modules.
    forbidden = {"delivery", "review", "remediation", "closeout", "receipts"}
    for filename in ("models.py", "state.py", "eligibility.py", "validation.py"):
        tree = ast.parse((CORE / filename).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.rsplit(".", 1)[-1])
        assert imported.isdisjoint(forbidden), (filename, imported & forbidden)

    test_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "tests" / "tools").glob("test_lck*.py")
        if path.name != "test_lck_structure.py"
    )
    assert "monkeypatch.setattr(lck," not in test_sources
    assert "\nimport lck " not in test_sources

    result = subprocess.run(
        [sys.executable, str(facade), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "delivery" in result.stdout
    assert "review" in result.stdout
    assert "remediation" in result.stdout
    assert "closeout" in result.stdout


def test_shared_facts_is_profile_neutral_and_lck_core_bypasses_audit_adapter() -> None:
    shared = CORE / "shared_facts.py"
    tree = ast.parse(shared.read_text(encoding="utf-8"))
    forbidden = {
        "issue_profiles",
        "profile_policies",
        "eligibility",
        "delivery",
        "review",
        "remediation",
        "closeout",
        "workflow_evidence",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[-1])
    assert imported.isdisjoint(forbidden)

    for path in CORE.glob("*.py"):
        if path.name in {"shared_facts.py", "__init__.py"}:
            continue
        assert "workflow_evidence" not in path.read_text(encoding="utf-8"), path.name

    evidence = (AGENT_WORKFLOW / "workflow_evidence.py").read_text(encoding="utf-8")
    assert "from lck_core import shared_facts" in evidence
