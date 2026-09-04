"""Execution boundary for Review Eval's Harness / Subject / Run planes.

Review Eval deliberately does not reuse :mod:`lck_core.review`'s live-state
resolver.  The caller supplies a :class:`FrozenReviewAuthority`, the current
checkout remains the Harness, and only a fresh Run-owned Subject workspace is
materialized for inspection.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar

from workflow_common import (
    CommandResult,
    CommandRunner,
    WorkflowToolError,
    atomic_write_json,
    read_json_file,
)

from .models import LCK_SCHEMA_VERSION, LckStopError
from .review_authority import (
    FrozenReviewAuthority,
    require_frozen_review_authority,
)

T = TypeVar("T")


class SubjectMaterializer(Protocol):
    """Materialize one frozen Subject below the supplied destination only."""

    def materialize(
        self, authority: FrozenReviewAuthority, destination: Path
    ) -> None: ...


SubjectMaterializerCallable = Callable[[FrozenReviewAuthority, Path], None]


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _assert_distinct_workspaces(harness_root: Path, subject_root: Path) -> None:
    harness = harness_root.resolve(strict=False)
    subject = subject_root.resolve(strict=False)
    if harness == subject or _within(harness, subject) or _within(subject, harness):
        raise LckStopError(
            "Review Eval Harness and Subject workspaces must be independent"
        )


@dataclass(frozen=True, slots=True)
class ReviewEvalRunContext:
    """Immutable identity and paths for one isolated Review Eval Run."""

    run_id: str
    authority: FrozenReviewAuthority
    harness_root: Path
    run_root: Path
    subject_root: Path
    result_root: Path
    _workspace: ReviewEvalWorkspaceManager = field(repr=False, compare=False)

    @property
    def execution_cwd(self) -> Path:
        """The current Harness checkout used to invoke the Eval entrypoint."""
        return self.harness_root

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "review-eval-run",
            "status": "READY_FOR_EVAL",
            "run_id": self.run_id,
            "authority": self.authority.to_dict(),
            "harness_root": str(self.harness_root),
            "run_root": str(self.run_root),
            "subject_root": str(self.subject_root),
            "result_root": str(self.result_root),
            "planes": {
                "harness": "current-checkout",
                "subject": "frozen-fixture-materialization",
                "run": "operation-owned-isolated-state",
            },
            "execution_cwd": str(self.harness_root),
            "subject_on_import_path": False,
            "mechanical_authority": "explicit FrozenReviewAuthority",
            "forbidden_authority": [
                "current GitHub PR resolution",
                "production Review live PR authority",
            ],
        }

    def result_path(self, name: str) -> Path:
        """Return a safe path for future Run-owned result storage."""
        candidate = (self.result_root / name).resolve(strict=False)
        if not _within(candidate, self.result_root) or candidate == self.result_root:
            raise LckStopError("Review Eval result path must remain inside the Run")
        return candidate

    def close(self) -> None:
        self._workspace.close(self)

    def __enter__(self) -> ReviewEvalRunContext:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class ReviewEvalExecution[T]:
    """Result of running the current Harness against one Eval Subject."""

    run: ReviewEvalRunContext
    value: T

    @property
    def run_id(self) -> str:
        return self.run.run_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LCK_SCHEMA_VERSION,
            "kind": "review-eval-execution",
            "status": "COMPLETED",
            "run": self.run.to_dict(),
            "value": self.value,
        }

    def close(self) -> None:
        self.run.close()


class ReviewEvalWorkspaceManager:
    """Create and clean only Run-owned temporary Eval workspaces."""

    OWNER_FILE: Final = ".review-eval-owner.json"
    RUN_PREFIX: Final = "tracequant-lck-review-eval-"

    def __init__(self, root: Path | None = None) -> None:
        default_root = Path(tempfile.gettempdir()) / "tracequant-lck-review-eval-runs"
        self.root = (root or default_root).resolve(strict=False)
        if self.root == Path("/") or self.root.name in {"", ".", ".."}:
            raise LckStopError("Review Eval workspace root is too broad")
        self.root.mkdir(parents=True, exist_ok=True)

    def reserve(
        self,
        authority: FrozenReviewAuthority,
        harness_root: Path,
    ) -> ReviewEvalRunContext:
        authority = require_frozen_review_authority(authority)
        harness_root = harness_root.resolve()
        if not harness_root.is_dir():
            raise LckStopError("Review Eval Harness checkout is unavailable")

        run_id = uuid.uuid4().hex
        run_root = (self.root / f"{self.RUN_PREFIX}{run_id}").resolve()
        if run_root.exists():
            raise LckStopError("Review Eval Run workspace identity already exists")
        subject_root = run_root / "subject"
        result_root = run_root / "results"
        _assert_distinct_workspaces(harness_root, subject_root)
        run_root.mkdir(parents=True)
        subject_root.mkdir()
        result_root.mkdir()
        atomic_write_json(
            run_root / self.OWNER_FILE,
            {
                "schema_version": LCK_SCHEMA_VERSION,
                "kind": "review-eval-run-owner",
                "run_id": run_id,
                "authority": authority.to_dict(),
                "harness_root": str(harness_root),
                "run_root": str(run_root),
                "subject_root": str(subject_root),
                "result_root": str(result_root),
                "authority_note": (
                    "frozen Eval authority only; never a production Review target"
                ),
            },
        )
        return ReviewEvalRunContext(
            run_id=run_id,
            authority=authority,
            harness_root=harness_root,
            run_root=run_root,
            subject_root=subject_root,
            result_root=result_root,
            _workspace=self,
        )

    def _assert_owned(self, run: ReviewEvalRunContext) -> None:
        root = run.run_root.resolve(strict=False)
        if not _within(root, self.root) or root == self.root:
            raise LckStopError("Review Eval Run cleanup path is not Run-owned")
        owner_path = root / self.OWNER_FILE
        try:
            owner = read_json_file(owner_path)
        except WorkflowToolError as exc:
            raise LckStopError(
                "Review Eval Run ownership marker is unavailable"
            ) from exc
        if not isinstance(owner, Mapping):
            raise LckStopError("Review Eval Run ownership marker is invalid")
        if (
            owner.get("kind") != "review-eval-run-owner"
            or owner.get("run_id") != run.run_id
            or owner.get("run_root") != str(root)
        ):
            raise LckStopError("Review Eval Run ownership marker does not match")

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

    def close(self, run: ReviewEvalRunContext) -> None:
        if run._workspace is not self:
            raise LckStopError("Review Eval Run belongs to another workspace manager")
        if not run.run_root.exists():
            return
        self._assert_owned(run)
        self._make_removable(run.run_root)
        shutil.rmtree(run.run_root, ignore_errors=True)
        if run.run_root.exists():
            raise LckStopError("failed to remove Review Eval Run workspace")


class GitFrozenSubjectMaterializer:
    """Materialize explicit frozen commits from a local Git source.

    This adapter is intentionally source-oriented, not GitHub-oriented.  It
    never resolves a PR and it checks out the frozen head in a detached
    Subject clone while the caller continues executing from the Harness.
    """

    def __init__(
        self,
        source_repository: Path,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.source_repository = source_repository.resolve()
        self.runner = runner or CommandRunner(self.source_repository)

    def _ensure_commit(self, destination: Path, sha: str, label: str) -> None:
        available = self.runner.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            command_id=f"review-eval-subject-{label}-available",
            cwd=destination,
        )
        if available.returncode == 0:
            return
        fetched = self.runner.run(
            ["git", "fetch", "--no-tags", "origin", sha],
            command_id=f"review-eval-subject-{label}-fetch",
            cwd=destination,
            retries=1,
        )
        if fetched.returncode != 0:
            raise LckStopError(
                f"frozen Subject {label} commit is unavailable: "
                + (fetched.stderr.strip() or f"exit {fetched.returncode}")
            )
        verified = self.runner.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            command_id=f"review-eval-subject-{label}-verified",
            cwd=destination,
        )
        if verified.returncode != 0:
            raise LckStopError(f"frozen Subject does not contain its {label} commit")

    def materialize(
        self,
        authority: FrozenReviewAuthority,
        destination: Path,
    ) -> None:
        authority = require_frozen_review_authority(authority)
        source = self.source_repository
        destination = destination.resolve()
        if not source.is_dir() or not (source / ".git").exists():
            raise LckStopError("frozen Subject source repository is unavailable")
        if (
            source == destination
            or _within(destination, source)
            or _within(source, destination)
        ):
            raise LckStopError(
                "frozen Subject destination must not be inside its source"
            )
        clone = self.runner.run(
            [
                "git",
                "clone",
                "--no-checkout",
                "--no-hardlinks",
                str(source),
                str(destination),
            ],
            command_id="review-eval-subject-clone",
        )
        if clone.returncode != 0:
            shutil.rmtree(destination, ignore_errors=True)
            raise LckStopError(
                "cannot create frozen Review Eval Subject: "
                + (clone.stderr.strip() or f"exit {clone.returncode}")
            )
        try:
            self._ensure_commit(destination, authority.base_sha, "base")
            self._ensure_commit(destination, authority.head_sha, "head")
            checkout = self.runner.run(
                ["git", "checkout", "--detach", authority.head_sha],
                command_id="review-eval-subject-checkout",
                cwd=destination,
            )
            if checkout.returncode != 0:
                raise LckStopError(
                    "cannot checkout frozen Review Eval Subject HEAD: "
                    + (checkout.stderr.strip() or f"exit {checkout.returncode}")
                )
            status = self.runner.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                command_id="review-eval-subject-clean",
                cwd=destination,
            )
            head = self.runner.run(
                ["git", "rev-parse", "HEAD"],
                command_id="review-eval-subject-head",
                cwd=destination,
            )
            if status.returncode != 0 or head.returncode != 0:
                raise LckStopError("cannot verify frozen Review Eval Subject")
            if status.stdout.strip() or head.stdout.strip() != authority.head_sha:
                raise LckStopError("frozen Review Eval Subject is not clean and exact")
        except BaseException:
            ReviewEvalWorkspaceManager._make_removable(destination)
            shutil.rmtree(destination, ignore_errors=True)
            raise


def _call_materializer(
    materializer: SubjectMaterializer | SubjectMaterializerCallable,
    authority: FrozenReviewAuthority,
    destination: Path,
) -> None:
    method = getattr(materializer, "materialize", None)
    if callable(method):
        method(authority, destination)
    elif callable(materializer):
        materializer(authority, destination)
    else:
        raise TypeError("Review Eval Subject materializer is not callable")


class ReviewEvalRunner:
    """Run a current Harness entrypoint against a frozen Subject workspace."""

    def __init__(
        self,
        harness_root: Path | None = None,
        *,
        workspace: ReviewEvalWorkspaceManager | None = None,
        workspace_root: Path | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.harness_root = (harness_root or Path.cwd()).resolve()
        if not self.harness_root.is_dir():
            raise LckStopError("Review Eval Harness checkout is unavailable")
        if workspace is not None and workspace_root is not None:
            raise ValueError("provide workspace or workspace_root, not both")
        self.workspace = workspace or ReviewEvalWorkspaceManager(workspace_root)
        self.command_runner = command_runner or CommandRunner(self.harness_root)

    def start(
        self,
        authority: FrozenReviewAuthority,
        materializer: SubjectMaterializer | SubjectMaterializerCallable,
    ) -> ReviewEvalRunContext:
        authority = require_frozen_review_authority(authority)
        run = self.workspace.reserve(authority, self.harness_root)
        try:
            _call_materializer(materializer, authority, run.subject_root)
            subject = run.subject_root.resolve()
            _assert_distinct_workspaces(run.harness_root, subject)
            if subject != run.subject_root or not run.subject_root.is_dir():
                raise LckStopError(
                    "Review Eval Subject materializer escaped its Run workspace"
                )
            return run
        except BaseException:
            self.workspace.close(run)
            raise

    def prepare(
        self,
        authority: FrozenReviewAuthority,
        materializer: SubjectMaterializer | SubjectMaterializerCallable,
    ) -> ReviewEvalRunContext:
        """Named entrypoint for callers that separate prepare from execution."""
        return self.start(authority, materializer)

    def run(
        self,
        authority: FrozenReviewAuthority,
        materializer: SubjectMaterializer | SubjectMaterializerCallable,
        evaluator: Callable[[ReviewEvalRunContext], T],
    ) -> ReviewEvalExecution[T]:
        """Materialize a Subject, then invoke the evaluator from the Harness."""
        if not callable(evaluator):
            raise TypeError("Review Eval evaluator is not callable")
        run = self.start(authority, materializer)
        try:
            value = evaluator(run)
        except BaseException:
            self.workspace.close(run)
            raise
        return ReviewEvalExecution(run=run, value=value)

    def execute_command(
        self,
        authority: FrozenReviewAuthority,
        materializer: SubjectMaterializer | SubjectMaterializerCallable,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> ReviewEvalExecution[CommandResult]:
        """Execute a Harness-owned command with the Subject as explicit input.

        The command's working directory is always the current Harness.  The
        Subject is not added to ``PYTHONPATH`` and never becomes the process
        checkout, so historical workflow files cannot replace the candidate
        Harness implementation.
        """
        if not argv:
            raise ValueError("Review Eval command argv cannot be empty")
        run = self.start(authority, materializer)
        command_env = dict(env or {})
        command_env.update(
            {
                "TRACEQUANT_REVIEW_EVAL_RUN_ID": run.run_id,
                "TRACEQUANT_REVIEW_EVAL_FIXTURE_ID": run.authority.fixture_id,
                "TRACEQUANT_REVIEW_EVAL_BASE_SHA": run.authority.base_sha,
                "TRACEQUANT_REVIEW_EVAL_HEAD_SHA": run.authority.head_sha,
                "TRACEQUANT_REVIEW_EVAL_SUBJECT_ROOT": str(run.subject_root),
                "TRACEQUANT_REVIEW_EVAL_HARNESS_ROOT": str(run.harness_root),
            }
        )
        existing_pythonpath = command_env.get("PYTHONPATH") or os.environ.get(
            "PYTHONPATH"
        )
        if existing_pythonpath:
            allowed = [
                item
                for item in existing_pythonpath.split(os.pathsep)
                if not _within(
                    Path(item) if Path(item).is_absolute() else run.harness_root / item,
                    run.subject_root,
                )
            ]
            if allowed:
                command_env["PYTHONPATH"] = os.pathsep.join(allowed)
            else:
                command_env.pop("PYTHONPATH", None)
        try:
            result = self.command_runner.run(
                list(argv),
                command_id="review-eval-harness-entrypoint",
                cwd=run.harness_root,
                env=command_env,
            )
        except BaseException:
            self.workspace.close(run)
            raise
        return ReviewEvalExecution(run=run, value=result)


__all__ = [
    "GitFrozenSubjectMaterializer",
    "ReviewEvalExecution",
    "ReviewEvalRunContext",
    "ReviewEvalRunner",
    "ReviewEvalWorkspaceManager",
    "SubjectMaterializer",
]
