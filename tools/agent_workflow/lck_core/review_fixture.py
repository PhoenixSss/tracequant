"""Immutable Review Eval fixture packages and their verification boundary.

The fixture is the authority for Eval.  A branch or tag may help a human find
the package, but the manifest, its digests, and the self-contained Git bundle
are only accepted after a Path load is checked against an independently
retained fixture digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from workflow_common import (
    CommandRunner,
    WorkflowToolError,
    atomic_write_json,
    is_sha,
    read_json_file,
    sha256_bytes,
    sha256_json,
)

from .models import LckStopError
from .review_authority import FrozenReviewAuthority

FIXTURE_SCHEMA_VERSION: Final = 1
FIXTURE_MANIFEST_NAME: Final = "fixture-manifest.json"
REPOSITORY_BUNDLE_NAME: Final = "repository.bundle"
TASK_CONTRACT_NAME: Final = "task-contract.json"
DETERMINISTIC_EVIDENCE_NAME: Final = "deterministic-evidence.json"
_DIGEST_FIELDS: Final = (
    "effective_diff_sha256",
    "task_contract_sha256",
    "deterministic_evidence_sha256",
    "repository_artifact_sha256",
)


def _digest(value: bytes, *, field: str) -> str:
    if not value:
        raise LckStopError(f"Review fixture {field} is empty")
    return sha256_bytes(value)


def _digest_value(value: Any, *, field: str) -> str:
    if isinstance(value, bytes):
        return _digest(value, field=field)
    if isinstance(value, str):
        return _digest(value.encode("utf-8"), field=field)
    return sha256_json(value)


def _required_digest(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise LckStopError(f"Review fixture {field} is unavailable")
    try:
        int(value, 16)
    except ValueError as exc:
        raise LckStopError(f"Review fixture {field} is not a SHA-256 digest") from exc
    return value.lower()


def _required_relative_path(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise LckStopError(f"Review fixture {field} path is invalid")
    path = Path(value)
    if ".." in path.parts or path == Path("."):
        raise LckStopError(f"Review fixture {field} path escapes the fixture")
    return path.as_posix()


def _safe_artifact(root: Path, relative: str, *, field: str) -> Path:
    candidate = root / relative
    if candidate.is_symlink():
        raise LckStopError(f"Review fixture {field} artifact is unavailable")
    path = candidate.resolve(strict=False)
    try:
        path.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise LckStopError(f"Review fixture {field} escapes the fixture") from exc
    if path.is_symlink() or not path.is_file():
        raise LckStopError(f"Review fixture {field} artifact is unavailable")
    return path


def _manifest_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove self-referential digests before hashing the manifest."""
    return {
        str(key): item
        for key, item in value.items()
        if key not in {"fixture_manifest_sha256", "fixture_digest"}
    }


def _expected_fixture_digest(value: Any) -> str:
    if value is None:
        raise LckStopError(
            "Review fixture requires an independently trusted expected fixture digest"
        )
    return _required_digest(value, field="expected fixture digest")


def _verify_expected_fixture_digest(
    fixture: FrozenReviewFixture, expected_fixture_digest: Any
) -> None:
    expected = _expected_fixture_digest(expected_fixture_digest)
    if fixture.fixture_digest != expected:
        raise LckStopError(
            "Review fixture identity does not match independently trusted digest"
        )


