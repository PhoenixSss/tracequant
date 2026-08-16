#!/usr/bin/env python3
"""Compact, read-only evidence snapshots for repository workflow Skills.

The tool normalizes deterministic Git and GitHub facts. It never mutates GitHub,
commits, pushes, merges, closes Issues, or deletes branches. Semantic review and
workflow verdicts remain the responsibility of the governing Skill.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from workflow_common import (
    CommandResult,
    CommandRunner,
    WorkflowToolError,
    atomic_write_json,
    bounded_list,
    command_warning,
    is_sha,
    parse_repository_slug,
    print_json,
    read_json_file,
    read_json_text,
    require_exact_ignored_directory,
    safe_text,
    sha256_bytes,
    sha256_json,
)

SCHEMA_VERSION: Final = 1
EVIDENCE_ROOT: Final = ".agents/evidence.local"
SNAPSHOT_SUBDIR: Final = "snapshots"
REVIEW_HANDOFF_SUBDIR: Final = "review-handoffs"
REVIEW_HANDOFF_SCHEMA_VERSION: Final = 1
CANONICAL_REVIEW_SKILL_PATHS: Final = frozenset(
    {
        ".agents/skills/task-pr-review-runner/SKILL.md",
        ".claude/skills/task-pr-review-runner/SKILL.md",
    }
)
REVIEW_FINDING_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
MAX_CHILDREN: Final = 50
MAX_FILES: Final = 100

RELATIONSHIPS_QUERY: Final = r"""
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    issue(number:$number) {
      number
      title
      state
      issueType { name }
      parent { number title state }
      subIssues(first:100) {
        nodes {
          number
          title
          state
          labels(first:20) { nodes { name } }
        }
        pageInfo { hasNextPage }
      }
      blockedBy(first:50) {
        nodes { number title state }
        pageInfo { hasNextPage }
      }
      blocking(first:50) {
        nodes { number title state }
        pageInfo { hasNextPage }
      }
    }
  }
}
"""

LIFECYCLE_LABELS: Final = frozenset(
    {"codex:needs-spec", "codex:ready", "codex:blocked"}
)
KNOWN_PROJECT_STATUSES: Final = frozenset(
    {"Inbox", "Specifying", "Ready", "In Progress", "Review", "Blocked", "Done"}
)
DELIVERY_ENTRY_POINTS: Final = (
    "delivery-start",
    "implementation",
    "final-validation",
    "pr-readiness",
    "review-remediation",
)
DELIVERY_ENTRY_PARAMS: Final = {
    "delivery-start": frozenset({"task", "expected_main_sha"}),
    "implementation": frozenset(
        {"task", "expected_main_sha", "branch", "expected_base_sha"}
    ),
    "final-validation": frozenset(
        {
            "task",
            "expected_main_sha",
            "branch",
            "expected_base_sha",
            "expected_head_sha",
        }
    ),
    "pr-readiness": frozenset(
        {
            "task",
            "expected_main_sha",
            "branch",
            "expected_base_sha",
            "expected_head_sha",
        }
    ),
    "review-remediation": frozenset(
        {"task", "expected_main_sha", "pr", "expected_base_sha", "expected_head_sha"}
    ),
}
DELIVERY_PARAM_SPACE: Final = frozenset(
    {
        "task",
        "expected_main_sha",
        "branch",
        "pr",
        "expected_base_sha",
        "expected_head_sha",
    }
)
BRANCH_NAME_ALLOWED: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-"
)

THREADS_QUERY: Final = r"""
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        nodes { isResolved isOutdated comments(first:1) { totalCount } }
        pageInfo { hasNextPage }
      }
    }
  }
}
"""

ISSUE_CLOSURE_QUERY: Final = r"""
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    issue(number:$number) {
      number
      state
      closedAt
      closedByPullRequestsReferences(first:20, includeClosedPrs:true) {
        nodes {
          number
          state
          merged
          mergedAt
          url
          repository { nameWithOwner }
        }
        pageInfo { hasNextPage }
      }
      timelineItems(last:50, itemTypes:[CLOSED_EVENT, REOPENED_EVENT]) {
        nodes {
          __typename
          ... on ClosedEvent {
            createdAt
            closer {
              __typename
              ... on PullRequest {
                number
                state
                merged
                mergedAt
                url
                repository { nameWithOwner }
              }
            }
          }
          ... on ReopenedEvent { createdAt }
        }
        pageInfo { hasPreviousPage }
      }
    }
  }
}
"""


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _gate(status: str, detail: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status}
    if detail:
        result["detail"] = safe_text(detail, limit=256)
    return result


def _json_result(result: CommandResult, *, field: str) -> Any:
    if result.returncode != 0:
        raise WorkflowToolError(
            f"{result.command_id} failed with exit code {result.returncode}"
        )
    return read_json_text(result.stdout, field=field)


def _git_value(
    runner: CommandRunner,
    args: Sequence[str],
    *,
    command_id: str,
    warnings: list[dict[str, Any]],
) -> str | None:
    result = runner.run(["git", *args], command_id=command_id)
    if result.returncode != 0:
        warnings.append(command_warning(result))
        return None
    return result.stdout.strip() or None


def _git_lines(
    runner: CommandRunner,
    args: Sequence[str],
    *,
    command_id: str,
    warnings: list[dict[str, Any]],
) -> list[str]:
    value = _git_value(
        runner,
        args,
        command_id=command_id,
        warnings=warnings,
    )
    if value is None:
        return []
    return [line for line in value.splitlines() if line]


def _repository_slug(
    runner: CommandRunner,
    explicit: str | None,
    warnings: list[dict[str, Any]],
) -> str | None:
    if explicit:
        return explicit
    remote = _git_value(
        runner,
        ["remote", "get-url", "origin"],
        command_id="git-origin-url",
        warnings=warnings,
    )
    if remote:
        slug = parse_repository_slug(remote)
        if slug:
            return slug
    result = runner.run(
        ["gh", "repo", "view", "--json", "nameWithOwner"],
        command_id="gh-repo-view",
    )
    if result.returncode != 0:
        warnings.append(command_warning(result))
        return None
    value = read_json_text(result.stdout, field="gh repo view")
    if isinstance(value, dict) and isinstance(value.get("nameWithOwner"), str):
        return str(value["nameWithOwner"])
    return None


def _git_snapshot(
    runner: CommandRunner,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    read_only_local_refs = os.environ.get("WORKFLOW_EVIDENCE_READ_ONLY") == "1"
    fetch: CommandResult | None = None
    if not read_only_local_refs:
        fetch = runner.run(
            ["git", "fetch", "--prune", "origin"],
            command_id="git-fetch-origin",
        )
        if fetch.returncode != 0:
            warnings.append(command_warning(fetch))
    branch = _git_value(
        runner,
        ["branch", "--show-current"],
        command_id="git-current-branch",
        warnings=warnings,
    )
    head = _git_value(
        runner,
        ["rev-parse", "HEAD"],
        command_id="git-head",
        warnings=warnings,
    )
    local_main = _git_value(
        runner,
        ["rev-parse", "refs/heads/main"],
        command_id="git-local-main",
        warnings=warnings,
    )
    origin_main = _git_value(
        runner,
        ["rev-parse", "refs/remotes/origin/main"],
        command_id="git-origin-main",
        warnings=warnings,
    )
    status_lines = _git_lines(
        runner,
        ["status", "--short", "--untracked-files=all"],
        command_id="git-status",
        warnings=warnings,
    )
    staged = _git_lines(
        runner,
        ["diff", "--cached", "--name-only"],
        command_id="git-staged-files",
        warnings=warnings,
    )
    changed = _git_lines(
        runner,
        ["diff", "--name-only"],
        command_id="git-changed-files",
        warnings=warnings,
    )
    worktrees = _git_lines(
        runner,
        ["worktree", "list", "--porcelain"],
        command_id="git-worktrees",
        warnings=warnings,
    )
    worktree_branches = sorted(
        line.removeprefix("branch refs/heads/")
        for line in worktrees
        if line.startswith("branch refs/heads/")
    )
    return {
        "origin_fetch": (
            "pass"
            if read_only_local_refs
            else "pass"
            if fetch is not None and fetch.returncode == 0
            else "unknown"
        ),
        "origin_refresh": (
            "skipped-read-only" if read_only_local_refs else "attempted"
        ),
        "branch": safe_text(branch),
        "head_sha": head if is_sha(head) else None,
        "local_main_sha": local_main if is_sha(local_main) else None,
        "origin_main_sha": origin_main if is_sha(origin_main) else None,
        "clean": len(status_lines) == 0,
        "status_entries": len(status_lines),
        "staged_files": bounded_list(staged, item_limit=MAX_FILES),
        "changed_files": bounded_list(changed, item_limit=MAX_FILES),
        "worktree_count": sum(1 for line in worktrees if line.startswith("worktree ")),
        "worktree_branches": bounded_list(worktree_branches, item_limit=MAX_FILES),
    }


def _issue_view(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    fields = (
        "number,title,body,comments,state,labels,projectItems,url,closedAt,"
        "closedByPullRequestsReferences"
    )
    result = runner.run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repository,
            "--json",
            fields,
        ],
        command_id=f"gh-issue-view-{number}",
    )
    if result.returncode != 0:
        fallback = runner.run(
            [
                "gh",
                "issue",
                "view",
                str(number),
                "--repo",
                repository,
                "--json",
                "number,title,body,state,labels,url,closedAt",
            ],
            command_id=f"gh-issue-view-fallback-{number}",
        )
        if fallback.returncode != 0:
            warnings.append(command_warning(result))
            warnings.append(command_warning(fallback))
            return None
        result = fallback
    value = read_json_text(result.stdout, field=f"Issue #{number}")
    if not isinstance(value, dict):
        warnings.append(
            {
                "command_id": result.command_id,
                "exit_code": 0,
                "error": "Issue response is not an object",
            }
        )
        return None
    labels = value.get("labels", [])
    normalized_labels: list[str] = []
    if isinstance(labels, list):
        for item in labels:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                normalized_labels.append(item["name"])
            elif isinstance(item, str):
                normalized_labels.append(item)
    pull_refs: list[dict[str, Any]] = []
    raw_pull_refs = value.get("closedByPullRequestsReferences", [])
    if isinstance(raw_pull_refs, list):
        for pr in raw_pull_refs:
            if not isinstance(pr, dict):
                continue
            pull_refs.append(
                {
                    "number": pr.get("number"),
                    "state": safe_text(pr.get("state")),
                    "merged": pr.get("merged")
                    if isinstance(pr.get("merged"), bool)
                    else None,
                    "merged_at": safe_text(pr.get("mergedAt")),
                    "url": safe_text(pr.get("url")),
                    "repository": _repository_name(pr),
                }
            )
    body = value.get("body") if isinstance(value.get("body"), str) else None
    raw_comments = value.get("comments", [])
    comment_facts: list[dict[str, Any]] = []
    if isinstance(raw_comments, list):
        for comment in raw_comments:
            if not isinstance(comment, dict):
                continue
            author = comment.get("author")
            comment_facts.append(
                {
                    "author": author.get("login") if isinstance(author, dict) else None,
                    "created_at": comment.get("createdAt"),
                    "updated_at": comment.get("updatedAt"),
                    "body": comment.get("body")
                    if isinstance(comment.get("body"), str)
                    else None,
                }
            )
    content_facts = {"body": body, "comments": comment_facts}
    issue = {
        "number": value.get("number"),
        "title": safe_text(value.get("title")),
        "content_sha256": sha256_json(content_facts),
        "body_characters": len(body) if body is not None else None,
        "comment_count": len(comment_facts),
        "state": safe_text(value.get("state")),
        "labels": bounded_list(sorted(normalized_labels)),
        "project_status": _find_project_status(value.get("projectItems")),
        "url": safe_text(value.get("url")),
        "closed_at": safe_text(value.get("closedAt")),
        "closing_pull_requests": bounded_list(pull_refs),
    }
    issue["issue_closure"] = _issue_closure_snapshot(
        runner, repository, number, warnings
    )
    return issue


def _find_project_status(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() == "status":
                if isinstance(nested, dict) and isinstance(nested.get("name"), str):
                    return safe_text(nested["name"])
                if isinstance(nested, str):
                    return safe_text(nested)
            result = _find_project_status(nested)
            if result:
                return result
    if isinstance(value, list):
        for nested in value:
            result = _find_project_status(nested)
            if result:
                return result
    return None


def _graphql(
    runner: CommandRunner,
    repository: str,
    number: int,
    query: str,
    *,
    command_id: str,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    owner, name = repository.split("/", 1)
    result = runner.run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={' '.join(query.split())}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ],
        command_id=command_id,
    )
    if result.returncode != 0:
        warnings.append(command_warning(result))
        return None
    value = read_json_text(result.stdout, field=command_id)
    if not isinstance(value, dict):
        warnings.append(
            {
                "command_id": command_id,
                "exit_code": 0,
                "error": "GraphQL response is not an object",
            }
        )
        return None
    errors = value.get("errors")
    if errors:
        warnings.append(
            {
                "command_id": command_id,
                "exit_code": 0,
                "error": safe_text(errors, limit=1000),
            }
        )
        return None
    return value


def _repository_name(value: Mapping[str, Any]) -> str | None:
    repository = value.get("repository")
    if isinstance(repository, Mapping):
        return safe_text(repository.get("nameWithOwner"))
    return None


def _normalize_closing_pr(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "number": value.get("number"),
        "state": safe_text(value.get("state")),
        "merged": value.get("merged")
        if isinstance(value.get("merged"), bool)
        else None,
        "merged_at": safe_text(value.get("mergedAt")),
        "url": safe_text(value.get("url")),
        "repository": _repository_name(value),
    }


def _issue_closure_snapshot(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    value = _graphql(
        runner,
        repository,
        number,
        ISSUE_CLOSURE_QUERY,
        command_id=f"gh-issue-closure-{number}",
        warnings=warnings,
    )
    if not isinstance(value, dict):
        return {
            "status": "unknown",
            "reason": "issue-closure-query-unavailable",
            "evidence_status": "partial",
        }
    data = value.get("data")
    repo = data.get("repository") if isinstance(data, Mapping) else None
    issue = repo.get("issue") if isinstance(repo, Mapping) else None
    if not isinstance(issue, Mapping):
        return {
            "status": "unknown",
            "reason": "issue-closure-metadata-unavailable",
            "evidence_status": "partial",
        }

    refs = issue.get("closedByPullRequestsReferences")
    if isinstance(refs, Mapping):
        ref_nodes = refs.get("nodes")
    elif isinstance(refs, list):
        ref_nodes = refs
    else:
        ref_nodes = []
    ref_items = (
        [
            normalized
            for normalized in (_normalize_closing_pr(item) for item in ref_nodes)
            if normalized is not None
        ]
        if isinstance(ref_nodes, list)
        else []
    )
    ref_page = refs.get("pageInfo") if isinstance(refs, Mapping) else None
    refs_truncated = (
        isinstance(ref_page, Mapping) and ref_page.get("hasNextPage") is True
    )

    timeline = issue.get("timelineItems")
    timeline_nodes = timeline.get("nodes") if isinstance(timeline, Mapping) else []
    timeline_page = timeline.get("pageInfo") if isinstance(timeline, Mapping) else None
    timeline_truncated = (
        isinstance(timeline_page, Mapping)
        and timeline_page.get("hasPreviousPage") is True
    )

    events: list[dict[str, Any]] = []
    if isinstance(timeline_nodes, list):
        for item in timeline_nodes:
            if not isinstance(item, Mapping):
                continue
            typename = safe_text(item.get("__typename"))
            created_at = safe_text(item.get("createdAt"))
            if typename == "ClosedEvent":
                closer = item.get("closer")
                closer_type = (
                    safe_text(closer.get("__typename"))
                    if isinstance(closer, Mapping)
                    else None
                )
                event: dict[str, Any] = {
                    "type": "closed",
                    "created_at": created_at,
                    "closer_type": closer_type,
                }
                if closer_type == "PullRequest" and isinstance(closer, Mapping):
                    event["closer"] = _normalize_closing_pr(closer)
                events.append(event)
            elif typename == "ReopenedEvent":
                events.append({"type": "reopened", "created_at": created_at})

    latest_closure: dict[str, Any] | None = None
    for event in sorted(events, key=lambda item: str(item.get("created_at") or "")):
        if event.get("type") == "closed":
            latest_closure = event
        elif event.get("type") == "reopened":
            latest_closure = None

    state = safe_text(issue.get("state"))
    closed_at = safe_text(issue.get("closedAt"))
    evidence_status = "complete"
    reason = "closed-by-pr"
    status = "closed-by-pr"
    if refs_truncated:
        evidence_status = "partial"
        reason = "closing-pr-references-truncated"
        status = "unknown"
    elif timeline_truncated:
        evidence_status = "partial"
        reason = "timeline-truncated"
        status = "unknown"
    elif state != "CLOSED":
        reason = "issue-not-closed"
        status = "not-closed"
    elif latest_closure is None:
        evidence_status = "partial"
        reason = "latest-effective-close-event-unavailable"
        status = "unknown"
    elif latest_closure.get("closer_type") != "PullRequest":
        reason = "latest-closer-is-not-pull-request"
        status = "not-pr-closer"
    elif not isinstance(latest_closure.get("closer"), Mapping):
        evidence_status = "partial"
        reason = "latest-closer-pr-metadata-unavailable"
        status = "unknown"

    closer = (
        latest_closure.get("closer") if isinstance(latest_closure, Mapping) else None
    )
    return {
        "status": status,
        "reason": reason,
        "state": state,
        "closed_at": closed_at,
        "latest_effective_event": latest_closure,
        "closer_type": latest_closure.get("closer_type")
        if isinstance(latest_closure, Mapping)
        else None,
        "closer_repository": closer.get("repository")
        if isinstance(closer, Mapping)
        else None,
        "closer_number": closer.get("number") if isinstance(closer, Mapping) else None,
        "closed_by_pull_requests": bounded_list(ref_items),
        "evidence_status": evidence_status,
        "evidence_complete": evidence_status == "complete",
        "conflict": False,
    }


def _relationship_snapshot(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    value = _graphql(
        runner,
        repository,
        number,
        RELATIONSHIPS_QUERY,
        command_id=f"gh-issue-relationships-{number}",
        warnings=warnings,
    )
    issue = None
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, dict):
            repo = data.get("repository")
            if isinstance(repo, dict):
                issue = repo.get("issue")
    if not isinstance(issue, dict):
        return {
            "available": False,
            "issue_type": None,
            "parent": None,
            "sub_issues": bounded_list([]),
            "sub_issue_set_digest": None,
            "sub_issues_complete": False,
            "blocked_by": bounded_list([]),
            "blocking": bounded_list([]),
        }

    def _nodes(field: str) -> list[dict[str, Any]]:
        connection = issue.get(field)
        if not isinstance(connection, dict):
            return []
        nodes = connection.get("nodes", [])
        if not isinstance(nodes, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in nodes:
            if not isinstance(item, dict):
                continue
            labels: list[str] = []
            raw_labels = item.get("labels")
            if isinstance(raw_labels, dict) and isinstance(
                raw_labels.get("nodes"), list
            ):
                labels = [
                    label["name"]
                    for label in raw_labels["nodes"]
                    if isinstance(label, dict) and isinstance(label.get("name"), str)
                ]
            normalized.append(
                {
                    "number": item.get("number"),
                    "title": safe_text(item.get("title")),
                    "state": safe_text(item.get("state")),
                    "labels": sorted(labels),
                }
            )
        return normalized

    parent = issue.get("parent")
    normalized_parent = None
    if isinstance(parent, dict):
        normalized_parent = {
            "number": parent.get("number"),
            "title": safe_text(parent.get("title")),
            "state": safe_text(parent.get("state")),
        }
    issue_type = issue.get("issueType")
    sub_issue_nodes = _nodes("subIssues")
    sub_connection = issue.get("subIssues")
    sub_page = (
        sub_connection.get("pageInfo") if isinstance(sub_connection, dict) else None
    )
    sub_has_next = (
        sub_page.get("hasNextPage") is True if isinstance(sub_page, dict) else False
    )
    sub_bounded = bounded_list(sub_issue_nodes, item_limit=MAX_CHILDREN)

    def _bounded_connection(field: str) -> dict[str, Any]:
        connection = issue.get(field)
        nodes = _nodes(field)
        bounded = bounded_list(nodes)
        page = connection.get("pageInfo") if isinstance(connection, dict) else None
        if isinstance(page, dict) and page.get("hasNextPage") is True:
            bounded["truncated"] = True
        return bounded

    return {
        "available": True,
        "issue_type": safe_text(issue_type.get("name"))
        if isinstance(issue_type, dict)
        else None,
        "parent": normalized_parent,
        "sub_issues": sub_bounded,
        "sub_issue_set_digest": sha256_json(
            sorted(
                item.get("number")
                for item in sub_issue_nodes
                if isinstance(item.get("number"), int)
            )
        ),
        "sub_issues_complete": not sub_has_next and not sub_bounded["truncated"],
        "blocked_by": _bounded_connection("blockedBy"),
        "blocking": _bounded_connection("blocking"),
    }


def _pr_view(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    fields = (
        "number,title,body,state,isDraft,url,baseRefName,baseRefOid,headRefName,headRefOid,"
        "mergeCommit,mergedAt,mergeable,reviewDecision,files,commits,statusCheckRollup,"
        "reviews,closingIssuesReferences,headRepository"
    )
    result = runner.run(
        ["gh", "pr", "view", str(number), "--repo", repository, "--json", fields],
        command_id=f"gh-pr-view-{number}",
    )
    if result.returncode != 0:
        warnings.append(command_warning(result))
        return None
    value = read_json_text(result.stdout, field=f"PR #{number}")
    if not isinstance(value, dict):
        warnings.append(
            {
                "command_id": result.command_id,
                "exit_code": 0,
                "error": "PR response is not an object",
            }
        )
        return None
    files: list[str] = []
    raw_files = value.get("files", [])
    if isinstance(raw_files, list):
        for item in raw_files:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                files.append(str(item["path"]))
    commits: list[dict[str, Any]] = []
    raw_commits = value.get("commits", [])
    if isinstance(raw_commits, list):
        for item in raw_commits:
            if not isinstance(item, dict):
                continue
            commits.append(
                {
                    "oid": safe_text(item.get("oid"), limit=64),
                    "headline": safe_text(item.get("messageHeadline"), limit=160),
                }
            )
    closing_issues: list[int] = []
    raw_closing = value.get("closingIssuesReferences", [])
    if isinstance(raw_closing, list):
        closing_issues = [
            item["number"]
            for item in raw_closing
            if isinstance(item, dict) and isinstance(item.get("number"), int)
        ]
    reviews: list[dict[str, Any]] = []
    raw_reviews = value.get("reviews", [])
    if isinstance(raw_reviews, list):
        for item in raw_reviews:
            if not isinstance(item, dict):
                continue
            author = item.get("author")
            reviews.append(
                {
                    "state": safe_text(item.get("state")),
                    "author": safe_text(author.get("login"))
                    if isinstance(author, dict)
                    else None,
                    "submitted_at": safe_text(item.get("submittedAt")),
                }
            )
    checks = _normalize_checks(value.get("statusCheckRollup"))
    merge_commit = value.get("mergeCommit")
    head_repo = value.get("headRepository")
    return {
        "number": value.get("number"),
        "title": safe_text(value.get("title")),
        "content_sha256": sha256_json(
            {"body": value.get("body") if isinstance(value.get("body"), str) else None}
        ),
        "body_characters": len(value["body"])
        if isinstance(value.get("body"), str)
        else None,
        "state": safe_text(value.get("state")),
        "is_draft": value.get("isDraft"),
        "url": safe_text(value.get("url")),
        "base_branch": safe_text(value.get("baseRefName")),
        "base_sha": value.get("baseRefOid")
        if is_sha(value.get("baseRefOid"))
        else None,
        "head_branch": safe_text(value.get("headRefName")),
        "head_sha": value.get("headRefOid")
        if is_sha(value.get("headRefOid"))
        else None,
        "merge_commit_sha": merge_commit.get("oid")
        if isinstance(merge_commit, dict) and is_sha(merge_commit.get("oid"))
        else None,
        "merged_at": safe_text(value.get("mergedAt")),
        "mergeable": safe_text(value.get("mergeable")),
        "review_decision": safe_text(value.get("reviewDecision")),
        "changed_files": bounded_list(files, item_limit=MAX_FILES),
        "commits": bounded_list(commits),
        "closing_issues": bounded_list(sorted(closing_issues)),
        "checks": checks,
        "reviews": bounded_list(reviews),
        "head_repository": safe_text(head_repo.get("nameWithOwner"))
        if isinstance(head_repo, dict)
        else None,
    }


def _normalize_checks(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {
            "count": 0,
            "success": 0,
            "pending": 0,
            "failed": 0,
            "skipped_or_unknown": 0,
            "all_success": None,
            "items": bounded_list([]),
        }
    items: list[dict[str, Any]] = []
    success = pending = failed = skipped = 0
    for item in value:
        if not isinstance(item, dict):
            continue
        name = (
            item.get("name")
            or item.get("context")
            or item.get("workflowName")
            or "unnamed"
        )
        state = (
            item.get("conclusion")
            or item.get("state")
            or item.get("status")
            or "UNKNOWN"
        )
        normalized_state = str(state).upper()
        if normalized_state in {"SUCCESS", "NEUTRAL"}:
            success += 1
            category = "success"
        elif normalized_state in {
            "PENDING",
            "QUEUED",
            "IN_PROGRESS",
            "EXPECTED",
            "WAITING",
        }:
            pending += 1
            category = "pending"
        elif normalized_state in {
            "FAILURE",
            "ERROR",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
        }:
            failed += 1
            category = "failed"
        else:
            skipped += 1
            category = "skipped-or-unknown"
        items.append(
            {
                "name": safe_text(name, limit=160),
                "state": safe_text(normalized_state, limit=64),
                "category": category,
            }
        )
    count = len(items)
    all_success: bool | None
    if count == 0:
        all_success = None
    else:
        all_success = success == count
    return {
        "count": count,
        "success": success,
        "pending": pending,
        "failed": failed,
        "skipped_or_unknown": skipped,
        "all_success": all_success,
        "items": bounded_list(items),
    }


def _required_checks(
    runner: CommandRunner,
    repository: str,
    branch: str | None,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    if not branch:
        return {"configuration": "unknown", "contexts": bounded_list([])}
    result = runner.run(
        [
            "gh",
            "api",
            f"repos/{repository}/branches/{branch}/protection/required_status_checks",
        ],
        command_id=f"gh-required-checks-{branch}",
    )
    if result.returncode != 0:
        combined = f"{result.stderr}\n{result.stdout}".casefold()
        failure = _classify_required_checks_failure(result)
        if failure["category"] == "plan-limit":
            return {
                "configuration": "plan-limited-403",
                "failure": failure,
                "contexts": bounded_list([]),
            }
        if "404" in combined:
            return {"configuration": "not-configured", "contexts": bounded_list([])}
        warnings.append(command_warning(result))
        return {
            "configuration": "unknown",
            "failure": failure,
            "contexts": bounded_list([]),
        }
    value = read_json_text(result.stdout, field="required checks")
    contexts: list[str] = []
    if isinstance(value, dict):
        raw = value.get("contexts", [])
        if isinstance(raw, list):
            contexts = [item for item in raw if isinstance(item, str)]
        checks = value.get("checks", [])
        if isinstance(checks, list):
            for item in checks:
                if isinstance(item, dict) and isinstance(item.get("context"), str):
                    contexts.append(item["context"])
    return {
        "configuration": "available" if contexts else "configured-empty",
        "contexts": bounded_list(sorted(set(contexts))),
    }


def _remote_branch_tip(value: str | None) -> str | None:
    if not value:
        return None
    for line in value.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and is_sha(parts[0]):
            return parts[0]
    return None


def _gate_pass(gates: Mapping[str, Any], name: str) -> bool:
    value = gates.get(name)
    return isinstance(value, dict) and value.get("status") == "pass"


def _checks_cleanup_status(pr: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(pr, Mapping):
        return False, "PR metadata unavailable"
    checks = pr.get("checks")
    if not isinstance(checks, Mapping):
        return False, "check run metadata unavailable"
    if checks.get("count") == 0:
        return False, "no applicable check runs observed"
    if checks.get("all_success") is not True:
        return False, "not all observed check runs reached a successful terminal state"
    items = checks.get("items")
    if not isinstance(items, Mapping) or items.get("count", 0) <= 0:
        return False, "check run item details unavailable"
    raw_items = items.get("items")
    check_items = raw_items if isinstance(raw_items, list) else []
    has_quality_gate = any(
        isinstance(item, Mapping) and str(item.get("name", "")).casefold() == "quality"
        for item in check_items
    )
    if not has_quality_gate:
        return False, "expected quality check run was not observed"
    return True, "all observed check runs successful"


def _lifecycle_labels_gate(labels: set[str], *, required_label: str) -> dict[str, Any]:
    lifecycle = labels & LIFECYCLE_LABELS
    if lifecycle == {required_label}:
        return _gate("pass", f"lifecycle labels: {sorted(lifecycle)}")
    detail = f"observed lifecycle labels: {sorted(lifecycle) or 'none'}"
    return _gate("fail", detail)


def _project_status_gate(
    status: str | None, *, entry_point: str | None = None
) -> dict[str, Any]:
    if status is None:
        return _gate("unknown", "Project Status unavailable")
    if status not in KNOWN_PROJECT_STATUSES:
        return _gate("fail", f"unknown Project Status: {status!r}")
    if entry_point is None:
        return _gate("pass", f"Project Status: {status}")
    compatible = {
        "delivery-start": {"Ready", "In Progress"},
        "implementation": {"Ready", "In Progress"},
        "final-validation": {"Ready", "In Progress"},
        "pr-readiness": {"Ready", "In Progress"},
        "review-remediation": {"Review"},
    }.get(entry_point, set())
    if status in compatible:
        return _gate("pass", f"Project Status {status} compatible with {entry_point}")
    return _gate(
        "fail",
        f"Project Status {status} not compatible with entry point {entry_point}",
    )


def _parent_state_gate(relationships: Mapping[str, Any]) -> dict[str, Any]:
    if relationships.get("available") is not True:
        return _gate("unknown", "Relationship facts unavailable")
    parent = relationships.get("parent")
    if not isinstance(parent, dict):
        return _gate("pass", "no parent")
    parent_state = parent.get("state")
    if not isinstance(parent_state, str):
        return _gate("unknown", "parent state unavailable")
    if parent_state.upper() == "OPEN":
        return _gate(
            "pass",
            f"parent #{parent.get('number')} is OPEN",
        )
    if parent_state.upper() == "CLOSED":
        return _gate(
            "fail",
            f"parent #{parent.get('number')} is CLOSED",
        )
    return _gate("unknown", f"parent state: {parent_state}")


def _validate_delivery_entry_args(args: argparse.Namespace) -> None:
    entry_point = args.entry_point
    allowed = DELIVERY_ENTRY_PARAMS[entry_point]
    supplied = {
        name for name in DELIVERY_PARAM_SPACE if getattr(args, name, None) is not None
    }
    missing = sorted(allowed - supplied)
    extra = sorted(supplied - allowed)
    if missing or extra:
        raise WorkflowToolError(
            f"delivery entry-point {entry_point} parameter contract violation: "
            f"missing={missing or 'none'} extra={extra or 'none'}"
        )
    if args.branch is not None:
        value = str(args.branch)
        if (
            not value
            or len(value) > 255
            or value[0]
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            or not all(c in BRANCH_NAME_ALLOWED for c in value)
            or ".." in value
            or "@{" in value
            or value.endswith(".")
            or value.endswith("/")
            or value.endswith(".lock")
        ):
            raise WorkflowToolError(f"invalid branch name: {value!r}")


def _task_branch_identity_gate(branch: Any, task: int) -> dict[str, Any]:
    """Require a branch name that carries the current Task identity.

    New branches use ``task/<number>-<slug>``.  The two historical numeric
    forms remain acceptable for safe reuse, but a branch with no Task identity
    is never eligible for bootstrap or implementation admission.
    """
    if not isinstance(branch, str) or not branch:
        return _gate("unknown", "Task branch name unavailable")
    valid_names = (
        f"task/{task}",
        f"{task}-",
        f"task-{task}-",
    )
    if (
        branch == valid_names[0]
        or branch.startswith(f"task/{task}-")
        or branch.startswith(valid_names[1])
        or branch == f"task-{task}"
        or branch.startswith(valid_names[2])
    ):
        return _gate("pass", f"branch carries Task #{task} identity")
    return _gate("fail", f"branch {branch!r} does not identify Task #{task}")


def _is_canonical_new_task_branch(branch: str, task: int) -> bool:
    """Return whether *branch* is valid for creating a new Task branch."""

    prefix = f"task/{task}-"
    return branch.startswith(prefix) and len(branch) > len(prefix)


def _review_skill_identity_from_args(
    args: argparse.Namespace, repo_root: Path
) -> dict[str, str] | None:
    """Validate and hash the canonical Review Skill supplied by a Runner."""

    raw_path = getattr(args, "review_skill_path", None)
    raw_sha256 = getattr(args, "review_skill_sha256", None)
    if raw_path is None and raw_sha256 is None:
        return None
    if (
        not isinstance(raw_path, str)
        or raw_path not in CANONICAL_REVIEW_SKILL_PATHS
        or not _is_sha256_digest(raw_sha256)
    ):
        raise WorkflowToolError("Review Skill identity is malformed or noncanonical")
    path = repo_root / raw_path
    if path.is_symlink() or not path.is_file():
        raise WorkflowToolError("canonical Review Skill file is missing or unsafe")
    try:
        actual_sha256 = sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise WorkflowToolError("canonical Review Skill file is unreadable") from exc
    if actual_sha256 != raw_sha256:
        raise WorkflowToolError("canonical Review Skill content address mismatches")
    return {"path": raw_path, "sha256": raw_sha256}


def _worktree_branch_items(git: Mapping[str, Any]) -> list[str]:
    raw = git.get("worktree_branches")
    items = raw.get("items") if isinstance(raw, Mapping) else []
    return (
        [item for item in items if isinstance(item, str)]
        if isinstance(items, list)
        else []
    )


def _branch_bootstrap_gate(
    *,
    git: Mapping[str, Any],
    branch: str | None,
    task: int,
    expected_main_sha: str | None,
    expected_base_sha: str | None,
    branch_exists: bool,
    local_tip: str | None,
    remote_tip: str | None,
    remote_available: bool,
) -> dict[str, Any]:
    """Decide whether implementation may create or reuse the Task branch.

    This is intentionally read-only.  The Delivery Skill performs the actual
    ``git switch -c`` or reuse after this gate passes.
    """
    identity = _task_branch_identity_gate(branch, task)
    if identity.get("status") != "pass":
        return identity
    if not isinstance(branch, str):
        return _gate("unknown", "Task branch name unavailable")

    worktree_items = _worktree_branch_items(git)
    reasons: list[str] = []
    if branch in worktree_items and git.get("branch") != branch:
        reasons.append("target branch is occupied by another worktree")
    if git.get("clean") is not True:
        reasons.append("branch setup requires a clean worktree")
    if not branch_exists:
        if not _is_canonical_new_task_branch(branch, task):
            return _gate(
                "fail",
                "new Task branches must use canonical task/<issue>-<slug> naming",
            )
        if not remote_available:
            reasons.append("remote branch existence unavailable")
        elif remote_tip is not None:
            reasons.append("remote branch already exists; ownership is ambiguous")
        if git.get("branch") != "main":
            reasons.append("bootstrap requires current branch main")
        if git.get("head_sha") != expected_main_sha:
            reasons.append("current HEAD does not equal expected main SHA")
        if git.get("local_main_sha") != expected_main_sha:
            reasons.append("local main does not equal expected main SHA")
        if git.get("origin_main_sha") != expected_main_sha:
            reasons.append("origin/main does not equal expected main SHA")
        if expected_base_sha != expected_main_sha:
            reasons.append("expected Task base is not the locked main SHA")
        if reasons:
            return _gate("fail", "; ".join(reasons))
        return _gate("pass", f"branch {branch!r} absent; safe to bootstrap")

    if not remote_available:
        return _gate("unknown", "existing branch remote state unavailable")
    if local_tip is None:
        return _gate("unknown", "existing branch tip unavailable")
    if remote_tip is not None and local_tip is not None and remote_tip != local_tip:
        reasons.append("local and remote Task branch tips differ")
    if reasons:
        return _gate("fail", "; ".join(reasons))
    return _gate("pass", f"existing branch {branch!r} is eligible for idempotent reuse")


def _active_pr_identity_gate(
    runner: CommandRunner,
    repository: str,
    *,
    branch: str,
    task: int,
    expected_base_sha: str,
    expected_head_sha: str | None,
    branch_tip: str | None,
    expected_pr: int | None,
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Check active PR ownership before implementation may write."""

    result = runner.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--head",
            branch,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,closingIssuesReferences",
        ],
        command_id="gh-active-pr-identity",
    )
    if result.returncode != 0:
        warnings.append(command_warning(result))
        return _gate("unknown", "active PR identity unavailable"), []
    raw = read_json_text(result.stdout, field="active PR identity")
    if not isinstance(raw, list):
        return _gate("unknown", "active PR identity response is malformed"), []
    if len(raw) >= 100:
        return _gate("fail", "active PR inventory may be truncated"), []

    active: list[dict[str, Any]] = [
        dict(item) for item in raw if isinstance(item, Mapping)
    ]
    if not active:
        return _gate("pass", "no active PR for existing Task branch"), active
    if len(active) != 1:
        return (
            _gate(
                "fail",
                f"multiple active PRs for branch {branch!r}: "
                f"{[item.get('number') for item in active]}",
            ),
            active,
        )

    pr = active[0]
    closing = pr.get("closingIssuesReferences")
    closing_numbers = (
        [
            item.get("number")
            for item in closing
            if isinstance(item, Mapping) and isinstance(item.get("number"), int)
        ]
        if isinstance(closing, list)
        else []
    )
    failures: list[str] = []
    if pr.get("number") != expected_pr and expected_pr is not None:
        failures.append(f"expected PR #{expected_pr}, observed #{pr.get('number')}")
    if pr.get("headRefName") != branch:
        failures.append(f"head branch={pr.get('headRefName')}")
    if pr.get("baseRefName") != "main":
        failures.append(f"base branch={pr.get('baseRefName')}")
    if pr.get("baseRefOid") != expected_base_sha:
        failures.append(f"base SHA={pr.get('baseRefOid')}")
    if expected_head_sha is not None and pr.get("headRefOid") != expected_head_sha:
        failures.append(f"head SHA={pr.get('headRefOid')}")
    if (
        expected_head_sha is None
        and branch_tip is not None
        and pr.get("headRefOid") != branch_tip
    ):
        failures.append(f"head SHA={pr.get('headRefOid')}, branch tip={branch_tip}")
    if expected_head_sha is None and branch_tip is None:
        failures.append("branch tip unavailable for active PR identity")
    if closing_numbers != [task]:
        failures.append(f"closing Issues={closing_numbers}")
    if failures:
        return _gate("fail", "; ".join(failures)), active
    return _gate(
        "pass", f"active PR #{pr.get('number')} belongs to Task #{task}"
    ), active


