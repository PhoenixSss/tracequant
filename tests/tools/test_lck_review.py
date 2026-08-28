# ruff: noqa: E402, I001

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import (
    Any,
    cast,
)

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from lck_core import (  # type: ignore[import-not-found]  # noqa: E402
    eligibility as lck_eligibility,
    models as lck_models,
    review as lck_review,
    review_workspace as lck_review_workspace,
    state as lck_state,
    validation as lck_validation,
)
from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandResult,
    CommandRunner,
    print_json,
)
from lck_test_support import (  # noqa: E402
    FakeRunner,
    FakeReviewChecks,
    FakeReviewValidation,
    FakeReviewWorkspace,
    FakeWorkspaceRunner,
    StaticResolver,
    SHA,
    _install_facts,
    _issue,
    _open_pr,
    _relationships,
    _resolver,
    _review_guard,
    _review_identity_value,
    _review_state,
    _task_contract,
    _write_required_checks_workflow,
)


def test_review_prepare_rejects_draft_open_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch, local_branches={branch}, remote_branches={branch: SHA}
    )
    issue = _issue()
    issue["project_status"] = "Review"
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        open_pr=_open_pr(branch, is_draft=True),
    )

    state = _resolver(fake).resolve(159)
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state,
        lck_models.Phase.REVIEW_PREPARE,
    )

    assert not decision.eligible
    assert any("non-Draft" in reason for reason in decision.reasons)


def test_delivery_complete_allows_review_status_for_partial_effect_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    fake = FakeRunner(
        branch=branch,
        local_branches={branch},
        remote_branches={branch: SHA},
    )
    issue = _issue()
    issue["project_status"] = "Review"
    _install_facts(
        monkeypatch,
        fake,
        issue=issue,
        open_pr=_open_pr(branch),
    )

    state = _resolver(fake).resolve(159)
    decision = lck_eligibility.PhaseEligibilityResolver().resolve(
        state,
        lck_models.Phase.DELIVERY_COMPLETE,
    )

    assert decision.eligible


def test_review_prepare_builds_context_only_from_live_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    identity = _review_identity_value()
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    workspace = FakeReviewWorkspace(tmp_path / "review-root")
    checks = FakeReviewChecks()
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)

    context = lck_review.ReviewPreparer(
        resolver,
        validation=cast(Any, FakeReviewValidation()),
        checks_gate=cast(Any, checks),
        workspace=cast(Any, workspace),
        store=store,
    ).prepare(159)

    value = context.to_dict()
    assert value["review_target"]["head_sha"] == SHA
    assert value["review_target"]["base_sha"] == SHA
    assert value["task_contract"]["body"] == "Task Contract"
    assert value["workspace_mode"] == "implementation-read-only"
    assert value["mechanical_authority"].startswith("live Git/GitHub")
    assert workspace.sealed == [tmp_path / "review-root"]
    assert store.guard_path(context.review_id).is_file()
    inflight = json.loads(
        store.review_prepare_inflight_path(159).read_text(encoding="utf-8")
    )
    assert inflight["state"] == "handed-off"
    assert inflight["review_id"] == context.review_id
    assert checks.calls == 1


