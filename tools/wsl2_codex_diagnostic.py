#!/usr/bin/env python3
"""Repeatable, bounded diagnostics for the supported WSL2 Codex environment.

The default profile is local-only. Remote reads, formal-repository writes, and
GitHub write probes require explicit flags. The script writes only to an
explicit output directory or to the Git-ignored local evidence root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

MAX_STREAM_CHARS = 4_000
PROXY_KEYS = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
}
TOKEN_PATTERNS = (
    re.compile(r"(?i)\b(?:gh[opsu]_|github_pat_)[A-Za-z0-9_]+"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?\S+"),
    re.compile(r"(?i)(token\s*[:=]\s*)\S+"),
)


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    argv: list[str]
    cwd: str
    exit_code: int | None
    duration_seconds: float
    status: str
    stdout_summary: str
    stderr_summary: str
    error: str | None = None


@dataclass(frozen=True)
class Capability:
    capability: str
    status: str
    evidence: list[str]
    approval_observation: str
    notes: str


class DiagnosticError(RuntimeError):
    """Raised when a requested destructive probe cannot be safely completed."""


def utc_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def bounded(value: str, limit: int = MAX_STREAM_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n<truncated>"


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if not parts.scheme or not parts.netloc:
        return value
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port is not None else ""
    return urlunsplit((parts.scheme, f"{host}{port}", parts.path, "", ""))


def redact_text(value: str, *, home: Path, repo_root: Path) -> str:
    text = value.replace(str(repo_root), "<repo>").replace(str(home), "$HOME")
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub(r"\1<redacted>" if pattern.groups else "<redacted>", text)
    for key in PROXY_KEYS:
        proxy_value = os.environ.get(key)
        if proxy_value:
            text = text.replace(proxy_value, redact_url(proxy_value))
    text = re.sub(
        r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@",
        r"\1<redacted>@",
        text,
    )
    return text


def display_argv(argv: Sequence[str], *, home: Path, repo_root: Path) -> list[str]:
    return [redact_text(item, home=home, repo_root=repo_root) for item in argv]


class Recorder:
    def __init__(self, repo_root: Path, output_dir: Path) -> None:
        self.repo_root = repo_root
        self.output_dir = output_dir
        self.home = Path.home()
        self.commands: list[CommandResult] = []

    def run(
        self,
        command_id: str,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 120,
    ) -> CommandResult:
        actual_cwd = cwd or self.repo_root
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(argv),
                cwd=actual_cwd,
                env=dict(env) if env is not None else None,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            duration = time.monotonic() - started
            stdout = redact_text(completed.stdout, home=self.home, repo_root=self.repo_root)
            stderr = redact_text(completed.stderr, home=self.home, repo_root=self.repo_root)
            result = CommandResult(
                command_id=command_id,
                argv=display_argv(argv, home=self.home, repo_root=self.repo_root),
                cwd=redact_text(str(actual_cwd), home=self.home, repo_root=self.repo_root),
                exit_code=completed.returncode,
                duration_seconds=round(duration, 3),
                status="pass" if completed.returncode == 0 else "fail",
                stdout_summary=bounded(stdout.strip()),
                stderr_summary=bounded(stderr.strip()),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            duration = time.monotonic() - started
            result = CommandResult(
                command_id=command_id,
                argv=display_argv(argv, home=self.home, repo_root=self.repo_root),
                cwd=redact_text(str(actual_cwd), home=self.home, repo_root=self.repo_root),
                exit_code=None,
                duration_seconds=round(duration, 3),
                status="error",
                stdout_summary="",
                stderr_summary="",
                error=redact_text(str(exc), home=self.home, repo_root=self.repo_root),
            )
        self.commands.append(result)
        return result

    def record_internal(
        self,
        command_id: str,
        *,
        status: str,
        stdout: str = "",
        stderr: str = "",
        error: str | None = None,
    ) -> CommandResult:
        result = CommandResult(
            command_id=command_id,
            argv=["<internal>"],
            cwd="<repo>",
            exit_code=0 if status == "pass" else 1,
            duration_seconds=0.0,
            status=status,
            stdout_summary=bounded(
                redact_text(stdout, home=self.home, repo_root=self.repo_root)
            ),
            stderr_summary=bounded(
                redact_text(stderr, home=self.home, repo_root=self.repo_root)
            ),
            error=error,
        )
        self.commands.append(result)
        return result


def command_exists(name: str) -> str | None:
    return shutil.which(name)


def parse_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def filesystem_type(path: Path, recorder: Recorder) -> str | None:
    result = recorder.run("filesystem-type", ["stat", "-f", "-c", "%T", str(path)])
    if result.status != "pass":
        return None
    return result.stdout_summary.strip() or None


def git_value(recorder: Recorder, command_id: str, *args: str) -> str | None:
    result = recorder.run(command_id, ["git", *args])
    if result.status != "pass":
        return None
    return result.stdout_summary.strip() or None


def proxy_summary() -> dict[str, str]:
    summary: dict[str, str] = {}
    for key in sorted(PROXY_KEYS):
        value = os.environ.get(key)
        if value:
            summary[key] = redact_url(value)
    return summary


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, values: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def ensure_ignored(repo_root: Path, relative_path: Path, recorder: Recorder) -> bool:
    result = recorder.run(
        "evidence-ignore-check",
        ["git", "check-ignore", "-q", str(relative_path)],
        cwd=repo_root,
    )
    return result.status == "pass"


def workspace_probe(output_dir: Path, recorder: Recorder) -> Capability:
    probe_dir = output_dir / "workspace-probe"
    original = probe_dir / "probe.txt"
    renamed = probe_dir / "probe-renamed.txt"
    evidence: list[str] = []
    try:
        probe_dir.mkdir(parents=True, exist_ok=False)
        original.write_text("created\n", encoding="utf-8")
        evidence.append("create")
        content = original.read_text(encoding="utf-8")
        if content != "created\n":
            raise DiagnosticError("workspace probe read-back mismatch")
        evidence.append("read")
        original.write_text("modified\n", encoding="utf-8")
        evidence.append("modify")
        original.rename(renamed)
        evidence.append("rename")
        renamed.unlink()
        probe_dir.rmdir()
        evidence.append("delete")
        recorder.record_internal(
            "workspace-probe",
            status="pass",
            stdout=", ".join(evidence),
        )
        return Capability(
            capability="workspace-file-operations",
            status="pass",
            evidence=["workspace-probe"],
            approval_observation="not-observable-inside-script",
            notes="Probe completed under the selected evidence directory.",
        )
    except (OSError, DiagnosticError) as exc:
        recorder.record_internal(
            "workspace-probe",
            status="fail",
            stderr=str(exc),
        )
        shutil.rmtree(probe_dir, ignore_errors=True)
        return Capability(
            capability="workspace-file-operations",
            status="fail",
            evidence=["workspace-probe"],
            approval_observation="not-observable-inside-script",
            notes=str(exc),
        )


def temporary_git_probe(recorder: Recorder) -> Capability:
    evidence: list[str] = []
    with tempfile.TemporaryDirectory(prefix="quant-system-wsl2-git-") as directory:
        path = Path(directory)
        commands = [
            ("temp-git-init", ["git", "init", "-b", "main"]),
            ("temp-git-config-name", ["git", "config", "user.name", "WSL2 Diagnostic"]),
            (
                "temp-git-config-email",
                ["git", "config", "user.email", "diagnostic.invalid@example.invalid"],
            ),
        ]
        for command_id, argv in commands:
            result = recorder.run(command_id, argv, cwd=path)
            evidence.append(command_id)
            if result.status != "pass":
                return Capability(
                    capability="temporary-git-write",
                    status="fail",
                    evidence=evidence,
                    approval_observation="not-observable-inside-script",
                    notes=f"Failed at {command_id}.",
                )
        (path / "probe.txt").write_text("probe\n", encoding="utf-8")
        steps = [
            ("temp-git-add", ["git", "add", "probe.txt"]),
            ("temp-git-commit", ["git", "commit", "-m", "diagnostic commit"]),
            ("temp-git-switch-create", ["git", "switch", "-c", "diagnostic-branch"]),
            ("temp-git-switch-main", ["git", "switch", "main"]),
            ("temp-git-log", ["git", "log", "--oneline", "-1"]),
        ]
        for command_id, argv in steps:
            result = recorder.run(command_id, argv, cwd=path)
            evidence.append(command_id)
            if result.status != "pass":
                return Capability(
                    capability="temporary-git-write",
                    status="fail",
                    evidence=evidence,
                    approval_observation="not-observable-inside-script",
                    notes=f"Failed at {command_id}.",
                )
    return Capability(
        capability="temporary-git-write",
        status="pass",
        evidence=evidence,
        approval_observation="not-observable-inside-script",
        notes="Temporary repository was removed after the probe.",
    )


def project_validation(recorder: Recorder) -> tuple[Capability, list[dict[str, Any]]]:
    commands: list[tuple[str, list[str]]] = [
        ("validation-lock", ["uv", "lock", "--check"]),
        ("validation-pytest", ["uv", "run", "--frozen", "pytest"]),
        ("validation-ruff-check", ["uv", "run", "--frozen", "ruff", "check", "."]),
        (
            "validation-ruff-format",
            ["uv", "run", "--frozen", "ruff", "format", "--check", "."],
        ),
        ("validation-mypy", ["uv", "run", "--frozen", "mypy", "src", "tests"]),
        ("validation-diff-check", ["git", "diff", "--check"]),
    ]
    results: list[dict[str, Any]] = []
    for command_id, argv in commands:
        result = recorder.run(command_id, argv, timeout=600)
        results.append(asdict(result))
    passed = all(item["status"] == "pass" for item in results)
    return (
        Capability(
            capability="project-validation",
            status="pass" if passed else "fail",
            evidence=[command_id for command_id, _ in commands],
            approval_observation="not-observable-inside-script",
            notes="Current CI-equivalent command set.",
        ),
        results,
    )


def remote_read_probe(
    recorder: Recorder,
    *,
    github_repo: str | None,
    formal_fetch: bool,
) -> list[Capability]:
    capabilities: list[Capability] = []
    network_commands = [
        ("dns-github", ["getent", "hosts", "github.com"]),
        ("dns-api-github", ["getent", "hosts", "api.github.com"]),
        ("tls-github", ["curl", "-I", "--max-time", "15", "https://github.com"]),
        (
            "tls-api-github",
            ["curl", "-I", "--max-time", "15", "https://api.github.com"],
        ),
    ]
    for command_id, argv in network_commands:
        recorder.run(command_id, argv, timeout=30)
    network_pass = all(
        next(item for item in recorder.commands if item.command_id == command_id).status
        == "pass"
        for command_id, _ in network_commands
    )
    capabilities.append(
        Capability(
            capability="network-dns-tls",
            status="pass" if network_pass else "partial",
            evidence=[command_id for command_id, _ in network_commands],
            approval_observation="not-observable-inside-script",
            notes="Proxy-enabled process environment, when configured.",
        )
    )

    if command_exists("gh") is None:
        capabilities.append(
            Capability(
                capability="github-read",
                status="not-available",
                evidence=[],
                approval_observation="not-observable-inside-script",
                notes="gh is not installed or not on PATH.",
            )
        )
    else:
        gh_commands: list[tuple[str, list[str]]] = [
            ("gh-auth-status", ["gh", "auth", "status"]),
            (
                "gh-repo-view",
                ["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef,url"],
            ),
        ]
        if github_repo:
            gh_commands.extend(
                [
                    (
                        "gh-issue-list",
                        [
                            "gh",
                            "issue",
                            "list",
                            "--repo",
                            github_repo,
                            "--limit",
                            "5",
                            "--json",
                            "number,title,state",
                        ],
                    ),
                    (
                        "gh-pr-list",
                        [
                            "gh",
                            "pr",
                            "list",
                            "--repo",
                            github_repo,
                            "--limit",
                            "5",
                            "--json",
                            "number,title,state,isDraft",
                        ],
                    ),
                ]
            )
        gh_results = [recorder.run(command_id, argv) for command_id, argv in gh_commands]
        capabilities.append(
            Capability(
                capability="github-read",
                status="pass" if all(result.status == "pass" for result in gh_results) else "fail",
                evidence=[command_id for command_id, _ in gh_commands],
                approval_observation="not-observable-inside-script",
                notes="No GitHub write is performed by this probe.",
            )
        )

    if formal_fetch:
        dry_run = recorder.run(
            "formal-fetch-dry-run", ["git", "fetch", "--dry-run", "origin"], timeout=120
        )
        actual = recorder.run(
            "formal-fetch", ["git", "fetch", "origin", "--prune"], timeout=120
        )
        capabilities.append(
            Capability(
                capability="formal-repository-fetch",
                status="pass" if dry_run.status == actual.status == "pass" else "fail",
                evidence=["formal-fetch-dry-run", "formal-fetch"],
                approval_observation="not-observable-inside-script",
                notes="The non-dry-run command updates FETCH_HEAD and remote refs.",
            )
        )
    return capabilities


def proxy_comparison(recorder: Recorder) -> Capability:
    stripped_env = os.environ.copy()
    for key in PROXY_KEYS:
        if key.lower() != "no_proxy":
            stripped_env.pop(key, None)
    commands = [
        (
            "no-proxy-github",
            ["curl", "-I", "--max-time", "15", "https://github.com"],
        ),
        (
            "no-proxy-api-github",
            ["curl", "-I", "--max-time", "15", "https://api.github.com"],
        ),
        (
            "no-proxy-git-remote",
            ["git", "ls-remote", "--heads", "origin", "main"],
        ),
    ]
    results = [
        recorder.run(command_id, argv, env=stripped_env, timeout=30)
        for command_id, argv in commands
    ]
    passed = sum(result.status == "pass" for result in results)
    status = "pass" if passed == len(results) else "partial" if passed else "fail"
    return Capability(
        capability="temporary-no-proxy-network",
        status=status,
        evidence=[command_id for command_id, _ in commands],
        approval_observation="not-observable-inside-script",
        notes="Only child processes have proxy variables removed; no persistent config changes.",
    )


def formal_write_probe(
    recorder: Recorder,
    *,
    repo_root: Path,
    confirmation: str | None,
) -> Capability:
    required = "DELETE_LOCAL_PROBE"
    if confirmation != required:
        raise DiagnosticError(
            f"formal write probe requires --confirm-formal-write-probe {required}"
        )
    probe_id = f"wsl2-write-probe-{utc_run_id()}"
    probe_branch = f"diagnostic/{probe_id}"
    second_branch = f"diagnostic/{probe_id}-switch"
    probe_dir = Path(tempfile.gettempdir()) / f"quant-system-{probe_id}"
    evidence: list[str] = []
    cleanup_errors: list[str] = []
    try:
        add = recorder.run(
            "formal-worktree-add",
            ["git", "worktree", "add", "-b", probe_branch, str(probe_dir), "HEAD"],
            cwd=repo_root,
        )
        evidence.append("formal-worktree-add")
        if add.status != "pass":
            return Capability(
                capability="formal-repository-write",
                status="fail",
                evidence=evidence,
                approval_observation="not-observable-inside-script",
                notes="Disposable worktree creation failed.",
            )
        (probe_dir / "wsl2-diagnostic-probe.txt").write_text(
            "temporary diagnostic content\n", encoding="utf-8"
        )
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "WSL2 Diagnostic",
                "GIT_AUTHOR_EMAIL": "diagnostic.invalid@example.invalid",
                "GIT_COMMITTER_NAME": "WSL2 Diagnostic",
                "GIT_COMMITTER_EMAIL": "diagnostic.invalid@example.invalid",
            }
        )
        steps = [
            ("formal-git-add", ["git", "add", "wsl2-diagnostic-probe.txt"]),
            (
                "formal-git-commit",
                ["git", "commit", "-m", "Diagnostic: verify WSL2 repository writes"],
            ),
            ("formal-git-switch-create", ["git", "switch", "-c", second_branch]),
            ("formal-git-switch-back", ["git", "switch", probe_branch]),
        ]
        for command_id, argv in steps:
            result = recorder.run(command_id, argv, cwd=probe_dir, env=env)
            evidence.append(command_id)
            if result.status != "pass":
                return Capability(
                    capability="formal-repository-write",
                    status="fail",
                    evidence=evidence,
                    approval_observation="not-observable-inside-script",
                    notes=f"Failed at {command_id}; cleanup was still attempted.",
                )
        return Capability(
            capability="formal-repository-write",
            status="pass",
            evidence=evidence,
            approval_observation="not-observable-inside-script",
            notes="Disposable worktree and diagnostic branches are deleted in cleanup.",
        )
    finally:
        if probe_dir.exists():
            remove = recorder.run(
                "formal-worktree-remove",
                ["git", "worktree", "remove", str(probe_dir)],
                cwd=repo_root,
            )
            if remove.status != "pass":
                cleanup_errors.append("worktree")
        for branch in (second_branch, probe_branch):
            result = recorder.run(
                f"formal-branch-delete-{branch.rsplit('/', 1)[-1]}",
                ["git", "branch", "-D", branch],
                cwd=repo_root,
            )
            if result.status != "pass" and "not found" not in result.stderr_summary.lower():
                cleanup_errors.append(branch)
        recorder.run("formal-worktree-prune", ["git", "worktree", "prune"], cwd=repo_root)
        if cleanup_errors:
            raise DiagnosticError(
                "formal write probe cleanup incomplete: " + ", ".join(cleanup_errors)
            )


def github_write_probe(
    recorder: Recorder,
    *,
    github_repo: str | None,
    confirmation: str | None,
) -> Capability:
    required = "DELETE_REMOTE_REF"
    if confirmation != required:
        raise DiagnosticError(
            f"GitHub write probe requires --confirm-github-write-probe {required}"
        )
    if not github_repo:
        raise DiagnosticError("GitHub write probe requires --github-repo OWNER/NAME")
    head = git_value(recorder, "github-write-head", "rev-parse", "HEAD")
    if not head:
        return Capability(
            capability="github-write",
            status="fail",
            evidence=["github-write-head"],
            approval_observation="not-observable-inside-script",
            notes="Unable to resolve HEAD.",
        )
    probe_id = f"wsl2-gh-write-probe-{utc_run_id()}"
    ref = f"refs/heads/diagnostic/{probe_id}"
    api_ref = f"heads/diagnostic/{probe_id}"
    created = False
    deleted = False
    evidence = ["github-write-head"]
    try:
        create = recorder.run(
            "github-write-create-ref",
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{github_repo}/git/refs",
                "-f",
                f"ref={ref}",
                "-f",
                f"sha={head}",
            ],
        )
        evidence.append("github-write-create-ref")
        created = create.status == "pass"
        if not created:
            return Capability(
                capability="github-write",
                status="fail",
                evidence=evidence,
                approval_observation="not-observable-inside-script",
                notes="Temporary remote ref creation failed.",
            )
        read = recorder.run(
            "github-write-read-ref",
            ["gh", "api", f"repos/{github_repo}/git/ref/{api_ref}"],
        )
        evidence.append("github-write-read-ref")
        if read.status != "pass":
            return Capability(
                capability="github-write",
                status="fail",
                evidence=evidence,
                approval_observation="not-observable-inside-script",
                notes="Temporary remote ref could not be read; cleanup was still attempted.",
            )
        return Capability(
            capability="github-write",
            status="pass",
            evidence=evidence + ["github-write-delete-ref", "github-write-verify-deleted"],
            approval_observation="not-observable-inside-script",
            notes="Only a unique temporary branch ref is created and deleted; no PR or Issue is created.",
        )
    finally:
        if created:
            delete = recorder.run(
                "github-write-delete-ref",
                [
                    "gh",
                    "api",
                    "--method",
                    "DELETE",
                    f"repos/{github_repo}/git/refs/{api_ref}",
                ],
            )
            deleted = delete.status == "pass"
            verify = recorder.run(
                "github-write-verify-deleted",
                ["gh", "api", f"repos/{github_repo}/git/ref/{api_ref}"],
            )
            residual = verify.status == "pass"
            if not deleted or residual:
                raise DiagnosticError(f"temporary GitHub ref may remain: {ref}")


def environment_payload(repo_root: Path, recorder: Recorder) -> dict[str, Any]:
    os_release = parse_os_release()
    kernel = platform.release()
    wsl_indicators = {
        "WSL_DISTRO_NAME": os.environ.get("WSL_DISTRO_NAME"),
        "WSL_INTEROP_present": bool(os.environ.get("WSL_INTEROP")),
        "kernel_contains_microsoft": "microsoft" in kernel.lower(),
    }
    tool_paths = {
        name: command_exists(name)
        for name in ("python", "python3", "uv", "git", "gh", "code", "curl")
    }
    versions: dict[str, str | None] = {}
    version_commands = {
        "python3": ["python3", "--version"],
        "uv": ["uv", "--version"],
        "git": ["git", "--version"],
        "gh": ["gh", "--version"],
        "code": ["code", "--version"],
    }
    for name, argv in version_commands.items():
        if tool_paths.get(name):
            result = recorder.run(f"version-{name}", argv)
            versions[name] = (
                result.stdout_summary.splitlines()[0]
                if result.status == "pass" and result.stdout_summary
                else None
            )
        else:
            versions[name] = None
    uv_python: str | None = None
    if tool_paths.get("uv"):
        result = recorder.run("version-uv-python", ["uv", "run", "python", "--version"])
        if result.status == "pass":
            uv_python = result.stdout_summary or result.stderr_summary
    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "kernel": kernel,
        "os_release": os_release,
        "wsl_indicators": wsl_indicators,
        "repository": {
            "path": "<repo>",
            "filesystem_type": filesystem_type(repo_root, recorder),
            "under_windows_mount": str(repo_root).startswith("/mnt/"),
            "branch": git_value(recorder, "git-branch", "branch", "--show-current"),
            "head": git_value(recorder, "git-head", "rev-parse", "HEAD"),
            "status": git_value(
                recorder,
                "git-status",
                "status",
                "--short",
                "--branch",
                "--untracked-files=all",
            ),
        },
        "tools": {
            "paths": {
                key: redact_text(value, home=Path.home(), repo_root=repo_root)
                if value
                else None
                for key, value in tool_paths.items()
            },
            "versions": versions,
            "uv_run_python": uv_python,
        },
        "proxy": proxy_summary(),
        "ssl_default_verify_paths": ssl.get_default_verify_paths()._asdict(),
        "hostname_hash": hashlib.sha256(socket.gethostname().encode()).hexdigest()[:16],
    }


def report_markdown(
    *,
    run_id: str,
    profile: str,
    environment: Mapping[str, Any],
    capabilities: Sequence[Capability],
    command_count: int,
    errors: Sequence[str],
) -> str:
    capability_lines = [
        f"| `{item.capability}` | `{item.status}` | {item.notes} |"
        for item in capabilities
    ]
    error_lines = [f"- {item}" for item in errors] or ["- None"]
    return "\n".join(
        [
            "# WSL2 Codex environment diagnostic",
            "",
            f"- Run ID: `{run_id}`",
            f"- Profile: `{profile}`",
            f"- Captured at: `{environment['captured_at_utc']}`",
            f"- Commands recorded: `{command_count}`",
            "",
            "## Environment",
            "",
            f"- Distribution: `{environment['os_release'].get('PRETTY_NAME', 'unknown')}`",
            f"- Kernel: `{environment['kernel']}`",
            f"- Repository filesystem: `{environment['repository']['filesystem_type']}`",
            f"- Repository under `/mnt`: `{environment['repository']['under_windows_mount']}`",
            "",
            "## Capability matrix",
            "",
            "| Capability | Status | Notes |",
            "| --- | --- | --- |",
            *capability_lines,
            "",
            "Approval/Guardian routing is not observable from inside this process. Merge this",
            "report with the Codex execution log when classifying direct versus approved runs.",
            "",
            "## Errors and limitations",
            "",
            *error_lines,
            "",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--profile",
        choices=("local", "remote-read", "full"),
        default="local",
        help="full adds temporary no-proxy comparison; write probes still require flags",
    )
    parser.add_argument("--github-repo", help="OWNER/NAME for bounded gh reads or write probe")
    parser.add_argument(
        "--skip-project-validation", action="store_true", help="skip uv/pytest/Ruff/mypy"
    )
    parser.add_argument("--skip-workspace-probe", action="store_true")
    parser.add_argument("--skip-temp-git-probe", action="store_true")
    parser.add_argument("--formal-fetch", action="store_true")
    parser.add_argument("--formal-write-probe", action="store_true")
    parser.add_argument("--confirm-formal-write-probe")
    parser.add_argument("--github-write-probe", action="store_true")
    parser.add_argument("--confirm-github-write-probe")
    parser.add_argument("--json", action="store_true", help="print compact result JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    if not (repo_root / ".gitignore").is_file():
        raise SystemExit(f"repository root is invalid: {repo_root}")
    run_id = args.run_id or utc_run_id()
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else repo_root
        / ".agents"
        / "evidence.local"
        / "wsl2-environment-diagnostic"
        / run_id
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    recorder = Recorder(repo_root, output_dir)
    errors: list[str] = []
    capabilities: list[Capability] = []

    relative_output: Path | None = None
    try:
        relative_output = output_dir.relative_to(repo_root)
    except ValueError:
        pass
    if relative_output is not None and not ensure_ignored(repo_root, relative_output, recorder):
        raise SystemExit(f"output directory is not Git ignored: {relative_output}")

    environment = environment_payload(repo_root, recorder)
    if not args.skip_workspace_probe:
        capabilities.append(workspace_probe(output_dir, recorder))
    if not args.skip_temp_git_probe:
        capabilities.append(temporary_git_probe(recorder))

    validation_results: list[dict[str, Any]] = []
    if not args.skip_project_validation:
        capability, validation_results = project_validation(recorder)
        capabilities.append(capability)

    if args.profile in {"remote-read", "full"}:
        capabilities.extend(
            remote_read_probe(
                recorder,
                github_repo=args.github_repo,
                formal_fetch=args.formal_fetch,
            )
        )
    if args.profile == "full":
        capabilities.append(proxy_comparison(recorder))

    try:
        if args.formal_write_probe:
            capabilities.append(
                formal_write_probe(
                    recorder,
                    repo_root=repo_root,
                    confirmation=args.confirm_formal_write_probe,
                )
            )
        if args.github_write_probe:
            capabilities.append(
                github_write_probe(
                    recorder,
                    github_repo=args.github_repo,
                    confirmation=args.confirm_github_write_probe,
                )
            )
    except DiagnosticError as exc:
        errors.append(str(exc))

    environment_path = output_dir / "environment.json"
    commands_path = output_dir / "commands.jsonl"
    capability_path = output_dir / "capability-matrix.json"
    guardian_path = output_dir / "guardian-approval-matrix.json"
    network_path = output_dir / "network-summary.json"
    git_github_path = output_dir / "git-github-summary.json"
    validation_path = output_dir / "validation-summary.json"
    report_path = output_dir / "diagnostic-report.md"

    write_json(environment_path, environment)
    write_jsonl(commands_path, (asdict(item) for item in recorder.commands))
    write_json(capability_path, [asdict(item) for item in capabilities])
    write_json(
        guardian_path,
        {
            "schema_version": 1,
            "observation": "not-observable-inside-script",
            "instruction": (
                "Classify direct, approval-required, elevated-first, and retry behavior "
                "from the Codex execution log using command_id and argv."
            ),
            "commands": [item.command_id for item in recorder.commands],
        },
    )
    network_capabilities = [
        asdict(item)
        for item in capabilities
        if item.capability in {"network-dns-tls", "temporary-no-proxy-network"}
    ]
    write_json(
        network_path,
        {
            "schema_version": 1,
            "proxy": environment["proxy"],
            "capabilities": network_capabilities,
        },
    )
    write_json(
        git_github_path,
        {
            "schema_version": 1,
            "repository": environment["repository"],
            "capabilities": [
                asdict(item)
                for item in capabilities
                if "git" in item.capability
                or "repository" in item.capability
                or "github" in item.capability
            ],
        },
    )
    write_json(
        validation_path,
        {
            "schema_version": 1,
            "commands": validation_results,
            "status": (
                "not-run"
                if args.skip_project_validation
                else (
                    "pass"
                    if validation_results
                    and all(item["status"] == "pass" for item in validation_results)
                    else "fail"
                )
            ),
        },
    )
    report_path.write_text(
        report_markdown(
            run_id=run_id,
            profile=args.profile,
            environment=environment,
            capabilities=capabilities,
            command_count=len(recorder.commands),
            errors=errors,
        ),
        encoding="utf-8",
    )

    result = {
        "status": "pass" if not errors else "partial",
        "run_id": run_id,
        "profile": args.profile,
        "output_dir": redact_text(str(output_dir), home=Path.home(), repo_root=repo_root),
        "capabilities": {item.capability: item.status for item in capabilities},
        "errors": errors,
        "artifacts": {
            path.name: sha256_file(path)
            for path in (
                environment_path,
                commands_path,
                capability_path,
                guardian_path,
                network_path,
                git_github_path,
                validation_path,
                report_path,
            )
        },
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
