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

import yaml
from workflow_common import sha256_json

RESEARCH_POLICY_ID: Final = "repository-research-artifact-v1"
RESEARCH_ARTIFACT_PREFIX: Final = "docs/research/"
RESEARCH_OUTCOME_FIELD: Final = "Research Outcome"
RESEARCH_TEMPLATE_PATH: Final = Path(".github/ISSUE_TEMPLATE/research.yml")


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
class ResearchTemplateContract:
    """The required sections declared by the repository Research Issue Form."""

    field_ids: tuple[str, ...]
    section_labels: tuple[str, ...]


class ResearchTemplateError(ValueError):
    """The repository-owned Research Issue form is not usable."""


@dataclass(frozen=True)
class ResearchContract:
    status: ResearchPolicyStatus
    required_sections: tuple[str, ...] = ()
    missing_sections: tuple[str, ...] = ()
    duplicate_sections: tuple[str, ...] = ()
    empty_sections: tuple[str, ...] = ()
    template_path: str = RESEARCH_TEMPLATE_PATH.as_posix()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "required_sections": list(self.required_sections),
            "missing_sections": list(self.missing_sections),
            "duplicate_sections": list(self.duplicate_sections),
            "empty_sections": list(self.empty_sections),
            "template_path": self.template_path,
            **({"detail": self.detail} if self.detail else {}),
        }


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


def _default_research_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / RESEARCH_TEMPLATE_PATH


def research_template_contract(
    template_path: Path | None = None,
) -> ResearchTemplateContract:
    """Load required Research fields from the formal Issue form."""

    path = template_path or _default_research_template_path()
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ResearchTemplateError(
            f"cannot read {RESEARCH_TEMPLATE_PATH.as_posix()}: {exc}"
        ) from exc
    if not isinstance(parsed, Mapping) or not isinstance(parsed.get("body"), list):
        raise ResearchTemplateError(
            f"{RESEARCH_TEMPLATE_PATH.as_posix()} must define a body list"
        )

    field_ids: list[str] = []
    section_labels: list[str] = []
    for field in parsed["body"]:
        if not isinstance(field, Mapping):
            raise ResearchTemplateError("Research form body item is invalid")
        validations = field.get("validations")
        if (
            not isinstance(validations, Mapping)
            or validations.get("required") is not True
        ):
            continue
        if field.get("type") != "textarea":
            raise ResearchTemplateError(
                "required Research fields must be textarea sections"
            )
        field_id = field.get("id")
        attributes = field.get("attributes")
        label = attributes.get("label") if isinstance(attributes, Mapping) else None
        if (
            not isinstance(field_id, str)
            or not field_id.strip()
            or not isinstance(label, str)
            or not label.strip()
        ):
            raise ResearchTemplateError(
                "required Research textarea must have an id and label"
            )
        normalized_id = field_id.strip()
        normalized_label = " ".join(label.split())
        if normalized_id in field_ids or normalized_label.casefold() in {
            item.casefold() for item in section_labels
        }:
            raise ResearchTemplateError(
                "required Research fields must have unique ids and labels"
            )
        field_ids.append(normalized_id)
        section_labels.append(normalized_label)

    if not section_labels:
        raise ResearchTemplateError(
            f"{RESEARCH_TEMPLATE_PATH.as_posix()} has no required sections"
        )
    return ResearchTemplateContract(tuple(field_ids), tuple(section_labels))


_SECTION_RE: Final = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)


def _section_details(body: str) -> tuple[tuple[str, str], ...]:
    matches = tuple(_SECTION_RE.finditer(body))
    return tuple(
        (
            " ".join(match.group(1).split()),
            body[
                match.end() : matches[index + 1].start()
                if index + 1 < len(matches)
                else None
            ].strip(),
        )
        for index, match in enumerate(matches)
    )


