from __future__ import annotations

import fcntl
import os
import re
import shutil
import stat
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from workflow_common import WorkflowToolError, atomic_write_json, is_sha, read_json_file

from .effective_diff import calculate_effective_diff
from .issue_profiles import resolve_leaf_issue_profile
from .models import (
    LCK_SCHEMA_VERSION,
    LckStopError,
    LiveState,
    ReviewStaleError,
    _pr_base_sha,
    _pr_head_sha,
)
from .profile_policies import (
    DEFAULT_PROFILE_POLICY_REGISTRY,
    ProfilePolicyError,
    ProfilePolicyRegistry,
    ProfileResolver,
    build_profile_review_artifact,
    resolve_issue_policy,
)
from .state import LiveStateResolver


@dataclass(frozen=True)
class ReviewTargetRefs:
    """Authoritative Review target facts acquired before repository materialization."""

    task_number: int
    pr_number: int
    base_sha: str
    head_sha: str
    task_body_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_number": self.task_number,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "task_body_sha256": self.task_body_sha256,
        }


@dataclass(frozen=True)
class ReviewIdentity:
    task_number: int
    pr_number: int
    base_sha: str
    head_sha: str
    task_body_sha256: str
    merge_base_sha: str
    effective_diff_sha256: str
    changed_files: tuple[str, ...]
    research_artifact: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_number": self.task_number,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "task_body_sha256": self.task_body_sha256,
            "merge_base_sha": self.merge_base_sha,
            "effective_diff_sha256": self.effective_diff_sha256,
            "changed_files": list(self.changed_files),
        }
        if self.research_artifact is not None:
            result["research_artifact"] = dict(self.research_artifact)
        return result


def _review_target_refs(
    state: LiveState,
    task_contract: Mapping[str, Any],
) -> ReviewTargetRefs:
    """Extract immutable Git/GitHub identities without requiring local Git objects."""
    pr = state.open_pr
    if not isinstance(pr, Mapping):
        raise LckStopError("Review target has no current OPEN PR")
    pr_number = pr.get("number")
    base_sha = pr.get("baseRefOid")
    head_sha = pr.get("headRefOid")
    task_body_sha256 = task_contract.get("body_sha256")
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number <= 0:
        raise LckStopError("Review target PR number is unavailable")
    if not is_sha(base_sha) or not is_sha(head_sha):
        raise LckStopError("Review target base/head identity is unavailable")
    if not isinstance(task_body_sha256, str) or not task_body_sha256:
        raise LckStopError("Review target Task Contract identity is unavailable")
    return ReviewTargetRefs(
        task_number=state.task_number,
        pr_number=pr_number,
        base_sha=str(base_sha),
        head_sha=str(head_sha),
        task_body_sha256=task_body_sha256,
    )


def _review_identity(
    resolver: LiveStateResolver,
    state: LiveState,
    task_contract: Mapping[str, Any],
    *,
    repo_root: Path,
    policy_registry: ProfilePolicyRegistry = DEFAULT_PROFILE_POLICY_REGISTRY,
    profile_resolver: ProfileResolver | None = None,
) -> ReviewIdentity:
    """Derive the effective diff from frozen refs inside a materialized repository.

    GitHub supplies the authoritative PR/base/head/Task identities.  Merge-base,
    effective diff, and changed-file inventory are derived facts and therefore
    must be computed only after those exact commits exist in the isolated Review
    clone.  The source repository is not required to contain the current PR head.
    """
    target = _review_target_refs(state, task_contract)
    effective_diff = calculate_effective_diff(
        resolver.runner,
        base_sha=target.base_sha,
        head_ref=target.head_sha,
        command_id_prefix="lck-review",
        cwd=repo_root,
    )
    artifact_input = {
        "task_number": target.task_number,
        "pr_number": target.pr_number,
        "base_sha": target.base_sha,
        "head_sha": target.head_sha,
        "task_body_sha256": target.task_body_sha256,
        "merge_base_sha": effective_diff.merge_base_sha,
        "effective_diff_sha256": effective_diff.effective_diff_sha256,
        "changed_files": effective_diff.changed_files,
    }
    try:
        profile, _policy = resolve_issue_policy(
            state.issue or {},
            registry=policy_registry,
            profile_resolver=profile_resolver or resolve_leaf_issue_profile,
        )
        research_artifact = build_profile_review_artifact(
            profile,
            state.issue or {},
            artifact_input,
            repo_root=repo_root,
            registry=policy_registry,
        )
    except (ProfilePolicyError, ValueError) as exc:
        raise LckStopError(
            f"Research artifact policy rejected the Review target: {exc}"
        ) from exc
    return ReviewIdentity(
        task_number=target.task_number,
        pr_number=target.pr_number,
        base_sha=target.base_sha,
        head_sha=target.head_sha,
        task_body_sha256=target.task_body_sha256,
        merge_base_sha=effective_diff.merge_base_sha,
        effective_diff_sha256=effective_diff.effective_diff_sha256,
        changed_files=effective_diff.changed_files,
        research_artifact=research_artifact,
    )


