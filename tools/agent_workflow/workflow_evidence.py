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

from review_fact_handoff import (
    WORKFLOW_CONTROL_PATHS,
    ReviewFactHandoffError,
    acquire_current_validation_facts,
    load_handoff,
    resolve_handoff_path,
    source_identity_for_snapshot,
    validate_against_snapshot,
)
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
WORKFLOW_PROFILES: Final = {
    "delivery-readiness": "delivery-readiness",
    "delivery-readiness-recheck": "delivery-readiness",
    "pr-review-snapshot": "review",
    "pr-review-recheck": "review",
}
EVIDENCE_ROOT: Final = ".agents/evidence.local"
SNAPSHOT_SUBDIR: Final = "snapshots"
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
DELIVERY_OPTIONAL_PARAMS: Final = {
    "implementation": frozenset({"bootstrap_verify"}),
}
DELIVERY_PARAM_SPACE: Final = frozenset(
    {
        "task",
        "expected_main_sha",
        "branch",
        "pr",
        "expected_base_sha",
        "expected_head_sha",
        "bootstrap_verify",
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


def _acceptance_criteria_ids(body: str | None) -> list[str] | None:
    """Extract deterministic AC identifiers from the current Task body."""
    if not isinstance(body, str):
        return None
    in_section = False
    identifiers: list[str] = []
    for line in body.splitlines():
        heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if heading:
            title = _normalize_title(heading.group(1)).rstrip(":：")
            in_section = title in {
                "acceptance criteria",
                "acceptance criterion",
                "验收标准",
                "验收条件",
            }
            continue
        if in_section and re.match(r"^\s*[-*]\s+\[[ xX]\]\s+", line):
            identifiers.append(f"AC-{len(identifiers) + 1}")
    return identifiers if in_section or identifiers else None


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
        "spec_sha256": sha256_bytes(body.encode("utf-8")) if body is not None else None,
        "acceptance_criteria_ids": _acceptance_criteria_ids(body),
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
                "run_id": safe_text(
                    item.get("databaseId") or item.get("id"), limit=160
                ),
                "started_at": safe_text(item.get("startedAt")),
                "completed_at": safe_text(item.get("completedAt")),
                "source_url": safe_text(
                    item.get("detailsUrl") or item.get("url"), limit=512
                ),
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
    optional = DELIVERY_OPTIONAL_PARAMS.get(entry_point, frozenset())
    supplied = {
        name for name in DELIVERY_PARAM_SPACE if getattr(args, name, None) is not None
    }
    missing = sorted(allowed - supplied)
    extra = sorted(supplied - allowed - optional)
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
    """Prove that a supplied branch name identifies the current Task.

    Canonical ``task/<number>-<slug>`` names are required for creation.  The
    older numeric forms remain valid only for an existing branch that is
    independently proven safe to reuse.
    """
    if not isinstance(branch, str) or not branch:
        return _gate("unknown", "Task branch name unavailable")
    if (
        branch == f"task/{task}"
        or branch.startswith(f"task/{task}-")
        or branch.startswith(f"{task}-")
        or branch == f"task-{task}"
        or branch.startswith(f"task-{task}-")
    ):
        return _gate("pass", f"branch carries Task #{task} identity")
    return _gate("fail", f"branch {branch!r} does not identify Task #{task}")


def _is_canonical_new_task_branch(branch: str, task: int) -> bool:
    prefix = f"task/{task}-"
    suffix = branch[len(prefix) :] if branch.startswith(prefix) else ""
    return bool(suffix) and "/" not in suffix


def _worktree_branch_items(git: Mapping[str, Any]) -> tuple[list[str], bool]:
    raw = git.get("worktree_branches")
    if not isinstance(raw, Mapping):
        return [], False
    items = raw.get("items")
    return (
        [item for item in items if isinstance(item, str)]
        if isinstance(items, list)
        else [],
        raw.get("truncated") is True,
    )


def _branch_bootstrap_gate(
    *,
    git: Mapping[str, Any],
    branch: str | None,
    task: int,
    expected_main_sha: str | None,
    expected_base_sha: str | None,
    branch_exists: bool | None,
    local_tip: str | None,
    remote_tip: str | None,
    remote_available: bool,
    bootstrap_verify: bool,
) -> dict[str, Any]:
    """Classify safe Task branch creation, reuse, or fail-closed state.

    This function is deterministic and read-only.  The Delivery Skill owns the
    branch creation/reuse procedure after the returned authorization.
    """
    identity = _task_branch_identity_gate(branch, task)
    if identity.get("status") != "pass":
        return identity
    if not isinstance(branch, str) or branch_exists is None:
        return _gate("unknown", "Task branch existence could not be established")

    worktree_items, worktree_truncated = _worktree_branch_items(git)
    reasons: list[str] = []
    if worktree_truncated:
        reasons.append("worktree branch inventory is truncated")
    if branch in worktree_items and git.get("branch") != branch:
        reasons.append("target branch is occupied by another worktree")
    if branch_exists and git.get("clean") is not True:
        return _gate("fail", "existing branch reuse requires a clean worktree")

    if not branch_exists:
        if bootstrap_verify:
            return _gate("fail", "bootstrap verification requires the branch to exist")
        if not _is_canonical_new_task_branch(branch, task):
            return _gate(
                "fail",
                "new Task branches must use canonical task/<issue>-<slug> naming",
            )
        if not remote_available:
            reasons.append("remote branch existence unavailable")
        elif remote_tip is not None:
            reasons.append("remote branch already exists; ownership is ambiguous")
        if git.get("clean") is not True:
            reasons.append("new branch bootstrap requires a clean worktree")
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
        return _gate(
            "pass",
            f"branch {branch!r} absent; branch creation authorized from locked base",
        )

    if not remote_available:
        return _gate("unknown", "existing branch remote state unavailable")
    if local_tip is None:
        return _gate("unknown", "existing branch tip unavailable")
    if remote_tip is not None and remote_tip != local_tip:
        reasons.append("local and remote Task branch tips differ")
    if bootstrap_verify:
        if git.get("branch") != branch:
            reasons.append(
                "post-bootstrap current branch does not match expected branch"
            )
        if git.get("head_sha") != expected_base_sha:
            reasons.append("post-bootstrap HEAD does not equal locked base SHA")
        if local_tip != expected_base_sha:
            reasons.append("post-bootstrap branch tip does not equal locked base SHA")
        if git.get("local_main_sha") != expected_main_sha:
            reasons.append("post-bootstrap local main does not equal locked main SHA")
        if git.get("origin_main_sha") != expected_main_sha:
            reasons.append("post-bootstrap origin/main does not equal locked main SHA")
        if remote_tip is not None:
            reasons.append("post-bootstrap remote branch unexpectedly exists")
        if git.get("clean") is not True:
            reasons.append("post-bootstrap worktree is not clean")
    if reasons:
        return _gate("fail", "; ".join(reasons))
    if bootstrap_verify:
        return _gate(
            "pass",
            f"post-bootstrap branch {branch!r}, HEAD, base, and clean state verified",
        )
    return _gate("pass", f"existing branch {branch!r} is eligible for idempotent reuse")


def _entry_point_gates(
    args: argparse.Namespace,
    runner: CommandRunner,
    repository: str | None,
    observed: Mapping[str, Any],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    entry_point = args.entry_point
    git = observed.get("git")
    git = git if isinstance(git, dict) else {}

    if entry_point in {"implementation", "final-validation", "pr-readiness"}:
        branch = args.branch
        bootstrap_verify = bool(getattr(args, "bootstrap_verify", False))
        branch_exists = runner.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            command_id="git-branch-exists",
        )
        branch_is_present: bool | None
        if branch_exists.returncode == 0:
            branch_is_present = True
            gates["branch_exists"] = _gate("pass")
        elif branch_exists.returncode == 1:
            branch_is_present = False
            if entry_point == "implementation":
                gates["branch_exists"] = _gate(
                    "pass",
                    f"branch {branch!r} absent; bootstrap classification required",
                )
            else:
                gates["branch_exists"] = _gate(
                    "fail", f"branch {branch!r} does not exist"
                )
        else:
            branch_is_present = None
            gates["branch_exists"] = _gate("unknown")

        local_branch_tip: str | None = None
        remote_tip: str | None = None
        remote_available = True
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
                bootstrap_verify=bootstrap_verify,
            )
            bootstrap_gate = gates["branch_bootstrap"]
            if branch_is_present:
                if bootstrap_gate.get("status") == "pass":
                    gates["branch_state"] = _gate(
                        "pass",
                        "post-bootstrap verification requested"
                        if bootstrap_verify
                        else "existing branch reuse authorized",
                    )
                else:
                    gates["branch_state"] = bootstrap_gate
            elif branch_is_present is False:
                gates["branch_state"] = bootstrap_gate
            else:
                gates["branch_state"] = _gate(
                    "unknown", "branch state classification unavailable"
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
        elif (
            args.expected_base_sha
            and repository
            and entry_point == "implementation"
            and branch_is_present is False
        ):
            gates["branch_base"] = _gate(
                "pass" if args.expected_base_sha == args.expected_main_sha else "fail",
                "locked base is the source for safe branch bootstrap"
                if args.expected_base_sha == args.expected_main_sha
                else "expected Task base is not the locked main SHA",
            )

        if bootstrap_verify and branch_is_present:
            gates["bootstrap_head"] = _gate(
                "pass"
                if git.get("head_sha") == args.expected_base_sha
                and local_branch_tip == args.expected_base_sha
                else "fail",
                "post-bootstrap HEAD and branch tip equal locked base SHA"
                if git.get("head_sha") == args.expected_base_sha
                and local_branch_tip == args.expected_base_sha
                else "post-bootstrap HEAD or branch tip drifted from locked base SHA",
            )

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
            observed_copy = dict(observed)
            observed_copy["pr"] = pr
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
                reviews = pr.get("reviews", {})
                review_items = reviews.get("items") if isinstance(reviews, dict) else []
                review_items = review_items if isinstance(review_items, list) else []
                gates["review_conclusion"] = _gate(
                    "pass",
                    (
                        "GitHub submitted Review not required: Independent Review is "
                        "read-only; Delivery Skill validates the bounded handoff; this "
                        "preflight locks PR/base/head identity "
                        f"(observed_reviews={len(review_items)})."
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
    remote_branch_state: str | None,
    local_exists: bool | None,
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
        "effective_diff_identity",
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
    if remote_branch_state == "PRESENT":
        if remote_tip != expected_head:
            reasons.append("remote branch tip does not match reviewed PR head")
    elif remote_branch_state == "ALREADY_DELETED":
        if not _gate_pass(gates, "remote_branch_tip"):
            reasons.append(
                "remote branch was auto-deleted but its replacement proof is not complete"
            )
    else:
        reasons.append("remote branch state is unavailable")
    if local_exists is not True:
        reasons.append("local task branch is unavailable")
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


def _squash_merge_identity_proven(
    *,
    repository: str | None,
    expected_pr_number: int | None,
    observed: Mapping[str, Any],
    gates: Mapping[str, Any],
    local_exists: bool,
    local_tip: str | None,
    tree_equal: bool | None,
) -> bool:
    """Prove the reviewed PR and squash-merged result have one identity.

    This deliberately checks the merged result and the reviewed PR identity,
    rather than requiring the squash-merged PR head to be an ancestor of main.
    """
    pr = observed.get("pr")
    if not isinstance(pr, Mapping):
        return False
    expected_head = pr.get("head_sha")
    merge_sha = pr.get("merge_commit_sha")
    if not is_sha(expected_head) or not is_sha(merge_sha):
        return False
    if expected_pr_number is not None and pr.get("number") != expected_pr_number:
        return False
    head_repository = pr.get("head_repository")
    if head_repository is not None and head_repository != repository:
        return False
    if not local_exists or local_tip != expected_head or tree_equal is not True:
        return False
    effective_diff = observed.get("effective_diff")
    if not isinstance(effective_diff, Mapping):
        return False
    if effective_diff.get("available") is not True or not is_sha(
        effective_diff.get("sha256")
    ):
        return False
    required_gates = (
        "pr_state",
        "head_sha",
        "merge_sha",
        "pull_request_merge",
        "main_contains_merge",
        "local_main_synced",
        "local_branch_tip",
        "head_merge_tree_equal",
        "effective_diff_identity",
    )
    return all(_gate_pass(gates, name) for name in required_gates)


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
    changed_files: list[str] = []
    for line in result.stdout.splitlines():
        if not line.startswith("diff --git a/"):
            continue
        marker = line[len("diff --git a/") :]
        separator = marker.find(" b/")
        if separator > 0:
            changed_files.append(marker[separator + 3 :])
    return {
        "available": True,
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "lines": result.stdout.count("\n"),
        "changed_files": bounded_list(sorted(set(changed_files)), item_limit=MAX_FILES),
    }


def _runner_source(
    runner: CommandRunner,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    script = Path(__file__).resolve()
    handoff_script = script.with_name("review_fact_handoff.py")
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
        "handoff_schema": {
            "path": "tools/agent_workflow/review_fact_handoff.py",
            "content_sha256": sha256_bytes(handoff_script.read_bytes()),
        },
    }


def _workflow_control_plane_identity() -> dict[str, dict[str, str]]:
    """Hash the stable control plane that defines Review evidence semantics."""
    repo_root = Path(__file__).resolve().parents[2]
    identity: dict[str, dict[str, str]] = {}
    for name, relative in WORKFLOW_CONTROL_PATHS.items():
        target = repo_root / relative
        if target.is_symlink() or not target.is_file():
            raise WorkflowToolError(
                f"workflow control-plane file is unavailable: {relative}"
            )
        identity[name] = {
            "path": relative,
            "content_sha256": sha256_bytes(target.read_bytes()),
        }
    return identity


def _skill_identity(
    repo_root: Path,
    value: str | None,
    *,
    default: str = ".agents/skills/task-pr-review-runner/SKILL.md",
) -> dict[str, Any]:
    relative = value or default
    normalized = relative.replace("\\", "/")
    if (
        normalized.startswith("/")
        or ".." in normalized
        or not normalized.endswith("/SKILL.md")
        or not normalized.startswith((".agents/skills/", ".claude/skills/"))
    ):
        raise WorkflowToolError("workflow Skill path is invalid")
    path = repo_root / normalized
    if path.is_symlink() or not path.is_file():
        # The outer WSL2 Runner verifies the caller Skill before collection.
        # Keep the lower-level evidence tool usable in isolated test fixtures
        # that intentionally contain only the workflow identity files.
        return {"path": normalized, "sha256": None}
    return {"path": normalized, "sha256": sha256_bytes(path.read_bytes())}


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
    git: Mapping[str, Any], *, entry_point: str, bootstrap_verify: bool = False
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

    dirty_allowed = entry_point in {"delivery-start", "implementation"} and not (
        entry_point == "implementation" and bootstrap_verify
    )
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
            git,
            entry_point=args.entry_point,
            bootstrap_verify=bool(getattr(args, "bootstrap_verify", False)),
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
        gates.update(_entry_point_gates(args, runner, repository, observed, warnings))
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
    is_review = operation in {"pr-review-snapshot", "pr-review-recheck"}
    skill_default = (
        ".agents/skills/task-pr-review-runner/SKILL.md"
        if is_review
        else ".agents/skills/task-delivery-runner/SKILL.md"
    )
    workflow_identity = {
        "profile": WORKFLOW_PROFILES[operation],
        "schema_version": SCHEMA_VERSION,
        "runner": _runner_source(runner, warnings),
        "skill": _skill_identity(
            repo_root, getattr(args, "skill_path", None), default=skill_default
        ),
        "control_plane": _workflow_control_plane_identity(),
    }
    execution_context = {
        "object_base_sha": object_base_sha,
        "runner": workflow_identity["runner"],
        "workflow_identity": workflow_identity,
    }
    if is_review:
        handoff_path = getattr(args, "handoff_path", None)
        source_observed = dict(observed)
        source_observed["review_fact_handoff"] = {
            "available": False,
            "status": "not-selected",
            "trusted": False,
            "strategy": "FULL_REACQUISITION",
        }
        source_snapshot = _base_snapshot(
            operation=operation,
            subject={
                "kind": "task-pr",
                "task_number": args.task,
                "pr_number": args.pr,
            },
            repository=repository,
            observed=source_observed,
            execution_context=execution_context,
            gates=gates,
            warnings=warnings,
            limitations=limitations,
            operations=runner.counters(),
        )
        if isinstance(handoff_path, str):
            try:
                path = resolve_handoff_path(repo_root, handoff_path)
                handoff, handoff_digest = load_handoff(
                    path, expected_repository=repository
                )
                pr_head_sha = pr.get("head_sha") if isinstance(pr, Mapping) else None
                current_validation_facts, validation_errors = (
                    acquire_current_validation_facts(
                        repo_root,
                        handoff["validation_facts"],
                        expected_base_sha=pr.get("base_sha")
                        if isinstance(pr.get("base_sha"), str)
                        else None,
                        expected_head_sha=pr_head_sha
                        if isinstance(pr_head_sha, str)
                        else None,
                    )
                )
                handoff_status = validate_against_snapshot(
                    handoff,
                    source_snapshot,
                    handoff_digest=handoff_digest,
                    current_acceptance_criteria_ids=(
                        issue.get("acceptance_criteria_ids")
                        if isinstance(issue, dict)
                        and isinstance(issue.get("acceptance_criteria_ids"), list)
                        else None
                    ),
                    current_validation_facts=current_validation_facts,
                    current_workflow_identity=workflow_identity,
                    current_source_identity=source_identity_for_snapshot(
                        source_snapshot
                    ),
                )
                if validation_errors:
                    handoff_status["invalidated"] = list(
                        dict.fromkeys(
                            handoff_status["invalidated"]
                            + [
                                f"VALIDATION_DRIFT: {item}"
                                for item in validation_errors
                            ]
                        )
                    )
                    handoff_status["status"] = "fail"
                    handoff_status["trusted"] = False
                    handoff_status["strategy"] = "FAIL_CLOSED"
                handoff_status["path"] = handoff_path
                observed["review_fact_handoff"] = handoff_status
                gates["review_fact_handoff"] = _gate(
                    handoff_status["status"],
                    "; ".join(handoff_status["invalidated"]),
                )
            except ReviewFactHandoffError as exc:
                observed["review_fact_handoff"] = {
                    "available": False,
                    "status": "fail",
                    "trusted": False,
                    "strategy": "FAIL_CLOSED",
                    "path": handoff_path,
                    "invalidated": [str(exc)],
                }
                gates["review_fact_handoff"] = _gate("fail", str(exc))
        else:
            observed["review_fact_handoff"] = source_observed["review_fact_handoff"]
    return _base_snapshot(
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
        effective_diff = observed.get("effective_diff")
        gates["effective_diff_identity"] = _gate(
            "pass"
            if isinstance(effective_diff, dict)
            and effective_diff.get("available") is True
            and is_sha(effective_diff.get("sha256"))
            else "unknown",
            "PR effective diff digest must be available",
        )
        branch = pr.get("head_branch") if isinstance(pr, dict) else None
        if isinstance(branch, str) and branch:
            remote_result = runner.run(
                ["git", "ls-remote", "--heads", "origin", branch],
                command_id="git-closeout-remote-branch",
            )
            if remote_result.returncode != 0:
                warnings.append(command_warning(remote_result))
                remote_branch_state = "UNKNOWN"
                remote_tip = None
            elif not remote_result.stdout.strip():
                remote_branch_state = "ALREADY_DELETED"
                remote_tip = None
            else:
                remote_tip = _remote_branch_tip(remote_result.stdout)
                remote_branch_state = "PRESENT" if remote_tip is not None else "UNKNOWN"
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
            tree_head_sha = (
                args.expected_head_sha
                if isinstance(args.expected_head_sha, str)
                else pr.get("head_sha")
                if isinstance(pr, dict)
                else None
            )
            if isinstance(tree_head_sha, str) and isinstance(merge_sha, str):
                tree = runner.run(
                    ["git", "diff", "--quiet", tree_head_sha, merge_sha],
                    command_id="git-closeout-tree-equality",
                )
                if tree.returncode == 0:
                    tree_equal = True
                elif tree.returncode == 1:
                    tree_equal = False
                else:
                    warnings.append(command_warning(tree))
            expected_head = pr.get("head_sha") if isinstance(pr, dict) else None
            gates["local_branch_tip"] = _gate(
                "pass" if local_tip == expected_head else "fail",
                "local task branch tip must match the recorded PR head",
            )
            gates["head_merge_tree_equal"] = _gate(
                "pass" if tree_equal is True else "fail",
                "PR head tree must equal the squash merge tree",
            )
            squash_identity_proven = _squash_merge_identity_proven(
                repository=repository,
                expected_pr_number=args.pr,
                observed=observed,
                gates=gates,
                local_exists=local_result.returncode == 0,
                local_tip=local_tip if is_sha(local_tip) else None,
                tree_equal=tree_equal,
            )
            if remote_branch_state == "PRESENT":
                remote_branch_gate = _gate(
                    "pass"
                    if remote_tip == expected_head and squash_identity_proven
                    else "fail",
                    "remote task branch and squash-merge identities must match",
                )
            elif remote_branch_state == "ALREADY_DELETED":
                remote_branch_gate = _gate(
                    "pass" if squash_identity_proven else "fail",
                    "remote ref absence is accepted only with complete squash-merge identity proof",
                )
            else:
                remote_branch_gate = _gate(
                    "unknown",
                    "remote task branch state could not be established",
                )
            gates["remote_branch_tip"] = remote_branch_gate
            observed["branch_cleanup"] = {
                "exact_branch": safe_text(branch),
                "remote_exists": remote_branch_state == "PRESENT",
                "remote_branch_state": remote_branch_state,
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
                    remote_branch_state=remote_branch_state,
                    local_exists=local_result.returncode == 0,
                    local_tip=local_tip if is_sha(local_tip) else None,
                    tree_equal=tree_equal,
                ),
            }
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
    previous: Mapping[str, Any], repo_root: Path, skill_path: str | None
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
        handoff_path=None,
        skill_path=skill_path,
    )
    previous_observed = previous.get("observed")
    previous_observed = (
        previous_observed if isinstance(previous_observed, Mapping) else {}
    )
    previous_handoff = previous_observed.get("review_fact_handoff")
    if isinstance(previous_handoff, Mapping) and isinstance(
        previous_handoff.get("path"), str
    ):
        namespace.handoff_path = previous_handoff["path"]
    if namespace.skill_path is None:
        previous_context = previous.get("execution_context")
        previous_context = (
            previous_context if isinstance(previous_context, Mapping) else {}
        )
        previous_identity = previous_context.get("workflow_identity")
        previous_identity = (
            previous_identity if isinstance(previous_identity, Mapping) else {}
        )
        previous_skill = previous_identity.get("skill")
        if isinstance(previous_skill, Mapping) and isinstance(
            previous_skill.get("path"), str
        ):
            namespace.skill_path = previous_skill["path"]
    if operation in {"delivery-readiness", "pr-review-snapshot"}:
        namespace.task = subject.get("task_number")
        namespace.pr = subject.get("pr_number")
        if not isinstance(namespace.task, int) or not isinstance(namespace.pr, int):
            raise WorkflowToolError("snapshot Task/PR identity is invalid")
        current_operation = (
            "pr-review-recheck"
            if operation == "pr-review-snapshot"
            else "delivery-readiness-recheck"
        )
        current = _task_pr_snapshot(namespace, repo_root, current_operation)
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
    handoff = observed.get("review_fact_handoff")
    handoff = handoff if isinstance(handoff, dict) else {}
    execution_context = snapshot.get("execution_context")
    execution_context = execution_context if isinstance(execution_context, dict) else {}
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
        "review_fact_handoff": handoff,
        "workflow_identity": execution_context.get("workflow_identity"),
    }


def _recheck(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    path = _snapshot_path(repo_root, args.snapshot_id)
    previous = read_json_file(path)
    if not isinstance(previous, dict):
        raise WorkflowToolError("snapshot is not an object")
    current = _collect_for_recheck(previous, repo_root, args.skill_path)
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
                    remote_branch_state=cleanup.get("remote_branch_state")
                    if isinstance(cleanup.get("remote_branch_state"), str)
                    else None,
                    local_exists=cleanup.get("local_exists")
                    if isinstance(cleanup.get("local_exists"), bool)
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
    parser.add_argument("--handoff-path")
    parser.add_argument("--skill-path")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate compact, read-only workflow evidence snapshots."
    )
    sub = parser.add_subparsers(dest="command", required=True)

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
        "--bootstrap-verify",
        action="store_true",
        default=None,
        help="verify a newly created Task branch before implementation writes",
    )

    readiness = sub.add_parser("delivery-readiness", help="Task PR readiness snapshot")
    _common(readiness)
    _pr_args(readiness)

    review = sub.add_parser(
        "pr-review-snapshot", help="independent PR review metadata snapshot"
    )
    _common(review)
    _pr_args(review)

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
        (
            "delivery-readiness-recheck",
            "recollect and compare a Delivery readiness snapshot",
        ),
        ("pr-review-recheck", "recollect and compare a PR review snapshot"),
        ("closeout-final", "recollect and compare a closeout plan"),
        ("feature-audit-recheck", "recollect and compare a Feature audit snapshot"),
    ):
        recheck = sub.add_parser(name, help=help_text)
        _common(recheck)
        recheck.add_argument("--snapshot-id", required=True)
        recheck.add_argument("--skill-path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    try:
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
