"""Architecture-level acceptance for the single LCK Task control authority."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

import lck  # type: ignore[import-not-found]  # noqa: E402

TASK_SKILLS = (
    "task-delivery-runner",
    "task-pr-review-runner",
    "task-closeout",
)


def _skill(name: str, root: str = ".agents") -> str:
    return (ROOT / root / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def test_lck_v1_full_lifecycle_has_single_deterministic_control_authority() -> None:
    """Guard the Critical Outcome named by Issue #163."""
    parser = lck._build_parser()
    commands = (
        ("delivery", "prepare", "163"),
        ("delivery", "complete", "163", "--commit-message", "m", "--summary", "s"),
        ("review", "prepare", "163"),
        ("review", "complete", "163", "--review-id", "r", "--verdict", "PASS"),
        ("remediation", "prepare", "163", "--review-id", "r"),
        (
            "remediation",
            "complete",
            "163",
            "--review-id",
            "r",
            "--commit-message",
            "m",
            "--summary",
            "s",
        ),
        ("merge", "preflight", "163"),
        ("closeout", "163"),
    )
    for argv in commands:
        assert parser.parse_args(list(argv)).command in {
            "delivery",
            "review",
            "remediation",
            "merge",
            "closeout",
        }

    lck_source = (ROOT / "tools/agent_workflow/lck.py").read_text(encoding="utf-8")
    assert "write_actions_allowed" not in lck_source
    assert "snapshot_id" not in lck_source
    assert "WORKFLOW_EVIDENCE_READ_ONLY" not in lck_source
    assert "workflow_validation.py" in lck_source
    assert "CommandRunner" in lck_source
    assert "workflow database" not in lck_source.casefold()
    assert "sqlite" not in lck_source.casefold()
    assert "daemon" not in lck_source.casefold()

    for name in TASK_SKILLS:
        codex = _skill(name)
        claude = _skill(name, ".claude")
        assert codex == claude
        for forbidden in (
            "git add",
            "git commit",
            "git push",
            "gh pr create",
            "gh pr merge",
            "wsl2_github_evidence_runner.py",
            "closeout-readonly",
            "workflow-closeout",
            "review-remediation",
            "write_actions_allowed",
            "snapshot_id",
        ):
            assert forbidden not in codex

    lifecycle_tests = {
        "tests/tools/test_lck.py": {
            # Fresh Delivery / interrupted resume / historical PR / live Review.
            "test_delivery_prepare_creates_then_reuses_workspace",
            "test_delivery_complete_allows_review_status_for_partial_effect_recovery",
            "test_closed_pr_does_not_block_current_open_pr",
            "test_closeout_eligibility_uses_merged_live_pr_state",
            "test_review_prepare_builds_context_only_from_live_resolution",
            "test_review_pass_stops_at_human_merge_boundary",
            "test_review_fail_returns_stop_required_without_starting_remediation",
            "test_remediation_prepare_uses_live_head_not_review_record_identity",
            "test_remediation_complete_requires_actual_repair_changes",
            "test_remediation_complete_can_resume_committed_new_head_and_requires_re_review",
            "test_review_complete_head_change_is_review_stale_head",
            "test_review_complete_base_change_is_review_stale_base",
        },
        "tests/tools/test_lck_delivery.py": {
            "test_delivery_complete_revalidates_clean_committed_head_and_stops_at_review_boundary",
            "test_delivery_complete_critical_outcome_failure_blocks_commit_and_remote",
            "test_delivery_complete_stops_before_commit_when_base_changes_during_validation",
        },
        "tests/tools/test_lck_closeout.py": {
            "test_merge_preflight_has_manual_merge_boundary",
            "test_closeout_resolves_business_delivery_and_cleanup_from_live_state",
            "test_closeout_keeps_business_complete_when_cleanup_is_pending",
            "test_closeout_stops_on_remote_divergence",
            "test_cleanup_proves_squash_tree_when_refs_are_already_deleted",
            "test_resolver_recovers_deleted_noncanonical_branch_from_closing_pr",
        },
    }
    for relative, required in lifecycle_tests.items():
        assert required <= _test_names(ROOT / relative), relative

    policy = (ROOT / ".agents/policies/workflow-evidence.md").read_text(
        encoding="utf-8"
    )
    assert "historical Evidence" in policy
    assert "not a Task lifecycle" in policy
    assert "LCK" in policy

    # The pre-LCK compatibility/control front door must be physically absent,
    # not merely unreferenced by the active Skills. Historical publication
    # material may retain old path strings as provenance only.
    for retired in (
        "tools/agent_workflow/wsl2_github_evidence_runner.py",
        "tools/agent_workflow/wsl2_github_evidence_profiles.json",
        ".codex/rules/tracequant-wsl-evidence.rules",
        "tests/tools/test_wsl2_github_evidence_runner.py",
        "tests/tools/test_wsl2_github_evidence_rules.py",
        "tools/agent_workflow/self_review.py",
        "tests/tools/test_self_review.py",
    ):
        assert not (ROOT / retired).exists(), retired

    claude_settings = (ROOT / ".claude/settings.json").read_text(encoding="utf-8")
    assert "wsl2_github_evidence_runner.py" not in claude_settings

    validation_profile = (
        ROOT / "tools/agent_workflow/wsl2_validation_profiles.json"
    ).read_text(encoding="utf-8")
    assert "test_wsl2_github_evidence_runner.py" not in validation_profile
    assert "test_wsl2_github_evidence_rules.py" not in validation_profile

    archive = (ROOT / "docs/workflows/wsl2-github-evidence-runner/README.md").read_text(
        encoding="utf-8"
    )
    assert "frozen historical publication evidence" in archive
    assert "not a current workflow entry point" in archive
