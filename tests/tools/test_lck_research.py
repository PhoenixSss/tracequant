# ruff: noqa: E402, I001

"""Acceptance tests for the repository-backed Research LCK profile."""

from __future__ import annotations

import hashlib
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

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    closeout as lck_closeout,
    delivery as lck_delivery,
    eligibility as lck_eligibility,
    models as lck_models,
    review as lck_review,
    review_workspace as lck_review_workspace,
    validation as lck_validation,
)
from lck_test_support import FakeReviewWorkspace  # noqa: E402
from research_policy import (  # type: ignore[import-not-found]  # noqa: E402
    RESEARCH_POLICY_ID,
    ResearchOutcome,
    ResearchPolicyError,
    architecture_decision_is_consistent,
    bind_research_outcome,
    decision_contract_snapshot,
    evaluate_research_changes,
    is_valid_research_contract,
    research_artifact_binding,
    research_contract_snapshot,
    research_template_contract,
    require_typed_research_outcome,
)
from workflow_evidence import (  # type: ignore[import-not-found]  # noqa: E402
    _formal_blockers_gate,
    _issue_view_with_contract,
)
from workflow_common import CommandResult, sha256_json  # type: ignore[import-not-found]  # noqa: E402


SHA = "a" * 40
DIGEST = "b" * 64
RESEARCH_BODY = """### Question / Decision Needed

Should the supported path be implemented?

### Context

The decision is needed before implementation.

### Scope

- Repository-backed workflow behavior.

### Non-goals

- No runtime implementation.

### Evidence / Evaluation Criteria

- Confirm the contract and operational risks.

### Expected Outcome / Artifact

Adopt the repository-backed workflow contract and record the resulting ADR.
"""


def test_live_research_issue_contract_uses_research_form() -> None:
    class Runner:
        repo_root = ROOT

        def run(self, argv: Any, *, command_id: str, **_: Any) -> CommandResult:
            return CommandResult(
                command_id,
                tuple(str(item) for item in argv),
                0,
                json.dumps(
                    {
                        "number": 199,
                        "title": "[Research] supported workflow",
                        "state": "OPEN",
                        "labels": [{"name": "type:research"}],
                        "body": RESEARCH_BODY,
                        "projectItems": [],
                    }
                ),
                "",
            )

    warnings: list[dict[str, Any]] = []
    issue, contract = _issue_view_with_contract(
        Runner(),
        "owner/repo",
        199,
        warnings,
        include_comments=False,
        include_closure=False,
    )

    assert warnings == []
    assert issue is not None
    assert contract is not None
    research_contract = issue["research_contract"]
    assert research_contract["status"] == "pass"
    assert research_contract["template_path"] == ".github/ISSUE_TEMPLATE/research.yml"
    assert is_valid_research_contract(research_contract)
    assert contract["research_contract"] == research_contract


