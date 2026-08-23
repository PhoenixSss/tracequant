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
    if state.get('remote_branch_exists',True):
        out(state.get('remote_branch_tips',{{}}).get(args[-1], state.get('pr',{{}}).get('headRefOid','d'*40))+'\\trefs/heads/'+args[-1])
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
elif args[:2] == ['pr','diff']:
    if not state.get('diff_available', True):
        sys.stderr.write('diff unavailable')
        sys.exit(1)
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


LEGACY_TASK_COMMANDS = (
    "delivery-preflight",
    "delivery-readiness",
    "pr-review-snapshot",
    "pr-review-recheck",
    "closeout-plan",
    "closeout-final",
)
LEGACY_TASK_CONTROL_TOKENS = (
    "write_actions_allowed",
    "review-remediation",
    "_compute_preflight_disposition",
    "_delivery_preflight",
    "_task_pr_snapshot",
    "_closeout_plan",
)


def _feature_state() -> dict[str, Any]:
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
        "timelineItems": {"nodes": [], "pageInfo": {"hasPreviousPage": False}},
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
        "blockedBy": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "blocking": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }
    return state


def test_help_exposes_feature_audit_only() -> None:
    for command in ("feature-audit-snapshot", "feature-audit-recheck"):
        result = subprocess.run(
            [PYTHON, str(SCRIPT), command, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr

    top = subprocess.run(
        [PYTHON, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert top.returncode == 0, top.stderr
    for command in LEGACY_TASK_COMMANDS:
        assert command not in top.stdout


def test_legacy_task_control_cli_and_semantics_are_physically_removed() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in (*LEGACY_TASK_COMMANDS, *LEGACY_TASK_CONTROL_TOKENS):
        assert token not in source


def test_feature_snapshot_collects_direct_child_and_pr_evidence(tmp_path: Path) -> None:
    state = _feature_state()
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "feature-audit-snapshot", "--feature", "62")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["operation"] == "feature-audit-snapshot"
    assert value["gates"]["formal_blockers"]["status"] == "pass"
    child = value["observed"]["direct_children"]["items"][0]
    assert child["relationship_evidence"]["parent"]["number"] == 62
    assert child["pull_request_evidence"]["items"][0]["number"] == 67
    assert child["pull_request_evidence"]["items"][0]["checks_all_success"] is True


def test_feature_recheck_detects_direct_child_set_drift(tmp_path: Path) -> None:
    state = _feature_state()
    repo, state_path, env = _write_repo(tmp_path, state)
    first = _run(repo, env, "feature-audit-snapshot", "--feature", "62")
    assert first.returncode == 0, first.stderr
    first_value = json.loads(first.stdout)

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
    assert value["gates"]["feature_audit_stability"]["status"] == "fail"
    assert "direct_child_set_digest" in value["stability"]["changed_fields"]["items"]


def test_feature_recheck_detects_child_lifecycle_drift_without_set_change(
    tmp_path: Path,
) -> None:
    state = _feature_state()
    repo, state_path, env = _write_repo(tmp_path, state)
    first = _run(repo, env, "feature-audit-snapshot", "--feature", "62")
    assert first.returncode == 0, first.stderr
    snapshot_id = json.loads(first.stdout)["snapshot_id"]

    state["issues"]["63"]["projectItems"] = [{"status": {"name": "Review"}}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    second = _run(repo, env, "feature-audit-recheck", "--snapshot-id", snapshot_id)
    assert second.returncode == 0, second.stderr
    value = json.loads(second.stdout)
    assert value["gates"]["feature_audit_stability"]["status"] == "fail"
    assert (
        "direct_child_evidence_digest" in value["stability"]["changed_fields"]["items"]
    )


def test_feature_blocker_gate_fails_for_open_blocker(tmp_path: Path) -> None:
    state = _feature_state()
    state["relationships"]["blockedBy"]["nodes"] = [
        {"number": 72, "title": "[Task] Dependency", "state": "OPEN"}
    ]
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "feature-audit-snapshot", "--feature", "62")
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["formal_blockers"]
    assert gate["status"] == "fail"
    assert "unresolved=1" in gate["detail"]


def test_feature_blocker_gate_is_unknown_when_relationships_unavailable(
    tmp_path: Path,
) -> None:
    state = _feature_state()
    state["relationships"] = None
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "feature-audit-snapshot", "--feature", "62")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["formal_blockers"]["status"] == "unknown"


def test_feature_fetch_failure_is_explicit_unknown_gate(tmp_path: Path) -> None:
    state = _feature_state()
    state["fetch_ok"] = False
    repo, _, env = _write_repo(tmp_path, state)
    result = _run(repo, env, "feature-audit-snapshot", "--feature", "62")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["origin_fetch"]["status"] == "unknown"
    assert any(item["command_id"] == "git-fetch-origin" for item in value["warnings"])
    serialized = json.dumps(value)
    assert "C:/Users" not in serialized
    assert "/home/maple" not in serialized
    assert "<absolute-path-redacted>" in serialized
