"""Structural guardrails for the isolated LCK package."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "tools" / "lck"


def _symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_lck_decomposition_preserves_responsibility_boundaries() -> None:
    owners = {
        "state.py": {"LiveStateResolver", "OperationSnapshotBuilder"},
        "eligibility.py": {"PhaseEligibilityResolver", "evaluate_shared_blockers"},
        "validation_gates.py": {"FormalValidationGate", "DeliveryChecksGate"},
        "effects.py": {"CommitCurrentTreeEffect", "EnsureRemoteBranchEffect"},
        "delivery.py": {"DeliveryPreparer", "DeliveryCompleter"},
        "review_workspace.py": {"ReviewWorkspaceManager", "ReviewInvocationStore"},
        "review.py": {"ReviewPreparer", "ReviewCompleter", "MergePreflight"},
        "remediation.py": {"RemediationPreparer", "RemediationCompleter"},
        "closeout.py": {"CloseoutCompleter"},
        "receipts.py": {"AuditReceiptStore"},
    }
    for filename, expected in owners.items():
        assert expected <= _symbols(CORE / filename)


def test_package_entrypoint_is_thin_and_stable() -> None:
    entrypoint = (CORE / "__main__.py").read_text(encoding="utf-8")
    assert "from .cli import main" in entrypoint
    assert len(entrypoint.splitlines()) < 20
    assert not (ROOT / "tools" / "agent_workflow").exists()


def test_shared_modules_do_not_import_phase_orchestration() -> None:
    forbidden = {"delivery", "review", "remediation", "closeout", "receipts"}
    for filename in ("models.py", "state.py", "eligibility.py", "validation_gates.py"):
        tree = ast.parse((CORE / filename).read_text(encoding="utf-8"))
        imported = {
            node.module.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert imported.isdisjoint(forbidden), (filename, imported & forbidden)
