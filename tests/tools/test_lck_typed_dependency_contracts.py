# ruff: noqa: E402, I001

"""Regression coverage for complete typed dependency contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import eligibility, models, shared_facts  # type: ignore[import-not-found]  # noqa: E402
from lck_core.issue_profiles import resolve_leaf_issue_profile  # type: ignore[import-not-found]  # noqa: E402
from lck_core.profile_policies import validate_profile_contract  # type: ignore[import-not-found]  # noqa: E402
from workflow_common import CommandResult, bounded_list, sha256_json  # type: ignore[import-not-found]  # noqa: E402
from workflow_evidence import _relationship_snapshot as audit_relationship_snapshot  # type: ignore[import-not-found]  # noqa: E402


BUG_BODY = (
    """### Observed

The long Bug contract remains readable.

### Expected

LCK should evaluate the complete Bug contract.

### Reproduction / Evidence

"""
    + ("Evidence prefix. " * 45)
    + """

### Acceptance Criteria

- The complete Bug contract is accepted.
"""
)

DOCUMENTATION_BODY = (
    """### Documentation Goal

The long Documentation contract remains readable.

### Requirements

"""
    + ("Document the supported behavior. " * 45)
    + """

### Acceptance Criteria

- The complete Documentation contract is accepted.
"""
)

RESEARCH_BODY = (
    """### Question / Decision Needed

Should the supported path be adopted?

### Context

The long Research contract remains readable.

### Scope

- Repository-backed workflow behavior.

### Non-goals

- No unrelated runtime changes.

### Evidence / Evaluation Criteria

"""
    + ("Evaluate the repository-backed evidence. " * 45)
    + """

### Expected Outcome / Artifact

