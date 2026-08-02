#!/usr/bin/env python3
"""Fixed WSL2 validation runner with bounded, parseable results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1
OUTPUT_ROOT: Final = ".agents/validation.local/wsl2-runs"
SPEC_PATH: Final = "tools/agent_workflow/wsl2_validation_profiles.json"
RUNNER_PATH: Final = "tools/agent_workflow/wsl2_validation_runner.py"
RULES_PATH: Final = ".codex/rules/quant-system-wsl-validation.rules"
STDIO_LIMIT_BYTES: Final = 4096
SENSITIVE_PATTERNS: Final = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(authorization|cookie|api[_-]?key|token|password|secret)\s*[:=]\s*\S+"
    ),
)
ALLOWED_ENV: Final = (
    "PATH",
    "HOME",
    "UV_CACHE_DIR",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "NO_COLOR",
    "CI",
)


class RunnerError(RuntimeError):
    """Expected fail-closed runner error."""


def _json_dumps(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _redact(text: str) -> str:
    value = text.replace("\x00", "")
    for pattern in SENSITIVE_PATTERNS:
        value = pattern.sub("<redacted>", value)
    return value


def _bounded(text: str) -> tuple[str, bool, str]:
    sanitized = _redact(text)
    digest = _sha256_bytes(sanitized.encode("utf-8", errors="replace"))
    payload = sanitized.encode("utf-8", errors="replace")
    if len(payload) <= STDIO_LIMIT_BYTES:
        return sanitized, False, digest
    truncated = payload[:STDIO_LIMIT_BYTES].decode("utf-8", errors="ignore")
    return truncated + "\n<truncated>\n", True, digest


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(temp, path)
    except BaseException:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunnerError(f"required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunnerError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"invalid JSON object in {path}")
    return value


def _run_quiet(
    argv: Sequence[str], repo_root: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in argv],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_command_env(),
    )


def _command_env() -> dict[str, str]:
    env = {
        key: value for key in ALLOWED_ENV if (value := os.environ.get(key)) is not None
    }
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("NO_COLOR", "1")
    return env


def _find_repo_root(script_path: Path) -> Path:
    if script_path.is_symlink():
        raise RunnerError("runner entry must not be invoked through a symlink")
    repo_root = script_path.resolve().parents[2]
    expected = repo_root / RUNNER_PATH
    if script_path.resolve() != expected.resolve():
        raise RunnerError("runner entry path is not the trusted repository entry")
    if Path.cwd().resolve() != repo_root.resolve():
        raise RunnerError("runner must be started from the repository root")
    if repo_root.resolve().as_posix().startswith("/mnt/"):
        raise RunnerError("repository must be on the WSL2 Linux filesystem, not /mnt")
    git_root = _run_quiet(["git", "rev-parse", "--show-toplevel"], repo_root)
    if git_root.returncode != 0:
        raise RunnerError("current directory is not a Git repository")
    if Path(git_root.stdout.strip()).resolve() != repo_root.resolve():
        raise RunnerError("Git repository root does not match runner repository")
    return repo_root


def _git_lines(repo_root: Path, *args: str) -> list[str]:
    result = _run_quiet(["git", *args], repo_root)
    if result.returncode != 0:
        raise RunnerError(
            f"git {' '.join(args)} failed: {_redact(result.stderr).strip()}"
        )
    return [line for line in result.stdout.splitlines() if line]


def _git_value(repo_root: Path, *args: str) -> str:
    lines = _git_lines(repo_root, *args)
    return lines[0] if lines else ""


def _assert_output_root(repo_root: Path) -> Path:
    ignore = repo_root / ".gitignore"
    patterns = {
        line.strip().removeprefix("./")
        for line in ignore.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "!"))
    }
    if ".agents/validation.local/" not in patterns:
        raise RunnerError(".agents/validation.local/ must be exactly Git ignored")
    output_root = repo_root / OUTPUT_ROOT
    output_root.mkdir(parents=True, exist_ok=True)
    output_root.chmod(0o700)
    return output_root


def _ci_run_commands(repo_root: Path, workflow: str) -> list[list[str]]:
    path = repo_root / workflow
    commands: list[list[str]] = []
    current_step = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- name: "):
            current_step = stripped.removeprefix("- name: ").strip()
            continue
        if not stripped.startswith("run: "):
            continue
        command = stripped.removeprefix("run: ").strip()
        if current_step in {
            "Validate lock file",
            "Run tests",
            "Run Ruff lint",
            "Check Ruff formatting",
            "Run mypy",
        }:
            commands.append(command.split())
    return commands


def _verify_drift(repo_root: Path, spec: Mapping[str, Any]) -> None:
    expected = spec.get("ci_validation_commands")
    workflow = spec.get("ci_workflow")
    if not isinstance(expected, list) or not isinstance(workflow, str):
        raise RunnerError("profile spec missing CI drift metadata")
    observed = _ci_run_commands(repo_root, workflow)
    if observed != expected:
        raise RunnerError(
            "CI validation commands drifted from canonical WSL2 profile spec"
        )


def _verify_profile_preconditions(
    repo_root: Path, profile: Mapping[str, Any]
) -> dict[str, Any]:
    branch = _git_value(repo_root, "branch", "--show-current")
    head = _git_value(repo_root, "rev-parse", "HEAD")
    origin_main = _git_value(repo_root, "rev-parse", "refs/remotes/origin/main")
    status = _git_lines(repo_root, "status", "--short", "--untracked-files=all")
    if profile.get("requires_clean_worktree") and status:
        raise RunnerError("profile requires a clean working tree")
    if profile.get("requires_main_branch") and branch != "main":
        raise RunnerError("profile requires branch main")
    if profile.get("requires_origin_main_identity") and (
        branch != "main" or head != origin_main
    ):
        raise RunnerError("profile requires local main to equal origin/main")
    return {
        "branch": branch,
        "head_sha": head,
        "origin_main_sha": origin_main,
        "clean": not status,
    }


def _run_command(
    command: Mapping[str, Any],
    repo_root: Path,
    run_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    command_id = command.get("id")
    argv = command.get("argv")
    if not isinstance(command_id, str) or not isinstance(argv, list):
        raise RunnerError("invalid profile command definition")
    if not all(isinstance(item, str) and item for item in argv):
        raise RunnerError("invalid profile command argv")
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_command_env(),
    )
    timed_out = False
    interrupted = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        stdout, stderr = proc.communicate()
    except KeyboardInterrupt:
        interrupted = True
        proc.send_signal(signal.SIGINT)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    stdout_summary, stdout_truncated, stdout_digest = _bounded(stdout)
    stderr_summary, stderr_truncated, stderr_digest = _bounded(stderr)
    exit_code = 124 if timed_out else 130 if interrupted else int(proc.returncode)
    log_path = run_dir / f"{command_id}.json"
    log_payload = {
        "id": command_id,
        "argv": argv,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "interrupted": interrupted,
        "stdout": _redact(stdout),
        "stderr": _redact(stderr),
    }
    _atomic_write(log_path, _json_dumps(log_payload, pretty=True).encode("utf-8"))
    return {
        "id": command_id,
        "argv": argv,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "stdout_digest": stdout_digest,
        "stderr_digest": stderr_digest,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "timed_out": timed_out,
        "interrupted": interrupted,
        "log_path": log_path.relative_to(repo_root).as_posix(),
        "stdout_summary": stdout_summary if exit_code != 0 else "",
        "stderr_summary": stderr_summary if exit_code != 0 else "",
    }


def _run_profile(profile_name: str) -> int:
    script_path = Path(__file__)
    repo_root = _find_repo_root(script_path)
    spec_path = repo_root / SPEC_PATH
    spec = _load_json(spec_path)
    profiles = spec.get("profiles")
    if not isinstance(profiles, dict):
        raise RunnerError("profile spec does not define profiles")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise RunnerError("unknown profile")
    _verify_drift(repo_root, spec)
    repository_state = _verify_profile_preconditions(repo_root, profile)
    output_root = _assert_output_root(repo_root)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_dir.chmod(0o700)
    started_at = datetime.now(UTC)
    timeout_seconds = int(profile.get("timeout_seconds", 900))
    commands = profile.get("commands")
    if not isinstance(commands, list) or not commands:
        raise RunnerError("profile must define at least one command")
    results: list[dict[str, Any]] = []
    status = "pass"
    try:
        for command in commands:
            result = _run_command(command, repo_root, run_dir, timeout_seconds)
            results.append(result)
            if result["exit_code"] != 0:
                status = "fail"
                break
        if len(results) != len(commands) and status == "pass":
            status = "fail"
    except KeyboardInterrupt:
        status = "interrupted"
    duration_ms = max(0, round((datetime.now(UTC) - started_at).total_seconds() * 1000))
    rules_file = repo_root / RULES_PATH
    result_document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": spec.get("runner_version"),
        "profile": profile_name,
        "profile_kind": profile.get("kind"),
        "status": status,
        "started_at": started_at.isoformat(),
        "duration_ms": duration_ms,
        "repository": {
            "root": ".",
            "state": repository_state,
        },
        "commands": results,
        "expected_command_count": len(commands),
        "artifacts": {
            "run_id": run_id,
            "run_dir": run_dir.relative_to(repo_root).as_posix(),
            "result_json": (run_dir / "result.json").relative_to(repo_root).as_posix(),
        },
        "integrity": {
            "runner_path": RUNNER_PATH,
            "runner_sha256": _sha256_file(repo_root / RUNNER_PATH),
            "profile_spec_path": SPEC_PATH,
            "profile_spec_sha256": _sha256_file(spec_path),
            "rules_path": RULES_PATH,
            "rules_sha256": _sha256_file(rules_file) if rules_file.exists() else None,
        },
    }
    result_path = run_dir / "result.json"
    _atomic_write(
        result_path, _json_dumps(result_document, pretty=True).encode("utf-8")
    )
    digest = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": spec.get("runner_version"),
        "profile": profile_name,
        "status": status,
        "command_count": len(results),
        "expected_command_count": len(commands),
        "duration_ms": duration_ms,
        "result_path": result_document["artifacts"]["result_json"],
        "result_sha256": _sha256_file(result_path),
        "failed_command": next(
            (item for item in results if item["exit_code"] != 0),
            None,
        ),
    }
    print(_json_dumps(digest), end="")
    if status == "pass":
        return 0
    if status == "interrupted":
        return 130
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a fixed WSL2 validation profile.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "profile",
        choices=(
            "current-ci-equivalent",
            "targeted",
            "targeted:tools-tests",
            "targeted:workflow-tests",
            "post-merge",
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        return _run_profile(args.profile)
    except (OSError, RunnerError) as exc:
        print(
            _json_dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "blocked",
                    "error": _redact(str(exc)),
                }
            ),
            end="",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
