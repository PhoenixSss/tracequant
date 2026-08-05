from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
RULES = ROOT / ".codex" / "rules" / "quant-system-wsl-validation.rules"
RUNNER_ARGV = ["tools/agent_workflow/wsl2_validation_runner.py"]


def _execpolicy_decision(argv: list[str]) -> str:
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("codex executable is required for real execpolicy checks")
    result = subprocess.run(
        [
            codex,
            "execpolicy",
            "check",
            "--pretty",
            "--rules",
            str(RULES),
            "--",
            *argv,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    payload_start = result.stdout.find("{")
    assert payload_start >= 0, result.stdout
    payload: dict[str, Any] = json.loads(result.stdout[payload_start:])
    decision = payload.get("decision")
    if decision is None:
        return "unmatched"
    assert isinstance(decision, str)
    assert decision in {"allow", "prompt", "forbidden"}
    return decision


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (RUNNER_ARGV + ["current-ci-equivalent"], "allow"),
        (RUNNER_ARGV + ["targeted"], "allow"),
        (RUNNER_ARGV + ["targeted:tools-tests"], "allow"),
        (RUNNER_ARGV + ["targeted:workflow-tests"], "allow"),
        (RUNNER_ARGV + ["post-merge"], "allow"),
        (RUNNER_ARGV + ["workflow-delivery", "--base-sha", "a" * 40], "allow"),
        (RUNNER_ARGV + ["workflow-review", "--base-sha", "a" * 40], "allow"),
        (RUNNER_ARGV + ["workflow-closeout", "--base-sha", "a" * 40], "allow"),
        (RUNNER_ARGV + ["targeted", "tests/tools"], "allow"),
        (RUNNER_ARGV + ["targeted", "arbitrary-value"], "allow"),
        (RUNNER_ARGV + ["targeted", "--command", "pytest"], "forbidden"),
        (RUNNER_ARGV + ["targeted", "--shell", "bash"], "forbidden"),
        (RUNNER_ARGV + ["targeted", "--exec", "anything"], "forbidden"),
    ],
)
def test_execpolicy_runner_profile_matrix(argv: list[str], expected: str) -> None:
    assert _execpolicy_decision(argv) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["uv", "run", "pytest"],
        ["uv", "run", "--frozen", "pytest"],
        ["python3", "tools/agent_workflow/wsl2_validation_runner.py", "targeted"],
        ["python", "tools/agent_workflow/wsl2_validation_runner.py", "targeted"],
        ["bash", "-c", "tools/agent_workflow/wsl2_validation_runner.py targeted"],
        ["sh", "-c", "tools/agent_workflow/wsl2_validation_runner.py targeted"],
    ],
)
def test_execpolicy_direct_interpreters_tools_and_shells_are_not_allowed(
    argv: list[str],
) -> None:
    assert _execpolicy_decision(argv) != "allow"


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "push"],
        ["git", "reset", "--hard"],
        ["git", "clean", "-fd"],
        ["gh", "issue", "edit", "83", "--body", "x"],
        ["gh", "pr", "edit", "101", "--body", "x"],
        ["gh", "api", "graphql", "-f", "query=mutation { viewer { login } }"],
    ],
)
def test_execpolicy_git_and_github_writes_do_not_allow(argv: list[str]) -> None:
    assert _execpolicy_decision(argv) in {"prompt", "forbidden", "unmatched"}


def test_execpolicy_prefix_boundary_is_runner_enforced() -> None:
    assert _execpolicy_decision(RUNNER_ARGV + ["targeted", "tests/tools"]) == "allow"
    assert (
        _execpolicy_decision(RUNNER_ARGV + ["targeted", "arbitrary-value"]) == "allow"
    )


def test_rules_do_not_reference_removed_trusted_runner() -> None:
    text = RULES.read_text(encoding="utf-8")
    assert "trusted_runner.py" not in text
    assert "--trusted-sha" not in text