def test_research_profile_binds_typed_outcome_to_reviewed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "docs" / "research" / "report.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "# Research report\n\nResearch Outcome: DO NOT IMPLEMENT\n\nEvidence.\n",
        encoding="utf-8",
    )

    binding = research_artifact_binding(
        tmp_path,
        task_number=199,
        pr_number=299,
        base_sha=SHA,
        head_sha="c" * 40,
        task_body_sha256=DIGEST,
        merge_base_sha=SHA,
        effective_diff_sha256="d" * 64,
        changed_files=("docs/research/report.md",),
    )

    assert binding["policy_id"] == RESEARCH_POLICY_ID
    assert binding["outcome"] == ResearchOutcome.DO_NOT_IMPLEMENT.value
    assert binding["outcome_status"] == "typed"
    assert binding["task_number"] == 199
    assert binding["pr_number"] == 299
    assert binding["artifact_files"] == ["docs/research/report.md"]
    assert binding["artifact_digests"][0]["path"] == "docs/research/report.md"
    assert require_typed_research_outcome(binding) is ResearchOutcome.DO_NOT_IMPLEMENT

    rebound = bind_research_outcome(binding, "DO NOT IMPLEMENT")
    assert rebound == binding

    # Exercise the actual shared LCK lifecycle gates that the Critical Outcome
    # names: Research Delivery, Review Complete, human-merge readiness, the
    # closed-PR Closeout path, Project Research Outcome, and blocker admission.
    report = tmp_path / "docs" / "research" / "lifecycle.md"
    report.write_text(
        "# Research report\n\nResearch Outcome: IMPLEMENT\n", encoding="utf-8"
    )
    review_root = tmp_path / "review-root"
    review_report = review_root / "docs" / "research" / "lifecycle.md"
    review_report.parent.mkdir(parents=True)
    review_report.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")

    base_sha = "1" * 40
    head_sha = "2" * 40
    merge_sha = "3" * 40
    branch = "research/199-supported-workflow"
    diff_text = "diff --git a/docs/research/lifecycle.md b/docs/research/lifecycle.md\n"
    body_sha = sha256_json({"body": RESEARCH_BODY})

    class Runner:
        def __init__(self) -> None:
            self.project_writes = 0

        def run(self, argv: Any, *, command_id: str, **_: Any) -> CommandResult:
            command = tuple(str(item) for item in argv)
            if command[:3] == ("git", "diff", "--quiet"):
                return CommandResult(command_id, command, 1, "", "")
            if command[:2] == ("git", "merge-base"):
                return CommandResult(command_id, command, 0, f"{base_sha}\n", "")
            if command[:2] == ("git", "diff") and "--name-only" in command:
                return CommandResult(
                    command_id,
                    command,
                    0,
                    "docs/research/lifecycle.md\n",
                    "",
                )
            if command[:2] == ("git", "diff"):
                return CommandResult(command_id, command, 0, diff_text, "")
            if command == ("git", "branch", "--show-current"):
                return CommandResult(command_id, command, 0, f"{branch}\n", "")
            if command == ("git", "rev-parse", "HEAD"):
                return CommandResult(command_id, command, 0, f"{head_sha}\n", "")
            if command == (
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ):
                return CommandResult(command_id, command, 0, "", "")
            if command[:2] == ("git", "show"):
                return CommandResult(
                    command_id,
                    command,
                    0,
                    "name: CI\non:\n  pull_request:\n    branches: [main]\njobs:\n  quality:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n",
                    "",
                )
            if command[:3] == ("gh", "project", "item-edit"):
                self.project_writes += 1
                return CommandResult(command_id, command, 0, "", "")
            if command[:3] == ("gh", "api", "graphql"):
                return CommandResult(
                    command_id,
                    command,
                    0,
                    json.dumps(
                        {
                            "data": {
                                "user": {
                                    "projectV2": {
                                        "items": {
                                            "nodes": [
                                                {
                                                    "content": {
                                                        "number": 199,
                                                        "repository": {
                                                            "nameWithOwner": "owner/repo"
                                                        },
                                                    },
                                                    "fieldValueByName": {
                                                        "name": "IMPLEMENT"
                                                    },
                                                }
                                            ],
                                            "pageInfo": {"hasNextPage": False},
                                        }
                                    }
                                },
                                "organization": None,
                            }
                        }
                    ),
                    "",
                )
            if command[:3] == ("gh", "issue", "view"):
                raise AssertionError(
                    "Research Outcome must use the Project GraphQL query"
                )
            raise AssertionError(f"unsupported lifecycle command: {command}")

    runner = Runner()
    issue = {
        "number": 199,
        "title": "[Research] supported workflow",
        "state": "OPEN",
        "labels": {"items": ["type:research", "codex:ready"]},
        "project_status": "Review",
        "body": RESEARCH_BODY,
        "body_sha256": body_sha,
        "research_contract": research_contract_snapshot(RESEARCH_BODY),
    }
    pr = {
        "number": 299,
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "baseRefOid": base_sha,
        "headRefName": branch,
        "headRefOid": head_sha,
        "mergeable": "MERGEABLE",
    }
    state = lck_models.LiveState(
        task_number=199,
        repository="owner/repo",
        issue=issue,
        relationships={
            "available": True,
            "blocked_by": {"items": [], "count": 0, "truncated": False},
        },
        git={
            "branch": branch,
            "head_sha": head_sha,
            "local_main_sha": base_sha,
            "origin_main_sha": base_sha,
            "remote_main_sha": base_sha,
            "clean": True,
        },
        target_branch=branch,
        local_task_branch=branch,
        local_task_head=head_sha,
        remote_task_branch=branch,
        remote_task_oid=head_sha,
        open_pr=pr,
        merged_pr_numbers=(),
        merged=False,
        checks={},
        cleanup={},
        task_contract={
            "number": 199,
            "title": issue["title"],
            "body": RESEARCH_BODY,
            "body_sha256": body_sha,
            "research_contract": issue["research_contract"],
        },
    )
    resolver = cast(
        Any,
        type("Resolver", (), {"repo_root": tmp_path, "runner": runner})(),
    )
    resolver.resolve = lambda _task_number: state

    class Eligible:
        def resolve(
            self,
            _state: lck_models.LiveState,
            phase: lck_models.Phase,
            **_: Any,
        ) -> lck_eligibility.PhaseDecision:
            return lck_eligibility.PhaseDecision(phase=phase, eligible=True)

    class Commit:
        def current_head_tree(self) -> str:
            return "4" * 40

        def verify_tree_unchanged(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class Remote:
        def execute(self, *_args: Any, **_kwargs: Any) -> lck_models.EffectReceipt:
            return lck_models.EffectReceipt(
                "ensure_remote_branch", "synchronized", {"remote_oid": head_sha}
            )

    class PR:
        def execute(self, *_args: Any, **_kwargs: Any) -> lck_models.EffectReceipt:
            return lck_models.EffectReceipt(
                "ensure_open_pr",
                "already-open",
                {"number": 299, "head_sha": head_sha, "base_sha": base_sha},
            )

    class Checks:
        def observe(self, _snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
            return {
                "status": "observed",
                "pr": {"number": 299, "head_sha": head_sha, "base_sha": base_sha},
            }

        def evaluate(self, _snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
            return {
                "status": "pass",
                "pr": {"number": 299, "head_sha": head_sha, "base_sha": base_sha},
            }

    class Status:
        def execute(self, *_args: Any, **_kwargs: Any) -> lck_models.EffectReceipt:
            return lck_models.EffectReceipt("set_project_review", "already-review", {})

    delivery = lck_delivery.DeliveryCompleter(
        resolver,
        eligibility=cast(Any, Eligible()),
        formal_validation=cast(
            Any, type("Validation", (), {"run": lambda *_: {"status": "pass"}})()
        ),
        commit_effect=cast(Any, Commit()),
        remote_effect=cast(Any, Remote()),
        pr_effect=cast(Any, PR()),
        status_effect=cast(Any, Status()),
        checks_gate=cast(Any, Checks()),
        research_validation=lck_validation.ResearchValidationGate(resolver),
    )
    delivery_result = delivery.complete(
        199,
        commit_message="test Research lifecycle",
        summary="exercise the supported Research path",
        operation_snapshot=lck_models.OperationSnapshot(
            operation=lck_models.Phase.DELIVERY_COMPLETE.value,
            state=state,
        ),
    )
    assert delivery_result.status == "READY_FOR_REVIEW"
    assert delivery_result.research_artifact["status"] == "pass"

    binding = research_artifact_binding(
        review_root,
        task_number=199,
        pr_number=299,
        base_sha=base_sha,
        head_sha=head_sha,
        task_body_sha256=body_sha,
        merge_base_sha=base_sha,
        effective_diff_sha256=hashlib.sha256(diff_text.encode()).hexdigest(),
        changed_files=("docs/research/lifecycle.md",),
    )
    identity = lck_review_workspace.ReviewIdentity(
        task_number=199,
        pr_number=299,
        base_sha=base_sha,
        head_sha=head_sha,
        task_body_sha256=body_sha,
        merge_base_sha=base_sha,
        effective_diff_sha256=hashlib.sha256(diff_text.encode()).hexdigest(),
        changed_files=("docs/research/lifecycle.md",),
        research_artifact=binding,
    )
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    review_store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = "e" * 32
    review_store.write_guard(
        review_id,
        {
            "task_number": 199,
            "identity": identity.to_dict(),
            "review_root": str(review_root),
            "validation": {"status": "pass"},
            "checks": {"status": "observed"},
            "snapshot": {"operation": "review-prepare"},
        },
    )
    review_result = lck_review.ReviewCompleter(
        resolver,
        eligibility=cast(Any, Eligible()),
        checks_gate=cast(Any, Checks()),
        store=review_store,
        workspace=cast(Any, FakeReviewWorkspace(review_root)),
    ).complete(199, review_id, verdict="PASS")
    assert review_result.status == "READY_FOR_MERGE_PREFLIGHT"
    review_record = review_store.read_record(199, review_id)
    assert review_record["research_outcome"] == "IMPLEMENT"

    merge_result = lck_review.MergePreflight(
        resolver,
        review_gate=cast(
            Any,
            type("ReviewGate", (), {"run": lambda *_: {"status": "pass"}})(),
        ),
        checks_gate=cast(Any, Checks()),
    ).run(199)
    assert merge_result.status == "READY_FOR_HUMAN_MERGE"

    closed_issue = dict(issue)
    closed_issue["state"] = "CLOSED"
    merged_pr = dict(pr)
    merged_pr.update(
        {
            "state": "MERGED",
            "mergeCommit": {"oid": merge_sha},
            "mergedAt": "2026-08-30T00:00:00Z",
            "closingIssuesReferences": [{"number": 199}],
        }
    )
    closeout_state = replace(
        state,
        issue=closed_issue,
        open_pr=None,
        merged=True,
        merged_pr=merged_pr,
        merged_pr_numbers=(299,),
    )
    resolver.resolve = lambda _task_number: closeout_state

    class FixedEffect:
        def __init__(self, effect: str, action: str) -> None:
            self.receipt = lck_models.EffectReceipt(effect, action, {})

        def execute(self, *_args: Any, **_kwargs: Any) -> lck_models.EffectReceipt:
            return self.receipt

    closeout = lck_closeout.CloseoutCompleter(
        resolver,
        eligibility=cast(Any, Eligible()),
        main_effect=cast(Any, FixedEffect("synchronize_main", "synchronized")),
        metadata_effect=cast(
            Any, FixedEffect("converge_task_metadata", "already-converged")
        ),
        cleanup_effect=cast(Any, FixedEffect("cleanup_task_refs", "already-clean")),
        review_store=review_store,
    )
    closeout._validate_merged_identity = lambda _state: (head_sha, merge_sha)
    closeout._validate_reviewed_identity = lambda _state, _pr: review_record
    closeout_result = closeout.complete(199)
    assert closeout_result.business_delivery == "COMPLETE"
    assert closeout_result.research_outcome == "IMPLEMENT"
    assert runner.project_writes == 1

    blocker_gate = _formal_blockers_gate(
        {
            "available": True,
            "blocked_by": {
                "items": [
                    {
                        "number": 198,
                        "state": "CLOSED",
                        "labels": ["type:research"],
                        "labels_complete": True,
                        "research_contract": research_contract_snapshot(RESEARCH_BODY),
                        "research_outcome": "IMPLEMENT",
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        }
    )
    assert blocker_gate["status"] == "pass"


def test_research_policy_reclassifies_changes_outside_repository_artifacts() -> None:
    result = evaluate_research_changes(
        ("docs/research/report.md", "src/tracequant/runtime.py")
    )

    assert result.status.value == "reclassification_required"
    assert result.artifact_files == ("docs/research/report.md",)
    assert result.disallowed_files == ("src/tracequant/runtime.py",)


def test_research_contract_is_bound_to_the_issue_form() -> None:
    template = research_template_contract(ROOT / ".github/ISSUE_TEMPLATE/research.yml")
    assert template.field_ids == (
        "question_decision",
        "context",
        "scope",
        "non_goals",
        "evidence_evaluation",
        "expected_outcome_artifact",
    )
    assert template.section_labels == (
        "Question / Decision Needed",
        "Context",
        "Scope",
        "Non-goals",
        "Evidence / Evaluation Criteria",
        "Expected Outcome / Artifact",
    )

    contract = research_contract_snapshot(RESEARCH_BODY)
    assert is_valid_research_contract(contract)
    assert contract["status"] == "pass"

    invalid = research_contract_snapshot(
        RESEARCH_BODY.replace("- Repository-backed workflow behavior.\n", "")
    )
    assert invalid["status"] == "reclassification_required"
    assert "empty sections" in invalid["detail"]


def test_architecture_decision_requires_matching_downstream_contract() -> None:
    research_decision = decision_contract_snapshot(RESEARCH_BODY, research=True)
    downstream = {
        "body": "### Decision Contract\n\nAdopt the repository-backed workflow contract and record the resulting ADR.\n",
    }
    assert research_decision["status"] == "pass"

    assert architecture_decision_is_consistent(research_decision, downstream)
    assert not architecture_decision_is_consistent(
        research_decision,
        {"body": "### Decision Contract\n\nAn unrelated decision.\n"},
    )
    assert not architecture_decision_is_consistent(research_decision, {})


def test_research_artifact_rejects_conflicting_typed_outcomes(tmp_path: Path) -> None:
    first = tmp_path / "docs" / "research" / "first.md"
    second = tmp_path / "docs" / "research" / "second.md"
    first.parent.mkdir(parents=True)
    first.write_text("Research Outcome: IMPLEMENT\n", encoding="utf-8")
    second.write_text("Research Outcome: NEEDS MORE EVIDENCE\n", encoding="utf-8")

    with pytest.raises(ResearchPolicyError, match="conflicting outcomes"):
        research_artifact_binding(
            tmp_path,
            task_number=199,
            pr_number=299,
            base_sha=SHA,
            head_sha=SHA,
            task_body_sha256=DIGEST,
            merge_base_sha=SHA,
            effective_diff_sha256=DIGEST,
            changed_files=("docs/research/first.md", "docs/research/second.md"),
        )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("IMPLEMENT", "pass"),
        ("DO NOT IMPLEMENT", "fail"),
        ("NEEDS MORE EVIDENCE", "fail"),
    ],
)
def test_closed_research_blocker_requires_an_implementation_outcome(
    outcome: str,
    expected: str,
) -> None:
    result = _formal_blockers_gate(
        {
            "available": True,
            "blocked_by": {
                "items": [
                    {
                        "number": 198,
                        "state": "CLOSED",
                        "labels": ["type:research"],
                        "research_contract": research_contract_snapshot(RESEARCH_BODY),
                        "research_outcome": outcome,
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        }
    )

    assert result["status"] == expected


def test_closed_architecture_decision_requires_consistent_contract() -> None:
    research_decision = decision_contract_snapshot(RESEARCH_BODY, research=True)
    blocker = {
        "number": 198,
        "state": "CLOSED",
        "labels": ["type:research"],
        "research_contract": research_contract_snapshot(RESEARCH_BODY),
        "decision_contract": research_decision,
        "research_outcome": "ARCHITECTURE DECISION",
    }
    relationships = {
        "available": True,
        "blocked_by": {"items": [blocker], "count": 1, "truncated": False},
    }
    assert _formal_blockers_gate(relationships)["status"] == "unknown"
    assert (
        _formal_blockers_gate(
            relationships,
            downstream_contract={
                "body": "### Decision Contract\n\nAdopt the repository-backed workflow contract and record the resulting ADR.\n",
            },
        )["status"]
        == "pass"
    )


def test_incomplete_research_label_evidence_fails_closed() -> None:
    result = _formal_blockers_gate(
        {
            "available": True,
            "blocked_by": {
                "items": [
                    {
                        "number": 198,
                        "state": "CLOSED",
                        "labels": [],
                        "labels_complete": False,
                        "research_outcome": "IMPLEMENT",
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        }
    )
    assert result["status"] == "unknown"


def test_closed_research_blocker_without_outcome_fails_closed() -> None:
    result = _formal_blockers_gate(
        {
            "available": True,
            "blocked_by": {
                "items": [
                    {
                        "number": 198,
                        "state": "CLOSED",
                        "labels": ["type:research"],
                        "research_contract": research_contract_snapshot(RESEARCH_BODY),
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        }
    )

    assert result["status"] == "unknown"
