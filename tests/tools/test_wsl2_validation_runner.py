from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "tools" / "agent_workflow" / "wsl2_validation_runner.py"
SPEC = ROOT / "tools" / "agent_workflow" / "wsl2_validation_profiles.json"
RULES = ROOT / ".codex" / "rules" / "quant-system-wsl-validation.rules"
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)


def _copy_runner_repo(tmp_path: Path, *, name: str = "repo") -> Path:
    repo = tmp_path / name
    (repo / "tools" / "agent_workflow").mkdir(parents=True)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".codex" / "rules").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "tools" / "agent_workflow" / SCRIPT.name)
    shutil.copy2(SPEC, repo / "tools" / "agent_workflow" / SPEC.name)
    shutil.copy2(RULES, repo / ".codex" / "rules" / RULES.name)
    (repo / ".gitignore").write_text(
        ".agents/validation.local/\n.agents/evidence.local/\n", encoding="utf-8"
    )
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        """name: CI
jobs:
  quality:
    steps:
      - name: Sync locked dependencies
        run: uv sync --locked --dev
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
    return repo


def _write_fake_tools(
    tmp_path: Path,
    repo: Path,
    *,
    branch: str = "83-task",
    dirty: bool = False,
    fail_id: str | None = None,
    sleep_id: str | None = None,
) -> Path:
    suffix = f"{branch}-{fail_id or 'pass'}-{sleep_id or 'nosleep'}-{dirty}"
    bin_dir = tmp_path / f"bin-{repo.name}-{suffix}"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(
        f"""#!{PYTHON}
import sys
args = sys.argv[1:]
repo = {str(repo)!r}
branch = {branch!r}
dirty = {dirty!r}
fail = {fail_id!r}
if args == ['rev-parse', '--show-toplevel']:
    print(repo)
elif args == ['branch', '--show-current']:
    print(branch)
elif args == ['rev-parse', 'HEAD']:
    print('a' * 40)
elif args == ['rev-parse', 'refs/remotes/origin/main']:
    print('a' * 40)
elif args[:2] == ['status', '--short']:
    print(' M file.txt' if dirty else '')
elif args == ['diff', '--check']:
    if fail == 'git-diff-check':
        print('diff failure ' + 'x' * 9000)
        sys.exit(1)
elif args[:2] == ['diff', '--name-only']:
    print('tools/agent_workflow/wsl2_validation_runner.py')
sys.exit(0)
""",
        encoding="utf-8",
    )
    uv = bin_dir / "uv"
    uv.write_text(
        f"""#!{PYTHON}
import sys, time
args = sys.argv[1:]
key = '-'.join(args)
fail = {fail_id!r}
sleep = {sleep_id!r}
if sleep and sleep in key:
    time.sleep(30)
if fail and fail in key:
    print('failure stdout secret=top-secret-token ' + 'x' * 9000)
    print('failure stderr ghp_' + 'a' * 25 + ' y' * 9000, file=sys.stderr)
    sys.exit(1)
if args == ['lock', '--check']:
    print('Resolved 3 packages')
elif args[:3] == ['run', '--frozen', 'pytest']:
    print('================ 7 passed in 0.01s ================')
elif args == ['run', '--frozen', 'ruff', 'check', '.']:
    print('All checks passed!')
elif args == ['run', '--frozen', 'ruff', 'format', '--check', '.']:
    print('8 files already formatted')
elif args == ['run', '--frozen', 'mypy', 'src', 'tests']:
    print('Success: no issues found in 8 source files')
else:
    print('unexpected ' + key)