class ReviewWorkspaceManager:
    """Own one standalone temporary clone for an Independent Review session.

    Review is read-only with respect to the source repository.  The reviewed
    workspace is therefore a self-contained temporary clone rather than a Git
    worktree registered in the source repository.  All Review Git metadata writes
    stay inside the temporary clone; durable validation evidence is copied only
    to the ignored LCK local evidence root before clone cleanup.
    """

    OWNER_FILE: Final = "lck-review-owner.json"

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    @staticmethod
    def _validated_workspace_path(path: Path) -> Path:
        resolved = path.resolve(strict=False)
        temp_root = Path(tempfile.gettempdir()).resolve()
        try:
            relative = resolved.relative_to(temp_root)
        except ValueError as exc:
            raise LckStopError(
                "Review workspace cleanup path is outside the temporary root"
            ) from exc
        if len(relative.parts) != 1 or not resolved.name.startswith(
            "tracequant-lck-review-"
        ):
            raise LckStopError("Review workspace cleanup path is not LCK-owned")
        return resolved

    def path_for(self, task_number: int, operation_id: str) -> Path:
        """Return an uncreated, operation-owned temporary clone path."""
        return self._validated_workspace_path(
            Path(tempfile.gettempdir())
            / f"tracequant-lck-review-{task_number}-{operation_id}"
        )

    def _source_remote_url(self) -> str:
        result = self.resolver.runner.run(
            ["git", "remote", "get-url", "origin"],
            command_id="lck-review-source-origin",
        )
        remote_url = result.stdout.strip()
        if result.returncode != 0 or not remote_url:
            raise LckStopError(
                "cannot resolve source repository origin for isolated Review clone: "
                + (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "origin unavailable"
                )
            )
        return remote_url

    @classmethod
    def _owner_path(cls, path: Path) -> Path:
        return path / ".git" / cls.OWNER_FILE

    def _write_owner(
        self,
        path: Path,
        *,
        task_number: int,
        base_sha: str,
        head_sha: str,
    ) -> None:
        atomic_write_json(
            self._owner_path(path),
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "lck-review-standalone-clone",
                "task_number": task_number,
                "review_root": str(path),
                "source_repo": str(self.resolver.repo_root),
                "expected_base_sha": base_sha,
                "expected_head_sha": head_sha,
                "authority": "operation-owned temporary Review workspace only",
            },
        )

    def _assert_owned_clone(
        self,
        path: Path,
        *,
        expected_head_sha: str | None = None,
    ) -> Mapping[str, Any]:
        path = self._validated_workspace_path(path)
        if not path.is_dir() or not (path / ".git").is_dir():
            raise LckStopError("isolated Review clone is unavailable")
        owner_path = self._owner_path(path)
        try:
            value = read_json_file(owner_path)
        except WorkflowToolError as exc:
            raise LckStopError(
                "isolated Review clone ownership marker is unavailable"
            ) from exc
        if not isinstance(value, Mapping):
            raise LckStopError("isolated Review clone ownership marker is invalid")
        if value.get("kind") != "lck-review-standalone-clone":
            raise LckStopError("isolated Review clone ownership marker is invalid")
        if value.get("review_root") != str(path):
            raise LckStopError("isolated Review clone ownership path does not match")
        if value.get("source_repo") != str(self.resolver.repo_root):
            raise LckStopError("isolated Review clone belongs to another repository")
        if (
            expected_head_sha is not None
            and value.get("expected_head_sha") != expected_head_sha
        ):
            raise LckStopError("isolated Review clone expected HEAD does not match")
        return value

    def _ensure_commit(self, path: Path, sha: str, *, label: str) -> None:
        available = self.resolver.runner.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            command_id=f"lck-review-clone-{label}-available",
            cwd=path,
        )
        if available.returncode == 0:
            return
        fetched = self.resolver.runner.run(
            ["git", "fetch", "--no-tags", "origin", sha],
            command_id=f"lck-review-clone-fetch-{label}",
            cwd=path,
            retries=1,
        )
        if fetched.returncode != 0:
            raise LckStopError(
                f"cannot materialize reviewed {label} commit in temporary clone: "
                + (
                    fetched.stderr.strip()
                    or fetched.stdout.strip()
                    or f"exit {fetched.returncode}"
                )
            )
        verified = self.resolver.runner.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            command_id=f"lck-review-clone-{label}-verified",
            cwd=path,
        )
        if verified.returncode != 0:
            raise LckStopError(
                f"temporary Review clone does not contain expected {label} commit"
            )

    def create(
        self,
        task_number: int,
        base_sha: str,
        head_sha: str,
        path: Path | None = None,
    ) -> Path:
        path = self.path_for(task_number, uuid.uuid4().hex) if path is None else path
        path = self._validated_workspace_path(path)
        if path.exists():
            raise LckStopError("isolated Review clone path is already occupied")
        remote_url = self._source_remote_url()
        clone = self.resolver.runner.run(
            [
                "git",
                "clone",
                "--no-checkout",
                "--no-hardlinks",
                str(self.resolver.repo_root),
                str(path),
            ],
            command_id="lck-review-clone-create",
        )
        if clone.returncode != 0:
            shutil.rmtree(path, ignore_errors=True)
            raise LckStopError(
                "cannot create isolated Review clone: "
                + (
                    clone.stderr.strip()
                    or clone.stdout.strip()
                    or f"exit {clone.returncode}"
                )
            )
        try:
            self._write_owner(
                path,
                task_number=task_number,
                base_sha=base_sha,
                head_sha=head_sha,
            )
            set_origin = self.resolver.runner.run(
                ["git", "remote", "set-url", "origin", remote_url],
                command_id="lck-review-clone-set-origin",
                cwd=path,
            )
            if set_origin.returncode != 0:
                raise LckStopError(
                    "cannot restore authoritative origin in isolated Review clone: "
                    + (set_origin.stderr.strip() or set_origin.stdout.strip())
                )
            self._ensure_commit(path, base_sha, label="base")
            self._ensure_commit(path, head_sha, label="head")
            checkout = self.resolver.runner.run(
                ["git", "checkout", "--detach", head_sha],
                command_id="lck-review-clone-checkout",
                cwd=path,
            )
            if checkout.returncode != 0:
                raise LckStopError(
                    "cannot checkout exact reviewed HEAD in isolated Review clone: "
                    + (checkout.stderr.strip() or checkout.stdout.strip())
                )
            self._assert_clean_exact(path, head_sha)
        except BaseException:
            self._make_removable(path)
            shutil.rmtree(path, ignore_errors=True)
            raise
        return path

    def _assert_clean_exact(self, path: Path, expected_head_sha: str) -> None:
        if not path.is_dir():
            raise LckStopError("isolated Review clone is unavailable")
        status = self.resolver.runner.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            command_id="lck-review-clone-clean",
            cwd=path,
            env={"GIT_OPTIONAL_LOCKS": "0"},
        )
        head = self.resolver.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="lck-review-clone-head",
            cwd=path,
            env={"GIT_OPTIONAL_LOCKS": "0"},
        )
        if status.returncode != 0 or head.returncode != 0:
            raise LckStopError("cannot verify isolated Review clone")
        if status.stdout.strip():
            raise LckStopError("formal Review validation changed the isolated clone")
        if head.stdout.strip() != expected_head_sha:
            raise LckStopError("isolated Review clone HEAD changed during validation")

    @staticmethod
    def _assert_read_only(path: Path) -> None:
        for root, dirs, files in os.walk(path, topdown=True, followlinks=False):
            root_path = Path(root)
            if root_path.stat().st_mode & 0o222:
                raise LckStopError("isolated Review clone is not read-only")
            for name in (*dirs, *files):
                target = root_path / name
                if not target.is_symlink() and target.stat().st_mode & 0o222:
                    raise LckStopError("isolated Review clone is not read-only")

    def seal_for_review(self, path: Path, expected_head_sha: str) -> None:
        """Verify exact contents, seal the independent clone, and keep it clean."""
        path = self._validated_workspace_path(path)
        self._assert_owned_clone(path, expected_head_sha=expected_head_sha)
        self._assert_clean_exact(path, expected_head_sha)
        self.seal_read_only(path)
        self._assert_clean_exact(path, expected_head_sha)
        self._assert_read_only(path)

    def assert_ready_for_completion(self, path: Path, expected_head_sha: str) -> None:
        """Fail closed unless the prepared standalone Review clone is intact."""
        path = self._validated_workspace_path(path)
        self._assert_owned_clone(path, expected_head_sha=expected_head_sha)
        self._assert_clean_exact(path, expected_head_sha)
        self._assert_read_only(path)

    @staticmethod
    def _remove_write_bits(path: Path) -> None:
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, mode & ~0o222)

    @classmethod
    def seal_read_only(cls, path: Path) -> None:
        for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
            root_path = Path(root)
            for name in files:
                target = root_path / name
                if not target.is_symlink():
                    cls._remove_write_bits(target)
            for name in dirs:
                target = root_path / name
                if not target.is_symlink():
                    cls._remove_write_bits(target)
        cls._remove_write_bits(path)

    @staticmethod
    def _make_removable(path: Path) -> None:
        if not path.exists():
            return
        os.chmod(path, 0o755)
        for root, dirs, files in os.walk(path, topdown=True, followlinks=False):
            root_path = Path(root)
            os.chmod(root_path, 0o755)
            for name in dirs:
                target = root_path / name
                if not target.is_symlink():
                    os.chmod(target, 0o755)
            for name in files:
                target = root_path / name
                if not target.is_symlink():
                    os.chmod(target, 0o644)

    def remove(self, path: Path) -> None:
        path = self._validated_workspace_path(path)
        if not path.exists():
            return
        self._assert_owned_clone(path)
        self._make_removable(path)
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            raise LckStopError("failed to remove isolated Review clone directory")

    def remove_recovered(self, path: Path) -> None:
        """Remove a marker-owned clone, including an interrupted partial clone."""
        path = self._validated_workspace_path(path)
        if not path.exists():
            return
        self._make_removable(path)
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            raise LckStopError("failed to remove recovered Review clone directory")


