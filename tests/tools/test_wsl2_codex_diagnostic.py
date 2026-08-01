from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).parents[2] / "tools" / "wsl2_codex_diagnostic.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wsl2_codex_diagnostic", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def _write_fake_uv(bin_dir: Path) -> None:
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'uv 0.11.28'; exit 0; fi\n"
        "if [ \"$1\" = \"run\" ] && [ \"$2\" = \"python\" ]; then "
        "echo 'Python 3.11.9'; exit 0; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)


def test_redact_text_hides_paths_proxy_credentials_and_tokens() -> None:
    module = _load_module()
    os.environ["HTTPS_PROXY"] = "http://user:password@127.0.0.1:7897/path?secret=1"
    try:
        result = module.redact_text(
            "/home/maple/repo github_pat_ABC123 "
            "http://user:password@127.0.0.1:7897/path?secret=1",
            home=Path("/home/maple"),
            repo_root=Path("/home/maple/repo"),
        )
    finally:
        os.environ.pop("HTTPS_PROXY", None)
    assert "github_pat_ABC123" not in result
    assert "password" not in result
    assert "secret=1" not in result
    assert "<repo>" in result
    assert result.endswith("http://127.0.0.1:7897/path")


def test_local_diagnostic_writes_parseable_bounded_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(
        ".agents/evidence.local/\n.agents/validation.local/\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)
    output_dir = tmp_path / "output"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(repo),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "test-run",
            "--skip-project-validation",
            "--skip-workspace-probe",
            "--skip-temp-git-probe",
            "--json",
        ],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["run_id"] == "test-run"

    expected_files = {
        "environment.json",
        "commands.jsonl",
        "capability-matrix.json",
        "guardian-approval-matrix.json",
        "network-summary.json",
        "git-github-summary.json",
        "validation-summary.json",
        "diagnostic-report.md",
    }
    assert {path.name for path in output_dir.iterdir()} == expected_files
    json.loads((output_dir / "environment.json").read_text(encoding="utf-8"))
    json.loads((output_dir / "capability-matrix.json").read_text(encoding="utf-8"))
    commands = [
        json.loads(line)
        for line in (output_dir / "commands.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert commands
    assert all(len(item["stdout_summary"]) <= 4_020 for item in commands)
    assert all(len(item["stderr_summary"]) <= 4_020 for item in commands)


def test_formal_write_probe_requires_explicit_confirmation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".agents/evidence.local/\n", encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(repo),
            "--output-dir",
            str(tmp_path / "output"),
            "--skip-project-validation",
            "--skip-workspace-probe",
            "--skip-temp-git-probe",
            "--formal-write-probe",
            "--json",
        ],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    assert payload["errors"] == [
        "formal write probe requires --confirm-formal-write-probe DELETE_LOCAL_PROBE"
    ]


def test_formal_write_probe_uses_disposable_worktree_and_cleans_up(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".agents/evidence.local/\n", encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "base")
    original_head = _git(repo, "rev-parse", "HEAD")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(repo),
            "--output-dir",
            str(tmp_path / "output"),
            "--skip-project-validation",
            "--skip-workspace-probe",
            "--skip-temp-git-probe",
            "--formal-write-probe",
            "--confirm-formal-write-probe",
            "DELETE_LOCAL_PROBE",
            "--json",
        ],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["capabilities"]["formal-repository-write"] == "pass"
    assert _git(repo, "rev-parse", "HEAD") == original_head
    assert _git(repo, "branch", "--list", "diagnostic/wsl2-write-probe-*") == ""
    assert len(_git(repo, "worktree", "list").splitlines()) == 1


def test_committed_wsl2_materials_are_parseable_and_redacted() -> None:
    root = Path(__file__).parents[2]
    docs = root / "docs" / "workflows" / "wsl2-codex-environment"
    diagnostic = json.loads(
        (docs / "current-diagnostic.json").read_text(encoding="utf-8")
    )
    article = json.loads(
        (docs / "article-materials.json").read_text(encoding="utf-8")
    )
    assert diagnostic["security"]["sensitive_data_committed"] is False
    assert diagnostic["security"]["broad_rules_configured"] is False
    assert diagnostic["security"]["task_65_candidate_executed"] is False
    assert article["redaction"]["raw_credentials_included"] is False
    assert article["redaction"]["raw_rollouts_included"] is False

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(docs.iterdir())
        if path.is_file()
    )
    assert "github_pat_" not in combined
    assert "ghp_" not in combined
    assert "/home/maple/" not in combined
    assert "password@" not in combined
