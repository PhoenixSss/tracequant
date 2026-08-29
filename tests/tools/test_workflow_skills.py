from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
ACTIVE_SKILLS = {
    "task-delivery-runner": ROOT / ".agents/skills/task-delivery-runner/SKILL.md",
    "task-pr-review-runner": ROOT / ".agents/skills/task-pr-review-runner/SKILL.md",
    "task-closeout": ROOT / ".agents/skills/task-closeout/SKILL.md",
    "feature-completion-audit": ROOT
    / ".agents/skills/feature-completion-audit/SKILL.md",
}
PATH_AUDIT = ROOT / "tools/agent_workflow/skill_path_audit.py"
LCK_COMMAND_PREFIX = "uv run --frozen python tools/agent_workflow/lck.py"
LCK_COMMAND_SOURCES = (
    ACTIVE_SKILLS["task-delivery-runner"],
    ACTIVE_SKILLS["task-pr-review-runner"],
    ROOT / ".claude/skills/task-delivery-runner/SKILL.md",
    ROOT / ".claude/skills/task-pr-review-runner/SKILL.md",
    ROOT / "docs/development/pr-review.md",
)


def _dual_skill(name: str) -> tuple[str, str]:
    return (
        (ROOT / ".agents/skills" / name / "SKILL.md").read_text(encoding="utf-8"),
        (ROOT / ".claude/skills" / name / "SKILL.md").read_text(encoding="utf-8"),
    )


# The sandbox execution-route contract (sandbox-first / elevated-first) is a
# Codex execution-profile concept. Claude Code permissions are governed by
# `.claude/settings.json`, so the Claude Skills deliberately omit the
# Codex-only `## Execution route contract` section; the dual Skills stay
# mirrored modulo that single section.


def _without_route_contract(text: str) -> str:
    """Return ``text`` with the Codex-only execution route contract removed."""
    marker = "## Execution route contract"
    start = text.find(marker)
    assert start != -1
    # The section ends at the next heading, or at the `Critical Outcome`
    # paragraph that follows it in the delivery Skill.
    ends = [
        index
        for probe in ("\n## ", "\nIt must contain")
        if (index := text.find(probe, start)) != -1
    ]
    assert ends
    return text[:start] + text[min(ends) + 1 :]


def test_current_runner_skills_use_one_mechanical_path() -> None:
    forbidden = (
        "python tools/agent_workflow/workflow_evidence.py",
        "python -X utf8 tools/agent_workflow/workflow_evidence.py",
        "python tools/agent_workflow/workflow_validation.py",
        "python -X utf8 tools/agent_workflow/workflow_validation.py",
        "gh pr view",
        "gh issue view",
        "uv lock --check",
        "uv run --frozen pytest",
        "git status --short",
        "trusted_runner.py",
        "--trusted-sha",
    )
    for name, path in ACTIVE_SKILLS.items():
        text = path.read_text(encoding="utf-8")
        assert f"name: {name}" in text
        assert len(text) < 28_000
        assert len(text.splitlines()) < 550
        for fragment in forbidden:
            assert fragment not in text
        assert "telemetry" not in text.casefold()

    # Review is deliberately cut over from Evidence Runner snapshot authority.
    review = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert ".agents/policies/command-execution.md" in review
    assert ".agents/policies/workflow-evidence.md" not in review
    for name in ("task-delivery-runner", "task-closeout", "feature-completion-audit"):
        assert ".agents/policies/workflow-evidence.md" in ACTIVE_SKILLS[name].read_text(
            encoding="utf-8"
        )


def test_lck_commands_use_the_pinned_project_python() -> None:
    for path in LCK_COMMAND_SOURCES:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "tools/agent_workflow/lck.py" in line:
                assert line.strip().startswith(LCK_COMMAND_PREFIX), (
                    f"{path} contains a non-canonical LCK launcher: {line!r}"
                )

    profile = (ROOT / ".agents/execution-profile.example.toml").read_text(
        encoding="utf-8"
    )
    assert 'executable = "python"' not in profile
    assert 'name = "lck-delivery-prepare"' in profile