@dataclass
class ReviewPrepareInvocation:
    """Own one operation-local Review Prepare marker until it finishes."""

    path: Path
    operation_id: str
    _lock_fd: int
    recovered: Mapping[str, Any] | None = None

    def update(self, payload: Mapping[str, Any]) -> None:
        atomic_write_json(self.path, payload)

    def release_lock(self) -> None:
        """Release the process lock while retaining durable handoff state."""
        if self._lock_fd < 0:
            return
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)
        self._lock_fd = -1

    def finish(self) -> None:
        try:
            self.path.unlink()
        finally:
            self.release_lock()


class ReviewInvocationStore:
    """Persist invocation-local guards and diagnostic review records only."""

    _ID = re.compile(r"^[0-9a-f]{32}$")

    def __init__(self, repo_root: Path) -> None:
        self.root = repo_root / ".workflow.local" / "lck"

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def _validate_id(self, review_id: str) -> None:
        if self._ID.fullmatch(review_id) is None:
            raise LckStopError("invalid Review invocation id")

    def guard_path(self, review_id: str) -> Path:
        self._validate_id(review_id)
        return self.root / "review-invocations" / f"{review_id}.json"

    def record_path(self, task_number: int, review_id: str) -> Path:
        self._validate_id(review_id)
        return self.root / "reviews" / f"task-{task_number}" / f"{review_id}.json"

    def latest_review_path(self, task_number: int) -> Path:
        return self.root / "review-state" / f"task-{task_number}-latest.json"

    def review_required_path(self, task_number: int) -> Path:
        return self.root / "review-state" / f"task-{task_number}-required.json"

    def remediation_session_path(self, task_number: int) -> Path:
        return self.root / "remediation-sessions" / f"task-{task_number}.json"

    def remediation_no_change_receipt_path(
        self, task_number: int, review_id: str
    ) -> Path:
        self._validate_id(review_id)
        return (
            self.root
            / "remediation-receipts"
            / f"task-{task_number}"
            / f"{review_id}-no-change.json"
        )

    def review_prepare_inflight_path(self, task_number: int) -> Path:
        return self.root / "review-inflight" / f"task-{task_number}.json"

    def review_prepare_lock_path(self, task_number: int) -> Path:
        return self.root / "review-inflight" / f"task-{task_number}.lock"

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def begin_review_prepare(self, task_number: int) -> ReviewPrepareInvocation:
        """Claim one Task-local Review Prepare operation before side effects."""
        path = self.review_prepare_inflight_path(task_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.review_prepare_lock_path(task_number)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                try:
                    active = read_json_file(path)
                except WorkflowToolError:
                    active = {}
                operation_id = (
                    active.get("operation_id") if isinstance(active, Mapping) else None
                )
                state = active.get("state") if isinstance(active, Mapping) else None
                raise LckStopError(
                    f"Review Prepare already in flight for Task #{task_number}"
                    + (
                        f" (operation {operation_id}, state {state})"
                        if operation_id
                        else ""
                    )
                ) from exc

            existing: Mapping[str, Any] | None = None
            if path.exists():
                parsed = read_json_file(path)
                if not isinstance(parsed, Mapping):
                    raise LckStopError("Review Prepare in-flight state is invalid")
                if parsed.get("task_number") != task_number:
                    raise LckStopError(
                        "Review Prepare in-flight Task identity is invalid"
                    )
                owner_pid = parsed.get("pid")
                if (
                    not isinstance(owner_pid, int)
                    or isinstance(owner_pid, bool)
                    or owner_pid <= 0
                ):
                    raise LckStopError(
                        "Review Prepare in-flight owner identity is invalid"
                    )
                if self._pid_is_alive(owner_pid):
                    raise LckStopError(
                        f"Review Prepare already in flight for Task #{task_number}"
                    )
                if parsed.get("state") == "handed-off":
                    review_id = parsed.get("review_id")
                    review_root = parsed.get("review_root")
                    guard_exists = (
                        isinstance(review_id, str)
                        and self.guard_path(review_id).exists()
                    )
                    root_exists = (
                        isinstance(review_root, str) and Path(review_root).exists()
                    )
                    if guard_exists and root_exists:
                        raise LckStopError(
                            "Review Prepare handoff is still owned by the prior "
                            f"operation (review {review_id})"
                        )
                existing = dict(parsed)

            operation_id = self.new_id()
            payload: dict[str, Any] = {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "review-prepare-in-flight",
                "operation_id": operation_id,
                "task_number": task_number,
                "pid": os.getpid(),
                "state": "starting",
                "review_root": None,
                "authority": "operation-local in-flight protection only",
            }
            if existing is not None:
                payload["recovered_from"] = existing.get("operation_id")
                payload["previous_review_root"] = existing.get("review_root")
            invocation = ReviewPrepareInvocation(
                path=path,
                operation_id=operation_id,
                _lock_fd=lock_fd,
                recovered=existing,
            )
            invocation.update(payload)
            return invocation
        except BaseException:
            os.close(lock_fd)
            raise

    def release_review_prepare(self, task_number: int, review_id: str) -> None:
        """Release a successful Prepare handoff after Review cleanup."""
        path = self.review_prepare_inflight_path(task_number)
        if not path.exists():
            return
        lock_path = self.review_prepare_lock_path(task_number)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if not path.exists():
                return
            value = read_json_file(path)
            if not isinstance(value, Mapping):
                raise LckStopError("Review Prepare in-flight state is invalid")
            owner_review_id = value.get("review_id")
            if owner_review_id != review_id:
                raise LckStopError(
                    "Review Prepare handoff belongs to a different Review invocation"
                )
            path.unlink()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def write_guard(self, review_id: str, payload: Mapping[str, Any]) -> None:
        atomic_write_json(self.guard_path(review_id), payload)

    def read_guard(self, review_id: str) -> dict[str, Any]:
        value = read_json_file(self.guard_path(review_id))
        if not isinstance(value, dict):
            raise LckStopError("Review invocation guard is not an object")
        return value

    def delete_guard(self, review_id: str) -> None:
        try:
            self.guard_path(review_id).unlink()
        except FileNotFoundError:
            pass

    def write_record(
        self, task_number: int, review_id: str, payload: Mapping[str, Any]
    ) -> Path:
        path = self.record_path(task_number, review_id)
        atomic_write_json(path, payload)
        return path

    def read_record(self, task_number: int, review_id: str) -> dict[str, Any]:
        value = read_json_file(self.record_path(task_number, review_id))
        if not isinstance(value, dict):
            raise LckStopError("Review record is not an object")
        return value

    def write_latest_review(
        self, task_number: int, review_id: str, verdict: str
    ) -> None:
        self._validate_id(review_id)
        atomic_write_json(
            self.latest_review_path(task_number),
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "latest-independent-review",
                "task_number": task_number,
                "review_id": review_id,
                "verdict": verdict,
                "authority": "semantic predecessor only; never mechanical target authority",
            },
        )

    def read_latest_review(self, task_number: int) -> dict[str, Any] | None:
        path = self.latest_review_path(task_number)
        if not path.exists():
            return None
        value = read_json_file(path)
        if not isinstance(value, dict) or value.get("task_number") != task_number:
            raise LckStopError("latest Review state is invalid")
        return value

    def write_review_required(
        self, task_number: int, review_id: str, head_sha: str
    ) -> None:
        self._validate_id(review_id)
        if not is_sha(head_sha):
            raise LckStopError("review-required state needs a valid remediation head")
        atomic_write_json(
            self.review_required_path(task_number),
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "fresh-review-required",
                "task_number": task_number,
                "source_review_id": review_id,
                "remediated_head": head_sha,
                "authority": "negative lifecycle boundary only; current target remains live-resolved",
            },
        )

    def read_review_required(self, task_number: int) -> dict[str, Any] | None:
        path = self.review_required_path(task_number)
        if not path.exists():
            return None
        value = read_json_file(path)
        if not isinstance(value, dict) or value.get("task_number") != task_number:
            raise LckStopError("review-required state is invalid")
        return value

    def clear_review_required(self, task_number: int) -> None:
        try:
            self.review_required_path(task_number).unlink()
        except FileNotFoundError:
            pass

    def write_remediation_session(
        self, task_number: int, payload: Mapping[str, Any]
    ) -> Path:
        path = self.remediation_session_path(task_number)
        existing: Mapping[str, Any] | None = None
        if path.exists():
            value = read_json_file(path)
            if not isinstance(value, Mapping):
                raise LckStopError("Remediation session state is invalid")
            existing = value
        if existing is not None and (
            existing.get("review_id") != payload.get("review_id")
            or existing.get("start_head_sha") != payload.get("start_head_sha")
        ):
            raise LckStopError(
                "another Remediation session is already prepared for this Task"
            )
        atomic_write_json(path, payload)
        return path

    def record_remediation_candidate(
        self,
        task_number: int,
        review_id: str,
        *,
        start_head_sha: str,
        candidate_head_sha: str,
        candidate_tree_oid: str,
    ) -> Path:
        """Bind one exact committed candidate to its prepared Remediation session."""
        session = self.read_remediation_session(task_number)
        if session is None:
            raise LckStopError(
                "cannot record a Remediation candidate without a prepared session"
            )
        if (
            session.get("review_id") != review_id
            or session.get("start_head_sha") != start_head_sha
        ):
            raise LckStopError(
                "Remediation candidate does not belong to the prepared session"
            )
        if not is_sha(candidate_head_sha) or not is_sha(candidate_tree_oid):
            raise LckStopError("Remediation candidate identity is incomplete")
        operation_id = session.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            operation_id = self.new_id()
            session["operation_id"] = operation_id
        candidate = {
            "operation_id": operation_id,
            "start_head_sha": start_head_sha,
            "head_sha": candidate_head_sha,
            "tree_oid": candidate_tree_oid,
        }
        existing = session.get("candidate")
        if existing is not None and existing != candidate:
            raise LckStopError(
                "prepared Remediation session already owns a different candidate"
            )
        session["candidate"] = candidate
        return self.write_remediation_session(task_number, session)

    def read_remediation_session(self, task_number: int) -> dict[str, Any] | None:
        path = self.remediation_session_path(task_number)
        if not path.exists():
            return None
        value = read_json_file(path)
        if not isinstance(value, dict) or value.get("task_number") != task_number:
            raise LckStopError("Remediation session state is invalid")
        return value

    def clear_remediation_session(self, task_number: int) -> None:
        try:
            self.remediation_session_path(task_number).unlink()
        except FileNotFoundError:
            pass

    def write_remediation_no_change_receipt(
        self, task_number: int, review_id: str, payload: Mapping[str, Any]
    ) -> Path:
        path = self.remediation_no_change_receipt_path(task_number, review_id)
        if path.exists():
            existing = read_json_file(path)
            if existing != dict(payload):
                raise LckStopError(
                    "existing Remediation no-change receipt does not match this completion"
                )
            return path
        atomic_write_json(path, payload)
        return path

    def read_remediation_no_change_receipt(
        self, task_number: int, review_id: str
    ) -> dict[str, Any] | None:
        path = self.remediation_no_change_receipt_path(task_number, review_id)
        if not path.exists():
            return None
        value = read_json_file(path)
        if (
            not isinstance(value, dict)
            or value.get("task_number") != task_number
            or value.get("review_id") != review_id
            or value.get("kind") != "remediation-no-change-receipt"
        ):
            raise LckStopError("Remediation no-change receipt is invalid")
        return value


