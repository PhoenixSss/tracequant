"""Architecture-level acceptance for the single LCK Task control authority."""

from __future__ import annotations

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


def test_lck_v1_full_lifecycle_has_single_deterministic_control_authority() -> None:
    """Guard the LCK v1 architecture contract named by Issue #163.

    Focused unit tests and a maintainer-started real workflow verify lifecycle
    behavior; this acceptance gate checks the single control-authority shape
    and the absence of retired control paths.
    """
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

    policy = (ROOT / ".agents/policies/workflow-evidence.md").read_text(
        encoding="utf-8"
    )
    assert "historical Evidence" in policy
    assert "not a Task lifecycle" in policy
    assert "LCK" in policy
    assert ".workflow.local/lck/" in policy
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
    assert "git fetch --prune origin" not in lck_source
    assert "lck-review-worktree-" not in lck_source
    assert '["git", "worktree", "add"' not in lck_source
    assert '["git", "worktree", "remove"' not in lck_source
    assert '["git", "worktree", "prune"' not in lck_source
    assert '"clone",' in lck_source
    assert '"--no-hardlinks",' in lck_source
    assert '"review-validation"' in lck_source
    assert lck_source.count("self.resolver.resolve(task_number)") == 1
    assert "while time.monotonic" not in lck_source
    assert "check-timeout-seconds" not in lck_source
    assert "required_status_checks" not in lck_source
    assert "gh-required-checks-" not in lck_source
    assert "_repository_required_checks_at_commit" in lck_source
    assert '"git", "show", f"{source_sha}:{REQUIRED_CHECKS_WORKFLOW}"' in lck_source
    assert '"source_sha": source_sha' in lck_source
    assert '"configuration": "repository-base-ci"' in lck_source
    assert 'repo_root / "pyproject.toml"' not in lck_source
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "jobs:" in ci
    assert "quality:" in ci
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.tracequant.lck]" not in pyproject
    assert "required-checks" not in pyproject
    assert 'repo_root / "pyproject.toml"' not in lck_source
    assert "repository-controlled" in policy
    assert "required-check policy" in policy
    assert "exact trusted base commit" in policy
    assert "mutable checkout" in policy
    core_lines = len((ROOT / "tools/agent_workflow/lck.py").read_text().splitlines())
    assert f"`lck.py`: {core_lines:,} LOC" in architecture_delta
    assert "remote_main_sha" in lck_source
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
    assert "Review-acceptance evidence gap" in review_skill
