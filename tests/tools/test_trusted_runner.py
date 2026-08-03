from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "tools" / "agent_workflow" / "trusted_runner.py"
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_trusted_runner_executes_tool_from_locked_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    tools = repo / "tools" / "agent_workflow"
    tools.mkdir(parents=True)
    (repo / ".gitignore").write_text(".agents/evidence.local/\n", encoding="utf-8")
    (tools / "workflow_common.py").write_text("# base common\n", encoding="utf-8")
    (tools / "workflow_evidence.py").write_text(
        "import json, os\n"
        'print(json.dumps({"marker": "base", "source": '
        'os.environ.get("WORKFLOW_TRUSTED_RUNNER_SHA")}))\n',
        encoding="utf-8",
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".gitignore", "tools")
    _git(repo, "commit", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    (tools / "workflow_evidence.py").write_text(
        "print('head must not execute')\n", encoding="utf-8"
    )

    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--repo-root",
            str(repo),
            "--trusted-sha",
            base_sha,
            "--tool",
            "evidence",
            "--",
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value == {"marker": "base", "source": base_sha}
    manifest = (
        repo
        / ".agents"
        / "evidence.local"
        / "trusted"
        / base_sha[:16]
        / "evidence"
        / "manifest.json"
    )
    assert manifest.is_file()
    manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_value["trusted_sha"] == base_sha
    assert manifest_value["tool"] == "evidence"


def test_trusted_runner_rejects_invalid_sha(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--repo-root",
            str(repo),
            "--trusted-sha",
            "not-a-sha",
            "--tool",
            "evidence",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2
    assert "invalid trusted SHA" in result.stderr


def test_trusted_runner_executes_fixed_front_door_bundle(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    tools = repo / "tools" / "agent_workflow"
    rules = repo / ".codex" / "rules"
    tools.mkdir(parents=True)
    rules.mkdir(parents=True)
    (repo / ".gitignore").write_text(".agents/evidence.local/\n", encoding="utf-8")
    (tools / "workflow_common.py").write_text("# common\n", encoding="utf-8")
    (tools / "workflow_evidence.py").write_text("# evidence\n", encoding="utf-8")
    (tools / "wsl2_github_evidence_profiles.json").write_text("{}\n", encoding="utf-8")
    (rules / "quant-system-wsl-evidence.rules").write_text(
        "# rules\n", encoding="utf-8"
    )
    (tools / "wsl2_github_evidence_runner.py").write_text(
        "import json, os\n"
        "print(json.dumps({"
        "'target': os.environ.get('WORKFLOW_TARGET_REPO_ROOT'),"
        "'bundle': os.environ.get('WORKFLOW_TRUSTED_BUNDLE_ROOT'),"
        "'sha': os.environ.get('WORKFLOW_TRUSTED_RUNNER_SHA')"
        "}))\n",
        encoding="utf-8",
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".gitignore", "tools", ".codex")
    _git(repo, "commit", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--trusted-sha",
            base_sha,
            "--tool",
            "evidence-runner",
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["target"] == str(repo.resolve())
    assert value["sha"] == base_sha
    bundle = Path(value["bundle"])
    assert bundle.is_relative_to(repo / ".agents/evidence.local/trusted")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["tool"] == "evidence-runner"
    assert manifest["entry"] == ("tools/agent_workflow/wsl2_github_evidence_runner.py")


def test_fixed_front_door_rejects_alternate_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    alternate = tmp_path / "other"
    alternate.mkdir()
    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--repo-root",
            str(alternate),
            "--trusted-sha",
            "a" * 40,
            "--tool",
            "validation-runner",
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 2
    assert "current repository root" in result.stderr


def test_trusted_runner_executes_real_validation_front_door(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    repo = tmp_path / "repo"
    for relative in (
        "tools/agent_workflow/workflow_common.py",
        "tools/agent_workflow/workflow_validation.py",
        "tools/agent_workflow/wsl2_validation_runner.py",
        "tools/agent_workflow/wsl2_validation_profiles.json",
        ".codex/rules/quant-system-wsl-validation.rules",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / relative).read_bytes())
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/ci.yml").write_text(
        """name: CI
jobs:
  quality:
    steps:
      - name: Validate lock file
        run: uv lock --check
      - name: Run tests
        run: uv run --frozen pytest
      - name: Run Ruff lint
        run: uv run --frozen ruff check .
      - name: Check Ruff formatting
        run: uv run --frozen ruff format --check .
      - name: Run mypy
        run: uv run --frozen mypy src tests
""",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(
        ".agents/evidence.local/\n.agents/validation.local/\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 88\n[tool.mypy]\npython_version = '3.11'\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  'run --frozen pytest tests/tools') echo '7 passed' ;;\n"
        "  *) echo unexpected >&2; exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "trusted runner base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "--trusted-sha",
            base_sha,
            "--tool",
            "validation-runner",
            "--",
            "targeted",
        ],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    digest = json.loads(result.stdout)
    assert digest["profile"] == "targeted"
    stored = json.loads((repo / digest["result_path"]).read_text(encoding="utf-8"))
    assert stored["integrity"]["verification"] == (
        "trusted-commit-bundle-pre-execution"
    )
    assert stored["commands"][0]["argv"] == [
        "uv",
        "run",
        "--frozen",
        "pytest",
        "tests/tools",
    ]
