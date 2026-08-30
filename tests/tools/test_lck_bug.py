# ruff: noqa: E402, I001

"""Acceptance tests for the implementation-bearing Bug LCK profile."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from bug_policy import (  # type: ignore[import-not-found]  # noqa: E402
    BugPolicyStatus,
    bug_contract_snapshot,
    bug_template_contract,
    is_valid_bug_contract,
)
from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    closeout as lck_closeout,
    eligibility as lck_eligibility,
    issue_profiles as lck_profiles,
    models as lck_models,
    review_workspace as lck_review_workspace,
)
from lck_test_support import (  # noqa: E402
    FakeRunner,
    StaticResolver,
    _install_facts,
    _issue,
    _relationships,
    _resolver,
    _review_identity_value,
    _review_state,
)
from workflow_common import CommandResult  # type: ignore[import-not-found]  # noqa: E402
from workflow_evidence import (  # type: ignore[import-not-found]  # noqa: E402
    _issue_view_with_contract,
)


BUG_BODY = """### Observed

The Bug profile is rejected before an implementation workspace can be prepared.

### Expected

An implementation-bearing Bug uses the shared lifecycle without a wrapper Task.

### Reproduction / Evidence

The profile resolver reports the canonical type:bug profile as disabled.

### Acceptance Criteria

- A valid Bug reaches shared Delivery admission without a fabricated Critical Outcome.
- A regression test or equivalent evidence is reviewed against the candidate head.
"""


def _bug_issue() -> dict[str, Any]:
    issue = _issue()
    issue.update(
        {
            "number": 159,
            "title": "[Bug] enable implementation-bearing Bug workflow",
            "body": BUG_BODY,
            "body_sha256": "d" * 64,
            "labels": {"items": ["type:bug", "codex:ready"]},
            "critical_outcome": None,
            "bug_contract": bug_contract_snapshot(BUG_BODY),
        }
    )
    return issue


def test_live_issue_view_carries_the_bug_form_contract() -> None:
    class Runner:
        repo_root = ROOT

        def run(self, argv: Any, *, command_id: str, **_: Any) -> CommandResult:
            return CommandResult(
                command_id,
                tuple(str(item) for item in argv),
                0,
                json.dumps(
                    {
                        "number": 159,
                        "title": "[Bug] enable implementation-bearing Bug workflow",
                        "state": "OPEN",
                        "labels": [{"name": "type:bug"}],
                        "body": BUG_BODY,
                        "projectItems": [],
                    }
                ),
                "",
            )

    warnings: list[dict[str, Any]] = []
    issue, contract = _issue_view_with_contract(
        Runner(),
        "owner/repo",
        159,
        warnings,
        include_comments=False,
        include_closure=False,
    )

    assert warnings == []
    assert issue is not None
    assert contract is not None
    assert issue["bug_contract"]["status"] == BugPolicyStatus.PASS.value
    assert contract["bug_contract"] == issue["bug_contract"]
    assert "critical_outcome" not in contract or contract["critical_outcome"] is None


def test_bug_profile_uses_defect_contract_without_task_critical_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supported Bug entrypoint is shared LCK admission, not Task wrapping."""

    issue = _bug_issue()
    fake = FakeRunner(branch="main")
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        relationships=_relationships(issue_type="Bug"),
    )

    resolution = lck_profiles.resolve_leaf_issue_profile(issue)
    assert resolution.resolved
    assert resolution.profile is lck_profiles.BUG_PROFILE
    assert resolution.profile.lifecycle_enabled
    assert not resolution.profile.requires_critical_outcome
    assert resolution.profile.branch_namespace == "bug/"

    state = _resolver(fake).resolve(159)
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state, lck_models.Phase.DELIVERY_PREPARE
    )

    assert decision.eligible
    assert decision.capabilities == ("prepare_task_workspace",)
    assert state.target_branch == "bug/159-enable-implementation-bearing-bug-workflow"
    assert state.issue is not None
    assert is_valid_bug_contract(cast(Any, state.issue["bug_contract"]))
    assert state.issue["critical_outcome"] is None


