from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.tools.lck.skill_package_support import copy_instruction_packages
from tools.lck.skill_package import resolve_skill_package

SCRIPT = Path(__file__).parents[3] / "tools" / "lck" / "validation_runner.py"
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)


def _write_fake_tools(tmp_path: Path, *, fail: str | None = None) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def add_windows_launcher(name: str) -> None:
        if os.name != "nt":
            return
        launcher = bin_dir / f"{name}.cmd"
        launcher.write_text(
            f'@echo off\r\n"{PYTHON}" "{bin_dir / name}" %*\r\n',
            encoding="utf-8",
        )

    uv = bin_dir / "uv"
    uv.write_text(
        f"""#!{PYTHON}
import os, sys
from pathlib import Path
args=sys.argv[1:]
key='-'.join(args)
if os.environ.get('FAKE_INSTRUCTION_MUTATION'):
    target=Path('.agents/skills/task-delivery-runner/references/remediation.md')
    target.write_text(target.read_text()+'\\nChanged during validation.\\n')
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
    print(os.environ.get('FAKE_CHANGED_FILE', 'src/example.py'))
elif args[:2] == ['diff','--check']:
    sys.exit(0)
else:
    sys.exit(0)
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    git.chmod(0o755)
    add_windows_launcher("uv")
    add_windows_launcher("git")
    return bin_dir


def _write_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".agents" / "skills" / "task-delivery-runner").mkdir(parents=True)
    copy_instruction_packages(repo)
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


def _run(
    repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    env = env.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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
    assert value["execution_identity"]["skill"] == resolve_skill_package(
        repo, ".agents/skills/task-delivery-runner/SKILL.md"
    )
    for command in value["commands"]:
        log = repo / command["log_path"]
        assert log.is_file()
        assert command["log_path"].startswith(".agents/validation.local/")


@pytest.mark.parametrize(
    "phase,name",
    [
        ("delivery", "task-delivery-runner"),
        ("review", "task-pr-review-runner"),
        ("closeout", "task-closeout"),
        ("feature-audit", "feature-completion-audit"),
    ],
)
def test_formal_validation_records_effective_instruction_package(
    tmp_path: Path, phase: str, name: str
) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = _run(repo, env, "--phase", phase)
    assert result.returncode == 0, result.stderr
    identity = json.loads(result.stdout)["execution_identity"]["skill"]
    assert identity == resolve_skill_package(repo, f".agents/skills/{name}/SKILL.md")


def test_formal_validation_records_actual_claude_adapter_package(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    adapter = ".claude/skills/task-delivery-runner/SKILL.md"

    result = _run(
        repo,
        env,
        "--phase",
        "delivery",
        "--skill-path",
        adapter,
    )

    assert result.returncode == 0, result.stderr
    identity = json.loads(result.stdout)["execution_identity"]["skill"]
    assert identity == resolve_skill_package(repo, adapter)
    assert adapter in identity["inventory"]
    assert ".agents/skills/task-delivery-runner/SKILL.md" in identity["inventory"]
    assert ".claude/settings.json" in identity["inventory"]


def test_formal_validation_rejects_caller_skill_from_another_phase(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = _run(
        repo,
        env,
        "--phase",
        "delivery",
        "--skill-path",
        ".claude/skills/task-pr-review-runner/SKILL.md",
    )

    assert result.returncode != 0
    assert "caller Skill does not match validation phase" in result.stderr


@pytest.mark.parametrize("when", ["before", "during"])
def test_formal_validation_fails_on_instruction_drift(
    tmp_path: Path, when: str
) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if when == "before":
        (
            repo / ".agents/skills/task-delivery-runner/references/remediation.md"
        ).unlink()
    else:
        env["FAKE_INSTRUCTION_MUTATION"] = "1"
    result = _run(repo, env, "--phase", "delivery")
    assert result.returncode != 0
    if when == "before":
        assert "missing instruction file" in result.stderr
        assert not (repo / ".agents/validation.local").exists()
    else:
        payload = json.loads(result.stdout)
        assert payload["status"] == "fail"
        assert "instruction identity changed" in str(payload["limitations"])


def test_progress_is_bounded_stderr_and_does_not_change_final_result(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = _run(repo, env, "--phase", "delivery")

    assert result.returncode == 0, result.stderr
    final_result = json.loads(result.stdout)
    progress = [json.loads(line) for line in result.stderr.splitlines() if line.strip()]
    assert final_result["status"] == "pass"
    assert progress[0] == {
        "authority": "non-authoritative observability only",
        "event": "started",
        "kind": "workflow-progress",
        "operation": "workflow-validation",
        "schema_version": 1,
        "stage": "validation",
    }
    assert progress[-1]["event"] == "completed"
    assert progress[-1]["stage"] == "validation"
    assert all(len(line.encode("utf-8")) < 512 for line in result.stderr.splitlines())
    assert all(
        "commands" not in line
        and "output_dir" not in line
        and "Task Contract" not in line
        for line in result.stderr.splitlines()
    )


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
    env["CODEX_SKILL_VALIDATOR"] = str(tmp_path / "missing_quick_validate.py")
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


def test_base_sha_detects_governance_change_and_runs_skill_validator(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    validator = tmp_path / "quick_validate.py"
    validator.write_text("import sys\nprint('valid', sys.argv[1])\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_CHANGED_FILE"] = ".agents/skills/task-delivery-runner/SKILL.md"
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
    assert any(
        item["command_id"] == "skill-task-delivery-runner" for item in value["commands"]
    )


def test_invalid_base_sha_fails_closed(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = _run(repo, env, "--phase", "review", "--base-sha", "not-a-sha")
    assert result.returncode == 2
    assert "full commit SHA" in result.stderr


def test_machine_absolute_paths_are_redacted_from_summary_and_log(
    tmp_path: Path,
) -> None:
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
