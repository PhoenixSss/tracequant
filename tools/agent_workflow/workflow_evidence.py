#!/usr/bin/env python3
"""Feature-audit evidence over the shared LCK fact-acquisition adapter.

Task lifecycle control belongs exclusively to ``lck.py``. This module retains
only audit-oriented Feature snapshot/recheck behavior; authoritative
profile-neutral Git/GitHub facts are owned by ``lck_core.shared_facts`` and
consumed here through adapters. It does not expose Delivery, Review,
Remediation, Merge, or Closeout control commands.
"""

from __future__ import annotations

import argparse
import copy
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from bug_policy import BUG_TEMPLATE_PATH, bug_contract_snapshot
from critical_outcome import critical_outcome_snapshot
from documentation_policy import (
    DOCUMENTATION_TEMPLATE_PATH,
    documentation_contract_snapshot,
)
from lck_core import shared_facts
from lck_core.eligibility import evaluate_shared_blockers
from lck_core.issue_profiles import resolve_leaf_issue_profile
from lck_core.profile_policies import (
    DEFAULT_PROFILE_POLICY_REGISTRY,
    PolicyContext,
    evaluate_profile_blockers,
    validate_profile_contract,
)
from research_policy import (
    RESEARCH_OUTCOME_FIELD,
    RESEARCH_TEMPLATE_PATH,
    decision_contract_snapshot,
    research_contract_snapshot,
)
from workflow_common import (
    CommandRunner,
    WorkflowToolError,
    atomic_write_json,
    bounded_list,
    command_warning,
    is_sha,
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


def _audit_issue_view_with_contract(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
    include_comments: bool = True,
    include_closure: bool = True,
    include_contract: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Adapt shared Issue facts with profile semantics for Feature audit."""
    issue, contract = shared_facts._issue_view_with_contract(
        runner,
        repository,
        number,
        warnings,
        include_comments,
        include_closure,
        include_contract,
    )
    if issue is None:
        return None, contract
    result = copy.deepcopy(issue)
    body = contract.get("body") if isinstance(contract, Mapping) else None
    labels = result.get("labels")
    if isinstance(labels, Mapping):
        labels = labels.get("items")
    labels = labels if isinstance(labels, list) else []
    resolution = resolve_leaf_issue_profile({"labels": labels})
    profile = resolution.profile if resolution.resolved else None
    root = getattr(runner, "repo_root", None)
    bug_template = root / BUG_TEMPLATE_PATH if isinstance(root, Path) else None
    documentation_template = (
        root / DOCUMENTATION_TEMPLATE_PATH if isinstance(root, Path) else None
    )
    research_template = (
        root / RESEARCH_TEMPLATE_PATH if isinstance(root, Path) else None
    )
    result["critical_outcome"] = (
        critical_outcome_snapshot(body)
        if profile is not None and profile.requires_critical_outcome
        else None
    )
    result["bug_contract"] = (
        bug_contract_snapshot(body, template_path=bug_template)
        if profile is not None and profile.contract_policy == "bug"
        else None
    )
    result["documentation_contract"] = (
        documentation_contract_snapshot(body, template_path=documentation_template)
        if profile is not None and profile.contract_policy == "documentation"
        else None
    )
    is_research = profile is not None and profile.supports_research_outcome
    result["research_contract"] = (
        research_contract_snapshot(body, template_path=research_template)
        if is_research
        else None
    )
    result["research_outcome"] = (
        shared_facts.canonical_project_field(
            result.get("project_items"),
            repository=repository,
            field_name=RESEARCH_OUTCOME_FIELD,
        )
        if is_research
        else None
    )
    if isinstance(contract, Mapping):
        enriched_contract = dict(contract)
        for field in (
            "critical_outcome",
            "bug_contract",
            "documentation_contract",
            "research_contract",
            "research_outcome",
        ):
            enriched_contract[field] = result[field]
        contract = enriched_contract
    return result, contract


def _audit_relationship_snapshot(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Adapt shared relationship facts with audit-only profile projections."""
    result = copy.deepcopy(
        shared_facts._relationship_snapshot(runner, repository, number, warnings)
    )
    for connection_name in ("sub_issues", "blocked_by", "blocking"):
        connection = result.get(connection_name)
        items = connection.get("items") if isinstance(connection, Mapping) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            labels = item.get("labels") if isinstance(item.get("labels"), list) else []
            resolution = resolve_leaf_issue_profile({"labels": labels})
            profile = resolution.profile if resolution.resolved else None
            complete_contract = shared_facts.relationship_contract(item)
            if "contract" in item:
                body = (
                    complete_contract.get("body")
                    if isinstance(complete_contract, Mapping)
                    else None
                )
            else:
                # Compatibility for already-normalized historical callers.
                body = item.get("body") if isinstance(item.get("body"), str) else None
            is_bug = profile is not None and profile.contract_policy == "bug"
            is_documentation = (
                profile is not None and profile.contract_policy == "documentation"
            )
            is_research = profile is not None and profile.supports_research_outcome
            item["research_contract"] = (
                research_contract_snapshot(body) if is_research else None
            )
            item["bug_contract"] = bug_contract_snapshot(body) if is_bug else None
            item["documentation_contract"] = (
                documentation_contract_snapshot(body) if is_documentation else None
            )
            item["decision_contract"] = (
                decision_contract_snapshot(body, research=True) if is_research else None
            )
            item["research_outcome"] = (
                shared_facts.canonical_project_field(
                    item.get("project_items"),
                    repository=repository,
                    field_name=RESEARCH_OUTCOME_FIELD,
                )
                if is_research
                else None
            )
            item["research_outcome_is_canonical"] = (
                item["research_outcome"] is not None if is_research else None
            )
            # The audit projection retains bounded metadata and typed contract
            # snapshots, not the raw full Issue body used to derive them.
            item.pop("contract", None)
    return result


# The public names below are retained as audit adapters for existing Feature
# snapshot consumers.  LCK imports the owner module directly and therefore
# cannot accidentally acquire lifecycle facts through this audit module.
_normalize_title = shared_facts.normalize_title
_gate = shared_facts.gate
_git_value = shared_facts._git_value
_git_snapshot = shared_facts._git_snapshot
_repository_slug = shared_facts._repository_slug
_issue_view_with_contract = _audit_issue_view_with_contract


def _issue_view(
    runner: CommandRunner,
    repository: str,
    number: int,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return _audit_issue_view_with_contract(runner, repository, number, warnings)[0]


_find_project_status = shared_facts._find_project_status
_find_project_field = shared_facts._find_project_field
_relationship_snapshot = _audit_relationship_snapshot
_normalize_checks = shared_facts._normalize_checks


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


def _audit_formal_blockers_gate(
    relationships: Mapping[str, Any],
    *,
    downstream_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit adapter for the shared gate and registered profile capabilities."""
    shared_gate = evaluate_shared_blockers(relationships)
    if shared_gate.get("status") == "fail":
        return shared_gate
    if shared_gate.get("status") != "pass":
        return shared_gate
    blocked_by = relationships.get("blocked_by")
    items = blocked_by.get("items") if isinstance(blocked_by, Mapping) else None
    if not isinstance(items, list):
        return shared_facts.gate("unknown", "Blocked-by metadata unavailable")
    profile_blockers: list[tuple[str, str]] = []
    resolved = 0
    for item in items:
        if (
            not isinstance(item, Mapping)
            or str(item.get("state", "")).upper() != "CLOSED"
        ):
            continue
        labels = item.get("labels")
        if not isinstance(labels, list):
            return shared_facts.gate("unknown", "closed dependency labels unavailable")
        if item.get("labels_complete") is False:
            return shared_facts.gate(
                "unknown", "closed dependency labels are incomplete"
            )
        resolution = resolve_leaf_issue_profile({"labels": labels})
        profile = resolution.profile if resolution.resolved else None
        if profile is None:
            return shared_facts.gate(
                "unknown",
                "unknown_state=1, closed dependency profile is unavailable",
            )
        if profile.contract_policy is None:
            resolved += 1
            continue
        try:
            contract_check = validate_profile_contract(
                profile, item, registry=DEFAULT_PROFILE_POLICY_REGISTRY
            )
            if not contract_check.valid:
                profile_blockers.append(("unknown", contract_check.failure_reason))
                continue
            blockers = evaluate_profile_blockers(
                profile,
                item,
                contract_evidence=contract_check.evidence,
                registry=DEFAULT_PROFILE_POLICY_REGISTRY,
                context=PolicyContext(
                    profile=profile,
                    phase="audit",
                    issue=item,
                    repository=relationships.get("repository")
                    if isinstance(relationships.get("repository"), str)
                    else None,
                    downstream_contract=downstream_contract,
                ),
            )
        except (TypeError, ValueError) as exc:
            profile_blockers.append(("unknown", str(exc)))
            continue
        if blockers:
            profile_blockers.extend(
                (
                    "unknown"
                    if blocker.code
                    in {
                        "CONTRACT_INVALID",
                        "RESEARCH_OUTCOME_UNKNOWN",
                        "ARCHITECTURE_DECISION_UNMATCHED",
                    }
                    else "fail",
                    blocker.detail,
                )
                for blocker in blockers
            )
        else:
            resolved += 1
    if any(status == "fail" for status, _detail in profile_blockers):
        details = "; ".join(
            detail for status, detail in profile_blockers if status == "fail"
        )
        return shared_facts.gate("fail", details or "profile blocker rejected")
    if profile_blockers:
        details = "; ".join(detail for _status, detail in profile_blockers)
        return shared_facts.gate(
            "unknown", details or "profile blocker evidence unavailable"
        )
    return shared_facts.gate(
        "pass", f"unresolved=0, resolved={resolved}, total={len(items)}"
    )


# Keep the historical audit helper available while routing all current LCK
# decisions through ``PhaseEligibilityResolver.blocker_reasons``.
_formal_blockers_gate = _audit_formal_blockers_gate


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
