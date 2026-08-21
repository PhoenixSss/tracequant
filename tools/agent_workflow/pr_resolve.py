#!/usr/bin/env python3
"""Deterministic, fail-closed PR resolve-or-create helper.

This is NOT a Runner. It is a shared library that provides a single,
non-fallback PR resolve/create path for use by delivery Skills.

It enforces in code the command discipline previously described only in
Skill prose: no stderr suppression, no empty-stdout-to-JSON, no retry with
modified --json fields, no implicit PR selection after creation.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

if __name__ != "__main__" or not any(p.endswith("agent_workflow") for p in sys.path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from workflow_common import (
    CommandRunner,
    WorkflowToolError,
    command_warning,
    read_json_text,
    safe_text,
)

_PR_URL_PATTERN: Final = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+"
)
_PR_LIST_FIELDS: Final = "number,url,state,isDraft,baseRefName,headRefName,headRefOid"
_PR_VIEW_FIELDS: Final = (
    "number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
    "statusCheckRollup"
)
_REQUIRED_PR_FIELDS: Final = (
    "number",
    "url",
    "state",
    "isDraft",
    "baseRefName",
    "baseRefOid",
    "headRefName",
    "headRefOid",
)


class PrResolveError(WorkflowToolError):
    """Expected fail-closed error from PR resolve/create operations."""


def _validate_pr_identity(
    pr: Mapping[str, Any],
    *,
    repository: str,
    expected_branch: str,
    expected_base: str,
    expected_head_sha: str | None,
    expected_base_sha: str | None,
    require_non_draft: bool = True,
) -> None:
    """Verify a PR dict has all required identity fields and matches expectations."""
    missing = [f for f in _REQUIRED_PR_FIELDS if f not in pr]
    if missing:
        raise PrResolveError(
            f"PR identity missing required fields: {', '.join(missing)}"
        )

    number = pr["number"]
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise PrResolveError(f"PR number is invalid: {number!r}")

    url = pr.get("url")
    if not isinstance(url, str) or not url:
        raise PrResolveError("PR URL is missing or invalid")
    if repository not in str(url):
        raise PrResolveError(
            f"PR URL repository mismatch: expected {repository} in {url}"
        )

    state = str(pr.get("state", "")).upper()
    if state != "OPEN":
        raise PrResolveError(f"PR state is not OPEN: {state}")

    is_draft = pr.get("isDraft")
    if require_non_draft and is_draft is not False:
        raise PrResolveError(f"PR is Draft or draft status unknown: {is_draft!r}")

    head_branch = pr.get("headRefName")
    if not isinstance(head_branch, str) or head_branch != expected_branch:
        raise PrResolveError(
            f"PR head branch mismatch: expected {expected_branch!r}, "
            f"observed {head_branch!r}"
        )

    base_branch = pr.get("baseRefName")
    if not isinstance(base_branch, str) or base_branch != expected_base:
        raise PrResolveError(
            f"PR base branch mismatch: expected {expected_base!r}, "
            f"observed {base_branch!r}"
        )

    if expected_base_sha is not None:
        base_sha = pr.get("baseRefOid")
        if not isinstance(base_sha, str) or base_sha != expected_base_sha:
            raise PrResolveError(
                f"PR base SHA mismatch: expected {expected_base_sha}, "
                f"observed {base_sha!r}"
            )

    if expected_head_sha is not None:
        head_sha = pr.get("headRefOid")
        if not isinstance(head_sha, str) or head_sha != expected_head_sha:
            raise PrResolveError(
                f"PR head SHA mismatch: expected {expected_head_sha}, "
                f"observed {head_sha!r}"
            )


def list_matching_prs(
    runner: CommandRunner,
    repository: str,
    current_branch: str,
    base_branch: str,
    warnings: list[dict[str, Any]],
    *,
    state: str = "all",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read matching PRs for live-state consumers without any side effect."""
    if state not in {"open", "closed", "merged", "all"}:
        raise PrResolveError(f"unsupported PR list state: {state!r}")
    if limit < 1 or limit > 100:
        raise PrResolveError(f"invalid PR list limit: {limit!r}")
    result = runner.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            current_branch,
            "--base",
            base_branch,
            "--state",
            state,
            "--limit",
            str(limit),
            "--json",
            _PR_LIST_FIELDS,
        ],
        command_id="gh-pr-list-live-state-history",
    )
    if result.returncode != 0:
        warnings.append(command_warning(result))
        raise PrResolveError(
            f"gh pr list failed with exit code {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    if not result.stdout.strip():
        raise PrResolveError("gh pr list returned empty stdout — cannot read PR state")
    value = read_json_text(result.stdout, field="gh-pr-list-live-state-history")
    if not isinstance(value, list):
        raise PrResolveError("live PR history result is not a JSON array")
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise PrResolveError("live PR history item is not a JSON object")
        items.append(dict(item))
    return items


def resolve_open_pr(
    runner: CommandRunner,
    repository: str,
    current_branch: str,
    base_branch: str,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve the unique matching OPEN PR without creating or changing one.

    This is the read-only counterpart used by live-state resolution.  It keeps
    the PR query and identity checks in the shared resolver instead of creating
    a second GitHub query stack in the Local Control Kernel.
    """
    list_result = runner.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            current_branch,
            "--base",
            base_branch,
            "--state",
            "open",
            "--limit",
            "2",
            "--json",
            _PR_LIST_FIELDS,
        ],
        command_id="gh-pr-list-live-state",
    )
    if list_result.returncode != 0:
        warnings.append(command_warning(list_result))
        raise PrResolveError(
            f"gh pr list failed with exit code {list_result.returncode}: "
            f"{list_result.stderr.strip() or list_result.stdout.strip()}"
        )
    if not list_result.stdout.strip():
        raise PrResolveError(
            "gh pr list returned empty stdout — cannot resolve live PR state"
        )
    value = read_json_text(list_result.stdout, field="gh-pr-list-live-state")
    if not isinstance(value, list):
        raise PrResolveError("live PR list result is not a JSON array")
    if not value:
        return None
    if len(value) > 1:
        numbers = [
            item.get("number") if isinstance(item, Mapping) else None for item in value
        ]
        raise PrResolveError(
            f"multiple OPEN PRs for head={current_branch!r} "
            f"base={base_branch!r}: {numbers}"
        )

    listed = value[0]
    if not isinstance(listed, Mapping):
        raise PrResolveError("live PR list item is not a JSON object")
    pr_url = listed.get("url")
    if not isinstance(pr_url, str) or not pr_url:
        raise PrResolveError("live PR is missing URL")
    view_result = runner.run(
        [
            "gh",
            "pr",
            "view",
            pr_url,
            "--repo",
            repository,
            "--json",
            _PR_VIEW_FIELDS,
        ],
        command_id="gh-pr-view-live-state",
    )
    if view_result.returncode != 0:
        warnings.append(command_warning(view_result))
        raise PrResolveError(
            f"gh pr view identity verification failed with exit code "
            f"{view_result.returncode}: "
            f"{view_result.stderr.strip() or view_result.stdout.strip()}"
        )
    viewed = read_json_text(view_result.stdout, field="gh-pr-view-live-state")
    if not isinstance(viewed, Mapping):
        raise PrResolveError("live PR view result is not a JSON object")
    _validate_pr_identity(
        viewed,
        repository=repository,
        expected_branch=current_branch,
        expected_base=base_branch,
        expected_head_sha=None,
        expected_base_sha=None,
        require_non_draft=False,
    )
    return dict(viewed)


def resolve_or_create_pr(
    runner: CommandRunner,
    repository: str,
    current_branch: str,
    base_branch: str,
    pr_title: str,
    pr_body_file: Path | None,
    expected_head_sha: str | None,
    expected_base_sha: str | None,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve an existing matching PR or create one — exactly one path, no fallbacks.

    Returns a dict with keys: action, number, url, state, is_draft,
    base_branch, base_sha, head_branch, head_sha.

    Raises PrResolveError on any failure: multiple matching PRs, identity
    mismatch, command failure, empty stdout, invalid JSON, or missing fields.
    """
    # Step 1: Query for existing matching PRs
    list_result = runner.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            current_branch,
            "--base",
            base_branch,
            "--state",
            "open",
            "--limit",
            "2",
            "--json",
            _PR_LIST_FIELDS,
        ],
        command_id="gh-pr-list-resolve",
    )

    if list_result.returncode != 0:
        warnings.append(command_warning(list_result))
        raise PrResolveError(
            f"gh pr list failed with exit code {list_result.returncode}: "
            f"{list_result.stderr.strip() or list_result.stdout.strip()}"
        )

    if not list_result.stdout.strip():
        raise PrResolveError(
            "gh pr list returned empty stdout — cannot determine PR state"
        )

    try:
        pr_list = read_json_text(list_result.stdout, field="gh-pr-list-resolve")
    except WorkflowToolError as exc:
        raise PrResolveError(str(exc)) from exc
    if not isinstance(pr_list, list):
        raise PrResolveError("gh pr list result is not a JSON array")

    pr_url: str
    pr_number: int

    if len(pr_list) == 0:
        # Step 2a: No matching PR — create one
        create_argv: list[str] = [
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            base_branch,
            "--head",
            current_branch,
            "--title",
            pr_title,
        ]
        if pr_body_file is not None:
            create_argv.extend(["--body-file", str(pr_body_file)])

        create_result = runner.run(
            create_argv,
            command_id="gh-pr-create",
        )

        if create_result.returncode != 0:
            warnings.append(command_warning(create_result))
            raise PrResolveError(
                f"gh pr create failed with exit code {create_result.returncode}: "
                f"{create_result.stderr.strip() or create_result.stdout.strip()}"
            )

        stdout = create_result.stdout.strip()
        if not stdout:
            raise PrResolveError("gh pr create returned empty stdout — no PR URL")

        match = _PR_URL_PATTERN.search(stdout)
        if match is None:
            raise PrResolveError(
                f"gh pr create stdout does not contain a PR URL: {safe_text(stdout, limit=200)}"
            )
        pr_url = match.group(0)
        # Extract number from URL: https://github.com/owner/repo/pull/N
        url_number_match = re.search(r"/pull/(\d+)$", pr_url.rstrip("/"))
        if url_number_match is None:
            raise PrResolveError(f"Cannot extract PR number from URL: {pr_url}")
        pr_number = int(url_number_match.group(1))
        action = "created"

    elif len(pr_list) == 1:
        # Step 2b: One matching PR — resolve it
        pr_item = pr_list[0]
        if not isinstance(pr_item, dict):
            raise PrResolveError("gh pr list item is not a JSON object")

        raw_url = pr_item.get("url")
        raw_number = pr_item.get("number")
        if not isinstance(raw_url, str) or not raw_url:
            raise PrResolveError("resolved PR is missing URL")
        if (
            not isinstance(raw_number, int)
            or isinstance(raw_number, bool)
            or raw_number <= 0
        ):
            raise PrResolveError(f"resolved PR number is invalid: {raw_number!r}")

        pr_url = raw_url
        pr_number = raw_number
        action = "resolved"

    else:
        # Step 2c: Multiple matching PRs — fail-closed
        numbers = [
            item.get("number") if isinstance(item, dict) else None for item in pr_list
        ]
        raise PrResolveError(
            f"multiple matching PRs for head={current_branch!r} "
            f"base={base_branch!r}: {numbers}"
        )

    # Step 3: Identity verification — exactly one gh pr view call, no fallback
    view_result = runner.run(
        [
            "gh",
            "pr",
            "view",
            pr_url,
            "--repo",
            repository,
            "--json",
            _PR_VIEW_FIELDS,
        ],
        command_id="gh-pr-view-identity",
    )

    if view_result.returncode != 0:
        warnings.append(command_warning(view_result))
        raise PrResolveError(
            f"gh pr view identity verification failed with exit code "
            f"{view_result.returncode}: "
            f"{view_result.stderr.strip() or view_result.stdout.strip()}"
        )

    if not view_result.stdout.strip():
        raise PrResolveError("gh pr view identity verification returned empty stdout")

    try:
        pr_identity = read_json_text(view_result.stdout, field="gh-pr-view-identity")
    except WorkflowToolError as exc:
        raise PrResolveError(str(exc)) from exc
    if not isinstance(pr_identity, dict):
        raise PrResolveError("gh pr view identity result is not a JSON object")

    # Verify the resolved/created number matches the identity response
    identity_number = pr_identity.get("number")
    if identity_number != pr_number:
        raise PrResolveError(
            f"PR number mismatch: resolved/created {pr_number}, "
            f"identity returned {identity_number!r}"
        )

    _validate_pr_identity(
        pr_identity,
        repository=repository,
        expected_branch=current_branch,
        expected_base=base_branch,
        expected_head_sha=expected_head_sha,
        expected_base_sha=expected_base_sha,
    )

    return {
        "action": action,
        "number": pr_number,
        "url": pr_url,
        "state": str(pr_identity.get("state", "")).upper(),
        "is_draft": pr_identity.get("isDraft"),
        "base_branch": safe_text(pr_identity.get("baseRefName")),
        "base_sha": pr_identity.get("baseRefOid"),
        "head_branch": safe_text(pr_identity.get("headRefName")),
        "head_sha": pr_identity.get("headRefOid"),
    }
