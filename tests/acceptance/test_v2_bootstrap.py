from __future__ import annotations

import ast
import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import tracequant
from tracequant.integrations import nautilus

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_NAUTILUS_VERSION = "2.0.0rc4"
EXPECTED_TOP_LEVEL = {
    ".env.example",
    ".github",
    ".gitignore",
    ".python-version",
    "LICENSE",
    "README.md",
    "config",
    "docs",
    "pyproject.toml",
    "src",
    "tests",
    "uv.lock",
    "uv.toml",
}


def _toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _candidate_paths() -> set[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {Path(line) for line in completed.stdout.splitlines() if line}


def _nautilus_lock_entry() -> dict[str, Any]:
    packages = cast(list[dict[str, Any]], _toml(REPOSITORY_ROOT / "uv.lock")["package"])
    matches = [entry for entry in packages if entry["name"] == "nautilus-trader"]
    assert len(matches) == 1
    return matches[0]


def _contains_forbidden_nautilus_reference(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(name.startswith("nautilus_trader") for name in names):
                return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("nautilus_trader")
        ):
            return True
    return False


def test_clean_bootstrap_uses_pinned_external_nautilus() -> None:
    pyproject = _toml(REPOSITORY_ROOT / "pyproject.toml")
    uv_policy = _toml(REPOSITORY_ROOT / "uv.toml")
    lock_entry = _nautilus_lock_entry()

    assert pyproject["project"]["requires-python"] == ">=3.13,<3.14"
    assert pyproject["project"]["dependencies"] == [
        f"nautilus-trader=={EXPECTED_NAUTILUS_VERSION}"
    ]
    assert uv_policy["no-build-package"] == ["nautilus-trader"]
    assert lock_entry["version"] == EXPECTED_NAUTILUS_VERSION
    assert lock_entry["source"] == {"registry": "https://pypi.org/simple"}
    assert not ({"git", "path", "directory", "editable", "virtual"} & lock_entry.keys())

    wheel_urls = [wheel["url"] for wheel in lock_entry["wheels"]]
    assert wheel_urls
    assert all(url.startswith("https://files.pythonhosted.org/") for url in wheel_urls)
    assert any("cp313" in url and url.endswith(".whl") for url in wheel_urls)

    distribution = importlib.metadata.distribution("nautilus-trader")
    assert distribution.version == EXPECTED_NAUTILUS_VERSION
    assert distribution.read_text("direct_url.json") is None
    assert nautilus.distribution_version() == EXPECTED_NAUTILUS_VERSION

    tracequant_origin = Path(tracequant.__file__).resolve()
    nautilus_origin = nautilus.package_origin()
    assert tracequant_origin.is_relative_to(REPOSITORY_ROOT / "src" / "tracequant")
    assert not nautilus_origin.is_relative_to(REPOSITORY_ROOT / "src")
    assert nautilus_origin.is_relative_to(Path(sys.prefix).resolve())
    assert nautilus.import_package().__name__ == "nautilus_trader"


def test_candidate_tree_matches_the_closed_v2_skeleton() -> None:
    paths = _candidate_paths()
    assert {path.parts[0] for path in paths} == EXPECTED_TOP_LEVEL

    forbidden_roots = {
        ".agents",
        ".claude",
        ".codex",
        ".workflow.local",
        "apps",
        "artifacts",
        "data",
        "deploy",
        "packages",
        "runtime",
        "scripts",
        "tools",
        "vendor",
    }
    assert not ({path.parts[0] for path in paths} & forbidden_roots)
    assert not any(path.is_relative_to(Path("docs/workflows")) for path in paths)
    assert not any(path.is_relative_to(Path("tests/tools")) for path in paths)

    production_python = {
        path
        for path in paths
        if path.suffix == ".py" and path.is_relative_to(Path("src"))
    }
    assert production_python == {
        Path("src/tracequant/__init__.py"),
        Path("src/tracequant/integrations/__init__.py"),
        Path("src/tracequant/integrations/nautilus/__init__.py"),
    }
    assert all(
        path.is_relative_to(Path("src/tracequant"))
        or path.is_relative_to(Path("tests"))
        for path in paths
        if path.suffix == ".py"
    )


def test_import_and_generated_path_guards_fail_closed() -> None:
    allowed_nautilus_import_root = Path("src/tracequant/integrations/nautilus")
    for relative_path in _candidate_paths():
        if relative_path.suffix != ".py" or not relative_path.is_relative_to(
            Path("src")
        ):
            continue
        tree = ast.parse((REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"))
        if not relative_path.is_relative_to(allowed_nautilus_import_root):
            assert not _contains_forbidden_nautilus_reference(tree)

        module_statements = [
            node
            for node in tree.body
            if not isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Import,
                    ast.ImportFrom,
                ),
            )
        ]
        assert not any(
            isinstance(descendant, ast.Call)
            for statement in module_statements
            for descendant in ast.walk(statement)
        )

    ignore_entries = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".gitignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {
        ".venv/",
        ".cache/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        ".coverage",
        "htmlcov/",
        "build/",
        "dist/",
        "*.egg-info/",
        "__pycache__/",
        ".env",
        ".env.*.local",
        "*.local.toml",
    } == ignore_entries
    assert (
        not {
            "data/",
            "catalog/",
            "cache/",
            "runs/",
            "artifacts/",
            "evidence/",
            "audit/",
            "models/",
            "logs/",
        }
        & ignore_entries
    )


def test_import_boundary_guard_rejects_direct_and_dynamic_references() -> None:
    prohibited_sources = (
        "import nautilus_trader",
        "from nautilus_trader.model import Order",
        'import_module("nautilus_trader")',
        'import_module(name="nautilus_trader")',
        'UPSTREAM_PACKAGE = "nautilus_trader"\nimport_module(UPSTREAM_PACKAGE)',
    )

    for source in prohibited_sources:
        assert _contains_forbidden_nautilus_reference(ast.parse(source))
