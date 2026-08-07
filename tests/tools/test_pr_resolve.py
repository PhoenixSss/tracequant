# ruff: noqa: E402, I001

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from pr_resolve import PrResolveError, resolve_or_create_pr  # type: ignore[import-not-found]  # noqa: E402
from workflow_common import CommandRunner  # type: ignore[import-not-found]  # noqa: E402

PYTHON = sys.executable
REPO = "PhoenixSss/tracequant"


def _write_fake_gh(bin_dir: Path, state_path: Path) -> None:
    gh = bin_dir / "gh"
    # Use {{ }} for all Python braces in the generated script;
    # only {PYTHON} and {state_path!r} are f-string interpolations.
    script = (
        f"""#!{PYTHON}\n"""
        """import json, sys\n"""
        """from pathlib import Path\n"""
        f"""state = json.loads(Path({str(state_path)!r}).read_text(encoding='utf-8'))\n"""
        """args = sys.argv[1:]\n"""
        """\n"""
        """def out(value):\n"""
        """    print(json.dumps(value, ensure_ascii=False))\n"""
        """\n"""
        """if args[:2] == ['pr', 'list']:\n"""
        """    if state.get('list_fail', False):\n"""
        """        sys.stderr.write(state.get('list_stderr', 'gh pr list failed'))\n"""
        """        sys.exit(state.get('list_exit', 1))\n"""
        """    if state.get('list_empty', False):\n"""
        """        print('')\n"""
        """        sys.exit(0)\n"""
        """    out(state.get('pr_list', []))\n"""
        """\n"""
        """elif args[:2] == ['pr', 'create']:\n"""
        """    if state.get('create_fail', False):\n"""
        """        sys.stderr.write(state.get('create_stderr', 'gh pr create failed'))\n"""
        """        sys.exit(state.get('create_exit', 1))\n"""
        """    if state.get('create_empty', False):\n"""
        """        print('')\n"""
        """        sys.exit(0)\n"""
        f"""    url = state.get('create_url', 'https://github.com/{REPO}/pull/' + str(state.get('pr_number', 111)))\n"""
        """    print(url)\n"""
        """\n"""
        """elif args[:2] == ['pr', 'view']:\n"""
        """    if state.get('view_fail', False):\n"""
        """        sys.stderr.write(state.get('view_stderr', 'gh pr view failed'))\n"""
        """        sys.exit(state.get('view_exit', 1))\n"""
        """    if state.get('view_empty', False):\n"""
        """        print('')\n"""
        """        sys.exit(0)\n"""
        """    if state.get('view_invalid_json', False):\n"""
        """        print('not json')\n"""
        """        sys.exit(0)\n"""
        f"""    pr_num = state.get('pr_number', 111)\n"""
        """    out(state.get('pr_identity', {\n"""
        """        'number': pr_num,\n"""
        f"""        'url': f'https://github.com/{REPO}/pull/{{pr_num}}',\n"""
        """        'state': 'OPEN',\n"""
        """        'isDraft': False,\n"""
        """        'baseRefName': 'main',\n"""
        """        'baseRefOid': state.get('base_sha', 'b' * 40),\n"""
        """        'headRefName': state.get('branch', 'task-110'),\n"""
        """        'headRefOid': state.get('head_sha', 'h' * 40),\n"""
        """    }))\n"""
        """\n"""
        """else:\n"""
        """    sys.stderr.write('unsupported fake gh: ' + ' '.join(args))\n"""
        """    sys.exit(1)\n"""
    )
    gh.write_text(script, encoding="utf-8")
    gh.chmod(0o755)


def _fake_git(_bin_dir: Path) -> None:
    pass


def _setup(
    tmp_path: Path, state_overrides: dict[str, Any] | None = None
) -> tuple[Path, CommandRunner, list[dict[str, Any]]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(
        ".agents/evidence.local/\n.agents/validation.local/\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    state: dict[str, Any] = {
        "pr_list": [],
        "pr_number": 111,
        "branch": "task-110",
        "base_sha": "b" * 40,
        "head_sha": "h" * 40,
    }
    if state_overrides:
        state.update(state_overrides)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_gh(bin_dir, state_path)
    _fake_git(bin_dir)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    # Update PATH for subprocess calls from CommandRunner
    os.environ["PATH"] = env["PATH"]
    runner = CommandRunner(repo)
    return repo, runner, []


# --- Resolve existing PR ---


def test_resolve_existing_single_pr(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "h" * 40,
                }
            ],
        },
    )
    result = resolve_or_create_pr(
        runner,
        REPO,
        "task-110",
        "main",
        "[Task] Test PR",
        None,
        "h" * 40,
        "b" * 40,
        warnings,
    )
    assert result["action"] == "resolved"
    assert result["number"] == 111
    assert result["state"] == "OPEN"
    assert result["head_sha"] == "h" * 40
    assert result["base_sha"] == "b" * 40
    assert result["head_branch"] == "task-110"
    assert result["base_branch"] == "main"
    assert result["is_draft"] is False


# --- Create PR ---


def test_create_when_no_pr_exists(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [],
            "create_url": f"https://github.com/{REPO}/pull/111",
        },
    )
    result = resolve_or_create_pr(
        runner,
        REPO,
        "task-110",
        "main",
        "[Task] Test PR",
        None,
        "h" * 40,
        "b" * 40,
        warnings,
    )
    assert result["action"] == "created"
    assert result["number"] == 111
    assert result["state"] == "OPEN"
    assert result["head_sha"] == "h" * 40


