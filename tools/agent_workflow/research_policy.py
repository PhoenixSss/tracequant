"""Repository-backed Research artifact and decision policy.

Research is deliberately a small, typed policy surface.  The lifecycle
controllers remain shared with Task and Documentation; this module only
answers the Research-specific questions: which files are artifacts, which
decision values are accepted, and how an artifact is bound to a reviewed
identity.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from workflow_common import sha256_json

RESEARCH_POLICY_ID: Final = "repository-research-artifact-v1"
RESEARCH_ARTIFACT_PREFIX: Final = "docs/research/"
RESEARCH_OUTCOME_FIELD: Final = "Research Outcome"


class ResearchOutcome(StrEnum):
    """The only decisions that a Research workflow may persist."""

    IMPLEMENT = "IMPLEMENT"
    DO_NOT_IMPLEMENT = "DO NOT IMPLEMENT"
    NEEDS_MORE_EVIDENCE = "NEEDS MORE EVIDENCE"
    ARCHITECTURE_DECISION = "ARCHITECTURE DECISION"


RESEARCH_OUTCOMES: Final = tuple(item.value for item in ResearchOutcome)
_IMPLEMENTATION_OUTCOMES: Final = frozenset(
    {ResearchOutcome.IMPLEMENT.value, ResearchOutcome.ARCHITECTURE_DECISION.value}
)
_OUTCOME_RE: Final = re.compile(
    r"^\s*(?:research\s+outcome|outcome)\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)
_OUTCOME_HEADING_RE: Final = re.compile(
    r"^\s{0,3}#{1,6}\s+research\s+outcome\s*#*\s*$", re.IGNORECASE
)
_SAFE_ARTIFACT_SUFFIXES: Final = frozenset(
    {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
)


class ResearchPolicyError(ValueError):
    """A Research artifact or typed decision is not safe to accept."""


class ResearchPolicyStatus(StrEnum):
    PASS = "pass"
    RECLASSIFICATION_REQUIRED = "reclassification_required"
    OUTCOME_REQUIRED = "outcome_required"


@dataclass(frozen=True)
class ResearchChangeResult:
    status: ResearchPolicyStatus
    policy_id: str
    changed_files: tuple[str, ...]
    artifact_files: tuple[str, ...] = ()
    disallowed_files: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status.value,
            "policy_id": self.policy_id,
            "changed_files": list(self.changed_files),
            "artifact_files": list(self.artifact_files),
            "disallowed_files": list(self.disallowed_files),
        }
        if self.detail:
            result["detail"] = self.detail
        return result


def parse_research_outcome(value: object) -> ResearchOutcome:
    """Parse one exact typed outcome and reject unknown values."""

    if not isinstance(value, str):
        raise ResearchPolicyError("Research Outcome must be one of the typed values")
    normalized = " ".join(value.split()).upper()
    try:
        return ResearchOutcome(normalized)
    except ValueError as exc:
        allowed = ", ".join(RESEARCH_OUTCOMES)
        raise ResearchPolicyError(
            f"unknown Research Outcome {value!r}; allowed values: {allowed}"
        ) from exc


def is_implementation_outcome(value: object) -> bool:
    """Return whether a typed Research decision may satisfy implementation entry."""

    try:
        outcome = parse_research_outcome(value)
    except ResearchPolicyError:
        return False
    return outcome.value in _IMPLEMENTATION_OUTCOMES


def _safe_path(path: str) -> bool:
    return bool(
        path
        and not path.startswith("/")
        and "\\" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def _is_allowed_artifact(path: str) -> bool:
    return (
        _safe_path(path)
        and path.startswith(RESEARCH_ARTIFACT_PREFIX)
        and path != RESEARCH_ARTIFACT_PREFIX
        and Path(path).suffix.casefold() in _SAFE_ARTIFACT_SUFFIXES
    )


def evaluate_research_changes(
    changed_files: Iterable[str],
) -> ResearchChangeResult:
    """Allow only versioned, non-executable Research artifacts."""

    normalized = tuple(sorted({path.strip() for path in changed_files if path.strip()}))
    disallowed = tuple(path for path in normalized if not _is_allowed_artifact(path))
    if disallowed:
        return ResearchChangeResult(
            ResearchPolicyStatus.RECLASSIFICATION_REQUIRED,
            RESEARCH_POLICY_ID,
            normalized,
            artifact_files=tuple(path for path in normalized if path not in disallowed),
            disallowed_files=disallowed,
            detail=(
                "Research candidate exceeds the repository-owned artifact policy; "
                "reclassification or split required for: " + ", ".join(disallowed)
            ),
        )
    if not normalized:
        return ResearchChangeResult(
            ResearchPolicyStatus.RECLASSIFICATION_REQUIRED,
            RESEARCH_POLICY_ID,
            normalized,
            detail="Research candidate contains no versioned artifact files",
        )
    return ResearchChangeResult(
        ResearchPolicyStatus.PASS,
        RESEARCH_POLICY_ID,
        normalized,
        artifact_files=normalized,
    )


def _declared_outcomes(text: str, *, path: str) -> list[ResearchOutcome]:
    values: list[ResearchOutcome] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _OUTCOME_RE.match(line)
        if match:
            try:
                values.append(parse_research_outcome(match.group(1)))
            except ResearchPolicyError as exc:
                raise ResearchPolicyError(f"{path}: {exc}") from exc
            continue
        if _OUTCOME_HEADING_RE.match(line):
            following = next(
                (
                    candidate.strip()
                    for candidate in lines[index + 1 :]
                    if candidate.strip()
                ),
                "",
            )
            following = following.strip("`*_ ")
            try:
                values.append(parse_research_outcome(following))
            except ResearchPolicyError as exc:
                raise ResearchPolicyError(
                    f"{path}: Research Outcome heading must be followed by a typed value"
                ) from exc
    return values


def _artifact_digests(
    repo_root: Path, artifact_files: tuple[str, ...]
) -> list[dict[str, str]]:
    digests: list[dict[str, str]] = []
    for relative in artifact_files:
        path = (repo_root / relative).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ResearchPolicyError(
                "Research artifact path escapes repository root"
            ) from exc
        if not path.is_file():
            raise ResearchPolicyError(f"Research artifact is unavailable: {relative}")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ResearchPolicyError(
                f"Research artifact cannot be read: {relative}"
            ) from exc
        digests.append(
            {"path": relative, "sha256": hashlib.sha256(content).hexdigest()}
        )
    return digests


def research_artifact_outcome(
    repo_root: Path, artifact_files: Iterable[str]
) -> ResearchOutcome | None:
    """Read an optional typed outcome declaration from the artifact set."""

    policy = evaluate_research_changes(artifact_files)
    if policy.status is not ResearchPolicyStatus.PASS:
        raise ResearchPolicyError(
            policy.detail or "Research artifact policy rejected the candidate"
        )
    declared: list[ResearchOutcome] = []
    for relative in policy.artifact_files:
        path = (repo_root / relative).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ResearchPolicyError(
                "Research artifact path escapes repository root"
            ) from exc
        if not path.is_file():
            raise ResearchPolicyError(f"Research artifact is unavailable: {relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ResearchPolicyError(
                f"Research artifact cannot be read: {relative}"
            ) from exc
        declared.extend(_declared_outcomes(text, path=relative))
    distinct = {item.value for item in declared}
    if len(distinct) > 1:
        raise ResearchPolicyError(
            "Research artifacts declare conflicting outcomes: "
            + ", ".join(sorted(distinct))
        )
    return next(iter(declared), None)


def research_artifact_binding(
    repo_root: Path,
    *,
    task_number: int,
    pr_number: int | None,
    base_sha: str,
    head_sha: str,
    task_body_sha256: str,
    merge_base_sha: str,
    effective_diff_sha256: str,
    changed_files: Iterable[str],
    outcome: object | None = None,
) -> dict[str, Any]:
    """Bind a Research decision candidate to exact artifact and diff identity."""

    policy = evaluate_research_changes(changed_files)
    if policy.status is not ResearchPolicyStatus.PASS:
        raise ResearchPolicyError(
            policy.detail or "Research artifact policy rejected the candidate"
        )

    artifact_digests = _artifact_digests(repo_root, policy.artifact_files)
    declared: list[ResearchOutcome] = []
    for item in artifact_digests:
        text = (repo_root / item["path"]).read_text(encoding="utf-8")
        declared.extend(_declared_outcomes(text, path=item["path"]))
    distinct = {item.value for item in declared}
    if len(distinct) > 1:
        raise ResearchPolicyError(
            "Research artifacts declare conflicting outcomes: "
            + ", ".join(sorted(distinct))
        )
    declared_outcome = next(iter(declared), None)
    explicit_outcome = parse_research_outcome(outcome) if outcome is not None else None
    if (
        explicit_outcome is not None
        and declared_outcome is not None
        and explicit_outcome is not declared_outcome
    ):
        raise ResearchPolicyError(
            "Research Outcome does not match the reviewed artifact declaration"
        )
    selected = explicit_outcome or declared_outcome
    return {
        "policy_id": RESEARCH_POLICY_ID,
        "outcome": selected.value if selected is not None else None,
        "outcome_status": "typed" if selected is not None else "pending",
        "task_number": task_number,
        "pr_number": pr_number,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "task_body_sha256": task_body_sha256,
        "merge_base_sha": merge_base_sha,
        "effective_diff_sha256": effective_diff_sha256,
        "artifact_files": list(policy.artifact_files),
        "artifact_digests": artifact_digests,
        "artifact_sha256": sha256_json(artifact_digests),
    }


def bind_research_outcome(
    binding: Mapping[str, Any], outcome: object
) -> dict[str, Any]:
    """Return an existing artifact binding with one validated typed outcome."""

    selected = parse_research_outcome(outcome)
    current = binding.get("outcome")
    if current is not None and parse_research_outcome(current) is not selected:
        raise ResearchPolicyError(
            "Research Outcome does not match the existing artifact binding"
        )
    result = dict(binding)
    result["outcome"] = selected.value
    result["outcome_status"] = "typed"
    return result


def require_typed_research_outcome(binding: Mapping[str, Any]) -> ResearchOutcome:
    """Fail closed at the decision boundary when no typed outcome is bound."""

    return parse_research_outcome(binding.get("outcome"))