@dataclass(frozen=True, slots=True)
class FrozenReviewFixture:
    """A verified, self-contained authority package for one Review Eval."""

    root: Path
    fixture_id: str
    fixture_schema_version: int
    base_sha: str
    head_sha: str
    effective_diff_sha256: str
    task_contract_sha256: str
    deterministic_evidence_sha256: str
    repository_artifact_sha256: str
    fixture_manifest_sha256: str
    fixture_digest: str
    repository_bundle: str = REPOSITORY_BUNDLE_NAME
    task_contract_artifact: str = TASK_CONTRACT_NAME
    deterministic_evidence_artifact: str = DETERMINISTIC_EVIDENCE_NAME

    def __post_init__(self) -> None:
        root = self.root.resolve(strict=False)
        object.__setattr__(self, "root", root)
        if not root.is_dir():
            raise LckStopError("Review fixture root is unavailable")
        if not isinstance(self.fixture_id, str) or not self.fixture_id.strip():
            raise LckStopError("Review fixture ID is unavailable")
        if self.fixture_schema_version != FIXTURE_SCHEMA_VERSION:
            raise LckStopError("Review fixture schema version is unsupported")
        for field in ("base_sha", "head_sha"):
            if not is_sha(getattr(self, field)):
                raise LckStopError(f"Review fixture {field} is unavailable")
        for field in (*_DIGEST_FIELDS, "fixture_manifest_sha256", "fixture_digest"):
            _required_digest(getattr(self, field), field=field)
        object.__setattr__(
            self,
            "repository_bundle",
            _required_relative_path(self.repository_bundle, field="repository bundle"),
        )
        object.__setattr__(
            self,
            "task_contract_artifact",
            _required_relative_path(self.task_contract_artifact, field="Task Contract"),
        )
        object.__setattr__(
            self,
            "deterministic_evidence_artifact",
            _required_relative_path(
                self.deterministic_evidence_artifact,
                field="deterministic evidence",
            ),
        )

    @property
    def manifest_path(self) -> Path:
        return self.root / FIXTURE_MANIFEST_NAME

    @property
    def repository_bundle_path(self) -> Path:
        return _safe_artifact(
            self.root, self.repository_bundle, field="repository bundle"
        )

    @property
    def authority(self) -> FrozenReviewAuthority:
        return FrozenReviewAuthority(
            fixture_id=self.fixture_id,
            base_sha=self.base_sha,
            head_sha=self.head_sha,
            effective_diff_sha256=self.effective_diff_sha256,
            task_contract_sha256=self.task_contract_sha256,
            deterministic_evidence_sha256=self.deterministic_evidence_sha256,
            repository_artifact_sha256=self.repository_artifact_sha256,
            fixture_schema_version=self.fixture_schema_version,
            fixture_manifest_sha256=self.fixture_manifest_sha256,
            fixture_digest=self.fixture_digest,
        )

    @property
    def identity_digest(self) -> str:
        return self.fixture_digest

    def identity_payload(self) -> dict[str, Any]:
        return {
            "fixture_schema_version": self.fixture_schema_version,
            "fixture_id": self.fixture_id,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "effective_diff_sha256": self.effective_diff_sha256,
            "task_contract_sha256": self.task_contract_sha256,
            "deterministic_evidence_sha256": self.deterministic_evidence_sha256,
            "repository_artifact_sha256": self.repository_artifact_sha256,
            "fixture_manifest_sha256": self.fixture_manifest_sha256,
        }

    def manifest_payload(self) -> dict[str, Any]:
        return {
            **self.identity_payload(),
            "artifacts": {
                "repository_bundle": {
                    "path": self.repository_bundle,
                    "sha256": self.repository_artifact_sha256,
                },
                "task_contract": {
                    "path": self.task_contract_artifact,
                    "sha256": self.task_contract_sha256,
                },
                "deterministic_evidence": {
                    "path": self.deterministic_evidence_artifact,
                    "sha256": self.deterministic_evidence_sha256,
                },
            },
        }

    def to_manifest(self) -> dict[str, Any]:
        payload = self.manifest_payload()
        return {
            **payload,
            "fixture_manifest_sha256": self.fixture_manifest_sha256,
            "fixture_digest": self.fixture_digest,
        }

    @classmethod
    def from_mapping(cls, root: Path, value: Mapping[str, Any]) -> FrozenReviewFixture:
        if not isinstance(value, Mapping):
            raise LckStopError("Review fixture manifest is not an object")
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, Mapping):
            raise LckStopError("Review fixture manifest artifacts are unavailable")

        def artifact(name: str, default: str) -> tuple[str, str]:
            item = artifacts.get(name)
            if not isinstance(item, Mapping):
                raise LckStopError(f"Review fixture artifact {name} is unavailable")
            return (
                _required_relative_path(item.get("path", default), field=name),
                _required_digest(item.get("sha256"), field=f"{name} digest"),
            )

        repository_bundle, _ = artifact("repository_bundle", REPOSITORY_BUNDLE_NAME)
        task_contract, _ = artifact("task_contract", TASK_CONTRACT_NAME)
        evidence, _ = artifact("deterministic_evidence", DETERMINISTIC_EVIDENCE_NAME)
        return cls(
            root=root,
            fixture_id=value.get("fixture_id", ""),
            fixture_schema_version=value.get("fixture_schema_version", 0),
            base_sha=value.get("base_sha", ""),
            head_sha=value.get("head_sha", ""),
            effective_diff_sha256=value.get("effective_diff_sha256", ""),
            task_contract_sha256=value.get("task_contract_sha256", ""),
            deterministic_evidence_sha256=value.get(
                "deterministic_evidence_sha256", ""
            ),
            repository_artifact_sha256=value.get("repository_artifact_sha256", ""),
            fixture_manifest_sha256=value.get("fixture_manifest_sha256", ""),
            fixture_digest=value.get("fixture_digest", ""),
            repository_bundle=repository_bundle,
            task_contract_artifact=task_contract,
            deterministic_evidence_artifact=evidence,
        )

    @classmethod
    def from_manifest(
        cls,
        path: Path,
        *,
        expected_fixture_digest: str | None = None,
    ) -> FrozenReviewFixture:
        """Load a manifest only when its identity has an external anchor."""
        expected = _expected_fixture_digest(expected_fixture_digest)
        manifest = path
        if manifest.is_dir():
            manifest = manifest / FIXTURE_MANIFEST_NAME
        if manifest.is_symlink():
            raise LckStopError("Review fixture manifest is unavailable")
        manifest = manifest.resolve(strict=False)
        try:
            value = read_json_file(manifest)
        except WorkflowToolError as exc:
            raise LckStopError("Review fixture manifest cannot be read") from exc
        fixture = cls.from_mapping(manifest.parent, value)
        _verify_expected_fixture_digest(fixture, expected)
        return fixture.verify(expected_fixture_digest=expected)

    def verify(
        self,
        runner: Any | None = None,
        *,
        expected_fixture_digest: str | None = None,
    ) -> FrozenReviewFixture:
        """Verify the manifest and every protected artifact before Eval."""
        if expected_fixture_digest is not None:
            _verify_expected_fixture_digest(self, expected_fixture_digest)
        try:
            recorded = read_json_file(self.manifest_path)
        except WorkflowToolError as exc:
            raise LckStopError("Review fixture manifest cannot be read") from exc
        if not isinstance(recorded, Mapping):
            raise LckStopError("Review fixture manifest is not an object")
        if self.manifest_path.is_symlink() or not self.manifest_path.is_file():
            raise LckStopError("Review fixture manifest is unavailable")
        current = type(self).from_mapping(self.root, recorded)
        if current.to_manifest() != self.to_manifest():
            raise LckStopError("Review fixture manifest identity does not match")
        manifest_digest = sha256_json(_manifest_payload(recorded))
        if manifest_digest != self.fixture_manifest_sha256:
            raise LckStopError("Review fixture manifest digest mismatch")
        computed_fixture_digest = sha256_json(self.identity_payload())
        if computed_fixture_digest != self.fixture_digest:
            raise LckStopError("Review fixture identity digest mismatch")

        recorded_artifacts = recorded.get("artifacts")
        if not isinstance(recorded_artifacts, Mapping):
            raise LckStopError("Review fixture manifest artifacts are unavailable")
        expected_artifacts = {
            "repository_bundle": (
                self.repository_bundle,
                self.repository_artifact_sha256,
            ),
            "task_contract": (self.task_contract_artifact, self.task_contract_sha256),
            "deterministic_evidence": (
                self.deterministic_evidence_artifact,
                self.deterministic_evidence_sha256,
            ),
        }
        for name, (expected_path, expected_digest) in expected_artifacts.items():
            item = recorded_artifacts.get(name)
            if not isinstance(item, Mapping) or item.get("path") != expected_path:
                raise LckStopError(f"Review fixture artifact {name} is inconsistent")
            if item.get("sha256") != expected_digest:
                raise LckStopError(f"Review fixture artifact {name} digest mismatch")

        artifacts = (
            (
                self.repository_bundle,
                self.repository_artifact_sha256,
                "repository bundle",
            ),
            (
                self.task_contract_artifact,
                self.task_contract_sha256,
                "Task Contract",
            ),
            (
                self.deterministic_evidence_artifact,
                self.deterministic_evidence_sha256,
                "deterministic evidence",
            ),
        )
        for relative, expected, field in artifacts:
            path = _safe_artifact(self.root, relative, field=field)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise LckStopError(f"Review fixture {field} digest mismatch")

        if runner is not None:
            verify_root = getattr(runner, "repo_root", None)
            if isinstance(verify_root, Path) and (verify_root / ".git").exists():
                result = runner.run(
                    ["git", "bundle", "verify", str(self.repository_bundle_path)],
                    command_id="review-fixture-bundle-verify",
                    cwd=verify_root,
                )
                if result.returncode != 0:
                    raise LckStopError("Review fixture Git bundle cannot be verified")
        return self


