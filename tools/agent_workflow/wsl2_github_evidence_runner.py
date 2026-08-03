#!/usr/bin/env python3
"""Fixed, read-only WSL2 GitHub and Git workflow evidence runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1
RUNNER_VERSION: Final = "1.1.0"
REPOSITORY: Final = "PhoenixSss/quant-system"
OUTPUT_ROOT: Final = ".agents/evidence.local/wsl2-github-runs"
RUNNER_PATH: Final = "tools/agent_workflow/wsl2_github_evidence_runner.py"
SPEC_PATH: Final = "tools/agent_workflow/wsl2_github_evidence_profiles.json"
RULES_PATH: Final = ".codex/rules/quant-system-wsl-evidence.rules"
EVIDENCE_TOOL_PATH: Final = "tools/agent_workflow/workflow_evidence.py"
COMMON_TOOL_PATH: Final = "tools/agent_workflow/workflow_common.py"
TRUSTED_BUNDLE_ROOT_ENV: Final = "WORKFLOW_TRUSTED_BUNDLE_ROOT"
TARGET_REPO_ROOT_ENV: Final = "WORKFLOW_TARGET_REPO_ROOT"
STDIO_LIMIT_BYTES: Final = 8192
SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
SNAPSHOT_ID_PATTERN: Final = re.compile(r"^ev-[0-9a-f]{16}$")
REPOSITORY_PATTERN: Final = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WINDOWS_PATH_PATTERN: Final = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"']+")
POSIX_PATH_PATTERN: Final = re.compile(r"(?<![:/A-Za-z0-9])/(?!/)[^\s\"']+")
SENSITIVE_PATTERNS: Final = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(authorization|cookie|api[_-]?key|token|password|secret)\s*[:=]\s*\S+"
    ),
)
TRUSTED_PATHS: Final = (
    RUNNER_PATH,
    SPEC_PATH,
    RULES_PATH,
    EVIDENCE_TOOL_PATH,
    COMMON_TOOL_PATH,
)
ALLOWED_ENV: Final = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "XDG_CONFIG_HOME",
    "GH_CONFIG_DIR",
    "GH_HOST",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "WORKFLOW_TRUSTED_RUNNER_SHA",
    "WORKFLOW_TRUSTED_TOOL_CONTENT_SHA256",
    TRUSTED_BUNDLE_ROOT_ENV,
    TARGET_REPO_ROOT_ENV,
)
CANONICAL_PROFILES: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "delivery": ("delivery-preflight", ("task", "expected_main_sha")),
    "delivery-readiness": (
        "delivery-readiness",
        ("task", "pr", "expected_base_sha", "expected_head_sha"),
    ),
    "review": (
        "pr-review-snapshot",
        ("task", "pr", "expected_base_sha", "expected_head_sha"),
    ),
    "pre-merge": (
        "pr-review-snapshot",
        ("task", "pr", "expected_base_sha", "expected_head_sha"),
    ),
    "closeout-readonly": (
        "closeout-plan",
        ("task", "pr", "expected_head_sha", "expected_merge_sha"),
    ),
    "recheck": ("recheck", ("snapshot_id",)),
}
RECHECK_COMMANDS: Final = {
    "delivery-readiness": "pr-review-recheck",
    "pr-review-snapshot": "pr-review-recheck",
    "closeout-plan": "closeout-final",
}


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


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _redact(text: str) -> str:
    value = text.replace("\x00", "")
    for pattern in SENSITIVE_PATTERNS:
        value = pattern.sub("<redacted>", value)
    value = WINDOWS_PATH_PATTERN.sub("<absolute-path-redacted>", value)
    return POSIX_PATH_PATTERN.sub("<absolute-path-redacted>", value)


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


def _command_env() -> dict[str, str]:
    env = {
        key: value for key in ALLOWED_ENV if (value := os.environ.get(key)) is not None
    }
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("NO_COLOR", "1")
    env["WORKFLOW_EVIDENCE_READ_ONLY"] = "1"
    return env


def _run(
    argv: Sequence[str], repo_root: Path, *, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in argv],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(env) if env is not None else _command_env(),
    )


def _run_bytes(
    argv: Sequence[str], repo_root: Path
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(item) for item in argv],
        cwd=repo_root,
        check=False,
        capture_output=True,
        env=_command_env(),
    )


def _read_tracked_file(repo_root: Path, relative_path: str) -> bytes:
    path = repo_root / relative_path
    if path.is_symlink():
        raise RunnerError(f"trusted file must not be a symlink: {relative_path}")
    try:
        actual = path.read_bytes()
    except FileNotFoundError as exc:
        raise RunnerError(f"required trusted file is missing: {relative_path}") from exc
    for argv, label in (
        (("git", "diff", "--quiet", "--", relative_path), "working tree"),
        (("git", "diff", "--cached", "--quiet", "--", relative_path), "index"),
    ):
        result = _run_bytes(argv, repo_root)
        if result.returncode == 1:
            raise RunnerError(
                f"trusted file differs from HEAD in the {label}: {relative_path}"
            )
        if result.returncode != 0:
            error = _redact(result.stderr.decode("utf-8", errors="replace")).strip()
            raise RunnerError(f"unable to verify trusted file {relative_path}: {error}")
    head = _run_bytes(("git", "show", f"HEAD:{relative_path}"), repo_root)
    if head.returncode != 0:
        raise RunnerError(f"trusted file is not tracked at HEAD: {relative_path}")
    if actual != head.stdout:
        raise RunnerError(f"trusted file content does not match HEAD: {relative_path}")
    return actual


def _bundle_root(repo_root: Path) -> Path | None:
    raw = os.environ.get(TRUSTED_BUNDLE_ROOT_ENV)
    if raw is None:
        return None
    root = Path(raw).resolve()
    allowed_root = (repo_root / ".agents/evidence.local/trusted").resolve()
    if not root.is_relative_to(allowed_root):
        raise RunnerError("trusted bundle must be below .agents/evidence.local/trusted")
    return root


def _read_bundle_inputs(repo_root: Path, bundle_root: Path) -> dict[str, bytes]:
    manifest_path = bundle_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("trusted bundle manifest is missing or invalid") from exc
    expected_sha = os.environ.get("WORKFLOW_TRUSTED_RUNNER_SHA")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 2
        or manifest.get("tool") != "evidence-runner"
        or manifest.get("trusted_sha") != expected_sha
    ):
        raise RunnerError("trusted evidence-runner bundle identity is invalid")
    hashes = manifest.get("files")
    if not isinstance(hashes, dict) or set(hashes) != set(TRUSTED_PATHS):
        raise RunnerError("trusted evidence-runner bundle file set is invalid")
    payloads: dict[str, bytes] = {}
    for relative_path in TRUSTED_PATHS:
        source = bundle_root / relative_path
        if source.is_symlink():
            raise RunnerError(
                f"trusted bundle file must not be a symlink: {relative_path}"
            )
        try:
            payload = source.read_bytes()
        except FileNotFoundError as exc:
            raise RunnerError(
                f"trusted bundle file is missing: {relative_path}"
            ) from exc
        if _sha256_bytes(payload) != hashes.get(relative_path):
            raise RunnerError(f"trusted bundle file digest mismatch: {relative_path}")
        payloads[relative_path] = payload
    return payloads


def _load_trusted_inputs(repo_root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    bundle_root = _bundle_root(repo_root)
    if bundle_root is None:
        payloads = {path: _read_tracked_file(repo_root, path) for path in TRUSTED_PATHS}
    else:
        payloads = _read_bundle_inputs(repo_root, bundle_root)
    try:
        spec = json.loads(payloads[SPEC_PATH].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("profile specification is not valid UTF-8 JSON") from exc
    if not isinstance(spec, dict):
        raise RunnerError("profile specification must be a JSON object")
    _validate_spec(spec)
    return spec, {path: _sha256_bytes(payload) for path, payload in payloads.items()}


def _validate_spec(spec: Mapping[str, Any]) -> None:
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise RunnerError("profile specification schema version is unsupported")
    if spec.get("runner_version") != RUNNER_VERSION:
        raise RunnerError("profile specification runner version does not match")
    if spec.get("repository") != REPOSITORY:
        raise RunnerError("profile specification repository is not allowed")
    raw_profiles = spec.get("profiles")
    if not isinstance(raw_profiles, dict) or set(raw_profiles) != set(
        CANONICAL_PROFILES
    ):
        raise RunnerError("profile specification names do not match canonical profiles")
    for name, (operation, required) in CANONICAL_PROFILES.items():
        raw = raw_profiles.get(name)
        if not isinstance(raw, dict):
            raise RunnerError(f"invalid profile specification: {name}")
        if raw.get("operation") != operation:
            raise RunnerError(f"profile operation drift: {name}")
        if raw.get("required_arguments") != list(required):
            raise RunnerError(f"profile argument contract drift: {name}")


def _parse_repository_slug(remote: str) -> str | None:
    value = remote.strip().removesuffix(".git")
    if value.startswith("git@github.com:"):
        value = value.split(":", 1)[1]
    elif "github.com/" in value:
        value = value.split("github.com/", 1)[1]
    value = value.strip("/")
    return value if REPOSITORY_PATTERN.fullmatch(value) else None


def _find_repo_root(script_path: Path) -> Path:
    if script_path.is_symlink():
        raise RunnerError("runner entry must not be invoked through a symlink")
    target_root = os.environ.get(TARGET_REPO_ROOT_ENV)
    if target_root is None:
        repo_root = script_path.resolve().parents[2]
        expected = repo_root / RUNNER_PATH
        if script_path.resolve() != expected.resolve():
            raise RunnerError("runner entry path is not the trusted repository entry")
    else:
        repo_root = Path(target_root).resolve()
        bundle_root = _bundle_root(repo_root)
        if bundle_root is None:
            raise RunnerError("trusted runner target requires a trusted bundle")
        expected = bundle_root / RUNNER_PATH
        if script_path.resolve() != expected.resolve():
            raise RunnerError("runner entry path is not the trusted bundle entry")
    if Path.cwd().resolve() != repo_root.resolve():
        raise RunnerError("runner must be started from the repository root")
    if repo_root.resolve().as_posix().startswith("/mnt/"):
        raise RunnerError("repository must be on the WSL2 Linux filesystem, not /mnt")
    git_root = _run(("git", "rev-parse", "--show-toplevel"), repo_root)
    if git_root.returncode != 0 or Path(git_root.stdout.strip()).resolve() != repo_root:
        raise RunnerError("current directory is not the runner Git repository root")
    gh_host = os.environ.get("GH_HOST")
    if gh_host and gh_host.casefold() != "github.com":
        raise RunnerError("GH_HOST must be github.com for this fixed repository")
    origin = _run(("git", "remote", "get-url", "origin"), repo_root)
    if origin.returncode != 0:
        raise RunnerError("origin remote is required")
    actual_repository = _parse_repository_slug(origin.stdout)
    if actual_repository != REPOSITORY:
        raise RunnerError(
            f"origin repository is not allowed: {actual_repository or 'unrecognized'}"
        )
    return repo_root


def _require_output_root_ignored(repo_root: Path) -> None:
    ignore_path = repo_root / ".gitignore"
    try:
        patterns = {
            line.strip().removeprefix("./")
            for line in ignore_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "!"))
        }
    except FileNotFoundError as exc:
        raise RunnerError(".gitignore is required") from exc
    if ".agents/evidence.local/" not in patterns:
        raise RunnerError(
            "local evidence root requires the exact .agents/evidence.local/ ignore rule"
        )


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number <= 0 or number > 2_147_483_647:
        raise argparse.ArgumentTypeError("must be a positive 32-bit integer")
    return number


def _sha(value: str) -> str:
    normalized = value.casefold()
    if SHA_PATTERN.fullmatch(normalized) is None:
        raise argparse.ArgumentTypeError("must be a full 40-character Git SHA")
    return normalized


def _snapshot_id(value: str) -> str:
    if SNAPSHOT_ID_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be an ev- snapshot ID")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect fixed, read-only GitHub and Git workflow evidence."
    )
    sub = parser.add_subparsers(dest="profile", required=True)

    delivery = sub.add_parser("delivery")
    delivery.add_argument("--task", type=_positive_int, required=True)
    delivery.add_argument("--expected-main-sha", type=_sha, required=True)

    for name in ("delivery-readiness", "review", "pre-merge"):
        item = sub.add_parser(name)
        item.add_argument("--task", type=_positive_int, required=True)
        item.add_argument("--pr", type=_positive_int, required=True)
        item.add_argument("--expected-base-sha", type=_sha, required=True)
        item.add_argument("--expected-head-sha", type=_sha, required=True)

    closeout = sub.add_parser("closeout-readonly")
    closeout.add_argument("--task", type=_positive_int, required=True)
    closeout.add_argument("--pr", type=_positive_int, required=True)
    closeout.add_argument("--expected-head-sha", type=_sha, required=True)
    closeout.add_argument("--expected-merge-sha", type=_sha, required=True)

    recheck = sub.add_parser("recheck")
    recheck.add_argument("--snapshot-id", type=_snapshot_id, required=True)
    return parser


def _evidence_argv(args: argparse.Namespace, repo_root: Path) -> list[str]:
    profile = str(args.profile)
    operation = CANONICAL_PROFILES[profile][0]
    base = [
        sys.executable,
        str((_bundle_root(repo_root) or repo_root) / EVIDENCE_TOOL_PATH),
    ]
    if profile == "recheck":
        snapshot_path = (
            repo_root / ".agents/evidence.local/snapshots" / f"{args.snapshot_id}.json"
        )
        if snapshot_path.is_symlink():
            raise RunnerError("recheck snapshot must not be a symlink")
        try:
            previous = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RunnerError("recheck snapshot does not exist") from exc
        except json.JSONDecodeError as exc:
            raise RunnerError("recheck snapshot is invalid JSON") from exc
        if not isinstance(previous, dict):
            raise RunnerError("recheck snapshot must be a JSON object")
        if previous.get("repository") != REPOSITORY:
            raise RunnerError("recheck snapshot repository is not allowed")
        stored_snapshot_id = previous.get("snapshot_id")
        core = {key: value for key, value in previous.items() if key != "snapshot_id"}
        expected_snapshot_id = f"ev-{_sha256_json(core)[:16]}"
        if (
            stored_snapshot_id != args.snapshot_id
            or stored_snapshot_id != expected_snapshot_id
        ):
            raise RunnerError("recheck snapshot fingerprint is invalid")
        subject = previous.get("subject")
        if not isinstance(subject, dict):
            raise RunnerError("recheck snapshot subject is invalid")
        for key in ("task_number", "pr_number"):
            value = subject.get(key)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                raise RunnerError("recheck snapshot subject identity is invalid")
        previous_operation = previous.get("operation")
        if not isinstance(previous_operation, str):
            raise RunnerError("recheck snapshot operation is invalid")
        recheck_command = RECHECK_COMMANDS.get(previous_operation)
        if recheck_command is None:
            raise RunnerError(
                "snapshot operation does not support this recheck profile"
            )
        return [
            *base,
            recheck_command,
            "--snapshot-id",
            args.snapshot_id,
            "--repo-root",
            ".",
            "--repository",
            REPOSITORY,
        ]

    result = [
        *base,
        operation,
        "--repo-root",
        ".",
        "--repository",
        REPOSITORY,
        "--task",
        str(args.task),
    ]
    if profile == "delivery":
        result.extend(["--expected-main-sha", args.expected_main_sha])
    elif profile in {"delivery-readiness", "review", "pre-merge"}:
        result.extend(
            [
                "--pr",
                str(args.pr),
                "--expected-base-sha",
                args.expected_base_sha,
                "--expected-head-sha",
                args.expected_head_sha,
            ]
        )
    elif profile == "closeout-readonly":
        result.extend(
            [
                "--pr",
                str(args.pr),
                "--expected-head-sha",
                args.expected_head_sha,
                "--expected-merge-sha",
                args.expected_merge_sha,
            ]
        )
    else:
        raise RunnerError("unsupported profile")
    return result


def _recursive_truncation(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("truncated") is True:
            return True
        return any(_recursive_truncation(item) for item in value.values())
    if isinstance(value, list):
        return any(_recursive_truncation(item) for item in value)
    return False


def _gate_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    raw = snapshot.get("gates")
    gates = raw if isinstance(raw, dict) else {}
    names: dict[str, list[str]] = {"pass": [], "fail": [], "unknown": []}
    for name, value in gates.items():
        status = value.get("status") if isinstance(value, dict) else None
        if status in names:
            names[status].append(str(name))
        else:
            names["unknown"].append(str(name))
    return {
        "pass": len(names["pass"]),
        "fail": len(names["fail"]),
        "unknown": len(names["unknown"]),
        "failed_gates": sorted(names["fail"]),
        "unknown_gates": sorted(names["unknown"]),
    }


def _derive_status(snapshot: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    gates = _gate_summary(snapshot)
    warning_count = snapshot.get("warning_count")
    limitation_count = snapshot.get("limitation_count")
    warning_count = warning_count if isinstance(warning_count, int) else 0
    limitation_count = limitation_count if isinstance(limitation_count, int) else 0
    truncated = _recursive_truncation(snapshot.get("observed"))
    if gates["fail"]:
        status = "fail"
    elif gates["unknown"] or warning_count or truncated:
        status = "partial"
    else:
        status = "pass"
    return status, {
        **gates,
        "warning_count": warning_count,
        "limitation_count": limitation_count,
        "observed_truncated": truncated,
    }


def _remote_refs(
    repo_root: Path, *, head_branch: str | None
) -> tuple[dict[str, Any], list[str]]:
    refs = ["main"]
    if head_branch and head_branch != "main":
        refs.append(head_branch)
    result = _run(("git", "ls-remote", "--heads", "origin", *refs), repo_root)
    warnings: list[str] = []
    if result.returncode != 0:
        warnings.append(_redact(result.stderr or result.stdout).strip())
        return {"available": False, "main_sha": None, "head_sha": None}, warnings
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2 or SHA_PATTERN.fullmatch(parts[0].casefold()) is None:
            continue
        values[parts[1].removeprefix("refs/heads/")] = parts[0].casefold()
    return {
        "available": "main" in values,
        "main_sha": values.get("main"),
        "head_sha": values.get(head_branch) if head_branch else None,
    }, warnings


def _compact_result(
    snapshot: Mapping[str, Any],
    *,
    profile: str,
    status: str,
    status_details: Mapping[str, Any],
    started_at: datetime,
    duration_ms: int,
    run_id: str,
    result_path: str,
    integrity: Mapping[str, str],
    trusted_bundle: bool,
    remote_refs: Mapping[str, Any],
    remote_warnings: Sequence[str],
) -> dict[str, Any]:
    observed = snapshot.get("observed")
    observed = observed if isinstance(observed, dict) else {}
    issue = observed.get("issue")
    issue = issue if isinstance(issue, dict) else {}
    issue_closure = issue.get("issue_closure")
    issue_closure = issue_closure if isinstance(issue_closure, dict) else {}
    pr = observed.get("pr")
    pr = pr if isinstance(pr, dict) else {}
    git = observed.get("git")
    git = git if isinstance(git, dict) else {}
    threads = observed.get("review_threads")
    threads = threads if isinstance(threads, dict) else {}
    diff = observed.get("effective_diff")
    diff = diff if isinstance(diff, dict) else {}
    checks = pr.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    required_checks = observed.get("required_checks")
    required_checks = required_checks if isinstance(required_checks, dict) else {}
    branch_cleanup = observed.get("branch_cleanup")
    branch_cleanup = branch_cleanup if isinstance(branch_cleanup, dict) else {}
    stability = snapshot.get("stability")
    stability = stability if isinstance(stability, dict) else {}
    source_core = {
        key: value for key, value in snapshot.items() if key != "details_path"
    }
    subject = snapshot.get("subject")
    subject = subject if isinstance(subject, dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "profile": profile,
        "status": status,
        "partial": status == "partial",
        "started_at": started_at.isoformat(),
        "duration_ms": duration_ms,
        "identity": {
            "task": issue.get("number") or subject.get("task_number"),
            "pr": pr.get("number") or subject.get("pr_number"),
            "repository": snapshot.get("repository"),
            "base_sha": pr.get("base_sha"),
            "head_sha": pr.get("head_sha"),
            "merge_sha": pr.get("merge_commit_sha"),
        },
        "issue": {
            "available": bool(issue),
            "state": issue.get("state"),
            "labels": issue.get("labels"),
            "project_status": issue.get("project_status"),
            "content_sha256": issue.get("content_sha256"),
            "closure": {
                "status": issue_closure.get("status"),
                "reason": issue_closure.get("reason"),
                "evidence_status": issue_closure.get("evidence_status"),
                "closer_type": issue_closure.get("closer_type"),
                "closer_repository": issue_closure.get("closer_repository"),
                "closer_number": issue_closure.get("closer_number"),
            },
        },
        "pull_request": {
            "available": bool(pr),
            "state": pr.get("state"),
            "draft": pr.get("is_draft"),
            "mergeable": pr.get("mergeable"),
            "review_decision": pr.get("review_decision"),
            "checks": checks,
            "unresolved_threads": threads.get("unresolved"),
            "threads_available": threads.get("available"),
        },
        "checks": {
            "required_configuration": required_checks.get("configuration"),
            "required_failure": required_checks.get("failure"),
            "observed_runs": checks,
        },
        "branch_cleanup": branch_cleanup,
        "scope": {
            "changed_files": pr.get("changed_files"),
            "commits": pr.get("commits"),
            "diff_digest": diff.get("sha256"),
            "diff_bytes": diff.get("bytes"),
        },
        "git": {
            "current_branch": git.get("branch"),
            "working_tree_clean": git.get("clean"),
            "current_head": git.get("head_sha"),
            "local_main": git.get("local_main_sha"),
            "origin_main": git.get("origin_main_sha"),
            "origin_refresh": git.get("origin_refresh"),
            "remote_main": remote_refs.get("main_sha"),
            "remote_head": remote_refs.get("head_sha"),
        },
        "stability": {
            "snapshot_id": snapshot.get("snapshot_id"),
            "snapshot_fingerprint": _sha256_json(source_core),
            "stable": stability.get("stable"),
            "changed_fields": stability.get("changed_fields"),
            "partial": status == "partial",
        },
        "evidence": {
            "source_operation": snapshot.get("operation"),
            "source_details_path": snapshot.get("details_path"),
            "gate_summary": dict(status_details),
            "operations": snapshot.get("operations"),
            "runner_operations": {"git_commands": 1, "github_queries": 0},
            "warnings": snapshot.get("warnings"),
            "limitations": snapshot.get("limitations"),
            "remote_ref_warnings": list(remote_warnings),
        },
        "artifacts": {
            "run_id": run_id,
            "result_json": result_path,
            "raw_api_responses_committed": False,
        },
        "integrity": {
            "verification": (
                "trusted-commit-bundle-pre-execution"
                if trusted_bundle
                else "tracked-head-pre-execution"
            ),
            "trusted_files": dict(integrity),
        },
    }


def _exit_code(status: str) -> int:
    return {"pass": 0, "partial": 3, "fail": 4}.get(status, 2)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        repo_root = _find_repo_root(Path(__file__))
        _, trusted_hashes = _load_trusted_inputs(repo_root)
        _require_output_root_ignored(repo_root)
        command = _evidence_argv(args, repo_root)
        started_at = datetime.now(UTC)
        started = time.monotonic()
        run_id = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
        run_dir = repo_root / OUTPUT_ROOT / run_id
        result_path = run_dir / "result.json"
        stdout_path = run_dir / "workflow-evidence.stdout.json"
        stderr_path = run_dir / "workflow-evidence.stderr.log"
        run_dir.mkdir(parents=True, exist_ok=False)

        completed = _run(command, repo_root, env=_command_env())
        bounded_stdout, stdout_truncated, stdout_digest = _bounded(completed.stdout)
        bounded_stderr, stderr_truncated, stderr_digest = _bounded(completed.stderr)
        _atomic_write(stdout_path, bounded_stdout.encode("utf-8"))
        _atomic_write(stderr_path, bounded_stderr.encode("utf-8"))
        if completed.returncode != 0:
            raise RunnerError(
                f"workflow evidence exited {completed.returncode}: "
                f"{bounded_stderr.strip() or bounded_stdout.strip()}"
            )
        try:
            snapshot = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RunnerError("workflow evidence returned invalid JSON") from exc
        if not isinstance(snapshot, dict):
            raise RunnerError("workflow evidence result is not a JSON object")

        pr_value = snapshot.get("observed", {}).get("pr")
        head_branch = (
            pr_value.get("head_branch") if isinstance(pr_value, dict) else None
        )
        remote_refs, remote_warnings = _remote_refs(
            repo_root, head_branch=head_branch if isinstance(head_branch, str) else None
        )
        status, status_details = _derive_status(snapshot)
        status_details = dict(status_details)
        observed_value = snapshot.get("observed")
        observed_value = observed_value if isinstance(observed_value, dict) else {}
        git_value = observed_value.get("git")
        git_value = git_value if isinstance(git_value, dict) else {}
        pr_value = observed_value.get("pr")
        pr_value = pr_value if isinstance(pr_value, dict) else {}
        remote_conflicts: list[str] = []
        if remote_warnings or remote_refs.get("available") is not True:
            if status == "pass":
                status = "partial"
        else:
            origin_main = git_value.get("origin_main_sha")
            if (
                isinstance(origin_main, str)
                and remote_refs.get("main_sha") != origin_main
            ):
                remote_conflicts.append("origin_main_vs_remote_main")
            pr_state = str(pr_value.get("state") or "").upper()
            pr_head = pr_value.get("head_sha")
            if pr_state == "OPEN" and isinstance(pr_head, str):
                if remote_refs.get("head_sha") != pr_head:
                    remote_conflicts.append("pr_head_vs_remote_head")
        if remote_conflicts:
            status = "fail"
        status_details["remote_ref_warning_count"] = len(remote_warnings)
        status_details["remote_ref_conflicts"] = remote_conflicts
        duration_ms = round((time.monotonic() - started) * 1000)
        result = _compact_result(
            snapshot,
            profile=args.profile,
            status=status,
            status_details=status_details,
            started_at=started_at,
            duration_ms=duration_ms,
            run_id=run_id,
            result_path=result_path.relative_to(repo_root).as_posix(),
            integrity=trusted_hashes,
            trusted_bundle=_bundle_root(repo_root) is not None,
            remote_refs=remote_refs,
            remote_warnings=remote_warnings,
        )
        result["evidence"]["subprocess"] = {
            "exit_code": completed.returncode,
            "stdout_digest": stdout_digest,
            "stderr_digest": stderr_digest,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        payload = _json_dumps(result, pretty=True).encode("utf-8")
        _atomic_write(result_path, payload)
        result_sha = _sha256_bytes(payload)
        compact = {
            "api_calls": result.get("evidence", {})
            .get("operations", {})
            .get("github_queries"),
            "base_sha": result["identity"]["base_sha"],
            "duration_ms": duration_ms,
            "head_sha": result["identity"]["head_sha"],
            "partial": result["partial"],
            "pr": result["identity"]["pr"],
            "profile": args.profile,
            "result_path": result_path.relative_to(repo_root).as_posix(),
            "result_sha256": result_sha,
            "snapshot_id": result["stability"]["snapshot_id"],
            "status": status,
            "task": result["identity"]["task"],
            "unknown_gate_count": status_details.get("unknown", 0),
        }
        print(_json_dumps(compact), end="")
        return _exit_code(status)
    except RunnerError as exc:
        print(
            f"WSL2 GitHub evidence runner error: {_redact(str(exc))}",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(
            f"WSL2 GitHub evidence runner I/O error: {_redact(str(exc))}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
