from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
RULES = ROOT / ".codex" / "rules" / "quant-system-wsl-evidence.rules"
RUNNER = ["tools/agent_workflow/wsl2_github_evidence_runner.py"]
SHA = "a" * 40


def _decision(argv: list[str]) -> str:
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("codex executable is required for real execpolicy checks")
    completed = subprocess.run(
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
    assert completed.returncode == 0, completed.stderr
    start = completed.stdout.find("{")
    assert start >= 0, completed.stdout
    value: dict[str, Any] = json.loads(completed.stdout[start:])
    decision = value.get("decision")
    if decision is None:
        return "unmatched"
    assert decision in {"allow", "prompt", "forbidden"}
    return str(decision)


@pytest.mark.parametrize(
    "argv",
    [
        RUNNER + ["delivery", "--task", "84", "--expected-main-sha", SHA],
        RUNNER
        + [
            "delivery-readiness",
            "--task",
            "84",
            "--pr",
            "102",
            "--expected-base-sha",
            SHA,
            "--expected-head-sha",
            SHA,
        ],
        RUNNER
        + [
            "review",
            "--task",
            "84",
            "--pr",
            "102",
            "--expected-base-sha",
            SHA,
            "--expected-head-sha",
            SHA,
        ],
        RUNNER
        + [
            "pre-merge",
            "--task",
            "84",
            "--pr",
            "102",
            "--expected-base-sha",
            SHA,
            "--expected-head-sha",
            SHA,
        ],
        RUNNER
        + [
            "closeout-readonly",
            "--task",
            "84",
            "--pr",
            "102",
            "--expected-head-sha",
            SHA,
            "--expected-merge-sha",
            SHA,
        ],
        RUNNER + ["recheck", "--snapshot-id", "ev-0123456789abcdef"],
        [
            "tools/agent_workflow/trusted_runner.py",
            "--tool",
            "evidence-runner",
            "--trusted-sha",
            SHA,
            "--",
            "review",
            "--task",
            "84",
            "--pr",
            "102",
            "--expected-base-sha",
            SHA,
            "--expected-head-sha",
            SHA,
        ],
    ],
)
def test_fixed_evidence_profiles_are_allowed(argv: list[str]) -> None:
    assert _decision(argv) == "allow"


@pytest.mark.parametrize(
    "argv",
    [
        RUNNER + ["review", "--repository", "other/repo"],
        RUNNER + ["review", "--api", "repos/x/y"],
        RUNNER + ["review", "--graphql", "mutation"],
        RUNNER + ["review", "--gh-arg", "issue edit"],
        RUNNER + ["review", "--git-arg", "push"],
        RUNNER + ["review", "--shell", "bash"],
        RUNNER + ["review", "--exec", "id"],
        RUNNER + ["review", "--", "gh", "api"],
        RUNNER + ["review", "|", "cat"],
        RUNNER + ["review", ">", "out.json"],
        RUNNER + ["review", "$(id)"],
    ],
)
def test_known_injection_forms_are_forbidden(argv: list[str]) -> None:
    assert _decision(argv) == "forbidden"


def test_arbitrary_trailing_value_is_prefix_allowed_but_runner_owned() -> None:
    assert _decision(RUNNER + ["review", "arbitrary-value"]) == "allow"


@pytest.mark.parametrize(
    "argv",
    [
        ["gh", "api", "repos/PhoenixSss/quant-system/pulls/101"],
        ["gh", "issue", "view", "84"],
        ["gh", "pr", "view", "101"],
        ["git", "status", "--short"],
        ["git", "diff", "--check"],
        ["python3", "tools/agent_workflow/wsl2_github_evidence_runner.py", "review"],
        ["bash", "-c", "tools/agent_workflow/wsl2_github_evidence_runner.py review"],
        ["sh", "-c", "tools/agent_workflow/wsl2_github_evidence_runner.py review"],
    ],
)
def test_direct_tools_are_not_allowed(argv: list[str]) -> None:
    assert _decision(argv) != "allow"


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "add", "."],
        ["git", "commit", "-m", "x"],
        ["git", "switch", "main"],
        ["git", "fetch", "origin"],
        ["git", "push"],
        ["git", "branch", "-D", "x"],
        ["git", "reset", "--hard"],
        ["git", "clean", "-fd"],
        ["git", "merge", "x"],
        ["git", "rebase", "main"],
        ["gh", "issue", "edit", "84", "--body", "x"],
        ["gh", "pr", "comment", "101", "--body", "x"],
        ["gh", "project", "item-edit"],
    ],
)
def test_git_and_github_writes_remain_approval_gated(argv: list[str]) -> None:
    assert _decision(argv) in {"prompt", "forbidden", "unmatched"}


def test_gh_auth_token_is_forbidden() -> None:
    assert _decision(["gh", "auth", "token"]) == "forbidden"