def _identity_from_mapping(value: Mapping[str, Any]) -> ReviewIdentity:
    changed = value.get("changed_files")
    if not isinstance(changed, list) or not all(
        isinstance(item, str) for item in changed
    ):
        raise LckStopError("Review invocation identity has invalid changed-files data")
    fields = {
        "task_number": value.get("task_number"),
        "pr_number": value.get("pr_number"),
        "base_sha": value.get("base_sha"),
        "head_sha": value.get("head_sha"),
        "task_body_sha256": value.get("task_body_sha256"),
        "merge_base_sha": value.get("merge_base_sha"),
        "effective_diff_sha256": value.get("effective_diff_sha256"),
    }
    if not isinstance(fields["task_number"], int) or not isinstance(
        fields["pr_number"], int
    ):
        raise LckStopError("Review invocation identity is incomplete")
    for name in ("base_sha", "head_sha", "merge_base_sha"):
        if not is_sha(fields[name]):
            raise LckStopError(f"Review invocation identity has invalid {name}")
    for name in ("task_body_sha256", "effective_diff_sha256"):
        item = fields[name]
        if not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None:
            raise LckStopError(f"Review invocation identity has invalid {name}")
    raw_research_artifact = value.get("research_artifact")
    if raw_research_artifact is not None and not isinstance(
        raw_research_artifact, Mapping
    ):
        raise LckStopError("Review invocation identity has invalid Research artifact")
    return ReviewIdentity(
        task_number=cast(int, fields["task_number"]),
        pr_number=cast(int, fields["pr_number"]),
        base_sha=cast(str, fields["base_sha"]),
        head_sha=cast(str, fields["head_sha"]),
        task_body_sha256=cast(str, fields["task_body_sha256"]),
        merge_base_sha=cast(str, fields["merge_base_sha"]),
        effective_diff_sha256=cast(str, fields["effective_diff_sha256"]),
        changed_files=tuple(changed),
        research_artifact=(
            dict(raw_research_artifact)
            if isinstance(raw_research_artifact, Mapping)
            else None
        ),
    )


