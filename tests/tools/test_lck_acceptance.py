# ruff: noqa: E402, I001

"""Architecture-level acceptance for the single LCK Task control authority."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
AGENT_WORKFLOW = str(ROOT / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    cli as lck_cli,
    issue_profiles as lck_profiles,
)
from workflow_evidence import (  # type: ignore[import-not-found]  # noqa: E402
    _formal_blockers_gate,
)

TASK_SKILLS = (
    "task-delivery-runner",
    "task-pr-review-runner",
    "task-closeout",
)


def _skill(name: str, root: str = ".agents") -> str:
    return (ROOT / root / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def _without_route_contract(text: str) -> str:
    """Remove the Codex-only `## Execution route contract` section.

    The sandbox route (sandbox-first / elevated-first) is a Codex
    execution-profile concept; Claude Code permissions come from
    `.claude/settings.json`, so the Claude Skills deliberately omit it.
    """
    marker = "## Execution route contract"
    start = text.find(marker)
    assert start != -1
    ends = [
        index
        for probe in ("\n## ", "\nIt must contain")
        if (index := text.find(probe, start)) != -1
    ]
    assert ends
    return text[:start] + text[min(ends) + 1 :]


def test_lck_v1_full_lifecycle_has_single_deterministic_control_authority() -> None:
    """Guard the LCK v1 architecture contract named by Issue #163.

    Focused unit tests and a maintainer-started real workflow verify lifecycle
    behavior; this acceptance gate checks the single control-authority shape
    and the absence of retired control paths.
    """
    parser = lck_cli._build_parser()
    commands = (
        ("delivery", "prepare", "163"),
        ("delivery", "complete", "163", "--commit-message", "m", "--summary", "s"),
        ("review", "prepare", "163"),
        ("review", "complete", "163", "--review-id", "r", "--verdict", "PASS"),
        ("remediation", "prepare", "163", "--review-id", "r"),
        (
            "remediation",
            "no-change",
            "163",
            "--review-id",
            "r",
            "--summary",
            "s",
        ),
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
    core_root = ROOT / "tools/agent_workflow/lck_core"
    core_sources = {
        path.name: path.read_text(encoding="utf-8") for path in core_root.glob("*.py")
    }
    core_source = "\n".join(core_sources.values())
    assert "write_actions_allowed" not in core_source
    assert "snapshot_id" not in core_source
    assert "WORKFLOW_EVIDENCE_READ_ONLY" not in core_source
    assert "workflow_validation.py" in core_sources["validation.py"]
    assert "CommandRunner" in core_sources["state.py"]
    assert "workflow database" not in core_source.casefold()
    assert "sqlite" not in core_source.casefold()
    assert "daemon" not in core_source.casefold()
    assert "from lck_core.cli import main" in lck_source
    assert len(lck_source.splitlines()) < 500

    for name in TASK_SKILLS:
        codex = _skill(name)
        claude = _skill(name, ".claude")
        assert _without_route_contract(codex) == claude
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

    policy = (ROOT / ".agents/policies/workflow-evidence.md").read_text(
        encoding="utf-8"
    )
    assert "historical Evidence" in policy
    assert "not a Task lifecycle" in policy
    assert "LCK" in policy
    assert ".workflow.local/lck/" in policy
    assert "remediation prepare|no-change|complete" in policy
    assert "Review Prepare fails closed" in policy
    assert "Independent Review MUST NOT write source" in policy
    assert ".agents/evidence.local/" in policy
    assert ".agents/validation.local/" in policy

    command_policy = (ROOT / ".agents/policies/command-execution.md").read_text(
        encoding="utf-8"
    )
    assert "these roots are not interchangeable fallbacks" in command_policy
    assert "source repository" in command_policy
    assert ".workflow.local/lck/review-validation/" in command_policy

    architecture_delta = (
        ROOT / "docs/workflows/lck-v1-closeout-architecture-delta.md"
    ).read_text(encoding="utf-8")
    assert "git ls-remote origin refs/heads/main" in architecture_delta
    assert "refs/remotes/origin/main" in architecture_delta
    assert "git fetch --prune origin" not in core_source
    assert "lck-review-worktree-" not in core_source
    assert '["git", "worktree", "add"' not in core_source
    assert '["git", "worktree", "remove"' not in core_source
    assert '["git", "worktree", "prune"' not in core_source
    assert '"clone",' in core_sources["review_workspace.py"]
    assert '"--no-hardlinks",' in core_sources["review_workspace.py"]
    assert '"review-validation"' in core_sources["validation.py"]
    assert core_sources["state.py"].count("self.resolver.resolve(task_number)") == 1
    assert "while time.monotonic" not in core_source
    assert "check-timeout-seconds" not in core_source
    assert "required_status_checks" not in core_source
    assert "gh-required-checks-" not in core_source
    assert "_repository_required_checks_at_commit" in core_sources["state.py"]
    assert (
        '"git", "show", f"{source_sha}:{REQUIRED_CHECKS_WORKFLOW}"'
        in core_sources["state.py"]
    )
    assert '"source_sha": source_sha' in core_sources["state.py"]
    assert '"configuration": "repository-base-ci"' in core_sources["state.py"]
    assert 'repo_root / "pyproject.toml"' not in core_source
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "jobs:" in ci
    assert "quality:" in ci
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.tracequant.lck]" not in pyproject
    assert "required-checks" not in pyproject
    assert 'repo_root / "pyproject.toml"' not in core_source
    assert "repository-controlled" in policy
    assert "required-check policy" in policy
    assert "exact trusted base commit" in policy
    assert "mutable checkout" in policy
    assert "canonical formatted candidate" in architecture_delta
    assert "intentionally not frozen in prose" in architecture_delta
    assert "remote_main_sha" in core_sources["models.py"]
    assert "local_main_sha" in architecture_delta
    assert "tracking_main_sha" in architecture_delta
    assert "pre-merge" in architecture_delta
    assert "post-merge" in architecture_delta
    assert (
        "not prerequisites for this PR's Independent Review PASS" in architecture_delta
    )

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


def test_typed_leaf_workflows_share_one_lck_control_kernel() -> None:
    """Verify Task #202's profile-routing and shared-kernel contract."""

    profiles = lck_profiles.PROFILES_BY_TYPE_LABEL
    assert set(profiles) == {
        "type:task",
        "type:bug",
        "type:documentation",
        "type:research",
    }
    assert all(profile.lifecycle_enabled for profile in profiles.values())
    assert len({profile.profile_id for profile in profiles.values()}) == 4
    assert len({profile.candidate_capability for profile in profiles.values()}) == 4
    assert lck_profiles.TASK_PROFILE.requires_critical_outcome
    assert not lck_profiles.BUG_PROFILE.requires_critical_outcome
    assert not lck_profiles.DOCUMENTATION_PROFILE.requires_critical_outcome
    assert not lck_profiles.RESEARCH_PROFILE.requires_critical_outcome
    assert lck_profiles.TASK_PROFILE.allow_legacy_branch_aliases
    assert not lck_profiles.BUG_PROFILE.allow_legacy_branch_aliases
    assert not lck_profiles.DOCUMENTATION_PROFILE.allow_legacy_branch_aliases
    assert not lck_profiles.RESEARCH_PROFILE.allow_legacy_branch_aliases

    core_root = ROOT / "tools/agent_workflow/lck_core"
    phase_sources = {
        name: (core_root / name).read_text(encoding="utf-8")
        for name in (
            "eligibility.py",
            "delivery.py",
            "review.py",
            "review_workspace.py",
            "closeout.py",
            "state.py",
            "models.py",
        )
    }
    assert all("LeafIssueKind" not in source for source in phase_sources.values())
    assert all("type:" not in source for source in phase_sources.values())
    assert "profile_policies" in phase_sources["eligibility.py"]
    assert "profile_policies" in phase_sources["delivery.py"]
    assert "profile_policies" in phase_sources["review.py"]
    assert "profile_policies" in phase_sources["review_workspace.py"]
    assert "profile_policies" in phase_sources["closeout.py"]
    profile_policy_source = (core_root / "profile_policies.py").read_text(
        encoding="utf-8"
    )
    assert "_CONTRACT_POLICIES" in profile_policy_source
    assert "run_profile_delivery_gates" in profile_policy_source
    assert "validate_profile_contract" in profile_policy_source

    workflow_evidence = (ROOT / "tools/agent_workflow/workflow_evidence.py").read_text(
        encoding="utf-8"
    )
    assert "resolve_leaf_issue_profile" in workflow_evidence
    assert "validate_profile_contract" in workflow_evidence

    relationships = {
        "available": True,
        "blocked_by": {
            "items": [],
            "count": 0,
            "truncated": False,
        },
    }
    for labels in ([], ["type:unknown"], ["type:feature"], ["type:task", "type:bug"]):
        result = _formal_blockers_gate(
            {
                **relationships,
                "blocked_by": {
                    "items": [
                        {
                            "state": "CLOSED",
                            "labels": labels,
                            "labels_complete": True,
                        }
                    ],
                    "count": 1,
                    "truncated": False,
                },
            }
        )
        assert result["status"] == "unknown"

    navigation = (ROOT / "docs/architecture/typed-leaf-workflows.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "canonical type:* label",
        "issue_profiles.py",
        "profile_policies.py",
        "shared LCK phase controllers",
        "type:task",
        "type:bug",
        "type:documentation",
        "type:research",
        "Issue #212",
        "Issue #191",
        "Issue #201",
        "Task #202",
        "#198",
        "#199",
        "#200",
    ):
        assert marker in navigation
    for stale_marker in ("Task #198", "Task #199", "Task #200", "#66"):
        assert stale_marker not in navigation


def test_remediation_candidate_creation_is_not_blocked_by_future_review_evidence() -> (
    None
):
    root = Path(__file__).parents[2]
    charter = (root / "docs/workflows/LCK-v1-Design-Charter.md").read_text(
        encoding="utf-8"
    )
    delivery_skill = (root / ".agents/skills/task-delivery-runner/SKILL.md").read_text(
        encoding="utf-8"
    )
    review_skill = (root / ".agents/skills/task-pr-review-runner/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Acceptance evidence is gated where it can truthfully exist" in charter
    assert "MUST NOT form a circular precondition" in charter
    assert "deferred Review-acceptance item" in delivery_skill
    assert "not** a prerequisite for `remediation complete`" in delivery_skill
    assert "remediation no-change" in delivery_skill
    assert "NO_IMPLEMENTATION_CHANGE" in delivery_skill
    assert "Review-acceptance evidence gap" in review_skill