def test_lck_git_write_operations_use_deterministic_controlled_execution_routes() -> (
    None
):
    profile = tomllib.loads(
        (ROOT / ".agents/execution-profile.example.toml").read_text(encoding="utf-8")
    )

    assert profile == {
        "schema_version": 1,
        "default_route": "sandbox-first",
        "rules": profile["rules"],
    }
    rules = profile["rules"]
    assert isinstance(rules, list)
    assert all(
        set(rule) == {"name", "executable", "argument_prefix", "route", "reason"}
        for rule in rules
    )
    lck_prefix = (
        "run",
        "--frozen",
        "python",
        "tools/agent_workflow/lck.py",
    )
    expected_routes = {
        lck_prefix + ("status",): "sandbox-first",
        lck_prefix + ("delivery", "prepare"): "elevated-first",
        lck_prefix + ("delivery", "complete"): "elevated-first",
        lck_prefix + ("review", "prepare"): "sandbox-first",
        lck_prefix + ("review", "complete"): "sandbox-first",
        lck_prefix + ("remediation", "prepare"): "elevated-first",
        lck_prefix + ("remediation", "no-change"): "sandbox-first",
        lck_prefix + ("remediation", "complete"): "elevated-first",
        lck_prefix + ("merge", "preflight"): "sandbox-first",
        lck_prefix + ("merge-preflight",): "sandbox-first",
        lck_prefix + ("closeout",): "elevated-first",
    }
    lck_rules = {
        tuple(rule["argument_prefix"]): rule["route"]
        for rule in rules
        if rule["executable"] == "uv"
    }
    assert lck_rules == expected_routes
    assert all(prefix[: len(lck_prefix)] == lck_prefix for prefix in lck_rules)
    assert all(prefix for prefix in lck_rules)
    assert all(rule["executable"] != "python" for rule in rules)
    assert all(rule["executable"] != "gh" for rule in rules)
    assert () not in lck_rules

    git_rules = {
        (rule["executable"], tuple(rule["argument_prefix"]), rule["route"])
        for rule in rules
        if rule["executable"] == "git"
    }
    assert git_rules == {
        ("git", ("status",), "sandbox-first"),
        ("git", ("diff",), "sandbox-first"),
    }

    policy = (ROOT / ".agents/policies/command-execution.md").read_text(
        encoding="utf-8"
    )
    assert "The route classification is deterministic" in policy
    assert "The profile must not contain" in policy
    assert "a generic `uv`, `python`, `git`, or `gh` write rule." in policy
    assert "Independent Review" in policy
    for retry_rule in (
        "sandbox-denied",
        "credential-isolated",
        "Only `sandbox-denied` or `credential-isolated` may justify an exact-context",
        "Do not retry a real command failure with broader permissions.",
    ):
        assert retry_rule in policy

    # The route contract is a Codex execution-profile concept; the Codex
    # Skills document it while the Claude Skills deliberately omit it (Claude
    # Code permissions come from `.claude/settings.json`). The Skills stay
    # mirrored modulo that single Codex-only section.
    for name in (
        "task-delivery-runner",
        "task-pr-review-runner",
        "task-closeout",
    ):
        codex, claude = _dual_skill(name)
        assert "## Execution route contract" in codex
        assert "## Execution route contract" not in claude
        assert _without_route_contract(codex) == claude


def test_delivery_runner_uses_lck_for_initial_delivery_and_explicit_remediation() -> (
    None
):
    text = ACTIVE_SKILLS["task-delivery-runner"].read_text(encoding="utf-8")
    assert "tools/agent_workflow/lck.py delivery prepare" in text
    assert "tools/agent_workflow/lck.py delivery complete" in text
    assert "Critical Outcome" in text
    assert "READY_FOR_REVIEW" in text
    assert "Agent / Skill MUST NOT directly" in text
    assert "branch/SHA/base/PR identity as workflow authority" in text
    assert "no alternate write route" in text

    assert "## Review remediation" in text
    assert "tools/agent_workflow/lck.py remediation prepare" in text
    assert "tools/agent_workflow/lck.py remediation no-change" in text
    assert "tools/agent_workflow/lck.py remediation complete" in text
    assert "semantic findings" in text
    assert "mechanical facts from the Review" in text
    assert "reuse existing OPEN PR" in text
    assert "READY_FOR_NEW_REVIEW" in text
    assert "deferred Review-acceptance item" in text
    assert "not** a prerequisite for `remediation complete`" in text
    assert "provider-attributed implementation receipts" in text
    assert "MUST NOT start\nIndependent Review automatically" in text


