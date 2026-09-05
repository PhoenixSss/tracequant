"""Canonical Structured Review v2 protocol for production Independent Review.

This module owns the production protocol declaration and its bounded semantic
receipt gate.  The shadow benchmark intentionally remains a separate,
non-authoritative consumer of the canonical Review contracts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from tracequant.contracts import (
    ALWAYS_ON_SURFACES,
    AssuranceObligation,
    AssuranceStatus,
    FindingBlockingStatus,
    FindingVerificationStatus,
    ReviewAuthorityIdentity,
    ReviewContractError,
    ReviewRunReceipt,
    ReviewSurface,
)

STRUCTURED_REVIEW_PROTOCOL_ID: Final[str] = "tracequant-independent-review-structured"
STRUCTURED_REVIEW_PROTOCOL_VERSION: Final[str] = "v2"


STRUCTURED_REVIEW_OBLIGATIONS: Final[tuple[AssuranceObligation, ...]] = (
    AssuranceObligation(
        obligation_id="contract-critical-outcome",
        description=(
            "Map Objective, Requirements, Acceptance Criteria, Critical Outcome, "
            "explicit constraints, and non-goals to implementation and evidence."
        ),
        required_surfaces=(ReviewSurface.CONTRACT_CONFORMANCE,),
    ),
    AssuranceObligation(
        obligation_id="functional-invariants",
        description=(
            "Identify core invariants, check caller/callee assumptions, and "
            "actively construct applicable counterexamples."
        ),
        required_surfaces=(ReviewSurface.FUNCTIONAL_CORRECTNESS,),
    ),
    AssuranceObligation(
        obligation_id="boundary-error-enumeration",
        description=(
            "Enumerate applicable parsing, conversion, external-input, and "
            "transport outcomes, including missing, empty, malformed, extreme, "
            "negative, conversion failure, runtime exception, and result mapping."
        ),
        required_surfaces=(ReviewSurface.ERROR_FAILURE_PATHS,),
    ),
    AssuranceObligation(
        obligation_id="state-persistence-compatibility",
        description=(
            "For applicable stateful or persisted behavior, enumerate ABSENT, "
            "PARTIAL, COMPLETE_VALID, COMPLETE_INVALID, LEGACY, CONFLICTING, "
            "and implemented recovery/quarantine states across read, write, "
            "publish, reconcile, retry, overwrite/conflict, recovery, and "
            "upstream access; check Base to Head compatibility."
        ),
        required_surfaces=(
            ReviewSurface.STATE_TRANSITIONS,
            ReviewSurface.PERSISTENCE_ATOMICITY,
            ReviewSurface.COMPATIBILITY_MIGRATION,
        ),
    ),
    AssuranceObligation(
        obligation_id="tests-vs-claims",
        description=(
            "Distinguish what tests actually prove from happy-path-only evidence "
            "and failure, boundary, state, compatibility, and invariant gaps."
        ),
        required_surfaces=(ReviewSurface.TESTS_VS_CLAIMS,),
    ),
    AssuranceObligation(
        obligation_id="adversarial-residual-sweep",
        description=(
            "Assume current findings are fixed and search for independent root "
            "causes; finish this sweep before producing the final verdict."
        ),
        required_surfaces=(ReviewSurface.FUNCTIONAL_CORRECTNESS,),
    ),
)

_OBLIGATION_IDS: Final[tuple[str, ...]] = tuple(
    item.obligation_id for item in STRUCTURED_REVIEW_OBLIGATIONS
)


class StructuredReviewProtocolError(ReviewContractError):
    """Raised when a production Structured Review receipt is not admissible."""


@dataclass(frozen=True, slots=True)
class StructuredReviewAssessment:
    """Bounded result used by LCK to derive the production semantic verdict."""

    status: str
    canonical_verdict: str | None
    obligation_statuses: Mapping[str, str]
    blocking_finding_ids: tuple[str, ...]
    issues: tuple[str, ...]
    receipt_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": STRUCTURED_REVIEW_PROTOCOL_ID,
            "protocol_version": STRUCTURED_REVIEW_PROTOCOL_VERSION,
            "status": self.status,
            "canonical_verdict": self.canonical_verdict,
            "obligation_statuses": dict(self.obligation_statuses),
            "blocking_finding_ids": list(self.blocking_finding_ids),
            "issues": list(self.issues),
            "receipt_sha256": self.receipt_sha256,
            "authority": "canonical Structured Review v2 protocol",
        }


def protocol_context(
    *, authority: ReviewAuthorityIdentity | None = None
) -> dict[str, Any]:
    """Return the standard protocol supplied to every production Reviewer."""
    return {
        "protocol_id": STRUCTURED_REVIEW_PROTOCOL_ID,
        "protocol_version": STRUCTURED_REVIEW_PROTOCOL_VERSION,
        "obligations": [item.to_dict() for item in STRUCTURED_REVIEW_OBLIGATIONS],
        "required_always_on_surfaces": [item.value for item in ALWAYS_ON_SURFACES],
        "completion_contract": {
            "required_statuses": [AssuranceStatus.PASS.value],
            "allowed_not_applicable_status": AssuranceStatus.NOT_APPLICABLE.value,
            "incomplete_status": "REVIEW_INCOMPLETE",
            "residual_sweep_before_verdict": True,
            "first_finding_does_not_end_review": True,
            "canonical_verdict_authority": "LCK Review Complete",
        },
        "coverage_matrix_fields": (
            "requirement",
            "implementation",
            "evidence",
            "status",
        ),
        "receipt": {
            "kind": ReviewRunReceipt.KIND,
            "schema_version": ReviewRunReceipt.SCHEMA_VERSION,
            "protocol_config": {
                "protocol_id": STRUCTURED_REVIEW_PROTOCOL_ID,
                "protocol_version": STRUCTURED_REVIEW_PROTOCOL_VERSION,
                "coverage_matrix": "one entry per obligation",
                "falsification_attempts": "all confirmed findings",
            },
        },
        "expected_authority": authority.to_dict() if authority is not None else None,
    }


def _require_protocol_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StructuredReviewProtocolError("protocol_config must be a JSON object")
    return value


def _validate_coverage_matrix(
    protocol_config: Mapping[str, Any],
    results_by_id: Mapping[str, Any],
) -> list[str]:
    issues: list[str] = []
    matrix = protocol_config.get("coverage_matrix")
    if not isinstance(matrix, Mapping):
        return ["coverage matrix is missing"]
    if set(matrix) != set(_OBLIGATION_IDS):
        return ["coverage matrix must contain exactly one entry per obligation"]
    for obligation_id in _OBLIGATION_IDS:
        entry = matrix.get(obligation_id)
        if not isinstance(entry, Mapping):
            issues.append(f"coverage matrix entry {obligation_id} is invalid")
            continue
        for field in ("requirement", "implementation", "evidence", "status"):
            value = entry.get(field)
            if field == "evidence":
                if isinstance(value, str):
                    valid = bool(value.strip())
                else:
                    valid = isinstance(value, Sequence) and bool(value)
            else:
                valid = isinstance(value, str) and bool(value.strip())
            if not valid:
                issues.append(f"coverage matrix entry {obligation_id} has no {field}")
        result = results_by_id.get(obligation_id)
        if result is not None and entry.get("status") != result.status.value:
            issues.append(
                f"coverage matrix status does not match {obligation_id} result"
            )
    return issues


def _falsification_issues(
    protocol_config: Mapping[str, Any],
    verified_findings: Sequence[Any],
) -> list[str]:
    blocking_ids = {
        item.finding_id
        for item in verified_findings
        if item.verification_status is FindingVerificationStatus.CONFIRMED
        and item.blocking_status is FindingBlockingStatus.BLOCKING
    }
    if not blocking_ids:
        return []
    attempts = protocol_config.get("falsification_attempts")
    if attempts is True:
        return []
    if isinstance(attempts, str):
        attempted_ids = {attempts}
    elif isinstance(attempts, Sequence):
        attempted_ids = {item for item in attempts if isinstance(item, str)}
    else:
        attempted_ids = set()
    missing = sorted(blocking_ids - attempted_ids)
    return (
        [
            "blocking findings are missing a recorded falsification attempt: "
            + ", ".join(missing)
        ]
        if missing
        else []
    )


def assess_receipt(
    receipt: ReviewRunReceipt,
    *,
    expected_authority: ReviewAuthorityIdentity,
    receipt_sha256: str = "",
) -> StructuredReviewAssessment:
    """Validate one receipt and derive PASS/FAIL only after all obligations finish."""
    if not isinstance(receipt, ReviewRunReceipt):
        raise TypeError("receipt must be a ReviewRunReceipt")
    if receipt.authority != expected_authority:
        raise StructuredReviewProtocolError(
            "Structured Review receipt authority does not match the reviewed target"
        )

    issues: list[str] = []
    protocol_config = _require_protocol_mapping(receipt.protocol_config)
    if protocol_config.get("protocol_id") != STRUCTURED_REVIEW_PROTOCOL_ID:
        issues.append("receipt does not identify Structured Review v2")
    if protocol_config.get("protocol_version") != STRUCTURED_REVIEW_PROTOCOL_VERSION:
        issues.append("receipt has an unsupported Structured Review protocol version")

    declared = tuple(item.obligation_id for item in receipt.assurance_obligations)
    if declared != _OBLIGATION_IDS:
        issues.append("receipt obligations do not match the canonical v2 protocol")
    else:
        for expected, actual in zip(
            STRUCTURED_REVIEW_OBLIGATIONS, receipt.assurance_obligations, strict=True
        ):
            if actual.description != expected.description or set(
                actual.required_surfaces
            ) != set(expected.required_surfaces):
                issues.append(
                    f"obligation definition changed: {expected.obligation_id}"
                )

    results_by_id = {item.obligation_id: item for item in receipt.assurance_results}
    issues.extend(_validate_coverage_matrix(protocol_config, results_by_id))
    if not receipt.coverage_complete:
        issues.append("required Review surface coverage is incomplete")
    if not receipt.review_complete:
        issues.append("required Review obligations are incomplete or unresolved")

    candidate_ids = {item.finding_id for item in receipt.candidate_findings}
    verified_ids = {item.finding_id for item in receipt.verified_findings}
    if candidate_ids != verified_ids:
        issues.append(
            "every candidate finding must have a completed canonical verification"
        )
    issues.extend(_falsification_issues(protocol_config, receipt.verified_findings))

    blockers = tuple(
        item.finding_id
        for item in receipt.verified_findings
        if item.verification_status is FindingVerificationStatus.CONFIRMED
        and item.blocking_status is FindingBlockingStatus.BLOCKING
    )
    status = "complete" if not issues else "incomplete"
    canonical_verdict = (
        ("FAIL" if blockers else "PASS") if status == "complete" else None
    )
    return StructuredReviewAssessment(
        status=status,
        canonical_verdict=canonical_verdict,
        obligation_statuses={
            obligation_id: (
                results_by_id[obligation_id].status.value
                if obligation_id in results_by_id
                else "unresolved"
            )
            for obligation_id in _OBLIGATION_IDS
        },
        blocking_finding_ids=blockers,
        issues=tuple(dict.fromkeys(issues)),
        receipt_sha256=receipt_sha256,
    )


def read_and_assess_receipt(
    path: Path,
    *,
    expected_authority: ReviewAuthorityIdentity,
) -> tuple[ReviewRunReceipt, StructuredReviewAssessment]:
    """Read and assess a bounded JSON receipt supplied by the semantic Reviewer."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise StructuredReviewProtocolError(
            f"cannot read Structured Review receipt: {exc}"
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        value = json.loads(raw.decode("utf-8"))
        receipt = ReviewRunReceipt.from_dict(value)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise StructuredReviewProtocolError(
            f"Structured Review receipt is invalid: {exc}"
        ) from exc
    return receipt, assess_receipt(
        receipt,
        expected_authority=expected_authority,
        receipt_sha256=digest,
    )


def expected_live_authority(
    *,
    repository: str,
    task_number: int,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    diff_sha256: str,
) -> ReviewAuthorityIdentity:
    """Build the contract authority from LCK's already-resolved live identity."""
    from tracequant.contracts import ReviewAuthorityKind

    return ReviewAuthorityIdentity(
        authority_kind=ReviewAuthorityKind.LIVE,
        repository=repository,
        task_number=task_number,
        pull_request_number=pr_number,
        base_sha=base_sha,
        head_sha=head_sha,
        diff_sha256=diff_sha256,
    )


__all__ = [
    "STRUCTURED_REVIEW_OBLIGATIONS",
    "STRUCTURED_REVIEW_PROTOCOL_ID",
    "STRUCTURED_REVIEW_PROTOCOL_VERSION",
    "StructuredReviewAssessment",
    "StructuredReviewProtocolError",
    "assess_receipt",
    "expected_live_authority",
    "protocol_context",
    "read_and_assess_receipt",
]