Adopt the repository-backed workflow contract.
"""
)

DEPENDENCY_PROJECT = {
    "nodes": [
        {
            "project": {"number": 1, "owner": {"login": "owner"}},
            "fieldValues": {
                "nodes": [
                    {
                        "name": "ARCHITECTURE DECISION",
                        "field": {"name": "Research Outcome"},
                    }
                ],
                "pageInfo": {"hasNextPage": False},
            },
        }
    ],
    "pageInfo": {"hasNextPage": False},
}


def _raw_dependency(
    *,
    number: int,
    label: str,
    body: str,
    project_items: Any = None,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"[{label}] long dependency",
        "state": "CLOSED",
        "body": body,
        "labels": {
            "nodes": [{"name": label}],
            "pageInfo": {"hasNextPage": False},
        },
        "projectItems": project_items
        or {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }


def _bounded_dependency(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = shared_facts._normalize_relationship_item(raw)
    assert normalized is not None
    value = bounded_list([normalized])
    return cast(dict[str, Any], value["items"][0])


def _relationship_payload(dependency: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "issue": {
                    "number": 300,
                    "title": "Task with typed dependency",
                    "state": "OPEN",
                    "blockedBy": {
                        "nodes": [dependency],
                        "pageInfo": {"hasNextPage": False},
                    },
                    "blocking": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False},
                    },
                    "subIssues": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False},
                    },
                    "issueType": {"name": "Task"},
                    "parent": None,
                }
            }
        }
    }


class _GraphQLRunner:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def run(self, argv: Any, *, command_id: str, **_: Any) -> CommandResult:
        return CommandResult(
            command_id,
            tuple(str(item) for item in argv),
            0,
            json.dumps(self.payload),
            "",
        )


@pytest.mark.parametrize(
    ("label", "body", "contract_key"),
    [
        ("type:bug", BUG_BODY, "bug_contract"),
        ("type:documentation", DOCUMENTATION_BODY, "documentation_contract"),
        ("type:research", RESEARCH_BODY, "research_contract"),
    ],
)
def test_lck_policy_uses_complete_dependency_contract(
    label: str,
    body: str,
    contract_key: str,
) -> None:
    dependency = _bounded_dependency(
        _raw_dependency(
            number=191,
            label=label,
            body=body,
            project_items=DEPENDENCY_PROJECT if label == "type:research" else None,
        )
    )
    assert len(dependency["body"]) < len(body)
    assert dependency["contract"]["body"] == body
    assert dependency["contract"]["body_characters"] == len(body)

    resolution = resolve_leaf_issue_profile({"labels": [label]})
    assert resolution.resolved and resolution.profile is not None
    check = validate_profile_contract(resolution.profile, dependency)
    assert check.valid, check.detail
    assert check.evidence is not None
    assert check.evidence.payload["contract"]["status"] == "pass"
    assert check.evidence.payload["contract"]["required_sections"]


def test_lck_eligibility_accepts_long_research_architecture_dependency() -> None:
    dependency = _bounded_dependency(
        _raw_dependency(
            number=191,
            label="type:research",
            body=RESEARCH_BODY,
            project_items=DEPENDENCY_PROJECT,
        )
    )
    downstream_body = (
        "### Decision Contract\n\nAdopt the repository-backed workflow contract.\n"
    )
    target = {
        "number": 239,
        "title": "[Task] downstream",
        "state": "OPEN",
        "labels": ["type:task", "codex:ready"],
    }
    state = models.LiveState(
        issue_number=239,
        repository="owner/repo",
        issue=target,
        leaf_contract={
            "number": 239,
            "body": downstream_body,
            "body_sha256": sha256_json({"body": downstream_body}),
        },
        relationships={
            "available": True,
            "blocked_by": {
                "items": [dependency],
                "count": 1,
                "truncated": False,
            },
        },
    )

    reasons = eligibility.PhaseEligibilityResolver().blocker_reasons(
        state, phase=models.Phase.REVIEW_PREPARE
    )
    assert reasons == ()


@pytest.mark.parametrize(
    ("label", "body", "contract_key"),
    [
        ("type:bug", BUG_BODY, "bug_contract"),
        ("type:documentation", DOCUMENTATION_BODY, "documentation_contract"),
        ("type:research", RESEARCH_BODY, "research_contract"),
    ],
)
def test_feature_audit_parses_complete_typed_dependency_contract(
    label: str,
    body: str,
    contract_key: str,
) -> None:
    raw = _raw_dependency(
        number=191,
        label=label,
        body=body,
        project_items=DEPENDENCY_PROJECT if label == "type:research" else None,
    )
    relationships = audit_relationship_snapshot(
        _GraphQLRunner(_relationship_payload(raw)), "owner/repo", 300, []
    )
    dependency = relationships["blocked_by"]["items"][0]
    assert len(dependency["body"]) < len(body)
    assert dependency[contract_key]["status"] == "pass"
    if label == "type:research":
        assert dependency["research_outcome"] == "ARCHITECTURE DECISION"
        assert dependency["research_outcome_is_canonical"] is True
        assert dependency["decision_contract"]["status"] == "pass"
    assert "contract" not in dependency


def test_relationship_contract_fails_closed_on_truncation_and_identity_mismatch() -> (
    None
):
    dependency = _bounded_dependency(
        _raw_dependency(number=201, label="type:bug", body=BUG_BODY)
    )
    assert shared_facts.relationship_contract(dependency) is not None

    invalid_body = dict(dependency["contract"])
    invalid_body["body"] = BUG_BODY[:512]
    dependency["contract"] = invalid_body
    assert shared_facts.relationship_contract(dependency) is None
    profile = resolve_leaf_issue_profile({"labels": ["type:bug"]}).profile
    assert profile is not None
    assert not validate_profile_contract(profile, dependency).valid

    dependency = _bounded_dependency(
        _raw_dependency(number=201, label="type:bug", body=BUG_BODY)
    )
    invalid_identity = dict(dependency["contract"])
    invalid_identity["number"] = 999
    dependency["contract"] = invalid_identity
    assert shared_facts.relationship_contract(dependency) is None
    assert not validate_profile_contract(profile, dependency).valid
