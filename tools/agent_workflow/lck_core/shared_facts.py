"""Authoritative, profile-neutral Git and GitHub fact acquisition.

This module owns the bounded queries and canonical normalization shared by LCK
and Feature audit.  It deliberately does not resolve Issue profiles or parse
profile contracts.  Profile-specific interpretation belongs to
``profile_policies`` and the Feature-audit adapter in ``workflow_evidence``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Final

from workflow_common import (
    CommandResult,
    CommandRunner,
    bounded_list,
    command_warning,
    is_sha,
    parse_repository_slug,
    read_json_text,
    safe_text,
    sha256_json,
)

SCHEMA_VERSION: Final = 1
MAX_CHILDREN: Final = 50
MAX_FILES: Final = 100
CANONICAL_PROJECT_NUMBER: Final = 1

RELATIONSHIPS_QUERY: Final = r"""
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    issue(number:$number) {
      number
      title
      state
      body
      labels(first:100) { nodes { name } pageInfo { hasNextPage } }
      issueType { name }
      parent { number title state }
      subIssues(first:100) {
        nodes {
          number
          title
          state
          body
          labels(first:100) { nodes { name } pageInfo { hasNextPage } }
        }
        pageInfo { hasNextPage }
      }
      blockedBy(first:50) {
        nodes {
          number
          title
          state
          body
          labels(first:100) { nodes { name } pageInfo { hasNextPage } }
          projectItems(first:20) {
            nodes {
              project {
                number
                owner {
                  ... on User { login }
                  ... on Organization { login }
                }
              }
              fieldValues(first:20) {
                nodes {
                  ... on ProjectV2ItemFieldSingleSelectValue {
                    name
                    field { ... on ProjectV2SingleSelectField { name } }
                  }
                }
                pageInfo { hasNextPage }
              }
            }
            pageInfo { hasNextPage }
          }
        }
        pageInfo { hasNextPage }
      }
      blocking(first:50) {
        nodes {
          number
          title
          state
          body
          labels(first:100) { nodes { name } pageInfo { hasNextPage } }
          projectItems(first:20) {
            nodes {
              project {
                number
                owner {
                  ... on User { login }
                  ... on Organization { login }
                }
              }
              fieldValues(first:20) {
                nodes {
                  ... on ProjectV2ItemFieldSingleSelectValue {
                    name
                    field { ... on ProjectV2SingleSelectField { name } }
                  }
                }
                pageInfo { hasNextPage }
              }
            }
            pageInfo { hasNextPage }
          }
        }
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


def normalize_title(value: str) -> str:
    """Normalize titles for mechanical identity comparisons."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def gate(status: str, detail: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status}
    if detail:
        result["detail"] = safe_text(detail, limit=256)
    return result


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
    value = _git_value(runner, args, command_id=command_id, warnings=warnings)
    return [] if value is None else [line for line in value.splitlines() if line]


def _remote_main_sha(
    runner: CommandRunner, warnings: list[dict[str, Any]]
) -> str | None:
    """Read authoritative remote main without changing local refs."""
    result = runner.run(
        ["git", "ls-remote", "origin", "refs/heads/main"],
        command_id="git-remote-main",
        retries=1,
    )
    if result.returncode != 0:
        warnings.append(command_warning(result))
        return None
    matches = [
        line.split("\t", 1)[0]
        for line in result.stdout.splitlines()
        if "\t" in line and line.split("\t", 1)[1] == "refs/heads/main"
    ]
    if len(matches) != 1 or not is_sha(matches[0]):
        warnings.append(
            {
                "command_id": result.command_id,
                "exit_code": result.returncode,
                "error": "authoritative refs/heads/main result is missing or malformed",
            }
        )
        return None
    return matches[0]


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
        retries=1,
    )
    if result.returncode != 0:
        warnings.append(command_warning(result))
        return None
    value = read_json_text(result.stdout, field="gh repo view")
    if isinstance(value, Mapping) and isinstance(value.get("nameWithOwner"), str):
        return str(value["nameWithOwner"])
    return None


def _git_snapshot(
    runner: CommandRunner,
    warnings: list[dict[str, Any]],
    *,
    read_only_local_refs: bool = False,
    include_workspace_inventory: bool = True,
) -> dict[str, Any]:
    """Collect bounded, profile-neutral Git facts."""
    fetch: CommandResult | None = None
    if not read_only_local_refs:
        fetch = runner.run(
            ["git", "fetch", "--prune", "origin"],
            command_id="git-fetch-origin",
            retries=1,
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
        runner, ["rev-parse", "HEAD"], command_id="git-head", warnings=warnings
    )
    local_main = _git_value(
        runner,
        ["rev-parse", "refs/heads/main"],
        command_id="git-local-main",
        warnings=warnings,
    )
    tracking_main = _git_value(
        runner,
        ["rev-parse", "refs/remotes/origin/main"],
        command_id="git-tracking-main",
        warnings=warnings,
    )
    remote_main = _remote_main_sha(runner, warnings)
    status_result = runner.run(
        ["git", "status", "--short", "--untracked-files=all"],
        command_id="git-status",
    )
    if status_result.returncode != 0:
        warnings.append(command_warning(status_result))
        status_lines: list[str] | None = None
    else:
        status_lines = [line for line in status_result.stdout.splitlines() if line]
    staged = (
        _git_lines(
            runner,
            ["diff", "--cached", "--name-only"],
            command_id="git-staged-files",
            warnings=warnings,
        )
        if include_workspace_inventory
        else []
    )
    changed = (
        _git_lines(
            runner,
            ["diff", "--name-only"],
            command_id="git-changed-files",
            warnings=warnings,
        )
        if include_workspace_inventory
        else []
    )
    worktrees = (
        _git_lines(
            runner,
            ["worktree", "list", "--porcelain"],
            command_id="git-worktrees",
            warnings=warnings,
        )
        if include_workspace_inventory
        else []
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
        "origin_refresh": "skipped-read-only" if read_only_local_refs else "attempted",
        "branch": safe_text(branch),
        "head_sha": head if is_sha(head) else None,
        "local_main_sha": local_main if is_sha(local_main) else None,
        "tracking_main_sha": tracking_main if is_sha(tracking_main) else None,
        "remote_main_sha": remote_main,
        "tracking_main_stale": (
            is_sha(tracking_main)
            and is_sha(remote_main)
            and tracking_main != remote_main
        ),
        "remote_main_query": "pass" if is_sha(remote_main) else "unknown",
        "clean": None if status_lines is None else len(status_lines) == 0,
        "status_entries": None if status_lines is None else len(status_lines),
        "staged_files": bounded_list(staged, item_limit=MAX_FILES)
        if include_workspace_inventory
        else None,
        "changed_files": bounded_list(changed, item_limit=MAX_FILES)
        if include_workspace_inventory
        else None,
        "worktree_count": (
            sum(1 for line in worktrees if line.startswith("worktree "))
            if include_workspace_inventory
            else None
        ),
        "worktree_branches": bounded_list(worktree_branches, item_limit=MAX_FILES)
        if include_workspace_inventory
        else None,
    }


def _repository_name(value: Mapping[str, Any]) -> str | None:
    repository = value.get("repository")
    return (
        safe_text(repository.get("nameWithOwner"))
        if isinstance(repository, Mapping)
        else None
    )


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
        retries=1,
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
    if value.get("errors"):
        warnings.append(
            {
                "command_id": command_id,
                "exit_code": 0,
                "error": safe_text(value.get("errors"), limit=1000),
            }
        )
        return None
    return value


def _normalize_project_items(value: Any) -> dict[str, Any]:
    """Normalize Project metadata without interpreting any field name."""
    if isinstance(value, Mapping):
        nodes = value.get("nodes")
        page = value.get("pageInfo")
    elif isinstance(value, list):
        nodes = value
        page = {"hasNextPage": False}
    else:
        return {"items": [], "count": 0, "truncated": True}
    if not isinstance(nodes, list) or not isinstance(page, Mapping):
        return {"items": [], "count": 0, "truncated": True}
    normalized: list[dict[str, Any]] = []
    for item in nodes:
        if not isinstance(item, Mapping):
            continue
        project = item.get("project")
        if not isinstance(project, Mapping):
            # Some GitHub/compatibility callers expose already-flattened
            # Project fields (for example ``{"status": {"name": "Ready"}}``)
            # rather than ProjectV2 item metadata. Preserve those normalized
            # fields for generic status lookup, but never treat them as a
            # canonical Project identity.
            normalized.append(
                {
                    "number": None,
                    "owner": None,
                    "fields": dict(item),
                    "fields_complete": True,
                }
            )
            continue
        owner = project.get("owner")
        owner_login = owner.get("login") if isinstance(owner, Mapping) else None
        fields_value = item.get("fieldValues")
        fields: dict[str, str] = {}
        fields_complete = False
        if isinstance(fields_value, Mapping):
            raw_fields = fields_value.get("nodes")
            field_page = fields_value.get("pageInfo")
            if isinstance(raw_fields, list):
                for field_value in raw_fields:
                    if not isinstance(field_value, Mapping):
                        continue
                    field = field_value.get("field")
                    field_name = (
                        field.get("name") if isinstance(field, Mapping) else None
                    )
                    field_value_name = field_value.get("name")
                    if isinstance(field_name, str) and isinstance(
                        field_value_name, str
                    ):
                        fields[safe_text(field_name)] = safe_text(field_value_name)
                fields_complete = (
                    isinstance(field_page, Mapping)
                    and field_page.get("hasNextPage") is False
                )
        normalized.append(
            {
                "number": project.get("number"),
                "owner": safe_text(owner_login),
                "fields": fields,
                "fields_complete": fields_complete,
            }
        )
    bounded = bounded_list(normalized)
    bounded["truncated"] = bounded["truncated"] or page.get("hasNextPage") is True
    return bounded


def _find_project_field(value: Any, field_name: str) -> str | None:
    """Find a normalized or raw Project field value by its mechanical name."""
    if isinstance(value, Mapping):
        fields = value.get("fields")
        if isinstance(fields, Mapping):
            candidate = fields.get(field_name)
            if isinstance(candidate, str) and candidate.strip():
                return safe_text(candidate)
        field = value.get("field")
        if isinstance(field, Mapping) and field.get("name") == field_name:
            for key in ("name", "value", "text"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return safe_text(candidate)
        for key, nested in value.items():
            if str(key).casefold() == field_name.casefold():
                if isinstance(nested, str) and nested.strip():
                    return safe_text(nested)
                if isinstance(nested, Mapping):
                    for candidate_key in ("name", "value", "text"):
                        candidate = nested.get(candidate_key)
                        if isinstance(candidate, str) and candidate.strip():
                            return safe_text(candidate)
            result = _find_project_field(nested, field_name)
            if result is not None:
                return result
    elif isinstance(value, list):
        for nested in value:
            result = _find_project_field(nested, field_name)
            if result is not None:
                return result
    return None


def _find_project_status(value: Any) -> str | None:
    return _find_project_field(value, "Status")


def canonical_project_field(
    value: Any,
    *,
    repository: str | None,
    field_name: str,
) -> str | None:
    """Return a field from the repository owner's canonical Project item.

    Project identity and field normalization are mechanical facts.  The caller
    chooses which normalized field has business meaning.
    """
    if not isinstance(value, Mapping) or repository is None:
        return None
    owner, separator, _name = repository.partition("/")
    if not separator or not owner:
        return None
    items = value.get("items")
    if not isinstance(items, list) or value.get("truncated") is True:
        return None
    canonical = [
        item
        for item in items
        if isinstance(item, Mapping)
        and item.get("number") == CANONICAL_PROJECT_NUMBER
        and isinstance(item.get("owner"), str)
        and item.get("owner", "").casefold() == owner.casefold()
        and item.get("fields_complete") is True
    ]
    if len(canonical) != 1:
        return None
    fields = canonical[0].get("fields")
    if not isinstance(fields, Mapping):
        return None
    result = fields.get(field_name)
    return safe_text(result) if isinstance(result, str) and result.strip() else None


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
    if not isinstance(value, Mapping):
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
    ref_nodes = (
        refs.get("nodes")
        if isinstance(refs, Mapping)
        else refs
        if isinstance(refs, list)
        else []
    )
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
        evidence_status, reason, status = (
            "partial",
            "closing-pr-references-truncated",
            "unknown",
        )
    elif timeline_truncated:
        evidence_status, reason, status = "partial", "timeline-truncated", "unknown"
    elif state != "CLOSED":
        reason, status = "issue-not-closed", "not-closed"
    elif latest_closure is None:
        evidence_status, reason, status = (
            "partial",
            "latest-effective-close-event-unavailable",
            "unknown",
        )
    elif latest_closure.get("closer_type") != "PullRequest":
        reason, status = "latest-closer-is-not-pull-request", "not-pr-closer"
    elif not isinstance(latest_closure.get("closer"), Mapping):
        evidence_status, reason, status = (
            "partial",
            "latest-closer-pr-metadata-unavailable",
            "unknown",
        )
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


def _issue_view_with_contract(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
    include_comments: bool = True,
    include_closure: bool = True,
    include_contract: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Read and normalize one Issue without profile interpretation."""
    fields = ["number", "title", "state", "labels", "projectItems", "url"]
    if include_contract:
        fields.append("body")
    if include_comments:
        fields.append("comments")
    if include_closure:
        fields.extend(("closedAt", "closedByPullRequestsReferences"))
    result = runner.run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            repository,
            "--json",
            ",".join(fields),
        ],
        command_id=f"gh-issue-view-{number}",
        retries=1,
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
                ",".join(
                    field
                    for field in fields
                    if field
                    not in {
                        "comments",
                        "projectItems",
                        "closedByPullRequestsReferences",
                    }
                ),
            ],
            command_id=f"gh-issue-view-fallback-{number}",
        )
        if fallback.returncode != 0:
            warnings.extend((command_warning(result), command_warning(fallback)))
            return None, None
        result = fallback
    value = read_json_text(result.stdout, field=f"Issue #{number}")
    if not isinstance(value, Mapping):
        warnings.append(
            {
                "command_id": result.command_id,
                "exit_code": 0,
                "error": "Issue response is not an object",
            }
        )
        return None, None
    labels = value.get("labels", [])
    normalized_labels: list[str] = []
    if isinstance(labels, list):
        for item in labels:
            if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                normalized_labels.append(item["name"])
            elif isinstance(item, str):
                normalized_labels.append(item)
    raw_comments = value.get("comments", [])
    comments: list[dict[str, Any]] = []
    if isinstance(raw_comments, list):
        for comment in raw_comments:
            if not isinstance(comment, Mapping):
                continue
            author = comment.get("author")
            comments.append(
                {
                    "author": author.get("login")
                    if isinstance(author, Mapping)
                    else None,
                    "created_at": comment.get("createdAt"),
                    "updated_at": comment.get("updatedAt"),
                    "body": comment.get("body")
                    if isinstance(comment.get("body"), str)
                    else None,
                }
            )
    body = value.get("body") if isinstance(value.get("body"), str) else None
    project_items = _normalize_project_items(value.get("projectItems"))
    pull_refs: list[dict[str, Any]] = []
    raw_pull_refs = value.get("closedByPullRequestsReferences", [])
    if isinstance(raw_pull_refs, list):
        pull_refs = [
            normalized
            for normalized in (_normalize_closing_pr(item) for item in raw_pull_refs)
            if normalized is not None
        ]
    issue: dict[str, Any] = {
        "number": value.get("number"),
        "title": safe_text(value.get("title")),
        "content_sha256": sha256_json(
            {"body": body, "comments": comments if include_comments else None}
        ),
        "body_sha256": sha256_json({"body": body}),
        "body_characters": len(body) if body is not None else None,
        "comment_count": len(comments) if include_comments else None,
        "state": safe_text(value.get("state")),
        "labels": bounded_list(sorted(normalized_labels)),
        "project_items": project_items,
        "project_status": _find_project_status(project_items),
        "url": safe_text(value.get("url")),
        "closed_at": safe_text(value.get("closedAt")),
        "closing_pull_requests": bounded_list(pull_refs),
    }
    if include_closure:
        issue["issue_closure"] = _issue_closure_snapshot(
            runner, repository, number, warnings
        )
    contract = (
        {
            "number": value.get("number"),
            "title": safe_text(value.get("title")),
            "url": safe_text(value.get("url")),
            "body": body,
            "body_sha256": issue["body_sha256"],
        }
        if include_contract and body is not None
        else None
    )
    return issue, contract


