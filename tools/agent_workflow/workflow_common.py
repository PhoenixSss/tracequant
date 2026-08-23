#!/usr/bin/env python3
"""Shared helpers for local workflow evidence and validation tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

MAX_STRING: Final = 512
MAX_LIST_ITEMS: Final = 50
MAX_STDERR_TAIL: Final = 2000
MAX_LOG_BYTES: Final = 1_000_000
DEFAULT_COMMAND_TIMEOUT_SECONDS: Final = 30.0
NETWORK_COMMAND_TIMEOUT_SECONDS: Final = 60.0
VALIDATION_COMMAND_TIMEOUT_SECONDS: Final = 1200.0
PROCESS_TERM_GRACE_SECONDS: Final = 2.0
RETRY_DELAY_SECONDS: Final = 0.5
TIMEOUT_EXIT_CODE: Final = 124
SHA_PATTERN: Final = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
WINDOWS_ABSOLUTE_PATH: Final = re.compile(r"^[A-Za-z]:[\\/]")
WINDOWS_ABSOLUTE_PATH_IN_TEXT: Final = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"']+"
)
POSIX_ABSOLUTE_PATH_IN_TEXT: Final = re.compile(r"(?<![:/A-Za-z0-9])/(?!/)[^\s\"']+")
SENSITIVE_VALUE_PATTERNS: Final = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(authorization|cookie|api[_-]?key|token|password|secret)\s*[:=]\s*\S+"
    ),
)


class WorkflowToolError(RuntimeError):
    """Expected user-facing workflow tooling error."""


def build_workflow_env(repo_root: Path) -> dict[str, str]:
    """Build the shared environment for Workflow-owned subprocesses.

    Workflow runtime state belongs under the repository, not in a user HOME
    cache that may be unavailable in a sandbox. An explicit caller override
    remains authoritative.
    """
    env = os.environ.copy()
    env.setdefault(
        "UV_CACHE_DIR",
        str(repo_root.resolve() / ".workflow.local" / "uv-cache"),
    )
    return env


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    timeout_seconds: float | None = None


def _command_timeout(
    argv: Sequence[str], *, executable: str, validation: bool
) -> float:
    """Choose a bounded timeout appropriate for the command class."""
    if validation:
        return VALIDATION_COMMAND_TIMEOUT_SECONDS
    if executable in {"gh", "gh.exe", "gh.cmd"}:
        return DEFAULT_COMMAND_TIMEOUT_SECONDS
    if executable in {"git", "git.exe", "git.cmd"} and any(
        item in {"fetch", "ls-remote", "push", "pull", "clone"} for item in argv[1:]
    ):
        return NETWORK_COMMAND_TIMEOUT_SECONDS
    return DEFAULT_COMMAND_TIMEOUT_SECONDS


def _signal_process_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
    """Terminate a bounded command and its descendants when possible."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        else:
            process.send_signal(sig)
    except ProcessLookupError:
        pass