def test_review_prepare_allows_pending_checks_for_parallel_semantic_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    identity = _review_identity_value()
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )

    class PendingChecks(FakeReviewChecks):
        def evaluate(self, _snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
            raise AssertionError("Review Prepare must not require passing checks")

    checks = PendingChecks()
    context = lck_review.ReviewPreparer(
        resolver,
        validation=cast(Any, FakeReviewValidation()),
        checks_gate=cast(Any, checks),
        workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
        store=lck_review_workspace.ReviewInvocationStore(tmp_path),
    ).prepare(159)

    assert context.checks["status"] == "observed"
    assert context.checks["check_state"] == "pending"
    assert checks.calls == 1


def test_review_prepare_emits_bounded_progress_without_changing_final_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    identity = _review_identity_value()
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    context = lck_review.ReviewPreparer(
        resolver,
        validation=cast(Any, FakeReviewValidation()),
        checks_gate=cast(Any, FakeReviewChecks()),
        workspace=cast(Any, FakeReviewWorkspace(tmp_path / "review-root")),
        store=store,
    ).prepare(159)

    print_json(context.to_dict())
    captured = capsys.readouterr()
    final_result = json.loads(captured.out)
    progress = [json.loads(line) for line in captured.err.splitlines() if line.strip()]

    assert final_result == context.to_dict()
    assert progress
    assert progress[0]["event"] == "started"
    assert any(item["event"] == "completed" for item in progress)
    assert {item["operation"] for item in progress} == {"review-prepare"}
    assert {item["stage"] for item in progress} >= {
        "initializing",
        "formal-validation",
        "handoff",
    }
    assert all(
        item["kind"] == "workflow-progress"
        and item["authority"] == "non-authoritative observability only"
        for item in progress
    )
    assert all("Task Contract" not in line for line in captured.err.splitlines())
    assert all(
        "snapshot" not in line and "diff" not in line
        for line in captured.err.splitlines()
    )


def test_review_prepare_freezes_authority_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    identity = _review_identity_value()
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )

    class DraftingValidation(FakeReviewValidation):
        def run(self, _root: Path, _base: str, _head: str) -> dict[str, Any]:
            assert state.open_pr is not None
            state.open_pr["isDraft"] = True
            return super().run(_root, _base, _head)

    workspace = FakeReviewWorkspace(tmp_path / "review-root")
    context = lck_review.ReviewPreparer(
        resolver,
        validation=cast(Any, DraftingValidation()),
        checks_gate=cast(Any, FakeReviewChecks()),
        workspace=cast(Any, workspace),
        store=lck_review_workspace.ReviewInvocationStore(tmp_path),
    ).prepare(159)

    assert context.identity == identity
    assert workspace.sealed == [tmp_path / "review-root"]


def test_review_complete_acquires_one_fresh_snapshot_and_accepts_unchanged_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value()
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(
        review_id,
        _review_guard(identity, review_root=review_root),
    )
    workspace = FakeReviewWorkspace(review_root)

    result = lck_review.ReviewCompleter(
        resolver,
        checks_gate=cast(Any, FakeReviewChecks()),
        store=store,
        workspace=cast(Any, workspace),
    ).complete(159, review_id, verdict="PASS")

    assert result.status == "READY_FOR_MERGE_PREFLIGHT"
    assert result.identity == identity
    assert resolver.calls == 1
    record = store.read_record(159, review_id)
    assert record["review_snapshot"]["operation"] == "review-prepare"
    assert record["completion_snapshot"]["operation"] == "review-complete"
    assert "fresh Review Complete snapshot matched" in record["authority_note"]
    assert workspace.ready_checked == [review_root]
    assert workspace.removed == [review_root]


