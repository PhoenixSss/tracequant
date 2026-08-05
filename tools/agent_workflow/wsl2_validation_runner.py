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
RUNNER_VERSION: Final = "1.2.0"
OUTPUT_ROOT: Final = ".agents/validation.local/wsl2-runs"
SPEC_PATH: Final = "tools/agent_workflow/wsl2_validation_profiles.json"
RUNNER_PATH: Final = "tools/agent_workflow/wsl2_validation_runner.py"
RULES_PATH: Final = ".codex/rules/quant-system-wsl-validation.rules"
CI_WORKFLOW_PATH: Final = ".github/workflows/ci.yml"
WORKFLOW_VALIDATION_PATH: Final = "tools/agent_workflow/workflow_validation.py"
COMMON_TOOL_PATH: Final = "tools/agent_workflow/workflow_common.py"
STDIO_LIMIT_BYTES: Final = 4096
SENSITIVE_PATTERNS: Final = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(authorization|cookie|api[_-]?key|token|password|secret)\s*[:=]\s*\S+"
    ),
)

IDENTITY_PATHS: Final = (
    RUNNER_PATH,
    SPEC_PATH,
    RULES_PATH,
    WORKFLOW_VALIDATION_PATH,
    COMMON_TOOL_PATH,
)
COMMAND_ID_PATTERN: Final = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
CANONICAL_COMMANDS: Final[dict[str, tuple[str, ...]]] = {
    "uv-lock-check": ("uv", "lock", "--check"),
    "pytest": ("uv", "run", "--frozen", "pytest"),
    "ruff-check": ("uv", "run", "--frozen", "ruff", "check", "."),
    "ruff-format-check": (
        "uv",
        "run",
        "--frozen",
        "ruff",
        "format",
        "--check",
        ".",
    ),
    "mypy": ("uv", "run", "--frozen", "mypy", "src", "tests"),
    "git-diff-check": ("git", "diff", "--check"),
    "pytest-tools": ("uv", "run", "--frozen", "pytest", "tests/tools"),
    "pytest-workflow": (
        "uv",
        "run",
        "--frozen",
        "pytest",
        "tests/tools/test_workflow_skills.py",
        "tests/tools/test_skill_variant_provenance.py",
        "tests/tools/test_workflow_validation.py",
        "tests/tools/test_workflow_evidence.py",
        "tests/tools/test_wsl2_validation_runner.py",
        "tests/tools/test_wsl2_validation_rules.py",
        "tests/tools/test_wsl2_github_evidence_runner.py",
        "tests/tools/test_wsl2_github_evidence_rules.py",
    ),
}
CANONICAL_PROFILE_COMMAND_IDS: Final[dict[str, tuple[str, ...]]] = {
    "current-ci-equivalent": (
        "uv-lock-check",
        "pytest",
        "ruff-check",
        "ruff-format-check",
        "mypy",
        "git-diff-check",
    ),
    "targeted": ("pytest-tools",),
    "targeted:tools-tests": ("pytest-tools",),
    "targeted:workflow-tests": ("pytest-workflow",),
    "post-merge": (
        "uv-lock-check",
        "pytest",
        "ruff-check",
        "ruff-format-check",
        "mypy",
        "git-diff-check",
    ),
}
CANONICAL_PROFILE_KINDS: Final[dict[str, str]] = {
    "current-ci-equivalent": "ci-equivalent",
    "targeted": "targeted",
    "targeted:tools-tests": "targeted",
    "targeted:workflow-tests": "targeted",
    "post-merge": "post-merge",
}
CANONICAL_PROFILE_PRECONDITIONS: Final[dict[str, tuple[bool, bool, bool]]] = {
    "current-ci-equivalent": (False, False, False),
    "targeted": (False, False, False),
    "targeted:tools-tests": (False, False, False),
    "targeted:workflow-tests": (False, False, False),
    "post-merge": (True, True, True),
}
CANONICAL_WORKFLOW_PROFILES: Final[dict[str, tuple[str, tuple[bool, bool, bool]]]] = {
    "workflow-delivery": ("delivery", (True, False, False)),
    "workflow-review": ("review", (True, False, False)),
    "workflow-closeout": ("closeout", (True, True, True)),
}
ALL_PROFILES: Final = tuple(CANONICAL_PROFILE_COMMAND_IDS) + tuple(
    CANONICAL_WORKFLOW_PROFILES
)
CANONICAL_CI_COMMANDS: Final[tuple[tuple[str, ...], ...]] = (
    CANONICAL_COMMANDS["uv-lock-check"],
    CANONICAL_COMMANDS["pytest"],
    CANONICAL_COMMANDS["ruff-check"],
    CANONICAL_COMMANDS["ruff-format-check"],
    CANONICAL_COMMANDS["mypy"],
)
PROCESS_TERM_GRACE_SECONDS: Final = 2.0
PROCESS_INTERRUPT_GRACE_SECONDS: Final = 5.0
ALLOWED_ENV: Final = (
    "PATH",
    "HOME",
    "UV_CACHE_DIR",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "NO_COLOR",
    "CI",
    "CODEX_SKILL_VALIDATOR",
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


def _load_json_bytes(payload: bytes, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise RunnerError(f"invalid UTF-8 in {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunnerError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"invalid JSON object in {path}")
    return value


def _read_current_file(repo_root: Path, relative_path: str) -> bytes:
    path = repo_root / relative_path
    if path.is_symlink():
        raise RunnerError(f"workflow file must not be a symlink: {relative_path}")
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise RunnerError(
            f"required workflow file is missing: {relative_path}"
        ) from exc


def _load_inputs(repo_root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    payloads = {
        relative_path: _read_current_file(repo_root, relative_path)
        for relative_path in IDENTITY_PATHS
    }
    spec_path = repo_root / SPEC_PATH
    spec = _load_json_bytes(payloads[SPEC_PATH], spec_path)
    hashes = {path: _sha256_bytes(payload) for path, payload in payloads.items()}
    return spec, hashes


def _skill_identity(repo_root: Path, profile_name: str) -> dict[str, str] | None:
    mapping = {
        "workflow-delivery": ".agents/skills/task-delivery-runner/SKILL.md",
        "workflow-review": ".agents/skills/task-pr-review-runner/SKILL.md",
        "workflow-closeout": ".agents/skills/task-closeout/SKILL.md",
    }
    relative_path = mapping.get(profile_name)
    if relative_path is None:
        return None
    payload = _read_current_file(repo_root, relative_path)
    return {"path": relative_path, "sha256": _sha256_bytes(payload)}


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
        raise RunnerError("runner entry path is not the repository entry")
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
    canonical = [list(argv) for argv in CANONICAL_CI_COMMANDS]
    if not isinstance(expected, list) or not isinstance(workflow, str):
        raise RunnerError("profile spec missing CI drift metadata")
    if workflow != CI_WORKFLOW_PATH:
        raise RunnerError("profile spec CI workflow path is not canonical")
    if expected != canonical:
        raise RunnerError(
            "profile spec CI commands differ from the runner canonical set"
        )
    observed = _ci_run_commands(repo_root, workflow)
    if observed != canonical:
        raise RunnerError(
            "CI validation commands drifted from the canonical WSL2 command set"
        )


def _validate_spec_identity(spec: Mapping[str, Any]) -> None:
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise RunnerError("profile spec schema_version is unsupported")
    if spec.get("runner_version") != RUNNER_VERSION:
        raise RunnerError("profile spec runner_version does not match the runner")
    profiles = spec.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(ALL_PROFILES):
        raise RunnerError("profile spec profile set differs from the canonical set")


def _validated_workflow_profile(
    repo_root: Path,
    profile_name: str,
    profile: Mapping[str, Any],
    base_sha: str | None,
) -> list[dict[str, Any]]:
    canonical = CANONICAL_WORKFLOW_PROFILES.get(profile_name)
    if canonical is None:
        raise RunnerError("workflow profile is not canonical")
    phase, expected_preconditions = canonical
    if base_sha is None or SHA_PATTERN.fullmatch(base_sha) is None:
        raise RunnerError("workflow profile requires a full --base-sha")
    if profile.get("kind") != "workflow-phase":
        raise RunnerError(
            f"profile kind differs from canonical profile: {profile_name}"
        )
    if profile.get("phase") != phase:
        raise RunnerError(
            f"profile phase differs from canonical profile: {profile_name}"
        )
    if profile.get("requires_base_sha") is not True:
        raise RunnerError(f"profile base-SHA contract drift: {profile_name}")
    if profile.get("include_skill_validators") is not True:
        raise RunnerError(f"profile Skill-validator contract drift: {profile_name}")
    if profile.get("require_skill_validator") is not True:
        raise RunnerError(f"profile Skill-validator requirement drift: {profile_name}")
    observed_preconditions = _profile_preconditions(profile)
    if observed_preconditions != expected_preconditions:
        raise RunnerError(
            f"profile preconditions differ from canonical profile: {profile_name}"
        )
    argv = [
        sys.executable,
        str(repo_root / WORKFLOW_VALIDATION_PATH),
        "run",
        "--repo-root",
        ".",
        "--phase",
        phase,
        "--base-sha",
        base_sha,
        "--include-skill-validators",
        "--require-skill-validator",
    ]
    return [{"id": "workflow-validation", "argv": argv}]


def _profile_preconditions(profile: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    values: list[bool] = []
    for key in (
        "requires_clean_worktree",
        "requires_main_branch",
        "requires_origin_main_identity",
    ):
        value = profile.get(key, False)
        if type(value) is not bool:
            raise RunnerError(f"profile precondition must be boolean: {key}")
        values.append(value)
    return tuple(values)  # type: ignore[return-value]


def _validated_commands(
    profile_name: str, profile: Mapping[str, Any]
) -> list[dict[str, Any]]:
    commands = profile.get("commands")
    if not isinstance(commands, list) or not commands:
        raise RunnerError("profile must define at least one command")
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for command in commands:
        if not isinstance(command, dict):
            raise RunnerError("invalid profile command definition")
        command_id = command.get("id")
        argv = command.get("argv")
        if (
            not isinstance(command_id, str)
            or COMMAND_ID_PATTERN.fullmatch(command_id) is None
        ):
            raise RunnerError("invalid profile command id")
        if command_id in seen_ids:
            raise RunnerError(f"duplicate profile command id: {command_id}")
        seen_ids.add(command_id)
        canonical = CANONICAL_COMMANDS.get(command_id)
        if canonical is None:
            raise RunnerError(f"profile command id is not allowed: {command_id}")
        if not isinstance(argv, list) or tuple(argv) != canonical:
            raise RunnerError(
                f"profile command argv does not match canonical command: {command_id}"
            )
        validated.append({"id": command_id, "argv": list(canonical)})
    observed_ids = tuple(item["id"] for item in validated)
    if observed_ids != CANONICAL_PROFILE_COMMAND_IDS[profile_name]:
        raise RunnerError(
            f"profile command sequence differs from canonical profile: {profile_name}"
        )
    if profile.get("kind") != CANONICAL_PROFILE_KINDS[profile_name]:
        raise RunnerError(
            f"profile kind differs from canonical profile: {profile_name}"
        )
    observed_preconditions = _profile_preconditions(profile)
    if observed_preconditions != CANONICAL_PROFILE_PRECONDITIONS[profile_name]:
        raise RunnerError(
            f"profile preconditions differ from canonical profile: {profile_name}"
        )
    return validated


def _verify_profile_preconditions(
    repo_root: Path, profile: Mapping[str, Any]
) -> dict[str, Any]:
    branch = _git_value(repo_root, "branch", "--show-current")
    head = _git_value(repo_root, "rev-parse", "HEAD")
    origin_main_result = _run_quiet(
        ["git", "rev-parse", "--verify", "refs/remotes/origin/main"], repo_root
    )
    origin_main = (
        origin_main_result.stdout.strip() if origin_main_result.returncode == 0 else ""
    )
    status = _git_lines(repo_root, "status", "--short", "--untracked-files=all")
    if profile.get("requires_clean_worktree") and status:
        raise RunnerError("profile requires a clean working tree")
    if profile.get("requires_main_branch") and branch != "main":
        raise RunnerError("profile requires branch main")
    if profile.get("requires_origin_main_identity"):
        if not origin_main:
            raise RunnerError("profile requires refs/remotes/origin/main")
        if branch != "main" or head != origin_main:
            raise RunnerError("profile requires local main to equal origin/main")
    return {
        "branch": branch,
        "head_sha": head,
        "origin_main_sha": origin_main,
        "clean": not status,
    }


def _signal_process_group(proc: subprocess.Popen[str], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return


def _terminate_process_group(
    proc: subprocess.Popen[str],
    *,
    first_signal: int,
    grace_seconds: float,
) -> tuple[str, str]:
    _signal_process_group(proc, first_signal)
    try:
        return proc.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _signal_process_group(proc, signal.SIGKILL)
        try:
            return proc.communicate(timeout=PROCESS_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise RunnerError(
                "failed to terminate validation command process group"
            ) from exc


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
        start_new_session=True,
    )
    timed_out = False
    interrupted = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        stdout, stderr = _terminate_process_group(
            proc,
            first_signal=signal.SIGTERM,
            grace_seconds=PROCESS_TERM_GRACE_SECONDS,
        )
    except KeyboardInterrupt:
        interrupted = True
        stdout, stderr = _terminate_process_group(
            proc,
            first_signal=signal.SIGINT,
            grace_seconds=PROCESS_INTERRUPT_GRACE_SECONDS,
        )
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


def _run_profile(profile_name: str, base_sha: str | None = None) -> int:
    script_path = Path(__file__)
    repo_root = _find_repo_root(script_path)
    spec, content_hashes = _load_inputs(repo_root)
    _validate_spec_identity(spec)
    profiles = spec.get("profiles")
    if not isinstance(profiles, dict):
        raise RunnerError("profile spec does not define profiles")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise RunnerError("unknown profile")
    _verify_drift(repo_root, spec)
    if profile_name in CANONICAL_WORKFLOW_PROFILES:
        commands = _validated_workflow_profile(
            repo_root, profile_name, profile, base_sha
        )
    else:
        if base_sha is not None:
            raise RunnerError("--base-sha is allowed only for workflow profiles")
        commands = _validated_commands(profile_name, profile)
    timeout_value = profile.get("timeout_seconds", 900)
    if type(timeout_value) is not int:
        raise RunnerError("profile timeout_seconds must be an integer")
    timeout_seconds = timeout_value
    if timeout_seconds <= 0 or timeout_seconds > 3600:
        raise RunnerError("profile timeout_seconds is outside the allowed range")
    repository_state = _verify_profile_preconditions(repo_root, profile)
    output_root = _assert_output_root(repo_root)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    run_dir.chmod(0o700)
    started_at = datetime.now(UTC)
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
    result_document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
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
            "verification": "current-worktree-content",
            "repository_head_sha": repository_state["head_sha"],
            "repository_clean": repository_state["clean"],
            "runner_path": RUNNER_PATH,
            "runner_sha256": content_hashes[RUNNER_PATH],
            "profile_spec_path": SPEC_PATH,
            "profile_spec_sha256": content_hashes[SPEC_PATH],
            "rules_path": RULES_PATH,
            "rules_sha256": content_hashes[RULES_PATH],
            "workflow_validation_path": WORKFLOW_VALIDATION_PATH,
            "workflow_validation_sha256": content_hashes[WORKFLOW_VALIDATION_PATH],
            "skill": _skill_identity(repo_root, profile_name),
        },
    }
    result_path = run_dir / "result.json"
    _atomic_write(
        result_path, _json_dumps(result_document, pretty=True).encode("utf-8")
    )
    digest = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
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
    parser.add_argument("profile", choices=ALL_PROFILES)
    parser.add_argument("--base-sha", type=str)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(raw_argv)
        if args.profile in CANONICAL_WORKFLOW_PROFILES:
            if len(raw_argv) != 3 or raw_argv[1] != "--base-sha":
                parser.error(
                    "workflow profiles require exactly: <profile> --base-sha <full-sha>"
                )
            normalized_base = str(args.base_sha or "").casefold()
            if SHA_PATTERN.fullmatch(normalized_base) is None:
                parser.error("--base-sha must be a full 40-character Git SHA")
            return _run_profile(args.profile, normalized_base)
        if len(raw_argv) != 1:
            parser.error(
                "fixed profiles accept exactly one profile argument; "
                "trailing arguments are not accepted"
            )
        return _run_profile(args.profile)
    except KeyboardInterrupt:
        print(
            _json_dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "interrupted",
                    "error": "runner interrupted before completion",
                }
            ),
            end="",
            file=sys.stderr,
        )
        return 130
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
