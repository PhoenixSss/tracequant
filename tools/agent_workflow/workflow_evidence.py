#!/usr/bin/env python3
"""Compact, read-only evidence snapshots for repository workflow Skills.

The tool normalizes deterministic Git and GitHub facts. It never mutates GitHub,
commits, pushes, merges, closes Issues, or deletes branches. Semantic review and
workflow verdicts remain the responsibility of the governing Skill.
"""

from __future__ import annotations

import argparse
import os
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
    fields = "number,title,body,comments,state,labels,projectItems,url,closedAt,closedByPullRequestsReferences"
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
                    "merged_at": safe_text(pr.get("mergedAt")),
                    "url": safe_text(pr.get("url")),
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
    return {
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
        "reviews,closingIssuesReferences"
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
        issue_refs = issue.get("closing_pull_requests")
        ref_items = issue_refs.get("items") if isinstance(issue_refs, Mapping) else []
        expected_pr = pr.get("number")
        if not (
            isinstance(ref_items, list)
            and any(
                isinstance(item, Mapping)
                and item.get("number") == expected_pr
                and str(item.get("state", "")).upper() == "MERGED"
                for item in ref_items
            )
        ):
            reasons.append("Issue closure is not linked to the merged PR")
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
            "resource not accessible by integration" in combined
            or "upgrade" in combined
            or "plan" in combined
            or "branch protection" in combined
            and "private repositories" in combined
        ):
            category = "plan-limit"
            reason = "github-plan-limit-403"
        elif "rate limit" in combined or "secondary rate" in combined:
            category = "rate-limit"
            reason = "github-rate-limit-403"
        elif "scope" in combined or "sso" in combined:
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
    trusted_source = __import__("os").environ.get("WORKFLOW_TRUSTED_RUNNER_SHA")
    trusted_digest = __import__("os").environ.get(
        "WORKFLOW_TRUSTED_TOOL_CONTENT_SHA256"
    )
    commit = None
    if not is_sha(trusted_source):
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
        "source_sha": trusted_source
        if is_sha(trusted_source)
        else commit
        if is_sha(commit)
        else None,
        "content_sha256": trusted_digest if isinstance(trusted_digest, str) else digest,
        "trusted_bootstrap": is_sha(trusted_source),
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
    trusted: Mapping[str, Any],
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
        "trusted_control": dict(trusted),
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


def _delivery_preflight(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
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
        gates = _issue_gates(
            issue,
            relationships,
            expected_title=args.expected_title,
            expected_state="OPEN",
            required_label="codex:ready",
            forbidden_label="codex:blocked",
            expected_type_label="type:task",
        )
        gates["repository"] = _gate("pass")
        gates["origin_fetch"] = _gate(git.get("origin_fetch", "unknown"))
        gates["origin_main"] = _sha_gate(
            git.get("origin_main_sha"), args.expected_main_sha, "origin/main SHA"
        )
        gates["worktree_clean"] = _gate("pass" if git.get("clean") is True else "fail")
    trusted = {
        "trusted_sha": observed["git"].get("origin_main_sha"),
        "runner": _runner_source(runner, warnings),
    }
    return _base_snapshot(
        operation="delivery-preflight",
        subject={"kind": "task", "task_number": args.task},
        repository=repository,
        observed=observed,
        trusted=trusted,
        gates=gates,
        warnings=warnings,
        limitations=limitations,
        operations=runner.counters(),
    )


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
            project = issue.get("project_status")
            gates["project_status_review"] = _gate(
                "pass" if project == "Review" else "unknown", str(project)
            )
    pr = observed.get("pr")
    trusted_sha = pr.get("base_sha") if isinstance(pr, dict) else None
    trusted = {"trusted_sha": trusted_sha, "runner": _runner_source(runner, warnings)}
    return _base_snapshot(
        operation=operation,
        subject={"kind": "task-pr", "task_number": args.task, "pr_number": args.pr},
        repository=repository,
        observed=observed,
        trusted=trusted,
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
    trusted_sha = pr.get("merge_commit_sha") if isinstance(pr, dict) else None
    trusted = {"trusted_sha": trusted_sha, "runner": _runner_source(runner, warnings)}
    return _base_snapshot(
        operation="closeout-plan",
        subject={"kind": "task-pr", "task_number": args.task, "pr_number": args.pr},
        repository=repository,
        observed=observed,
        trusted=trusted,
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
    trusted = {
        "trusted_sha": git.get("origin_main_sha"),
        "runner": _runner_source(runner, warnings),
    }
    return _base_snapshot(
        operation="feature-audit-snapshot",
        subject={"kind": "feature", "feature_number": args.feature},
        repository=repository,
        observed=observed,
        trusted=trusted,
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
    previous: Mapping[str, Any], repo_root: Path
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
    current = _collect_for_recheck(previous, repo_root)
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
        ("pr-review-recheck", "recollect and compare a PR review snapshot"),
        ("closeout-final", "recollect and compare a closeout plan"),
        ("feature-audit-recheck", "recollect and compare a Feature audit snapshot"),
    ):
        recheck = sub.add_parser(name, help=help_text)
        _common(recheck)
        recheck.add_argument("--snapshot-id", required=True)
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
