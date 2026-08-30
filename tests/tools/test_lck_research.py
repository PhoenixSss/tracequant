# ruff: noqa: E402, I001

"""Acceptance tests for the repository-backed Research LCK profile."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from research_policy import (  # type: ignore[import-not-found]  # noqa: E402
    RESEARCH_POLICY_ID,
    ResearchOutcome,
    ResearchPolicyError,
    bind_research_outcome,
    evaluate_research_changes,
    research_artifact_binding,
    require_typed_research_outcome,
)
from workflow_evidence import (  # type: ignore[import-not-found]  # noqa: E402
    _formal_blockers_gate,
)


SHA = "a" * 40
DIGEST = "b" * 64


def test_research_profile_binds_typed_outcome_to_reviewed_artifact(
    tmp_path: Path,
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


def test_research_policy_reclassifies_changes_outside_repository_artifacts() -> None:
    result = evaluate_research_changes(
        ("docs/research/report.md", "src/tracequant/runtime.py")
    )

    assert result.status.value == "reclassification_required"
    assert result.artifact_files == ("docs/research/report.md",)
    assert result.disallowed_files == ("src/tracequant/runtime.py",)


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
        ("ARCHITECTURE DECISION", "pass"),
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
                        "research_outcome": outcome,
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        }
    )

    assert result["status"] == expected


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
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        }
    )

    assert result["status"] == "unknown"