def _review_handoff_digest(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "evidence_id"}
    return sha256_json(unsigned)


def _safe_handoff_staging_path(repo_root: Path, raw_path: str) -> Path:
    if not raw_path or raw_path.startswith("/") or "\\" in raw_path:
        raise WorkflowToolError("handoff payload path must be repository-relative")
    path = Path(raw_path)
    if ".." in path.parts:
        raise WorkflowToolError("handoff payload path traversal is not allowed")
    root_path = _ensure_evidence_root(repo_root)
    if root_path.is_symlink():
        raise WorkflowToolError("ignored evidence root must not be a symlink")
    root = root_path.resolve()
    candidate = repo_root / path
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise WorkflowToolError(
            "handoff payload must be inside the ignored evidence root"
        ) from exc
    current = candidate
    while current != root_path:
        if current.is_symlink():
            raise WorkflowToolError("handoff payload path must not use a symlink")
        current = current.parent
    if candidate.is_symlink():
        raise WorkflowToolError("handoff payload path must not be a symlink")
    return candidate


def _ensure_evidence_root(repo_root: Path) -> Path:
    """Return the ignored evidence root without traversing a symlink."""

    root_path = repo_root / EVIDENCE_ROOT
    current = root_path
    while current != repo_root:
        if current.is_symlink():
            raise WorkflowToolError("ignored evidence ancestry must not use a symlink")
        current = current.parent
    root_path = require_exact_ignored_directory(repo_root, EVIDENCE_ROOT)
    return root_path