def test_review_complete_acquires_only_review_complete_fact_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "task/159-lck-core-live-state-resolution"
    issue = _issue()
    issue.update({"project_status": "Review", "body_sha256": "d" * 64})
    contract = _task_contract(issue)
    contract["body_sha256"] = "d" * 64
    pr = _open_pr(branch)
    pr["statusCheckRollup"] = [{"name": "quality", "conclusion": "SUCCESS"}]
    observations: dict[str, list[Any]] = {
        "issue": [],
        "branches": [],
        "pr": [],
    }

    monkeypatch.setattr(lck_state, "_repository_slug", lambda *_args: "owner/repo")

    def issue_query(*args: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        observations["issue"].append(tuple(args[-3:]))
        return issue, contract

    def task_branches(
        _self: Any,
        _task: int,
        _warnings: list[dict[str, Any]],
        *,
        include_local: bool,
        include_remote: bool,
    ) -> tuple[set[str], dict[str, str], bool]:
        observations["branches"].append((include_local, include_remote))
        return set(), {branch: SHA}, True

    def open_pr_query(*args: Any) -> dict[str, Any]:
        observations["pr"].append(tuple(args[-3:]))
        return pr

    monkeypatch.setattr(lck_state, "_issue_view_with_contract", issue_query)
    monkeypatch.setattr(
        lck_state, "_relationship_snapshot", lambda *_args: _relationships()
    )
    monkeypatch.setattr(lck_state.LiveStateResolver, "_task_branches", task_branches)
    monkeypatch.setattr(lck_state, "resolve_open_pr", open_pr_query)
    monkeypatch.setattr(
        lck_state,
        "list_matching_prs",
        lambda *_args, **_kwargs: pytest.fail("Review Complete queried PR history"),
    )
    monkeypatch.setattr(
        lck_state,
        "_git_snapshot",
        lambda *_args, **_kwargs: pytest.fail(
            "Review Complete queried source workspace Git facts"
        ),
    )

    snapshot = lck_state.OperationSnapshotBuilder(
        lck_state.LiveStateResolver(tmp_path, runner=cast(Any, FakeRunner()))
    ).acquire(159, operation="review-complete")

    assert snapshot.state.status is lck_models.ResolutionStatus.RESOLVED
    assert snapshot.fact_profile == "review-complete"
    assert observations == {
        "issue": [(False, False, True)],
        "branches": [(False, True)],
        "pr": [(True, False, False)],
    }
    assert "task_contract" in snapshot.acquired_facts
    assert "checks" in snapshot.acquired_facts
    assert {
        "comments",
        "issue_closure",
        "git",
        "workspace_inventory",
        "local_task_branches",
        "pr_history",
    }.isdisjoint(snapshot.acquired_facts)


def test_review_complete_can_retry_after_transient_live_resolution_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value()
    state = _review_state()

    class FlakyResolver(StaticResolver):
        def resolve(self, task_number: int) -> lck_models.LiveState:
            self.calls += 1
            if self.calls == 1:
                raise lck_models.LckStopError("transient live-state resolution failure")
            return self.state

    resolver = cast(Any, FlakyResolver(tmp_path, state))
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(review_id, _review_guard(identity, review_root=review_root))
    store.review_prepare_inflight_path(159).parent.mkdir(parents=True, exist_ok=True)
    store.review_prepare_inflight_path(159).write_text(
        json.dumps(
            {
                "task_number": 159,
                "operation_id": store.new_id(),
                "pid": 1,
                "state": "handed-off",
                "review_id": review_id,
                "review_root": str(review_root),
            }
        ),
        encoding="utf-8",
    )
    workspace = FakeReviewWorkspace(review_root)
    completer = lck_review.ReviewCompleter(
        resolver,
        checks_gate=cast(Any, FakeReviewChecks()),
        store=store,
        workspace=cast(Any, workspace),
    )

    with pytest.raises(
        lck_models.LckStopError, match="transient live-state resolution"
    ):
        completer.complete(159, review_id, verdict="PASS")

    assert resolver.calls == 1
    assert workspace.removed == []
    assert store.guard_path(review_id).is_file()
    assert store.review_prepare_inflight_path(159).is_file()

    result = completer.complete(159, review_id, verdict="PASS")

    assert result.status == "READY_FOR_MERGE_PREFLIGHT"
    assert resolver.calls == 2
    assert workspace.removed == [review_root]
    assert not store.guard_path(review_id).exists()
    assert not store.review_prepare_inflight_path(159).exists()


def test_review_pass_stops_at_merge_preflight_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value()
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(review_id, _review_guard(identity, review_root=review_root))

    result = lck_review.ReviewCompleter(
        resolver,
        checks_gate=cast(Any, FakeReviewChecks()),
        store=store,
        workspace=cast(Any, FakeReviewWorkspace(review_root)),
    ).complete(159, review_id, verdict="PASS")

    assert result.status == "READY_FOR_MERGE_PREFLIGHT"
    assert "Merge Preflight" in result.to_dict()["human_boundary"]
    assert resolver.calls == 1


@pytest.mark.parametrize(
    ("code", "current_identity"),
    [
        (
            "REVIEW_STALE_PR",
            lck_review_workspace.ReviewIdentity(
                task_number=159,
                pr_number=201,
                base_sha=SHA,
                head_sha=SHA,
                task_body_sha256="d" * 64,
                merge_base_sha=SHA,
                effective_diff_sha256="e" * 64,
                changed_files=("tools/agent_workflow/lck.py",),
            ),
        ),
        ("REVIEW_STALE_HEAD", _review_identity_value(head="b" * 40)),
        ("REVIEW_STALE_BASE", _review_identity_value(base="b" * 40)),
        (
            "REVIEW_STALE_TASK",
            lck_review_workspace.ReviewIdentity(
                task_number=159,
                pr_number=200,
                base_sha=SHA,
                head_sha=SHA,
                task_body_sha256="f" * 64,
                merge_base_sha=SHA,
                effective_diff_sha256="e" * 64,
                changed_files=("tools/agent_workflow/lck.py",),
            ),
        ),
        (
            "REVIEW_STALE_DIFF",
            lck_review_workspace.ReviewIdentity(
                task_number=159,
                pr_number=200,
                base_sha=SHA,
                head_sha=SHA,
                task_body_sha256="d" * 64,
                merge_base_sha=SHA,
                effective_diff_sha256="f" * 64,
                changed_files=("tools/agent_workflow/lck.py",),
            ),
        ),
    ],
)
def test_review_complete_rejects_stale_target_from_one_fresh_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    current_identity: lck_review_workspace.ReviewIdentity,
) -> None:
    reviewed = _review_identity_value()
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: current_identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(review_id, _review_guard(reviewed, review_root=review_root))

    with pytest.raises(lck_models.ReviewStaleError, match=code):
        lck_review.ReviewCompleter(
            resolver,
            checks_gate=cast(Any, FakeReviewChecks()),
            store=store,
            workspace=cast(Any, FakeReviewWorkspace(review_root)),
        ).complete(159, review_id, verdict="PASS")

    assert resolver.calls == 1
    assert store.read_latest_review(159) is None
    assert not store.record_path(159, review_id).exists()