def test_bug_contract_is_bound_to_form_and_fails_closed_on_ambiguous_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert bug_template_contract().field_ids == (
        "observed",
        "expected",
        "reproduction_evidence",
        "acceptance_criteria",
    )

    ambiguous = BUG_BODY.replace(
        "### Reproduction / Evidence",
        "### Expected\n\nA second conflicting expected behavior.\n\n"
        "### Reproduction / Evidence",
    )
    contract = bug_contract_snapshot(ambiguous)
    assert contract["status"] == BugPolicyStatus.RECLASSIFICATION_REQUIRED.value
    assert contract["duplicate_sections"] == ["Expected"]
    assert not is_valid_bug_contract(contract)

    issue = _bug_issue()
    issue["body"] = ambiguous
    issue["bug_contract"] = contract
    fake = FakeRunner(branch="main")
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        relationships=_relationships(issue_type="Bug"),
    )
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        _resolver(fake).resolve(159), lck_models.Phase.DELIVERY_PREPARE
    )
    assert not decision.eligible
    assert any("Bug defect contract invalid" in reason for reason in decision.reasons)
    assert any("duplicate sections: Expected" in reason for reason in decision.reasons)


def _body_with_section_content(body: str, section: str, content: str) -> str:
    marker = f"### {section}\n\n"
    start = body.index(marker) + len(marker)
    end = body.find("\n### ", start)
    if end < 0:
        end = len(body)
    return body[:start] + content.strip() + "\n\n" + body[end + 1 :]


@pytest.mark.parametrize(
    ("section", "placeholder"),
    [
        ("Observed", "实际发生：\n..."),
        ("Expected", "正确行为应为：\n..."),
        ("Reproduction / Evidence", "复现步骤或证据：\n无法在当前环境复现"),
        ("Acceptance Criteria", "- [ ] ..."),
    ],
)
def test_bug_form_placeholders_stop_before_delivery_admission(
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    placeholder: str,
) -> None:
    body = _body_with_section_content(BUG_BODY, section, placeholder)
    contract = bug_contract_snapshot(body)

    assert contract["status"] == BugPolicyStatus.RECLASSIFICATION_REQUIRED.value
    assert section in contract["insufficient_sections"]

    issue = _bug_issue()
    issue.update({"body": body, "bug_contract": contract})
    fake = FakeRunner(branch="main")
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        relationships=_relationships(issue_type="Bug"),
    )
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        _resolver(fake).resolve(159), lck_models.Phase.DELIVERY_PREPARE
    )

    assert not decision.eligible
    assert any("Bug defect contract invalid" in reason for reason in decision.reasons)


def test_bug_review_stales_when_candidate_head_changes() -> None:
    reviewed = _review_identity_value()
    state = _review_state()
    bug_contract = bug_contract_snapshot(BUG_BODY)
    issue = dict(state.issue)
    issue.update(
        {
            "labels": {"items": ["type:bug", "codex:ready"]},
            "body": BUG_BODY,
            "bug_contract": bug_contract,
            "critical_outcome": None,
        }
    )
    assert state.task_contract is not None
    task_contract = dict(state.task_contract)
    task_contract.update(
        {
            "body": BUG_BODY,
            "body_sha256": reviewed.task_body_sha256,
            "bug_contract": bug_contract,
        }
    )
    pr = dict(state.open_pr or {})
    pr.update(
        {
            "headRefName": "bug/159-enable-implementation-bearing-bug-workflow",
            "headRefOid": "b" * 40,
        }
    )
    bug_state = replace(
        state,
        issue=issue,
        task_contract=task_contract,
        target_branch="bug/159-enable-implementation-bearing-bug-workflow",
        open_pr=pr,
    )

    with pytest.raises(lck_models.ReviewStaleError, match="REVIEW_STALE_HEAD"):
        lck_review_workspace._assert_review_target_facts_applicable(
            reviewed, bug_state, task_contract
        )