class ReviewFixtureBuilder:
    """Create a fixture package whose contents can outlive the source clone."""

    def __init__(self, repository: Path, *, runner: Any | None = None) -> None:
        self.repository = repository.resolve()
        if not self.repository.is_dir() or not (self.repository / ".git").exists():
            raise LckStopError("Review fixture source repository is unavailable")
        self.runner = runner or CommandRunner(self.repository)

    def create(
        self,
        destination: Path,
        *,
        fixture_id: str,
        base_sha: str,
        head_sha: str,
        task_contract: Any,
        deterministic_evidence: Any,
        effective_diff_sha256: str | None = None,
    ) -> FrozenReviewFixture:
        if not is_sha(base_sha) or not is_sha(head_sha):
            raise LckStopError("Review fixture base/head identity is unavailable")
        destination = destination.resolve(strict=False)
        if destination.exists() and any(destination.iterdir()):
            raise LckStopError("Review fixture destination must be empty")
        destination.mkdir(parents=True, exist_ok=True)
        bundle = destination / REPOSITORY_BUNDLE_NAME
        current_head = self.runner.run(
            ["git", "rev-parse", "HEAD"],
            command_id="review-fixture-source-head",
            cwd=self.repository,
        )
        bundle_revision = (
            "HEAD"
            if current_head.returncode == 0 and current_head.stdout.strip() == head_sha
            else "--all"
        )
        result = self.runner.run(
            # ``git bundle`` accepts symbolic refs more consistently than raw
            # object IDs across supported Git versions.  The bundle may carry
            # navigation refs, but verification and materialization below use
            # only the explicit immutable base/head SHAs.
            ["git", "bundle", "create", str(bundle), bundle_revision],
            command_id="review-fixture-bundle-create",
            cwd=self.repository,
        )
        if result.returncode != 0:
            raise LckStopError(
                "Review fixture Git bundle creation failed: "
                + (result.stderr.strip() or f"exit {result.returncode}")
            )
        contract_path = destination / TASK_CONTRACT_NAME
        evidence_path = destination / DETERMINISTIC_EVIDENCE_NAME
        contract_path.write_text(
            _canonical_artifact_text(task_contract), encoding="utf-8", newline="\n"
        )
        evidence_path.write_text(
            _canonical_artifact_text(deterministic_evidence),
            encoding="utf-8",
            newline="\n",
        )
        if effective_diff_sha256 is None:
            diff = self.runner.run(
                [
                    "git",
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    "--no-textconv",
                    f"{base_sha}...{head_sha}",
                ],
                command_id="review-fixture-effective-diff",
                cwd=self.repository,
            )
            if diff.returncode != 0:
                raise LckStopError("Review fixture effective diff is unavailable")
            effective_diff_sha256 = hashlib.sha256(
                diff.stdout.encode("utf-8", errors="replace")
            ).hexdigest()
        effective_diff_sha256 = _required_digest(
            effective_diff_sha256, field="effective diff"
        )
        manifest_payload = {
            "fixture_schema_version": FIXTURE_SCHEMA_VERSION,
            "fixture_id": fixture_id,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "effective_diff_sha256": effective_diff_sha256,
            "task_contract_sha256": _digest(
                contract_path.read_bytes(), field="Task Contract"
            ),
            "deterministic_evidence_sha256": _digest(
                evidence_path.read_bytes(), field="deterministic evidence"
            ),
            "repository_artifact_sha256": _digest(
                bundle.read_bytes(), field="repository bundle"
            ),
            "artifacts": {
                "repository_bundle": {
                    "path": REPOSITORY_BUNDLE_NAME,
                    "sha256": _digest(bundle.read_bytes(), field="repository bundle"),
                },
                "task_contract": {
                    "path": TASK_CONTRACT_NAME,
                    "sha256": _digest(
                        contract_path.read_bytes(), field="Task Contract"
                    ),
                },
                "deterministic_evidence": {
                    "path": DETERMINISTIC_EVIDENCE_NAME,
                    "sha256": _digest(
                        evidence_path.read_bytes(), field="deterministic evidence"
                    ),
                },
            },
        }
        manifest_sha = sha256_json(manifest_payload)
        identity = {
            "fixture_schema_version": FIXTURE_SCHEMA_VERSION,
            "fixture_id": fixture_id,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "effective_diff_sha256": effective_diff_sha256,
            "task_contract_sha256": manifest_payload["task_contract_sha256"],
            "deterministic_evidence_sha256": manifest_payload[
                "deterministic_evidence_sha256"
            ],
            "repository_artifact_sha256": manifest_payload[
                "repository_artifact_sha256"
            ],
            "fixture_manifest_sha256": manifest_sha,
        }
        fixture_digest = sha256_json(identity)
        atomic_write_json(
            destination / FIXTURE_MANIFEST_NAME,
            {
                **manifest_payload,
                "fixture_manifest_sha256": manifest_sha,
                "fixture_digest": fixture_digest,
            },
        )
        return FrozenReviewFixture.from_manifest(
            destination,
            expected_fixture_digest=fixture_digest,
        ).verify(self.runner, expected_fixture_digest=fixture_digest)


def _canonical_artifact_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def load_frozen_review_fixture(
    path: Path, *, expected_fixture_digest: str | None = None
) -> FrozenReviewFixture:
    """Load a fixture only against an independently retained identity digest."""
    return FrozenReviewFixture.from_manifest(
        path,
        expected_fixture_digest=expected_fixture_digest,
    )


ReviewFixture = FrozenReviewFixture
ReviewEvalFixture = FrozenReviewFixture


__all__ = [
    "DETERMINISTIC_EVIDENCE_NAME",
    "FIXTURE_MANIFEST_NAME",
    "FIXTURE_SCHEMA_VERSION",
    "FrozenReviewFixture",
    "REPOSITORY_BUNDLE_NAME",
    "ReviewEvalFixture",
    "ReviewFixture",
    "ReviewFixtureBuilder",
    "load_frozen_review_fixture",
]
