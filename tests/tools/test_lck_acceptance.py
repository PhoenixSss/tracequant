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

    architecture_delta = (
        ROOT / "docs/workflows/lck-v1-closeout-architecture-delta.md"
    ).read_text(encoding="utf-8")
    assert "git fetch --prune origin" in architecture_delta
    assert "refs/remotes/origin/main" in architecture_delta
    assert "4,215-line" in architecture_delta

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
