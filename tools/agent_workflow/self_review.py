#!/usr/bin/env python3
"""Structured self-review artifact helper for delivery Skills.

This is NOT a Runner. It provides the self-review schema, identity binding,
staleness detection, and structural validation. The model fills in semantic
content — this module only enforces structural completeness and evidence
constraints.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from workflow_common import (
    WorkflowToolError,
    atomic_write_json,
    sha256_bytes,
)

SCHEMA_VERSION: Final = 1
SELF_REVIEW_ROOT: Final = ".agents/evidence.local/self-reviews"
VALID_STATUSES: Final = frozenset({"verified", "partially_verified", "not_verified"})
VALID_OVERALLS: Final = frozenset({"verified", "partial", "not_verified"})
_REQUIRED_TOP_FIELDS: Final = (
    "schema_version",
    "task",
    "base_sha",
    "head_sha",
    "effective_diff_sha256",
    "pr",
    "generated_at",
    "areas",
    "acceptance_criteria",
    "overall",
)
_REQUIRED_AREA_FIELDS: Final = (
    "name",
    "files",
    "status",
    "implementation_evidence",
    "validation_evidence",
    "findings",
    "remaining_risk",
)
_REQUIRED_AC_FIELDS: Final = (
    "id",
    "text",
    "status",
    "implementation_evidence",
    "validation_evidence",
    "remaining_risk",
)


class SelfReviewError(WorkflowToolError):
    """Expected error from self-review artifact operations."""


def _ensure_self_review_root(repo_root: Path) -> Path:
    """Create the self-review output directory under the evidence root."""
    evidence_root = repo_root / ".agents/evidence.local"
    if not evidence_root.exists():
        raise SelfReviewError(
            ".agents/evidence.local/ directory must exist before self-review "
            "artifact generation"
        )
    root = repo_root / SELF_REVIEW_ROOT
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def validate_artifact_structure(artifact: Mapping[str, Any]) -> list[str]:
    """Validate structural completeness of a self-review artifact.

    Returns a list of violation strings. An empty list means structurally valid.
    This does NOT verify semantic correctness — only field presence, types, and
    evidence constraints.
    """
    violations: list[str] = []

    # Top-level fields
    for field in _REQUIRED_TOP_FIELDS:
        if field not in artifact:
            violations.append(f"missing top-level field: {field}")

    if artifact.get("schema_version") != SCHEMA_VERSION:
        violations.append(
            f"schema_version must be {SCHEMA_VERSION}, "
            f"got {artifact.get('schema_version')!r}"
        )

    overall = artifact.get("overall")
    if overall not in VALID_OVERALLS:
        violations.append(f"invalid overall: {overall!r}")

    # Areas validation
    areas = artifact.get("areas")
    if not isinstance(areas, list) or len(areas) == 0:
        violations.append("areas must be a non-empty list")
        return violations  # can't validate further without areas

    all_area_verified = True
    for idx, area in enumerate(areas):
        if not isinstance(area, dict):
            violations.append(f"area[{idx}] is not an object")
            continue

        for field in _REQUIRED_AREA_FIELDS:
            if field not in area:
                violations.append(f"area[{idx}] missing field: {field}")

        status = area.get("status")
        if status not in VALID_STATUSES:
            violations.append(f"area[{idx}] invalid status: {status!r}")
        if status != "verified":
            all_area_verified = False

        # Evidence constraint: non-verified must have remaining_risk
        if status in {"partially_verified", "not_verified"}:
            risk = area.get("remaining_risk")
            if not isinstance(risk, str) or not risk.strip():
                violations.append(
                    f"area[{idx}] ({area.get('name', 'unnamed')!r}) "
                    f"is {status} but remaining_risk is empty"
                )

        # Evidence constraint: verified must have evidence
        if status == "verified":
            impl_evidence = area.get("implementation_evidence", [])
            val_evidence = area.get("validation_evidence", [])
            if (not isinstance(impl_evidence, list) or len(impl_evidence) == 0) and (
                not isinstance(val_evidence, list) or len(val_evidence) == 0
            ):
                violations.append(
                    f"area[{idx}] ({area.get('name', 'unnamed')!r}) "
                    f"is verified but has no evidence entries"
                )

        # Files must be a non-empty list
        files = area.get("files")
        if not isinstance(files, list) or len(files) == 0:
            violations.append(
                f"area[{idx}] ({area.get('name', 'unnamed')!r}) "
                f"has empty or missing files list"
            )

    # Acceptance criteria validation
    acs = artifact.get("acceptance_criteria")
    if not isinstance(acs, list) or len(acs) == 0:
        violations.append("acceptance_criteria must be a non-empty list")
    else:
        all_ac_verified = True
        for idx, ac in enumerate(acs):
            if not isinstance(ac, dict):
                violations.append(f"acceptance_criteria[{idx}] is not an object")
                continue

            for field in _REQUIRED_AC_FIELDS:
                if field not in ac:
                    violations.append(
                        f"acceptance_criteria[{idx}] missing field: {field}"
                    )

            status = ac.get("status")
            if status not in VALID_STATUSES:
                violations.append(
                    f"acceptance_criteria[{idx}] invalid status: {status!r}"
                )
            if status != "verified":
                all_ac_verified = False

            # Evidence constraint: non-verified must have remaining_risk
            if status in {"partially_verified", "not_verified"}:
                risk = ac.get("remaining_risk")
                if not isinstance(risk, str) or not risk.strip():
                    violations.append(
                        f"acceptance_criteria[{idx}] "
                        f"({ac.get('id', 'unnamed')!r}) "
                        f"is {status} but remaining_risk is empty"
                    )

            # Evidence constraint: verified must have evidence
            if status == "verified":
                impl_evidence = ac.get("implementation_evidence", [])
                val_evidence = ac.get("validation_evidence", [])
                if (
                    not isinstance(impl_evidence, list) or len(impl_evidence) == 0
                ) and (not isinstance(val_evidence, list) or len(val_evidence) == 0):
                    violations.append(
                        f"acceptance_criteria[{idx}] "
                        f"({ac.get('id', 'unnamed')!r}) "
                        f"is verified but has no evidence entries"
                    )

        # Cross-constraint: overall verified requires all areas and ACs verified
        if overall == "verified":
            if not all_area_verified:
                violations.append(
                    "overall is 'verified' but not all areas are verified"
                )
            if not all_ac_verified:
                violations.append(
                    "overall is 'verified' but not all acceptance criteria are verified"
                )

    return violations


def compute_diff_sha256(repo_root: Path, base_sha: str) -> str | None:
    """Compute the SHA-256 of the effective diff between base_sha and HEAD."""
    import subprocess

    result = subprocess.run(
        ["git", "diff", f"{base_sha}...HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=False,
    )
    if result.returncode != 0:
        return None
    return sha256_bytes(result.stdout)


def compute_changed_files(repo_root: Path, base_sha: str) -> list[str]:
    """Return the list of files changed between base_sha and HEAD."""
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_sha}...HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def generate_artifact_path(output_root: Path, task: int, head_sha: str) -> Path:
    """Generate a stable artifact path for the given task and head."""
    short_head = head_sha[:12]
    return output_root / f"task-{task}-head-{short_head}.json"


def write_artifact(
    repo_root: Path,
    artifact: Mapping[str, Any],
    *,
    task: int,
    head_sha: str,
) -> Path:
    """Write a self-review artifact atomically.

    Returns the path to the written artifact (relative to repo root where possible).
    """
    output_root = _ensure_self_review_root(repo_root)
    path = generate_artifact_path(output_root, task, head_sha)
    atomic_write_json(path, dict(artifact))
    return path


def read_artifact(path: Path) -> dict[str, Any]:
    """Read a self-review artifact from disk."""
    import json

    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise SelfReviewError(f"artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SelfReviewError(f"invalid JSON in artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SelfReviewError(f"artifact {path} is not a JSON object")
    return value


def is_artifact_fresh(
    artifact: Mapping[str, Any],
    *,
    current_head_sha: str,
    current_diff_sha256: str | None,
) -> tuple[bool, str]:
    """Check whether an artifact is still valid for the current head.

    Returns (fresh: bool, reason: str).
    """
    artifact_head = artifact.get("head_sha")
    if not isinstance(artifact_head, str) or artifact_head != current_head_sha:
        return False, (
            f"head SHA changed: artifact={str(artifact_head)[:12]}, "
            f"current={current_head_sha[:12]}"
        )

    if current_diff_sha256 is not None:
        artifact_diff = artifact.get("effective_diff_sha256")
        if artifact_diff != current_diff_sha256:
            return False, (
                f"effective diff changed: "
                f"artifact={str(artifact_diff)[:16] if isinstance(artifact_diff, str) else 'none'}, "
                f"current={current_diff_sha256[:16]}"
            )

    return True, "artifact is fresh"


def validate_file_coverage(
    artifact: Mapping[str, Any], changed_files: Sequence[str]
) -> list[str]:
    """Check that every changed file is covered by at least one area.

    Returns a list of uncovered file paths (empty = full coverage).
    """
    areas = artifact.get("areas", [])
    if not isinstance(areas, list):
        return list(changed_files)

    covered: set[str] = set()
    for area in areas:
        if not isinstance(area, dict):
            continue
        area_files = area.get("files", [])
        if isinstance(area_files, list):
            for f in area_files:
                if isinstance(f, str):
                    covered.add(f)

    uncovered = [f for f in changed_files if f not in covered]
    return uncovered
