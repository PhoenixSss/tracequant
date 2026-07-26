from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "tools" / "agent_workflow" / "workflow_validation.py"
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)


def _write_fake_tools(tmp_path: Path, *, fail: str | None = None) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"""#!{PYTHON}
import os, sys
args=sys.argv[1:]
key='-'.join(args)
if os.environ.get('FAKE_PATH_OUTPUT'):
    print(r'C:/Users/Maple/secret/file.txt /home/maple/private/file.txt')
fail=os.environ.get('FAKE_VALIDATION_FAIL')
if fail and fail in key:
    print('very long failure marker ' + 'x'*5000)
    sys.exit(1)
if args[:2] == ['lock','--check']:
    print('Resolved 3 packages')
elif 'pytest' in args:
    print('================ 12 passed in 0.10s ================')
elif args[-3:] == ['ruff','check','.'] or ('ruff' in args and 'check' in args):
    print('All checks passed!')
elif 'format' in args:
    print('8 files already formatted')
elif 'mypy' in args:
    print('Success: no issues found in 12 source files')
else:
    print('ok')
""",
        encoding="utf-8",
    )
    git = bin_dir / "git"
    git.write_text(
        f"""#!{PYTHON}
import os, sys
args=sys.argv[1:]
if args[:2] == ['diff','--name-only']:
    print(os.environ.get('FAKE_CHANGED_FILE', 'tools/agent_workflow/workflow_validation.py'))
elif args[:2] == ['diff','--check']:
    sys.exit(0)
else:
    sys.exit(0)
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    git.chmod(0o755)
    return bin_dir


def _write_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".agents" / "skills" / "task-delivery").mkdir(parents=True)
    (repo / ".gitignore").write_text(
        ".agents/validation.local/\n.agents/evidence.local/\n",
        encoding="utf-8",
    )
    (repo / "uv.lock").write_text("lock", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[tool.ruff]\ntarget-version='py311'\n[tool.mypy]\nstrict=true\n",
        encoding="utf-8",
    )
    return repo


def _run(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, str(SCRIPT), "run", "--repo-root", str(repo), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_help() -> None:
    result = subprocess.run(
        [PYTHON, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0


def test_success_output_is_compact_and_logs_are_ignored(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = _run(repo, env, "--phase", "delivery")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "pass"
    assert value["command_count"] == 6
    assert value["failed"] == 0
    assert len(result.stdout) < 10000
    assert "12 passed" in json.dumps(value)
    for command in value["commands"]:
        log = repo / command["log_path"]
        assert log.is_file()
        assert command["log_path"].startswith(".agents/validation.local/")


def test_failure_has_bounded_diagnostic_and_nonzero_exit(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_VALIDATION_FAIL"] = "pytest"
    result = _run(repo, env, "--phase", "review")
    assert result.returncode == 1, result.stderr
    value = json.loads(result.stdout)
    assert value["status"] == "fail"
    failed = [item for item in value["commands"] if item["status"] == "fail"]
    assert len(failed) == 1
    assert len(failed[0]["diagnostic"]) <= 2015
    assert "<truncated>" in failed[0]["diagnostic"]


def test_required_skill_validator_fails_closed_when_missing(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env.pop("CODEX_SKILL_VALIDATOR", None)
    result = _run(
        repo,
        env,
        "--phase",
        "feature-audit",
        "--include-skill-validators",
        "--require-skill-validator",
    )
    assert result.returncode == 2
    assert "required but unavailable" in result.stderr


def test_base_sha_detects_governance_change_and_runs_skill_validator(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    validator = tmp_path / "quick_validate.py"
    validator.write_text("import sys\nprint('valid', sys.argv[1])\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_CHANGED_FILE"] = ".agents/skills/task-delivery/SKILL.md"
    result = _run(
        repo,
        env,
        "--phase",
        "review",
        "--base-sha",
        "a" * 40,
        "--skill-validator",
        str(validator),
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["base_sha"] == "a" * 40
    assert any(item["command_id"] == "skill-task-delivery" for item in value["commands"])


def test_invalid_base_sha_fails_closed(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = _run(repo, env, "--phase", "review", "--base-sha", "not-a-sha")
    assert result.returncode == 2
    assert "full commit SHA" in result.stderr


def test_machine_absolute_paths_are_redacted_from_summary_and_log(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_PATH_OUTPUT"] = "1"
    result = _run(repo, env, "--phase", "delivery")
    assert result.returncode == 0, result.stderr
    assert "C:/Users" not in result.stdout
    assert "/home/maple" not in result.stdout
    value = json.loads(result.stdout)
    logs = [
        (repo / command["log_path"]).read_text(encoding="utf-8")
        for command in value["commands"]
    ]
    assert all("C:/Users" not in log and "/home/maple" not in log for log in logs)
    assert any("<absolute-path-redacted>" in log for log in logs)
