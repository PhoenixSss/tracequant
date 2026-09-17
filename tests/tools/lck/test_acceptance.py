"""Repository-level acceptance tests for the restored LCK capability."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from tools.lck import cli, issue_profiles
from tools.lck import state as lck_state
from tools.lck.common import CommandResult, CommandRunner, sha256_json

from .support import FakeRunner

ROOT = Path(__file__).resolve().parents[3]
LCK_ROOT = ROOT / "tools" / "lck"
SKILLS = (
    "task-delivery-runner",
    "task-pr-review-runner",
    "task-closeout",
    "feature-completion-audit",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_restored_lck_runs_from_current_repository_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.lck", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    for command in (
        "delivery",
        "review",
        "remediation",
        "refresh",
        "merge",
        "closeout",
    ):
        assert command in result.stdout

    body = """## Critical Outcome

Caller: repository maintainer
Capability: run the restored LCK
Observable result: current Issue and profile contracts are loaded
Verification test: tests/tools/lck/test_acceptance.py::test_restored_lck_runs_from_current_repository_layout
"""
    head = "a" * 40

    class ControlledStatusRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__(branch="main")
            self.labels = ["type:task", "codex:ready"]

        def run(
            self,
            argv: list[str] | tuple[str, ...],
            *,
            command_id: str,
            **kwargs: Any,
        ) -> CommandResult:
            command = tuple(str(item) for item in argv)
            if command_id == "gh-issue-view-333":
                value: dict[str, Any] = {
                    "number": 333,
                    "title": "[Task] Restore LCK",
                    "body": body,
                    "state": "OPEN",
                    "labels": [{"name": label} for label in self.labels],
                    "comments": [],
                    "closedAt": None,
                    "closedByPullRequestsReferences": [],
                    "url": "https://github.com/owner/repo/issues/333",
                }
                return CommandResult(command_id, command, 0, json.dumps(value), "")
            if command_id == "gh-issue-project-items-333":
                project_item = {
                    "project": {
                        "number": 1,
                        "title": "Quant System Development",
                        "owner": {"login": "owner"},
                    },
                    "content": {
                        "number": 333,
                        "repository": {"nameWithOwner": "owner/repo"},
                    },
                    "fieldValues": {
                        "nodes": [
                            {
                                "__typename": "ProjectV2ItemFieldSingleSelectValue",
                                "name": "Review",
                                "field": {"name": "Status"},
                            }
                        ],
                        "pageInfo": {"hasNextPage": False},
                    },
                }
                value = {
                    "data": {
                        "repository": {
                            "issue": {
                                "number": 333,
                                "projectItems": {
                                    "nodes": [project_item],
                                    "pageInfo": {"hasNextPage": False},
                                },
                            }
                        }
                    }
                }
                return CommandResult(command_id, command, 0, json.dumps(value), "")
            if command_id == "gh-issue-closure-333":
                value = {
                    "data": {
                        "repository": {
                            "issue": {
                                "number": 333,
                                "state": "OPEN",
                                "closedAt": None,
                                "closedByPullRequestsReferences": {
                                    "nodes": [],
                                    "pageInfo": {"hasNextPage": False},
                                },
                                "timelineItems": {
                                    "nodes": [],
                                    "pageInfo": {"hasPreviousPage": False},
                                },
                            }
                        }
                    }
                }
                return CommandResult(command_id, command, 0, json.dumps(value), "")
            if command_id == "gh-issue-relationships-333":
                empty_connection = {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False},
                }
                value = {
                    "data": {
                        "repository": {
                            "issue": {
                                "number": 333,
                                "issueType": {"name": "Task"},
                                "parent": None,
                                "subIssues": empty_connection,
                                "blockedBy": empty_connection,
                                "blocking": empty_connection,
                            }
                        }
                    }
                }
                return CommandResult(command_id, command, 0, json.dumps(value), "")
            if command == ("git", "rev-parse", "refs/remotes/origin/main"):
                return CommandResult(command_id, command, 0, head, "")
            if command == ("git", "ls-remote", "origin", "refs/heads/main"):
                return CommandResult(
                    command_id, command, 0, f"{head}\trefs/heads/main\n", ""
                )
            if command[:2] in {
                ("git", "status"),
                ("git", "diff"),
            }:
                return CommandResult(command_id, command, 0, "", "")
            if command == ("git", "worktree", "list", "--porcelain"):
                return CommandResult(
                    command_id,
                    command,
                    0,
                    f"worktree {tmp_path}\nbranch refs/heads/main\n",
                    "",
                )
            return super().run(argv, command_id=command_id, **kwargs)

    fake = ControlledStatusRunner()

    def controlled_resolver(
        repo_root: Path,
        *,
        repository: str | None = None,
    ) -> lck_state.LiveStateResolver:
        return lck_state.LiveStateResolver(
            repo_root,
            runner=cast(CommandRunner, fake),
            repository=repository,
        )

    monkeypatch.setattr(cli, "LiveStateResolver", controlled_resolver)
    return_code = cli.main(
        [
            "--repo-root",
            str(tmp_path),
            "--repository",
            "owner/repo",
            "status",
            "333",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert return_code == 0
    assert payload["status"] == "resolved"
    assert payload["issue_profile"]["profile"]["profile_id"] == "task"
    assert payload["leaf_contract"]["number"] == 333
    assert payload["leaf_contract"]["body_sha256"] == sha256_json({"body": body})

    fake.labels = []
    return_code = cli.main(
        [
            "--repo-root",
            str(tmp_path),
            "--repository",
            "owner/repo",
            "status",
            "333",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert return_code == 2
    assert payload["status"] == "stop"
    assert payload["issue_profile"]["terminal_status"] == "MISSING_TYPE"

    assert Path(cli.__file__).resolve().is_relative_to(LCK_ROOT)
    assert not (ROOT / "tools" / "agent_workflow").exists()
    assert not (ROOT / "src" / "tracequant" / "contracts" / "review.py").exists()


def test_restored_lck_is_separate_from_product_code_and_distribution() -> None:
    for path in LCK_ROOT.glob("*.py"):
        imported = _imports(path)
        assert not any(
            name == "tracequant"
            or name.startswith("tracequant.")
            or name == "nautilus_trader"
            or name.startswith("nautilus_trader.")
            for name in imported
        ), path

    product_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "tracequant").rglob("*.py")
    )
    assert "tools.lck" not in product_source
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'module-root = "src"' in pyproject
    assert 'module-name = "tracequant"' in pyproject


def test_lck_exposes_the_complete_lifecycle_parser() -> None:
    parser = cli._build_parser()
    commands = (
        ("delivery", "prepare", "333"),
        (
            "delivery",
            "complete",
            "333",
            "--commit-message",
            "m",
            "--summary",
            "s",
        ),
        ("review", "prepare", "333"),
        (
            "review",
            "complete",
            "333",
            "--review-id",
            "r",
            "--verdict",
            "PASS",
        ),
        ("remediation", "prepare", "333", "--review-id", "r"),
        ("refresh", "333"),
        ("merge", "preflight", "333"),
        ("closeout", "333"),
    )
    for argv in commands:
        assert parser.parse_args(argv).command == argv[0]


def test_typed_leaf_profiles_share_the_restored_kernel() -> None:
    profiles = issue_profiles.PROFILES_BY_TYPE_LABEL
    assert set(profiles) == {
        "type:task",
        "type:bug",
        "type:documentation",
        "type:research",
    }
    assert all(profile.lifecycle_enabled for profile in profiles.values())
    assert len({profile.profile_id for profile in profiles.values()}) == 4


def test_agent_assets_have_one_canonical_source_and_thin_adapters() -> None:
    for skill in SKILLS:
        canonical = ROOT / ".agents" / "skills" / skill / "SKILL.md"
        adapter = ROOT / ".claude" / "skills" / skill / "SKILL.md"
        assert canonical.is_file()
        adapter_text = adapter.read_text(encoding="utf-8")
        assert canonical.relative_to(ROOT).as_posix() in adapter_text
        assert len(adapter_text.splitlines()) < 15
        assert "uv run --frozen python -m tools.lck" in canonical.read_text(
            encoding="utf-8"
        )


def test_restoration_manifest_records_source_objects_and_decisions() -> None:
    manifest = (ROOT / "docs/workflows/lck/restoration-manifest.md").read_text(
        encoding="utf-8"
    )
    assert "0d9d762238a3e837a864b5350bf16d1b885a73c3" in manifest
    assert "tools/lck/" in manifest
    assert "tests/tools/lck/" in manifest
    assert "docs/workflows/lck/" in manifest
    assert "restored/adapted" in manifest
    assert "omitted" in manifest

    # These identities were captured from the authoritative source during the
    # restoration.  Keep the acceptance check usable in a default shallow CI
    # checkout, where that historical commit is intentionally unavailable.
    source_configuration = {
        ".github/ISSUE_TEMPLATE/bug.yml": "5ae205ca966e9505119c00f8eaf6f4ebb5e1eac5",
        ".github/ISSUE_TEMPLATE/config.yml": "8005e3226730ef74f37ae9614ba94a1bb879b4a0",
        ".github/ISSUE_TEMPLATE/documentation.yml": "d337abab2e720c3f84a628a73e075aef096e3c17",
        ".github/ISSUE_TEMPLATE/epic.yml": "ff3a2e739a3ae0300db0d15e532da360aca9c72b",
        ".github/ISSUE_TEMPLATE/feature.yml": "8c2e55aad1b2305b5db1f249328fb705e59bc13c",
        ".github/ISSUE_TEMPLATE/research.yml": "cdb61d2b5a26e8508bd6135e201d45a71131037e",
        ".github/ISSUE_TEMPLATE/task.yml": "ca7d13fea03ad2d5c8cd512f6db6297f8888e87d",
        ".github/pull_request_template.md": "d9620d336755c740700d8fa68c10f3c3917d5176",
    }
    for path, blob in source_configuration.items():
        assert f"| `{path}` | `{blob}` |" in manifest


def test_feature_audit_names_the_stable_lck_entrypoint() -> None:
    module_text = (LCK_ROOT / "feature_audit.py").read_text(encoding="utf-8")
    module_docstring = ast.get_docstring(ast.parse(module_text))
    assert module_docstring is not None
    assert "uv run --frozen python -m tools.lck" in module_docstring
    assert "lck.py" not in module_docstring


def test_implementation_map_test_paths_exist() -> None:
    implementation_map = (ROOT / "docs/workflows/lck/implementation-map.md").read_text(
        encoding="utf-8"
    )
    mapped_tests = re.findall(r"^- `([^`]+\.py)`", implementation_map, re.MULTILINE)
    assert mapped_tests
    missing = [
        name for name in mapped_tests if not (ROOT / "tests/tools/lck" / name).is_file()
    ]
    assert missing == []