def _issue_view(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    issue, _contract = _issue_view_with_contract(runner, repository, number, warnings)
    return issue


def _normalize_relationship_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    labels: list[str] = []
    raw_labels = item.get("labels")
    labels_complete = False
    if isinstance(raw_labels, Mapping) and isinstance(raw_labels.get("nodes"), list):
        labels = [
            label["name"]
            for label in raw_labels["nodes"]
            if isinstance(label, Mapping) and isinstance(label.get("name"), str)
        ]
        page = raw_labels.get("pageInfo")
        labels_complete = isinstance(page, Mapping) and page.get("hasNextPage") is False
    return {
        "number": item.get("number"),
        "title": safe_text(item.get("title")),
        "state": safe_text(item.get("state")),
        "labels": sorted(labels),
        "labels_complete": labels_complete,
        "body": item.get("body") if isinstance(item.get("body"), str) else None,
        "project_items": _normalize_project_items(item.get("projectItems")),
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
    if isinstance(value, Mapping):
        data = value.get("data")
        repo = data.get("repository") if isinstance(data, Mapping) else None
        issue = repo.get("issue") if isinstance(repo, Mapping) else None
    if not isinstance(issue, Mapping):
        return {
            "available": False,
            "repository": repository,
            "issue_type": None,
            "parent": None,
            "sub_issues": bounded_list([]),
            "sub_issue_set_digest": None,
            "sub_issues_complete": False,
            "blocked_by": bounded_list([]),
            "blocking": bounded_list([]),
        }

    def nodes(field: str) -> list[dict[str, Any]]:
        connection = issue.get(field)
        raw_nodes = (
            connection.get("nodes", []) if isinstance(connection, Mapping) else []
        )
        return (
            [
                normalized
                for normalized in (
                    _normalize_relationship_item(item) for item in raw_nodes
                )
                if normalized is not None
            ]
            if isinstance(raw_nodes, list)
            else []
        )

    def bounded_connection(field: str) -> dict[str, Any]:
        connection = issue.get(field)
        normalized = bounded_list(nodes(field))
        page = connection.get("pageInfo") if isinstance(connection, Mapping) else None
        if isinstance(page, Mapping) and page.get("hasNextPage") is True:
            normalized["truncated"] = True
        return normalized

    parent = issue.get("parent")
    normalized_parent = (
        {
            "number": parent.get("number"),
            "title": safe_text(parent.get("title")),
            "state": safe_text(parent.get("state")),
        }
        if isinstance(parent, Mapping)
        else None
    )
    issue_type = issue.get("issueType")
    sub_issue_nodes = nodes("subIssues")
    sub_connection = issue.get("subIssues")
    sub_page = (
        sub_connection.get("pageInfo") if isinstance(sub_connection, Mapping) else None
    )
    sub_has_next = isinstance(sub_page, Mapping) and sub_page.get("hasNextPage") is True
    sub_bounded = bounded_list(sub_issue_nodes, item_limit=MAX_CHILDREN)
    return {
        "available": True,
        "repository": repository,
        "issue_type": safe_text(issue_type.get("name"))
        if isinstance(issue_type, Mapping)
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
        "blocked_by": bounded_connection("blockedBy"),
        "blocking": bounded_connection("blocking"),
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
        if not isinstance(item, Mapping):
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
    return {
        "count": count,
        "success": success,
        "pending": pending,
        "failed": failed,
        "skipped_or_unknown": skipped,
        "all_success": None if count == 0 else success == count,
        "items": bounded_list(items),
    }
