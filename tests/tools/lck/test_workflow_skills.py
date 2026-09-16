"""Contracts exposed by the canonical LCK workflow Skills."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.tools.lck.skill_package_support import copy_instruction_packages
from tools.lck.skill_audit import SKILLS, audit
from tools.lck.skill_package import SkillPackageError, resolve_skill_package

ROOT = Path(__file__).resolve().parents[3]
LOCKED_PYTHON = "uv run --frozen python"


def _skill(name: str) -> str:
    identity = resolve_skill_package(ROOT, f".agents/skills/{name}/SKILL.md")
    return "\n".join(
        (ROOT / path).read_text()
        for path in identity["inventory"]
        if path.startswith(f".agents/skills/{name}/") and path.endswith(".md")
    )


def test_lifecycle_commands_use_the_locked_project_python() -> None:
    combined = "\n".join(_skill(name) for name in SKILLS)
    assert f"{LOCKED_PYTHON} -m tools.lck delivery prepare" in combined
    assert f"{LOCKED_PYTHON} -m tools.lck review prepare" in combined
    assert f"{LOCKED_PYTHON} -m tools.lck merge preflight" in combined
    assert "tools/agent_workflow" not in combined


def test_feature_audit_documents_complete_locked_module_commands() -> None:
    feature = _skill("feature-completion-audit")
    assert (
        f"{LOCKED_PYTHON} -m tools.lck.feature_audit feature-audit-snapshot \\\n"
        "  --feature <FEATURE> --expected-main-sha <SHA>"
    ) in feature
    assert (
        f"{LOCKED_PYTHON} -m tools.lck.validation_runner run \\\n"
        "  --phase feature-audit --skill-path <CALLER_SKILL_PATH> \\\n"
        "  --include-skill-validators "
        "--require-skill-validator"
    ) in feature
    assert (
        f"{LOCKED_PYTHON} -m tools.lck.feature_audit feature-audit-recheck \\\n"
        "  --snapshot-id <SNAPSHOT_ID>"
    ) in feature
    assert "\npython -m tools.lck" not in feature


def test_active_evidence_policy_uses_locked_module_front_doors() -> None:
    policy = (ROOT / ".agents" / "policies" / "workflow-evidence.md").read_text(
        encoding="utf-8"
    )
    for command in (
        "-m tools.lck delivery prepare|complete",
        "-m tools.lck review prepare|complete",
        "-m tools.lck remediation prepare|no-change|complete",
        "-m tools.lck.wsl2_validation_runner <PROFILE>",
        "-m tools.lck.feature_audit feature-audit-snapshot",
        "-m tools.lck.feature_audit feature-audit-recheck",
    ):
        assert f"{LOCKED_PYTHON} {command}" in policy
    assert "\npython -m tools.lck" not in policy


def test_agent_skills_guide_documents_module_audit_and_current_fields() -> None:
    guide = (ROOT / "docs" / "workflows" / "lck" / "agent-skills.md").read_text(
        encoding="utf-8"
    )
    assert f"{LOCKED_PYTHON} -m tools.lck.skill_audit" in guide
    assert "`canonical_skills` 与 `adapters`" in guide
    assert "tools/lck/skill_audit.py\n" not in guide
    assert "`active_skills` 与 `claude_skills`" not in guide


def test_delivery_skill_delegates_git_and_github_effects_to_lck() -> None:
    delivery = _skill("task-delivery-runner")
    assert "delivery prepare" in delivery
    assert "delivery complete" in delivery
    assert "remediation prepare" in delivery
    assert "remediation complete" in delivery
    for direct in ("git commit", "git push", "gh pr create"):
        assert direct not in delivery


def test_review_skill_is_read_only_and_requires_fresh_review() -> None:
    review = _skill("task-pr-review-runner")
    assert "review prepare" in review
    assert "review complete" in review
    assert "implementation-read-only" in review
    assert "Independent Review never modifies implementation" in review
    assert "REVIEW_STALE_HEAD" in review
    assert "REVIEW_STALE_BASE" in review


def test_closeout_never_merges_and_feature_audit_is_separate() -> None:
    closeout = _skill("task-closeout")
    feature = _skill("feature-completion-audit")
    assert "This Skill never merges" in " ".join(closeout.split())
    assert "merge preflight" in closeout
    assert "feature-audit-snapshot" in feature
    assert "feature-audit-recheck" in feature


def test_agent_permission_adapters_only_allow_the_stable_front_doors() -> None:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    serialized = json.dumps(settings, sort_keys=True)
    assert f"{LOCKED_PYTHON} -m tools.lck.wsl2_validation_runner" in serialized
    assert "tools/agent_workflow" not in serialized

    rules = (ROOT / ".codex/rules/tracequant-wsl-validation.rules").read_text(
        encoding="utf-8"
    )
    assert "tools.lck.wsl2_validation_runner" in rules


def test_path_audit_reports_clean_canonical_skills_and_adapters() -> None:
    report, returncode = audit(ROOT)
    assert returncode == 0, report["violations"]
    assert report["status"] == "pass"


def test_workflow_skills_use_progressive_disclosure_with_bound_instruction_identity(
    tmp_path: Path,
) -> None:
    """Exercise real entrypoints, branch selection and audit/Runner identity."""
    from tools.lck.wsl2_validation_runner import _resolve_skill_identity

    copy_instruction_packages(tmp_path)
    cases = {
        "task-delivery-runner": ("initial-delivery", "remediation"),
        "task-pr-review-runner": ("review",),
        "task-closeout": ("closeout",),
        "feature-completion-audit": tuple(f"phase-{n}" for n in range(1, 7)),
    }
    agents = (tmp_path / "AGENTS.md").read_text()
    report, code = audit(tmp_path)
    assert code == 0, report
    for name, routes in cases.items():
        canonical = f".agents/skills/{name}/SKILL.md"
        adapter = canonical.replace(".agents/", ".claude/", 1)
        assert canonical in agents  # The four natural-language entries resolve here.
        identity = resolve_skill_package(tmp_path, canonical)
        assert set(identity["routes"]) == set(routes)
        via_adapter = resolve_skill_package(tmp_path, adapter)
        assert via_adapter["canonical_package_sha256"] == identity["package_sha256"]
        assert via_adapter["package_sha256"] != identity["package_sha256"]
        recorded = _resolve_skill_identity(tmp_path, "targeted", adapter)
        assert recorded == via_adapter
        audited = report["canonical_skills"][name]["instruction_package"]  # type: ignore[index]
        assert audited == identity
        for route in routes:
            selected = resolve_skill_package(tmp_path, canonical, route=route)
            expected = [canonical] + identity["routes"][route]
            assert selected["selected_instructions"] == expected
            assert selected["package_sha256"] == identity["package_sha256"]
            assert len(set(selected["selected_instructions"])) == len(expected)
    # Changing an unselected supporting branch cannot escape package evidence.
    path = ".agents/skills/task-delivery-runner/references/remediation.md"
    (tmp_path / path).write_text(
        (tmp_path / path).read_text() + "\nChanged contract.\n"
    )
    with pytest.raises(SkillPackageError, match="digest mismatch"):
        resolve_skill_package(
            tmp_path,
            ".claude/skills/task-delivery-runner/SKILL.md",
            route="initial-delivery",
        )
    assert audit(tmp_path)[1] == 1


def _refresh_manifest(root: Path, name: str) -> None:
    path = root / f".agents/skills/{name}/package.json"
    value = json.loads(path.read_text())
    for relative in value["files"]:
        value["files"][relative] = hashlib.sha256(
            (root / relative).read_bytes()
        ).hexdigest()
    path.write_text(json.dumps(value, sort_keys=True))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "missing-manifest",
        "undeclared-link",
        "reference-link",
        "non-markdown-link",
        "undeclared-file",
        "traversal",
        "absolute",
        "duplicate-key",
        "too-many-files",
        "symlink",
        "adapter",
        "oversize",
        "route",
        "digest",
    ],
)
def test_instruction_inventory_fails_closed(tmp_path: Path, mutation: str) -> None:
    copy_instruction_packages(tmp_path)
    name = "task-delivery-runner"
    root = f".agents/skills/{name}/SKILL.md"
    manifest_path = tmp_path / f".agents/skills/{name}/package.json"
    manifest = json.loads(manifest_path.read_text())
    support = tmp_path / f".agents/skills/{name}/references/initial-delivery.md"
    expected = None
    if mutation == "missing":
        support.unlink()
    elif mutation == "missing-manifest":
        manifest_path.unlink()
    elif mutation == "undeclared-link":
        support.write_text(support.read_text() + "\nRead [extra](extra.md).\n")
        _refresh_manifest(tmp_path, name)
    elif mutation == "undeclared-file":
        support.with_name("extra.md").write_text("Uninventoried instructions")
    elif mutation in {"reference-link", "non-markdown-link"}:
        extra = (
            "\nRead [extra][policy].\n[policy]: extra.md\n"
            if mutation == "reference-link"
            else "\nRead [extra](extra.txt).\n"
        )
        support.write_text(support.read_text() + extra)
        _refresh_manifest(tmp_path, name)
    elif mutation == "traversal":
        manifest["files"][".agents/skills/../outside.md"] = "a" * 64
        manifest_path.write_text(json.dumps(manifest))
    elif mutation == "symlink":
        support.unlink()
        support.symlink_to(tmp_path / "AGENTS.md")
    elif mutation == "absolute":
        manifest["files"][str(tmp_path / "AGENTS.md")] = "a" * 64
        manifest_path.write_text(json.dumps(manifest))
    elif mutation == "duplicate-key":
        manifest_path.write_text(
            manifest_path.read_text().replace(
                '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
            )
        )
    elif mutation == "too-many-files":
        manifest["files"].update({f"extra{n}.md": "a" * 64 for n in range(33)})
        manifest_path.write_text(json.dumps(manifest))
    elif mutation == "adapter":
        adapter = tmp_path / root.replace(".agents/", ".claude/", 1)
        adapter.write_text(
            adapter.read_text().replace(
                "task-delivery-runner/SKILL.md", "task-closeout/SKILL.md"
            )
        )
    elif mutation == "oversize":
        support.write_text("x" * (256 * 1024 + 1))
        _refresh_manifest(tmp_path, name)
    elif mutation == "route":
        manifest["routes"]["initial-delivery"] = ["AGENTS.md"]
        manifest_path.write_text(json.dumps(manifest))
    elif mutation == "digest":
        expected = "a" * 64
    with pytest.raises(SkillPackageError):
        resolve_skill_package(
            tmp_path, root.replace(".agents/", ".claude/", 1), expected_sha256=expected
        )


def test_instruction_identity_is_reproducible_and_tracks_shared_owner_changes(
    tmp_path: Path,
) -> None:
    copy_instruction_packages(tmp_path)
    path = ".agents/skills/task-delivery-runner/SKILL.md"
    before = resolve_skill_package(ROOT, path)
    assert resolve_skill_package(tmp_path, path) == before
    policy = tmp_path / ".agents/policies/command-execution.md"
    policy.write_text(policy.read_text() + "\nUpdated execution contract.\n")
    _refresh_manifest(tmp_path, "task-delivery-runner")
    after = resolve_skill_package(tmp_path, path)
    assert after["sha256"] == before["sha256"]
    assert after["package_sha256"] != before["package_sha256"]
    with pytest.raises(SkillPackageError, match="package digest mismatch"):
        resolve_skill_package(tmp_path, path, expected_sha256=before["package_sha256"])


@pytest.mark.parametrize(
    ("name", "route", "obligations"),
    [
        (
            "task-delivery-runner",
            "initial-delivery",
            (
                "READY_FOR_DELIVERY",
                "targeted-ready",
                "READY_FOR_REVIEW",
                "Human boundary",
            ),
        ),
        (
            "task-delivery-runner",
            "remediation",
            (
                "explicit maintainer",
                "failed `review_id`",
                "NO_IMPLEMENTATION_CHANGE",
                "READY_FOR_NEW_REVIEW",
                "Never automatically start Review",
            ),
        ),
        (
            "task-pr-review-runner",
            "review",
            (
                "READY_FOR_MERGE_PREFLIGHT",
                "READY_FOR_HUMAN_MERGE",
                "STOP_REQUIRED",
                "REVIEW_STALE_HEAD",
                "REVIEW_STALE_BASE",
                "不通过，需要修复",
                "implementation-read-only",
            ),
        ),
        (
            "task-closeout",
            "closeout",
            (
                "COMPLETE",
                "PENDING",
                "on-demand audit pointer",
                "partial/unknown",
                "same LCK",
                "never merges",
            ),
        ),
        (
            "feature-completion-audit",
            "phase-6",
            (
                "feature-audit-recheck",
                "change invalidates",
                "Feature 已完成，可以由维护者人工收尾",
                "Feature 尚未完成，需要补充或修复 Task",
                "证据不足，暂不能判定 Feature 完成",
                "maintainer-only",
            ),
        ),
    ],
)
def test_selected_instructions_preserve_authority_and_terminal_obligations(
    name: str, route: str, obligations: tuple[str, ...]
) -> None:
    selected = resolve_skill_package(
        ROOT, f".agents/skills/{name}/SKILL.md", route=route
    )
    instructions = "\n".join(
        (ROOT / path).read_text() for path in selected["selected_instructions"]
    )
    for obligation in obligations:
        assert obligation in instructions
    for command in ("git commit", "git push", "gh pr create", "gh pr merge"):
        assert command not in instructions


def test_shared_policy_owners_are_not_duplicated_by_skills() -> None:
    for name in SKILLS:
        instructions = _skill(name)
        assert "command-execution.md" in instructions
        assert "context-retrieval.md" in instructions
        assert "30-second" not in instructions
        assert "elevated-first" not in instructions
        assert "sandbox-first" not in instructions
        root = (ROOT / f".agents/skills/{name}/SKILL.md").read_text()
        adapter = (ROOT / f".claude/skills/{name}/SKILL.md").read_text()
        description = root.split("description: ", 1)[1].splitlines()[0]
        assert f"description: {description}" in adapter
        assert "adapter" not in description.casefold()
    agents = (ROOT / "AGENTS.md").read_text()
    assert "LCK executes the" in agents and "Do not pre-run" in agents