def test_delivery_skill_reserves_full_validation_for_lck_complete() -> None:
    for path in (
        ROOT / ".agents/skills/task-delivery-runner/SKILL.md",
        ROOT / ".claude/skills/task-delivery-runner/SKILL.md",
    ):
        text = path.read_text(encoding="utf-8")
        implementation = " ".join(
            text.split("### 2. Semantic implementation", 1)[1]
            .split("### 3. LCK Delivery Complete", 1)[0]
            .split()
        )
        completion = " ".join(text.split("### 3. LCK Delivery Complete", 1)[1].split())

        assert "Development validation boundary" in implementation
        assert "development feedback, not Delivery authorization" in implementation
        assert "smallest check that" in implementation
        assert "current-ci-equivalent" in implementation
        assert "workflow-delivery" in implementation
        assert "merely as a precaution" in implementation
        assert "concrete diagnostic" in implementation
        assert "explicit maintainer request" in implementation
        assert "global-infrastructure" in implementation
        assert "alone is not a diagnostic trigger" in implementation
        assert "targeted-ready" in implementation
        assert "precautionary repository-wide" in implementation
        assert "status-only checking" in implementation
        assert "does not replace" in implementation
        assert "Critical Outcome verifier" in implementation
        assert "Critical Outcome and formal Delivery validation" in implementation
        assert "authorize commit/push/PR effects" in implementation

        assert "run formal Delivery validation" in completion
        assert "prove validated staged tree is unchanged" in completion
        assert "commit_current_tree" in completion


def test_delivery_lck_contract_is_shared_by_both_skills() -> None:
    agent, claude = _dual_skill("task-delivery-runner")
    assert _without_route_contract(agent) == claude
    for phrase in (
        "LCK Delivery Prepare",
        "LCK Delivery Complete",
        "commit_current_tree",
        "ensure_remote_branch",
        "ensure_open_pr",
        "READY_FOR_REVIEW",
        "LCK Remediation Prepare",
        "LCK Remediation No Change",
        "LCK Remediation Complete",
        "READY_FOR_NEW_REVIEW",
        "The Skill is a semantic procedure",
    ):
        assert phrase in agent
    assert "git switch -c task/<Issue number>-<slug>" not in agent
    assert "branch_bootstrap = pass" not in agent
    assert "--bootstrap-verify" not in agent


def test_review_runner_is_fresh_read_only_lck_review() -> None:
    agent, claude = _dual_skill("task-pr-review-runner")
    assert _without_route_contract(agent) == claude
    text = agent
    for phrase in (
        "fresh session",
        "implementation-read-only",
        "tools/agent_workflow/lck.py review prepare",
        "READY_FOR_SEMANTIC_REVIEW",
        "Inspect",
        "Reason",
        "Judge",
        "Report",
        "tools/agent_workflow/lck.py review complete",
        "REVIEW_STALE_HEAD",
        "REVIEW_STALE_BASE",
        "REVIEW_STALE_TASK",
        "REVIEW_STALE_DIFF",
        "READY_FOR_MERGE_PREFLIGHT",
        "tools/agent_workflow/lck.py merge preflight",
        "READY_FOR_HUMAN_MERGE",
        "STOP_REQUIRED",
        "通过，可以人工合并",
        "不通过，需要修复",
    ):
        assert phrase in text
    assert "do not start Remediation" in text
    assert "Task Contract" in text
    assert "complete effective diff" in text


def test_review_runner_does_not_restore_pre_cutover_authority() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    for forbidden in (
        "workflow-review",
        "recheck --snapshot-id",
        "--expected-head-sha",
        "--expected-base-sha",
        "有条件通过，不得合并",
        "Remediation handoff",
        "Reviewed head SHA:",
        "Objective gates:",
        "Maintainer decision required:",
    ):
        assert forbidden not in text
    assert "Delivery handoff" in text
    assert "MUST NOT pass PR/base/head/checks/snapshot" in text
    assert "do not fall back to archived evidence snapshots" in text


def test_review_fail_stops_and_never_auto_starts_remediation() -> None:
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "FAIL" in text
    assert "STOP_REQUIRED" in text
    assert "maintainer may explicitly use for\nRemediation" in text
    assert "Do not emit an automatic" in text and "Delivery prompt" in text
    assert "do not start Remediation" in text


def test_review_skill_keeps_semantic_coverage_without_mechanical_handoff_matrix() -> (
    None
):
    text = ACTIVE_SKILLS["task-pr-review-runner"].read_text(encoding="utf-8")
    assert "AC\ncoverage/evidence matrix" in text
    assert "complete effective diff" in text
    assert "Inspect correctness" in text
    assert "test source, coverage, and failure semantics" in text
    assert "Historical Skill matches source commit blob" not in text
    assert "All target Skills are canonical-state" not in text


