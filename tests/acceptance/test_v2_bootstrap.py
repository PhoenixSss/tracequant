from __future__ import annotations

import ast
import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import tracequant
import tracequant.integrations.nautilus as nautilus

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_NAUTILUS_VERSION = "2.0.0rc4"
EXPECTED_TOP_LEVEL = {
    ".agents",
    ".claude",
    ".codex",
    ".env.example",
    ".gitattributes",
    ".github",
    ".gitignore",
    ".python-version",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    "README.md",
    "config",
    "docs",
    "pyproject.toml",
    "src",
    "tests",
    "tools",
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


def test_candidate_tree_matches_the_approved_product_and_lck_layout() -> None:
    paths = _candidate_paths()
    assert {path.parts[0] for path in paths} == EXPECTED_TOP_LEVEL

    forbidden_roots = {
        ".workflow.local",
        "apps",
        "artifacts",
        "data",
        "deploy",
        "packages",
        "runtime",
        "scripts",
        "vendor",
    }
    assert not ({path.parts[0] for path in paths} & forbidden_roots)
    assert all(
        path.is_relative_to(Path("tools/lck"))
        for path in paths
        if path.is_relative_to(Path("tools"))
    )
    assert all(
        path.is_relative_to(Path("tests/tools/lck"))
        for path in paths
        if path.is_relative_to(Path("tests/tools"))
    )
    assert all(
        path.is_relative_to(Path("docs/workflows/lck"))
        for path in paths
        if path.is_relative_to(Path("docs/workflows"))
    )
    assert all(
        path.is_relative_to(Path("docs/guides/lck"))
        for path in paths
        if path.is_relative_to(Path("docs/guides/lck"))
    )

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
        or path.is_relative_to(Path("tools/lck"))
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
        ".agents/execution-profile.local.toml",
        ".agents/evidence.local/",
        ".agents/validation.local/",
        ".workflow.local/",
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


def test_lck_and_product_import_boundaries_are_one_way() -> None:
    for relative_path in _candidate_paths():
        if relative_path.suffix != ".py":
            continue
        if not (
            relative_path.is_relative_to(Path("src"))
            or relative_path.is_relative_to(Path("tools/lck"))
        ):
            continue
        tree = ast.parse((REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        if relative_path.is_relative_to(Path("src")):
            assert not any(
                name == "tools.lck" or name.startswith("tools.lck.")
                for name in imported
            )
        else:
            assert not any(
                name == "tracequant"
                or name.startswith("tracequant.")
                or name == "nautilus_trader"
                or name.startswith("nautilus_trader.")
                for name in imported
            )


def test_distribution_excludes_lck_tooling() -> None:
    build = _toml(REPOSITORY_ROOT / "pyproject.toml")["tool"]["uv"]["build-backend"]
    assert build == {"module-name": "tracequant", "module-root": "src"}


def test_repository_text_approves_lck_without_expanding_product_runtime() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    structure = (
        REPOSITORY_ROOT / "docs/architecture/repository-structure.md"
    ).read_text(encoding="utf-8")
    baseline = (REPOSITORY_ROOT / "docs/architecture/technical-baseline.md").read_text(
        encoding="utf-8"
    )

    assert "approved Local Control Kernel (LCK)" in readme
    assert "tooling under `tools/lck/`" in readme
    assert "initial v2 bootstrap restriction" in readme
    assert "initial bootstrap text" in structure
    assert "exact LCK-owned roots" in structure
    assert "Repository-only LCK" in baseline
    assert "engineering tooling is approved outside this runtime" in baseline
    assert "LCK is not part of the TraceQuant product runtime" in readme