sys.exit(0)
""",
        encoding="utf-8",
    )
    git.chmod(0o755)
    uv.chmod(0o755)
    env_file = bin_dir / "env.json"
    env_file.write_text(
        json.dumps({"fail_id": fail_id, "sleep_id": sleep_id}), encoding="utf-8"
    )
    return bin_dir


def _run(
    repo: Path,
    bin_dir: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("GITHUB_TOKEN", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [PYTHON, str(repo / "tools" / "agent_workflow" / SCRIPT.name), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_current_ci_equivalent_success_digest_and_logs(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    result = _run(repo, bin_dir, "current-ci-equivalent")
    assert result.returncode == 0, result.stderr
    digest = json.loads(result.stdout)
    assert digest["status"] == "pass"
    assert digest["profile"] == "current-ci-equivalent"
    assert digest["command_count"] == 6
    assert "7 passed" not in result.stdout
    result_path = repo / digest["result_path"]
    stored = json.loads(result_path.read_text(encoding="utf-8"))
    assert stored["commands"][0]["argv"] == ["uv", "lock", "--check"]
    assert stored["commands"][-1]["id"] == "git-diff-check"
    assert stored["integrity"]["rules_sha256"]


def test_targeted_profile_is_not_ci_equivalent(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    result = _run(repo, bin_dir, "targeted")
    assert result.returncode == 0, result.stderr
    stored = json.loads((repo / json.loads(result.stdout)["result_path"]).read_text())
    assert stored["profile_kind"] == "targeted"
    assert stored["expected_command_count"] == 1


def test_post_merge_fails_closed_off_main_and_accepts_clean_main(
    tmp_path: Path,
) -> None:
    repo = _copy_runner_repo(tmp_path)
    off_main = _write_fake_tools(tmp_path, repo, branch="feature")
    assert _run(repo, off_main, "post-merge").returncode == 2
    main = _write_fake_tools(tmp_path, repo, branch="main")
    assert _run(repo, main, "post-merge").returncode == 0


def test_unknown_profile_unknown_argument_and_trailing_argument_fail(
    tmp_path: Path,
) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    assert _run(repo, bin_dir, "unknown").returncode == 2
    assert _run(repo, bin_dir, "targeted", "--command", "pytest").returncode == 2
    assert _run(repo, bin_dir, "targeted", "tests/tools").returncode == 2
    assert _run(repo, bin_dir, "targeted", "--", "tests/tools").returncode == 2


def test_failure_propagates_and_summaries_are_bounded_and_redacted(
    tmp_path: Path,
) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    bin_dir = _write_fake_tools(tmp_path, repo, fail_id="pytest")
    result = _run(repo, bin_dir, "current-ci-equivalent")
    assert result.returncode == 1, result.stderr
    digest = json.loads(result.stdout)
    assert digest["status"] == "fail"
    failed = digest["failed_command"]
    assert failed["id"] == "pytest"
    assert failed["stdout_truncated"]
    assert failed["stderr_truncated"]
    assert "top-secret-token" not in result.stdout
    assert "ghp_" not in result.stdout
    stored = json.loads((repo / digest["result_path"]).read_text())
    assert len(stored["commands"]) == 2


def test_git_diff_failure_propagates(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    bin_dir = _write_fake_tools(tmp_path, repo, fail_id="git-diff-check")
    result = _run(repo, bin_dir, "current-ci-equivalent")
    assert result.returncode == 1
    assert json.loads(result.stdout)["failed_command"]["id"] == "git-diff-check"


def test_timeout_and_sigint_are_explicit(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    spec = json.loads(
        (repo / "tools/agent_workflow/wsl2_validation_profiles.json").read_text()
    )
    spec["profiles"]["targeted"]["timeout_seconds"] = 1
    (repo / "tools/agent_workflow/wsl2_validation_profiles.json").write_text(
        json.dumps(spec), encoding="utf-8"
    )
    bin_dir = _write_fake_tools(tmp_path, repo, sleep_id="pytest")
    timeout = _run(repo, bin_dir, "targeted")
    assert timeout.returncode == 1
    assert json.loads(timeout.stdout)["failed_command"]["timed_out"]

    proc_env = os.environ.copy()
    proc_env["PATH"] = f"{bin_dir}{os.pathsep}{proc_env.get('PATH', '')}"
    proc = subprocess.Popen(
        [PYTHON, str(repo / "tools" / "agent_workflow" / SCRIPT.name), "targeted"],
        cwd=repo,
        env=proc_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    time.sleep(0.5)
    proc.send_signal(signal.SIGINT)
    stdout, _stderr = proc.communicate(timeout=10)
    assert proc.returncode in {1, 130}
    if stdout:
        assert json.loads(stdout)["status"] in {"fail", "interrupted"}


def test_repository_root_cwd_space_and_symlink_checks(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path, name="repo with space")
    bin_dir = _write_fake_tools(tmp_path, repo)
    assert _run(repo, bin_dir, "targeted").returncode == 0

    subdir = repo / "subdir"
    subdir.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    wrong_cwd = subprocess.run(
        [PYTHON, str(repo / "tools" / "agent_workflow" / SCRIPT.name), "targeted"],
        cwd=subdir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert wrong_cwd.returncode == 2

    link = tmp_path / "runner-link.py"
    link.symlink_to(repo / "tools" / "agent_workflow" / SCRIPT.name)
    symlinked = subprocess.run(
        [PYTHON, str(link), "targeted"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert symlinked.returncode == 2


def test_ci_drift_and_integrity_change_are_observable(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    first = _run(repo, bin_dir, "targeted")
    original = json.loads((repo / json.loads(first.stdout)["result_path"]).read_text())
    runner_path = repo / "tools" / "agent_workflow" / SCRIPT.name
    runner_path.write_text(runner_path.read_text(encoding="utf-8") + "\n# changed\n")
    second = _run(repo, bin_dir, "targeted")
    changed = json.loads((repo / json.loads(second.stdout)["result_path"]).read_text())
    assert (
        original["integrity"]["runner_sha256"] != changed["integrity"]["runner_sha256"]
    )

    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace("pytest", "pytest -q")
    )
    drift = _run(repo, bin_dir, "targeted")
    assert drift.returncode == 2
    assert "drifted" in drift.stderr


def test_result_write_failure_does_not_report_success(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    blocked = repo / ".agents" / "validation.local"
    blocked.parent.mkdir(parents=True)
    blocked.write_text("not a directory", encoding="utf-8")
    result = _run(repo, bin_dir, "targeted")
    assert result.returncode == 2
    assert result.stdout == ""


def test_rules_file_contains_positive_and_negative_boundaries() -> None:
    text = RULES.read_text(encoding="utf-8")
    for allowed in (
        "current-ci-equivalent",
        "targeted",
        "post-merge",
    ):
        assert allowed in text
    for blocked in (
        "uv run --frozen pytest",
        "python3 tools/agent_workflow/wsl2_validation_runner.py",
        "--command",
        "--shell",
        "gh auth token",
        "git push",
        "git reset",
        "git clean",
    ):
        assert blocked in text
