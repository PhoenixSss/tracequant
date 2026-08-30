#!/usr/bin/env python3
"""Feature-audit evidence plus shared read-only GitHub fact helpers for LCK.

Task lifecycle control belongs exclusively to ``lck.py``. This module retains
only audit-oriented Feature snapshot/recheck behavior and deterministic query
helpers reused by LCK; it does not expose Delivery, Review, Remediation, Merge,
or Closeout control commands.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from bug_policy import BUG_TEMPLATE_PATH, bug_contract_snapshot
from critical_outcome import critical_outcome_snapshot
from documentation_policy import (
    DOCUMENTATION_TEMPLATE_PATH,
    documentation_contract_snapshot,
)
from lck_core.issue_profiles import resolve_leaf_issue_profile
from lck_core.profile_policies import validate_profile_contract
from research_policy import (
    RESEARCH_OUTCOME_FIELD,
    RESEARCH_TEMPLATE_PATH,
    ResearchPolicyError,
    architecture_decision_is_consistent,
    decision_contract_snapshot,
    is_implementation_outcome,
    parse_research_outcome,
    research_contract_snapshot,
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
EVIDENCE_ROOT: Final = ".agents/evidence.local"
SNAPSHOT_SUBDIR: Final = "snapshots"
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


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _gate(status: str, detail: str | None = None) -> dict[str, Any]:
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
    value = _git_value(
        runner,
        args,
        command_id=command_id,
        warnings=warnings,
    )
    if value is None:
        return []
    return [line for line in value.splitlines() if line]


def _remote_main_sha(
    runner: CommandRunner, warnings: list[dict[str, Any]]
) -> str | None:
    """Read the authoritative remote main ref without changing local refs."""
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
    if isinstance(value, dict) and isinstance(value.get("nameWithOwner"), str):
        return str(value["nameWithOwner"])
    return None


def _git_snapshot(
    runner: CommandRunner,
    warnings: list[dict[str, Any]],
    *,
    read_only_local_refs: bool = False,
    include_workspace_inventory: bool = True,
) -> dict[str, Any]:
    """Collect bounded Git facts for Feature audit and LCK queries.

    Feature audit may refresh refs for its local object inventory. LCK supplies
    the read-only mode when it reuses this helper; its authoritative main
    identity always comes from ``git ls-remote`` rather than a tracking ref.
    No environment variable or persisted evidence record controls Task
    lifecycle behavior.
    """

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
        "origin_refresh": (
            "skipped-read-only" if read_only_local_refs else "attempted"
        ),
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
        "staged_files": (
            bounded_list(staged, item_limit=MAX_FILES)
            if include_workspace_inventory
            else None
        ),
        "changed_files": (
            bounded_list(changed, item_limit=MAX_FILES)
            if include_workspace_inventory
            else None
        ),
        "worktree_count": (
            sum(1 for line in worktrees if line.startswith("worktree "))
            if include_workspace_inventory
            else None
        ),
        "worktree_branches": (
            bounded_list(worktree_branches, item_limit=MAX_FILES)
            if include_workspace_inventory
            else None
        ),
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
    """Read one Issue once and return compact facts plus the Task contract.

    The compact Issue snapshot intentionally omits the full body so audit/state
    payloads stay bounded.  LCK, however, needs the body as semantic input.  A
    single GitHub read therefore produces both representations so callers do
    not re-query the same Task later in an operation.
    """
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
            warnings.append(command_warning(result))
            warnings.append(command_warning(fallback))
            return None, None
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
        return None, None
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
    runner_root = getattr(runner, "repo_root", None)
    template_path = (
        runner_root / DOCUMENTATION_TEMPLATE_PATH
        if isinstance(runner_root, Path)
        else None
    )
    bug_template_path = (
        runner_root / BUG_TEMPLATE_PATH if isinstance(runner_root, Path) else None
    )
    research_template_path = (
        runner_root / RESEARCH_TEMPLATE_PATH if isinstance(runner_root, Path) else None
    )
    profile_resolution = resolve_leaf_issue_profile({"labels": normalized_labels})
    profile = profile_resolution.profile if profile_resolution.resolved else None
    is_task = profile is not None and profile.requires_critical_outcome
    is_bug = profile is not None and profile.contract_policy == "bug"
    is_documentation = (
        profile is not None and profile.contract_policy == "documentation"
    )
    is_research = profile is not None and profile.supports_research_outcome
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
    content_facts = {
        "body": body,
        "comments": comment_facts if include_comments else None,
    }
    issue = {
        "number": value.get("number"),
        "title": safe_text(value.get("title")),
        "content_sha256": sha256_json(content_facts),
        "body_sha256": sha256_json({"body": body}),
        "body_characters": len(body) if body is not None else None,
        "critical_outcome": critical_outcome_snapshot(body) if is_task else None,
        "bug_contract": (
            bug_contract_snapshot(body, template_path=bug_template_path)
            if is_bug
            else None
        ),
        "documentation_contract": (
            documentation_contract_snapshot(body, template_path=template_path)
            if is_documentation
            else None
        ),
        "research_contract": (
            research_contract_snapshot(body, template_path=research_template_path)
            if is_research
            else None
        ),
        "comment_count": len(comment_facts) if include_comments else None,
        "state": safe_text(value.get("state")),
        "labels": bounded_list(sorted(normalized_labels)),
        "project_status": _find_project_status(value.get("projectItems")),
        "research_outcome": (
            _find_project_field(value.get("projectItems"), RESEARCH_OUTCOME_FIELD)
            if is_research
            else None
        ),
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
            "critical_outcome": issue["critical_outcome"],
            "bug_contract": issue["bug_contract"],
            "documentation_contract": issue["documentation_contract"],
            "research_contract": issue["research_contract"],
            "research_outcome": issue["research_outcome"],
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
    issue, _contract = _issue_view_with_contract(
        runner,
        repository,
        number,
        warnings,
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


def _find_project_field(value: Any, field_name: str) -> str | None:
    """Find a named Project single-select value in bounded GitHub JSON."""

    if isinstance(value, Mapping):
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


def _canonical_research_outcome(
    project_items: Any,
    *,
    repository: str,
) -> str | None:
    """Read Research Outcome only from the repository owner's canonical Project."""

    if not isinstance(project_items, Mapping):
        return None
    nodes = project_items.get("nodes")
    page_info = project_items.get("pageInfo")
    if not isinstance(nodes, list) or not isinstance(page_info, Mapping):
        return None
    if page_info.get("hasNextPage") is not False:
        return None
    owner, separator, _name = repository.partition("/")
    if not separator or not owner:
        return None

    canonical_items: list[Mapping[str, Any]] = []
    for item in nodes:
        if not isinstance(item, Mapping):
            continue
        project = item.get("project")
        if not isinstance(project, Mapping):
            continue
        project_number = project.get("number")
        project_owner = project.get("owner")
        project_owner_login = (
            project_owner.get("login") if isinstance(project_owner, Mapping) else None
        )
        if (
            isinstance(project_number, int)
            and not isinstance(project_number, bool)
            and project_number == CANONICAL_PROJECT_NUMBER
            and isinstance(project_owner_login, str)
            and project_owner_login.casefold() == owner.casefold()
        ):
            canonical_items.append(item)

    # A missing or duplicate canonical item is not evidence for a blocker gate.
    if len(canonical_items) != 1:
        return None
    field_values = canonical_items[0].get("fieldValues")
    if not isinstance(field_values, Mapping):
        return None
    field_page_info = field_values.get("pageInfo")
    if (
        not isinstance(field_page_info, Mapping)
        or field_page_info.get("hasNextPage") is not False
    ):
        return None
    return _find_project_field(field_values, RESEARCH_OUTCOME_FIELD)


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
            labels_complete = False
            if isinstance(raw_labels, dict) and isinstance(
                raw_labels.get("nodes"), list
            ):
                labels = [
                    label["name"]
                    for label in raw_labels["nodes"]
                    if isinstance(label, dict) and isinstance(label.get("name"), str)
                ]
                page = raw_labels.get("pageInfo")
                labels_complete = (
                    isinstance(page, dict) and page.get("hasNextPage") is False
                )
            body = item.get("body") if isinstance(item.get("body"), str) else None
            profile_resolution = resolve_leaf_issue_profile({"labels": labels})
            profile = (
                profile_resolution.profile if profile_resolution.resolved else None
            )
            is_bug = profile is not None and profile.contract_policy == "bug"
            is_research = profile is not None and profile.supports_research_outcome
            research_outcome = (
                _canonical_research_outcome(
                    item.get("projectItems"),
                    repository=repository,
                )
                if is_research
                else None
            )
            normalized.append(
                {
                    "number": item.get("number"),
                    "title": safe_text(item.get("title")),
                    "state": safe_text(item.get("state")),
                    "labels": sorted(labels),
                    "labels_complete": labels_complete,
                    "research_contract": (
                        research_contract_snapshot(body) if is_research else None
                    ),
                    "bug_contract": (bug_contract_snapshot(body) if is_bug else None),
                    "decision_contract": (
                        decision_contract_snapshot(body, research=True)
                        if is_research
                        else None
                    ),
                    "research_outcome": safe_text(research_outcome) or None,
                    "research_outcome_is_canonical": (
                        research_outcome is not None if is_research else None
                    ),
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


def _formal_blockers_gate(
    relationships: Mapping[str, Any],
    *,
    downstream_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
    research_not_implementation = 0
    research_contract_unknown = 0
    architecture_contract_unknown = 0
    research_outcome_unknown = 0
    bug_contract_unknown = 0
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
            labels = item.get("labels")
            labels_complete = item.get("labels_complete")
            if labels_complete is None:
                # Unit callers may provide the already-normalized legacy
                # shape. Live GraphQL normalization always supplies this
                # explicit completeness bit.
                labels_complete = isinstance(labels, list)
            if labels_complete is not True or not isinstance(labels, list):
                unknown_state += 1
                continue
            profile_resolution = resolve_leaf_issue_profile({"labels": labels})
            if not profile_resolution.resolved or profile_resolution.profile is None:
                unknown_state += 1
                continue
            profile = profile_resolution.profile
            contract_check = validate_profile_contract(profile, item)
            if contract_check is not None and not contract_check.valid:
                if contract_check.policy == "bug":
                    bug_contract_unknown += 1
                elif contract_check.policy == "research":
                    research_contract_unknown += 1
                else:
                    unknown_state += 1
                continue
            if not profile.supports_research_outcome:
                resolved += 1
                continue
            outcome_is_canonical = item.get("research_outcome_is_canonical")
            if outcome_is_canonical is None:
                # Preserve compatibility for already-normalized unit callers;
                # live relationship normalization always supplies this bit.
                outcome_is_canonical = "projectItems" not in item
            if outcome_is_canonical is not True:
                research_outcome_unknown += 1
                continue
            raw_outcome = item.get("research_outcome")
            try:
                outcome = parse_research_outcome(raw_outcome)
            except ResearchPolicyError:
                unknown_state += 1
                continue
            if outcome.value == "ARCHITECTURE DECISION":
                if not architecture_decision_is_consistent(
                    item.get("decision_contract"), downstream_contract
                ):
                    architecture_contract_unknown += 1
                else:
                    resolved += 1
            elif is_implementation_outcome(outcome):
                resolved += 1
            else:
                research_not_implementation += 1
        else:
            unknown_state += 1

    if unresolved or research_not_implementation:
        return _gate(
            "fail",
            "unresolved="
            f"{unresolved + research_not_implementation}, resolved={resolved}, "
            f"open={open_numbers[:10]}, research_not_implementation={research_not_implementation}",
        )
    if (
        research_contract_unknown
        or architecture_contract_unknown
        or research_outcome_unknown
        or bug_contract_unknown
    ):
        return _gate(
            "unknown",
            "research decision evidence unavailable: "
            f"research_contract_unknown={research_contract_unknown}, "
            f"architecture_contract_unknown={architecture_contract_unknown}, "
            f"research_outcome_unknown={research_outcome_unknown}, "
            f"bug_contract_unknown={bug_contract_unknown}, "
            f"resolved={resolved}, total={count}",
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
            git.get("remote_main_sha"), args.expected_main_sha, "remote main SHA"
        )
        gates["tracking_main"] = _gate(
            "unknown" if git.get("tracking_main_sha") is None else "pass",
            "local remote-tracking ref is unavailable"
            if git.get("tracking_main_sha") is None
            else None,
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
        "object_base_sha": git.get("remote_main_sha"),
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


def _feature_stability_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return only Feature-audit facts whose change invalidates the audit."""

    observed = snapshot.get("observed")
    if not isinstance(observed, Mapping):
        return {}
    feature = observed.get("feature")
    feature_facts: Mapping[str, Any] = feature if isinstance(feature, Mapping) else {}
    git = observed.get("git")
    git_facts: Mapping[str, Any] = git if isinstance(git, Mapping) else {}
    return {
        "repository": snapshot.get("repository"),
        "subject": snapshot.get("subject"),
        "feature_number": feature_facts.get("number"),
        "feature_title": feature_facts.get("title"),
        "feature_state": feature_facts.get("state"),
        "feature_content_sha256": feature_facts.get("content_sha256"),
        "feature_metadata_sha256": sha256_json(
            {
                "labels": feature_facts.get("labels"),
                "project_status": feature_facts.get("project_status"),
                "relationships": observed.get("relationships"),
            }
        ),
        "remote_main_sha": git_facts.get("remote_main_sha"),
        "tracking_main_sha": git_facts.get("tracking_main_sha"),
        "direct_child_set_digest": observed.get("direct_child_set_digest"),
        "direct_child_evidence_digest": observed.get("direct_child_evidence_digest"),
        "relationships_digest": observed.get("relationships_digest"),
    }


def _feature_recheck(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    path = _snapshot_path(repo_root, args.snapshot_id)
    previous = read_json_file(path)
    if not isinstance(previous, dict):
        raise WorkflowToolError("snapshot is not an object")
    if previous.get("operation") != "feature-audit-snapshot":
        raise WorkflowToolError("only Feature audit snapshots support evidence recheck")

    subject = previous.get("subject")
    if not isinstance(subject, Mapping):
        raise WorkflowToolError("snapshot subject is invalid")
    feature_number = subject.get("feature_number")
    if not isinstance(feature_number, int) or isinstance(feature_number, bool):
        raise WorkflowToolError("snapshot Feature identity is invalid")

    namespace = argparse.Namespace(
        repository=previous.get("repository")
        if isinstance(previous.get("repository"), str)
        else None,
        no_store=False,
        expected_title=None,
        expected_main_sha=None,
        feature=feature_number,
    )
    current = _feature_snapshot(namespace, repo_root)
    current["operation"] = "feature-audit-recheck"

    before = _feature_stability_projection(previous)
    after = _feature_stability_projection(current)
    changed = sorted(
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    )
    current["stability"] = {
        "stable": not changed,
        "changed_fields": bounded_list(changed),
        "previous_snapshot_id": args.snapshot_id,
        "previous_fingerprint": sha256_json(before),
        "current_fingerprint": sha256_json(after),
    }
    current["gates"] = dict(current.get("gates", {}))
    current["gates"]["feature_audit_stability"] = _gate(
        "pass" if not changed else "fail", ", ".join(changed)
    )
    current["snapshot_id"] = (
        "ev-"
        + sha256_json(
            {key: value for key, value in current.items() if key != "snapshot_id"}
        )[:16]
    )
    return current


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=".", help="repository root")
    parser.add_argument("--repository", help="GitHub owner/repository override")
    parser.add_argument(
        "--no-store", action="store_true", help="do not write ignored snapshot file"
    )
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate compact Feature-audit evidence. "
            "Task lifecycle commands are owned exclusively by LCK."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    feature = sub.add_parser(
        "feature-audit-snapshot", help="Feature inventory and locked-main snapshot"
    )
    _common(feature)
    feature.add_argument("--feature", type=int, required=True)
    feature.add_argument("--expected-title")
    feature.add_argument("--expected-main-sha")

    recheck = sub.add_parser(
        "feature-audit-recheck", help="recollect and compare a Feature audit snapshot"
    )
    _common(recheck)
    recheck.add_argument("--snapshot-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    try:
        if args.command == "feature-audit-snapshot":
            snapshot = _feature_snapshot(args, repo_root)
        else:
            snapshot = _feature_recheck(args, repo_root)
        output = _snapshot_output(repo_root, snapshot, no_store=args.no_store)
        print_json(output, pretty=args.pretty)
        return 0
    except WorkflowToolError as exc:
        print(f"workflow evidence error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
