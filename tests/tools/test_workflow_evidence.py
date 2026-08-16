from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

SCRIPT = Path(__file__).parents[2] / "tools" / "agent_workflow" / "workflow_evidence.py"
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)


def _write_fake_tools(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def add_windows_launcher(name: str) -> None:
        if os.name != "nt":
            return
        launcher = bin_dir / f"{name}.cmd"
        launcher.write_text(
            f'@echo off\r\n"{PYTHON}" "{bin_dir / name}" %*\r\n',
            encoding="utf-8",
        )

    git_script = bin_dir / "git"
    git_script.write_text(
        f"""#!{PYTHON}
import json, os, sys
from pathlib import Path
state=json.loads(Path(os.environ['FAKE_WORKFLOW_STATE']).read_text(encoding='utf-8'))
args=sys.argv[1:]
def out(value=''):
    sys.stdout.write(str(value))
    if value and not str(value).endswith('\\n'): sys.stdout.write('\\n')
if args[:2] == ['fetch','--prune']:
    if not state.get('fetch_ok', True):
        sys.stderr.write(r'failed at C:/Users/Maple/repo and /home/maple/repo')
        sys.exit(1)
    sys.exit(0)
elif args[:3] == ['remote','get-url','origin']: out('https://github.com/owner/repo.git')
elif args[:2] == ['branch','--show-current']: out(state.get('branch','main'))
elif args[:2] == ['rev-parse','HEAD']: out(state.get('git_head','a'*40))
elif args[:2] == ['rev-parse','refs/heads/main']: out(state.get('local_main',state.get('origin_main','b'*40)))
elif args[:2] == ['rev-parse','refs/remotes/origin/main']: out(state.get('origin_main','b'*40))
elif args[:1] == ['rev-parse'] and args[1].startswith('refs/heads/'):
    branch=args[1].removeprefix('refs/heads/')
    out(state.get('local_branch_tips',{{}}).get(branch, state.get('pr',{{}}).get('headRefOid','d'*40)))
elif args[:2] == ['status','--short']: out('\\n'.join(state.get('status',[])))
elif args[:3] == ['diff','--cached','--name-only']: out('\\n'.join(state.get('staged',[])))
elif args[:2] == ['diff','--name-only']: out('\\n'.join(state.get('changed',[])))
elif args[:3] == ['worktree','list','--porcelain']:
    lines=['worktree <repo>','HEAD '+state.get('git_head','a'*40),'branch refs/heads/main']
    for branch in state.get('extra_worktree_branches',[]):
        lines.extend(['worktree <repo-'+branch+'>','HEAD '+state.get('pr',{{}}).get('headRefOid','d'*40),'branch refs/heads/'+branch])
    out('\\n'.join(lines))
elif args[:3] == ['log','-1','--format=%H']: out(state.get('runner_source_sha','c'*40))
elif args[:2] == ['ls-remote','--heads']:
    if state.get('remote_branch_exists',True): out(state.get('pr',{{}}).get('headRefOid','d'*40)+'\\trefs/heads/'+args[-1])
elif args[:3] == ['show-ref','--verify','--quiet']:
    sys.exit(0 if state.get('local_branch_exists',True) else 1)
elif args[:1] == ['merge-base']:
    out(state.get('branch_base', state.get('origin_main','b'*40)))
elif args[:2] == ['merge-base','--is-ancestor']:
    sys.exit(0 if state.get('merge_on_main', True) else 1)
elif args[:2] == ['diff','--quiet']:
    sys.exit(0 if state.get('tree_equal', True) else 1)
elif args[:2] == ['diff','--check']: sys.exit(0)
else:
    sys.stderr.write('unsupported fake git: '+' '.join(args))
    sys.exit(1)
""",
        encoding="utf-8",
    )
    gh_script = bin_dir / "gh"
    gh_script.write_text(
        f"""#!{PYTHON}
import json, os, sys
from pathlib import Path
state=json.loads(Path(os.environ['FAKE_WORKFLOW_STATE']).read_text(encoding='utf-8'))
args=sys.argv[1:]
def dump(value): print(json.dumps(value, ensure_ascii=False))
if args[:2] == ['repo','view']:
    dump({{'nameWithOwner':'owner/repo'}})
elif args[:2] == ['issue','view']:
    number=args[2]
    issue=state.get('issues',{{}}).get(number)
    if issue is None: sys.stderr.write('404 issue'); sys.exit(1)
    dump(issue)
elif args[:2] == ['pr','view']:
    number=args[2]
    dump(state.get('prs',{{}}).get(number, state['pr']))
elif args[:2] == ['pr','list']:
    dump(state.get('active_prs',[]))
elif args[:2] == ['pr','diff']:
    sys.stdout.write(state.get('diff','diff --git a/a b/a\\n'))
elif args[:2] == ['api','graphql']:
    query=' '.join(args)
    if 'pullRequest' in query and 'reviewThreads' in query:
        dump({{'data':{{'repository':{{'pullRequest':{{'reviewThreads':state.get('threads',{{'nodes':[],'pageInfo':{{'hasNextPage':False}}}})}}}}}}}})
    elif 'timelineItems' in query:
        number_value=None
        for arg in args:
            if arg.startswith('number='):
                number_value=arg.split('=',1)[1]
        issue=state.get('issues',{{}}).get(number_value)
        dump({{'data':{{'repository':{{'issue':issue}}}}}})
    else:
        number_value=None
        for arg in args:
            if arg.startswith('number='):
                number_value=arg.split('=',1)[1]
        relation=state.get('relationships_by_issue',{{}}).get(number_value, state.get('relationships'))
        dump({{'data':{{'repository':{{'issue':relation}}}}}})
elif args and args[0] == 'api' and 'required_status_checks' in args[1]:
    mode=state.get('required_checks_mode','available')
    if mode == 'plan-limit-403':
        sys.stderr.write('HTTP 403 Branch protection for private repositories is not included in this GitHub plan')
        sys.exit(1)
    if mode == '403':
        sys.stderr.write('HTTP 403 Resource not accessible by integration')
        sys.exit(1)
    if mode == '404': sys.stderr.write('HTTP 404 Not Found'); sys.exit(1)
    dump(state.get('required_checks',{{'contexts':['CI']}}))
else:
    sys.stderr.write('unsupported fake gh: '+' '.join(args))
    sys.exit(1)
""",
        encoding="utf-8",
    )
    git_script.chmod(0o755)
    gh_script.chmod(0o755)
    add_windows_launcher("git")
    add_windows_launcher("gh")
    return bin_dir


def _base_state() -> dict[str, Any]:
    task_issue = {
        "number": 70,
        "title": "[Task] Optimize workflow",
        "state": "OPEN",
        "labels": [{"name": "type:task"}, {"name": "codex:ready"}],
        "projectItems": [{"status": {"name": "Ready"}}],
        "url": "https://github.com/owner/repo/issues/70",
        "closedAt": None,
        "closedByPullRequestsReferences": [],
        "timelineItems": {"nodes": [], "pageInfo": {"hasPreviousPage": False}},
    }
    child_issue = {
        "number": 63,
        "title": "[Task] Child",
        "state": "CLOSED",
        "labels": [{"name": "type:task"}, {"name": "codex:ready"}],
        "projectItems": [{"status": {"name": "Done"}}],
        "url": "https://github.com/owner/repo/issues/63",
        "closedAt": "2026-07-26T00:00:00Z",
        "closedByPullRequestsReferences": [
            {
                "number": 67,
                "state": "MERGED",
                "mergedAt": "2026-07-26T00:00:00Z",
                "url": "https://github.com/owner/repo/pull/67",
                "merged": True,
                "repository": {"nameWithOwner": "owner/repo"},
            }
        ],
        "timelineItems": {
            "nodes": [
                {
                    "__typename": "ClosedEvent",
                    "createdAt": "2026-07-26T00:00:00Z",
                    "closer": {
                        "__typename": "PullRequest",
                        "number": 67,
                        "state": "MERGED",
                        "merged": True,
                        "mergedAt": "2026-07-26T00:00:00Z",
                        "url": "https://github.com/owner/repo/pull/67",
                        "repository": {"nameWithOwner": "owner/repo"},
                    },
                }
            ],
            "pageInfo": {"hasPreviousPage": False},
        },
    }
    return {
        "branch": "task-70",
        "git_head": "1" * 40,
        "origin_main": "2" * 40,
        "runner_source_sha": "3" * 40,
        "status": [],
        "staged": [],
        "changed": ["tools/agent_workflow/workflow_evidence.py"],
        "remote_branch_exists": True,
        "local_branch_exists": True,
        "issues": {"70": task_issue, "63": child_issue},
        "relationships_by_issue": {
            "63": {
                "number": 63,
                "title": "[Task] Child",
                "state": "CLOSED",
                "issueType": {"name": "Task"},
                "parent": {
                    "number": 62,
                    "title": "[Feature] Workflow",
                    "state": "OPEN",
                },
                "subIssues": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                "blockedBy": {"nodes": []},
                "blocking": {"nodes": []},
            }
        },
        "relationships": {
            "number": 70,
            "title": "[Task] Optimize workflow",
            "state": "OPEN",
            "issueType": {"name": "Task"},
            "parent": {"number": 62, "title": "[Feature] Workflow", "state": "OPEN"},
            "subIssues": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            "blockedBy": {"nodes": []},
            "blocking": {"nodes": []},
        },
        "prs": {
            "67": {
                "number": 67,
                "title": "[Task] Child",
                "state": "MERGED",
                "isDraft": False,
                "url": "https://github.com/owner/repo/pull/67",
                "baseRefName": "main",
                "baseRefOid": "0" * 40,
                "headRefName": "task-63",
                "headRefOid": "6" * 40,
                "mergeCommit": {"oid": "7" * 40},
                "mergedAt": "2026-07-26T00:00:00Z",
                "mergeable": "UNKNOWN",
                "reviewDecision": "APPROVED",
                "files": [{"path": "child.py"}],
                "commits": [{"oid": "6" * 40, "messageHeadline": "Child"}],
                "statusCheckRollup": [{"name": "CI", "conclusion": "SUCCESS"}],
                "reviews": [],
                "closingIssuesReferences": [{"number": 63}],
            }
        },
        "pr": {
            "number": 71,
            "title": "[Task] Optimize workflow",
            "state": "OPEN",
            "isDraft": False,
            "url": "https://github.com/owner/repo/pull/71",
            "baseRefName": "main",
            "baseRefOid": "2" * 40,
            "headRefName": "task-70",
            "headRefOid": "4" * 40,
            "mergeCommit": None,
            "mergedAt": None,
            "mergeable": "MERGEABLE",
            "reviewDecision": "",
            "files": [{"path": f"file-{index}.py"} for index in range(120)],
            "commits": [{"oid": "4" * 40, "messageHeadline": "Implement"}],
            "statusCheckRollup": [{"name": "CI", "conclusion": "SUCCESS"}],
            "reviews": [],
            "closingIssuesReferences": [{"number": 70}],
        },
        "threads": {
            "nodes": [],
            "pageInfo": {"hasNextPage": False},
        },
        "required_checks": {"contexts": ["CI"]},
        "required_checks_mode": "available",
        "diff": "diff --git a/file.py b/file.py\n+safe content\n",
    }


def _write_repo(
    tmp_path: Path, state: dict[str, Any]
) -> tuple[Path, Path, dict[str, str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(
        ".agents/evidence.local/\n.agents/validation.local/\n",
        encoding="utf-8",
    )
    skill_path = repo / ".agents" / "skills" / "task-pr-review-runner" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("fixture review skill\n", encoding="utf-8")
    delivery_skill = repo / ".agents" / "skills" / "task-delivery-runner" / "SKILL.md"
    delivery_skill.parent.mkdir(parents=True)
    delivery_skill.write_text("fixture delivery skill\n", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_WORKFLOW_STATE"] = str(state_path)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return repo, state_path, env


def _run(
    repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, str(SCRIPT), *args, "--repo-root", str(repo)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_help_for_all_commands() -> None:
    commands = (
        "emit-review-handoff",
        "review-terminal",
        "delivery-preflight",
        "delivery-readiness",
        "pr-review-snapshot",
        "pr-review-recheck",
        "closeout-plan",
        "closeout-final",
        "feature-audit-snapshot",
        "feature-audit-recheck",
    )
    for command in commands:
        result = subprocess.run(
            [PYTHON, str(SCRIPT), command, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_pr_snapshot_is_compact_bounded_and_read_only(tmp_path: Path) -> None:
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "pr-review-snapshot",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-title",
        "[Task] Optimize workflow",
        "--expected-base-sha",
        "2" * 40,
        "--expected-head-sha",
        "4" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["operation"] == "pr-review-snapshot"
    assert value["gates"]["closing_linkage"]["status"] == "pass"
    assert value["gates"]["check_runs"]["status"] == "pass"
    assert value["observed"]["pr"]["changed_files"]["count"] == 120
    assert value["observed"]["pr"]["changed_files"]["truncated"] is True
    assert value["details_path"].startswith(".agents/evidence.local/")
    assert "safe content" not in result.stdout
    assert "authorization" not in result.stdout.casefold()
    assert value["observed"]["effective_diff"]["sha256"]


def test_pr_snapshot_fails_when_pr_closes_extra_issue(tmp_path: Path) -> None:
    state = _base_state()
    state["pr"]["closingIssuesReferences"] = [{"number": 70}, {"number": 999}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["closing_linkage"]
    assert gate["status"] == "fail"
    assert "observed=[70, 999]" in gate["detail"]


def test_pr_snapshot_fails_when_pr_closes_wrong_issue(tmp_path: Path) -> None:
    state = _base_state()
    state["pr"]["closingIssuesReferences"] = [{"number": 999}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["gates"]["closing_linkage"]["status"] == "fail"


def test_pr_snapshot_fails_without_closing_issue(tmp_path: Path) -> None:
    state = _base_state()
    state["pr"]["closingIssuesReferences"] = []
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["gates"]["closing_linkage"]["status"] == "fail"


def test_pr_snapshot_never_passes_truncated_closing_linkage(tmp_path: Path) -> None:
    state = _base_state()
    state["pr"]["closingIssuesReferences"] = [
        {"number": 70},
        *({"number": number} for number in range(1000, 1050)),
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["closing_linkage"]
    assert gate["status"] == "fail"
    assert "count=51" in gate["detail"]
    assert "truncated=true" in gate["detail"]


def test_review_recheck_detects_head_and_diff_drift(tmp_path: Path) -> None:
    state = _base_state()
    repo, state_path, env = _write_repo(tmp_path, state)
    first = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert first.returncode == 0, first.stderr
    first_value = json.loads(first.stdout)
    state["pr"]["headRefOid"] = "5" * 40
    state["diff"] = "diff --git a/file.py b/file.py\n+changed\n"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second = _run(
        repo,
        env,
        "pr-review-recheck",
        "--snapshot-id",
        first_value["snapshot_id"],
    )
    assert second.returncode == 0, second.stderr
    value = json.loads(second.stdout)
    assert value["stability"]["stable"] is False
    assert "head_sha" in value["stability"]["changed_fields"]["items"]
    assert "effective_diff_sha256" in value["stability"]["changed_fields"]["items"]
    assert value["gates"]["snapshot_stability"]["status"] == "fail"


def test_read_only_mode_skips_fetch_and_reports_local_main(tmp_path: Path) -> None:
    state = _base_state()
    state["fetch_ok"] = False
    state["local_main"] = "8" * 40
    repo, _, env = _write_repo(tmp_path, state)
    env["WORKFLOW_EVIDENCE_READ_ONLY"] = "1"
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    git = value["observed"]["git"]
    assert git["origin_fetch"] == "pass"
    assert git["origin_refresh"] == "skipped-read-only"
    assert git["local_main_sha"] == "8" * 40


def test_plan_limit_is_distinct_from_success(tmp_path: Path) -> None:
    state = _base_state()
    state["required_checks_mode"] = "plan-limit-403"
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["observed"]["required_checks"]["configuration"] == "plan-limited-403"
    assert value["gates"]["required_checks_configuration"]["status"] == "unknown"
    assert value["gates"]["check_runs"]["status"] == "pass"


def test_feature_recheck_detects_direct_child_set_drift(tmp_path: Path) -> None:
    state = _base_state()
    state["issues"]["62"] = {
        "number": 62,
        "title": "[Feature] Workflow",
        "state": "OPEN",
        "labels": [{"name": "type:feature"}],
        "projectItems": [{"status": {"name": "In Progress"}}],
        "url": "https://github.com/owner/repo/issues/62",
        "closedAt": None,
        "closedByPullRequestsReferences": [],
    }
    state["relationships"] = {
        "number": 62,
        "title": "[Feature] Workflow",
        "state": "OPEN",
        "issueType": {"name": "Feature"},
        "parent": None,
        "subIssues": {
            "nodes": [
                {
                    "number": 63,
                    "title": "[Task] Child",
                    "state": "CLOSED",
                    "labels": {"nodes": [{"name": "type:task"}]},
                }
            ],
            "pageInfo": {"hasNextPage": False},
        },
        "blockedBy": {"nodes": []},
        "blocking": {"nodes": []},
    }
    repo, state_path, env = _write_repo(tmp_path, state)
    first = _run(repo, env, "feature-audit-snapshot", "--feature", "62")
    assert first.returncode == 0, first.stderr
    first_value = json.loads(first.stdout)
    child = first_value["observed"]["direct_children"]["items"][0]
    assert child["relationship_evidence"]["parent"]["number"] == 62
    assert child["pull_request_evidence"]["items"][0]["number"] == 67
    assert child["pull_request_evidence"]["items"][0]["checks_all_success"] is True
    state["issues"]["64"] = dict(
        state["issues"]["63"], number=64, title="[Task] Another"
    )
    state["relationships"]["subIssues"]["nodes"].append(
        {
            "number": 64,
            "title": "[Task] Another",
            "state": "CLOSED",
            "labels": {"nodes": [{"name": "type:task"}]},
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second = _run(
        repo,
        env,
        "feature-audit-recheck",
        "--snapshot-id",
        first_value["snapshot_id"],
    )
    assert second.returncode == 0, second.stderr
    value = json.loads(second.stdout)
    assert value["stability"]["stable"] is False
    assert "direct_child_set_digest" in value["stability"]["changed_fields"]["items"]


def test_closeout_accepts_merge_reachable_from_later_origin_main(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["branch"] = "main"
    state["git_head"] = "9" * 40
    state["origin_main"] = "9" * 40
    state["issues"]["70"]["state"] = "CLOSED"
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Done"}}]
    state["pr"].update(
        state="MERGED",
        mergeCommit={"oid": "8" * 40},
        mergedAt="2026-07-26T00:00:00Z",
        statusCheckRollup=[{"name": "quality", "conclusion": "SUCCESS"}],
    )
    state["merge_on_main"] = True
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["main_contains_merge"]["status"] == "pass"
    assert value["gates"]["local_main_synced"]["status"] == "pass"
    assert value["limitations"] == ["read-only plan; no branch deletion performed"]


def _completed_closeout_state() -> dict[str, Any]:
    state = _base_state()
    state["branch"] = "main"
    state["git_head"] = "8" * 40
    state["local_main"] = "8" * 40
    state["origin_main"] = "8" * 40
    state["required_checks_mode"] = "plan-limit-403"
    state["issues"]["70"]["state"] = "CLOSED"
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Done"}}]
    state["issues"]["70"]["closedByPullRequestsReferences"] = [
        {
            "number": 71,
            "state": "MERGED",
            "merged": True,
            "mergedAt": "2026-07-26T00:00:00Z",
            "url": "https://github.com/owner/repo/pull/71",
            "repository": {"nameWithOwner": "owner/repo"},
        }
    ]
    state["issues"]["70"]["timelineItems"] = {
        "nodes": [
            {
                "__typename": "ClosedEvent",
                "createdAt": "2026-07-26T00:00:00Z",
                "closer": {
                    "__typename": "PullRequest",
                    "number": 71,
                    "state": "MERGED",
                    "merged": True,
                    "mergedAt": "2026-07-26T00:00:00Z",
                    "url": "https://github.com/owner/repo/pull/71",
                    "repository": {"nameWithOwner": "owner/repo"},
                },
            }
        ],
        "pageInfo": {"hasPreviousPage": False},
    }
    state["pr"].update(
        state="MERGED",
        mergeCommit={"oid": "8" * 40},
        mergedAt="2026-07-26T00:00:00Z",
        statusCheckRollup=[{"name": "quality", "conclusion": "SUCCESS"}],
    )
    state["merge_on_main"] = True
    state["tree_equal"] = True
    return state


def test_closeout_plan_limit_cleanup_eligibility_is_separate(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["required_checks_configuration"]["status"] == "unknown"
    assert value["observed"]["required_checks"]["failure"]["reason"] == (
        "github-plan-limit-403"
    )
    eligibility = value["observed"]["branch_cleanup"]["cleanup_eligibility"]
    assert eligibility["status"] == "blocked"
    assert eligibility["limitation_preserved"] is True
    assert eligibility["allowed_scope"] == "exact-task-branch-cleanup-only"
    assert (
        "final evidence recheck stability is not proven"
        in eligibility["reasons"]["items"]
    )
    assert value["gates"]["capability_limited_cleanup_eligibility"]["status"] == (
        "unknown"
    )


def test_closeout_final_plan_limit_cleanup_eligibility_requires_stable_recheck(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    repo, _, env = _write_repo(tmp_path, state)
    first = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert first.returncode == 0, first.stderr
    first_value = json.loads(first.stdout)
    second = _run(
        repo,
        env,
        "closeout-final",
        "--snapshot-id",
        first_value["snapshot_id"],
    )
    assert second.returncode == 0, second.stderr
    value = json.loads(second.stdout)
    assert value["gates"]["snapshot_stability"]["status"] == "pass"
    eligibility = value["observed"]["branch_cleanup"]["cleanup_eligibility"]
    assert eligibility["status"] == "eligible-under-capability-limited-policy"
    assert value["gates"]["capability_limited_cleanup_eligibility"]["status"] == "pass"


def test_closeout_cleanup_reports_null_closing_pr_metadata_as_unknown(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    state["issues"]["70"]["closedByPullRequestsReferences"] = [
        {
            "number": 71,
            "state": None,
            "merged": None,
            "mergedAt": None,
            "url": "https://github.com/owner/repo/pull/71",
            "repository": {"nameWithOwner": "owner/repo"},
        }
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["issue_closure"]["status"] == "unknown"
    eligibility = value["observed"]["branch_cleanup"]["cleanup_eligibility"]
    assert eligibility["status"] == "blocked"
    reasons = " ".join(eligibility["reasons"]["items"])
    assert "incomplete-closing-pr-metadata" in reasons
    assert "Issue closure is not linked to the merged PR" not in reasons


def test_closeout_cleanup_blocks_reopened_issue(tmp_path: Path) -> None:
    state = _completed_closeout_state()
    state["issues"]["70"]["state"] = "OPEN"
    state["issues"]["70"]["timelineItems"]["nodes"].append(
        {"__typename": "ReopenedEvent", "createdAt": "2026-07-26T01:00:00Z"}
    )
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["issue_state"]["status"] == "fail"
    assert value["gates"]["issue_closure"]["status"] == "fail"
    assert "issue-not-closed" in value["gates"]["issue_closure"]["detail"]
    assert (
        value["observed"]["branch_cleanup"]["cleanup_eligibility"]["status"]
        == "blocked"
    )


def test_closeout_cleanup_blocks_issue_closed_by_different_pr(tmp_path: Path) -> None:
    state = _completed_closeout_state()
    state["issues"]["70"]["closedByPullRequestsReferences"].append(
        {
            "number": 72,
            "state": "MERGED",
            "merged": True,
            "mergedAt": "2026-07-26T02:00:00Z",
            "url": "https://github.com/owner/repo/pull/72",
            "repository": {"nameWithOwner": "owner/repo"},
        }
    )
    state["issues"]["70"]["timelineItems"]["nodes"].append(
        {
            "__typename": "ClosedEvent",
            "createdAt": "2026-07-26T02:00:00Z",
            "closer": {
                "__typename": "PullRequest",
                "number": 72,
                "state": "MERGED",
                "merged": True,
                "mergedAt": "2026-07-26T02:00:00Z",
                "url": "https://github.com/owner/repo/pull/72",
                "repository": {"nameWithOwner": "owner/repo"},
            },
        }
    )
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["issue_closure"]["status"] == "fail"
    assert "closer-pr-number-mismatch" in value["gates"]["issue_closure"]["detail"]
    assert (
        value["observed"]["branch_cleanup"]["cleanup_eligibility"]["status"]
        == "blocked"
    )


def test_cleanup_eligibility_blocks_failed_pending_and_missing_checks(
    tmp_path: Path,
) -> None:
    for index, rollup in enumerate(
        (
            [{"name": "CI", "conclusion": "FAILURE"}],
            [{"name": "quality", "status": "IN_PROGRESS"}],
            [{"name": "CI", "conclusion": "SUCCESS"}],
            [],
        )
    ):
        state = _completed_closeout_state()
        state["pr"]["statusCheckRollup"] = rollup
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        repo, _, env = _write_repo(case_dir, state)
        result = _run(
            repo,
            env,
            "closeout-plan",
            "--task",
            "70",
            "--pr",
            "71",
            "--expected-head-sha",
            "4" * 40,
            "--expected-merge-sha",
            "8" * 40,
        )
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        eligibility = value["observed"]["branch_cleanup"]["cleanup_eligibility"]
        assert eligibility["status"] == "blocked"
        assert value["gates"]["capability_limited_cleanup_eligibility"]["status"] == (
            "unknown"
        )


def test_cleanup_eligibility_blocks_non_plan_limit_403_and_ref_drift(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    state["required_checks_mode"] = "available"
    state["local_branch_tips"] = {"task-70": "5" * 40}
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    eligibility = value["observed"]["branch_cleanup"]["cleanup_eligibility"]
    assert eligibility["status"] == "blocked"
    assert "Required Checks failure is not classified" in " ".join(
        eligibility["reasons"]["items"]
    )
    assert value["gates"]["local_branch_tip"]["status"] == "fail"


def test_generic_resource_not_accessible_403_does_not_enable_cleanup(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    state["required_checks_mode"] = "403"
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["observed"]["required_checks"]["configuration"] == "unknown"
    assert value["observed"]["required_checks"]["failure"]["reason"] == (
        "github-scope-or-sso-403"
    )
    assert (
        value["observed"]["branch_cleanup"]["cleanup_eligibility"]["status"]
        == "blocked"
    )


def test_closeout_recheck_blocks_cleanup_eligibility_on_drift(tmp_path: Path) -> None:
    state = _completed_closeout_state()
    repo, state_path, env = _write_repo(tmp_path, state)
    first = _run(
        repo,
        env,
        "closeout-plan",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-head-sha",
        "4" * 40,
        "--expected-merge-sha",
        "8" * 40,
    )
    assert first.returncode == 0, first.stderr
    first_value = json.loads(first.stdout)
    state["pr"]["statusCheckRollup"] = [{"name": "CI", "conclusion": "FAILURE"}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second = _run(
        repo,
        env,
        "closeout-final",
        "--snapshot-id",
        first_value["snapshot_id"],
    )
    assert second.returncode == 0, second.stderr
    value = json.loads(second.stdout)
    assert value["gates"]["snapshot_stability"]["status"] == "fail"
    assert (
        value["observed"]["branch_cleanup"]["cleanup_eligibility"]["status"]
        == "blocked"
    )


def test_missing_github_fact_never_becomes_false_pass(tmp_path: Path) -> None:
    state = _base_state()
    del state["issues"]["70"]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["issue_available"]["status"] == "unknown"
    assert value["warnings"]


def test_relationship_unavailable_keeps_blocker_gate_unknown(tmp_path: Path) -> None:
    state = _base_state()
    state["relationships"] = None
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["formal_blockers"]["status"] == "unknown"


def test_no_formal_blockers_passes(tmp_path: Path) -> None:
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["formal_blockers"]
    assert gate["status"] == "pass"
    assert gate["detail"] == "unresolved=0, resolved=0, total=0"


def test_closed_formal_blocker_is_resolved(tmp_path: Path) -> None:
    state = _base_state()
    state["relationships"]["blockedBy"]["nodes"] = [
        {"number": 72, "title": "[Task] Dependency", "state": "CLOSED"}
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["formal_blockers"]
    assert gate["status"] == "pass"
    assert gate["detail"] == "unresolved=0, resolved=1, total=1"


def test_open_formal_blocker_fails(tmp_path: Path) -> None:
    state = _base_state()
    state["relationships"]["blockedBy"]["nodes"] = [
        {"number": 72, "title": "[Task] Dependency", "state": "OPEN"}
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["formal_blockers"]
    assert gate["status"] == "fail"
    assert "unresolved=1" in gate["detail"]
    assert "open=[72]" in gate["detail"]


def test_mixed_formal_blockers_fail_when_any_is_open(tmp_path: Path) -> None:
    state = _base_state()
    state["relationships"]["blockedBy"]["nodes"] = [
        {"number": 72, "title": "[Task] Resolved", "state": "CLOSED"},
        {"number": 73, "title": "[Task] Unresolved", "state": "OPEN"},
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["formal_blockers"]
    assert gate["status"] == "fail"
    assert "unresolved=1" in gate["detail"]
    assert "resolved=1" in gate["detail"]


def test_unknown_formal_blocker_state_does_not_pass(tmp_path: Path) -> None:
    state = _base_state()
    state["relationships"]["blockedBy"]["nodes"] = [
        {"number": 72, "title": "[Task] Dependency", "state": "UNKNOWN"}
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["formal_blockers"]
    assert gate["status"] == "unknown"
    assert "unknown_state=1" in gate["detail"]


def test_truncated_formal_blockers_do_not_pass(tmp_path: Path) -> None:
    state = _base_state()
    state["relationships"]["blockedBy"] = {
        "nodes": [{"number": 72, "title": "[Task] Resolved", "state": "CLOSED"}],
        "pageInfo": {"hasNextPage": True},
    }
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["formal_blockers"]
    assert gate["status"] == "unknown"
    assert "truncated" in gate["detail"]


def test_truncated_review_threads_do_not_pass(tmp_path: Path) -> None:
    state = _base_state()
    state["threads"]["pageInfo"]["hasNextPage"] = True
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["unresolved_threads"]["status"] == "unknown"


def test_recheck_detects_issue_content_and_review_metadata_drift(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["body"] = "initial body"
    state["issues"]["70"]["comments"] = []
    state["pr"]["body"] = "initial PR body"
    repo, state_path, env = _write_repo(tmp_path, state)
    first = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert first.returncode == 0, first.stderr
    snapshot_id = json.loads(first.stdout)["snapshot_id"]
    state["issues"]["70"]["comments"] = [
        {
            "author": {"login": "maintainer"},
            "createdAt": "2026-07-26T00:00:00Z",
            "updatedAt": "2026-07-26T00:00:00Z",
            "body": "scope clarification",
        }
    ]
    state["pr"]["reviews"] = [
        {
            "author": {"login": "reviewer"},
            "state": "APPROVED",
            "submittedAt": "2026-07-26T00:00:00Z",
        }
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second = _run(repo, env, "pr-review-recheck", "--snapshot-id", snapshot_id)
    assert second.returncode == 0, second.stderr
    changed = json.loads(second.stdout)["stability"]["changed_fields"]["items"]
    assert "issue_content_sha256" in changed
    assert "pr_review_metadata_sha256" in changed


def test_feature_recheck_detects_child_lifecycle_drift_without_set_change(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["62"] = {
        "number": 62,
        "title": "[Feature] Workflow",
        "state": "OPEN",
        "labels": [{"name": "type:feature"}],
        "projectItems": [{"status": {"name": "In Progress"}}],
        "url": "https://github.com/owner/repo/issues/62",
        "closedAt": None,
        "closedByPullRequestsReferences": [],
    }
    state["relationships"] = {
        "number": 62,
        "title": "[Feature] Workflow",
        "state": "OPEN",
        "issueType": {"name": "Feature"},
        "parent": None,
        "subIssues": {
            "nodes": [
                {
                    "number": 63,
                    "title": "[Task] Child",
                    "state": "CLOSED",
                    "labels": {"nodes": [{"name": "type:task"}]},
                }
            ],
            "pageInfo": {"hasNextPage": False},
        },
        "blockedBy": {"nodes": []},
        "blocking": {"nodes": []},
    }
    repo, state_path, env = _write_repo(tmp_path, state)
    first = _run(repo, env, "feature-audit-snapshot", "--feature", "62")
    assert first.returncode == 0, first.stderr
    snapshot_id = json.loads(first.stdout)["snapshot_id"]
    state["issues"]["63"]["projectItems"] = [{"status": {"name": "Review"}}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second = _run(repo, env, "feature-audit-recheck", "--snapshot-id", snapshot_id)
    assert second.returncode == 0, second.stderr
    changed = json.loads(second.stdout)["stability"]["changed_fields"]["items"]
    assert "direct_child_evidence_digest" in changed


def test_fetch_failure_is_explicit_unknown_gate(tmp_path: Path) -> None:
    state = _base_state()
    state["fetch_ok"] = False
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2222222222222222222222222222222222222222",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["origin_fetch"]["status"] == "unknown"
    assert any(item["command_id"] == "git-fetch-origin" for item in value["warnings"])
    serialized = json.dumps(value)
    assert "C:/Users" not in serialized
    assert "/home/maple" not in serialized
    assert "<absolute-path-redacted>" in serialized


# --- Delivery Preflight tests (new) ---

SHA40 = "2" * 40


def _run_implementation_preflight(
    repo: Path,
    env: dict[str, str],
    *,
    branch: str = "task/70-bootstrap",
    expected_base: str = SHA40,
) -> dict[str, Any]:
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "implementation",
        "--branch",
        branch,
        "--expected-base-sha",
        expected_base,
    )
    assert result.returncode == 0, result.stderr
    return cast(dict[str, Any], json.loads(result.stdout))


def test_implementation_bootstraps_missing_canonical_branch_from_locked_main(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state.update(
        branch="main",
        git_head=SHA40,
        local_main=SHA40,
        origin_main=SHA40,
        local_branch_exists=False,
        remote_branch_exists=False,
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_exists"]["status"] == "pass"
    assert value["gates"]["branch_remote"]["status"] == "pass"
    assert value["gates"]["branch_identity"]["status"] == "pass"
    assert value["gates"]["branch_bootstrap"]["status"] == "pass"
    assert value["gates"]["branch_base"]["status"] == "pass"
    assert value["disposition"]["write_actions_allowed"] is True


def test_implementation_reuses_existing_synced_canonical_branch(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state.update(
        branch="main",
        git_head=SHA40,
        local_main=SHA40,
        origin_main=SHA40,
        local_branch_exists=True,
        remote_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": "4" * 40},
        branch_base=SHA40,
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_exists"]["status"] == "pass"
    assert value["gates"]["branch_bootstrap"]["status"] == "pass"
    assert value["gates"]["branch_base"]["status"] == "pass"
    assert value["disposition"]["status"] == "pass"


def test_implementation_rejects_existing_branch_with_wrong_base(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state.update(
        branch="main",
        git_head=SHA40,
        local_main=SHA40,
        origin_main=SHA40,
        local_branch_exists=True,
        remote_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": "4" * 40},
        branch_base="9" * 40,
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_base"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_implementation_rejects_branch_identity_and_worktree_ownership_conflicts(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state.update(
        branch="main",
        git_head=SHA40,
        local_main=SHA40,
        origin_main=SHA40,
        local_branch_exists=False,
        remote_branch_exists=False,
        extra_worktree_branches=["task/70-bootstrap"],
    )
    repo, _, env = _write_repo(tmp_path, state)
    identity_value = _run_implementation_preflight(repo, env, branch="foreign/branch")
    assert identity_value["gates"]["branch_identity"]["status"] == "fail"
    assert identity_value["disposition"]["write_actions_allowed"] is False

    ownership_value = _run_implementation_preflight(repo, env)
    assert ownership_value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert "another worktree" in ownership_value["gates"]["branch_bootstrap"]["detail"]
    assert ownership_value["disposition"]["write_actions_allowed"] is False


def test_implementation_rejects_dirty_missing_branch_bootstrap(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state.update(
        branch="main",
        git_head=SHA40,
        local_main=SHA40,
        origin_main=SHA40,
        local_branch_exists=False,
        remote_branch_exists=False,
        status=[" M existing.py"],
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert "clean worktree" in value["gates"]["branch_bootstrap"]["detail"]
    assert value["disposition"]["write_actions_allowed"] is False


def test_implementation_checks_existing_active_pr_identity_before_write(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state.update(
        branch="main",
        git_head=SHA40,
        local_main=SHA40,
        origin_main=SHA40,
        local_branch_exists=True,
        remote_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": "4" * 40},
        branch_base=SHA40,
        active_prs=[
            {
                "number": 71,
                "state": "OPEN",
                "isDraft": False,
                "headRefName": "task/70-bootstrap",
                "headRefOid": "4" * 40,
                "baseRefName": "main",
                "baseRefOid": SHA40,
                "closingIssuesReferences": [{"number": 70}],
            }
        ],
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["active_pr_identity"]["status"] == "pass"
    assert value["disposition"]["write_actions_allowed"] is True

    state["active_prs"][0]["closingIssuesReferences"] = [{"number": 71}]
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    conflict = _run_implementation_preflight(repo, env)
    assert conflict["gates"]["active_pr_identity"]["status"] == "fail"
    assert conflict["disposition"]["write_actions_allowed"] is False

    state["active_prs"].append(dict(state["active_prs"][0]))
    state_path.write_text(json.dumps(state), encoding="utf-8")
    multiple = _run_implementation_preflight(repo, env)
    assert multiple["gates"]["active_pr_identity"]["status"] == "fail"
    assert multiple["disposition"]["write_actions_allowed"] is False


def _write_review_handoff(
    repo: Path,
    initial: dict[str, Any],
    recheck: dict[str, Any],
    *,
    base_sha: str = SHA40,
    head_sha: str = "4" * 40,
    skill_relative: str = ".agents/skills/task-pr-review-runner/SKILL.md",
    required_findings: list[dict[str, Any]] | None = None,
    required_remediation: list[dict[str, Any]] | None = None,
) -> str:
    skill_path = repo / skill_relative
    matrix_path = (
        repo / ".agents" / "evidence.local" / "review-matrices" / "task-70-pr-71.json"
    )
    matrix = {
        "task": 70,
        "pr": 71,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "effective_diff_sha256": initial["observed"]["effective_diff"]["sha256"],
        "review_skill": {
            "path": skill_relative,
            "sha256": hashlib.sha256(skill_path.read_bytes()).hexdigest(),
        },
        "changed_file_groups": [
            {
                "name": "workflow",
                "files": ["tools/agent_workflow/workflow_evidence.py"],
                "status": "partially_verified",
                "evidence": ["test fixture"],
                "findings": ["H1"],
                "remaining_risk": "fixture",
            }
        ],
        "acceptance_criteria": [
            {
                "id": "AC-1",
                "text": "fixture",
                "status": "partially_verified",
                "implementation_evidence": ["test fixture"],
                "validation_evidence": ["test fixture"],
                "remaining_risk": "fixture",
            }
        ],
        "evidence_gates": {
            "review": "pass",
            "validation": "pass",
            "recheck": "pass",
        },
        "overall": "partial",
    }
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    matrix_bytes = json.dumps(
        matrix, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    matrix_path.write_bytes(matrix_bytes)
    handoff: dict[str, Any] = {
        "schema_version": 1,
        "kind": "independent-review-handoff",
        "repository": "owner/repo",
        "task": 70,
        "pr": 71,
        "reviewed_base_sha": base_sha,
        "reviewed_head_sha": head_sha,
        "verdict": "FAIL",
        "required_findings": required_findings
        if required_findings is not None
        else [{"id": "H1", "severity": "High", "required": True}],
        "required_remediation": required_remediation
        if required_remediation is not None
        else [{"id": "H1", "required": True, "description": "fixture repair"}],
        "objective_gates": [],
        "maintainer_decision_required": False,
        "created_at": "2026-08-15T00:00:00Z",
        "freshness": {"status": "fresh", "recheck": "pass"},
        "review_evidence": {
            "effective_diff_sha256": initial["observed"]["effective_diff"]["sha256"],
            "review_skill": {
                "path": skill_relative,
                "sha256": hashlib.sha256(skill_path.read_bytes()).hexdigest(),
            },
            "evidence_matrix_path": ".agents/evidence.local/review-matrices/task-70-pr-71.json",
            "evidence_matrix_sha256": hashlib.sha256(matrix_bytes).hexdigest(),
            "review_snapshot_id": initial["snapshot_id"],
            "recheck_snapshot_id": recheck["snapshot_id"],
        },
    }
    payload_path = (
        repo / ".agents" / "evidence.local" / "review-staging" / "handoff-payload.json"
    )
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(
        json.dumps(handoff, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    emitted = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "emit-review-handoff",
            "--repo-root",
            str(repo),
            "--payload",
            ".agents/evidence.local/review-staging/handoff-payload.json",
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert emitted.returncode == 0, emitted.stdout + emitted.stderr
    evidence_id = str(json.loads(emitted.stdout)["evidence_id"])
    destination = (
        repo / ".agents" / "evidence.local" / "review-handoffs" / f"{evidence_id}.json"
    )
    assert destination.is_file()
    return evidence_id


def _review_remediation_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, str], dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = _base_state()
    state["git_head"] = "4" * 40
    state["pr"]["headRepository"] = {"nameWithOwner": "owner/repo"}
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    repo, state_path, env = _write_repo(tmp_path, state)
    first = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert first.returncode == 0, first.stderr
    initial = json.loads(first.stdout)
    second = _run(
        repo,
        env,
        "pr-review-recheck",
        "--snapshot-id",
        initial["snapshot_id"],
    )
    assert second.returncode == 0, second.stderr
    recheck = json.loads(second.stdout)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return repo, state_path, env, state, initial, recheck


def _run_review_remediation(
    repo: Path,
    env: dict[str, str],
    *,
    expected_base: str = SHA40,
    expected_head: str = "4" * 40,
    evidence_id: str = "0" * 64,
) -> dict[str, Any]:
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "review-remediation",
        "--pr",
        "71",
        "--expected-base-sha",
        expected_base,
        "--expected-head-sha",
        expected_head,
        "--review-handoff-id",
        evidence_id,
    )
    assert result.returncode == 0, result.stderr
    return cast(dict[str, Any], json.loads(result.stdout))


def test_review_remediation_accepts_fresh_structured_handoff_without_submitted_review(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff"]["status"] == "pass"
    assert value["gates"]["review_handoff_identity"]["status"] == "pass"
    assert value["gates"]["review_handoff_evidence"]["status"] == "pass"
    assert value["gates"]["review_handoff_freshness"]["status"] == "pass"
    assert value["gates"]["implementation_head"]["status"] == "pass"
    assert value["gates"]["review_conclusion"]["status"] == "pass"
    assert value["observed"]["review_handoff"]["selected"]["evidence_id"] == evidence_id
    assert value["disposition"]["write_actions_allowed"] is True


def test_review_producer_emits_self_verifying_canonical_artifact(
    tmp_path: Path,
) -> None:
    repo, _, _, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    artifact = json.loads(
        (
            repo
            / ".agents"
            / "evidence.local"
            / "review-handoffs"
            / f"{evidence_id}.json"
        ).read_text(encoding="utf-8")
    )
    unsigned = {key: value for key, value in artifact.items() if key != "evidence_id"}
    assert (
        artifact["evidence_id"]
        == hashlib.sha256(
            json.dumps(
                unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    )
    assert artifact["review_evidence"]["evidence_matrix_path"].startswith(
        ".agents/evidence.local/"
    )


def test_review_producer_rejects_symlinked_handoff_parent_without_external_write(
    tmp_path: Path,
) -> None:
    repo, _, _, _, _, _ = _review_remediation_fixture(tmp_path)
    evidence_root = repo / ".agents" / "evidence.local"
    outside = tmp_path / "outside"
    outside.mkdir()
    handoff_dir = evidence_root / "review-handoffs"
    try:
        handoff_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    payload_path = evidence_root / "review-staging" / "malicious.json"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text("{}", encoding="utf-8")
    emitted = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "emit-review-handoff",
            "--repo-root",
            str(repo),
            "--payload",
            ".agents/evidence.local/review-staging/malicious.json",
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert emitted.returncode != 0
    assert list(outside.iterdir()) == []


def test_review_terminal_materializes_and_exposes_same_evidence_id(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    handoff_path = (
        repo / ".agents" / "evidence.local" / "review-handoffs" / f"{evidence_id}.json"
    )
    payload_path = (
        repo / ".agents" / "evidence.local" / "review-staging" / "terminal.json"
    )
    payload_path.write_text(handoff_path.read_text(encoding="utf-8"), encoding="utf-8")
    handoff_path.unlink()
    terminal = _run(
        repo,
        env,
        "review-terminal",
        "--repository",
        "owner/repo",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-base-sha",
        SHA40,
        "--expected-head-sha",
        "4" * 40,
        "--effective-diff-sha256",
        initial["observed"]["effective_diff"]["sha256"],
        "--review-snapshot-id",
        initial["snapshot_id"],
        "--recheck-snapshot-id",
        recheck["snapshot_id"],
        "--payload",
        ".agents/evidence.local/review-staging/terminal.json",
    )
    assert terminal.returncode == 0, terminal.stdout + terminal.stderr
    result = json.loads(terminal.stdout)
    assert result["status"] == "pass"
    assert result["review_handoff_id"] == evidence_id
    assert result["reference"].endswith(f"{evidence_id}.json")
    assert handoff_path.is_file()
    admitted = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert admitted["gates"]["review_conclusion"]["status"] == "pass"


def test_review_terminal_fails_closed_when_emission_parent_is_symlink(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    handoff_path = (
        repo / ".agents" / "evidence.local" / "review-handoffs" / f"{evidence_id}.json"
    )
    payload_path = (
        repo / ".agents" / "evidence.local" / "review-staging" / "terminal.json"
    )
    payload_path.write_text(handoff_path.read_text(encoding="utf-8"), encoding="utf-8")
    handoff_path.unlink()
    outside = tmp_path / "terminal-outside"
    outside.mkdir()
    handoff_dir = handoff_path.parent
    try:
        handoff_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    terminal = _run(
        repo,
        env,
        "review-terminal",
        "--repository",
        "owner/repo",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-base-sha",
        SHA40,
        "--expected-head-sha",
        "4" * 40,
        "--effective-diff-sha256",
        initial["observed"]["effective_diff"]["sha256"],
        "--review-snapshot-id",
        initial["snapshot_id"],
        "--recheck-snapshot-id",
        recheck["snapshot_id"],
        "--payload",
        ".agents/evidence.local/review-staging/terminal.json",
    )
    assert terminal.returncode == 2
    assert list(outside.iterdir()) == []


def test_review_remediation_rejects_malformed_explicit_handoff(
    tmp_path: Path,
) -> None:
    repo, _, env, _, _, _ = _review_remediation_fixture(tmp_path)
    evidence_id = "a" * 64
    target = (
        repo / ".agents" / "evidence.local" / "review-handoffs" / f"{evidence_id}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{", encoding="utf-8")
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff"]["status"] == "fail"
    assert value["gates"]["review_conclusion"]["status"] == "fail"


@pytest.mark.parametrize(
    ("findings", "remediation"),
    [
        ([{}], [{"id": "H1", "required": True, "description": "repair"}]),
        (
            [{"severity": "High", "required": True}],
            [{"id": "H1", "required": True, "description": "repair"}],
        ),
        (
            [{"id": "H1", "severity": "High", "required": True}],
            [{"id": "UNKNOWN", "required": True, "description": "repair"}],
        ),
        (
            [{"id": "H1", "severity": "High", "required": False}],
            [{"id": "H1", "required": True, "description": "repair"}],
        ),
        (
            [{"id": "H1", "severity": "High", "required": True}],
            [{"id": "H1", "required": True, "description": ""}],
        ),
        (
            [
                {"id": "H1", "severity": "High", "required": True},
                {"id": "H1", "severity": "High", "required": True},
            ],
            [
                {"id": "H1", "required": True, "description": "repair"},
                {"id": "H1", "required": True, "description": "repair again"},
            ],
        ),
        (
            [{"id": "H1", "severity": "High", "required": True}],
            [],
        ),
    ],
)
def test_review_remediation_rejects_invalid_required_remediation_binding(
    tmp_path: Path,
    findings: list[dict[str, Any]],
    remediation: list[dict[str, Any]],
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(
        repo,
        initial,
        recheck,
        required_findings=findings,
        required_remediation=remediation,
    )
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff_findings"]["status"] == "fail"
    assert value["gates"]["review_conclusion"]["status"] == "fail"


def test_review_remediation_rechecks_actual_skill_bytes(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    (repo / ".agents" / "skills" / "task-pr-review-runner" / "SKILL.md").write_text(
        "tampered review skill\n", encoding="utf-8"
    )
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff_evidence"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_review_remediation_rejects_noncanonical_review_skill(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(
        repo,
        initial,
        recheck,
        skill_relative=".agents/skills/task-delivery-runner/SKILL.md",
    )
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff_evidence"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_review_remediation_rechecks_snapshot_content_address(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    snapshot_path = (
        repo
        / ".agents"
        / "evidence.local"
        / "snapshots"
        / f"{initial['snapshot_id']}.json"
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["observed"]["pr"]["head_sha"] = "5" * 40
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff_evidence"]["status"] == "fail"


def test_review_remediation_rechecks_matrix_content_address(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    matrix_path = (
        repo / ".agents" / "evidence.local" / "review-matrices" / "task-70-pr-71.json"
    )
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["overall"] = "verified"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff_evidence"]["status"] == "fail"


def test_review_remediation_rejects_conflicting_current_head_handoffs(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    first_id = _write_review_handoff(repo, initial, recheck)
    first_path = (
        repo / ".agents" / "evidence.local" / "review-handoffs" / f"{first_id}.json"
    )
    second = json.loads(first_path.read_text(encoding="utf-8"))
    second.pop("evidence_id")
    second["required_findings"] = [
        {"id": "H2", "severity": "Blocking", "required": True}
    ]
    second["required_remediation"] = [
        {"id": "H2", "required": True, "description": "conflicting fixture"}
    ]
    payload_path = (
        repo / ".agents" / "evidence.local" / "review-staging" / "second.json"
    )
    payload_path.write_text(
        json.dumps(second, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    emitted = subprocess.run(
        [
            PYTHON,
            str(SCRIPT),
            "emit-review-handoff",
            "--repo-root",
            str(repo),
            "--payload",
            ".agents/evidence.local/review-staging/second.json",
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert emitted.returncode == 0, emitted.stdout + emitted.stderr
    second_id = json.loads(emitted.stdout)["evidence_id"]
    assert second_id != first_id
    value = _run_review_remediation(repo, env, evidence_id=first_id)
    assert value["gates"]["review_handoff"]["status"] == "fail"
    assert "ambiguous" in value["gates"]["review_handoff"]["detail"]


def test_review_remediation_explicit_reference_is_not_hidden_by_unrelated_files(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    directory = repo / ".agents" / "evidence.local" / "review-handoffs"
    for index in range(101):
        (directory / f"unrelated-{index:03d}.json").write_text(
            json.dumps({"task": 999, "pr": 999}), encoding="utf-8"
        )
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff"]["status"] == "pass"


def test_review_remediation_rejects_symlinked_explicit_reference(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck)
    target = (
        repo / ".agents" / "evidence.local" / "review-handoffs" / f"{evidence_id}.json"
    )
    backup = target.with_suffix(".backup.json")
    target.rename(backup)
    try:
        target.symlink_to(backup.name)
    except OSError:
        target.rename(backup)
        return
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff"]["status"] == "fail"


def test_review_remediation_rejects_stale_handoff_after_head_drift(
    tmp_path: Path,
) -> None:
    repo, state_path, env, state, initial, recheck = _review_remediation_fixture(
        tmp_path
    )
    evidence_id = _write_review_handoff(repo, initial, recheck)
    state["pr"]["headRefOid"] = "5" * 40
    state["git_head"] = "5" * 40
    state_path.write_text(json.dumps(state), encoding="utf-8")
    value = _run_review_remediation(
        repo, env, expected_head="5" * 40, evidence_id=evidence_id
    )
    assert value["gates"]["pr_head_sha"]["status"] == "pass"
    assert value["gates"]["review_handoff"]["status"] == "fail"
    assert value["gates"]["review_handoff_freshness"]["status"] == "fail"
    assert value["gates"]["review_conclusion"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_review_remediation_invalidates_h1_after_remediation_head_h2(
    tmp_path: Path,
) -> None:
    repo, state_path, env, state, initial, recheck = _review_remediation_fixture(
        tmp_path
    )
    evidence_id = _write_review_handoff(repo, initial, recheck, head_sha="4" * 40)
    first = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert first["disposition"]["write_actions_allowed"] is True

    state["pr"]["headRefOid"] = "5" * 40
    state["git_head"] = "5" * 40
    state["diff"] = "diff --git a/file.py b/file.py\n+remediated\n"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second = _run_review_remediation(
        repo, env, expected_head="5" * 40, evidence_id=evidence_id
    )
    assert second["gates"]["pr_head_sha"]["status"] == "pass"
    assert second["gates"]["review_handoff"]["status"] == "fail"
    assert second["gates"]["review_conclusion"]["status"] == "fail"
    assert second["disposition"]["write_actions_allowed"] is False


def test_review_remediation_rejects_missing_handoff_and_submitted_review(
    tmp_path: Path,
) -> None:
    repo, _, env, _, _, _ = _review_remediation_fixture(tmp_path)
    value = _run_review_remediation(repo, env)
    assert value["gates"]["review_handoff"]["status"] == "fail"
    assert value["gates"]["review_conclusion"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_review_remediation_rejects_handoff_with_wrong_base_identity(
    tmp_path: Path,
) -> None:
    repo, _, env, _, initial, recheck = _review_remediation_fixture(tmp_path)
    evidence_id = _write_review_handoff(repo, initial, recheck, base_sha="9" * 40)
    value = _run_review_remediation(repo, env, evidence_id=evidence_id)
    assert value["gates"]["review_handoff"]["status"] == "fail"
    assert value["gates"]["review_handoff_identity"]["status"] == "fail"
    assert value["gates"]["review_conclusion"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_review_remediation_submitted_review_cannot_bypass_missing_handoff(
    tmp_path: Path,
) -> None:
    repo, _, env, state, _, _ = _review_remediation_fixture(tmp_path)
    state["pr"]["reviews"] = [
        {
            "state": "COMMENTED",
            "author": {"login": "reviewer"},
            "submittedAt": "2026-08-15T00:00:00Z",
        }
    ]
    # The fixture's state file was already updated to Review; rewrite only the PR evidence.
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    value = _run_review_remediation(repo, env)
    assert value["gates"]["review_handoff"]["status"] == "fail"
    assert value["gates"]["review_conclusion"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_delivery_preflight_delivery_start_passes_with_ready_status(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Ready"}}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["operation"] == "delivery-preflight"
    assert value["subject"]["entry_point"] == "delivery-start"
    assert value["gates"]["lifecycle_labels_exclusive"]["status"] == "pass"
    assert value["gates"]["project_status_known"]["status"] == "pass"
    assert value["gates"]["parent_blocking"]["status"] == "pass"


def _review_snapshot_for_type(
    tmp_path: Path,
    *,
    labels: list[dict[str, str]],
    native_type: str | None,
) -> dict[str, Any]:
    state = _base_state()
    state["issues"]["70"]["labels"] = labels
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    state["relationships"]["issueType"] = (
        {"name": native_type} if native_type is not None else None
    )
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "pr-review-snapshot",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-base-sha",
        "2" * 40,
        "--expected-head-sha",
        "4" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def test_review_admission_accepts_canonical_task_leaf(tmp_path: Path) -> None:
    value = _review_snapshot_for_type(
        tmp_path,
        labels=[{"name": "type:task"}, {"name": "codex:ready"}],
        native_type="Task",
    )
    assert value["gates"]["issue_type"]["status"] == "pass"


def test_review_admission_accepts_canonical_bug_leaf(tmp_path: Path) -> None:
    value = _review_snapshot_for_type(
        tmp_path,
        labels=[{"name": "type:bug"}, {"name": "codex:ready"}],
        native_type="Bug",
    )
    assert value["gates"]["issue_type"]["status"] == "pass"
    assert value["gates"]["project_status_review"]["status"] == "pass"
    for gate_name in (
        "issue_state",
        "label:codex:ready",
        "label-not:codex:blocked",
        "closing_linkage",
        "base_sha",
        "head_sha",
    ):
        assert value["gates"][gate_name]["status"] == "pass"


def test_review_admission_accepts_real_issue_134_metadata_shape(
    tmp_path: Path,
) -> None:
    value = _review_snapshot_for_type(
        tmp_path,
        labels=[
            {"name": "type:bug"},
            {"name": "area:foundation"},
            {"name": "codex:ready"},
        ],
        native_type="Bug",
    )
    assert value["gates"]["issue_type"]["status"] == "pass"


@pytest.mark.parametrize(
    ("canonical_type", "native_type"),
    [("feature", "Feature"), ("epic", "Epic"), ("research", "Research")],
)
def test_review_admission_rejects_non_reviewable_leaf_types(
    tmp_path: Path,
    canonical_type: str,
    native_type: str,
) -> None:
    value = _review_snapshot_for_type(
        tmp_path,
        labels=[{"name": f"type:{canonical_type}"}, {"name": "codex:ready"}],
        native_type=native_type,
    )
    assert value["gates"]["issue_type"]["status"] == "fail"


def test_review_admission_rejects_missing_canonical_type(tmp_path: Path) -> None:
    value = _review_snapshot_for_type(
        tmp_path,
        labels=[{"name": "codex:ready"}],
        native_type=None,
    )
    assert value["gates"]["issue_type"]["status"] == "fail"
    assert "missing" in value["gates"]["issue_type"]["detail"]


def test_review_admission_rejects_conflicting_type_identity(tmp_path: Path) -> None:
    value = _review_snapshot_for_type(
        tmp_path,
        labels=[
            {"name": "type:task"},
            {"name": "type:bug"},
            {"name": "codex:ready"},
        ],
        native_type="Task",
    )
    assert value["gates"]["issue_type"]["status"] == "fail"
    assert "conflicting" in value["gates"]["issue_type"]["detail"]


def test_review_admission_rejects_native_type_conflict(tmp_path: Path) -> None:
    value = _review_snapshot_for_type(
        tmp_path,
        labels=[{"name": "type:bug"}, {"name": "codex:ready"}],
        native_type="Task",
    )
    assert value["gates"]["issue_type"]["status"] == "fail"
    assert "conflicts" in value["gates"]["issue_type"]["detail"]


def test_delivery_and_review_accept_the_same_bug_leaf_contract(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["labels"] = [
        {"name": "type:bug"},
        {"name": "codex:ready"},
    ]
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Ready"}}]
    state["relationships"]["issueType"] = {"name": "Bug"}
    repo, state_path, env = _write_repo(tmp_path, state)
    delivery = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        "2" * 40,
        "--entry-point",
        "delivery-start",
    )
    assert delivery.returncode == 0, delivery.stderr
    assert json.loads(delivery.stdout)["gates"]["issue_type"]["status"] == "pass"
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    review = _run(
        repo,
        env,
        "pr-review-snapshot",
        "--task",
        "70",
        "--pr",
        "71",
        "--expected-base-sha",
        "2" * 40,
        "--expected-head-sha",
        "4" * 40,
    )
    assert review.returncode == 0, review.stderr
    assert json.loads(review.stdout)["gates"]["issue_type"]["status"] == "pass"


def test_delivery_preflight_lifecycle_conflict_ready_and_needs_spec(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["labels"] = [
        {"name": "type:task"},
        {"name": "codex:ready"},
        {"name": "codex:needs-spec"},
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["lifecycle_labels_exclusive"]["status"] == "fail"
    assert "codex:needs-spec" in value["gates"]["lifecycle_labels_exclusive"]["detail"]
    assert "codex:ready" in value["gates"]["lifecycle_labels_exclusive"]["detail"]


def test_delivery_preflight_blocked_task_fails(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["labels"] = [
        {"name": "type:task"},
        {"name": "codex:blocked"},
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["label-not:codex:blocked"]["status"] == "fail"
    assert value["gates"]["lifecycle_labels_exclusive"]["status"] == "fail"


def test_delivery_preflight_project_status_incompatible(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["project_status_known"]["status"] == "fail"
    assert "Review" in value["gates"]["project_status_known"]["detail"]
    assert "delivery-start" in value["gates"]["project_status_known"]["detail"]


def test_delivery_preflight_closed_parent_blocks(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["relationships"]["parent"] = {
        "number": 62,
        "title": "[Feature] Closed parent",
        "state": "CLOSED",
    }
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["parent_blocking"]["status"] == "fail"
    assert "CLOSED" in value["gates"]["parent_blocking"]["detail"]


def test_delivery_preflight_entry_point_contract_violation_extra_params(
    tmp_path: Path,
) -> None:
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
        "--pr",
        "71",
    )
    assert result.returncode == 2
    assert "parameter contract violation" in result.stderr
    assert "extra=['pr']" in result.stderr


def test_delivery_preflight_entry_point_contract_violation_missing_params(
    tmp_path: Path,
) -> None:
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "implementation",
    )
    assert result.returncode == 2
    assert "parameter contract violation" in result.stderr
    assert "missing=['branch'" in result.stderr


def test_delivery_preflight_subject_includes_entry_point(
    tmp_path: Path,
) -> None:
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "pr-readiness",
        "--branch",
        "task-70",
        "--expected-base-sha",
        SHA40,
        "--expected-head-sha",
        "4" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["subject"]["entry_point"] == "pr-readiness"
    assert value["subject"]["branch"] == "task-70"


def test_delivery_preflight_unknown_project_status(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "BogusStatus"}}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["project_status_known"]["status"] == "fail"
    assert "BogusStatus" in value["gates"]["project_status_known"]["detail"]


def test_delivery_preflight_no_lifecycle_labels(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["labels"] = [{"name": "type:task"}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["lifecycle_labels_exclusive"]["status"] == "fail"
    assert "none" in value["gates"]["lifecycle_labels_exclusive"]["detail"]


def test_delivery_readiness_has_lifecycle_gates(tmp_path: Path) -> None:
    state = _base_state()
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-readiness",
        "--task",
        "70",
        "--pr",
        "71",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert "lifecycle_labels_exclusive" in value["gates"]
    assert "project_status_known" in value["gates"]
    assert value["gates"]["project_status_review"]["status"] == "pass"


def test_delivery_readiness_lifecycle_conflict_returns_fail(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["issues"]["70"]["labels"] = [
        {"name": "type:task"},
        {"name": "codex:ready"},
        {"name": "codex:needs-spec"},
    ]
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-readiness",
        "--task",
        "70",
        "--pr",
        "71",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["lifecycle_labels_exclusive"]["status"] == "fail"
    assert "codex:needs-spec" in value["gates"]["lifecycle_labels_exclusive"]["detail"]


def test_pr_review_snapshot_does_not_have_lifecycle_gates(
    tmp_path: Path,
) -> None:
    """Independent review boundary must not include lifecycle label gates."""
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "pr-review-snapshot",
        "--task",
        "70",
        "--pr",
        "71",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert "lifecycle_labels_exclusive" not in value["gates"]
    assert "project_status_known" not in value["gates"]


# --- Preflight disposition tests ---


def test_preflight_pass_disposition_allows_continuation(tmp_path: Path) -> None:
    """Pass disposition must set workflow_may_continue and write_actions_allowed."""
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    d = json.loads(result.stdout)["disposition"]
    assert d["status"] == "pass"
    assert d["disposition"] == "proceed"
    assert d["workflow_may_continue"] is True
    assert d["write_actions_allowed"] is True
    assert d["auto_remediation_allowed"] is False
    assert d["maintainer_action_required"] is False
    assert d["failed_gates"] == []


def test_preflight_fail_disposition_forbids_writes(tmp_path: Path) -> None:
    """Lifecycle conflict produces fail disposition with no write permission."""
    state = _base_state()
    state["issues"]["70"]["labels"] = [
        {"name": "type:task"},
        {"name": "codex:ready"},
        {"name": "codex:needs-spec"},
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    d = json.loads(result.stdout)["disposition"]
    assert d["status"] == "fail"
    assert d["disposition"] == "stop"
    assert d["workflow_may_continue"] is False
    assert d["write_actions_allowed"] is False
    assert d["auto_remediation_allowed"] is False
    assert d["maintainer_action_required"] is True
    assert len(d["failed_gates"]) >= 1
    assert any("lifecycle" in g["gate"] for g in d["failed_gates"])


def test_preflight_identity_conflict_forbids_writes(tmp_path: Path) -> None:
    """identity mismatch produces fail disposition with no write permission."""
    state = _base_state()
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    d = json.loads(result.stdout)["disposition"]
    assert d["status"] == "fail"
    assert d["workflow_may_continue"] is False
    assert d["write_actions_allowed"] is False


# --- Worktree compatibility tests ---


def _dirty_state(status_entries: list[str] | None = None) -> dict[str, Any]:
    state = _base_state()
    if status_entries is None:
        status_entries = [" M tools/agent_workflow/workflow_evidence.py"]
    state["status"] = status_entries
    return state


def test_worktree_delivery_start_allows_task_dirty(tmp_path: Path) -> None:
    """delivery-start with Task-owned dirty worktree → pass."""
    state = _dirty_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    gate = value["gates"]["worktree_state_compatible"]
    assert gate["status"] == "pass"
    assert gate["observed_clean"] is False
    assert gate["dirty_allowed"] is True
    assert gate["worktree_disposition"] == "continue-through-implementation"
    assert value["disposition"]["status"] == "pass"
    assert value["disposition"]["workflow_may_continue"] is True


def test_worktree_implementation_dirty_branch_setup_fails_closed(
    tmp_path: Path,
) -> None:
    """implementation branch admission rejects a dirty worktree."""
    state = _dirty_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "implementation",
        "--branch",
        "task-70",
        "--expected-base-sha",
        SHA40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["worktree_state_compatible"]["status"] == "pass"
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_worktree_final_validation_rejects_dirty(tmp_path: Path) -> None:
    """final-validation + dirty worktree → fail (stop)."""
    state = _dirty_state()
    state["local_branch_tips"] = {"task-70": "4" * 40}
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "final-validation",
        "--branch",
        "task-70",
        "--expected-base-sha",
        SHA40,
        "--expected-head-sha",
        "4" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    gate = value["gates"]["worktree_state_compatible"]
    assert gate["status"] == "fail"
    assert gate["dirty_allowed"] is False
    assert "clean committed head" in gate["detail"].casefold()
    assert value["disposition"]["workflow_may_continue"] is False


def test_worktree_pr_readiness_rejects_dirty(tmp_path: Path) -> None:
    """pr-readiness + dirty worktree → fail (stop)."""
    state = _dirty_state()
    state["local_branch_tips"] = {"task-70": "4" * 40}
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "pr-readiness",
        "--branch",
        "task-70",
        "--expected-base-sha",
        SHA40,
        "--expected-head-sha",
        "4" * 40,
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["worktree_state_compatible"]
    assert gate["status"] == "fail"
    assert gate["dirty_allowed"] is False


def test_worktree_review_remediation_rejects_dirty(tmp_path: Path) -> None:
    """review-remediation + dirty worktree → fail-closed."""
    state = _dirty_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "review-remediation",
        "--pr",
        "71",
        "--expected-base-sha",
        SHA40,
        "--expected-head-sha",
        "4" * 40,
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["worktree_state_compatible"]
    assert gate["status"] == "fail"
    assert gate["dirty_allowed"] is False


def test_worktree_clean_passes_all_entry_points(tmp_path: Path) -> None:
    """Clean worktree always passes regardless of entry point."""
    for index, entry_point in enumerate(
        (
            "delivery-start",
            "implementation",
            "final-validation",
            "pr-readiness",
            "review-remediation",
        ),
    ):
        extra: list[str] = []
        if entry_point in ("implementation", "final-validation", "pr-readiness"):
            extra = ["--branch", "task-70", "--expected-base-sha", SHA40]
        if entry_point in ("final-validation", "pr-readiness"):
            extra.extend(["--expected-head-sha", "4" * 40])
        if entry_point == "review-remediation":
            extra = [
                "--pr",
                "71",
                "--expected-base-sha",
                SHA40,
                "--expected-head-sha",
                "4" * 40,
            ]
        state = _base_state()
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        repo, _, env = _write_repo(case_dir, state)
        result = _run(
            repo,
            env,
            "delivery-preflight",
            "--task",
            "70",
            "--expected-main-sha",
            SHA40,
            "--entry-point",
            entry_point,
            *extra,
        )
        assert result.returncode == 0, result.stderr
        gate = json.loads(result.stdout)["gates"]["worktree_state_compatible"]
        assert gate["status"] == "pass", f"unexpected fail for {entry_point}"
        assert gate["observed_clean"] is True


def test_worktree_disposition_includes_detailed_observations(
    tmp_path: Path,
) -> None:
    """Dirty-allowed Preflight records staged/changed/untracked details."""
    state = _dirty_state()
    state["staged"] = ["staged_file.py"]
    state["changed"] = ["changed_file.py"]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["worktree_state_compatible"]
    assert "staged_files" in gate
    assert "changed_files" in gate


def test_preflight_has_no_legacy_worktree_clean_gate(tmp_path: Path) -> None:
    """worktree_clean gate must be replaced by worktree_state_compatible."""
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    gates = json.loads(result.stdout)["gates"]
    assert "worktree_clean" not in gates
    assert "worktree_state_compatible" in gates


def test_preflight_disposition_partial_on_unknown_gate(tmp_path: Path) -> None:
    """Unknown critical gate (e.g. missing issue) → partial disposition stop."""
    state = _base_state()
    del state["issues"]["70"]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    d = json.loads(result.stdout)["disposition"]
    assert d["disposition"] == "stop"
    assert d["workflow_may_continue"] is False
    assert d["write_actions_allowed"] is False


def test_preflight_blocked_label_forbids_continuation(tmp_path: Path) -> None:
    """codex:blocked label → fail disposition."""
    state = _base_state()
    state["issues"]["70"]["labels"] = [
        {"name": "type:task"},
        {"name": "codex:blocked"},
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    d = json.loads(result.stdout)["disposition"]
    assert d["workflow_may_continue"] is False
    assert d["write_actions_allowed"] is False


def test_preflight_closed_parent_forbids_continuation(tmp_path: Path) -> None:
    """Closed parent → fail disposition forbidding writes."""
    state = _base_state()
    state["relationships"]["parent"] = {
        "number": 62,
        "title": "[Feature] Closed",
        "state": "CLOSED",
    }
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    d = json.loads(result.stdout)["disposition"]
    assert d["workflow_may_continue"] is False
    assert d["write_actions_allowed"] is False


def test_preflight_compact_digest_includes_disposition(
    tmp_path: Path,
) -> None:
    """Compact digest schema includes explicit disposition field."""
    state = _base_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(
        repo,
        env,
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "delivery-start",
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert "disposition" in value
    d = value["disposition"]
    for key in (
        "status",
        "disposition",
        "workflow_may_continue",
        "write_actions_allowed",
        "auto_remediation_allowed",
        "maintainer_action_required",
        "failed_gates",
    ):
        assert key in d, f"disposition missing key: {key}"
