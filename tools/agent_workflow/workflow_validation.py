#!/usr/bin/env python3
"""Run current repository validation with compact, bounded JSON output."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from workflow_common import (
    MAX_LOG_BYTES,
    CommandRunner,
    WorkflowToolError,
    is_sha,
    print_json,
    redact_machine_paths,
    redact_sensitive,
    require_exact_ignored_directory,
    safe_text,
    sha256_json,
    stderr_tail,
)

SCHEMA_VERSION: Final = 1
OUTPUT_DIR: Final = ".agents/validation.local"
SKILLS: Final = (
    "task-delivery",
    "task-pr-review",
    "task-closeout",
    "feature-completion-audit",
)


@dataclass(frozen=True)
class ValidationCommand:
    command_id: str
    argv: tuple[str, ...]
    required: bool = True


def _git_changed_files(runner: CommandRunner, base_sha: str | None) -> list[str]:
    diff_target = f"{base_sha}...HEAD" if base_sha else "HEAD"
    result = runner.run(
        ["git", "diff", "--name-only", diff_target],
        command_id="git-validation-changed-files",
    )
    if result.returncode != 0:
        raise WorkflowToolError(
            "cannot determine validation change scope: "
            + stderr_tail(result.stderr or result.stdout, limit=500)
        )
    return [line for line in result.stdout.splitlines() if line]


def _discover_skill_validator(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate.resolve() if candidate.is_file() else None
    env_value = os.environ.get("CODEX_SKILL_VALIDATOR")
    if env_value:
        candidate = Path(env_value).expanduser()
        return candidate.resolve() if candidate.is_file() else None
    candidates: list[Path] = [
        Path.home() / ".codex/skills/.system/skill-creator/scripts/quick_validate.py",
        Path.home()
        / ".codex"
        / "skills"
        / ".system"
        / "skill-creator"
        / "scripts"
        / "quick_validate.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _build_plan(
    repo_root: Path,
    runner: CommandRunner,
    *,
    include_skill_validators: bool,
    skill_validator: Path | None,
    base_sha: str | None,
) -> tuple[list[ValidationCommand], list[str]]:
    commands: list[ValidationCommand] = []
    limitations: list[str] = []
    pyproject = repo_root / "pyproject.toml"
    lock = repo_root / "uv.lock"
    tests = repo_root / "tests"
    if lock.exists():
        commands.append(ValidationCommand("uv-lock-check", ("uv", "lock", "--check")))
    if tests.exists():
        commands.append(
            ValidationCommand("pytest", ("uv", "run", "--frozen", "pytest"))
        )
    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8")
        if "[tool.ruff" in content:
            commands.extend(
                [
                    ValidationCommand(
                        "ruff-check", ("uv", "run", "--frozen", "ruff", "check", ".")
                    ),
                    ValidationCommand(
                        "ruff-format-check",
                        ("uv", "run", "--frozen", "ruff", "format", "--check", "."),
                    ),
                ]
            )
        if "[tool.mypy]" in content:
            commands.append(
                ValidationCommand(
                    "mypy",
                    ("uv", "run", "--frozen", "mypy", "src", "tests"),
                )
            )
    commands.append(ValidationCommand("git-diff-check", ("git", "diff", "--check")))

    changed = _git_changed_files(runner, base_sha)
    governance_changed = any(
        path.startswith(".agents/skills/")
        or path.startswith(".agents/policies/")
        or path.startswith("tools/agent_workflow/")
        for path in changed
    )
    if include_skill_validators or governance_changed:
        if skill_validator is None:
            limitations.append("Skill validator unavailable")
        else:
            for skill in SKILLS:
                path = repo_root / ".agents" / "skills" / skill
                if path.is_dir():
                    commands.append(
                        ValidationCommand(
                            f"skill-{skill}",
                            (sys.executable, str(skill_validator), str(path)),
                        )
                    )
    return commands, limitations


def _sanitize_log(value: str, repo_root: Path) -> str:
    text = redact_machine_paths(redact_sensitive(value))
    replacements = [
        (str(repo_root.resolve()), "<repo>"),
        (str(Path.home().resolve()), "<home>"),
    ]
    for original, replacement in replacements:
        if original:
            text = text.replace(original, replacement)
            text = text.replace(original.replace("\\", "/"), replacement)
    if len(text.encode("utf-8")) <= MAX_LOG_BYTES:
        return text
    raw = text.encode("utf-8")[:MAX_LOG_BYTES]
    return raw.decode("utf-8", errors="ignore") + "\n<truncated>\n"


def _summary(command_id: str, stdout: str, stderr: str, returncode: int) -> str:
    combined = f"{stdout}\n{stderr}".strip()
    if returncode != 0:
        return stderr_tail(combined, limit=1200)
    patterns = {
        "pytest": r"(?m)^=+\s+([^\n]*passed[^\n]*)\s+=+$",
        "mypy": r"(?m)^Success: no issues found[^\n]*$",
        "ruff-check": r"(?m)^All checks passed!?$",
        "ruff-format-check": r"(?m)^\d+ files? already formatted$",
    }
    pattern = patterns.get(command_id)
    if pattern:
        match = re.search(pattern, combined)
        if match:
            return (
                safe_text(
                    match.group(1) if match.lastindex else match.group(0), limit=240
                )
                or "pass"
            )
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    if not lines:
        return "pass"
    return safe_text(lines[-1], limit=240) or "pass"


def _run_validation(
    args: argparse.Namespace, repo_root: Path
) -> tuple[dict[str, Any], int]:
    runner = CommandRunner(repo_root)
    skill_validator = _discover_skill_validator(args.skill_validator)
    plan, limitations = _build_plan(
        repo_root,
        runner,
        include_skill_validators=args.include_skill_validators,
        skill_validator=skill_validator,
        base_sha=args.base_sha,
    )
    if args.require_skill_validator and skill_validator is None:
        raise WorkflowToolError("Skill validator is required but unavailable")
    if not plan:
        raise WorkflowToolError("no validation commands were selected")

    plan_identity = {
        "phase": args.phase,
        "base_sha": args.base_sha,
        "commands": [
            {
                "command_id": item.command_id,
                "argv": [
                    Path(value).name if index == 0 else value
                    for index, value in enumerate(item.argv)
                ],
            }
            for item in plan
        ],
    }
    run_id = f"val-{args.phase}-{sha256_json(plan_identity)[:12]}"
    output_root = require_exact_ignored_directory(repo_root, OUTPUT_DIR)
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    overall = True
    for command in plan:
        started = time.monotonic()
        result = runner.run(
            command.argv,
            command_id=command.command_id,
            validation=True,
        )
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        status = "pass" if result.returncode == 0 else "fail"
        overall = overall and result.returncode == 0
        log_text = _sanitize_log(
            f"$ {' '.join(command.argv)}\n\n[stdout]\n{result.stdout}\n\n[stderr]\n{result.stderr}",
            repo_root,
        )
        log_path = run_dir / f"{command.command_id}.log"
        log_path.write_text(log_text, encoding="utf-8", newline="\n")
        entry: dict[str, Any] = {
            "command_id": command.command_id,
            "status": status,
            "exit_code": result.returncode,
            "duration_ms": duration_ms,
            "summary": _summary(
                command.command_id,
                result.stdout,
                result.stderr,
                result.returncode,
            ),
            "log_path": log_path.relative_to(repo_root).as_posix(),
        }
        if result.returncode != 0:
            entry["diagnostic"] = stderr_tail(
                f"{result.stdout}\n{result.stderr}",
                limit=2000,
            )
        results.append(entry)

    output: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "operation": "workflow-validation",
        "run_id": run_id,
        "phase": args.phase,
        "base_sha": args.base_sha,
        "status": "pass" if overall else "fail",
        "command_count": len(results),
        "passed": sum(item["status"] == "pass" for item in results),
        "failed": sum(item["status"] == "fail" for item in results),
        "commands": results,
        "limitations": limitations,
        "operations": runner.counters(),
        "output_dir": run_dir.relative_to(repo_root).as_posix(),
        "trusted_runner": {
            "source_sha": os.environ.get("WORKFLOW_TRUSTED_RUNNER_SHA"),
            "content_sha256": os.environ.get("WORKFLOW_TRUSTED_TOOL_CONTENT_SHA256"),
            "active": bool(os.environ.get("WORKFLOW_TRUSTED_RUNNER_SHA")),
        },
    }
    return output, 0 if overall else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run current CI-equivalent validation with compact JSON output."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run selected validation commands")
    run.add_argument("--repo-root", default=".")
    run.add_argument(
        "--phase",
        choices=("delivery", "review", "closeout", "feature-audit"),
        required=True,
    )
    run.add_argument("--skill-validator", help="path to quick_validate.py")
    run.add_argument(
        "--base-sha",
        help="trusted PR base SHA used to detect governance changes",
    )
    run.add_argument(
        "--include-skill-validators",
        action="store_true",
        help="run validators for all repository workflow Skills",
    )
    run.add_argument(
        "--require-skill-validator",
        action="store_true",
        help="fail when no Skill validator can be found",
    )
    run.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    if (
        args.command == "run"
        and args.base_sha is not None
        and not is_sha(args.base_sha)
    ):
        print(
            "workflow validation error: --base-sha must be a full commit SHA",
            file=sys.stderr,
        )
        return 2
    try:
        output, returncode = _run_validation(args, repo_root)
        print_json(output, pretty=args.pretty)
        return returncode
    except WorkflowToolError as exc:
        print(f"workflow validation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