def _assert_review_target_facts_applicable(
    reviewed: ReviewIdentity,
    state: LiveState,
    task_contract: Mapping[str, Any],
) -> None:
    """Reject obvious stale Review identity before computing the current diff.

    This ordering matters when another actor pushed a new head that is visible on
    GitHub but whose Git object is not materialized locally.  Head/base/Task
    staleness must still produce the precise REVIEW_STALE_* result without a
    fetch or a failed local diff probe.
    """
    pr = state.open_pr
    if not isinstance(pr, Mapping):
        raise ReviewStaleError(
            "REVIEW_STALE_PR",
            f"reviewed PR #{reviewed.pr_number} is no longer the unique current OPEN PR",
        )
    current_number = pr.get("number")
    if current_number != reviewed.pr_number:
        raise ReviewStaleError(
            "REVIEW_STALE_PR",
            f"OPEN PR changed from #{reviewed.pr_number} to #{current_number}",
        )
    current_head = _pr_head_sha(pr)
    if current_head != reviewed.head_sha:
        raise ReviewStaleError(
            "REVIEW_STALE_HEAD",
            f"PR head changed from {reviewed.head_sha} to {current_head or 'unavailable'}",
        )
    current_base = _pr_base_sha(pr)
    if current_base != reviewed.base_sha:
        raise ReviewStaleError(
            "REVIEW_STALE_BASE",
            f"PR base changed from {reviewed.base_sha} to {current_base or 'unavailable'}",
        )
    current_task = task_contract.get("body_sha256")
    if current_task != reviewed.task_body_sha256:
        raise ReviewStaleError(
            "REVIEW_STALE_TASK",
            "Task Contract changed since Review Prepare",
        )


