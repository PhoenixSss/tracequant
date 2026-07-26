from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).parents[2] / "tools" / "agent_workflow" / "workflow_evidence.py"
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)


def _write_fake_tools(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
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
elif args[:2] == ['rev-parse','refs/remotes/origin/main']: out(state.get('origin_main','b'*40))
elif args[:2] == ['status','--short']: out('\\n'.join(state.get('status',[])))
elif args[:3] == ['diff','--cached','--name-only']: out('\\n'.join(state.get('staged',[])))
elif args[:2] == ['diff','--name-only']: out('\\n'.join(state.get('changed',[])))
elif args[:3] == ['worktree','list','--porcelain']: out('worktree <repo>\\nHEAD '+state.get('git_head','a'*40)+'\\nbranch refs/heads/main')
elif args[:3] == ['log','-1','--format=%H']: out(state.get('runner_source_sha','c'*40))
elif args[:2] == ['ls-remote','--heads']:
    if state.get('remote_branch_exists',True): out(state.get('pr',{{}}).get('headRefOid','d'*40)+'\\trefs/heads/'+args[-1])
elif args[:3] == ['show-ref','--verify','--quiet']:
    sys.exit(0 if state.get('local_branch_exists',True) else 1)
elif args[:2] == ['merge-base','--is-ancestor']:
    sys.exit(0 if state.get('merge_on_main', True) else 1)
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
elif args[:2] == ['pr','diff']:
    sys.stdout.write(state.get('diff','diff --git a/a b/a\\n'))
elif args[:2] == ['api','graphql']:
    query=' '.join(args)
    if 'pullRequest(number' in query:
        dump({{'data':{{'repository':{{'pullRequest':{{'reviewThreads':state.get('threads',{{'nodes':[],'pageInfo':{{'hasNextPage':False}}}})}}}}}}}})
    else:
        number_value=None
        for arg in args:
            if arg.startswith('number='):
                number_value=arg.split('=',1)[1]
        relation=state.get('relationships_by_issue',{{}}).get(number_value, state.get('relationships'))
        dump({{'data':{{'repository':{{'issue':relation}}}}}})
elif args and args[0] == 'api' and 'required_status_checks' in args[1]:
    mode=state.get('required_checks_mode','available')
    if mode == '403': sys.stderr.write('HTTP 403 Resource not accessible by integration'); sys.exit(1)
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
    return bin_dir


def _base_state() -> dict[str, Any]:
    task_issue = {
        "number": 70,
        "title": "[Task] Optimize workflow",
        "state": "OPEN",
        "labels": [{"name": "type:task"}, {"name": "codex:ready"}],
        "projectItems": [{"status": {"name": "Review"}}],
        "url": "https://github.com/owner/repo/issues/70",
        "closedAt": None,
        "closedByPullRequestsReferences": [],
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
            {"number": 67, "state": "MERGED", "mergedAt": "2026-07-26T00:00:00Z", "url": "https://github.com/owner/repo/pull/67"}
        ],
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
                "parent": {"number": 62, "title": "[Feature] Workflow", "state": "OPEN"},
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


def _write_repo(tmp_path: Path, state: dict[str, Any]) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(
        ".agents/evidence.local/\n.agents/validation.local/\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_WORKFLOW_STATE"] = str(state_path)
    return repo, state_path, env


def _run(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
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


def test_plan_limit_is_distinct_from_success(tmp_path: Path) -> None:
    state = _base_state()
    state["required_checks_mode"] = "403"
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
                {"number": 63, "title": "[Task] Child", "state": "CLOSED", "labels": {"nodes": [{"name": "type:task"}]}}
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
    state["issues"]["64"] = dict(state["issues"]["63"], number=64, title="[Task] Another")
    state["relationships"]["subIssues"]["nodes"].append(
        {"number": 64, "title": "[Task] Another", "state": "CLOSED", "labels": {"nodes": [{"name": "type:task"}]}}
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


def test_closeout_accepts_merge_reachable_from_later_origin_main(tmp_path: Path) -> None:
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


def test_missing_github_fact_never_becomes_false_pass(tmp_path: Path) -> None:
    state = _base_state()
    del state["issues"]["70"]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "delivery-preflight", "--task", "70")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["issue_available"]["status"] == "unknown"
    assert value["warnings"]


def test_relationship_unavailable_keeps_blocker_gate_unknown(tmp_path: Path) -> None:
    state = _base_state()
    state["relationships"] = None
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "delivery-preflight", "--task", "70")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["formal_blockers"]["status"] == "unknown"


def test_truncated_review_threads_do_not_pass(tmp_path: Path) -> None:
    state = _base_state()
    state["threads"]["pageInfo"]["hasNextPage"] = True
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "pr-review-snapshot", "--task", "70", "--pr", "71")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["unresolved_threads"]["status"] == "unknown"


def test_recheck_detects_issue_content_and_review_metadata_drift(tmp_path: Path) -> None:
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


def test_feature_recheck_detects_child_lifecycle_drift_without_set_change(tmp_path: Path) -> None:
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
    result = _run(repo, env, "delivery-preflight", "--task", "70")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["origin_fetch"]["status"] == "unknown"
    assert any(item["command_id"] == "git-fetch-origin" for item in value["warnings"])
    serialized = json.dumps(value)
    assert "C:/Users" not in serialized
    assert "/home/maple" not in serialized
    assert "<absolute-path-redacted>" in serialized