# --- Fail: multiple PRs ---


def test_fail_multiple_matching_prs(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "h" * 40,
                },
                {
                    "number": 112,
                    "url": f"https://github.com/{REPO}/pull/112",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "h" * 40,
                },
            ],
        },
    )
    with pytest.raises(PrResolveError, match="multiple matching PRs"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )


# --- Fail: pr list non-zero exit ---


def test_fail_pr_list_nonzero_exit(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {"list_fail": True, "list_exit": 1, "list_stderr": "gh pr list failed"},
    )
    with pytest.raises(PrResolveError, match="gh pr list failed"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )
    assert len(warnings) == 1


# --- Fail: pr list empty stdout ---


def test_fail_pr_list_empty_stdout(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(tmp_path, {"list_empty": True})
    with pytest.raises(PrResolveError, match="empty stdout"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )


# --- Fail: pr create non-zero exit ---


def test_fail_pr_create_nonzero_exit(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [],
            "create_fail": True,
            "create_exit": 1,
            "create_stderr": "gh pr create failed",
        },
    )
    with pytest.raises(PrResolveError, match="gh pr create failed"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )
    assert len(warnings) == 1


# --- Fail: pr create empty stdout ---


def test_fail_pr_create_empty_stdout(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(tmp_path, {"pr_list": [], "create_empty": True})
    with pytest.raises(PrResolveError, match="empty stdout"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )


# --- Fail: pr view non-zero exit ---


def test_fail_pr_view_nonzero_exit(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "h" * 40,
                }
            ],
            "view_fail": True,
            "view_exit": 1,
            "view_stderr": "gh pr view failed",
        },
    )
    with pytest.raises(PrResolveError, match="gh pr view"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )
    assert len(warnings) == 1


# --- Fail: pr view empty stdout ---


def test_fail_pr_view_empty_stdout(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "h" * 40,
                }
            ],
            "view_empty": True,
        },
    )
    with pytest.raises(PrResolveError, match="empty stdout"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )


# --- Fail: pr view invalid JSON ---


def test_fail_pr_view_invalid_json(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "h" * 40,
                }
            ],
            "view_invalid_json": True,
        },
    )
    with pytest.raises(PrResolveError, match="invalid JSON"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )


# --- Fail: identity mismatch - base SHA ---


def test_fail_identity_mismatch_base_sha(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "h" * 40,
                }
            ],
            "base_sha": "w" * 40,
        },
    )
    with pytest.raises(PrResolveError, match="base SHA mismatch"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            "h" * 40,
            "b" * 40,
            warnings,
        )


# --- Fail: identity mismatch - head SHA ---


def test_fail_identity_mismatch_head_sha(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "w" * 40,
                }
            ],
            # Also set head_sha so pr_identity returns mismatched value
            "head_sha": "w" * 40,
        },
    )
    with pytest.raises(PrResolveError, match="head SHA mismatch"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            "h" * 40,
            "b" * 40,
            warnings,
        )


# --- Fail: identity mismatch - branch ---


def test_fail_identity_mismatch_branch(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "other-branch",
                    "headRefOid": "h" * 40,
                }
            ],
            "branch": "other-branch",
        },
    )
    with pytest.raises(PrResolveError, match="head branch mismatch"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            "h" * 40,
            "b" * 40,
            warnings,
        )


# --- Fail: identity mismatch - draft ---


def test_fail_identity_mismatch_draft(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [
                {
                    "number": 111,
                    "url": f"https://github.com/{REPO}/pull/111",
                    "state": "OPEN",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": "task-110",
                    "headRefOid": "h" * 40,
                }
            ],
            "pr_identity": {
                "number": 111,
                "url": f"https://github.com/{REPO}/pull/111",
                "state": "OPEN",
                "isDraft": True,
                "baseRefName": "main",
                "baseRefOid": "b" * 40,
                "headRefName": "task-110",
                "headRefOid": "h" * 40,
            },
        },
    )
    with pytest.raises(PrResolveError, match="Draft"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            "h" * 40,
            "b" * 40,
            warnings,
        )


# --- PR create uses body file ---


def test_pr_create_with_body_file(tmp_path: Path) -> None:
    body_file = tmp_path / "pr_body.md"
    body_file.write_text("PR body content", encoding="utf-8")
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [],
            "create_url": f"https://github.com/{REPO}/pull/111",
        },
    )
    result = resolve_or_create_pr(
        runner,
        REPO,
        "task-110",
        "main",
        "[Task] Test PR",
        body_file,
        "h" * 40,
        "b" * 40,
        warnings,
    )
    assert result["action"] == "created"
    assert result["number"] == 111


# --- PR create with invalid URL format ---


def test_fail_pr_create_invalid_url_format(tmp_path: Path) -> None:
    repo, runner, warnings = _setup(
        tmp_path,
        {
            "pr_list": [],
            "create_url": "not a valid url",
        },
    )
    with pytest.raises(PrResolveError, match="does not contain a PR URL"):
        resolve_or_create_pr(
            runner,
            REPO,
            "task-110",
            "main",
            "[Task] Test PR",
            None,
            None,
            None,
            warnings,
        )
