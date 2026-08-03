from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
RUNNER_REL = Path("tools/agent_workflow/wsl2_github_evidence_runner.py")
TRUSTED_FILES = (
    RUNNER_REL,
    Path("tools/agent_workflow/wsl2_github_evidence_profiles.json"),
    Path(".codex/rules/quant-system-wsl-evidence.rules"),
    Path("tools/agent_workflow/workflow_evidence.py"),
    Path("tools/agent_workflow/workflow_common.py"),
)
REAL_GIT_OPTIONAL = shutil.which("git")
assert REAL_GIT_OPTIONAL is not None
REAL_GIT: str = REAL_GIT_OPTIONAL
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [REAL_GIT, *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _state(main_sha: str, head_sha: str) -> dict[str, Any]:
    return {
        "issue": {
            "number": 84,
            "title": "[Task] Evidence runner",
            "body": "fixed read-only evidence",
            "comments": [],
            "state": "OPEN",
            "labels": [{"name": "type:task"}, {"name": "codex:ready"}],
            "projectItems": [{"status": {"name": "Review"}}],
            "url": "https://github.com/PhoenixSss/quant-system/issues/84",
            "closedAt": None,
            "closedByPullRequestsReferences": [],
        },
        "relationships": {
            "number": 84,
            "title": "[Task] Evidence runner",
            "state": "OPEN",
            "issueType": {"name": "Task"},
            "parent": {"number": 77, "title": "[Feature] WSL2", "state": "OPEN"},
            "subIssues": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            "blockedBy": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            "blocking": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        },
        "pr": {
            "number": 102,
            "title": "[Task] Evidence runner",
            "body": "Closes #84",
            "state": "OPEN",
            "isDraft": False,
            "url": "https://github.com/PhoenixSss/quant-system/pull/102",
            "baseRefName": "main",
            "baseRefOid": main_sha,
            "headRefName": "84-task-evidence-runner",
            "headRefOid": head_sha,
            "mergeCommit": None,
            "mergedAt": None,
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
            "files": [
                {"path": "tools/agent_workflow/wsl2_github_evidence_runner.py"},
                {"path": ".codex/rules/quant-system-wsl-evidence.rules"},
            ],
            "commits": [{"oid": head_sha, "messageHeadline": "Implement Task #84"}],
            "statusCheckRollup": [{"name": "quality", "conclusion": "SUCCESS"}],
            "reviews": [],
            "closingIssuesReferences": [{"number": 84}],
        },
        "threads": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "required_checks_mode": "available",
        "required_checks": {"contexts": ["quality"]},
        "diff": "diff --git a/a b/a\n+read only\n",
        "remote_refs": {
            "main": main_sha,
            "84-task-evidence-runner": head_sha,
        },
        "remote_error": None,
        "issue_error": None,
    }


def _write_fake_tools(bin_dir: Path, state_path: Path) -> None:
    bin_dir.mkdir(parents=True)
    git_wrapper = bin_dir / "git"
    refs_path = state_path.with_name("remote-refs.txt")
    error_path = state_path.with_name("remote-error.txt")
    calls_path = state_path.with_name("git-calls.txt")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    refs_path.write_text(
        "".join(
            f"{sha}\trefs/heads/{name}\n" for name, sha in state["remote_refs"].items()
        ),
        encoding="utf-8",
    )
    error_path.write_text("", encoding="utf-8")
    git_wrapper.write_text(
        f"""#!/bin/sh
printf '%s\n' "$*" >> {str(calls_path)!r}
if [ "$1" = "ls-remote" ] && [ "$2" = "--heads" ] && [ "$3" = "origin" ]; then
  if [ -s {str(error_path)!r} ]; then
    cat {str(error_path)!r} >&2
    exit 1
  fi
  shift 3
  while [ "$#" -gt 0 ]; do
    grep "refs/heads/$1$" {str(refs_path)!r} || true
    shift
  done
  exit 0
fi
exec {REAL_GIT!r} "$@"
""",
        encoding="utf-8",
        newline="\n",
    )
    git_wrapper.chmod(0o755)

    gh = bin_dir / "gh"
    gh.write_text(
        f"""#!{PYTHON}
import json, sys
from pathlib import Path
state_path=Path({str(state_path)!r})
log_path=state_path.with_name('gh-calls.jsonl')
args=sys.argv[1:]
with log_path.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps(args)+'\\n')
state=json.loads(state_path.read_text(encoding='utf-8'))
def dump(value):
    print(json.dumps(value, ensure_ascii=False))
if args == ['--version']:
    print('gh version 2.94.0 (fake)')
elif args[:2] == ['issue','view']:
    if state.get('issue_error'):
        sys.stderr.write(str(state['issue_error']))
        sys.exit(1)
    dump(state['issue'])
elif args[:2] == ['pr','view']:
    dump(state['pr'])
elif args[:2] == ['pr','diff']:
    sys.stdout.write(state.get('diff',''))
elif args[:2] == ['api','graphql']:
    joined=' '.join(args)
    if 'reviewThreads' in joined:
        dump({{'data':{{'repository':{{'pullRequest':{{
            'reviewThreads':state['threads']
        }}}}}}}})
    else:
        dump({{'data':{{'repository':{{'issue':state['relationships']}}}}}})
elif (
    args
    and args[0] == 'api'
    and len(args) > 1
    and 'required_status_checks' in args[1]
):
    mode=state.get('required_checks_mode')
    if mode == '403':
        sys.stderr.write('HTTP 403 Resource not accessible by integration')
        sys.exit(1)
    if mode == 'auth-403':
        sys.stderr.write('HTTP 403 Requires authentication')
        sys.exit(1)
    if mode == 'scope-403':
        sys.stderr.write('HTTP 403 missing required scope')
        sys.exit(1)
    if mode == 'permission-403':
        sys.stderr.write('HTTP 403 Forbidden')
        sys.exit(1)
    if mode == 'network':
        sys.stderr.write('proxyconnect tcp: network failure')
        sys.exit(1)
    if mode == '404':
        sys.stderr.write('HTTP 404 Not Found')
        sys.exit(1)
    if mode == 'rate-limit':
        sys.stderr.write(
            'HTTP 429 rate limit token='
            'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456'
        )
        sys.exit(1)
    dump(state['required_checks'])
else:
    sys.stderr.write('unsupported fake gh: '+' '.join(args))
    sys.exit(1)
""",
        encoding="utf-8",
        newline="\n",
    )
    gh.chmod(0o755)


def _prepare_repo(
    tmp_path: Path, *, with_space: bool = False
) -> tuple[Path, Path, dict[str, str], str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / ("repo with space" if with_space else "repo")
    repo.mkdir()
    for relative in TRUSTED_FILES:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    (repo / ".gitignore").write_text(
        ".agents/evidence.local/\n.agents/validation.local/\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Task84 Tests")
    _git(repo, "config", "user.email", "task84@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "main baseline")
    _git(repo, "branch", "-M", "main")
    main_sha = _git(repo, "rev-parse", "HEAD")
    _git(
        repo,
        "remote",
        "add",
        "origin",
        "https://github.com/PhoenixSss/quant-system.git",
    )
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "switch", "-q", "-c", "84-task-evidence-runner")
    (repo / "task84.txt").write_text("task 84\n", encoding="utf-8")
    _git(repo, "add", "task84.txt")
    _git(repo, "commit", "-q", "-m", "task head")
    head_sha = _git(repo, "rev-parse", "HEAD")

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_state(main_sha, head_sha)), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    _write_fake_tools(bin_dir, state_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return repo, state_path, env, main_sha, head_sha


def _run(
    repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo / RUNNER_REL), *args],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _review_args(main_sha: str, head_sha: str, profile: str = "review") -> list[str]:
    return [
        profile,
        "--task",
        "84",
        "--pr",
        "102",
        "--expected-base-sha",
        main_sha,
        "--expected-head-sha",
        head_sha,
    ]


def _calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if path.suffix == ".txt":
        return [line.split() for line in lines]
    return [json.loads(line) for line in lines]


def _sync_remote_files(state_path: Path, state: dict[str, Any]) -> None:
    state_path.with_name("remote-refs.txt").write_text(
        "".join(
            f"{sha}\trefs/heads/{name}\n"
            for name, sha in state.get("remote_refs", {}).items()
        ),
        encoding="utf-8",
    )
    state_path.with_name("remote-error.txt").write_text(
        str(state.get("remote_error") or ""), encoding="utf-8"
    )


def _result(repo: Path, stdout: str) -> dict[str, Any]:
    digest = json.loads(stdout)
    result = json.loads((repo / digest["result_path"]).read_text(encoding="utf-8"))
    assert isinstance(result, dict)
    return result


def test_review_profile_returns_required_schema_and_compact_digest(
    tmp_path: Path,
) -> None:
    repo, _, env, main_sha, head_sha = _prepare_repo(tmp_path)
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 0, completed.stderr
    digest = json.loads(completed.stdout)
    assert digest["status"] == "pass"
    assert digest["profile"] == "review"
    assert digest["task"] == 84
    assert digest["pr"] == 102
    value = _result(repo, completed.stdout)
    assert value["identity"] == {
        "task": 84,
        "pr": 102,
        "repository": "PhoenixSss/quant-system",
        "base_sha": main_sha,
        "head_sha": head_sha,
        "merge_sha": None,
    }
    assert value["issue"]["project_status"] == "Review"
    assert value["pull_request"]["checks"]["all_success"] is True
    assert value["pull_request"]["unresolved_threads"] == 0
    assert value["scope"]["diff_digest"]
    assert value["git"]["local_main"] == main_sha
    assert value["git"]["origin_main"] == main_sha
    assert value["git"]["remote_main"] == main_sha
    assert value["git"]["remote_head"] == head_sha
    assert value["git"]["origin_refresh"] == "skipped-read-only"
    assert value["stability"]["snapshot_id"].startswith("ev-")
    assert value["integrity"]["verification"] == "tracked-head-pre-execution"


@pytest.mark.parametrize("profile", ["delivery-readiness", "review", "pre-merge"])
def test_task_pr_profiles_are_fixed_and_pass(tmp_path: Path, profile: str) -> None:
    repo, _, env, main_sha, head_sha = _prepare_repo(tmp_path)
    completed = _run(repo, env, *_review_args(main_sha, head_sha, profile))
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["profile"] == profile


def test_delivery_profile_is_task_only_and_read_only(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, _ = _prepare_repo(tmp_path)
    completed = _run(
        repo,
        env,
        "delivery",
        "--task",
        "84",
        "--expected-main-sha",
        main_sha,
    )
    assert completed.returncode == 0, completed.stderr
    value = _result(repo, completed.stdout)
    assert value["identity"]["pr"] is None
    assert value["status"] == "pass"
    git_calls = _calls(state_path.with_name("git-calls.txt"))
    assert not any(call[:1] == ["fetch"] for call in git_calls)


def test_plan_limit_is_partial_not_success(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["required_checks_mode"] = "403"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 3, completed.stderr
    digest = json.loads(completed.stdout)
    assert digest["status"] == "partial"
    value = _result(repo, completed.stdout)
    assert (
        "required_checks_configuration"
        in value["evidence"]["gate_summary"]["unknown_gates"]
    )


def test_missing_issue_and_rate_limit_are_partial_and_redacted(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["issue_error"] = "HTTP 429 token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    state["required_checks_mode"] = "rate-limit"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 3, completed.stderr
    all_evidence = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (repo / ".agents/evidence.local").rglob("*")
        if path.is_file()
    )
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in all_evidence
    assert "<redacted>" in all_evidence


def test_task_pr_linkage_failure_is_fail(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pr"]["closingIssuesReferences"] = []
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 4
    value = _result(repo, completed.stdout)
    assert "closing_linkage" in value["evidence"]["gate_summary"]["failed_gates"]


def test_remote_ref_drift_is_fail(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["remote_refs"]["main"] = "f" * 40
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 4
    value = _result(repo, completed.stdout)
    assert (
        "origin_main_vs_remote_main"
        in value["evidence"]["gate_summary"]["remote_ref_conflicts"]
    )


def test_remote_query_failure_is_partial(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["remote_error"] = "network unavailable"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 3
    assert json.loads(completed.stdout)["partial"] is True


def test_recheck_detects_head_checks_and_thread_drift(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    first = _run(repo, env, *_review_args(main_sha, head_sha))
    assert first.returncode == 0, first.stderr
    first_digest = json.loads(first.stdout)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    new_head = "e" * 40
    state["pr"]["headRefOid"] = new_head
    state["pr"]["statusCheckRollup"] = [{"name": "quality", "conclusion": "FAILURE"}]
    state["threads"]["nodes"] = [
        {"isResolved": False, "isOutdated": False, "comments": {"totalCount": 1}}
    ]
    state["remote_refs"]["84-task-evidence-runner"] = new_head
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    second = _run(repo, env, "recheck", "--snapshot-id", first_digest["snapshot_id"])
    assert second.returncode == 4, second.stderr
    value = _result(repo, second.stdout)
    changed = value["stability"]["changed_fields"]["items"]
    assert "head_sha" in changed
    assert "checks" in changed
    assert "unresolved_threads" in changed
    assert "snapshot_stability" in value["evidence"]["gate_summary"]["failed_gates"]


def test_tampered_recheck_snapshot_fails_before_github(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    first = _run(repo, env, *_review_args(main_sha, head_sha))
    assert first.returncode == 0, first.stderr
    digest = json.loads(first.stdout)
    snapshot = (
        repo / ".agents/evidence.local/snapshots" / f"{digest['snapshot_id']}.json"
    )
    value = json.loads(snapshot.read_text(encoding="utf-8"))
    value["repository"] = "other/repo"
    snapshot.write_text(json.dumps(value), encoding="utf-8")
    before_calls = len(_calls(state_path.with_name("gh-calls.jsonl")))
    completed = _run(repo, env, "recheck", "--snapshot-id", digest["snapshot_id"])
    assert completed.returncode == 2
    assert "repository is not allowed" in completed.stderr
    assert len(_calls(state_path.with_name("gh-calls.jsonl"))) == before_calls


def test_invalid_recheck_does_not_create_run_directory(tmp_path: Path) -> None:
    repo, _, env, _, _ = _prepare_repo(tmp_path)
    completed = _run(repo, env, "recheck", "--snapshot-id", "ev-0000000000000000")
    assert completed.returncode == 2
    root = repo / ".agents/evidence.local/wsl2-github-runs"
    assert not root.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["unexpected"],
        ["--repository", "other/repo"],
        ["--api", "repos/x/y"],
        ["--shell", "bash"],
        ["--task", "84;rm -rf /"],
    ],
)
def test_unknown_or_injected_arguments_fail_before_subprocess(
    tmp_path: Path, extra: list[str]
) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    completed = _run(repo, env, *_review_args(main_sha, head_sha), *extra)
    assert completed.returncode == 2
    assert _calls(state_path.with_name("gh-calls.jsonl")) == []
    assert not (repo / ".agents/evidence.local/wsl2-github-runs").exists()


def test_trusted_file_mutation_fails_before_github(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    spec = repo / "tools/agent_workflow/wsl2_github_evidence_profiles.json"
    spec.write_text(spec.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 2
    assert "differs from HEAD" in completed.stderr
    assert _calls(state_path.with_name("gh-calls.jsonl")) == []


def test_wrong_origin_and_symlink_entry_fail_closed(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    _git(repo, "remote", "set-url", "origin", "https://github.com/other/repo.git")
    wrong = _run(repo, env, *_review_args(main_sha, head_sha))
    assert wrong.returncode == 2
    assert "origin repository is not allowed" in wrong.stderr
    assert _calls(state_path.with_name("gh-calls.jsonl")) == []

    _git(
        repo,
        "remote",
        "set-url",
        "origin",
        "https://github.com/PhoenixSss/quant-system.git",
    )
    link = repo / "evidence-link"
    link.symlink_to(repo / RUNNER_REL)
    linked = subprocess.run(
        [str(link), *_review_args(main_sha, head_sha)],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert linked.returncode == 2
    assert "symlink" in linked.stderr


def test_repository_path_with_spaces_is_supported(tmp_path: Path) -> None:
    repo, _, env, main_sha, head_sha = _prepare_repo(tmp_path, with_space=True)
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 0, completed.stderr


def test_changed_file_truncation_is_partial(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["pr"]["files"] = [{"path": f"file-{index}.py"} for index in range(120)]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    completed = _run(repo, env, *_review_args(main_sha, head_sha))
    assert completed.returncode == 3
    value = _result(repo, completed.stdout)
    assert value["scope"]["changed_files"]["truncated"] is True


def test_concurrent_safe_run_ids_and_no_github_writes(tmp_path: Path) -> None:
    repo, state_path, env, main_sha, head_sha = _prepare_repo(tmp_path)
    first = _run(repo, env, *_review_args(main_sha, head_sha))
    second = _run(repo, env, *_review_args(main_sha, head_sha))
    assert first.returncode == second.returncode == 0
    first_digest = json.loads(first.stdout)
    second_digest = json.loads(second.stdout)
    assert first_digest["result_path"] != second_digest["result_path"]
    gh_calls = _calls(state_path.with_name("gh-calls.jsonl"))
    mutation_tokens = {"edit", "close", "comment", "merge", "delete", "create"}
    assert all(not mutation_tokens.intersection(call) for call in gh_calls)
    read_prefixes = (
        ["issue", "view"],
        ["pr", "view"],
        ["pr", "diff"],
        ["api", "graphql"],
    )
    assert all(
        call[:2] in read_prefixes
        or (
            call
            and call[0] == "api"
            and len(call) > 1
            and "required_status_checks" in call[1]
        )
        for call in gh_calls
    )


def test_closeout_readonly_profile(tmp_path: Path) -> None:
    repo, state_path, env, _main_sha, head_sha = _prepare_repo(tmp_path)
    _git(repo, "switch", "-q", "main")
    _git(repo, "merge", "--ff-only", "84-task-evidence-runner")
    merge_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", merge_sha)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["issue"]["state"] = "CLOSED"
    state["issue"]["projectItems"] = [{"status": {"name": "Done"}}]
    state["issue"]["closedAt"] = "2026-08-02T16:00:00Z"
    state["issue"]["closedByPullRequestsReferences"] = [
        {
            "number": 102,
            "state": "MERGED",
            "mergedAt": "2026-08-02T16:00:00Z",
            "url": "https://github.com/PhoenixSss/quant-system/pull/102",
        }
    ]
    state["pr"].update(
        state="MERGED",
        mergeCommit={"oid": merge_sha},
        mergedAt="2026-08-02T16:00:00Z",
        mergeable="UNKNOWN",
    )
    state["remote_refs"]["main"] = merge_sha
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    completed = _run(
        repo,
        env,
        "closeout-readonly",
        "--task",
        "84",
        "--pr",
        "102",
        "--expected-head-sha",
        head_sha,
        "--expected-merge-sha",
        merge_sha,
    )
    assert completed.returncode == 0, completed.stderr
    value = _result(repo, completed.stdout)
    assert value["identity"]["merge_sha"] == merge_sha
    assert value["issue"]["state"] == "CLOSED"
    assert value["git"]["current_branch"] == "main"


def test_closeout_plan_limit_cleanup_eligibility_digest_remains_partial(
    tmp_path: Path,
) -> None:
    repo, state_path, env, _main_sha, head_sha = _prepare_repo(tmp_path)
    _git(repo, "switch", "-q", "main")
    _git(repo, "merge", "--ff-only", "84-task-evidence-runner")
    merge_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", merge_sha)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["required_checks_mode"] = "403"
    state["issue"]["state"] = "CLOSED"
    state["issue"]["projectItems"] = [{"status": {"name": "Done"}}]
    state["issue"]["closedAt"] = "2026-08-02T16:00:00Z"
    state["issue"]["closedByPullRequestsReferences"] = [
        {
            "number": 102,
            "state": "MERGED",
            "mergedAt": "2026-08-02T16:00:00Z",
            "url": "https://github.com/PhoenixSss/quant-system/pull/102",
        }
    ]
    state["pr"].update(
        state="MERGED",
        mergeCommit={"oid": merge_sha},
        mergedAt="2026-08-02T16:00:00Z",
        mergeable="UNKNOWN",
    )
    state["remote_refs"]["main"] = merge_sha
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _sync_remote_files(state_path, state)
    completed = _run(
        repo,
        env,
        "closeout-readonly",
        "--task",
        "84",
        "--pr",
        "102",
        "--expected-head-sha",
        head_sha,
        "--expected-merge-sha",
        merge_sha,
    )
    assert completed.returncode == 3, completed.stderr
    digest = json.loads(completed.stdout)
    assert digest["status"] == "partial"
    value = _result(repo, completed.stdout)
    assert (
        "required_checks_configuration"
        in value["evidence"]["gate_summary"]["unknown_gates"]
    )
    assert value["checks"]["required_configuration"] == "plan-limited-403"
    eligibility = value["branch_cleanup"]["cleanup_eligibility"]
    assert eligibility["status"] == "eligible-under-capability-limited-policy"
    assert eligibility["limitation_preserved"] is True
    assert eligibility["allowed_scope"] == "exact-task-branch-cleanup-only"


def test_required_checks_403_failure_types_do_not_enable_cleanup(
    tmp_path: Path,
) -> None:
    for index, (mode, reason) in enumerate(
        (
            ("auth-403", "github-authentication-failure"),
            ("scope-403", "github-scope-or-sso-403"),
            ("permission-403", "github-permission-403"),
            ("network", "network-failure"),
        )
    ):
        repo, state_path, env, _main_sha, head_sha = _prepare_repo(
            tmp_path / str(index)
        )
        _git(repo, "switch", "-q", "main")
        _git(repo, "merge", "--ff-only", "84-task-evidence-runner")
        merge_sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "update-ref", "refs/remotes/origin/main", merge_sha)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["required_checks_mode"] = mode
        state["issue"]["state"] = "CLOSED"
        state["issue"]["projectItems"] = [{"status": {"name": "Done"}}]
        state["issue"]["closedByPullRequestsReferences"] = [
            {"number": 102, "state": "MERGED", "mergedAt": "2026-08-02T16:00:00Z"}
        ]
        state["pr"].update(
            state="MERGED",
            mergeCommit={"oid": merge_sha},
            mergedAt="2026-08-02T16:00:00Z",
            mergeable="UNKNOWN",
        )
        state["remote_refs"]["main"] = merge_sha
        state_path.write_text(json.dumps(state), encoding="utf-8")
        _sync_remote_files(state_path, state)
        completed = _run(
            repo,
            env,
            "closeout-readonly",
            "--task",
            "84",
            "--pr",
            "102",
            "--expected-head-sha",
            head_sha,
            "--expected-merge-sha",
            merge_sha,
        )
        assert completed.returncode == 3, completed.stderr
        value = _result(repo, completed.stdout)
        assert value["checks"]["required_configuration"] == "unknown"
        assert value["checks"]["required_failure"]["reason"] == reason
        assert value["branch_cleanup"]["cleanup_eligibility"]["status"] == "blocked"


@pytest.mark.skipif(
    os.environ.get("TASK84_LIVE_REPOSITORY") != "PhoenixSss/quant-system",
    reason="set TASK84_LIVE_REPOSITORY and live IDs for an explicit network probe",
)
def test_live_task_pr_schema() -> None:
    task = os.environ["TASK84_LIVE_TASK"]
    pr = os.environ["TASK84_LIVE_PR"]
    base = os.environ["TASK84_LIVE_BASE_SHA"]
    head = os.environ["TASK84_LIVE_HEAD_SHA"]
    completed = subprocess.run(
        [
            str(ROOT / RUNNER_REL),
            "review",
            "--task",
            task,
            "--pr",
            pr,
            "--expected-base-sha",
            base,
            "--expected-head-sha",
            head,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
    )
    assert task == "84"
    assert pr.isdigit()
    assert completed.returncode in {0, 3}, completed.stderr
    assert json.loads(completed.stdout)["task"] == 84


def test_trusted_runner_executes_real_evidence_front_door(tmp_path: Path) -> None:
    repo, _, env, main_sha, head_sha = _prepare_repo(tmp_path)
    trusted_runner = ROOT / "tools/agent_workflow/trusted_runner.py"
    completed = subprocess.run(
        [
            PYTHON,
            str(trusted_runner),
            "--trusted-sha",
            main_sha,
            "--tool",
            "evidence-runner",
            "--",
            *_review_args(main_sha, head_sha),
        ],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    digest = json.loads(completed.stdout)
    stored = json.loads((repo / digest["result_path"]).read_text(encoding="utf-8"))
    assert stored["integrity"]["verification"] == (
        "trusted-commit-bundle-pre-execution"
    )
    assert digest["base_sha"] == main_sha
    assert digest["head_sha"] == head_sha