def _ensure_handoff_directory(root: Path) -> Path:
    """Create the handoff directory only when its complete ancestry is safe."""

    directory = root / REVIEW_HANDOFF_SUBDIR
    if directory.is_symlink():
        raise WorkflowToolError("review handoff directory must not be a symlink")
    directory.mkdir(parents=False, exist_ok=True)
    if directory.is_symlink():
        raise WorkflowToolError("review handoff directory must not be a symlink")
    current = directory
    while current != root:
        if current.is_symlink():
            raise WorkflowToolError("review handoff ancestry must not use a symlink")
        current = current.parent
    return directory


def _materialize_review_handoff(
    repo_root: Path, payload: Mapping[str, Any]
) -> tuple[str, Path]:
    """Materialize one content-addressed handoff in ignored local evidence."""

    if not isinstance(payload, Mapping):
        raise WorkflowToolError("review handoff payload must be a JSON object")
    unsigned = dict(payload)
    unsigned.pop("evidence_id", None)
    evidence_id = sha256_json(unsigned)
    supplied_id = payload.get("evidence_id")
    if supplied_id is not None and supplied_id != evidence_id:
        raise WorkflowToolError("review handoff evidence_id does not match content")
    artifact = dict(unsigned)
    artifact["evidence_id"] = evidence_id

    root = _ensure_evidence_root(repo_root)
    directory = _ensure_handoff_directory(root)
    destination = directory / f"{evidence_id}.json"
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink():
            raise WorkflowToolError("review handoff destination must not be a symlink")
        existing = read_json_file(destination)
        if (
            not isinstance(existing, Mapping)
            or _review_handoff_digest(existing) != evidence_id
        ):
            raise WorkflowToolError(
                "review handoff destination has conflicting content"
            )
        if dict(existing) != artifact:
            raise WorkflowToolError("review handoff destination is not canonical")
        return evidence_id, destination

    atomic_write_json(destination, artifact)
    return evidence_id, destination


