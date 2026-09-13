"""Repository-level acceptance tests for the restored LCK capability."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from tools.lck import cli, issue_profiles

ROOT = Path(__file__).resolve().parents[3]
LCK_ROOT = ROOT / "tools" / "lck"
SKILLS = (
    "task-delivery-runner",
    "task-pr-review-runner",
    "task-closeout",
    "feature-completion-audit",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_restored_lck_runs_from_current_repository_layout() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.lck", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    for command in ("delivery", "review", "remediation", "merge", "closeout"):
        assert command in result.stdout

    assert Path(cli.__file__).resolve().is_relative_to(LCK_ROOT)
    assert not (ROOT / "tools" / "agent_workflow").exists()
    assert not (ROOT / "src" / "tracequant" / "contracts" / "review.py").exists()


def test_restored_lck_is_separate_from_product_code_and_distribution() -> None:
    for path in LCK_ROOT.glob("*.py"):
        imported = _imports(path)
        assert not any(
            name == "tracequant"
            or name.startswith("tracequant.")
            or name == "nautilus_trader"
            or name.startswith("nautilus_trader.")
            for name in imported
        ), path

    product_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "tracequant").rglob("*.py")
    )
    assert "tools.lck" not in product_source
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'module-root = "src"' in pyproject
    assert 'module-name = "tracequant"' in pyproject


def test_lck_exposes_the_complete_lifecycle_parser() -> None:
    parser = cli._build_parser()
    commands = (
        ("delivery", "prepare", "333"),
        (
            "delivery",
            "complete",
            "333",
            "--commit-message",
            "m",
            "--summary",
            "s",
        ),
        ("review", "prepare", "333"),
        (
            "review",
            "complete",
            "333",
            "--review-id",
            "r",
            "--verdict",
            "PASS",
        ),
        ("remediation", "prepare", "333", "--review-id", "r"),
        ("merge", "preflight", "333"),
        ("closeout", "333"),
    )
    for argv in commands:
        assert parser.parse_args(argv).command == argv[0]


def test_typed_leaf_profiles_share_the_restored_kernel() -> None:
    profiles = issue_profiles.PROFILES_BY_TYPE_LABEL
    assert set(profiles) == {
        "type:task",
        "type:bug",
        "type:documentation",
        "type:research",
    }
    assert all(profile.lifecycle_enabled for profile in profiles.values())
    assert len({profile.profile_id for profile in profiles.values()}) == 4


def test_agent_assets_have_one_canonical_source_and_thin_adapters() -> None:
    for skill in SKILLS:
        canonical = ROOT / ".agents" / "skills" / skill / "SKILL.md"
        adapter = ROOT / ".claude" / "skills" / skill / "SKILL.md"
        assert canonical.is_file()
        adapter_text = adapter.read_text(encoding="utf-8")
        assert canonical.relative_to(ROOT).as_posix() in adapter_text
        assert len(adapter_text.splitlines()) < 15
        assert "python -m tools.lck" in canonical.read_text(encoding="utf-8")


def test_restoration_manifest_records_source_objects_and_decisions() -> None:
    manifest = (ROOT / "docs/workflows/lck/restoration-manifest.md").read_text(
        encoding="utf-8"
    )
    assert "0d9d762238a3e837a864b5350bf16d1b885a73c3" in manifest
    assert "tools/lck/" in manifest
    assert "tests/tools/lck/" in manifest
    assert "docs/workflows/lck/" in manifest
    assert "restored/adapted" in manifest
    assert "omitted" in manifest
