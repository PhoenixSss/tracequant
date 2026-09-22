from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

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
APPROVED_NON_LCK_GUIDES = {
    Path("docs/guides/nautilustrader-import-and-update-policy.md"),
}
HISTORICAL_LCK_GUIDE_PATHS = {
    Path("docs/guides/LCK-overview.md"),
    Path("docs/guides/LCK-adoption.md"),
}
FOUNDATION_SELECTION_DIR = Path("docs/research/foundation-selection")
CAPABILITY_VALIDATION_BASELINE = Path(
    "docs/research/nautilustrader-capability-validation-2026-09-12.md"
)
REGISTERED_FOUNDATION_REPORTS = {
    "TraceQuant 开源技术栈与自研边界深度研究.md": (
        "5fc90e347c8115301c673f4f98dde6070555332062f40fb5c55451b72ee9b88a"
    ),
    "TraceQuant Trading Runtime Read-Only Review.md": (
        "93f607c0adef9f6f27e96f4a835c4e258902ce546c62d433bc4d0a8d74d26179"
    ),
    "TraceQuant Nautilus 技术栈与自研边界复审.md": (
        "954edd7b2f7439b261225d0c46266e68fa6bba7ce33ab8feacf50779b8dea89e"
    ),
    "TraceQuant 分阶段推进计划.md": (
        "6710bb0505d19971ea46959cf0644cef97720a8a348326e826929d841dd76001"
    ),
}
_MARKDOWN_LINK = re.compile(r"\[(?P<label>[^\]]+)\]\(\s*<?(?P<target>[^>\)]+)>?\s*\)")


