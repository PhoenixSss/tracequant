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
        "print(json.dumps({'marker':'base','source':os.environ.get('WORKFLOW_TRUSTED_RUNNER_SHA')}))\n",
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
