from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    if state.get('remote_error', False):
        sys.stderr.write('remote facts unavailable')
        sys.exit(1)
    if state.get('remote_branch_exists',True):
        out(state.get('remote_branch_tips',{{}}).get(args[-1], state.get('pr',{{}}).get('headRefOid','d'*40))+'\\trefs/heads/'+args[-1])
elif args[:3] == ['show-ref','--verify','--quiet']:
    sys.exit(0 if state.get('local_branch_exists',True) else 1)
elif args[:2] == ['merge-base','--is-ancestor']:
    if state.get('ancestor_error', False):
        sys.stderr.write('cannot determine ancestry')
        sys.exit(2)
    sys.exit(0 if state.get('remote_is_ancestor', True) else 1)
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
    pr=state['pr']
    dump([{{
        'number': pr['number'],
        'state': pr['state'],
        'isDraft': pr['isDraft'],
        'headRefName': pr['headRefName'],
        'headRefOid': pr['headRefOid'],
        'baseRefName': pr['baseRefName'],
        'baseRefOid': pr['baseRefOid'],
        'closingIssuesReferences': pr['closingIssuesReferences'],
    }}])
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
    for relative in (
        "tools/agent_workflow/wsl2_validation_runner.py",
        "tools/agent_workflow/wsl2_validation_profiles.json",
        ".codex/rules/tracequant-wsl-validation.rules",
        "tools/agent_workflow/workflow_validation.py",
        "tools/agent_workflow/workflow_common.py",
        "tools/agent_workflow/wsl2_github_evidence_runner.py",
        "tools/agent_workflow/wsl2_github_evidence_profiles.json",
        ".codex/rules/tracequant-wsl-evidence.rules",
        "tools/agent_workflow/workflow_evidence.py",
        ".agents/skills/task-delivery-runner/SKILL.md",
    ):
        source = Path(__file__).parents[2] / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    bin_dir = _write_fake_tools(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_WORKFLOW_STATE"] = str(state_path)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return repo, state_path, env


def _write_workflow_delivery_artifact(
    repo: Path,
    *,
    base_sha: str,
    head_sha: str,
    main_sha: str,
    branch: str,
    relative_path: str = ".agents/validation.local/wsl2-runs/delivery/result.json",
) -> str:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = (
        "tools/agent_workflow/wsl2_validation_runner.py",
        "tools/agent_workflow/wsl2_validation_profiles.json",
        ".codex/rules/tracequant-wsl-validation.rules",
        "tools/agent_workflow/workflow_validation.py",
        "tools/agent_workflow/workflow_common.py",
    )
    hashes = {
        relative: hashlib.sha256((repo / relative).read_bytes()).hexdigest()
        for relative in identity
    }
    skill_path = ".agents/skills/task-delivery-runner/SKILL.md"
    skill_hash = hashlib.sha256((repo / skill_path).read_bytes()).hexdigest()
    receipt_path = relative_path.replace("/result.json", "/receipt.json")
    artifact = {
        "schema_version": 1,
        "runner_version": "1.3.0",
        "profile": "workflow-delivery",
        "profile_kind": "workflow-phase",
        "base_sha": base_sha,
        "status": "pass",
        "repository": {
            "state": {
                "branch": branch,
                "head_sha": head_sha,
                "origin_main_sha": main_sha,
                "clean": True,
            }
        },
        "commands": [
            {
                "id": "workflow-validation",
                "argv": [
                    "python",
                    "/repo/tools/agent_workflow/workflow_validation.py",
                    "run",
                    "--repo-root",
                    ".",
                    "--phase",
                    "delivery",
                    "--base-sha",
                    base_sha,
                    "--include-skill-validators",
                    "--require-skill-validator",
                ],
                "exit_code": 0,
                "timed_out": False,
                "interrupted": False,
            }
        ],
        "expected_command_count": 1,
        "artifacts": {"result_json": relative_path, "receipt_json": receipt_path},
        "integrity": {
            "verification": "current-worktree-content",
            "repository_head_sha": head_sha,
            "repository_clean": True,
            "runner_path": identity[0],
            "runner_sha256": hashes[identity[0]],
            "profile_spec_path": identity[1],
            "profile_spec_sha256": hashes[identity[1]],
            "rules_path": identity[2],
            "rules_sha256": hashes[identity[2]],
            "workflow_validation_path": identity[3],
            "workflow_validation_sha256": hashes[identity[3]],
            "skill": {"path": skill_path, "sha256": skill_hash},
        },
    }
    payload = json.dumps(artifact, sort_keys=True, indent=2).encode()
    path.write_bytes(payload)
    (repo / receipt_path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation": "workflow-validation-receipt",
                "runner_version": "1.3.0",
                "profile": "workflow-delivery",
                "base_sha": base_sha,
                "status": "pass",
                "result_path": relative_path,
                "result_sha256": hashlib.sha256(payload).hexdigest(),
                "producer": {"files": hashes},
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return relative_path


def _write_push_verification_artifact(
    repo: Path,
    *,
    task: int = 70,
    branch: str = "task-70",
    main_sha: str = "2" * 40,
    head_sha: str = "4" * 40,
    relative_path: str = ".agents/evidence.local/wsl2-github-runs/verify/result.json",
) -> str:
    identity = (
        "tools/agent_workflow/wsl2_github_evidence_runner.py",
        "tools/agent_workflow/wsl2_github_evidence_profiles.json",
        ".codex/rules/tracequant-wsl-evidence.rules",
        "tools/agent_workflow/workflow_evidence.py",
        "tools/agent_workflow/workflow_common.py",
    )
    hashes = {
        relative: hashlib.sha256((repo / relative).read_bytes()).hexdigest()
        for relative in identity
    }
    receipt_path = relative_path.replace("/result.json", "/receipt.json")
    artifact = {
        "schema_version": 1,
        "runner_version": "1.3.0",
        "profile": "delivery",
        "status": "pass",
        "partial": False,
        "identity": {"task": task},
        "git": {
            "current_branch": branch,
            "working_tree_clean": True,
            "current_head": head_sha,
            "origin_main": main_sha,
            "remote_main": main_sha,
            "remote_head": head_sha,
        },
        "push_readiness": {
            "validated_head_sha": head_sha,
            "remote_branch_state": "PRESENT",
            "remote_tip": head_sha,
            "remote_push": "pass",
            "push_action": "none",
            "verify": True,
        },
        "disposition": {
            "workflow_may_continue": True,
            "write_actions_allowed": True,
        },
        "evidence": {"gate_summary": {"failed_gates": [], "unknown_gates": []}},
        "artifacts": {"result_json": relative_path, "receipt_json": receipt_path},
        "integrity": {
            "verification": "current-worktree-content",
            "repository_head_sha": head_sha,
            "repository_clean": True,
            "files": hashes,
        },
    }
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(artifact, sort_keys=True, indent=2).encode()
    path.write_bytes(payload)
    (repo / receipt_path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation": "github-evidence-receipt",
                "runner_version": "1.3.0",
                "profile": "delivery",
                "result_path": relative_path,
                "result_sha256": hashlib.sha256(payload).hexdigest(),
                "invocation": {
                    "task": task,
                    "entry_point": "push-readiness",
                    "branch": branch,
                    "expected_main_sha": main_sha,
                    "expected_base_sha": main_sha,
                    "expected_head_sha": head_sha,
                    "validation_result": ".agents/validation.local/wsl2-runs/verify/result.json",
                    "push_verification_result": None,
                    "verify": True,
                },
                "producer": {"files": hashes},
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return relative_path


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


def test_closeout_accepts_matching_remote_task_branch(
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
    cleanup = value["observed"]["branch_cleanup"]
    assert cleanup["remote_branch_state"] == "PRESENT"
    assert value["gates"]["remote_branch_tip"]["status"] == "pass"


def test_closeout_accepts_auto_deleted_remote_branch_and_allows_cleanup(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    state["remote_branch_exists"] = False
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
    assert first_value["observed"]["branch_cleanup"]["remote_branch_state"] == (
        "ALREADY_DELETED"
    )
    assert first_value["gates"]["remote_branch_tip"]["status"] == "pass"

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
    assert (
        value["observed"]["branch_cleanup"]["cleanup_eligibility"]["status"]
        == "eligible-under-capability-limited-policy"
    )


def test_closeout_rejects_absent_remote_branch_when_pr_is_not_merged(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    state["remote_branch_exists"] = False
    state["pr"].update(state="OPEN", mergeCommit=None, mergedAt=None)
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
    assert value["gates"]["pull_request_merge"]["status"] == "fail"
    assert value["gates"]["remote_branch_tip"]["status"] == "fail"


def test_closeout_rejects_absent_remote_branch_when_local_tip_drifts(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    state["remote_branch_exists"] = False
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
    assert value["gates"]["local_branch_tip"]["status"] == "fail"
    assert value["gates"]["remote_branch_tip"]["status"] == "fail"


def test_closeout_rejects_absent_remote_branch_without_effective_diff_identity(
    tmp_path: Path,
) -> None:
    state = _completed_closeout_state()
    state["remote_branch_exists"] = False
    state["diff_available"] = False
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
    assert value["gates"]["effective_diff_identity"]["status"] == "unknown"
    assert value["gates"]["remote_branch_tip"]["status"] == "fail"


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
    bootstrap_verify: bool = False,
) -> dict[str, Any]:
    args = [
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
    ]
    if bootstrap_verify:
        args.append("--bootstrap-verify")
    result = _run(repo, env, *args)
    assert result.returncode == 0, result.stderr
    return cast(dict[str, Any], json.loads(result.stdout))


def _safe_bootstrap_state() -> dict[str, Any]:
    state = _base_state()
    state.update(
        branch="main",
        git_head=SHA40,
        local_main=SHA40,
        origin_main=SHA40,
        local_branch_exists=False,
        remote_branch_exists=False,
        status=[],
        extra_worktree_branches=[],
    )
    return state


def _push_readiness_state(
    *,
    branch: str = "task/70-push-readiness",
    head: str = "4" * 40,
    remote_tip: str | None = None,
) -> dict[str, Any]:
    state = _base_state()
    state.update(
        branch=branch,
        git_head=head,
        local_main=SHA40,
        origin_main=SHA40,
        local_branch_exists=True,
        local_branch_tips={branch: head},
        branch_base=SHA40,
        remote_branch_exists=remote_tip is not None,
        remote_branch_tips={} if remote_tip is None else {branch: remote_tip},
        status=[],
    )
    return state


def _run_push_readiness(
    repo: Path,
    env: dict[str, str],
    artifact: str,
    *,
    branch: str = "task/70-push-readiness",
    head: str = "4" * 40,
    verify: bool = False,
) -> dict[str, Any]:
    args = [
        "delivery-preflight",
        "--task",
        "70",
        "--expected-main-sha",
        SHA40,
        "--entry-point",
        "push-readiness",
        "--branch",
        branch,
        "--expected-base-sha",
        SHA40,
        "--expected-head-sha",
        head,
        "--validation-result",
        artifact,
    ]
    if verify:
        args.append("--verify")
    result = _run(repo, env, *args)
    assert result.returncode == 0, result.stderr
    return cast(dict[str, Any], json.loads(result.stdout))


def test_push_readiness_remote_absent_authorizes_create_remote(tmp_path: Path) -> None:
    state = _push_readiness_state()
    repo, _, env = _write_repo(tmp_path, state)
    artifact = _write_workflow_delivery_artifact(
        repo,
        base_sha=SHA40,
        head_sha="4" * 40,
        main_sha=SHA40,
        branch="task/70-push-readiness",
    )
    value = _run_push_readiness(repo, env, artifact)
    assert value["gates"]["validated_head_binding"]["status"] == "pass"
    assert value["gates"]["remote_push"]["status"] == "pass"
    assert value["observed"]["push_readiness"]["push_action"] == "create_remote"
    assert value["disposition"]["write_actions_allowed"] is True


def test_push_readiness_equal_remote_is_idempotent(tmp_path: Path) -> None:
    state = _push_readiness_state(remote_tip="4" * 40)
    repo, _, env = _write_repo(tmp_path, state)
    artifact = _write_workflow_delivery_artifact(
        repo,
        base_sha=SHA40,
        head_sha="4" * 40,
        main_sha=SHA40,
        branch="task/70-push-readiness",
    )
    value = _run_push_readiness(repo, env, artifact)
    assert value["observed"]["push_readiness"]["push_action"] == "none"
    assert value["gates"]["remote_push"]["status"] == "pass"


def test_push_readiness_predecessor_authorizes_fast_forward(tmp_path: Path) -> None:
    state = _push_readiness_state(remote_tip="3" * 40)
    repo, state_path, env = _write_repo(tmp_path, state)
    state["remote_is_ancestor"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    artifact = _write_workflow_delivery_artifact(
        repo,
        base_sha=SHA40,
        head_sha="4" * 40,
        main_sha=SHA40,
        branch="task/70-push-readiness",
    )
    value = _run_push_readiness(repo, env, artifact)
    assert value["observed"]["push_readiness"]["push_action"] == "fast_forward_update"
    assert value["gates"]["remote_push"]["status"] == "pass"


def test_push_readiness_divergence_fails_closed(tmp_path: Path) -> None:
    state = _push_readiness_state(remote_tip="3" * 40)
    state["remote_is_ancestor"] = False
    repo, state_path, env = _write_repo(tmp_path, state)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    artifact = _write_workflow_delivery_artifact(
        repo,
        base_sha=SHA40,
        head_sha="4" * 40,
        main_sha=SHA40,
        branch="task/70-push-readiness",
    )
    value = _run_push_readiness(repo, env, artifact)
    assert value["gates"]["remote_push"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


@pytest.mark.parametrize(
    "mutations, expected_gate",
    [
        ({"status": [" M drift.py"]}, "worktree_state_compatible"),
        ({"git_head": "5" * 40}, "local_head_validated"),
        ({"branch": "task/70-other"}, "current_branch"),
        ({"branch_base": "9" * 40}, "branch_base"),
        ({"remote_error": True}, "remote_branch"),
    ],
)
def test_push_readiness_fail_closed_matrix(
    tmp_path: Path, mutations: dict[str, Any], expected_gate: str
) -> None:
    state = _push_readiness_state()
    state.update(mutations)
    repo, state_path, env = _write_repo(tmp_path, state)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    artifact = _write_workflow_delivery_artifact(
        repo,
        base_sha=SHA40,
        head_sha="4" * 40,
        main_sha=SHA40,
        branch="task/70-push-readiness",
    )
    value = _run_push_readiness(repo, env, artifact)
    assert value["gates"][expected_gate]["status"] in {"fail", "unknown"}
    assert value["disposition"]["write_actions_allowed"] is False


def test_push_readiness_verify_requires_remote_validated_head(tmp_path: Path) -> None:
    state = _push_readiness_state(remote_tip="4" * 40)
    repo, _, env = _write_repo(tmp_path, state)
    artifact = _write_workflow_delivery_artifact(
        repo,
        base_sha=SHA40,
        head_sha="4" * 40,
        main_sha=SHA40,
        branch="task/70-push-readiness",
    )
    value = _run_push_readiness(repo, env, artifact, verify=True)
    assert value["gates"]["remote_push"]["status"] == "pass"
    assert value["observed"]["push_readiness"]["verify"] is True


def test_push_readiness_verify_rejects_remote_mismatch(tmp_path: Path) -> None:
    state = _push_readiness_state(remote_tip="3" * 40)
    repo, _, env = _write_repo(tmp_path, state)
    artifact = _write_workflow_delivery_artifact(
        repo,
        base_sha=SHA40,
        head_sha="4" * 40,
        main_sha=SHA40,
        branch="task/70-push-readiness",
    )
    value = _run_push_readiness(repo, env, artifact, verify=True)
    assert value["gates"]["remote_push"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_push_readiness_rejects_validation_artifact_head_binding_drift(
    tmp_path: Path,
) -> None:
    state = _push_readiness_state()
    repo, _, env = _write_repo(tmp_path, state)
    artifact = _write_workflow_delivery_artifact(
        repo,
        base_sha=SHA40,
        head_sha="5" * 40,
        main_sha=SHA40,
        branch="task/70-push-readiness",
    )
    value = _run_push_readiness(repo, env, artifact)
    assert value["gates"]["validated_head_binding"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_push_readiness_rejects_handcrafted_validation_artifact(
    tmp_path: Path,
) -> None:
    state = _push_readiness_state()
    repo, _, env = _write_repo(tmp_path, state)
    relative_path = ".agents/validation.local/handcrafted/result.json"
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "profile": "workflow-delivery",
                "base_sha": SHA40,
                "status": "pass",
                "repository": {
                    "state": {
                        "branch": "task/70-push-readiness",
                        "head_sha": "4" * 40,
                        "origin_main_sha": SHA40,
                        "clean": True,
                    }
                },
                "artifacts": {"result_json": relative_path},
                "integrity": {
                    "repository_head_sha": "4" * 40,
                    "repository_clean": True,
                },
            }
        ),
        encoding="utf-8",
    )
    value = _run_push_readiness(repo, env, relative_path)
    assert value["gates"]["validated_head_binding"]["status"] in {"fail", "unknown"}
    assert value["disposition"]["write_actions_allowed"] is False


def test_pr_readiness_rejects_without_successful_push_verification(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["git_head"] = "4" * 40
    repo, _, env = _write_repo(tmp_path, state)
    relative_path = ".agents/evidence.local/wsl2-github-runs/failed/result.json"
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
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
        "--push-verification-result",
        relative_path,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["push_verification_binding"]["status"] in {"fail", "unknown"}
    assert value["disposition"]["write_actions_allowed"] is False


def test_pr_readiness_accepts_exact_successful_push_verification(
    tmp_path: Path,
) -> None:
    state = _base_state()
    state["git_head"] = "4" * 40
    repo, _, env = _write_repo(tmp_path, state)
    verification = _write_push_verification_artifact(repo)
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
        "--push-verification-result",
        verification,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["push_verification_binding"]["status"] == "pass"
    assert value["disposition"]["write_actions_allowed"] is True


def test_implementation_missing_canonical_branch_authorizes_bootstrap(
    tmp_path: Path,
) -> None:
    repo, _, env = _write_repo(tmp_path, _safe_bootstrap_state())
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_exists"]["status"] == "pass"
    assert value["gates"]["branch_identity"]["status"] == "pass"
    assert value["gates"]["branch_remote"]["status"] == "pass"
    assert value["gates"]["branch_base"]["status"] == "pass"
    assert value["gates"]["branch_bootstrap"]["status"] == "pass"
    assert "creation authorized" in value["gates"]["branch_bootstrap"]["detail"]
    assert value["disposition"]["write_actions_allowed"] is True


def test_implementation_existing_valid_branch_is_reuse_authorized(
    tmp_path: Path,
) -> None:
    state = _safe_bootstrap_state()
    state.update(
        local_branch_exists=True,
        remote_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": "4" * 40},
        remote_branch_tips={"task/70-bootstrap": "4" * 40},
        branch_base=SHA40,
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_state"]["status"] == "pass"
    assert "reuse authorized" in value["gates"]["branch_state"]["detail"]
    assert value["gates"]["branch_bootstrap"]["status"] == "pass"
    assert value["gates"]["branch_base"]["status"] == "pass"
    assert value["disposition"]["status"] == "pass"


def test_implementation_existing_dirty_branch_fails_closed(tmp_path: Path) -> None:
    state = _safe_bootstrap_state()
    state.update(
        local_branch_exists=True,
        remote_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": "4" * 40},
        remote_branch_tips={"task/70-bootstrap": "4" * 40},
        branch_base=SHA40,
        status=[" M unrelated.py"],
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert "clean worktree" in value["gates"]["branch_bootstrap"]["detail"]
    assert value["disposition"]["write_actions_allowed"] is False


def test_implementation_existing_numeric_branch_can_only_be_reused(
    tmp_path: Path,
) -> None:
    state = _safe_bootstrap_state()
    state.update(
        local_branch_exists=True,
        remote_branch_exists=True,
        local_branch_tips={"task-70": "4" * 40},
        remote_branch_tips={"task-70": "4" * 40},
        branch_base=SHA40,
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env, branch="task-70")
    assert value["gates"]["branch_identity"]["status"] == "pass"
    assert value["gates"]["branch_bootstrap"]["status"] == "pass"


@pytest.mark.parametrize("branch", ["task-70", "task/70", "foreign/branch"])
def test_implementation_missing_noncanonical_or_unproven_branch_fails_closed(
    tmp_path: Path, branch: str
) -> None:
    repo, _, env = _write_repo(tmp_path, _safe_bootstrap_state())
    value = _run_implementation_preflight(repo, env, branch=branch)
    assert value["disposition"]["write_actions_allowed"] is False
    assert value["gates"]["branch_state"]["status"] == "fail"
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    if branch == "foreign/branch":
        assert value["gates"]["branch_identity"]["status"] == "fail"
    else:
        assert "canonical" in value["gates"]["branch_bootstrap"]["detail"]


def test_implementation_existing_wrong_base_fails_closed(tmp_path: Path) -> None:
    state = _safe_bootstrap_state()
    state.update(
        local_branch_exists=True,
        remote_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": "4" * 40},
        remote_branch_tips={"task/70-bootstrap": "4" * 40},
        branch_base="9" * 40,
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_base"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_implementation_dirty_missing_branch_fails_closed(tmp_path: Path) -> None:
    state = _safe_bootstrap_state()
    state["status"] = [" M unrelated.py"]
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert "clean worktree" in value["gates"]["branch_bootstrap"]["detail"]
    assert value["disposition"]["write_actions_allowed"] is False


def test_implementation_remote_conflict_on_missing_branch_fails_closed(
    tmp_path: Path,
) -> None:
    state = _safe_bootstrap_state()
    state["remote_branch_exists"] = True
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert "ownership is ambiguous" in value["gates"]["branch_bootstrap"]["detail"]


def test_implementation_worktree_ownership_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    state = _safe_bootstrap_state()
    state["extra_worktree_branches"] = ["task/70-bootstrap"]
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert "another worktree" in value["gates"]["branch_bootstrap"]["detail"]
    assert value["disposition"]["write_actions_allowed"] is False


def test_implementation_existing_remote_tip_drift_fails_closed(tmp_path: Path) -> None:
    state = _safe_bootstrap_state()
    state.update(
        local_branch_exists=True,
        remote_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": "4" * 40},
        remote_branch_tips={"task/70-bootstrap": "5" * 40},
        branch_base=SHA40,
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env)
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert "tips differ" in value["gates"]["branch_bootstrap"]["detail"]
    assert value["disposition"]["write_actions_allowed"] is False


def test_bootstrap_then_reenter_implementation_verifies_and_continues(
    tmp_path: Path,
) -> None:
    state = _safe_bootstrap_state()
    repo, state_path, env = _write_repo(tmp_path, state)
    initial = _run_implementation_preflight(repo, env)
    assert initial["gates"]["branch_bootstrap"]["status"] == "pass"

    state.update(
        branch="task/70-bootstrap",
        git_head=SHA40,
        local_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": SHA40},
        branch_base=SHA40,
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    verified = _run_implementation_preflight(repo, env, bootstrap_verify=True)
    assert verified["gates"]["branch_bootstrap"]["status"] == "pass"
    assert verified["gates"]["bootstrap_head"]["status"] == "pass"
    assert verified["gates"]["worktree_state_compatible"]["status"] == "pass"
    assert verified["disposition"]["workflow_may_continue"] is True


def test_bootstrap_verification_requires_clean_worktree(tmp_path: Path) -> None:
    state = _safe_bootstrap_state()
    state.update(
        branch="task/70-bootstrap",
        git_head=SHA40,
        local_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": SHA40},
        branch_base=SHA40,
        status=[" M drift.py"],
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env, bootstrap_verify=True)
    assert value["gates"]["worktree_state_compatible"]["status"] == "fail"
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
    assert value["disposition"]["write_actions_allowed"] is False


def test_bootstrap_verification_detects_unexpected_drift(tmp_path: Path) -> None:
    state = _safe_bootstrap_state()
    state.update(
        branch="task/70-bootstrap",
        git_head="4" * 40,
        local_branch_exists=True,
        local_branch_tips={"task/70-bootstrap": "4" * 40},
        branch_base=SHA40,
    )
    repo, _, env = _write_repo(tmp_path, state)
    value = _run_implementation_preflight(repo, env, bootstrap_verify=True)
    assert value["gates"]["bootstrap_head"]["status"] == "fail"
    assert value["gates"]["branch_bootstrap"]["status"] == "fail"
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
    verification = ".agents/evidence.local/wsl2-github-runs/verify/result.json"
    (repo / verification).parent.mkdir(parents=True, exist_ok=True)
    (repo / verification).write_text("{}", encoding="utf-8")
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
        "--push-verification-result",
        verification,
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


def test_worktree_implementation_allows_task_dirty(tmp_path: Path) -> None:
    """implementation with Task-owned dirty worktree → pass."""
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
    gate = json.loads(result.stdout)["gates"]["worktree_state_compatible"]
    assert gate["status"] == "pass"
    assert gate["dirty_allowed"] is True


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
    verification = ".agents/evidence.local/wsl2-github-runs/verify/result.json"
    (repo / verification).parent.mkdir(parents=True, exist_ok=True)
    (repo / verification).write_text("{}", encoding="utf-8")
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
        "--push-verification-result",
        verification,
    )
    assert result.returncode == 0, result.stderr
    gate = json.loads(result.stdout)["gates"]["worktree_state_compatible"]
    assert gate["status"] == "fail"
    assert gate["dirty_allowed"] is False


def test_review_remediation_accepts_read_only_review_without_github_submission(
    tmp_path: Path,
) -> None:
    """A bounded read-only review handoff must not require a GitHub Review object."""
    state = _base_state()
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    state["pr"]["headRepository"] = {"nameWithOwner": "owner/repo"}
    state["pr"]["reviews"] = []
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
    value = json.loads(result.stdout)
    gate = value["gates"]["review_conclusion"]
    assert gate["status"] == "pass"
    assert "GitHub submitted Review not required" in gate["detail"]
    assert "observed_reviews=0" in gate["detail"]
    assert value["disposition"]["workflow_may_continue"] is True
    assert value["disposition"]["write_actions_allowed"] is True


def test_review_remediation_still_fails_on_reviewed_head_mismatch(
    tmp_path: Path,
) -> None:
    """Removing the GitHub-Review dependency must not weaken exact head locking."""
    state = _base_state()
    state["issues"]["70"]["projectItems"] = [{"status": {"name": "Review"}}]
    state["pr"]["headRepository"] = {"nameWithOwner": "owner/repo"}
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
        "5" * 40,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["gates"]["review_conclusion"]["status"] == "pass"
    assert value["gates"]["pr_head_sha"]["status"] == "fail"
    assert value["disposition"]["workflow_may_continue"] is False
    assert value["disposition"]["write_actions_allowed"] is False


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
            "push-readiness",
            "pr-readiness",
            "review-remediation",
        ),
    ):
        state = _base_state()
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        if entry_point == "push-readiness":
            state["git_head"] = "4" * 40
        extra: list[str] = []
        if entry_point in (
            "implementation",
            "final-validation",
            "push-readiness",
            "pr-readiness",
        ):
            extra = ["--branch", "task-70", "--expected-base-sha", SHA40]
        if entry_point in ("final-validation", "pr-readiness", "push-readiness"):
            extra.extend(["--expected-head-sha", "4" * 40])
        if entry_point == "push-readiness":
            extra.extend(["--validation-result", "__push_artifact__"])
        if entry_point == "pr-readiness":
            verification = ".agents/evidence.local/wsl2-github-runs/verify/result.json"
            extra.extend(["--push-verification-result", verification])
        if entry_point == "review-remediation":
            extra = [
                "--pr",
                "71",
                "--expected-base-sha",
                SHA40,
                "--expected-head-sha",
                "4" * 40,
            ]
        repo, _, env = _write_repo(case_dir, state)
        if entry_point == "pr-readiness":
            verification_path = repo / extra[-1]
            verification_path.parent.mkdir(parents=True, exist_ok=True)
            verification_path.write_text("{}", encoding="utf-8")
        if entry_point == "push-readiness":
            artifact = _write_workflow_delivery_artifact(
                repo,
                base_sha=SHA40,
                head_sha="4" * 40,
                main_sha=SHA40,
                branch="task-70",
            )
            extra[-1] = artifact
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