def _assert_review_applicable(start: ReviewIdentity, current: ReviewIdentity) -> None:
    """Compare exact reviewed/current identities after basic target facts match."""
    if current.pr_number != start.pr_number:
        raise ReviewStaleError(
            "REVIEW_STALE_PR",
            f"OPEN PR changed from #{start.pr_number} to #{current.pr_number}",
        )
    if current.head_sha != start.head_sha:
        raise ReviewStaleError(
            "REVIEW_STALE_HEAD",
            f"PR head changed from {start.head_sha} to {current.head_sha}",
        )
    if current.base_sha != start.base_sha:
        raise ReviewStaleError(
            "REVIEW_STALE_BASE",
            f"PR base changed from {start.base_sha} to {current.base_sha}",
        )
    if current.task_body_sha256 != start.task_body_sha256:
        raise ReviewStaleError(
            "REVIEW_STALE_TASK",
            "Task Contract changed during this Review invocation",
        )
    if (
        current.merge_base_sha != start.merge_base_sha
        or current.effective_diff_sha256 != start.effective_diff_sha256
        or current.changed_files != start.changed_files
        or current.research_artifact != start.research_artifact
    ):
        raise ReviewStaleError(
            "REVIEW_STALE_DIFF",
            "effective diff or Research artifact changed during this Review invocation",
        )