def _run_process(
    resolved_argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
) -> tuple[int, str, str, bool]:
    """Run one command with a timeout and process-group cleanup."""
    process = subprocess.Popen(
        resolved_argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=PROCESS_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_process_group(process, signal.SIGKILL)
            stdout, stderr = process.communicate()
        timeout_message = f"command timed out after {timeout_seconds:g} seconds"
        stderr = f"{stderr.rstrip()}\n{timeout_message}" if stderr else timeout_message
        return TIMEOUT_EXIT_CODE, stdout, stderr, True
    return process.returncode, stdout, stderr, False


class CommandRunner:
    """Run commands without echoing raw output to the caller."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.tool_calls = 0
        self.git_commands = 0
        self.github_queries = 0
        self.validation_commands = 0

    def run(
        self,
        argv: Sequence[str],
        *,
        command_id: str,
        cwd: Path | None = None,
        validation: bool = False,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        retries: int = 0,
    ) -> CommandResult:
        if not argv:
            raise WorkflowToolError("command argv cannot be empty")
        if retries < 0 or retries > 2:
            raise WorkflowToolError("command retries must be between 0 and 2")
        process_env = build_workflow_env(self.repo_root)
        process_env.setdefault("PYTHONUTF8", "1")
        process_env.setdefault("PYTHONIOENCODING", "utf-8")
        if env:
            process_env.update(env)
        executable_path = shutil.which(str(argv[0]), path=process_env.get("PATH"))
        resolved_argv = [
            executable_path or str(argv[0]),
            *[str(arg) for arg in argv[1:]],
        ]
        executable = Path(resolved_argv[0]).name.lower()
        self.tool_calls += 1
        if executable in {"git", "git.exe", "git.cmd"}:
            self.git_commands += 1
        if executable in {"gh", "gh.exe", "gh.cmd"}:
            self.github_queries += 1
        if validation:
            self.validation_commands += 1
        effective_timeout = (
            _command_timeout(
                resolved_argv,
                executable=executable,
                validation=validation,
            )
            if timeout_seconds is None
            else timeout_seconds
        )
        if effective_timeout <= 0:
            raise WorkflowToolError("command timeout must be positive")
        for attempt in range(retries + 1):
            try:
                returncode, stdout, stderr, timed_out = _run_process(
                    resolved_argv,
                    cwd=cwd or self.repo_root,
                    env=process_env,
                    timeout_seconds=effective_timeout,
                )
            except FileNotFoundError:
                return CommandResult(
                    command_id=command_id,
                    argv=tuple(resolved_argv),
                    returncode=127,
                    stdout="",
                    stderr=f"executable not found: {Path(argv[0]).name}",
                )
            if returncode == 0 or returncode == 127 or attempt == retries:
                break
            time.sleep(RETRY_DELAY_SECONDS)
        return CommandResult(
            command_id=command_id,
            argv=tuple(resolved_argv),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            timeout_seconds=effective_timeout if timed_out else None,
        )

    def counters(self) -> dict[str, int]:
        return {
            "tool_calls": self.tool_calls,
            "git_commands": self.git_commands,
            "github_queries": self.github_queries,
            "validation_commands": self.validation_commands,
        }


def json_dumps(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def print_json(value: Mapping[str, Any], *, pretty: bool = False) -> None:
    print(json_dumps(value, pretty=pretty), end="")


def read_json_text(text: str, *, field: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowToolError(f"invalid JSON from {field}: {exc}") from exc


def read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowToolError(f"required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowToolError(f"invalid JSON in {path}: {exc}") from exc


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json_dumps(value, pretty=True), encoding="utf-8", newline="\n")
    os.replace(temp, path)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_PATTERN.fullmatch(value) is not None


def safe_text(value: Any, *, limit: int = MAX_STRING) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", "")
    text = redact_machine_paths(redact_sensitive(text))
    if WINDOWS_ABSOLUTE_PATH.match(text) or (
        text.startswith("/") and not text.startswith("//")
    ):
        return "<absolute-path-redacted>"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 16)] + "…<truncated>"


def redact_sensitive(text: str) -> str:
    result = text
    for pattern in SENSITIVE_VALUE_PATTERNS:
        result = pattern.sub("<redacted>", result)
    return result


def redact_machine_paths(text: str) -> str:
    value = WINDOWS_ABSOLUTE_PATH_IN_TEXT.sub("<absolute-path-redacted>", text)
    return POSIX_ABSOLUTE_PATH_IN_TEXT.sub("<absolute-path-redacted>", value)


def stderr_tail(text: str, *, limit: int = MAX_STDERR_TAIL) -> str:
    value = redact_machine_paths(redact_sensitive(text.strip()))
    if len(value) <= limit:
        return value
    return "<truncated>…" + value[-limit:]


def bounded_list(
    values: Sequence[Any],
    *,
    item_limit: int = MAX_LIST_ITEMS,
    string_limit: int = MAX_STRING,
) -> dict[str, Any]:
    bounded: list[Any] = []
    for item in values[:item_limit]:
        if isinstance(item, str):
            bounded.append(safe_text(item, limit=string_limit))
        elif isinstance(item, Mapping):
            bounded.append(
                {
                    str(key): safe_text(value, limit=string_limit)
                    if isinstance(value, str)
                    else value
                    for key, value in item.items()
                }
            )
        else:
            bounded.append(item)
    return {
        "items": bounded,
        "count": len(values),
        "truncated": len(values) > item_limit,
    }


def normalize_repo_relative(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    root = repo_root.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise WorkflowToolError("path must remain inside repository root") from exc
    return relative.as_posix()


def exact_gitignore_patterns(repo_root: Path) -> set[str]:
    ignore = repo_root / ".gitignore"
    if not ignore.exists():
        return set()
    patterns: set[str] = set()
    for raw in ignore.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.add(line.removeprefix("./"))
    return patterns


def require_exact_ignored_directory(repo_root: Path, relative: str) -> Path:
    normalized = relative.strip().removeprefix("./").rstrip("/") + "/"
    if normalized not in exact_gitignore_patterns(repo_root):
        raise WorkflowToolError(
            f"local output directory is not covered by an exact .gitignore rule: {normalized}"
        )
    path = repo_root / normalized.rstrip("/")
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_repository_slug(remote: str) -> str | None:
    value = remote.strip()
    if not value:
        return None
    value = re.sub(r"^[a-z]+://[^/@]+@", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^[a-z]+://", "", value, flags=re.IGNORECASE)
    value = value.removeprefix("git@")
    value = value.replace(":", "/", 1) if value.startswith("github.com:") else value
    if "github.com/" in value:
        value = value.split("github.com/", 1)[1]
    elif value.startswith("github.com/"):
        value = value[len("github.com/") :]
    value = value.removesuffix(".git").strip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        return value
    return None


def command_warning(result: CommandResult) -> dict[str, Any]:
    warning: dict[str, Any] = {
        "command_id": result.command_id,
        "exit_code": result.returncode,
        "error": stderr_tail(result.stderr or result.stdout),
    }
    if result.timed_out:
        warning["timed_out"] = True
        warning["timeout_seconds"] = result.timeout_seconds
    return warning