def _toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _candidate_paths() -> set[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return {Path(entry.decode()) for entry in completed.stdout.split(b"\0") if entry}


def _documented_production_python() -> set[Path]:
    document = (
        REPOSITORY_ROOT / "docs/architecture/repository-structure.md"
    ).read_text(encoding="utf-8")
    marker = "The complete production package is:\n\n```text\n"
    _, found, remainder = document.partition(marker)
    assert found
    tree, found, _ = remainder.partition("\n```")
    assert found

    directories: list[tuple[int, Path]] = []
    python_paths: set[Path] = set()
    for line in tree.splitlines():
        name = line.strip()
        if not name:
            continue
        indentation = len(line) - len(line.lstrip())
        while directories and directories[-1][0] >= indentation:
            directories.pop()
        parent = directories[-1][1] if directories else Path()
        path = parent / name.removesuffix("/")
        if name.endswith("/"):
            directories.append((indentation, path))
        elif path.suffix == ".py":
            python_paths.add(path)
    return python_paths


def _guide_paths_outside_approved_lck_layout(paths: set[Path]) -> set[Path]:
    return {
        path
        for path in paths
        if path.is_relative_to(Path("docs/guides"))
        and not path.is_relative_to(Path("docs/guides/lck"))
        and path not in APPROVED_NON_LCK_GUIDES
    }


def _nautilus_lock_entry() -> dict[str, Any]:
    packages = cast(list[dict[str, Any]], _toml(REPOSITORY_ROOT / "uv.lock")["package"])
    matches = [entry for entry in packages if entry["name"] == "nautilus-trader"]
    assert len(matches) == 1
    return matches[0]


def _is_name_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
        return False
    if len(test.comparators) != 1:
        return False
    comparator = test.comparators[0]
    return isinstance(comparator, ast.Constant) and comparator.value == "__main__"


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
        "lightgbm==4.7.0",
        f"nautilus-trader=={EXPECTED_NAUTILUS_VERSION}",
        "polars==1.44.2",
        "zstandard==0.25.0",
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
    assert not (paths & HISTORICAL_LCK_GUIDE_PATHS)
    assert not _guide_paths_outside_approved_lck_layout(paths)

    production_python = {
        path
        for path in paths
        if path.suffix == ".py" and path.is_relative_to(Path("src"))
    }
    assert _documented_production_python() == production_python
    assert production_python == {
        Path("src/tracequant/__init__.py"),
        Path("src/tracequant/integrations/__init__.py"),
        Path("src/tracequant/integrations/nautilus/__init__.py"),
        Path("src/tracequant/integrations/nautilus/stage1_backtest.py"),
        Path("src/tracequant/integrations/nautilus/stage1_btcusdt.py"),
        Path("src/tracequant/integrations/nautilus/stage2_artifact.py"),
        Path("src/tracequant/integrations/nautilus/stage2_btceth.py"),
        Path("src/tracequant/integrations/nautilus/stage2_source_artifact.py"),
        Path("src/tracequant/integrations/nautilus/stage3_evaluation.py"),
        Path("src/tracequant/integrations/nautilus/stage3_model.py"),
        Path("src/tracequant/integrations/nautilus/stage3_momentum.py"),
        Path("src/tracequant/integrations/nautilus/stage3_oos.py"),
        Path("src/tracequant/integrations/nautilus/stage4_demo.py"),
        Path("src/tracequant/integrations/nautilus/stage4_demo_data.py"),
        Path("src/tracequant/integrations/nautilus/stage4_demo_evidence.py"),
        Path("src/tracequant/integrations/nautilus/strategies/__init__.py"),
        Path("src/tracequant/integrations/nautilus/strategies/stage1_ma_cross.py"),
        Path("src/tracequant/integrations/nautilus/strategies/stage3_model.py"),
        Path("src/tracequant/integrations/nautilus/strategies/stage3_momentum.py"),
        Path("src/tracequant/research/__init__.py"),
        Path("src/tracequant/research/source_schema.py"),
        Path("src/tracequant/research/stage3_artifacts.py"),
        Path("src/tracequant/research/stage3_features.py"),
        Path("src/tracequant/research/views.py"),
        Path("src/tracequant/source_data/__init__.py"),
        Path("src/tracequant/source_data/stage1_btcusdt.py"),
        Path("src/tracequant/source_data/stage2_btceth.py"),
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
            and not _is_name_main_guard(node)
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


def test_lck_guide_guard_rejects_historical_and_escaped_paths() -> None:
    allowed = {
        Path("docs/guides/lck/overview.md"),
        Path("docs/guides/lck/adoption.md"),
        Path("docs/guides/nautilustrader-import-and-update-policy.md"),
    }
    assert not (allowed & HISTORICAL_LCK_GUIDE_PATHS)
    assert not _guide_paths_outside_approved_lck_layout(allowed)
    assert _guide_paths_outside_approved_lck_layout(
        allowed | {Path("docs/guides/LCK-overview.md")}
    ) == {Path("docs/guides/LCK-overview.md")}
    assert _guide_paths_outside_approved_lck_layout(
        allowed | {Path("docs/guides/LCK-adoption.md")}
    ) == {Path("docs/guides/LCK-adoption.md")}
    assert _guide_paths_outside_approved_lck_layout(
        allowed | {Path("docs/guides/other/overview.md")}
    ) == {Path("docs/guides/other/overview.md")}


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


def test_v2_foundation_source_documents_are_tracked_and_registered() -> None:
    tracked = {
        path.name
        for path in _candidate_paths()
        if path.parent == FOUNDATION_SELECTION_DIR
    }
    assert tracked == set(REGISTERED_FOUNDATION_REPORTS)
    attributes = (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "docs/research/foundation-selection/*.md -whitespace" in attributes

    baseline_path = REPOSITORY_ROOT / CAPABILITY_VALIDATION_BASELINE
    baseline = baseline_path.read_text(encoding="utf-8")
    register = baseline.split("## External report register", 1)[1]
    assert "remain outside the repository" not in register
    assert "supporting source reports" in register
    assert "adr-0001-nautilustrader-primary-runtime.md" in register
    assert "repository-structure.md" in register
    assert "Current GitHub Issues" in register
    assert "does not reopen the completed runtime selection" in register

    resolved_reports: set[Path] = set()
    for match in _MARKDOWN_LINK.finditer(register):
        target = unquote(match.group("target").strip())
        if "://" in target:
            continue
        relative_target = target.split("#", 1)[0]
        if not relative_target:
            continue
        resolved = (baseline_path.parent / relative_target).resolve()
        if resolved.is_file():
            resolved_reports.add(resolved)

    for name, digest in REGISTERED_FOUNDATION_REPORTS.items():
        report_path = REPOSITORY_ROOT / FOUNDATION_SELECTION_DIR / name
        assert hashlib.sha256(report_path.read_bytes()).hexdigest() == digest
        assert digest in register
        assert name in register
        assert report_path.resolve() in resolved_reports
