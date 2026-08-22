#!/usr/bin/env python3
"""Deterministic project status helper for delivery Skills.

Uses ``gh project item-edit`` with ``--url`` (not raw GraphQL) to update a
project item's single-select field.  This avoids the GraphQL ``items(filter:)``
compatibility problem and the manual ID-resolve dance.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Final

from workflow_common import CommandRunner, WorkflowToolError

_REPO: Final = "PhoenixSss/tracequant"
_ISSUE_URL_PREFIX: Final = f"https://github.com/{_REPO}/issues/"


def set_project_status_with_runner(
    runner: CommandRunner,
    repository: str,
    task: int,
    *,
    project_number: int = 1,
    field: str = "Status",
    value: str = "Review",
) -> None:
    """Update one Task Project field through the shared deterministic runner."""
    if task <= 0:
        raise WorkflowToolError("Task number must be positive")
    owner, separator, _name = repository.partition("/")
    if not separator or not owner:
        raise WorkflowToolError("repository must be owner/name")
    url = f"https://github.com/{repository}/issues/{task}"
    result = runner.run(
        [
            "gh",
            "project",
            "item-edit",
            str(project_number),
            "--owner",
            owner,
            "--url",
            url,
            "--field",
            field,
            "--value",
            value,
        ],
        command_id="lck-project-status",
    )
    if result.returncode != 0:
        raise WorkflowToolError(
            f"gh project item-edit failed (exit {result.returncode}): "
            f"task={task}, field={field!r}, value={value!r}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def set_project_status(
    task: int,
    *,
    project_number: int = 1,
    owner: str = "PhoenixSss",
    field: str = "Status",
    value: str = "Review",
) -> None:
    """Update a Task project item's single-select field value.

    Uses ``gh project item-edit --url``, which resolves the item by issue URL
    and does not require GraphQL filter support.
    """
    url = f"{_ISSUE_URL_PREFIX}{task}"
    result = subprocess.run(
        [
            "gh",
            "project",
            "item-edit",
            str(project_number),
            "--owner",
            owner,
            "--url",
            url,
            "--field",
            field,
            "--value",
            value,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh project item-edit failed (exit {result.returncode}): "
            f"task={task}, field={field!r}, value={value!r}\n"
            f"stderr: {result.stderr.strip()}\n"
            f"stdout: {result.stdout.strip()}"
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: set-project-status <task-number> [--value <status>]."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Set a Task project item Status field."
    )
    parser.add_argument("task", type=int, help="Task / Issue number")
    parser.add_argument(
        "--value",
        default="Review",
        help="Status value to set (default: Review)",
    )
    parser.add_argument(
        "--field",
        default="Status",
        help="Field name (default: Status)",
    )
    parser.add_argument(
        "--project-number",
        type=int,
        default=1,
        help="Project number (default: 1)",
    )
    parser.add_argument(
        "--owner",
        default="PhoenixSss",
        help="Project owner (default: PhoenixSss)",
    )
    args = parser.parse_args(argv)

    try:
        set_project_status(
            args.task,
            project_number=args.project_number,
            owner=args.owner,
            field=args.field,
            value=args.value,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Task #{args.task}: {args.field} → {args.value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
