# ruff: noqa: E402, I001

"""Acceptance tests for the canonical LCK leaf Issue profile resolver."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core.models import Phase  # type: ignore[import-not-found]  # noqa: E402
from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    eligibility as lck_eligibility,
    issue_profiles as lck_profiles,
)
from lck_test_support import (  # noqa: E402
    FakeRunner,
    _install_facts,
    _issue,
    _relationships,
    _resolver,
)


def _issue_with_labels(*labels: str, title: str = "ordinary title") -> dict[str, Any]:
    return {
        "labels": list(labels),
        "title": title,
        "body": "body text that must not participate in type resolution",
    }


def test_leaf_issue_profile_resolution_is_exact_and_fail_closed() -> None:
    profiles = {
        "type:task": ("task", True, "task/", True),
        "type:bug": ("bug", False, "bug/", False),
        "type:documentation": ("documentation", False, "documentation/", True),
        "type:research": ("research", False, "research/", True),
    }

    for label, expected in profiles.items():
        resolution = lck_profiles.resolve_leaf_issue_profile(_issue_with_labels(label))
        assert resolution.resolved
        assert resolution.profile is not None
        profile = resolution.profile
        assert (
            profile.issue_kind.value,
            profile.requires_critical_outcome,
            profile.branch_namespace,
            profile.lifecycle_enabled,
        ) == expected
        assert profile.canonical_type_label == label
        assert profile.eligibility_policy == expected[0]
        assert profile.validation_policy == expected[0]
        assert profile.completion_policy == expected[0]

    invalid_cases = (
        (_issue_with_labels(), "MISSING_TYPE"),
        (_issue_with_labels("type:task", "type:bug"), "MULTIPLE_TYPES"),
        (_issue_with_labels("type:task", "type:task"), "MULTIPLE_TYPES"),
        (_issue_with_labels("type:unknown"), "UNKNOWN_TYPE"),
        (_issue_with_labels("type:feature"), "NON_LEAF_TYPE"),
        (_issue_with_labels("type:epic"), "NON_LEAF_TYPE"),
    )
    for issue, expected_status in invalid_cases:
        resolution = lck_profiles.resolve_leaf_issue_profile(issue)
        assert not resolution.resolved
        assert resolution.terminal_status == expected_status
        assert resolution.profile is None

    # The resolver has one input authority: changing title/body or supplying a
    # GitHub Issue Type-shaped field cannot reclassify the canonical label.
    resolution = lck_profiles.resolve_leaf_issue_profile(
        {
            "labels": [{"name": "type:task"}],
            "title": "[Bug] type:bug",
            "body": "### Objective\nThis is not a type carrier.",
            "issue_type": "Bug",
        }
    )
    assert resolution.profile is lck_profiles.TASK_PROFILE


@pytest.mark.parametrize("label", ["type:bug"])
def test_unenabled_leaf_profiles_return_typed_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
) -> None:
    fake = FakeRunner(branch="main")
    issue = _issue()
    issue["labels"] = {"items": [label, "codex:ready"]}
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        relationships=_relationships(issue_type="Task"),
    )

    state = _resolver(fake).resolve(159)
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state, Phase.DELIVERY_PREPARE
    )

    assert not decision.eligible
    assert any(
        f"PROFILE_NOT_ENABLED: workflow profile {label}" in reason
        for reason in decision.reasons
    )
    assert not any(
        "target Issue is not a type:task" in reason for reason in decision.reasons
    )
    assert decision.issue_profile is not None
    assert decision.issue_profile["profile"]["canonical_type_label"] == label


def test_task_profile_uses_labels_without_issue_type_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRunner(branch="main")
    issue = _issue()
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        relationships=_relationships(issue_type="Bug"),
    )

    state = _resolver(fake).resolve(159)
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state, Phase.DELIVERY_PREPARE
    )

    assert decision.eligible
    assert state.issue_profile is not None
    assert state.issue_profile["profile"]["issue_kind"] == "task"
    assert state.target_branch == "task/159-lck-core-live-state-resolution"