def test_bug_branch_namespace_isolated_from_task_aliases() -> None:
    assert lck_models.branch_matches_profile(
        "bug/159-enable-implementation-bearing-bug-workflow",
        159,
        lck_profiles.BUG_PROFILE,
    )
    assert not lck_models.branch_matches_profile(
        "task/159-enable-implementation-bearing-bug-workflow",
        159,
        lck_profiles.BUG_PROFILE,
    )
    assert not lck_models.branch_matches_profile(
        "bug/159-enable-implementation-bearing-bug-workflow",
        159,
        lck_profiles.TASK_PROFILE,
    )


def test_bug_closeout_uses_shared_path_for_merged_bug_branch() -> None:
    state = _review_state()
    bug_branch = "bug/159-enable-implementation-bearing-bug-workflow"
    bug_contract = bug_contract_snapshot(BUG_BODY)
    issue = dict(state.issue)
    issue.update(
        {
            "state": "CLOSED",
            "labels": {"items": ["type:bug"]},
            "project_status": "Done",
            "body": BUG_BODY,
            "bug_contract": bug_contract,
            "critical_outcome": None,
            "issue_closure": {
                "evidence_status": "complete",
                "status": "closed-by-pr",
                "closer_repository": "owner/repo",
                "closer_number": 200,
            },
        }
    )
    merged_pr = {
        "number": 200,
        "state": "MERGED",
        "baseRefName": "main",
        "baseRefOid": "a" * 40,
        "headRefName": bug_branch,
        "headRefOid": "a" * 40,
        "mergeCommit": {"oid": "b" * 40},
        "mergedAt": "2026-08-23T00:00:00Z",
        "closingIssuesReferences": [{"number": 159}],
    }
    task_contract = dict(cast(dict[str, Any], state.task_contract))
    task_contract.update(
        {"body": BUG_BODY, "bug_contract": bug_contract, "critical_outcome": None}
    )
    bug_state = replace(
        state,
        issue=issue,
        relationships=_relationships(issue_type="Bug"),
        target_branch=bug_branch,
        local_task_branch=bug_branch,
        local_task_head="a" * 40,
        remote_task_branch=bug_branch,
        remote_task_oid="a" * 40,
        open_pr=None,
        merged_pr_numbers=(200,),
        merged=True,
        merged_pr=merged_pr,
        task_contract=task_contract,
    )

    class FixedEffect:
        def __init__(self, effect: str, action: str) -> None:
            self.receipt = lck_models.EffectReceipt(
                effect=effect, action=action, details={}
            )

        def execute(self, *_args: Any, **_kwargs: Any) -> lck_models.EffectReceipt:
            return self.receipt

    class ReviewStore:
        def read_latest_review(self, _task_number: int) -> dict[str, Any]:
            return {"task_number": 159, "review_id": "bug-review", "verdict": "PASS"}

        def read_record(self, _task_number: int, _review_id: str) -> dict[str, Any]:
            return {
                "task_number": 159,
                "review_id": "bug-review",
                "verdict": "PASS",
                "status": "READY_FOR_MERGE_PREFLIGHT",
                "identity": {
                    "task_number": 159,
                    "pr_number": 200,
                    "base_sha": "a" * 40,
                    "head_sha": "a" * 40,
                    "task_body_sha256": issue["body_sha256"],
                    "merge_base_sha": "a" * 40,
                    "effective_diff_sha256": "e" * 64,
                    "changed_files": [],
                },
            }

    result = lck_closeout.CloseoutCompleter(
        StaticResolver(Path.cwd(), bug_state),
        main_effect=FixedEffect("synchronize_main", "synchronized"),
        metadata_effect=FixedEffect("converge_task_metadata", "already-converged"),
        cleanup_effect=FixedEffect("cleanup_task_refs", "already-clean"),
        review_store=ReviewStore(),
    ).complete(159)

    assert result.business_delivery == "COMPLETE"
    assert result.cleanup == "COMPLETE"