def research_contract_snapshot(
    body: str | None,
    *,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Return a bounded typed snapshot of the Research Issue Form contract."""

    try:
        template = research_template_contract(template_path)
    except ResearchTemplateError as exc:
        return ResearchContract(
            ResearchPolicyStatus.RECLASSIFICATION_REQUIRED,
            detail=f"Research template contract unavailable: {exc}",
        ).to_dict()

    if not isinstance(body, str) or not body.strip():
        return ResearchContract(
            ResearchPolicyStatus.RECLASSIFICATION_REQUIRED,
            required_sections=template.section_labels,
            detail="Research body is unavailable",
        ).to_dict()

    section_details = _section_details(body)
    section_keys = tuple(section.casefold() for section, _content in section_details)
    required_keys = tuple(section.casefold() for section in template.section_labels)
    missing = tuple(
        required
        for required, key in zip(template.section_labels, required_keys, strict=True)
        if key not in section_keys
    )
    duplicates = tuple(
        required
        for required, key in zip(template.section_labels, required_keys, strict=True)
        if section_keys.count(key) > 1
    )
    empty = tuple(
        required
        for required, key in zip(template.section_labels, required_keys, strict=True)
        if any(
            section.casefold() == key and not content
            for section, content in section_details
        )
    )
    if missing or duplicates or empty:
        parts: list[str] = []
        if missing:
            parts.append("missing sections: " + ", ".join(missing))
        if duplicates:
            parts.append("duplicate sections: " + ", ".join(duplicates))
        if empty:
            parts.append("empty sections: " + ", ".join(empty))
        return ResearchContract(
            ResearchPolicyStatus.RECLASSIFICATION_REQUIRED,
            required_sections=template.section_labels,
            missing_sections=missing,
            duplicate_sections=duplicates,
            empty_sections=empty,
            detail="; ".join(parts),
        ).to_dict()
    return ResearchContract(
        ResearchPolicyStatus.PASS,
        required_sections=template.section_labels,
    ).to_dict()


def is_valid_research_contract(value: object) -> bool:
    """Validate the bounded Research contract consumed by LCK eligibility."""

    if not isinstance(value, Mapping):
        return False
    try:
        template = research_template_contract()
    except ResearchTemplateError:
        return False
    return (
        value.get("status") == ResearchPolicyStatus.PASS.value
        and value.get("required_sections") == list(template.section_labels)
        and value.get("missing_sections") == []
        and value.get("duplicate_sections") == []
        and value.get("empty_sections") == []
        and value.get("template_path") == RESEARCH_TEMPLATE_PATH.as_posix()
    )


_DECISION_CONTRACT_HEADINGS: Final = frozenset(
    {"decision contract", "architecture decision contract", "adr"}
)


def decision_contract_snapshot(
    body: str | None,
    *,
    research: bool = False,
) -> dict[str, Any]:
    """Extract the explicit contract that an Architecture Decision authorizes.

    Research uses its required Expected Outcome / Artifact section as the
    fallback contract. Downstream implementation Issues must opt in with an
    explicit Decision Contract (or ADR) section, so an empty or unrelated Task
    body cannot satisfy an Architecture Decision dependency by accident.
    """

    if not isinstance(body, str) or not body.strip():
        return {"status": "unknown", "detail": "decision contract body unavailable"}
    sections = _section_details(body)
    selected: tuple[str, str] | None = None
    for heading, content in sections:
        if heading.casefold() in _DECISION_CONTRACT_HEADINGS:
            if selected is not None:
                return {
                    "status": "unknown",
                    "detail": "decision contract is duplicated",
                }
            selected = (heading, content)
    if selected is None and research:
        for heading, content in sections:
            if heading.casefold() == "expected outcome / artifact":
                selected = (heading, content)
                break
    if selected is None or not selected[1].strip():
        return {
            "status": "unknown",
            "detail": "decision contract is missing or empty",
        }
    normalized = " ".join(selected[1].split())
    return {
        "status": "pass",
        "heading": selected[0],
        "contract_sha256": sha256_json({"contract": normalized}),
    }


def architecture_decision_is_consistent(
    research_decision: object,
    downstream_contract: object,
) -> bool:
    """Return whether a downstream Issue explicitly matches the decision."""

    if not isinstance(research_decision, Mapping) or not isinstance(
        downstream_contract, Mapping
    ):
        return False
    if research_decision.get("status") != "pass":
        return False
    downstream_decision = downstream_contract.get("decision_contract")
    if not isinstance(downstream_decision, Mapping):
        body = downstream_contract.get("body")
        downstream_decision = decision_contract_snapshot(
            body if isinstance(body, str) else None
        )
    return downstream_decision.get("status") == "pass" and downstream_decision.get(
        "contract_sha256"
    ) == research_decision.get("contract_sha256")


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


def _resolve_research_artifact_path(repo_root: Path, relative: str) -> Path:
    """Resolve an artifact only when its complete path stays in the namespace."""

    root = repo_root.resolve()
    candidate = root / relative
    current = root
    try:
        for part in Path(relative).parts:
            current /= part
            if current.is_symlink():
                raise ResearchPolicyError(
                    f"Research artifacts must not use symlinks: {relative}"
                )
        resolved = candidate.resolve()
        artifact_root = (root / RESEARCH_ARTIFACT_PREFIX.rstrip("/")).resolve()
        resolved.relative_to(artifact_root)
    except ResearchPolicyError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ResearchPolicyError(
            f"Research artifact path cannot be inspected: {relative}"
        ) from exc
    except ValueError as exc:
        raise ResearchPolicyError(
            f"Research artifact path resolves outside docs/research/: {relative}"
        ) from exc
    return resolved


def _artifact_path_policy_error(repo_root: Path, relative: str) -> str | None:
    try:
        _resolve_research_artifact_path(repo_root, relative)
    except ResearchPolicyError as exc:
        return str(exc)
    return None


def evaluate_research_changes(
    changed_files: Iterable[str],
    *,
    repo_root: Path | None = None,
) -> ResearchChangeResult:
    """Allow only versioned, non-executable Research artifacts.

    ``repo_root`` is required for callers that will read the artifacts.  When
    provided, the complete path is checked without following symlinks and its
    resolved target must remain under ``docs/research/``.
    """

    normalized = tuple(sorted({path.strip() for path in changed_files if path.strip()}))
    path_errors = {
        path: _artifact_path_policy_error(repo_root, path)
        for path in normalized
        if repo_root is not None and _is_allowed_artifact(path)
    }
    disallowed = tuple(
        path
        for path in normalized
        if not _is_allowed_artifact(path) or path_errors.get(path) is not None
    )
    if disallowed:
        detail = (
            "Research candidate exceeds the repository-owned artifact policy; "
            "reclassification or split required for: " + ", ".join(disallowed)
        )
        reasons = tuple(path_errors[path] for path in disallowed if path in path_errors)
        if reasons:
            detail += "; " + "; ".join(reasons)
        return ResearchChangeResult(
            ResearchPolicyStatus.RECLASSIFICATION_REQUIRED,
            RESEARCH_POLICY_ID,
            normalized,
            artifact_files=tuple(path for path in normalized if path not in disallowed),
            disallowed_files=disallowed,
            detail=detail,
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
        path = _resolve_research_artifact_path(repo_root, relative)
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

    policy = evaluate_research_changes(artifact_files, repo_root=repo_root)
    if policy.status is not ResearchPolicyStatus.PASS:
        raise ResearchPolicyError(
            policy.detail or "Research artifact policy rejected the candidate"
        )
    declared: list[ResearchOutcome] = []
    for relative in policy.artifact_files:
        path = _resolve_research_artifact_path(repo_root, relative)
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

    policy = evaluate_research_changes(changed_files, repo_root=repo_root)
    if policy.status is not ResearchPolicyStatus.PASS:
        raise ResearchPolicyError(
            policy.detail or "Research artifact policy rejected the candidate"
        )

    artifact_digests = _artifact_digests(repo_root, policy.artifact_files)
    declared: list[ResearchOutcome] = []
    for item in artifact_digests:
        try:
            text = _resolve_research_artifact_path(repo_root, item["path"]).read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeDecodeError) as exc:
            raise ResearchPolicyError(
                f"Research artifact cannot be read: {item['path']}"
            ) from exc
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