def test_review_complete_reports_changed_remote_head_before_local_diff_probe(
    tmp_path: Path,
) -> None:
    reviewed = _review_identity_value()
    resolver = cast(Any, StaticResolver(tmp_path, _review_state(head="b" * 40)))
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(review_id, _review_guard(reviewed, review_root=review_root))

    with pytest.raises(lck_models.ReviewStaleError, match="REVIEW_STALE_HEAD"):
        lck_review.ReviewCompleter(
            resolver,
            checks_gate=cast(Any, FakeReviewChecks()),
            store=store,
            workspace=cast(Any, FakeReviewWorkspace(review_root)),
        ).complete(159, review_id, verdict="PASS")

    assert resolver.calls == 1
    assert not any("merge-base" in command for command in resolver.runner.commands)
    assert not any("diff" in command for command in resolver.runner.commands)


def test_review_complete_revalidates_current_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value()
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(review_id, _review_guard(identity, review_root=review_root))

    class CurrentChecksFail:
        def evaluate(self, _snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
            raise lck_models.LckStopError("PR checks are pending")

    with pytest.raises(lck_models.LckStopError, match="PR checks are pending"):
        lck_review.ReviewCompleter(
            resolver,
            checks_gate=cast(Any, CurrentChecksFail()),
            store=store,
            workspace=cast(Any, FakeReviewWorkspace(review_root)),
        ).complete(159, review_id, verdict="PASS")

    assert resolver.calls == 1
    assert store.read_latest_review(159) is None


def test_review_fail_is_not_delayed_by_pending_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _review_identity_value()
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "review-root"
    review_root.mkdir()
    store.write_guard(review_id, _review_guard(identity, review_root=review_root))
    findings = tmp_path / "findings.txt"
    findings.write_text(
        "[F1][Medium] CI-independent semantic finding.\n", encoding="utf-8"
    )

    class PendingChecks(FakeReviewChecks):
        def evaluate(self, _snapshot: lck_models.OperationSnapshot) -> dict[str, Any]:
            raise lck_models.LckStopError("PR checks are pending")

    result = lck_review.ReviewCompleter(
        resolver,
        checks_gate=cast(Any, PendingChecks()),
        store=store,
        workspace=cast(Any, FakeReviewWorkspace(review_root)),
    ).complete(159, review_id, verdict="FAIL", findings_file=findings)

    assert result.status == "STOP_REQUIRED"


def test_review_workspace_seal_removes_write_bits(tmp_path: Path) -> None:
    root = tmp_path / "review"
    nested = root / "pkg"
    nested.mkdir(parents=True)
    target = nested / "file.py"
    target.write_text("print('read only')\n", encoding="utf-8")
    executable = nested / "tool.py"
    executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    executable.chmod(0o755)

    lck_review_workspace.ReviewWorkspaceManager.seal_read_only(root)

    assert root.stat().st_mode & 0o222 == 0
    assert nested.stat().st_mode & 0o222 == 0
    assert target.stat().st_mode & 0o222 == 0
    assert executable.stat().st_mode & 0o222 == 0
    assert executable.stat().st_mode & 0o111 != 0
    lck_review_workspace.ReviewWorkspaceManager._make_removable(root)


def test_review_workspace_seal_preserves_clean_status_and_rejects_mutation(
    tmp_path: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="tracequant-lck-review-") as raw:
        root = Path(raw)
        (root / ".git").mkdir()
        (root / "tracked.py").write_text("print('review')\n", encoding="utf-8")
        resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
        runner = FakeWorkspaceRunner(root)
        resolver.runner = runner
        manager = lck_review_workspace.ReviewWorkspaceManager(resolver)
        manager._write_owner(
            root,
            task_number=159,
            base_sha=SHA,
            head_sha=SHA,
        )

        manager.seal_for_review(root, SHA)

        assert root.stat().st_mode & 0o222 == 0
        assert (root / "tracked.py").stat().st_mode & 0o222 == 0
        manager.assert_ready_for_completion(root, SHA)

        runner.status_output = " M tracked.py\n"
        with pytest.raises(lck_models.LckStopError, match="changed the isolated clone"):
            manager.assert_ready_for_completion(root, SHA)
        manager._make_removable(root)


def test_review_prepare_materializes_missing_pr_head_before_deriving_diff(
    tmp_path: Path,
) -> None:
    """A fresh remote PR head need not already exist in the source object store."""
    if shutil.which("git") is None:
        pytest.skip("git is required for Review clone integration test")

    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    producer = tmp_path / "producer"
    subprocess.run(
        ["git", "clone", str(remote), str(producer)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    def git(
        cwd: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    git(producer, "config", "user.name", "TraceQuant Test")
    git(producer, "config", "user.email", "tracequant-test@example.invalid")
    (producer / "tracked.py").write_text("base\n", encoding="utf-8")
    _write_required_checks_workflow(producer)
    git(producer, "add", "tracked.py", ".github/workflows/ci.yml")
    git(producer, "commit", "-m", "base")
    git(producer, "branch", "-M", "main")
    git(producer, "push", "origin", "main")
    base_sha = git(producer, "rev-parse", "HEAD").stdout.strip()

    source = tmp_path / "source"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote), str(source)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _write_required_checks_workflow(source)

    git(producer, "checkout", "-b", "task/159-lck-core-live-state-resolution")
    (producer / "tracked.py").write_text("base\nhead\n", encoding="utf-8")
    git(producer, "add", "tracked.py")
    git(producer, "commit", "-m", "head")
    git(producer, "push", "origin", "HEAD")
    head_sha = git(producer, "rev-parse", "HEAD").stdout.strip()

    missing = git(source, "cat-file", "-e", f"{head_sha}^{{commit}}", check=False)
    assert missing.returncode != 0

    state = _review_state(head=head_sha, base=base_sha)
    resolver = cast(Any, StaticResolver(source, state))
    resolver.runner = CommandRunner(source)
    store = lck_review_workspace.ReviewInvocationStore(source)
    workspace = lck_review_workspace.ReviewWorkspaceManager(resolver)

    context = lck_review.ReviewPreparer(
        resolver,
        validation=cast(Any, FakeReviewValidation()),
        checks_gate=cast(Any, FakeReviewChecks()),
        workspace=workspace,
        store=store,
    ).prepare(159)

    try:
        assert context.identity.base_sha == base_sha
        assert context.identity.head_sha == head_sha
        assert context.identity.merge_base_sha == base_sha
        assert context.identity.changed_files == ("tracked.py",)
        assert (
            git(
                source, "cat-file", "-e", f"{head_sha}^{{commit}}", check=False
            ).returncode
            != 0
        )
        assert (
            git(
                context.review_root, "cat-file", "-e", f"{head_sha}^{{commit}}"
            ).returncode
            == 0
        )
    finally:
        workspace.remove(context.review_root)
        store.delete_guard(context.review_id)
        store.release_review_prepare(159, context.review_id)


def test_review_workspace_uses_standalone_clone_without_source_git_mutation(
    tmp_path: Path,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required for Review clone integration test")

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str, cwd: Path = repo) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    git("init")
    git("config", "user.name", "TraceQuant Test")
    git("config", "user.email", "tracequant-test@example.invalid")
    git("remote", "add", "origin", "https://example.invalid/tracequant.git")
    tracked = repo / "tracked.py"
    tracked.write_text("print('review')\n", encoding="utf-8")
    executable = repo / "tool.py"
    executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    executable.chmod(0o755)
    git("add", "tracked.py", "tool.py")
    git("commit", "-m", "initial")
    head_sha = git("rev-parse", "HEAD").stdout.strip()

    source_worktrees_before = git("worktree", "list", "--porcelain").stdout
    source_head_before = git("rev-parse", "HEAD").stdout.strip()
    source_status_before = git("status", "--porcelain=v1").stdout
    source_git_modes_before = {
        path.relative_to(repo / ".git"): path.stat().st_mode
        for path in (repo / ".git").rglob("*")
        if not path.is_symlink()
    }
    source_object = repo / ".git" / "objects" / head_sha[:2] / head_sha[2:]
    assert source_object.is_file()

    resolver = cast(Any, StaticResolver(repo, _review_state()))
    resolver.runner = CommandRunner(repo)
    manager = lck_review_workspace.ReviewWorkspaceManager(resolver)
    review_root = manager.create(159, head_sha, head_sha)

    try:
        assert git("rev-parse", "HEAD", cwd=review_root).stdout.strip() == head_sha
        assert git("branch", "--show-current", cwd=review_root).stdout.strip() == ""
        assert git("status", "--porcelain=v1", cwd=review_root).stdout.strip() == ""
        assert (
            git("remote", "get-url", "origin", cwd=review_root).stdout.strip()
            == "https://example.invalid/tracequant.git"
        )
        clone_object = review_root / ".git" / "objects" / head_sha[:2] / head_sha[2:]
        assert clone_object.is_file()
        assert clone_object.stat().st_ino != source_object.stat().st_ino

        manager.seal_for_review(review_root, head_sha)
        manager.assert_ready_for_completion(review_root, head_sha)
        assert review_root.stat().st_mode & 0o222 == 0
        assert (review_root / "tracked.py").stat().st_mode & 0o222 == 0
        sealed_executable = review_root / "tool.py"
        assert sealed_executable.stat().st_mode & 0o222 == 0
        assert sealed_executable.stat().st_mode & 0o111 != 0

        assert git("rev-parse", "HEAD").stdout.strip() == source_head_before
        assert git("status", "--porcelain=v1").stdout == source_status_before
        assert git("worktree", "list", "--porcelain").stdout == source_worktrees_before
        source_git_modes_after = {
            path.relative_to(repo / ".git"): path.stat().st_mode
            for path in (repo / ".git").rglob("*")
            if not path.is_symlink()
        }
        assert source_git_modes_after == source_git_modes_before
    finally:
        manager.remove(review_root)

    assert not review_root.exists()
    assert git("worktree", "list", "--porcelain").stdout == source_worktrees_before


def test_review_workspace_remove_rejects_unvalidated_path(tmp_path: Path) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    manager = lck_review_workspace.ReviewWorkspaceManager(resolver)

    with pytest.raises(lck_models.LckStopError, match="cleanup path"):
        manager.remove(tmp_path / "not-an-lck-workspace")


def test_review_workspace_recovered_partial_clone_cleanup_is_path_local(
    tmp_path: Path,
) -> None:
    resolver = cast(Any, StaticResolver(tmp_path, _review_state()))
    manager = lck_review_workspace.ReviewWorkspaceManager(resolver)
    review_root = manager.path_for(159, uuid.uuid4().hex)
    review_root.mkdir()
    partial_git = review_root / ".git"
    partial_git.mkdir()
    (partial_git / "partial").write_text("interrupted clone\n", encoding="utf-8")
    manager.seal_read_only(review_root)

    manager.remove_recovered(review_root)

    assert not review_root.exists()


def test_review_validation_artifacts_are_preserved_outside_review_clone(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_agents = repo_root / ".agents"
    source_agents.mkdir()
    review_root = tmp_path / "review-root"
    tool = review_root / "tools" / "agent_workflow" / "workflow_validation.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("# validation stub\n", encoding="utf-8")
    output_dir = review_root / ".agents" / "validation.local" / "run"
    output_dir.mkdir(parents=True)
    (output_dir / "pytest.log").write_text("pass\n", encoding="utf-8")

    class ValidationRunner:
        def run(self, argv: Any, *, command_id: str, **_: Any) -> CommandResult:
            payload = {
                "status": "pass",
                "output_dir": ".agents/validation.local/run",
                "commands": [
                    {
                        "status": "pass",
                        "log_path": ".agents/validation.local/run/pytest.log",
                    }
                ],
            }
            return CommandResult(
                command_id=command_id,
                argv=tuple(str(item) for item in argv),
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )

    resolver = cast(Any, StaticResolver(repo_root, _review_state()))
    resolver.runner = ValidationRunner()
    validation = lck_validation.ReviewValidationGate(resolver).run(
        review_root, SHA, SHA
    )

    durable_output = repo_root / validation["output_dir"]
    durable_log = repo_root / validation["commands"][0]["log_path"]
    assert validation["output_dir"].startswith(
        ".workflow.local/lck/review-validation/lck-review-"
    )
    assert validation["evidence_path"] == validation["output_dir"]
    assert validation["validated_base_sha"] == SHA
    assert validation["validated_head_sha"] == SHA
    assert durable_output.is_dir()
    assert durable_log.read_text(encoding="utf-8") == "pass\n"
    evidence_file = repo_root / validation["evidence_file"]
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert evidence["validated_base_sha"] == SHA
    assert evidence["commands"][0]["status"] == "pass"
    assert not (source_agents / "validation.local").exists()


def test_review_validation_failure_is_persisted_before_prepare_stops(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    review_root = tmp_path / "review-root"
    tool = review_root / "tools" / "agent_workflow" / "workflow_validation.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("# validation stub\n", encoding="utf-8")
    output_dir = review_root / ".agents" / "validation.local" / "run"
    output_dir.mkdir(parents=True)
    (output_dir / "ruff-check.log").write_text("format mismatch\n", encoding="utf-8")

    class ValidationRunner:
        def run(self, argv: Any, *, command_id: str, **_: Any) -> CommandResult:
            payload = {
                "status": "fail",
                "output_dir": ".agents/validation.local/run",
                "commands": [
                    {
                        "command_id": "ruff-check",
                        "status": "fail",
                        "exit_code": 1,
                        "diagnostic": "format mismatch",
                        "log_path": ".agents/validation.local/run/ruff-check.log",
                    }
                ],
            }
            return CommandResult(
                command_id=command_id,
                argv=tuple(str(item) for item in argv),
                returncode=1,
                stdout=json.dumps(payload),
                stderr="",
            )

    resolver = cast(Any, StaticResolver(repo_root, _review_state()))
    resolver.runner = ValidationRunner()
    validation = lck_validation.ReviewValidationGate(resolver).run(
        review_root, SHA, "b" * 40
    )

    assert validation["status"] == "fail"
    assert validation["validated_base_sha"] == SHA
    assert validation["validated_head_sha"] == "b" * 40
    assert validation["commands"][0]["command_id"] == "ruff-check"
    assert validation["commands"][0]["exit_code"] == 1
    evidence = repo_root / validation["evidence_path"]
    assert evidence.is_dir()
    assert (evidence / "ruff-check.log").read_text(encoding="utf-8") == (
        "format mismatch\n"
    )
    stored = json.loads(
        (repo_root / validation["evidence_file"]).read_text(encoding="utf-8")
    )
    assert stored["commands"][0]["command_id"] == "ruff-check"
    assert stored["commands"][0]["exit_code"] == 1
    assert stored["commands"][0]["diagnostic"] == "format mismatch"
    assert stored["validated_head_sha"] == "b" * 40


def test_review_prepare_claims_operation_before_validation_and_has_no_review_id_on_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _review_state()
    resolver = cast(Any, StaticResolver(tmp_path, state))
    identity = _review_identity_value()
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    monkeypatch.setattr(
        lck_review, "_review_identity", lambda *_args, **_kwargs: identity
    )

    class FailingValidation:
        def run(self, _root: Path, base: str, head: str) -> dict[str, Any]:
            assert store.review_prepare_inflight_path(159).is_file()
            return {
                "status": "fail",
                "validated_base_sha": base,
                "validated_head_sha": head,
                "commands": [
                    {
                        "command_id": "ruff-check",
                        "status": "fail",
                        "exit_code": 1,
                        "diagnostic": "format mismatch",
                    }
                ],
                "evidence_path": ".workflow.local/lck/review-validation/lck-review-failure",
            }

    workspace = FakeReviewWorkspace(tmp_path / "review-root")
    with pytest.raises(
        lck_models.LckStopError,
        match="failed command ruff-check.*evidence",
    ) as exc_info:
        lck_review.ReviewPreparer(
            resolver,
            validation=cast(Any, FailingValidation()),
            checks_gate=cast(Any, FakeReviewChecks()),
            workspace=cast(Any, workspace),
            store=store,
        ).prepare(159)

    assert "validated base" in str(exc_info.value)
    assert workspace.removed == [tmp_path / "review-root"]
    assert not (store.root / "review-invocations").exists()
    assert not store.review_prepare_inflight_path(159).exists()


def test_review_prepare_inflight_guard_blocks_second_operation(tmp_path: Path) -> None:
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    first = store.begin_review_prepare(159)
    try:
        with pytest.raises(lck_models.LckStopError, match="already in flight"):
            store.begin_review_prepare(159)
    finally:
        first.finish()

    assert not store.review_prepare_inflight_path(159).exists()


def test_review_prepare_handoff_keeps_explicit_ownership_until_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = lck_review_workspace.ReviewInvocationStore(tmp_path)
    review_id = store.new_id()
    review_root = tmp_path / "tracequant-lck-review-159-handoff"
    review_root.mkdir()
    store.write_guard(review_id, {"review_id": review_id})
    store.review_prepare_inflight_path(159).parent.mkdir(parents=True, exist_ok=True)
    store.review_prepare_inflight_path(159).write_text(
        json.dumps(
            {
                "task_number": 159,
                "pid": 1,
                "operation_id": store.new_id(),
                "state": "handed-off",
                "review_id": review_id,
                "review_root": str(review_root),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        lck_review_workspace.ReviewInvocationStore,
        "_pid_is_alive",
        staticmethod(lambda _pid: False),
    )

    with pytest.raises(lck_models.LckStopError, match="handoff is still owned"):
        store.begin_review_prepare(159)

    assert store.review_prepare_inflight_path(159).is_file()