def _emit_review_terminal(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    """Finalize a review only after the stable evidence chain is revalidated."""

    if not is_sha(args.expected_base_sha) or not is_sha(args.expected_head_sha):
        raise WorkflowToolError("review terminal base/head identity is malformed")
    if not _is_sha256_digest(args.effective_diff_sha256):
        raise WorkflowToolError("review terminal effective diff identity is malformed")

    payload_path = _safe_handoff_staging_path(repo_root, args.payload)
    payload = read_json_file(payload_path)
    if not isinstance(payload, Mapping):
        raise WorkflowToolError("review terminal payload must be a JSON object")
    root = _ensure_evidence_root(repo_root)
    terminal_skill = _review_skill_identity_from_args(args, repo_root)

    initial, initial_error = _read_review_snapshot(
        root, args.review_snapshot_id, operation="pr-review-snapshot"
    )
    recheck, recheck_error = _read_review_snapshot(
        root, args.recheck_snapshot_id, operation="pr-review-recheck"
    )
    problems: list[str] = []
    if initial_error:
        problems.append(f"initial review snapshot: {initial_error}")
    if recheck_error:
        problems.append(f"final recheck snapshot: {recheck_error}")
    for snapshot, label in (
        (initial, "initial review snapshot"),
        (recheck, "final recheck snapshot"),
    ):
        for detail in _snapshot_identity_problems(
            snapshot,
            task=args.task,
            pr=args.pr,
            base_sha=args.expected_base_sha,
            head_sha=args.expected_head_sha,
            effective_diff_sha256=args.effective_diff_sha256,
        ):
            problems.append(f"{label}: {detail}")
    if not (
        isinstance(recheck, Mapping)
        and isinstance(recheck.get("stability"), Mapping)
        and recheck["stability"].get("stable") is True
        and isinstance(recheck.get("gates"), Mapping)
        and isinstance(recheck["gates"].get("snapshot_stability"), Mapping)
        and recheck["gates"]["snapshot_stability"].get("status") == "pass"
    ):
        problems.append("final recheck did not prove stability")

    initial_skill = (
        initial.get("review_skill") if isinstance(initial, Mapping) else None
    )
    recheck_skill = (
        recheck.get("review_skill") if isinstance(recheck, Mapping) else None
    )
    if initial_skill != recheck_skill:
        problems.append("initial and recheck Review Skill identities differ")
    if terminal_skill is not None and initial_skill != terminal_skill:
        problems.append("snapshot Review Skill identity differs from terminal Skill")

    # Recollect once after the supplied recheck. This closes the timing gap
    # between the Review recheck and terminal artifact emission.
    if isinstance(initial, Mapping):
        current = _collect_for_recheck(
            initial,
            repo_root,
            review_skill_path=(
                terminal_skill.get("path") if terminal_skill is not None else None
            ),
            review_skill_sha256=(
                terminal_skill.get("sha256") if terminal_skill is not None else None
            ),
        )
        if isinstance(recheck, Mapping) and _stability_projection(
            current
        ) != _stability_projection(recheck):
            problems.append("review state drifted after final recheck")
        if terminal_skill is not None and current.get("review_skill") != terminal_skill:
            problems.append("terminal recollection Review Skill identity mismatch")
        for detail in _snapshot_identity_problems(
            current,
            task=args.task,
            pr=args.pr,
            base_sha=args.expected_base_sha,
            head_sha=args.expected_head_sha,
            effective_diff_sha256=args.effective_diff_sha256,
        ):
            problems.append(f"terminal recollection: {detail}")

    review_evidence = payload.get("review_evidence")
    if not isinstance(review_evidence, Mapping):
        problems.append("terminal payload review evidence is missing")
    else:
        if review_evidence.get("review_snapshot_id") != args.review_snapshot_id:
            problems.append("terminal payload initial snapshot ID mismatch")
        if review_evidence.get("recheck_snapshot_id") != args.recheck_snapshot_id:
            problems.append("terminal payload recheck snapshot ID mismatch")
        if review_evidence.get("effective_diff_sha256") != args.effective_diff_sha256:
            problems.append("terminal payload effective diff mismatch")
        if (
            terminal_skill is not None
            and review_evidence.get("review_skill") != terminal_skill
        ):
            problems.append("terminal payload Review Skill identity mismatch")

    unsigned = {key: item for key, item in payload.items() if key != "evidence_id"}
    artifact = dict(unsigned)
    artifact["evidence_id"] = sha256_json(unsigned)
    artifact_id = artifact["evidence_id"]
    candidate = _review_handoff_candidate(
        artifact,
        path=root / REVIEW_HANDOFF_SUBDIR / f"{artifact_id}.json",
        repo_root=repo_root,
        root=root,
        repository=args.repository,
        task=args.task,
        pr=args.pr,
        expected_base_sha=args.expected_base_sha,
        expected_head_sha=args.expected_head_sha,
    )
    if not candidate["valid"]:
        for category, details in candidate["categories"].items():
            problems.extend(f"{category}: {detail}" for detail in details)
    if problems:
        raise WorkflowToolError(
            "review terminal failed closed: " + "; ".join(problems[:20])
        )

    evidence_id, destination = _materialize_review_handoff(repo_root, artifact)
    stored = read_json_file(destination)
    if not isinstance(stored, Mapping) or dict(stored) != artifact:
        raise WorkflowToolError("materialized review handoff failed self-verification")
    stored_candidate = _review_handoff_candidate(
        stored,
        path=destination,
        repo_root=repo_root,
        root=root,
        repository=args.repository,
        task=args.task,
        pr=args.pr,
        expected_base_sha=args.expected_base_sha,
        expected_head_sha=args.expected_head_sha,
    )
    if not stored_candidate["valid"]:
        raise WorkflowToolError(
            "materialized review handoff failed provenance validation"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "task": args.task,
        "pr": args.pr,
        "repository": args.repository,
        "reviewed_base_sha": args.expected_base_sha,
        "reviewed_head_sha": args.expected_head_sha,
        "effective_diff_sha256": args.effective_diff_sha256,
        "verdict": stored.get("verdict"),
        "review_handoff_id": evidence_id,
        "reference": destination.relative_to(repo_root).as_posix(),
        "final_recheck_snapshot_id": args.recheck_snapshot_id,
    }


def _is_sha256_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _safe_evidence_file(root: Path, relative: str) -> Path | None:
    """Resolve one repo-relative file without symlinks or traversal."""

    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        return None
    path = Path(relative)
    if ".." in path.parts or "\\" in relative:
        return None
    evidence_root = root.resolve()
    candidate = root / path
    try:
        candidate.resolve(strict=False).relative_to(evidence_root)
    except ValueError:
        return None
    current = candidate
    while current != root:
        if current.is_symlink():
            return None
        current = current.parent
    if candidate.is_symlink():
        return None
    return candidate


def _read_review_snapshot(
    root: Path, snapshot_id: Any, *, operation: str
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(snapshot_id, str) or not re.fullmatch(
        r"ev-[0-9a-f]{16}", snapshot_id
    ):
        return None, "snapshot identity is malformed"
    path = _safe_evidence_file(root, f"{SNAPSHOT_SUBDIR}/{snapshot_id}.json")
    if path is None or not path.is_file():
        return None, "snapshot path is missing or unsafe"
    try:
        value = read_json_file(path)
    except WorkflowToolError as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "snapshot is not a JSON object"
    if value.get("schema_version") != SCHEMA_VERSION:
        return None, "snapshot schema is unsupported"
    core = {key: item for key, item in value.items() if key != "snapshot_id"}
    expected_id = f"ev-{sha256_json(core)[:16]}"
    if value.get("snapshot_id") != snapshot_id or expected_id != snapshot_id:
        return None, "snapshot content address does not match"
    if value.get("operation") != operation:
        return None, "snapshot operation does not match"
    return value, None


def _snapshot_identity_problems(
    snapshot: Mapping[str, Any] | None,
    *,
    task: int,
    pr: int,
    base_sha: str,
    head_sha: str,
    effective_diff_sha256: str,
) -> list[str]:
    problems: list[str] = []
    if not isinstance(snapshot, Mapping):
        return ["snapshot content is unavailable"]
    subject = snapshot.get("subject")
    if not isinstance(subject, Mapping):
        problems.append("snapshot subject is missing")
    elif subject.get("task_number") != task or subject.get("pr_number") != pr:
        problems.append("snapshot Task/PR identity mismatch")
    observed = snapshot.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    observed_pr = observed.get("pr")
    observed_pr = observed_pr if isinstance(observed_pr, Mapping) else {}
    if (
        observed_pr.get("base_sha") != base_sha
        or observed_pr.get("head_sha") != head_sha
    ):
        problems.append("snapshot base/head identity mismatch")
    diff = observed.get("effective_diff")
    if not isinstance(diff, Mapping) or diff.get("sha256") != effective_diff_sha256:
        problems.append("snapshot effective diff identity mismatch")
    gates = snapshot.get("gates")
    required_gates = (
        "repository",
        "issue_available",
        "issue_state",
        "issue_type",
        "label:codex:ready",
        "label-not:codex:blocked",
        "project_status_review",
        "pr_available",
        "pr_state",
        "not_draft",
        "closing_linkage",
        "base_sha",
        "head_sha",
        "check_runs",
        "required_checks_configuration",
        "unresolved_threads",
    )
    if not isinstance(gates, Mapping):
        problems.append("snapshot gates are missing")
    else:
        for gate_name in required_gates:
            gate = gates.get(gate_name)
            if not isinstance(gate, Mapping) or gate.get("status") not in {
                "pass",
                "partial",
                "unknown",
                "fail",
            }:
                problems.append(f"snapshot gate {gate_name} is incomplete")
        for gate_name in (
            "repository",
            "issue_available",
            "issue_state",
            "issue_type",
            "label:codex:ready",
            "label-not:codex:blocked",
            "project_status_review",
            "pr_available",
            "pr_state",
            "not_draft",
            "closing_linkage",
            "base_sha",
            "head_sha",
        ):
            gate = gates.get(gate_name)
            if isinstance(gate, Mapping) and gate.get("status") != "pass":
                problems.append(f"snapshot identity gate {gate_name} is not pass")
    return problems


def _safe_repo_evidence_file(repo_root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative.startswith(f"{EVIDENCE_ROOT}/"):
        return None
    root = repo_root / EVIDENCE_ROOT
    return _safe_evidence_file(root, relative.removeprefix(f"{EVIDENCE_ROOT}/"))


def _safe_repo_skill_file(repo_root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not (
        relative.startswith(".agents/skills/") or relative.startswith(".claude/skills/")
    ):
        return None
    if not relative.endswith("/SKILL.md"):
        return None
    root = repo_root
    return _safe_evidence_file(root, relative)


def _matrix_problems(
    repo_root: Path,
    evidence: Mapping[str, Any],
    *,
    task: int,
    pr: int,
    base_sha: str,
    head_sha: str,
    effective_diff_sha256: str,
    skill_path: str,
    skill_sha256: str,
) -> list[str]:
    problems: list[str] = []
    matrix_path = _safe_repo_evidence_file(
        repo_root, evidence.get("evidence_matrix_path")
    )
    if matrix_path is None or not matrix_path.is_file():
        return ["evidence matrix path is missing or unsafe"]
    try:
        matrix_bytes = matrix_path.read_bytes()
        matrix = read_json_file(matrix_path)
    except (OSError, WorkflowToolError) as exc:
        return [f"evidence matrix cannot be read: {exc}"]
    if not isinstance(matrix, Mapping):
        problems.append("evidence matrix is not a JSON object")
        return problems
    claimed_digest = evidence.get("evidence_matrix_sha256")
    if not _is_sha256_digest(claimed_digest):
        problems.append("evidence matrix digest is malformed")
    elif sha256_bytes(matrix_bytes) != claimed_digest:
        problems.append("evidence matrix content address does not match")
    expected_values = {
        "task": task,
        "pr": pr,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "effective_diff_sha256": effective_diff_sha256,
    }
    for key, expected in expected_values.items():
        if matrix.get(key) != expected:
            problems.append(f"evidence matrix {key} identity mismatch")
    if not isinstance(matrix.get("changed_file_groups"), list):
        problems.append("evidence matrix changed_file_groups is incomplete")
    if not isinstance(matrix.get("acceptance_criteria"), list):
        problems.append("evidence matrix acceptance_criteria is incomplete")
    gates = matrix.get("evidence_gates")
    if not isinstance(gates, Mapping) or any(
        gates.get(key) not in {"pass", "partial", "unknown", "fail"}
        for key in ("review", "validation", "recheck")
    ):
        problems.append("evidence matrix evidence_gates is incomplete")
    if matrix.get("overall") not in {"verified", "partial", "not_verified"}:
        problems.append("evidence matrix overall status is invalid")
    matrix_skill = matrix.get("review_skill")
    if not isinstance(matrix_skill, Mapping):
        problems.append("evidence matrix Review Skill identity is missing")
    elif (
        matrix_skill.get("path") != skill_path
        or matrix_skill.get("sha256") != skill_sha256
    ):
        problems.append("evidence matrix Review Skill identity mismatch")
    return problems


def _review_handoff_candidate(
    value: Mapping[str, Any],
    *,
    path: Path,
    repo_root: Path,
    root: Path,
    repository: str,
    task: int,
    pr: int,
    expected_base_sha: str,
    expected_head_sha: str,
) -> dict[str, Any]:
    categories: dict[str, list[str]] = {
        "identity": [],
        "evidence": [],
        "freshness": [],
        "findings": [],
        "maintainer_decision": [],
    }

    def problem(category: str, detail: str) -> None:
        categories[category].append(detail)

    if value.get("schema_version") != REVIEW_HANDOFF_SCHEMA_VERSION:
        problem("evidence", "unsupported handoff schema")
    if value.get("kind") != "independent-review-handoff":
        problem("evidence", "handoff kind is invalid")
    if value.get("repository") != repository:
        problem("identity", "handoff repository mismatch")
    if value.get("task") != task:
        problem("identity", "handoff Task mismatch")
    if value.get("pr") != pr:
        problem("identity", "handoff PR mismatch")

    reviewed_base = value.get("reviewed_base_sha")
    reviewed_head = value.get("reviewed_head_sha")
    if reviewed_base != expected_base_sha:
        problem("identity", "reviewed base SHA mismatch")
    if reviewed_head != expected_head_sha:
        problem("identity", "stale or mismatched reviewed head SHA")
    if not is_sha(reviewed_base) or not is_sha(reviewed_head):
        problem("identity", "reviewed base/head SHA is malformed")

    verdict = value.get("verdict")
    if verdict in {"通过，可以人工合并", "PASS"}:
        normalized_verdict = "PASS"
    elif verdict in {"有条件通过，不得合并", "CONDITIONAL"}:
        normalized_verdict = "CONDITIONAL"
    elif verdict in {"不通过，需要修复", "FAIL"}:
        normalized_verdict = "FAIL"
    else:
        normalized_verdict = None
        problem("findings", "remediation verdict must be CONDITIONAL or FAIL")

    findings = value.get("required_findings")
    finding_items = findings if isinstance(findings, list) else []
    if not isinstance(findings, list):
        problem("findings", "required_findings must be a list")
    valid_severities = {"Blocking", "High", "Medium"}
    finding_ids: list[str] = []
    for finding in finding_items:
        if not isinstance(finding, Mapping):
            problem("findings", "finding entry is malformed")
            continue
        finding_id = finding.get("id")
        if (
            not isinstance(finding_id, str)
            or REVIEW_FINDING_ID_PATTERN.fullmatch(finding_id) is None
        ):
            problem("findings", "finding ID is missing")
        else:
            if finding_id in finding_ids:
                problem("findings", f"duplicate required finding ID: {finding_id}")
            finding_ids.append(finding_id)
        if finding.get("severity") not in valid_severities:
            problem("findings", "finding severity is not Blocking/High/Medium")
        if finding.get("required") is not True:
            problem("findings", "finding is not marked required")
    objective_gates = value.get("objective_gates")
    if not isinstance(objective_gates, list):
        problem("findings", "objective_gates must be a list")
        objective_gates = []
    if normalized_verdict == "FAIL" and not finding_items:
        problem("findings", "FAIL handoff has no required finding")
    if (
        normalized_verdict == "CONDITIONAL"
        and not finding_items
        and not objective_gates
    ):
        problem("findings", "CONDITIONAL handoff has no finding or objective gate")

    remediation = value.get("required_remediation")
    remediation_items = remediation if isinstance(remediation, list) else []
    if not isinstance(remediation, list):
        problem("findings", "required_remediation must be a list")
    remediation_ids: list[str] = []
    for item in remediation_items:
        if not isinstance(item, Mapping):
            problem("findings", "required remediation entry is malformed")
            continue
        remediation_id = item.get("id")
        if (
            not isinstance(remediation_id, str)
            or REVIEW_FINDING_ID_PATTERN.fullmatch(remediation_id) is None
        ):
            problem("findings", "required remediation ID is missing or invalid")
        else:
            if remediation_id in remediation_ids:
                problem(
                    "findings", f"duplicate required remediation ID: {remediation_id}"
                )
            remediation_ids.append(remediation_id)
        if item.get("required") is not True:
            problem("findings", "required remediation is not marked required")
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            problem("findings", "required remediation description is empty")

    if normalized_verdict == "FAIL" and not remediation_items:
        problem("findings", "FAIL handoff has no required remediation")
    if set(finding_ids) != set(remediation_ids) or len(finding_ids) != len(
        remediation_ids
    ):
        problem(
            "findings",
            "required_findings and required_remediation are not one-to-one",
        )
    if normalized_verdict == "PASS" and (finding_ids or remediation_ids):
        problem("findings", "PASS handoff must not contain required remediation")

    maintainer_required = value.get("maintainer_decision_required")
    if maintainer_required is not False:
        problem("maintainer_decision", "maintainer decision is required or unavailable")

    freshness = value.get("freshness")
    if not isinstance(freshness, Mapping):
        problem("freshness", "freshness evidence is missing")
        freshness = {}
    if freshness.get("status") != "fresh" or freshness.get("recheck") != "pass":
        problem("freshness", "review freshness/recheck is not pass")

    evidence = value.get("review_evidence")
    if not isinstance(evidence, Mapping):
        problem("evidence", "review evidence identity is missing")
        evidence = {}
    review_snapshot_id = evidence.get("review_snapshot_id")
    recheck_snapshot_id = evidence.get("recheck_snapshot_id")
    diff_digest = evidence.get("effective_diff_sha256")
    if not _is_sha256_digest(diff_digest):
        problem("evidence", "effective diff evidence digest is malformed")
    skill = evidence.get("review_skill")
    if not isinstance(skill, Mapping):
        problem("evidence", "review Skill identity is missing")
    else:
        skill_path = skill.get("path")
        if (
            not isinstance(skill_path, str)
            or skill_path not in CANONICAL_REVIEW_SKILL_PATHS
        ):
            problem(
                "evidence",
                "review Skill path is not the canonical independent task-pr-review Skill",
            )
        if not _is_sha256_digest(skill.get("sha256")):
            problem("evidence", "review Skill digest is malformed")
    if not isinstance(value.get("created_at"), str) or not value.get("created_at"):
        problem("evidence", "handoff creation timestamp is missing")
    matrix_path = evidence.get("evidence_matrix_path")
    if not isinstance(matrix_path, str):
        problem("evidence", "evidence matrix path is missing")

    evidence_id = value.get("evidence_id")
    if not _is_sha256_digest(evidence_id) or evidence_id != _review_handoff_digest(
        value
    ):
        problem("evidence", "content address does not match handoff content")
    if path.stem != evidence_id:
        problem("evidence", "handoff filename is not its content address")

    if not categories["evidence"]:
        skill = evidence.get("review_skill")
        skill_path = skill.get("path") if isinstance(skill, Mapping) else None
        skill_digest = skill.get("sha256") if isinstance(skill, Mapping) else None
        skill_file = (
            _safe_repo_skill_file(repo_root, skill_path)
            if isinstance(skill_path, str)
            else None
        )
        if skill_file is None or not skill_file.is_file():
            problem("evidence", "Review Skill path is missing or unsafe")
        elif not _is_sha256_digest(skill_digest):
            problem("evidence", "Review Skill digest is malformed")
        else:
            try:
                actual_skill_digest = sha256_bytes(skill_file.read_bytes())
            except OSError:
                actual_skill_digest = None
            if actual_skill_digest != skill_digest:
                problem("evidence", "Review Skill content address does not match")

        initial, initial_error = _read_review_snapshot(
            root, review_snapshot_id, operation="pr-review-snapshot"
        )
        initial_problems = _snapshot_identity_problems(
            initial,
            task=task,
            pr=pr,
            base_sha=expected_base_sha,
            head_sha=expected_head_sha,
            effective_diff_sha256=diff_digest,
        )
        if initial_error:
            initial_problems.insert(0, initial_error)
        for detail in initial_problems:
            problem("evidence", f"initial review snapshot: {detail}")

        recheck, recheck_error = _read_review_snapshot(
            root, recheck_snapshot_id, operation="pr-review-recheck"
        )
        recheck_problems = _snapshot_identity_problems(
            recheck,
            task=task,
            pr=pr,
            base_sha=expected_base_sha,
            head_sha=expected_head_sha,
            effective_diff_sha256=diff_digest,
        )
        if recheck_error:
            recheck_problems.insert(0, recheck_error)
        if not (
            isinstance(recheck, Mapping)
            and isinstance(recheck.get("stability"), Mapping)
            and recheck["stability"].get("stable") is True
            and isinstance(recheck.get("gates"), Mapping)
            and isinstance(recheck["gates"].get("snapshot_stability"), Mapping)
            and recheck["gates"]["snapshot_stability"].get("status") == "pass"
        ):
            recheck_problems.append("recheck did not prove stability")
        for detail in recheck_problems:
            problem("freshness", f"review recheck snapshot: {detail}")

        if isinstance(skill_path, str) and isinstance(skill_digest, str):
            matrix_problems = _matrix_problems(
                repo_root,
                evidence,
                task=task,
                pr=pr,
                base_sha=expected_base_sha,
                head_sha=expected_head_sha,
                effective_diff_sha256=diff_digest,
                skill_path=skill_path,
                skill_sha256=skill_digest,
            )
            for detail in matrix_problems:
                problem("evidence", detail)

    valid = not any(categories.values())
    return {
        "valid": valid,
        "categories": categories,
        "task": value.get("task"),
        "pr": value.get("pr"),
        "reviewed_base_sha": reviewed_base,
        "reviewed_head_sha": reviewed_head,
        "verdict": normalized_verdict,
        "evidence_id": evidence_id,
        "path": path.relative_to(root.parent.parent).as_posix(),
    }


def _review_handoff_snapshot(
    repo_root: Path,
    *,
    repository: str,
    task: int,
    pr: int,
    expected_base_sha: str,
    expected_head_sha: str,
    evidence_id: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _ensure_evidence_root(repo_root)
    directory = _ensure_handoff_directory(root)
    candidates: list[dict[str, Any]] = []
    invalid_files: list[str] = []
    if not isinstance(evidence_id, str) or not re.fullmatch(
        r"[0-9a-f]{64}", evidence_id
    ):
        return _review_handoff_failure(
            "explicit review handoff evidence_id is missing or malformed",
            categories={
                "identity": "fail",
                "evidence": "fail",
                "freshness": "fail",
                "findings": "fail",
                "maintainer_decision": "fail",
            },
        )

    target = _safe_evidence_file(root, f"{REVIEW_HANDOFF_SUBDIR}/{evidence_id}.json")
    if target is None or not target.is_file():
        return _review_handoff_failure(
            "explicit review handoff evidence is missing or unsafe",
            categories={
                "identity": "fail",
                "evidence": "fail",
                "freshness": "fail",
                "findings": "fail",
                "maintainer_decision": "fail",
            },
        )

    def collect(path: Path) -> None:
        if path.is_symlink():
            if path == target:
                invalid_files.append(path.name)
            return
        try:
            value = read_json_file(path)
        except WorkflowToolError:
            if path == target:
                invalid_files.append(path.name)
            return
        if not isinstance(value, Mapping):
            if path == target:
                invalid_files.append(path.name)
            return
        if value.get("task") != task or value.get("pr") != pr:
            return
        candidates.append(
            _review_handoff_candidate(
                value,
                path=path,
                repo_root=repo_root,
                root=root,
                repository=repository,
                task=task,
                pr=pr,
                expected_base_sha=expected_base_sha,
                expected_head_sha=expected_head_sha,
            )
        )

    collect(target)
    # This is conflict detection only.  Evidence discovery is the explicit
    # target above; the complete scan has no arbitrary cap and never selects a
    # different artifact.  Stale artifacts for an older head remain usable
    # only for that older head and do not hide a current conflict.
    for path in sorted(directory.glob("*.json")):
        if path != target:
            collect(path)

    selected = next(
        (
            candidate
            for candidate in candidates
            if candidate["path"].endswith(f"review-handoffs/{evidence_id}.json")
        ),
        None,
    )
    if selected is not None and selected["valid"]:
        if selected.get("verdict") == "PASS":
            return _review_handoff_failure(
                "PASS review evidence cannot authorize remediation",
                candidates=candidates,
                invalid_files=invalid_files,
                categories={
                    "identity": "pass",
                    "evidence": "pass",
                    "freshness": "pass",
                    "findings": "fail",
                    "maintainer_decision": "pass",
                },
            )
        conflicts = [
            candidate
            for candidate in candidates
            if candidate is not selected
            and candidate.get("reviewed_base_sha") == expected_base_sha
            and candidate.get("reviewed_head_sha") == expected_head_sha
        ]
        if conflicts:
            return _review_handoff_failure(
                "ambiguous or conflicting current-head review handoff evidence",
                candidates=candidates,
                invalid_files=invalid_files,
                categories={
                    "identity": "fail",
                    "evidence": "fail",
                    "freshness": "fail",
                    "findings": "fail",
                    "maintainer_decision": "fail",
                },
            )
        gates = {
            "review_handoff": _gate(
                "pass", "canonical independent-review handoff is valid"
            ),
            "review_handoff_identity": _gate("pass"),
            "review_handoff_evidence": _gate("pass"),
            "review_handoff_freshness": _gate("pass"),
            "review_handoff_findings": _gate("pass"),
            "review_handoff_maintainer_decision": _gate("pass"),
        }
        return {
            "status": "pass",
            "candidate_count": len(candidates),
            "selected": selected,
            "invalid_files": invalid_files,
        }, gates

    if selected is None:
        detail = "explicit review handoff evidence was not a target candidate"
    elif candidates:
        detail = "stale or invalid review handoff"
    else:
        detail = "invalid review handoff evidence"
    categories: dict[str, str] = {
        "identity": "fail",
        "evidence": "fail",
        "freshness": "fail",
        "findings": "fail",
        "maintainer_decision": "fail",
    }
    if selected is not None:
        merged = {
            category
            for category, problems in selected["categories"].items()
            if problems
        }
        for category in categories:
            categories[category] = "fail" if category in merged else "pass"
    return _review_handoff_failure(
        detail,
        candidates=candidates,
        invalid_files=invalid_files,
        categories=categories,
    )


def _review_handoff_failure(
    detail: str,
    *,
    candidates: Sequence[Mapping[str, Any]] = (),
    invalid_files: Sequence[str] = (),
    categories: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    gates = {
        "review_handoff": _gate("fail", detail),
        "review_handoff_identity": _gate(categories["identity"]),
        "review_handoff_evidence": _gate(categories["evidence"]),
        "review_handoff_freshness": _gate(categories["freshness"]),
        "review_handoff_findings": _gate(categories["findings"]),
        "review_handoff_maintainer_decision": _gate(categories["maintainer_decision"]),
    }
    return {
        "status": "fail",
        "candidate_count": len(candidates),
        "selected": None,
        "invalid_files": list(invalid_files),
        "detail": detail,
    }, gates


def _entry_point_gates(
    args: argparse.Namespace,
    runner: CommandRunner,
    repo_root: Path,
    repository: str | None,
    observed: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    entry_point = args.entry_point
    git = observed.get("git")
    git = git if isinstance(git, dict) else {}

    if entry_point in {"implementation", "final-validation", "pr-readiness"}:
        branch = args.branch
        branch_exists = runner.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            command_id="git-branch-exists",
        )
        branch_is_present = branch_exists.returncode == 0
        if branch_is_present:
            gates["branch_exists"] = _gate("pass")
        elif branch_exists.returncode == 1:
            if entry_point == "implementation":
                gates["branch_exists"] = _gate(
                    "pass", f"branch {branch!r} absent; bootstrap evaluation required"
                )
            else:
                gates["branch_exists"] = _gate(
                    "fail", f"branch {branch!r} does not exist"
                )
        else:
            gates["branch_exists"] = _gate("unknown")

        remote_tip: str | None = None
        remote_available = True
        local_branch_tip: str | None = None
        if entry_point == "implementation" and repository:
            if branch_is_present:
                local_tip_result = runner.run(
                    ["git", "rev-parse", f"refs/heads/{branch}"],
                    command_id="git-implementation-branch-tip",
                )
                if local_tip_result.returncode == 0:
                    local_branch_tip = local_tip_result.stdout.strip()
                else:
                    warnings.append(command_warning(local_tip_result))
            remote_result = runner.run(
                ["git", "ls-remote", "--heads", "origin", str(branch)],
                command_id="git-implementation-remote-branch",
            )
            if remote_result.returncode == 0:
                remote_tip = _remote_branch_tip(remote_result.stdout)
                gates["branch_remote"] = _gate(
                    "pass",
                    "remote branch absent"
                    if remote_tip is None
                    else f"remote branch tip {remote_tip}",
                )
            else:
                remote_available = False
                warnings.append(command_warning(remote_result))
                gates["branch_remote"] = _gate("unknown")

            gates["branch_identity"] = _task_branch_identity_gate(branch, args.task)
            gates["branch_bootstrap"] = _branch_bootstrap_gate(
                git=git,
                branch=branch,
                task=args.task,
                expected_main_sha=args.expected_main_sha,
                expected_base_sha=args.expected_base_sha,
                branch_exists=branch_is_present,
                local_tip=local_branch_tip,
                remote_tip=remote_tip,
                remote_available=remote_available,
            )

        if args.expected_base_sha and repository and branch_is_present:
            merge_base = runner.run(
                [
                    "git",
                    "merge-base",
                    f"refs/heads/{branch}",
                    "refs/remotes/origin/main",
                ],
                command_id="git-branch-base",
            )
            if merge_base.returncode == 0:
                actual_base = merge_base.stdout.strip()
                if is_sha(actual_base) and actual_base == args.expected_base_sha:
                    gates["branch_base"] = _gate("pass")
                else:
                    gates["branch_base"] = _gate(
                        "fail",
                        f"expected {args.expected_base_sha}, observed {actual_base}",
                    )
            elif merge_base.returncode == 1:
                gates["branch_base"] = _gate(
                    "fail", "no common ancestor with origin/main"
                )
            else:
                warnings.append(command_warning(merge_base))
                gates["branch_base"] = _gate("unknown")
        elif args.expected_base_sha and repository and entry_point == "implementation":
            gates["branch_base"] = _gate(
                "pass", "expected base is the source for safe branch bootstrap"
            )

        if (
            entry_point == "implementation"
            and repository
            and branch_is_present
            and isinstance(args.expected_base_sha, str)
        ):
            active_gate, active_prs = _active_pr_identity_gate(
                runner,
                repository,
                branch=branch,
                task=args.task,
                expected_base_sha=args.expected_base_sha,
                expected_head_sha=args.expected_head_sha,
                branch_tip=local_branch_tip,
                expected_pr=args.pr,
                warnings=warnings,
            )
            observed["active_prs"] = bounded_list(active_prs)
            gates["active_pr_identity"] = active_gate

    if entry_point in {"final-validation", "pr-readiness"}:
        branch = args.branch
        head_result = runner.run(
            ["git", "rev-parse", f"refs/heads/{branch}"],
            command_id="git-branch-head",
        )
        if head_result.returncode == 0:
            actual_head = head_result.stdout.strip()
            if is_sha(actual_head) and actual_head == args.expected_head_sha:
                gates["branch_head"] = _gate("pass")
            else:
                gates["branch_head"] = _gate(
                    "fail",
                    f"expected {args.expected_head_sha}, observed {actual_head or 'none'}",
                )
        else:
            warnings.append(command_warning(head_result))
            gates["branch_head"] = _gate("unknown")

    if entry_point == "review-remediation":
        gates["implementation_head"] = _sha_gate(
            git.get("head_sha"), args.expected_head_sha, "implementation HEAD"
        )

    if entry_point == "pr-readiness":
        branch = args.branch
        remote_result = runner.run(
            ["git", "ls-remote", "--heads", "origin", str(branch)],
            command_id="git-remote-branch-head",
        )
        if remote_result.returncode == 0:
            remote_tip = _remote_branch_tip(remote_result.stdout)
            if remote_tip == args.expected_head_sha:
                gates["remote_head"] = _gate("pass")
            elif remote_tip is None:
                gates["remote_head"] = _gate("fail", "branch not found on remote")
            else:
                gates["remote_head"] = _gate(
                    "fail",
                    f"expected {args.expected_head_sha}, observed {remote_tip}",
                )
        else:
            warnings.append(command_warning(remote_result))
            gates["remote_head"] = _gate("unknown")

        if repository and isinstance(branch, str):
            pr_list = runner.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repository,
                    "--head",
                    branch,
                    "--state",
                    "all",
                    "--limit",
                    "100",
                    "--json",
                    "number,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,closingIssuesReferences",
                ],
                command_id="gh-pr-list-branch",
            )
            if pr_list.returncode != 0:
                warnings.append(command_warning(pr_list))
                gates["pr_identity"] = _gate("unknown")
            else:
                prs = read_json_text(pr_list.stdout, field="pr-list-branch")
                if isinstance(prs, list):
                    if len(prs) == 0:
                        gates["pr_identity"] = _gate(
                            "pass", "no PR for branch; PR creation authorized"
                        )
                    elif len(prs) == 1:
                        pr_item = prs[0]
                        if not isinstance(pr_item, dict):
                            gates["pr_identity"] = _gate("unknown")
                        else:
                            issues = []
                            raw_closing = pr_item.get("closingIssuesReferences", [])
                            if isinstance(raw_closing, list):
                                issues = [
                                    item["number"]
                                    for item in raw_closing
                                    if isinstance(item, dict)
                                    and isinstance(item.get("number"), int)
                                ]
                            state_ok = str(pr_item.get("state", "")).upper() == "OPEN"
                            head_ok = (
                                pr_item.get("headRefOid") == args.expected_head_sha
                            )
                            base_ok = (
                                pr_item.get("baseRefOid") == args.expected_base_sha
                            )
                            closing_ok = issues == [args.task]
                            if state_ok and head_ok and base_ok and closing_ok:
                                gates["pr_identity"] = _gate("pass")
                            else:
                                failures = []
                                if not state_ok:
                                    failures.append(f"state={pr_item.get('state')}")
                                if not head_ok:
                                    failures.append(f"head={pr_item.get('headRefOid')}")
                                if not base_ok:
                                    failures.append(f"base={pr_item.get('baseRefOid')}")
                                if not closing_ok:
                                    failures.append(f"closing={issues}")
                                gates["pr_identity"] = _gate(
                                    "fail", "; ".join(failures)
                                )
                    else:
                        gates["pr_identity"] = _gate(
                            "fail",
                            f"multiple PRs for branch: {[p.get('number') for p in prs if isinstance(p, dict)]}",
                        )
                else:
                    gates["pr_identity"] = _gate("unknown")

    if entry_point == "review-remediation":
        if repository and args.pr is not None:
            pr = _pr_view(runner, repository, args.pr, warnings)
            observed["pr"] = pr
            if pr is None:
                gates["pr_available"] = _gate("unknown", "PR metadata unavailable")
            else:
                gates["pr_available"] = _gate("pass")
                state = str(pr.get("state", "")).upper()
                gates["pr_state"] = _gate("pass" if state == "OPEN" else "fail", state)
                gates["not_draft"] = _gate(
                    "pass" if pr.get("is_draft") is False else "fail"
                )
                gates["pr_base_sha"] = _sha_gate(
                    pr.get("base_sha"), args.expected_base_sha, "PR base SHA"
                )
                gates["pr_head_sha"] = _sha_gate(
                    pr.get("head_sha"), args.expected_head_sha, "PR head SHA"
                )
                gates["closing_linkage"] = _closing_linkage_gate(
                    pr.get("closing_issues"), task_number=args.task
                )
                handoff, handoff_gates = _review_handoff_snapshot(
                    repo_root,
                    repository=repository,
                    task=args.task,
                    pr=args.pr,
                    expected_base_sha=args.expected_base_sha,
                    expected_head_sha=args.expected_head_sha,
                    evidence_id=args.review_handoff_id,
                )
                observed["review_handoff"] = handoff
                gates.update(handoff_gates)
                if handoff.get("status") == "pass":
                    selected = handoff.get("selected")
                    evidence_id = (
                        selected.get("evidence_id")
                        if isinstance(selected, Mapping)
                        else None
                    )
                    gates["review_conclusion"] = _gate(
                        "pass",
                        f"canonical independent-review handoff {evidence_id} verified",
                    )
                else:
                    gates["review_conclusion"] = _gate(
                        "fail",
                        str(
                            handoff.get("detail")
                            or "no valid independent-review handoff/evidence"
                        ),
                    )
                head_repo = pr.get("head_repository")
                if head_repo == repository:
                    gates["head_fixable"] = _gate("pass")
                elif head_repo is None:
                    gates["head_fixable"] = _gate("unknown")
                else:
                    gates["head_fixable"] = _gate(
                        "fail", f"head from fork: {head_repo}"
                    )

    return gates


def _closeout_cleanup_eligibility(
    *,
    repository: str | None,
    observed: Mapping[str, Any],
    gates: Mapping[str, Any],
    branch: str | None,
    remote_tip: str | None,
    local_tip: str | None,
    tree_equal: bool | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    pr = observed.get("pr")
    issue = observed.get("issue")
    git = observed.get("git")
    required = observed.get("required_checks")
    if "snapshot_stability" not in gates:
        reasons.append("final evidence recheck stability is not proven")
    if not repository:
        reasons.append("repository identity unavailable")
    if not isinstance(pr, Mapping):
        reasons.append("PR metadata unavailable")
    if not isinstance(issue, Mapping):
        reasons.append("Issue metadata unavailable")
    if not isinstance(git, Mapping):
        reasons.append("Git metadata unavailable")
    if (
        not isinstance(required, Mapping)
        or required.get("configuration") != "plan-limited-403"
    ):
        reasons.append(
            "Required Checks failure is not classified as GitHub plan-limit 403"
        )
    elif (
        not isinstance(required.get("failure"), Mapping)
        or required["failure"].get("reason") != "github-plan-limit-403"
    ):
        reasons.append("plan-limit 403 classification detail is unavailable")
    for name in (
        "pr_state",
        "closing_linkage",
        "issue_closure",
        "pull_request_merge",
        "head_sha",
        "merge_sha",
        "main_contains_merge",
        "local_main_synced",
        "project_done",
        "final_codex_label",
        "check_runs",
        "unresolved_threads",
        "snapshot_stability",
    ):
        if name in gates and not _gate_pass(gates, name):
            reasons.append(f"{name} gate is not pass")
    checks_ok, checks_detail = _checks_cleanup_status(
        pr if isinstance(pr, Mapping) else None
    )
    if not checks_ok:
        reasons.append(checks_detail)
    if isinstance(git, Mapping):
        merge_sha = pr.get("merge_commit_sha") if isinstance(pr, Mapping) else None
        if git.get("branch") != "main":
            reasons.append("current branch is not main")
        if git.get("clean") is not True:
            reasons.append("working tree is not clean")
        if not (
            isinstance(merge_sha, str)
            and git.get("local_main_sha") == merge_sha
            and git.get("origin_main_sha") == merge_sha
        ):
            reasons.append("local main, origin/main, and merge SHA are not identical")
        branches = git.get("worktree_branches")
        raw_branch_items = (
            branches.get("items") if isinstance(branches, Mapping) else []
        )
        branch_items = raw_branch_items if isinstance(raw_branch_items, list) else []
        if isinstance(branch, str) and branch in branch_items:
            reasons.append("target branch is occupied by a worktree")
    if isinstance(issue, Mapping) and isinstance(pr, Mapping):
        closure_gate = _issue_closure_gate(issue, pr, repository=repository)
        if closure_gate.get("status") == "unknown":
            reasons.append(
                f"issue_closure_linkage unknown: {closure_gate.get('detail')}"
            )
        elif closure_gate.get("status") == "fail":
            reasons.append(
                f"issue_closure_linkage blocked: {closure_gate.get('detail')}"
            )
        merge_gate = _pull_request_merge_gate(pr)
        if merge_gate.get("status") == "unknown":
            reasons.append(f"pull_request_merge unknown: {merge_gate.get('detail')}")
        elif merge_gate.get("status") == "fail":
            reasons.append(f"pull_request_merge blocked: {merge_gate.get('detail')}")
    expected_head = pr.get("head_sha") if isinstance(pr, Mapping) else None
    if not isinstance(branch, str) or not branch:
        reasons.append("exact PR head branch unavailable")
    if remote_tip != expected_head:
        reasons.append("remote branch tip does not match reviewed PR head")
    if local_tip != expected_head:
        reasons.append("local branch tip does not match reviewed PR head")
    if tree_equal is not True:
        reasons.append("PR head tree does not match merge tree")
    status = "eligible-under-capability-limited-policy" if not reasons else "blocked"
    return {
        "status": status,
        "limitation_preserved": True,
        "allowed_scope": "exact-task-branch-cleanup-only",
        "required_checks_configuration": {
            "status": "unknown",
            "reason": "github-plan-limit-403",
        },
        "reasons": bounded_list(reasons),
    }


def _classify_required_checks_failure(result: CommandResult) -> dict[str, Any]:
    combined = f"{result.stderr}\n{result.stdout}".casefold()
    category = "unknown"
    reason = "required-checks-query-failed"
    http_status: int | None = None
    if (
        " 401" in combined
        or "http 401" in combined
        or "bad credentials" in combined
        or "requires authentication" in combined
    ):
        category = "authentication"
        reason = "github-authentication-failure"
        http_status = 401
    elif " 403" in combined or "http 403" in combined or "403" in combined:
        http_status = 403
        if (
            "plan" in combined
            or "upgrade" in combined
            or "requires github pro" in combined
            or "requires github team" in combined
            or "private repositories require" in combined
            or (
                "branch protection" in combined
                and (
                    "private repositories" in combined
                    or "not available" in combined
                    or "not included" in combined
                )
            )
        ):
            category = "plan-limit"
            reason = "github-plan-limit-403"
        elif "rate limit" in combined or "secondary rate" in combined:
            category = "rate-limit"
            reason = "github-rate-limit-403"
        elif (
            "resource not accessible by integration" in combined
            or "scope" in combined
            or "sso" in combined
        ):
            category = "scope-or-permission"
            reason = "github-scope-or-sso-403"
        else:
            category = "permission"
            reason = "github-permission-403"
    elif " 429" in combined or "http 429" in combined or "rate limit" in combined:
        category = "rate-limit"
        reason = "github-rate-limit"
        http_status = 429
    elif (
        "could not resolve host" in combined
        or "connection refused" in combined
        or "operation timed out" in combined
        or "network" in combined
        or "proxyconnect" in combined
    ):
        category = "network"
        reason = "network-failure"
    elif "parse" in combined or "schema" in combined or "json" in combined:
        category = "schema-or-parse"
        reason = "github-schema-or-parse-failure"
    elif " 5" in combined or "http 5" in combined:
        category = "service"
        reason = "github-service-failure"
    return {
        "category": category,
        "reason": reason,
        "http_status": http_status,
        "command_id": result.command_id,
    }


def _review_threads(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    value = _graphql(
        runner,
        repository,
        number,
        THREADS_QUERY,
        command_id=f"gh-pr-threads-{number}",
        warnings=warnings,
    )
    nodes: list[Any] = []
    has_next_page = False
    if isinstance(value, dict):
        data = value.get("data")
        repo = data.get("repository") if isinstance(data, dict) else None
        pr = repo.get("pullRequest") if isinstance(repo, dict) else None
        threads = pr.get("reviewThreads") if isinstance(pr, dict) else None
        if isinstance(threads, dict):
            if isinstance(threads.get("nodes"), list):
                nodes = threads["nodes"]
            page = threads.get("pageInfo")
            if isinstance(page, dict):
                has_next_page = page.get("hasNextPage") is True
    unresolved = 0
    outdated = 0
    for item in nodes:
        if not isinstance(item, dict):
            continue
        if item.get("isResolved") is not True:
            unresolved += 1
        if item.get("isOutdated") is True:
            outdated += 1
    return {
        "available": value is not None,
        "count": len(nodes),
        "unresolved": unresolved,
        "outdated": outdated,
        "truncated": has_next_page,
    }


def _diff_digest(
    runner: CommandRunner,
    repository: str,
    pr_number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    result = runner.run(
        ["gh", "pr", "diff", str(pr_number), "--repo", repository, "--patch"],
        command_id=f"gh-pr-diff-{pr_number}",
    )
    if result.returncode != 0:
        warnings.append(command_warning(result))
        return {"available": False, "sha256": None, "bytes": None, "lines": None}
    raw = result.stdout.encode("utf-8")
    return {
        "available": True,
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "lines": result.stdout.count("\n"),
    }


def _runner_source(
    runner: CommandRunner,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    script = Path(__file__).resolve()
    digest = sha256_bytes(script.read_bytes())
    commit = _git_value(
        runner,
        [
            "log",
            "-1",
            "--format=%H",
            "--",
            "tools/agent_workflow/workflow_evidence.py",
        ],
        command_id="git-runner-source-sha",
        warnings=warnings,
    )
    return {
        "path": "tools/agent_workflow/workflow_evidence.py",
        "source_sha": commit if is_sha(commit) else None,
        "content_sha256": digest,
    }


def _issue_gates(
    issue: Mapping[str, Any] | None,
    relationships: Mapping[str, Any],
    *,
    expected_title: str | None,
    expected_state: str | None,
    required_label: str | None,
    forbidden_label: str | None,
    expected_type_label: str | None,
) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    if issue is None:
        return {"issue_available": _gate("unknown", "Issue metadata unavailable")}
    gates["issue_available"] = _gate("pass")
    actual_title = issue.get("title")
    if expected_title is None:
        gates["title"] = _gate("pass")
    elif isinstance(actual_title, str) and _normalize_title(
        actual_title
    ) == _normalize_title(expected_title):
        gates["title"] = _gate("pass")
    else:
        gates["title"] = _gate("fail", "supplied and canonical titles differ")
    if expected_state is None:
        gates["issue_state"] = _gate("pass")
    elif str(issue.get("state", "")).upper() == expected_state.upper():
        gates["issue_state"] = _gate("pass")
    else:
        gates["issue_state"] = _gate("fail", f"expected {expected_state}")
    labels = set(issue.get("labels", {}).get("items", []))
    if required_label:
        gates[f"label:{required_label}"] = _gate(
            "pass" if required_label in labels else "fail"
        )
    if forbidden_label:
        gates[f"label-not:{forbidden_label}"] = _gate(
            "pass" if forbidden_label not in labels else "fail"
        )
    if expected_type_label:
        issue_type = relationships.get("issue_type")
        matches_type = expected_type_label in labels or (
            isinstance(issue_type, str)
            and issue_type.casefold()
            == expected_type_label.removeprefix("type:").casefold()
        )
        gates["issue_type"] = _gate(
            "pass" if matches_type else "unknown", expected_type_label
        )
    gates["formal_blockers"] = _formal_blockers_gate(relationships)
    return gates


def _formal_blockers_gate(relationships: Mapping[str, Any]) -> dict[str, Any]:
    if relationships.get("available") is not True:
        return _gate("unknown", "Relationship facts unavailable")

    blocked_by = relationships.get("blocked_by")
    if not isinstance(blocked_by, Mapping):
        return _gate("unknown", "Blocked-by metadata unavailable")

    items = blocked_by.get("items")
    count = blocked_by.get("count")
    truncated = blocked_by.get("truncated")
    if (
        not isinstance(items, list)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(truncated, bool)
    ):
        return _gate("unknown", "Blocked-by metadata is malformed")

    open_numbers: list[int] = []
    unresolved = 0
    resolved = 0
    unknown_state = 0
    for item in items:
        if not isinstance(item, Mapping):
            unknown_state += 1
            continue
        state = item.get("state")
        normalized_state = state.upper() if isinstance(state, str) else ""
        if normalized_state == "OPEN":
            unresolved += 1
            number = item.get("number")
            if isinstance(number, int) and not isinstance(number, bool):
                open_numbers.append(number)
        elif normalized_state == "CLOSED":
            resolved += 1
        else:
            unknown_state += 1

    if unresolved:
        return _gate(
            "fail",
            f"unresolved={unresolved}, resolved={resolved}, open={open_numbers[:10]}",
        )
    if truncated:
        return _gate(
            "unknown",
            f"blocked-by list truncated; observed={len(items)}, resolved={resolved}",
        )
    if count != len(items):
        return _gate(
            "unknown",
            f"blocked-by count mismatch: count={count}, observed={len(items)}",
        )
    if unknown_state:
        return _gate(
            "unknown",
            f"unknown_state={unknown_state}, resolved={resolved}, total={count}",
        )
    return _gate("pass", f"unresolved=0, resolved={resolved}, total={count}")


def _sha_gate(actual: Any, expected: str | None, name: str) -> dict[str, Any]:
    if expected is None:
        return _gate("pass")
    if actual == expected:
        return _gate("pass")
    if actual is None:
        return _gate("unknown", f"{name} unavailable")
    return _gate("fail", f"expected {expected}, observed {actual}")


def _closing_linkage_gate(closing_issues: Any, *, task_number: int) -> dict[str, Any]:
    if not isinstance(closing_issues, Mapping):
        return _gate("unknown", "closing linkage unavailable")

    items = closing_issues.get("items")
    count = closing_issues.get("count")
    truncated = closing_issues.get("truncated")
    if (
        not isinstance(items, list)
        or not all(
            isinstance(item, int) and not isinstance(item, bool) for item in items
        )
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(truncated, bool)
    ):
        return _gate("unknown", "closing linkage metadata is malformed")

    exact = truncated is False and count == 1 and items == [task_number]
    detail = (
        f"expected=[{task_number}], count={count}, "
        f"truncated={str(truncated).lower()}, observed={items[:10]}"
    )
    return _gate("pass" if exact else "fail", detail)


def _pull_request_merge_gate(pr: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(pr, Mapping):
        return _gate("unknown", "PR metadata unavailable")
    state = str(pr.get("state", "")).upper()
    if state != "MERGED":
        return _gate("fail", f"PR state is {state or 'unknown'}")
    if not isinstance(pr.get("merged_at"), str):
        return _gate("unknown", "PR mergedAt unavailable")
    if not isinstance(pr.get("merge_commit_sha"), str):
        return _gate("unknown", "PR merge commit unavailable")
    return _gate("pass")


def _issue_closure_gate(
    issue: Mapping[str, Any] | None,
    pr: Mapping[str, Any] | None,
    *,
    repository: str | None,
) -> dict[str, Any]:
    if not isinstance(issue, Mapping):
        return _gate("unknown", "Issue metadata unavailable")
    if not isinstance(pr, Mapping):
        return _gate("unknown", "PR metadata unavailable")
    closure = issue.get("issue_closure")
    if not isinstance(closure, Mapping):
        return _gate("unknown", "issue closure evidence unavailable")
    if closure.get("evidence_status") != "complete":
        return _gate("unknown", str(closure.get("reason") or "partial evidence"))
    if str(issue.get("state", "")).upper() != "CLOSED":
        return _gate("fail", "issue-not-closed")
    if closure.get("status") != "closed-by-pr":
        return _gate("fail", str(closure.get("reason") or "not-closed-by-pr"))
    if closure.get("closer_repository") != repository:
        return _gate("fail", "closer-repository-mismatch")
    if closure.get("closer_number") != pr.get("number"):
        return _gate("fail", "closer-pr-number-mismatch")

    refs = closure.get("closed_by_pull_requests")
    ref_items = refs.get("items") if isinstance(refs, Mapping) else []
    matching_refs = (
        [
            item
            for item in ref_items
            if isinstance(item, Mapping) and item.get("number") == pr.get("number")
        ]
        if isinstance(ref_items, list)
        else []
    )
    if not matching_refs:
        return _gate("fail", "closed-by-pr-reference-missing")
    for item in matching_refs:
        if (
            item.get("repository") == repository
            and str(item.get("state", "")).upper() == "MERGED"
            and item.get("merged") is True
            and isinstance(item.get("merged_at"), str)
        ):
            return _gate("pass")
        if (
            item.get("state") is None
            or item.get("merged") is None
            or item.get("merged_at") is None
        ):
            return _gate("unknown", "incomplete-closing-pr-metadata")
    return _gate("fail", "closed-by-pr-reference-not-merged")


def _pr_gates(
    pr: Mapping[str, Any] | None,
    threads: Mapping[str, Any],
    required_checks: Mapping[str, Any],
    *,
    task_number: int,
    expected_base_sha: str | None,
    expected_head_sha: str | None,
    require_open: bool,
) -> dict[str, Any]:
    if pr is None:
        return {"pr_available": _gate("unknown", "PR metadata unavailable")}
    gates: dict[str, Any] = {"pr_available": _gate("pass")}
    state = str(pr.get("state", "")).upper()
    gates["pr_state"] = _gate(
        "pass" if (state == "OPEN" if require_open else state == "MERGED") else "fail",
        state,
    )
    if require_open:
        gates["not_draft"] = _gate("pass" if pr.get("is_draft") is False else "fail")
    gates["closing_linkage"] = _closing_linkage_gate(
        pr.get("closing_issues"), task_number=task_number
    )
    gates["base_sha"] = _sha_gate(pr.get("base_sha"), expected_base_sha, "base SHA")
    gates["head_sha"] = _sha_gate(pr.get("head_sha"), expected_head_sha, "head SHA")
    checks = pr.get("checks", {})
    if checks.get("all_success") is True:
        gates["check_runs"] = _gate("pass")
    elif checks.get("count") == 0:
        gates["check_runs"] = _gate("unknown", "no applicable check runs observed")
    elif checks.get("pending", 0) > 0:
        gates["check_runs"] = _gate("fail", "checks pending")
    else:
        gates["check_runs"] = _gate("fail", "failed, skipped, or unknown checks")
    config = required_checks.get("configuration")
    if config in {"available", "configured-empty", "not-configured"}:
        gates["required_checks_configuration"] = _gate("pass", str(config))
    elif config == "plan-limited-403":
        gates["required_checks_configuration"] = _gate(
            "unknown", "GitHub plan-limit 403"
        )
    else:
        gates["required_checks_configuration"] = _gate("unknown")
    if threads.get("available") is True and threads.get("truncated") is not True:
        gates["unresolved_threads"] = _gate(
            "pass" if threads.get("unresolved") == 0 else "fail"
        )
    elif threads.get("truncated") is True:
        gates["unresolved_threads"] = _gate("unknown", "review threads truncated")
    else:
        gates["unresolved_threads"] = _gate("unknown")
    return gates


def _base_snapshot(
    *,
    operation: str,
    subject: Mapping[str, Any],
    repository: str | None,
    observed: Mapping[str, Any],
    execution_context: Mapping[str, Any],
    gates: Mapping[str, Any],
    warnings: Sequence[Mapping[str, Any]],
    limitations: Sequence[str],
    operations: Mapping[str, int],
) -> dict[str, Any]:
    warning_items = list(warnings[:50])
    limitation_values = sorted(set(limitations))
    core: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "subject": dict(subject),
        "repository": repository,
        "execution_context": dict(execution_context),
        "observed": dict(observed),
        "gates": dict(sorted(gates.items())),
        "warnings": warning_items,
        "warning_count": len(warnings),
        "warnings_truncated": len(warnings) > len(warning_items),
        "limitations": limitation_values[:50],
        "limitation_count": len(limitation_values),
        "limitations_truncated": len(limitation_values) > 50,
        "operations": dict(operations),
    }
    snapshot_id = f"ev-{sha256_json(core)[:16]}"
    return {**core, "snapshot_id": snapshot_id}


def _store_snapshot(
    repo_root: Path, snapshot: Mapping[str, Any], no_store: bool
) -> str | None:
    if no_store:
        return None
    root = require_exact_ignored_directory(repo_root, EVIDENCE_ROOT)
    directory = root / SNAPSHOT_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    snapshot_id = snapshot.get("snapshot_id")
    if not isinstance(snapshot_id, str):
        raise WorkflowToolError("snapshot_id is missing")
    path = directory / f"{snapshot_id}.json"
    atomic_write_json(path, snapshot)
    return path.relative_to(repo_root).as_posix()


def _snapshot_output(
    repo_root: Path,
    snapshot: dict[str, Any],
    *,
    no_store: bool,
) -> dict[str, Any]:
    details = _store_snapshot(repo_root, snapshot, no_store)
    output = dict(snapshot)
    output["details_path"] = details
    return output


def _collect_task_pr(
    runner: CommandRunner,
    repository: str,
    task: int,
    pr_number: int | None,
    *,
    expected_title: str | None,
    expected_base_sha: str | None,
    expected_head_sha: str | None,
    require_open_pr: bool,
    issue_expected_state: str,
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    issue = _issue_view(runner, repository, task, warnings)
    relationships = _relationship_snapshot(runner, repository, task, warnings)
    observed: dict[str, Any] = {
        "git": _git_snapshot(runner, warnings),
        "issue": issue,
        "relationships": relationships,
    }
    gates = _issue_gates(
        issue,
        relationships,
        expected_title=expected_title,
        expected_state=issue_expected_state,
        required_label="codex:ready",
        forbidden_label="codex:blocked",
        expected_type_label="type:task",
    )
    gates["origin_fetch"] = _gate(observed["git"].get("origin_fetch", "unknown"))
    if pr_number is not None:
        pr = _pr_view(runner, repository, pr_number, warnings)
        threads = _review_threads(runner, repository, pr_number, warnings)
        base_branch = pr.get("base_branch") if isinstance(pr, dict) else None
        required = _required_checks(runner, repository, base_branch, warnings)
        diff = _diff_digest(runner, repository, pr_number, warnings)
        observed.update(
            {
                "pr": pr,
                "review_threads": threads,
                "required_checks": required,
                "effective_diff": diff,
            }
        )
        gates.update(
            _pr_gates(
                pr,
                threads,
                required,
                task_number=task,
                expected_base_sha=expected_base_sha,
                expected_head_sha=expected_head_sha,
                require_open=require_open_pr,
            )
        )
    return observed, gates


def _worktree_state_compatible_gate(
    git: Mapping[str, Any], *, entry_point: str
) -> dict[str, Any]:
    """Gate worktree cleanliness per entry point.

    delivery-start and implementation may accept a dirty worktree when the
    changes are plausibly owned by the current Task.  final-validation,
    pr-readiness, and review-remediation require a clean committed head.
    """
    clean = bool(git.get("clean"))
    branch = git.get("branch")
    staged = git.get("staged_files", {})
    staged_items = staged.get("items") if isinstance(staged, dict) else []
    changed = git.get("changed_files", {})
    changed_items = changed.get("items") if isinstance(changed, dict) else []
    status_entries = git.get("status_entries")

    dirty_allowed = entry_point in {"delivery-start", "implementation"}
    blocked: list[dict[str, Any]] = []

    if clean:
        return {
            "status": "pass",
            "observed_clean": True,
            "detail": f"worktree clean; entry_point={entry_point}",
        }

    if not dirty_allowed:
        blocked.append(
            {
                "gate": "worktree_state_compatible",
                "reason": f"entry-point-{entry_point}-requires-clean-committed-head",
                "detail": (
                    f"entry_point={entry_point} requires a clean committed head; "
                    f"observed_clean=False, status_entries={status_entries}"
                ),
            }
        )
        return {
            "status": "fail",
            "observed_clean": False,
            "dirty_allowed": False,
            "worktree_disposition": "stop",
            "detail": blocked[0]["detail"],
            "blocked": blocked,
        }

    # dirty but allowed — record full observations
    staged_list = staged_items if isinstance(staged_items, list) else []
    changed_list = changed_items if isinstance(changed_items, list) else []

    gate: dict[str, Any] = {
        "status": "pass",
        "observed_clean": False,
        "dirty_allowed": True,
        "worktree_disposition": "continue-through-implementation",
        "branch": branch,
        "staged_files": staged_items,
        "changed_files": changed_items,
        "status_entries": status_entries,
        "detail": (
            f"dirty worktree allowed for entry_point={entry_point}; "
            f"staged={len(staged_list)}, changed={len(changed_list)}, "
            f"status_entries={status_entries}"
        ),
    }
    # flag obviously untracked files for maintainer awareness
    untracked: list[str] = []
    status_lines = git.get("status") if isinstance(git, dict) else None
    if isinstance(status_lines, str):
        for line in status_lines.splitlines():
            stripped = line.strip()
            if stripped.startswith("??"):
                untracked.append(stripped.removeprefix("??").strip())
    if untracked:
        gate["untracked_observed"] = bounded_list(untracked)
    return gate


def _compute_preflight_disposition(
    gates: Mapping[str, Any], *, entry_point: str
) -> dict[str, Any]:
    """Derive a terminal admission disposition from Preflight gates.

    Only pass allows the workflow to proceed to writes.  Any other effective
    result is a stop disposition that forbids auto-remediation.
    """
    failed_gates: list[dict[str, Any]] = []
    unknown_gates: list[str] = []
    for name, value in gates.items():
        if not isinstance(value, dict):
            continue
        status = value.get("status")
        if status == "fail":
            failed_gates.append(
                {"gate": name, "reason": value.get("detail", "no detail")}
            )
        elif status == "unknown":
            unknown_gates.append(name)

    # worktree gate may carry its own blocked list
    worktree_gate = gates.get("worktree_state_compatible")
    if isinstance(worktree_gate, dict):
        wt_blocked = worktree_gate.get("blocked")
        if isinstance(wt_blocked, list):
            failed_gates.extend(wt_blocked)

    # lifecycle conflict is a hard stop
    lifecycle = gates.get("lifecycle_labels_exclusive")
    if isinstance(lifecycle, dict) and lifecycle.get("status") == "fail":
        failed_gates.append(
            {
                "gate": "lifecycle_consistency",
                "reason": lifecycle.get("detail", "lifecycle-label-conflict"),
            }
        )

    # identity conflict is a hard stop
    for identity_gate in (
        "origin_main",
        "branch_base",
        "branch_head",
        "pr_identity",
        "pr_base_sha",
        "pr_head_sha",
        "remote_head",
    ):
        gate_value = gates.get(identity_gate)
        if isinstance(gate_value, dict) and gate_value.get("status") == "fail":
            failed_gates.append(
                {
                    "gate": f"identity:{identity_gate}",
                    "reason": gate_value.get("detail", "identity-mismatch"),
                }
            )

    has_fail = bool(failed_gates)
    has_critical_unknown = bool(unknown_gates)
    pass_gate_count = sum(
        1 for v in gates.values() if isinstance(v, dict) and v.get("status") == "pass"
    )
    fail_gate_count = sum(
        1 for v in gates.values() if isinstance(v, dict) and v.get("status") == "fail"
    )
    unknown_gate_count = len(unknown_gates)

    if has_fail:
        return {
            "status": "fail",
            "disposition": "stop",
            "workflow_may_continue": False,
            "write_actions_allowed": False,
            "auto_remediation_allowed": False,
            "maintainer_action_required": True,
            "failed_gates": failed_gates,
            "gate_counts": {
                "pass": pass_gate_count,
                "fail": fail_gate_count,
                "unknown": unknown_gate_count,
            },
        }

    if has_critical_unknown:
        return {
            "status": "partial",
            "disposition": "stop",
            "workflow_may_continue": False,
            "write_actions_allowed": False,
            "auto_remediation_allowed": False,
            "maintainer_action_required": True,
            "failed_gates": [
                {"gate": name, "reason": "unknown-critical-gate"}
                for name in unknown_gates
            ],
            "gate_counts": {
                "pass": pass_gate_count,
                "fail": fail_gate_count,
                "unknown": unknown_gate_count,
            },
        }

    return {
        "status": "pass",
        "disposition": "proceed",
        "workflow_may_continue": True,
        "write_actions_allowed": True,
        "auto_remediation_allowed": False,
        "maintainer_action_required": False,
        "failed_gates": [],
        "gate_counts": {
            "pass": pass_gate_count,
            "fail": 0,
            "unknown": 0,
        },
    }


def _delivery_preflight(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    _validate_delivery_entry_args(args)
    runner = CommandRunner(repo_root)
    warnings: list[dict[str, Any]] = []
    repository = _repository_slug(runner, args.repository, warnings)
    limitations: list[str] = []
    observed: dict[str, Any]
    if repository is None:
        limitations.append("repository identity unavailable")
        observed = {
            "git": _git_snapshot(runner, warnings),
            "issue": None,
            "relationships": {},
        }
        gates = {"repository": _gate("unknown")}
    else:
        issue = _issue_view(runner, repository, args.task, warnings)
        relationships = _relationship_snapshot(runner, repository, args.task, warnings)
        git = _git_snapshot(runner, warnings)
        observed = {"git": git, "issue": issue, "relationships": relationships}
        issue_gates = _issue_gates(
            issue,
            relationships,
            expected_title=args.expected_title,
            expected_state="OPEN",
            required_label="codex:ready",
            forbidden_label="codex:blocked",
            expected_type_label="type:task",
        )
        gates = dict(issue_gates)
        gates["repository"] = _gate("pass")
        gates["origin_fetch"] = _gate(git.get("origin_fetch", "unknown"))
        gates["origin_main"] = _sha_gate(
            git.get("origin_main_sha"), args.expected_main_sha, "origin/main SHA"
        )
        gates["worktree_state_compatible"] = _worktree_state_compatible_gate(
            git, entry_point=args.entry_point
        )
        if issue is not None:
            labels = set(issue.get("labels", {}).get("items", []))
            gates["lifecycle_labels_exclusive"] = _lifecycle_labels_gate(
                labels, required_label="codex:ready"
            )
            gates["project_status_known"] = _project_status_gate(
                issue.get("project_status"), entry_point=args.entry_point
            )
        else:
            gates["lifecycle_labels_exclusive"] = _gate(
                "unknown", "Issue metadata unavailable"
            )
            gates["project_status_known"] = _gate(
                "unknown", "Issue metadata unavailable"
            )
        gates["parent_blocking"] = _parent_state_gate(relationships)
        gates.update(
            _entry_point_gates(args, runner, repo_root, repository, observed, warnings)
        )
    subject: dict[str, Any] = {
        "kind": "task",
        "task_number": args.task,
        "entry_point": args.entry_point,
    }
    if args.branch is not None:
        subject["branch"] = args.branch
    if args.pr is not None:
        subject["pr_number"] = args.pr
    execution_context = {
        "object_base_sha": observed["git"].get("origin_main_sha"),
        "runner": _runner_source(runner, warnings),
    }
    snapshot = _base_snapshot(
        operation="delivery-preflight",
        subject=subject,
        repository=repository,
        observed=observed,
        execution_context=execution_context,
        gates=gates,
        warnings=warnings,
        limitations=limitations,
        operations=runner.counters(),
    )
    disposition = _compute_preflight_disposition(gates, entry_point=args.entry_point)
    snapshot["disposition"] = disposition
    return snapshot


def _task_pr_snapshot(
    args: argparse.Namespace, repo_root: Path, operation: str
) -> dict[str, Any]:
    runner = CommandRunner(repo_root)
    warnings: list[dict[str, Any]] = []
    repository = _repository_slug(runner, args.repository, warnings)
    limitations: list[str] = []
    if repository is None:
        limitations.append("repository identity unavailable")
        observed = {"git": _git_snapshot(runner, warnings)}
        gates = {"repository": _gate("unknown")}
    else:
        observed, gates = _collect_task_pr(
            runner,
            repository,
            args.task,
            args.pr,
            expected_title=args.expected_title,
            expected_base_sha=args.expected_base_sha,
            expected_head_sha=args.expected_head_sha,
            require_open_pr=True,
            issue_expected_state="OPEN",
            warnings=warnings,
        )
        gates["repository"] = _gate("pass")
        issue = observed.get("issue")
        if isinstance(issue, dict):
            if operation == "delivery-readiness":
                labels = set(issue.get("labels", {}).get("items", []))
                gates["lifecycle_labels_exclusive"] = _lifecycle_labels_gate(
                    labels, required_label="codex:ready"
                )
                gates["project_status_known"] = _project_status_gate(
                    issue.get("project_status")
                )
            project = issue.get("project_status")
            gates["project_status_review"] = _gate(
                "pass" if project == "Review" else "unknown", str(project)
            )
    pr = observed.get("pr")
    object_base_sha = pr.get("base_sha") if isinstance(pr, dict) else None
    execution_context = {
        "object_base_sha": object_base_sha,
        "runner": _runner_source(runner, warnings),
    }
    snapshot = _base_snapshot(
        operation=operation,
        subject={"kind": "task-pr", "task_number": args.task, "pr_number": args.pr},
        repository=repository,
        observed=observed,
        execution_context=execution_context,
        gates=gates,
        warnings=warnings,
        limitations=limitations,
        operations=runner.counters(),
    )
    review_skill = _review_skill_identity_from_args(args, repo_root)
    if review_skill is not None:
        snapshot["review_skill"] = review_skill
        snapshot["snapshot_id"] = (
            f"ev-{sha256_json({key: value for key, value in snapshot.items() if key != 'snapshot_id'})[:16]}"
        )
    return snapshot


def _closeout_plan(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    runner = CommandRunner(repo_root)
    warnings: list[dict[str, Any]] = []
    repository = _repository_slug(runner, args.repository, warnings)
    limitations: list[str] = []
    if repository is None:
        limitations.append("repository identity unavailable")
        observed = {"git": _git_snapshot(runner, warnings)}
        gates = {"repository": _gate("unknown")}
    else:
        observed, gates = _collect_task_pr(
            runner,
            repository,
            args.task,
            args.pr,
            expected_title=args.expected_title,
            expected_base_sha=None,
            expected_head_sha=args.expected_head_sha,
            require_open_pr=False,
            issue_expected_state="CLOSED",
            warnings=warnings,
        )
        gates["repository"] = _gate("pass")
        pr = observed.get("pr")
        issue = observed.get("issue")
        git = observed.get("git", {})
        merge_sha = pr.get("merge_commit_sha") if isinstance(pr, dict) else None
        gates["merge_sha"] = _sha_gate(merge_sha, args.expected_merge_sha, "merge SHA")
        gates["issue_closure"] = _issue_closure_gate(
            issue if isinstance(issue, dict) else None,
            pr if isinstance(pr, dict) else None,
            repository=repository,
        )
        gates["pull_request_merge"] = _pull_request_merge_gate(
            pr if isinstance(pr, dict) else None
        )
        if isinstance(merge_sha, str):
            ancestry = runner.run(
                [
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    merge_sha,
                    "refs/remotes/origin/main",
                ],
                command_id="git-closeout-merge-on-main",
            )
            if ancestry.returncode == 0:
                main_contains = "pass"
            elif ancestry.returncode == 1:
                main_contains = "fail"
            else:
                main_contains = "unknown"
                warnings.append(command_warning(ancestry))
        else:
            main_contains = "unknown"
        gates["main_contains_merge"] = _gate(
            main_contains,
            "verified merge commit must be reachable from current origin/main",
        )
        gates["local_main_synced"] = _gate(
            "pass"
            if git.get("branch") == "main"
            and git.get("head_sha") is not None
            and git.get("head_sha") == git.get("origin_main_sha")
            else "unknown",
            "local main must equal origin/main before final verification",
        )
        if isinstance(issue, dict):
            project = issue.get("project_status")
            gates["project_done"] = _gate(
                "pass" if project == "Done" else "unknown", str(project)
            )
            labels = set(issue.get("labels", {}).get("items", []))
            gates["final_codex_label"] = _gate(
                "pass"
                if "codex:ready" in labels and "codex:blocked" not in labels
                else "fail"
            )
        branch = pr.get("head_branch") if isinstance(pr, dict) else None
        if isinstance(branch, str) and branch:
            remote_exists = _git_value(
                runner,
                ["ls-remote", "--heads", "origin", branch],
                command_id="git-closeout-remote-branch",
                warnings=warnings,
            )
            remote_tip = _remote_branch_tip(remote_exists)
            local_result = runner.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                command_id="git-closeout-local-branch",
            )
            local_tip = _git_value(
                runner,
                ["rev-parse", f"refs/heads/{branch}"],
                command_id="git-closeout-local-branch-tip",
                warnings=warnings,
            )
            tree_equal: bool | None = None
            if isinstance(args.expected_head_sha, str) and isinstance(merge_sha, str):
                tree = runner.run(
                    ["git", "diff", "--quiet", args.expected_head_sha, merge_sha],
                    command_id="git-closeout-tree-equality",
                )
                if tree.returncode == 0:
                    tree_equal = True
                elif tree.returncode == 1:
                    tree_equal = False
                else:
                    warnings.append(command_warning(tree))
            observed["branch_cleanup"] = {
                "exact_branch": safe_text(branch),
                "remote_exists": bool(remote_exists),
                "remote_tip": remote_tip,
                "local_exists": local_result.returncode == 0,
                "local_tip": local_tip if is_sha(local_tip) else None,
                "tree_equal": tree_equal,
                "apply_authorized": False,
                "cleanup_eligibility": _closeout_cleanup_eligibility(
                    repository=repository,
                    observed=observed,
                    gates=gates,
                    branch=branch,
                    remote_tip=remote_tip,
                    local_tip=local_tip if is_sha(local_tip) else None,
                    tree_equal=tree_equal,
                ),
            }
            expected_head = pr.get("head_sha") if isinstance(pr, dict) else None
            gates["remote_branch_tip"] = _gate(
                "pass" if remote_tip == expected_head else "fail"
            )
            gates["local_branch_tip"] = _gate(
                "pass" if local_tip == expected_head else "fail"
            )
            gates["head_merge_tree_equal"] = _gate(
                "pass" if tree_equal is True else "fail"
            )
            worktree_branches = git.get("worktree_branches", {})
            branch_items = (
                worktree_branches.get("items")
                if isinstance(worktree_branches, dict)
                else []
            )
            branch_items = branch_items if isinstance(branch_items, list) else []
            gates["target_branch_not_worktree"] = _gate(
                "pass" if branch not in branch_items else "fail"
            )
            required = observed.get("required_checks")
            if (
                isinstance(required, dict)
                and required.get("configuration") == "plan-limited-403"
            ):
                gates["capability_limited_cleanup_eligibility"] = _gate(
                    "unknown",
                    "final evidence recheck is required before cleanup eligibility",
                )
    pr = observed.get("pr")
    object_base_sha = pr.get("merge_commit_sha") if isinstance(pr, dict) else None
    execution_context = {
        "object_base_sha": object_base_sha,
        "runner": _runner_source(runner, warnings),
    }
    return _base_snapshot(
        operation="closeout-plan",
        subject={"kind": "task-pr", "task_number": args.task, "pr_number": args.pr},
        repository=repository,
        observed=observed,
        execution_context=execution_context,
        gates=gates,
        warnings=warnings,
        limitations=limitations + ["read-only plan; no branch deletion performed"],
        operations=runner.counters(),
    )


def _feature_snapshot(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    runner = CommandRunner(repo_root)
    warnings: list[dict[str, Any]] = []
    repository = _repository_slug(runner, args.repository, warnings)
    limitations: list[str] = []
    git = _git_snapshot(runner, warnings)
    observed: dict[str, Any]
    if repository is None:
        limitations.append("repository identity unavailable")
        observed = {
            "git": git,
            "feature": None,
            "relationships": {},
            "direct_children": bounded_list([]),
        }
        gates = {"repository": _gate("unknown")}
    else:
        feature = _issue_view(runner, repository, args.feature, warnings)
        relationships = _relationship_snapshot(
            runner, repository, args.feature, warnings
        )
        sub_issues = relationships.get("sub_issues")
        child_items = (
            sub_issues.get("items", []) if isinstance(sub_issues, dict) else []
        )
        children: list[dict[str, Any]] = []
        for child in child_items[:MAX_CHILDREN]:
            if not isinstance(child, dict) or not isinstance(child.get("number"), int):
                continue
            child_issue = _issue_view(runner, repository, child["number"], warnings)
            if child_issue is not None:
                child_relationships = _relationship_snapshot(
                    runner, repository, child["number"], warnings
                )
                child_issue["relationship_evidence"] = {
                    "available": child_relationships.get("available"),
                    "issue_type": child_relationships.get("issue_type"),
                    "parent": child_relationships.get("parent"),
                    "blocked_by": child_relationships.get("blocked_by"),
                    "blocking": child_relationships.get("blocking"),
                }
                pull_summaries: list[dict[str, Any]] = []
                pull_items = child_issue.get("closing_pull_requests", {}).get(
                    "items", []
                )
                if isinstance(pull_items, list):
                    for pull_item in pull_items[:3]:
                        if not isinstance(pull_item, dict) or not isinstance(
                            pull_item.get("number"), int
                        ):
                            continue
                        pull = _pr_view(
                            runner, repository, pull_item["number"], warnings
                        )
                        if pull is None:
                            continue
                        raw_checks = pull.get("checks")
                        checks: dict[str, Any] = (
                            raw_checks if isinstance(raw_checks, dict) else {}
                        )
                        pull_summaries.append(
                            {
                                "number": pull.get("number"),
                                "state": pull.get("state"),
                                "merged_at": pull.get("merged_at"),
                                "merge_commit_sha": pull.get("merge_commit_sha"),
                                "base_sha": pull.get("base_sha"),
                                "head_sha": pull.get("head_sha"),
                                "checks_count": checks.get("count"),
                                "checks_all_success": checks.get("all_success"),
                                "closing_issues": pull.get("closing_issues"),
                            }
                        )
                child_issue["pull_request_evidence"] = bounded_list(
                    pull_summaries, item_limit=3
                )
                children.append(child_issue)
        observed = {
            "git": git,
            "feature": feature,
            "relationships": relationships,
            "direct_children": bounded_list(children, item_limit=MAX_CHILDREN),
        }
        gates = _issue_gates(
            feature,
            relationships,
            expected_title=args.expected_title,
            expected_state="OPEN",
            required_label=None,
            forbidden_label=None,
            expected_type_label="type:feature",
        )
        gates["repository"] = _gate("pass")
        gates["origin_fetch"] = _gate(git.get("origin_fetch", "unknown"))
        gates["audited_main_sha"] = _sha_gate(
            git.get("origin_main_sha"), args.expected_main_sha, "origin/main SHA"
        )
        gates["direct_children_available"] = _gate(
            "pass" if relationships.get("available") else "unknown"
        )
        gates["direct_children_complete"] = _gate(
            "pass" if relationships.get("sub_issues_complete") is True else "unknown",
            "direct-child inventory must be complete",
        )
    direct_children = observed.get("direct_children")
    direct_children_items = (
        direct_children.get("items", []) if isinstance(direct_children, dict) else []
    )
    relationships_value = observed.get("relationships")
    relationships_digest_source = (
        relationships_value if isinstance(relationships_value, dict) else {}
    )
    direct_numbers = [
        child.get("number")
        for child in direct_children_items
        if isinstance(child, dict) and isinstance(child.get("number"), int)
    ]
    observed["direct_child_set_digest"] = relationships_digest_source.get(
        "sub_issue_set_digest"
    ) or sha256_json(sorted(direct_numbers))
    observed["direct_child_evidence_digest"] = sha256_json(direct_children_items)
    observed["relationships_digest"] = sha256_json(relationships_digest_source)
    execution_context = {
        "object_base_sha": git.get("origin_main_sha"),
        "runner": _runner_source(runner, warnings),
    }
    return _base_snapshot(
        operation="feature-audit-snapshot",
        subject={"kind": "feature", "feature_number": args.feature},
        repository=repository,
        observed=observed,
        execution_context=execution_context,
        gates=gates,
        warnings=warnings,
        limitations=limitations,
        operations=runner.counters(),
    )


def _snapshot_path(repo_root: Path, snapshot_id: str) -> Path:
    if not snapshot_id.startswith("ev-") or len(snapshot_id) > 64:
        raise WorkflowToolError("invalid snapshot ID")
    root = require_exact_ignored_directory(repo_root, EVIDENCE_ROOT)
    directory = root / SNAPSHOT_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{snapshot_id}.json"


def _collect_for_recheck(
    previous: Mapping[str, Any],
    repo_root: Path,
    *,
    review_skill_path: str | None = None,
    review_skill_sha256: str | None = None,
) -> dict[str, Any]:
    operation = previous.get("operation")
    subject = previous.get("subject")
    repository = previous.get("repository")
    if not isinstance(subject, dict):
        raise WorkflowToolError("snapshot subject is invalid")
    namespace = argparse.Namespace(
        repository=repository if isinstance(repository, str) else None,
        no_store=False,
        expected_title=None,
        expected_base_sha=None,
        expected_head_sha=None,
        expected_main_sha=None,
        expected_merge_sha=None,
        review_skill_path=review_skill_path,
        review_skill_sha256=review_skill_sha256,
    )
    if operation in {"delivery-readiness", "pr-review-snapshot"}:
        namespace.task = subject.get("task_number")
        namespace.pr = subject.get("pr_number")
        if not isinstance(namespace.task, int) or not isinstance(namespace.pr, int):
            raise WorkflowToolError("snapshot Task/PR identity is invalid")
        current = _task_pr_snapshot(namespace, repo_root, "pr-review-recheck")
    elif operation == "closeout-plan":
        namespace.task = subject.get("task_number")
        namespace.pr = subject.get("pr_number")
        if not isinstance(namespace.task, int) or not isinstance(namespace.pr, int):
            raise WorkflowToolError("snapshot Task/PR identity is invalid")
        observed = previous.get("observed")
        pr = observed.get("pr") if isinstance(observed, Mapping) else None
        if isinstance(pr, Mapping):
            namespace.expected_head_sha = pr.get("head_sha")
            namespace.expected_merge_sha = pr.get("merge_commit_sha")
        current = _closeout_plan(namespace, repo_root)
        current["operation"] = "closeout-final"
    elif operation == "feature-audit-snapshot":
        namespace.feature = subject.get("feature_number")
        if not isinstance(namespace.feature, int):
            raise WorkflowToolError("snapshot Feature identity is invalid")
        current = _feature_snapshot(namespace, repo_root)
        current["operation"] = "feature-audit-recheck"
    else:
        raise WorkflowToolError(
            f"snapshot operation does not support recheck: {operation}"
        )
    return current


def _stability_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    observed = snapshot.get("observed", {})
    if not isinstance(observed, dict):
        return {}
    raw_pr = observed.get("pr")
    pr: dict[str, Any] = raw_pr if isinstance(raw_pr, dict) else {}
    raw_issue = observed.get("issue")
    raw_feature = observed.get("feature")
    issue: dict[str, Any] = (
        raw_issue
        if isinstance(raw_issue, dict)
        else raw_feature
        if isinstance(raw_feature, dict)
        else {}
    )
    raw_git = observed.get("git")
    git: dict[str, Any] = raw_git if isinstance(raw_git, dict) else {}
    raw_threads = observed.get("review_threads")
    threads: dict[str, Any] = raw_threads if isinstance(raw_threads, dict) else {}
    return {
        "repository": snapshot.get("repository"),
        "subject": snapshot.get("subject"),
        "issue_number": issue.get("number") if isinstance(issue, dict) else None,
        "issue_title": issue.get("title") if isinstance(issue, dict) else None,
        "issue_state": issue.get("state") if isinstance(issue, dict) else None,
        "issue_closed_at": issue.get("closed_at") if isinstance(issue, dict) else None,
        "issue_closure": issue.get("issue_closure")
        if isinstance(issue, dict)
        else None,
        "issue_content_sha256": issue.get("content_sha256")
        if isinstance(issue, dict)
        else None,
        "issue_metadata_sha256": sha256_json(
            {
                "labels": issue.get("labels") if isinstance(issue, dict) else None,
                "project_status": issue.get("project_status")
                if isinstance(issue, dict)
                else None,
                "relationships": observed.get("relationships"),
            }
        ),
        "origin_main_sha": git.get("origin_main_sha"),
        "base_sha": pr.get("base_sha"),
        "head_sha": pr.get("head_sha"),
        "merge_commit_sha": pr.get("merge_commit_sha"),
        "pr_content_sha256": pr.get("content_sha256"),
        "pr_review_metadata_sha256": sha256_json(
            {
                "state": pr.get("state"),
                "is_draft": pr.get("is_draft"),
                "mergeable": pr.get("mergeable"),
                "review_decision": pr.get("review_decision"),
                "reviews": pr.get("reviews"),
                "closing_issues": pr.get("closing_issues"),
            }
        ),
        "effective_diff_sha256": observed.get("effective_diff", {}).get("sha256")
        if isinstance(observed.get("effective_diff"), dict)
        else None,
        "checks": pr.get("checks") if isinstance(pr, dict) else None,
        "required_checks": observed.get("required_checks"),
        "unresolved_threads": threads.get("unresolved"),
        "direct_child_set_digest": observed.get("direct_child_set_digest"),
        "direct_child_evidence_digest": observed.get("direct_child_evidence_digest"),
        "relationships_digest": observed.get("relationships_digest"),
    }


def _recheck(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    path = _snapshot_path(repo_root, args.snapshot_id)
    previous = read_json_file(path)
    if not isinstance(previous, dict):
        raise WorkflowToolError("snapshot is not an object")
    review_skill_path = getattr(args, "review_skill_path", None)
    review_skill_sha256 = getattr(args, "review_skill_sha256", None)
    previous_skill = previous.get("review_skill")
    if isinstance(previous_skill, Mapping):
        if review_skill_path != previous_skill.get(
            "path"
        ) or review_skill_sha256 != previous_skill.get("sha256"):
            raise WorkflowToolError("recheck Review Skill identity drifted")
    elif review_skill_path is not None or review_skill_sha256 is not None:
        raise WorkflowToolError("initial snapshot has no Review Skill identity")
    current = _collect_for_recheck(
        previous,
        repo_root,
        review_skill_path=review_skill_path,
        review_skill_sha256=review_skill_sha256,
    )
    before = _stability_projection(previous)
    after = _stability_projection(current)
    changed = sorted(
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    )
    drift = {
        "stable": len(changed) == 0,
        "changed_fields": bounded_list(changed),
        "previous_snapshot_id": args.snapshot_id,
        "previous_fingerprint": sha256_json(before),
        "current_fingerprint": sha256_json(after),
    }
    current["stability"] = drift
    current["gates"] = dict(current.get("gates", {}))
    current["gates"]["snapshot_stability"] = _gate(
        "pass" if not changed else "fail", ", ".join(changed)
    )
    if current.get("operation") == "closeout-final":
        observed = current.get("observed")
        gates = current.get("gates")
        if isinstance(observed, dict) and isinstance(gates, dict):
            cleanup = observed.get("branch_cleanup")
            pr = observed.get("pr")
            branch = pr.get("head_branch") if isinstance(pr, dict) else None
            if isinstance(cleanup, dict):
                cleanup["cleanup_eligibility"] = _closeout_cleanup_eligibility(
                    repository=current.get("repository")
                    if isinstance(current.get("repository"), str)
                    else None,
                    observed=observed,
                    gates=gates,
                    branch=branch,
                    remote_tip=cleanup.get("remote_tip")
                    if isinstance(cleanup.get("remote_tip"), str)
                    else None,
                    local_tip=cleanup.get("local_tip")
                    if isinstance(cleanup.get("local_tip"), str)
                    else None,
                    tree_equal=cleanup.get("tree_equal")
                    if isinstance(cleanup.get("tree_equal"), bool)
                    else None,
                )
                eligibility = cleanup["cleanup_eligibility"]
                required = observed.get("required_checks")
                if (
                    isinstance(required, dict)
                    and required.get("configuration") == "plan-limited-403"
                ):
                    gates["capability_limited_cleanup_eligibility"] = _gate(
                        "pass"
                        if eligibility["status"]
                        == "eligible-under-capability-limited-policy"
                        else "fail",
                        "exact task branch cleanup only; required checks remain unknown",
                    )
    current["snapshot_id"] = (
        f"ev-{sha256_json({key: value for key, value in current.items() if key != 'snapshot_id'})[:16]}"
    )
    return current


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=".", help="repository root")
    parser.add_argument("--repository", help="GitHub owner/repository override")
    parser.add_argument(
        "--no-store", action="store_true", help="do not write ignored snapshot file"
    )
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")


def _task_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--expected-title")


def _pr_args(parser: argparse.ArgumentParser) -> None:
    _task_args(parser)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--expected-base-sha")
    parser.add_argument("--expected-head-sha")


def _review_skill_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--review-skill-path")
    parser.add_argument("--review-skill-sha256")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate compact, read-only workflow evidence snapshots."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    emit_handoff = sub.add_parser(
        "emit-review-handoff",
        help="materialize one content-addressed independent-review handoff",
    )
    emit_handoff.add_argument("--repo-root", default=".")
    emit_handoff.add_argument("--payload", required=True)

    review_terminal = sub.add_parser(
        "review-terminal",
        help="finalize a stable independent review and emit canonical evidence",
    )
    review_terminal.add_argument("--repo-root", default=".")
    review_terminal.add_argument("--repository", required=True)
    review_terminal.add_argument("--task", type=int, required=True)
    review_terminal.add_argument("--pr", type=int, required=True)
    review_terminal.add_argument("--expected-base-sha", required=True)
    review_terminal.add_argument("--expected-head-sha", required=True)
    review_terminal.add_argument("--effective-diff-sha256", required=True)
    review_terminal.add_argument("--review-snapshot-id", required=True)
    review_terminal.add_argument("--recheck-snapshot-id", required=True)
    review_terminal.add_argument("--payload", required=True)
    _review_skill_args(review_terminal)

    delivery = sub.add_parser(
        "delivery-preflight", help="Task and repository preflight snapshot"
    )
    _common(delivery)
    _task_args(delivery)
    delivery.add_argument("--expected-main-sha")
    delivery.add_argument(
        "--entry-point",
        default="delivery-start",
        choices=list(DELIVERY_ENTRY_POINTS),
        help="stable semantic entry point for this delivery invocation",
    )
    delivery.add_argument("--branch", help="expected Task branch name")
    delivery.add_argument("--expected-base-sha", help="expected branch base SHA")
    delivery.add_argument("--expected-head-sha", help="expected branch head SHA")
    delivery.add_argument("--pr", type=int, help="expected PR number")
    delivery.add_argument(
        "--review-handoff-id", help="explicit content-addressed review handoff ID"
    )

    readiness = sub.add_parser("delivery-readiness", help="Task PR readiness snapshot")
    _common(readiness)
    _pr_args(readiness)

    review = sub.add_parser(
        "pr-review-snapshot", help="independent PR review metadata snapshot"
    )
    _common(review)
    _pr_args(review)
    _review_skill_args(review)

    closeout = sub.add_parser(
        "closeout-plan", help="read-only post-merge closeout plan"
    )
    _common(closeout)
    _task_args(closeout)
    closeout.add_argument("--pr", type=int, required=True)
    closeout.add_argument("--expected-head-sha")
    closeout.add_argument("--expected-merge-sha")

    feature = sub.add_parser(
        "feature-audit-snapshot", help="Feature inventory and locked-main snapshot"
    )
    _common(feature)
    feature.add_argument("--feature", type=int, required=True)
    feature.add_argument("--expected-title")
    feature.add_argument("--expected-main-sha")

    for name, help_text in (
        ("pr-review-recheck", "recollect and compare a PR review snapshot"),
        ("closeout-final", "recollect and compare a closeout plan"),
        ("feature-audit-recheck", "recollect and compare a Feature audit snapshot"),
    ):
        recheck = sub.add_parser(name, help=help_text)
        _common(recheck)
        recheck.add_argument("--snapshot-id", required=True)
        if name == "pr-review-recheck":
            _review_skill_args(recheck)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    try:
        if args.command == "emit-review-handoff":
            payload_path = _safe_handoff_staging_path(repo_root, args.payload)
            payload = read_json_file(payload_path)
            if not isinstance(payload, Mapping):
                raise WorkflowToolError("review handoff payload must be a JSON object")
            evidence_id, destination = _materialize_review_handoff(repo_root, payload)
            print_json(
                {
                    "schema_version": REVIEW_HANDOFF_SCHEMA_VERSION,
                    "evidence_id": evidence_id,
                    "reference": destination.relative_to(repo_root).as_posix(),
                }
            )
            return 0
        if args.command == "review-terminal":
            print_json(_emit_review_terminal(args, repo_root))
            return 0
        if args.command == "delivery-preflight":
            snapshot = _delivery_preflight(args, repo_root)
        elif args.command == "delivery-readiness":
            snapshot = _task_pr_snapshot(args, repo_root, "delivery-readiness")
        elif args.command == "pr-review-snapshot":
            snapshot = _task_pr_snapshot(args, repo_root, "pr-review-snapshot")
        elif args.command == "closeout-plan":
            snapshot = _closeout_plan(args, repo_root)
        elif args.command == "feature-audit-snapshot":
            snapshot = _feature_snapshot(args, repo_root)
        else:
            snapshot = _recheck(args, repo_root)
        output = _snapshot_output(repo_root, snapshot, no_store=args.no_store)
        print_json(output, pretty=args.pretty)
        return 0
    except WorkflowToolError as exc:
        print(f"workflow evidence error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
