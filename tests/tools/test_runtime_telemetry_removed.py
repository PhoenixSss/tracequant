from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]

REMOVED_TRACKED_PATHS = (
    ".agents/policies/task-workflow-telemetry.md",
    ".agents/task-workflow-telemetry.example.toml",
    "tools/agent_workflow/telemetry.py",
    "tests/tools/test_task_workflow_telemetry.py",
    "docs/workflows/task-workflow-telemetry.md",
    "docs/workflows/task-workflow-token-baseline-63-64.md",
)

ACTIVE_WORKFLOW_FILES = (
    "AGENTS.md",
    ".agents/policies/command-execution.md",
    ".agents/policies/workflow-evidence.md",
    ".agents/skills/task-delivery-runner/SKILL.md",
    ".agents/skills/task-pr-review-runner/SKILL.md",
    ".agents/skills/task-closeout/SKILL.md",
    ".agents/skills/feature-completion-audit/SKILL.md",
    "docs/workflows/agent-skills.md",
    "tools/agent_workflow/workflow_common.py",
    "tools/agent_workflow/workflow_evidence.py",
    "tools/agent_workflow/workflow_validation.py",
    ".github/workflows/ci.yml",
)

APPROVED_TELEMETRY_REFERENCE_FILES = {
    "AGENTS.md": "normative statement that runtime telemetry is disabled",
    "tests/tools/test_runtime_telemetry_removed.py": "removal regression assertions",
    "tests/tools/test_workflow_skills.py": "Skill regression assertion",
    "docs/workflows/benchmarks/task-65-round-2/README.md": (
        "Task #65 round-two benchmark evidence index"
    ),
    "docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json": (
        "machine-readable freeze record for retired Task #65 requirements"
    ),
    "docs/workflows/benchmarks/task-65-round-2/protocol.md": (
        "round-two protocol describing the retired runtime requirement boundary"
    ),
    "docs/workflows/benchmarks/task-65-round-2/task-65-frozen.md": (
        "frozen Task #65 body after removing retired runtime requirements"
    ),
    "docs/workflows/benchmarks/task-65-round-2/task-65-original.md": (
        "verbatim historical Task #65 body retained for audit"
    ),
    "docs/workflows/benchmarks/task-65-round-2/task-65-telemetry-only.diff": (
        "auditable historical-to-frozen Task #65 diff"
    ),
    "docs/workflows/publication-materials/task-material-register.md": (
        "publication material register retaining historical workflow sample labels"
    ),
    "docs/workflows/context-retrieval-v2/before-after-retrieval.md": (
        "#87 retrieval before/after evidence noting Token telemetry remains disabled"
    ),
}

LOCAL_ONLY_EXCLUSIONS = {
    ".agents/task-workflow-telemetry.local.toml",
}


def _tracked_text_paths() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode == 0:
        return [
            ROOT / item.decode("utf-8")
            for item in completed.stdout.split(b"\0")
            if item
        ]

    excluded_roots = {
        ROOT / ".agents/evidence.local",
        ROOT / ".agents/validation.local",
        ROOT / ".agents/telemetry.local",
        ROOT / ".venv",
        ROOT / ".pytest_cache",
        ROOT / ".git",
    }
    paths: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in LOCAL_ONLY_EXCLUSIONS:
            continue
        if any(root in path.parents for root in excluded_roots):
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        paths.append(path)
    return paths


FORBIDDEN_ACTIVE_REFERENCES = (
    "tools/agent_workflow/telemetry.py",
    ".agents/policies/task-workflow-telemetry.md",
    ".agents/task-workflow-telemetry.example.toml",
    "telemetry.py start",
    "telemetry.py status",
    "telemetry.py record",
    "telemetry.py finish",
    "telemetry.py validate",
    "telemetry.py summarize",
    "telemetry.py patch-usage",
    "active telemetry run",
    "usage.source",
    "telemetry_complete",
    "missing_phases",
)


def test_historical_telemetry_reference_allowlist_is_minimal() -> None:
    observed: set[str] = set()
    for path in _tracked_text_paths():
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        if "telemetry" in text.casefold():
            observed.add(path.relative_to(ROOT).as_posix())
    assert observed == set(APPROVED_TELEMETRY_REFERENCE_FILES)


def test_runtime_telemetry_module_and_current_operation_docs_are_removed() -> None:
    for relative in REMOVED_TRACKED_PATHS:
        assert not (ROOT / relative).exists(), relative


def test_active_workflow_files_do_not_reference_runtime_telemetry() -> None:
    for relative in ACTIVE_WORKFLOW_FILES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_ACTIVE_REFERENCES:
            assert forbidden.casefold() not in text.casefold(), (relative, forbidden)


def test_workflow_tools_have_no_telemetry_import_or_subprocess_dependency() -> None:
    for path in (ROOT / "tools/agent_workflow").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        assert "import telemetry" not in lowered
        assert "from telemetry" not in lowered
        assert "telemetry.py" not in lowered


def test_no_renamed_runtime_usage_tracker_exists() -> None:
    suspicious_fragments = (
        "telemetry",
        "usage_tracker",
        "metrics_recorder",
        "analytics_collector",
        "run_manifest",
    )
    for path in (ROOT / "tools/agent_workflow").glob("*.py"):
        stem = path.stem.casefold()
        assert not any(fragment in stem for fragment in suspicious_fragments), path.name


def test_external_analysis_boundary_is_documented() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/workflows/agent-skills.md").read_text(encoding="utf-8")
    assert "outside this repository" in agents
    assert "Raw rollout logs" in agents
    assert "仓库外 Token 消耗分析边界" in guide
    assert "Codex rollout JSONL" in guide
    assert "不得提交本仓库" in guide


def test_gitignore_has_no_runtime_telemetry_specific_rules() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    forbidden_telemetry_rules = {
        ".agents/task-workflow-telemetry.local.toml",
        ".agents/telemetry.local/",
    }
    assert patterns.isdisjoint(forbidden_telemetry_rules)

    # Evidence and validation remain active local-only workflow outputs.
    assert ".agents/evidence.local/" in patterns
    assert ".agents/validation.local/" in patterns
