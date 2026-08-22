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
RULES = ROOT / ".codex" / "rules" / "tracequant-wsl-validation.rules"
WORKFLOW_VALIDATION = ROOT / "tools" / "agent_workflow" / "workflow_validation.py"
WORKFLOW_COMMON = ROOT / "tools" / "agent_workflow" / "workflow_common.py"
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)
IDENTITY_RELATIVE_PATHS = (
    "tools/agent_workflow/wsl2_validation_runner.py",
    "tools/agent_workflow/wsl2_validation_profiles.json",
    ".codex/rules/tracequant-wsl-validation.rules",
    "tools/agent_workflow/workflow_validation.py",
    "tools/agent_workflow/workflow_common.py",
)
REMOVED_TRUSTED_ENV_KEYS = (
    "WORKFLOW_TRUSTED_RUNNER_SHA",
    "WORKFLOW_TRUSTED_TOOL_CONTENT_SHA256",
    "WORKFLOW_TRUSTED_BUNDLE_ROOT",
    "WORKFLOW_TARGET_REPO_ROOT",
)


def _clean_test_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in REMOVED_TRUSTED_ENV_KEYS:
        env.pop(key, None)
    env.pop("UV_CACHE_DIR", None)
    return env


def _snapshot_trusted_files(repo: Path) -> None:
    snapshot_root = repo / ".fake-head"
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root)
    for relative_path in IDENTITY_RELATIVE_PATHS:
        source = repo / relative_path
        destination = snapshot_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_runner_repo(tmp_path: Path, *, name: str = "repo") -> Path:
    repo = tmp_path / name
    (repo / "tools" / "agent_workflow").mkdir(parents=True)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".codex" / "rules").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "tools" / "agent_workflow" / SCRIPT.name)
    shutil.copy2(SPEC, repo / "tools" / "agent_workflow" / SPEC.name)
    shutil.copy2(RULES, repo / ".codex" / "rules" / RULES.name)
    shutil.copy2(
        WORKFLOW_VALIDATION,
        repo / "tools" / "agent_workflow" / WORKFLOW_VALIDATION.name,
    )
    shutil.copy2(
        WORKFLOW_COMMON, repo / "tools" / "agent_workflow" / WORKFLOW_COMMON.name
    )
    (repo / ".gitignore").write_text(
        ".agents/validation.local/\n.agents/evidence.local/\n.workflow.local/\n",
        encoding="utf-8",
    )
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 88\n[tool.mypy]\npython_version = '3.11'\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    for skill_dir in (".agents", ".claude"):
        for skill in (
            "task-delivery-runner",
            "task-pr-review-runner",
            "task-closeout",
            "feature-completion-audit",
        ):
            target = repo / skill_dir / "skills" / skill
            target.mkdir(parents=True, exist_ok=True)
            (target / "SKILL.md").write_text(
                f"---\nname: {skill}\n---\n", encoding="utf-8"
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
    _snapshot_trusted_files(repo)
    return repo


def _write_fake_tools(
    tmp_path: Path,
    repo: Path,
    *,
    branch: str = "83-task",
    dirty: bool = False,
    fail_id: str | None = None,
    sleep_id: str | None = None,
    grandchild_marker: Path | None = None,
    cache_marker: Path | None = None,
) -> Path:
    suffix = (
        f"{branch}-{fail_id or 'pass'}-{sleep_id or 'nosleep'}-"
        f"{dirty}-{grandchild_marker.name if grandchild_marker else 'nochild'}-"
        f"{cache_marker.name if cache_marker else 'nocache'}"
    )
    bin_dir = tmp_path / f"bin-{repo.name}-{suffix}"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(
        f"""#!{PYTHON}
from pathlib import Path
import sys
args = sys.argv[1:]
repo = Path({str(repo)!r})
branch = {branch!r}
dirty = {dirty!r}
fail = {fail_id!r}
def snapshot(relative):
    return repo / '.fake-head' / relative
if args == ['rev-parse', '--show-toplevel']:
    print(repo)
elif args == ['branch', '--show-current']:
    print(branch)
elif args == ['rev-parse', 'HEAD']:
    print('a' * 40)
elif args in (
    ['rev-parse', 'refs/remotes/origin/main'],
    ['rev-parse', '--verify', 'refs/remotes/origin/main'],
):
    print('a' * 40)
elif args[:2] == ['status', '--short']:
    print(' M file.txt' if dirty else '')
elif len(args) == 4 and args[:3] == ['diff', '--quiet', '--']:
    relative = args[3]
    current = repo / relative
    expected = snapshot(relative)
    if (
        not expected.exists()
        or not current.exists()
        or current.read_bytes() != expected.read_bytes()
    ):
        sys.exit(1)
elif len(args) == 5 and args[:4] == ['diff', '--cached', '--quiet', '--']:
    relative = args[4]
    current = repo / relative
    expected = snapshot(relative)
    if (
        not expected.exists()
        or not current.exists()
        or current.read_bytes() != expected.read_bytes()
    ):
        sys.exit(1)
elif len(args) == 2 and args[0] == 'show' and args[1].startswith('HEAD:'):
    relative = args[1].split(':', 1)[1]
    expected = snapshot(relative)
    if not expected.exists():
        sys.exit(128)
    sys.stdout.buffer.write(expected.read_bytes())
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
import os
import subprocess
import sys
import time
from pathlib import Path
args = sys.argv[1:]
key = '-'.join(args)
fail = {fail_id!r}
sleep = {sleep_id!r}
marker = {str(grandchild_marker) if grandchild_marker else None!r}
cache_marker = {str(cache_marker) if cache_marker else None!r}
if cache_marker:
    Path(cache_marker).write_text(os.environ.get('UV_CACHE_DIR', ''), encoding='utf-8')
if sleep and sleep in key:
    if marker:
        child_code = (
            "import signal, time; from pathlib import Path; "
            + "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            + "time.sleep(4); Path(" + repr(marker) + ").write_text('survived')"
        )
        subprocess.Popen([{PYTHON!r}, '-c', child_code])
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


def _fake_skill_validator(tmp_path: Path) -> Path:
    validator = tmp_path / "quick_validate.py"
    validator.write_text("import sys\nprint('valid', sys.argv[1])\n", encoding="utf-8")
    return validator


def _run(
    repo: Path,
    bin_dir: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = _clean_test_env()
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


def test_uv_subprocess_uses_repo_local_cache_by_default(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    cache_marker = tmp_path / "uv-cache-env.txt"
    bin_dir = _write_fake_tools(tmp_path, repo, cache_marker=cache_marker)

    result = _run(repo, bin_dir, "targeted")

    assert result.returncode == 0, result.stderr
    assert cache_marker.read_text(encoding="utf-8") == str(
        repo / ".workflow.local" / "uv-cache"
    )


def test_targeted_profile_is_not_ci_equivalent(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    result = _run(repo, bin_dir, "targeted")
    assert result.returncode == 0, result.stderr
    stored = json.loads((repo / json.loads(result.stdout)["result_path"]).read_text())
    assert stored["profile_kind"] == "targeted"
    assert stored["expected_command_count"] == 1


def test_fixed_profile_test_paths_exist() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    missing: list[str] = []
    for profile in spec["profiles"].values():
        for command in profile.get("commands", []):
            for argument in command["argv"]:
                if argument.startswith("tests/") and not (ROOT / argument).exists():
                    missing.append(argument)
    assert missing == []


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
    assert _run(repo, bin_dir, "targeted", "arbitrary-value").returncode == 2
    assert _run(repo, bin_dir, "targeted", "--unknown").returncode == 2
    assert _run(repo, bin_dir, "targeted", "--", "tests/tools").returncode == 2
    assert _run(repo, bin_dir, "targeted", ">").returncode == 2


def test_trailing_arguments_fail_before_validation_side_effects(
    tmp_path: Path,
) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    canary = bin_dir / "validation-command-started"
    for tool in ("uv", "git"):
        tool_path = bin_dir / tool
        original = tool_path.read_text(encoding="utf-8")
        tool_path.write_text(
            original.replace(
                "import sys\n",
                (
                    "import sys\nfrom pathlib import Path\n"
                    f"Path({str(canary)!r}).write_text({tool!r})\n"
                ),
                1,
            ),
            encoding="utf-8",
        )

    for extra in (
        ("tests/tools",),
        ("arbitrary-value",),
        ("--unknown",),
        ("--", "tests/tools"),
        (">",),
        ("$(id)",),
    ):
        result = _run(repo, bin_dir, "targeted", *extra)
        assert result.returncode == 2, f"extra={extra!r}: {result.stderr}"
        assert (
            "trailing arguments are not accepted" in result.stderr
            or "unrecognized arguments" in result.stderr
            or "error: unrecognized" in result.stderr
        )
        assert result.stdout == ""
        assert not canary.exists()
        assert not (repo / ".agents" / "validation.local").exists()


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
    _snapshot_trusted_files(repo)
    marker = tmp_path / "grandchild-survived.txt"
    bin_dir = _write_fake_tools(
        tmp_path, repo, sleep_id="pytest", grandchild_marker=marker
    )
    timeout = _run(repo, bin_dir, "targeted")
    assert timeout.returncode == 1
    assert json.loads(timeout.stdout)["failed_command"]["timed_out"]
    time.sleep(4.5)
    assert not marker.exists(), "timed-out grandchild survived process-group kill"

    proc_env = _clean_test_env()
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
    env = _clean_test_env()
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


def test_current_content_is_hashed_and_canonical_drift_fails_closed(
    tmp_path: Path,
) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    first = _run(repo, bin_dir, "targeted")
    assert first.returncode == 0
    stored = json.loads((repo / json.loads(first.stdout)["result_path"]).read_text())
    assert stored["integrity"]["verification"] == "current-worktree-content"
    original_hash = stored["integrity"]["runner_sha256"]

    runner_path = repo / "tools/agent_workflow" / SCRIPT.name
    runner_path.write_text(
        runner_path.read_text(encoding="utf-8") + "\n# current change\n",
        encoding="utf-8",
    )
    changed_runner = _run(repo, bin_dir, "targeted")
    assert changed_runner.returncode == 0, changed_runner.stderr
    changed_stored = json.loads(
        (repo / json.loads(changed_runner.stdout)["result_path"]).read_text()
    )
    assert changed_stored["integrity"]["runner_sha256"] != original_hash

    spec_path = repo / "tools/agent_workflow" / SPEC.name
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["profiles"]["targeted"]["commands"][0]["argv"] = [
        "python3",
        "-c",
        "print('not canonical')",
    ]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    bad_spec = _run(repo, bin_dir, "targeted")
    assert bad_spec.returncode == 2
    assert "does not match canonical command" in bad_spec.stderr

    shutil.copy2(SPEC, spec_path)
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace("pytest", "pytest -q")
    )
    drift = _run(repo, bin_dir, "targeted")
    assert drift.returncode == 2
    assert "drifted" in drift.stderr


def test_invalid_or_duplicate_command_ids_fail_before_execution(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    spec_path = repo / "tools/agent_workflow" / SPEC.name

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["profiles"]["targeted"]["commands"][0]["id"] = "../../escape"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    _snapshot_trusted_files(repo)
    invalid = _run(repo, bin_dir, "targeted")
    assert invalid.returncode == 2
    assert "invalid profile command id" in invalid.stderr

    shutil.copy2(SPEC, spec_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    command = spec["profiles"]["targeted"]["commands"][0]
    spec["profiles"]["targeted"]["commands"] = [command, command]
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    _snapshot_trusted_files(repo)
    duplicate = _run(repo, bin_dir, "targeted")
    assert duplicate.returncode == 2
    assert "duplicate profile command id" in duplicate.stderr


def test_result_write_failure_does_not_report_success(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    blocked = repo / ".agents" / "validation.local"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("not a directory", encoding="utf-8")
    result = _run(repo, bin_dir, "targeted")
    assert result.returncode == 2
    assert result.stdout == ""


def test_workflow_profiles_require_base_sha_and_run_one_bounded_command(
    tmp_path: Path,
) -> None:
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    validator = _fake_skill_validator(tmp_path)
    base_sha = "b" * 40
    for profile in ("workflow-delivery", "workflow-review"):
        missing = _run(repo, bin_dir, profile)
        assert missing.returncode == 2
        result = _run(
            repo,
            bin_dir,
            profile,
            "--base-sha",
            base_sha,
            extra_env={"CODEX_SKILL_VALIDATOR": str(validator)},
        )
        assert result.returncode == 0, result.stderr
        digest = json.loads(result.stdout)
        assert digest["profile"] == profile
        assert digest["command_count"] == 1
        stored = json.loads((repo / digest["result_path"]).read_text())
        assert stored["commands"][0]["id"] == "workflow-validation"
        assert stored["commands"][0]["exit_code"] == 0


def test_workflow_closeout_requires_clean_synchronized_main(tmp_path: Path) -> None:
    repo = _copy_runner_repo(tmp_path)
    validator = _fake_skill_validator(tmp_path)
    base_sha = "b" * 40
    off_main = _write_fake_tools(tmp_path, repo, branch="feature")
    result = _run(
        repo,
        off_main,
        "workflow-closeout",
        "--base-sha",
        base_sha,
        extra_env={"CODEX_SKILL_VALIDATOR": str(validator)},
    )
    assert result.returncode == 2
    main = _write_fake_tools(tmp_path, repo, branch="main")
    result = _run(
        repo,
        main,
        "workflow-closeout",
        "--base-sha",
        base_sha,
        extra_env={"CODEX_SKILL_VALIDATOR": str(validator)},
    )
    assert result.returncode == 0, result.stderr


def test_rules_file_contains_positive_and_negative_boundaries() -> None:
    text = RULES.read_text(encoding="utf-8")
    for allowed in (
        "current-ci-equivalent",
        "targeted",
        "post-merge",
        "workflow-delivery",
        "workflow-review",
        "workflow-closeout",
    ):
        assert allowed in text
    for boundary in (
        "generic Python, uv, shell wrappers",
        "--command",
        "--shell",
        "gh",
        "auth",
        "token",
        "push",
        "reset",
        "clean",
    ):
        assert boundary in text


def test_workflow_delivery_and_review_require_clean_committed_head(
    tmp_path: Path,
) -> None:
    repo = _copy_runner_repo(tmp_path)
    validator = _fake_skill_validator(tmp_path)
    dirty_tools = _write_fake_tools(tmp_path, repo, dirty=True)
    for profile in ("workflow-delivery", "workflow-review"):
        result = _run(
            repo,
            dirty_tools,
            profile,
            "--base-sha",
            "b" * 40,
            extra_env={"CODEX_SKILL_VALIDATOR": str(validator)},
        )
        assert result.returncode == 2
        assert "clean working tree" in result.stderr


def test_validation_runner_has_no_removed_trusted_version_interface() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for fragment in (
        "WORKFLOW_TRUSTED_RUNNER_SHA",
        "WORKFLOW_TRUSTED_BUNDLE_ROOT",
        "WORKFLOW_TARGET_REPO_ROOT",
        "trusted-commit-bundle-pre-execution",
    ):
        assert fragment not in text


# --- Skill identity tests ---


def test_workflow_review_records_default_agents_skill_path(tmp_path: Path) -> None:
    """Without --skill-path the validation runner falls back to .agents path."""
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    validator = _fake_skill_validator(tmp_path)
    result = _run(
        repo,
        bin_dir,
        "workflow-review",
        "--base-sha",
        "b" * 40,
        extra_env={"CODEX_SKILL_VALIDATOR": str(validator)},
    )
    assert result.returncode == 0, result.stderr
    stored = json.loads((repo / json.loads(result.stdout)["result_path"]).read_text())
    assert stored["integrity"]["skill"]["path"] == (
        ".agents/skills/task-pr-review-runner/SKILL.md"
    )
    assert stored["integrity"]["skill"]["sha256"]


def test_workflow_review_records_claude_skill_path_when_provided(
    tmp_path: Path,
) -> None:
    """--skill-path .claude/... is recorded with actual content hash."""
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    validator = _fake_skill_validator(tmp_path)
    claude_skill = ".claude/skills/task-pr-review-runner/SKILL.md"
    result = _run(
        repo,
        bin_dir,
        "workflow-review",
        "--base-sha",
        "b" * 40,
        "--skill-path",
        claude_skill,
        extra_env={"CODEX_SKILL_VALIDATOR": str(validator)},
    )
    assert result.returncode == 0, result.stderr
    stored = json.loads((repo / json.loads(result.stdout)["result_path"]).read_text())
    assert stored["integrity"]["skill"]["path"] == claude_skill
    import hashlib

    expected_hash = hashlib.sha256((repo / claude_skill).read_bytes()).hexdigest()
    assert stored["integrity"]["skill"]["sha256"] == expected_hash


def test_validation_skill_path_outside_allowed_roots_fails(tmp_path: Path) -> None:
    """--skill-path outside .agents/skills/ or .claude/skills/ is rejected."""
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    result = _run(
        repo,
        bin_dir,
        "workflow-review",
        "--base-sha",
        "b" * 40,
        "--skill-path",
        "tools/agent_workflow/README.md",
    )
    assert result.returncode == 2


def test_validation_skill_path_parent_traversal_fails(tmp_path: Path) -> None:
    """--skill-path with .. is rejected."""
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    result = _run(
        repo,
        bin_dir,
        "workflow-review",
        "--base-sha",
        "b" * 40,
        "--skill-path",
        ".claude/skills/../../dangerous/SKILL.md",
    )
    assert result.returncode == 2


def test_workflow_delivery_agents_skill_is_default(tmp_path: Path) -> None:
    """workflow-delivery defaults to .agents task-delivery-runner path."""
    repo = _copy_runner_repo(tmp_path)
    bin_dir = _write_fake_tools(tmp_path, repo)
    validator = _fake_skill_validator(tmp_path)
    result = _run(
        repo,
        bin_dir,
        "workflow-delivery",
        "--base-sha",
        "b" * 40,
        extra_env={"CODEX_SKILL_VALIDATOR": str(validator)},
    )
    assert result.returncode == 0, result.stderr
    stored = json.loads((repo / json.loads(result.stdout)["result_path"]).read_text())
    assert ".agents/skills/task-delivery-runner/SKILL.md" in str(
        stored["integrity"]["skill"]["path"]
    )


def test_workflow_closeout_agents_skill_is_default(tmp_path: Path) -> None:
    """workflow-closeout defaults to .agents task-closeout path."""
    repo = _copy_runner_repo(tmp_path)
    validator = _fake_skill_validator(tmp_path)
    main = _write_fake_tools(tmp_path, repo, branch="main")
    result = _run(
        repo,
        main,
        "workflow-closeout",
        "--base-sha",
        "b" * 40,
        extra_env={"CODEX_SKILL_VALIDATOR": str(validator)},
    )
    assert result.returncode == 0, result.stderr
    stored = json.loads((repo / json.loads(result.stdout)["result_path"]).read_text())
    assert ".agents/skills/task-closeout/SKILL.md" in str(
        stored["integrity"]["skill"]["path"]
    )