def test_task_skills_enforce_lck_authority_consumption_boundaries() -> None:
    delivery_agent, delivery_claude = _dual_skill("task-delivery-runner")
    review_agent, review_claude = _dual_skill("task-pr-review-runner")
    closeout_agent, closeout_claude = _dual_skill("task-closeout")

    assert _without_route_contract(delivery_agent) == delivery_claude
    assert _without_route_contract(review_agent) == review_claude
    assert _without_route_contract(closeout_agent) == closeout_claude

    delivery = " ".join(delivery_agent.split())
    for phrase in (
        "targeted-ready",
        "global-infrastructure classification alone is not a diagnostic trigger",
        "Do not insert precautionary repository-wide pytest/static validation",
        "A newly observed failure/finding or an explicit maintainer request",
        "remains non-authoritative",
    ):
        assert phrase in delivery

    review = " ".join(review_agent.split())
    for phrase in (
        "authoritative formal Review validation for the exact reviewed head",
        "immutable review evidence artifact, not an executable development workspace",
        "test source, coverage, and failure semantics",
        "do not execute pytest, Ruff, mypy, lock checks, Skill validators",
        "validation/evidence gap",
        "not duplicate mechanical validation",
    ):
        assert phrase in review

    closeout = " ".join(closeout_agent.split())
    for phrase in (
        "`receipt_reference` is a pointer, not an instruction to read",
        "terminal success with Business Delivery `COMPLETE`, Cleanup `COMPLETE`",
        "Do not follow `receipt_reference`",
        "partial/unknown state",
        "explicit maintainer request for audit details",
        "Exact merge/branch/worktree audit details are not required",
    ):
        assert phrase in closeout

    policy = (ROOT / ".agents/policies/workflow-evidence.md").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / "docs/development/issue-workflow.md").read_text(encoding="utf-8")
    review_doc = (ROOT / "docs/development/pr-review.md").read_text(encoding="utf-8")
    assert "## Authority consumption" in policy
    assert "receipt_reference` is an on-demand audit pointer" in policy
    assert "candidate 进入 targeted-ready" in workflow
    assert "independent semantic judgement" in review_doc
    assert "immutable review evidence artifact" in review_doc


def test_closeout_and_feature_audit_keep_manual_gates() -> None:
    closeout = ACTIVE_SKILLS["task-closeout"].read_text(encoding="utf-8")
    audit = ACTIVE_SKILLS["feature-completion-audit"].read_text(encoding="utf-8")
    assert "tools/agent_workflow/lck.py merge preflight" in closeout
    assert "tools/agent_workflow/lck.py closeout" in closeout
    assert "Business Delivery" in closeout
    assert "Cleanup" in closeout
    assert "This Skill never merges" in closeout
    assert "cleanup-only" in closeout
    assert "eligible-under-capability-limited-policy" in closeout
    assert "Feature 已完成，可以由维护者人工收尾" in audit
    assert "Feature 尚未完成，需要补充或修复 Task" in audit
    assert "证据不足，暂不能判定 Feature 完成" in audit
    assert "Audited main SHA" in audit
    assert "performs none" in audit


def test_active_skills_have_no_evolution_traces() -> None:
    forbidden_traces = (
        "trusted_runner.py",
        "--trusted-sha",
        "trusted base",
        "trusted-base",
        "trusted control",
        "predecessor",
        "old chain",
        "old path",
        "legacy path",
        "task #85",
    )
    for path in ACTIVE_SKILLS.values():
        text = path.read_text(encoding="utf-8").casefold()
        for trace in forbidden_traces:
            assert trace not in text


def test_path_audit_reports_only_clean_current_skills() -> None:
    result = subprocess.run(
        [sys.executable, str(PATH_AUDIT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["schema_version"] == 6
    assert value["status"] == "pass"
    assert value["totals"]["direct_command_path_count"] == 0
    assert value["totals"]["evolution_trace_count"] == 0
    assert "baseline_skills" not in value


def test_legacy_skill_directories_are_absent() -> None:
    for relative in (
        ".agents/skills/task-delivery",
        ".agents/skills/task-pr-review",
    ):
        assert not (ROOT / relative).exists(), relative


def test_local_workflow_artifact_directories_are_ignored() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".agents/evidence.local/" in patterns
    assert ".agents/validation.local/" in patterns
    assert ".workflow.local/" in patterns
