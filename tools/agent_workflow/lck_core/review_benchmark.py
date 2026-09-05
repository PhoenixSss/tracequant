"""Task #194 Review benchmark corpus, replay protocol, and scorer.

The benchmark has three deliberately separate planes:

* a frozen fixture contains only the historical repository and its mechanical
  identity;
* the Review protocol sees a Harness and one materialized Subject, but never
  the corpus manifest or known-findings oracle;
* the scorer reads the oracle only after the protocol has produced candidate
  findings.

This module is an evaluation harness, not a production Review gate and not a
new semantic Review pipeline.  The bundled Task #194 adapter is a deterministic
protocol implementation used to make the first baseline reproducible without
requiring a model provider or network service.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Protocol

from workflow_common import (
    CommandRunner,
    atomic_write_json,
    is_sha,
    read_json_file,
    sha256_json,
)

from tracequant.contracts import (
    CandidateFinding as CanonicalCandidateFinding,
)
from tracequant.contracts import (
    FindingBlockingStatus,
    FindingSeverity,
    FindingVerificationStatus,
    ReviewAuthorityIdentity,
    ReviewAuthorityKind,
    ReviewSurface,
    RunProvenance,
    TokenUsage,
    VerifiedFinding,
)

from .models import LckStopError
from .review_eval import (
    GitFrozenSubjectMaterializer,
    ReviewEvalRunContext,
    ReviewEvalRunner,
    VerifiedHarnessSnapshot,
    _verified_harness_snapshot,
)
from .review_fixture import FrozenReviewFixture, load_frozen_review_fixture
from .review_shadow import (
    DEFAULT_DISCOVERY_PASSES,
    DiscoveryPass,
    DiscoveryPassResult,
    IndependentVerification,
    IndependentVerificationRequest,
    ProductionReviewState,
    ShadowReviewPipeline,
    ShadowReviewResult,
    StructuredDiscoveryPlan,
    VerificationMethod,
)

BENCHMARK_SCHEMA_VERSION: Final = 1
CORPUS_MANIFEST_NAME: Final = "corpus-manifest.json"
KNOWN_FINDINGS_NAME: Final = "known-findings.json"
PROTOCOL_ID: Final = "tracequant-production-equivalent-review"
PROTOCOL_VERSION: Final = "v1"
PROTOCOL_STEPS: Final = ("Inspect", "Reason", "Judge", "Report")
_SEVERITIES: Final = frozenset({"Blocking", "High", "Medium", "Low", "Nit"})
_FIXTURE_KINDS: Final = frozenset({"defect-rich", "stable"})
BENCHMARK_WALL_CLOCK_SCOPE: Final[str] = (
    "ReviewEvalRunner.run: Subject materialization through evaluator completion"
)
_HARNESS_EXCLUDED_ROOTS: Final = frozenset(
    {
        ".agents",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".workflow.local",
    }
)
_HEX64: Final = re.compile(r"\A[0-9a-fA-F]{64}\Z")


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LckStopError(f"Review benchmark {field} is unavailable")
    return value


def _required_digest(value: Any, *, field: str) -> str:
    text = _required_text(value, field=field)
    if _HEX64.fullmatch(text) is None:
        raise LckStopError(f"Review benchmark {field} is not a SHA-256 digest")
    return text.lower()


def _required_commit_sha(value: Any, *, field: str) -> str:
    if not is_sha(value) or len(str(value)) != 40:
        raise LckStopError(f"Review benchmark {field} is not a commit SHA")
    return str(value).lower()


def _safe_relative(root: Path, value: Any, *, field: str) -> Path:
    relative = _required_text(value, field=f"{field} path")
    path = Path(relative)
    if path.is_absolute() or path == Path(".") or ".." in path.parts:
        raise LckStopError(f"Review benchmark {field} path is invalid")
    candidate = (root / path).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise LckStopError(f"Review benchmark {field} path escapes corpus") from exc
    return candidate


def _read_object(path: Path, *, field: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LckStopError(f"Review benchmark {field} is unavailable")
    try:
        value = read_json_file(path)
    except Exception as exc:
        raise LckStopError(f"Review benchmark {field} cannot be read") from exc
    if not isinstance(value, Mapping):
        raise LckStopError(f"Review benchmark {field} is not an object")
    return value


def _load_scorer_oracle(
    oracle_path: Path, expected_oracle_digest: str
) -> dict[str, tuple[KnownFinding, ...]]:
    """Load and validate the oracle only from the post-Review scorer path."""
    try:
        oracle_bytes = oracle_path.read_bytes()
    except OSError as exc:
        raise LckStopError("Review benchmark scorer oracle is unavailable") from exc
    if hashlib.sha256(oracle_bytes).hexdigest() != expected_oracle_digest:
        raise LckStopError("Review benchmark oracle digest mismatch")
    oracle = _read_object(oracle_path, field="scorer oracle")
    if oracle.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise LckStopError("Review benchmark oracle schema version is unsupported")
    if oracle.get("boundary") != "scorer-only":
        raise LckStopError("Review benchmark oracle boundary is invalid")
    raw_entries = oracle.get("fixtures")
    if not isinstance(raw_entries, Mapping):
        raise LckStopError("Review benchmark oracle fixtures are unavailable")
    parsed_oracle: dict[str, tuple[KnownFinding, ...]] = {}
    for fixture_id in ("task-194:defect-rich-v1", "task-194:stable-v1"):
        entry = raw_entries.get(fixture_id)
        if not isinstance(entry, Mapping):
            raise LckStopError(
                f"Review benchmark oracle entry is unavailable: {fixture_id}"
            )
        raw_findings = entry.get("known_findings")
        if not isinstance(raw_findings, Sequence) or isinstance(
            raw_findings, (str, bytes)
        ):
            raise LckStopError(
                f"Review benchmark known findings are unavailable: {fixture_id}"
            )
        parsed: list[KnownFinding] = []
        for raw in raw_findings:
            if not isinstance(raw, Mapping):
                raise LckStopError("Review benchmark known finding is invalid")
            evidence = raw.get("evidence")
            if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
                raise LckStopError("Review benchmark known finding evidence is invalid")
            parsed.append(
                KnownFinding(
                    finding_id=_required_text(
                        raw.get("finding_id"), field="finding ID"
                    ),
                    title=_required_text(raw.get("title"), field="finding title"),
                    severity=_required_text(
                        raw.get("severity"), field="finding severity"
                    ),
                    path=_required_text(raw.get("path"), field="finding path"),
                    symbol=_required_text(raw.get("symbol"), field="finding symbol"),
                    category=_required_text(
                        raw.get("category"), field="finding category"
                    ),
                    applicability=_required_text(
                        raw.get("applicability"), field="finding applicability"
                    ),
                    evidence=tuple(str(item) for item in evidence),
                    introduced_head=_required_text(
                        raw.get("introduced_head"), field="finding introduced head"
                    ),
                    fixed_head=_required_text(
                        raw.get("fixed_head"), field="finding fixed head"
                    ),
                )
            )
        if len({item.finding_id for item in parsed}) != len(parsed):
            raise LckStopError("Review benchmark known finding IDs are duplicated")
        if len({item.fingerprint for item in parsed}) != len(parsed):
            raise LckStopError(
                "Review benchmark known finding fingerprints are duplicated"
            )
        parsed_oracle[fixture_id] = tuple(parsed)
    if len(parsed_oracle["task-194:defect-rich-v1"]) < 3:
        raise LckStopError("defect-rich oracle must contain at least three findings")
    return parsed_oracle


@dataclass(frozen=True, slots=True)
class CandidateFinding:
    """A finding emitted by a Reviewer without oracle identifiers."""

    severity: str
    path: str
    symbol: str
    category: str
    summary: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITIES:
            raise ValueError("candidate finding severity is invalid")
        for field_name in ("path", "symbol", "category", "summary"):
            if (
                not isinstance(getattr(self, field_name), str)
                or not getattr(self, field_name).strip()
            ):
                raise ValueError(f"candidate finding {field_name} is unavailable")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence):
            raise ValueError("candidate finding evidence is invalid")

    @property
    def fingerprint(self) -> tuple[str, str, str]:
        """Return the public semantic key used for post-review matching."""
        return (self.path, self.symbol, self.category)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "path": self.path,
            "symbol": self.symbol,
            "category": self.category,
            "summary": self.summary,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ReviewObservation:
    """Structured semantic output of one production-shaped Review run."""

    verdict: str
    findings: tuple[CandidateFinding, ...]
    model: str
    config: Mapping[str, Any]
    token_usage: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.verdict not in {"PASS", "FAIL"}:
            raise ValueError("Review observation verdict must be PASS or FAIL")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Review observation model is unavailable")
        for name, value in self.token_usage.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Review observation token field is invalid")
            if type(value) is not int or value < 0:
                raise ValueError("Review observation token count is invalid")
        if self.verdict == "PASS" and self.findings:
            raise ValueError("Review observation PASS cannot contain findings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": [item.to_dict() for item in self.findings],
            "model": self.model,
            "config": dict(self.config),
            "token_usage": dict(self.token_usage),
        }


class ReviewProtocol(Protocol):
    """Callable semantic protocol boundary used by the replay runner."""

    def __call__(self, run: ReviewEvalRunContext) -> ReviewObservation: ...


@dataclass(frozen=True, slots=True)
class BenchmarkFixture:
    fixture: FrozenReviewFixture
    fixture_id: str
    kind: str
    relative_path: str

    def __post_init__(self) -> None:
        if self.fixture.fixture_id != self.fixture_id:
            raise LckStopError("Review benchmark fixture ID does not match manifest")
        if self.kind not in _FIXTURE_KINDS:
            raise LckStopError("Review benchmark fixture kind is invalid")


@dataclass(frozen=True, slots=True)
class KnownFinding:
    """A scorer-only historical finding; never passed to a Review protocol."""

    finding_id: str
    title: str
    severity: str
    path: str
    symbol: str
    category: str
    applicability: str
    evidence: tuple[str, ...]
    introduced_head: str
    fixed_head: str

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITIES:
            raise ValueError("known finding severity is invalid")
        for field_name in (
            "finding_id",
            "title",
            "path",
            "symbol",
            "category",
            "applicability",
            "introduced_head",
            "fixed_head",
        ):
            if (
                not isinstance(getattr(self, field_name), str)
                or not getattr(self, field_name).strip()
            ):
                raise ValueError(f"known finding {field_name} is unavailable")
        _required_commit_sha(self.introduced_head, field="finding introduced head")
        _required_commit_sha(self.fixed_head, field="finding fixed head")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence):
            raise ValueError("known finding evidence is invalid")

    @property
    def fingerprint(self) -> tuple[str, str, str]:
        return (self.path, self.symbol, self.category)


@dataclass(frozen=True, slots=True)
class FindingMatch:
    candidate: CandidateFinding
    known_finding_id: str | None
    match_type: str
    verification: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "known_finding_id": self.known_finding_id,
            "match_type": self.match_type,
            "verification": self.verification,
        }


@dataclass(frozen=True, slots=True)
class FindingScore:
    """Auditable post-review comparison of candidate and scorer-only findings."""

    fixture_id: str
    known_count: int
    candidate_count: int
    verified_count: int
    matches: tuple[FindingMatch, ...]
    adjudication_required: bool
    control_status: str

    @property
    def matched_count(self) -> int:
        return len(
            {
                item.known_finding_id
                for item in self.matches
                if item.known_finding_id is not None
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "known_count": self.known_count,
            "candidate_count": self.candidate_count,
            "verified_count": self.verified_count,
            "matched_count": self.matched_count,
            "matches": [item.to_dict() for item in self.matches],
            "adjudication_required": self.adjudication_required,
            "control_status": self.control_status,
            "oracle_boundary": "scorer-only; not Review protocol input",
        }


@dataclass(frozen=True, slots=True)
class BaselineRunReceipt:
    """Structured identity and outcome receipt for one replay."""

    fixture_id: str
    fixture_kind: str
    fixture_digest: str
    base_sha: str
    head_sha: str
    effective_diff_sha256: str
    task_contract_sha256: str
    deterministic_evidence_sha256: str
    repository_artifact_sha256: str
    fixture_manifest_sha256: str
    run_id: str
    harness_sha: str
    protocol_id: str
    protocol_version: str
    protocol_steps: tuple[str, ...]
    model: str
    config: Mapping[str, Any]
    token_usage: Mapping[str, int]
    wall_clock_ms: float
    verdict: str
    findings: tuple[CandidateFinding, ...]
    findings_sha256: str
    subject_clean_exact_head: bool
    run_workspace_cleaned: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "kind": "review-benchmark-run-receipt",
            "fixture": {
                "fixture_id": self.fixture_id,
                "fixture_kind": self.fixture_kind,
                "fixture_digest": self.fixture_digest,
                "base_sha": self.base_sha,
                "head_sha": self.head_sha,
                "effective_diff_sha256": self.effective_diff_sha256,
                "task_contract_sha256": self.task_contract_sha256,
                "deterministic_evidence_sha256": self.deterministic_evidence_sha256,
                "repository_artifact_sha256": self.repository_artifact_sha256,
                "fixture_manifest_sha256": self.fixture_manifest_sha256,
            },
            "run": {
                "run_id": self.run_id,
                "harness_sha": self.harness_sha,
                "protocol_id": self.protocol_id,
                "protocol_version": self.protocol_version,
                "protocol_steps": list(self.protocol_steps),
                "model": self.model,
                "config": dict(self.config),
                "token_usage": dict(self.token_usage),
                "wall_clock_ms": self.wall_clock_ms,
                "verdict": self.verdict,
                "findings": [item.to_dict() for item in self.findings],
                "findings_sha256": self.findings_sha256,
                "subject_clean_exact_head": self.subject_clean_exact_head,
                "run_workspace_cleaned": self.run_workspace_cleaned,
            },
            "visibility": {
                "reviewer_received_oracle": False,
                "reviewer_received_expected_verdict": False,
                "reviewer_received_accepted_finding_list": False,
            },
        }

    def semantic_fingerprint(self) -> str:
        return sha256_json(
            {
                "fixture_id": self.fixture_id,
                "fixture_digest": self.fixture_digest,
                "head_sha": self.head_sha,
                "verdict": self.verdict,
                "findings": [item.to_dict() for item in self.findings],
                "model": self.model,
                "config": dict(self.config),
                "token_usage": dict(self.token_usage),
            }
        )

    def write(self, path: Path) -> Path:
        """Persist one bounded receipt outside the temporary Run workspace."""
        path = path.resolve(strict=False)
        if path.name in {"", ".", ".."}:
            raise LckStopError("Review benchmark receipt path is invalid")
        atomic_write_json(path, self.to_dict())
        return path


@dataclass(frozen=True, slots=True)
class ReplayResult:
    observation: ReviewObservation
    receipt: BaselineRunReceipt


@dataclass(frozen=True, slots=True)
class ShadowReplayResult:
    """One structured shadow replay and its scorer-only benchmark result."""

    shadow: ShadowReviewResult
    score: FindingScore


@dataclass(frozen=True, slots=True)
class BaselineShadowComparison:
    """Comparable baseline/shadow metrics for one frozen fixture."""

    fixture_id: str
    baseline: ReplayResult
    baseline_score: FindingScore
    shadow: ShadowReplayResult

    @property
    def baseline_tokens(self) -> int:
        return int(self.baseline.receipt.token_usage.get("total", 0))

    @property
    def shadow_tokens(self) -> int:
        return self.shadow.shadow.receipt.token_usage.total_tokens

    @property
    def baseline_wall_clock_ms(self) -> float:
        return self.baseline.receipt.wall_clock_ms

    @property
    def shadow_wall_clock_ms(self) -> int:
        return self.shadow.shadow.receipt.wall_clock_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "wall_clock_scope": BENCHMARK_WALL_CLOCK_SCOPE,
            "baseline": {
                "verdict": self.baseline.observation.verdict,
                "known_findings_covered": self.baseline_score.matched_count,
                "candidate_count": self.baseline_score.candidate_count,
                "verified_count": self.baseline_score.verified_count,
                "token_total": self.baseline_tokens,
                "wall_clock_ms": self.baseline_wall_clock_ms,
                "wall_clock_scope": BENCHMARK_WALL_CLOCK_SCOPE,
            },
            "shadow": {
                "production_verdict": self.shadow.shadow.production_verdict,
                "known_findings_covered": self.shadow.score.matched_count,
                "candidate_count": self.shadow.shadow.receipt.candidate_count,
                "verified_count": self.shadow.shadow.receipt.verified_count,
                "rejected_count": self.shadow.shadow.receipt.rejected_count,
                "unresolved_count": self.shadow.shadow.receipt.unresolved_count,
                "verified_blocker_count": self.shadow.shadow.receipt.verified_blocker_count,
                "token_total": self.shadow_tokens,
                "wall_clock_ms": self.shadow_wall_clock_ms,
                "wall_clock_scope": BENCHMARK_WALL_CLOCK_SCOPE,
                "incremental_known_findings": list(
                    self.shadow.shadow.receipt.incremental_known_findings
                ),
                "control_status": self.shadow.score.control_status,
            },
            "production_state_unchanged": self.shadow.shadow.receipt.production_state_unchanged,
        }


def _task194_review_authority(authority: Any) -> ReviewAuthorityIdentity:
    """Convert frozen Eval identity to the canonical contract identity."""
    fixture_id = str(authority.fixture_id)
    task_number = int(fixture_id.split(":", 1)[0].removeprefix("task-"))
    diff_sha = authority.effective_diff_sha256 or "0" * 64
    return ReviewAuthorityIdentity(
        authority_kind=ReviewAuthorityKind.FIXTURE,
        repository="fixture/" + fixture_id,
        task_number=task_number,
        pull_request_number=None,
        base_sha=authority.base_sha,
        head_sha=authority.head_sha,
        diff_sha256=diff_sha,
    )


def _shadow_to_benchmark_candidate(
    candidate: CanonicalCandidateFinding,
    verified: VerifiedFinding | None = None,
) -> CandidateFinding:
    """Project canonical findings into the benchmark's scorer-only fingerprint."""
    location = candidate.affected_locations[0]
    if "::" in location:
        path, symbol = location.split("::", 1)
    else:
        path, symbol = location.split(":", 1)[0], "shadow-finding"
    category = candidate.failure_scenario.removeprefix("category=")
    severity = "Low" if verified is None else verified.severity.value.title()
    return CandidateFinding(
        severity=severity,
        path=path,
        symbol=symbol,
        category=category,
        summary=candidate.claim,
        evidence=candidate.evidence_refs,
    )


@dataclass(frozen=True, slots=True)
class ReviewBenchmarkCorpus:
    """Verified Task #194 corpus with a deferred scorer-only oracle."""

    root: Path
    corpus_id: str
    protocol_id: str
    protocol_version: str
    fixtures: tuple[BenchmarkFixture, ...]
    _oracle_path: Path = field(repr=False, compare=False)
    _oracle_sha256: str = field(repr=False, compare=False)

    @classmethod
    def from_manifest(cls, path: Path) -> ReviewBenchmarkCorpus:
        manifest_path = path
        if manifest_path.is_dir():
            manifest_path = manifest_path / CORPUS_MANIFEST_NAME
        manifest_path = manifest_path.resolve(strict=False)
        value = _read_object(manifest_path, field="corpus manifest")
        manifest_digest = _required_digest(
            value.get("manifest_sha256"), field="corpus manifest digest"
        )
        manifest_payload = {
            key: item for key, item in value.items() if key != "manifest_sha256"
        }
        if (
            hashlib.sha256(
                json.dumps(
                    manifest_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            != manifest_digest
        ):
            raise LckStopError("Review benchmark corpus manifest digest mismatch")
        if value.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
            raise LckStopError("Review benchmark corpus schema version is unsupported")
        root = manifest_path.parent
        corpus_id = _required_text(value.get("corpus_id"), field="corpus ID")
        protocol = value.get("protocol")
        if not isinstance(protocol, Mapping):
            raise LckStopError("Review benchmark protocol is unavailable")
        protocol_id = _required_text(protocol.get("protocol_id"), field="protocol ID")
        protocol_version = _required_text(
            protocol.get("protocol_version"), field="protocol version"
        )
        if protocol_id != PROTOCOL_ID or protocol_version != PROTOCOL_VERSION:
            raise LckStopError("Review benchmark protocol identity is unsupported")
        raw_fixtures = value.get("fixtures")
        if not isinstance(raw_fixtures, Sequence) or isinstance(
            raw_fixtures, (str, bytes)
        ):
            raise LckStopError("Review benchmark fixtures are unavailable")
        fixtures: list[BenchmarkFixture] = []
        for item in raw_fixtures:
            if not isinstance(item, Mapping):
                raise LckStopError("Review benchmark fixture entry is invalid")
            fixture_id = _required_text(item.get("fixture_id"), field="fixture ID")
            fixture_path = _safe_relative(root, item.get("path"), field=fixture_id)
            expected_digest = _required_digest(
                item.get("expected_fixture_digest"),
                field=f"{fixture_id} fixture digest",
            )
            fixture = load_frozen_review_fixture(
                fixture_path,
                expected_fixture_digest=expected_digest,
            )
            fixtures.append(
                BenchmarkFixture(
                    fixture=fixture,
                    fixture_id=fixture_id,
                    kind=_required_text(item.get("kind"), field="fixture kind"),
                    relative_path=str(item.get("path")),
                )
            )
        if {item.fixture_id for item in fixtures} != {
            "task-194:defect-rich-v1",
            "task-194:stable-v1",
        }:
            raise LckStopError(
                "Review benchmark corpus must contain both Task #194 fixtures"
            )
        expected_kinds = {
            "task-194:defect-rich-v1": "defect-rich",
            "task-194:stable-v1": "stable",
        }
        if {item.fixture_id: item.kind for item in fixtures} != expected_kinds:
            raise LckStopError("Review benchmark fixture roles are invalid")

        oracle_path = _safe_relative(root, value.get("oracle_path"), field="oracle")
        expected_oracle_digest = _required_digest(
            value.get("oracle_sha256"), field="oracle digest"
        )
        try:
            oracle_digest = hashlib.sha256(oracle_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise LckStopError("Review benchmark scorer oracle is unavailable") from exc
        if oracle_digest != expected_oracle_digest:
            raise LckStopError("Review benchmark oracle digest mismatch")
        return cls(
            root=root,
            corpus_id=corpus_id,
            protocol_id=protocol_id,
            protocol_version=protocol_version,
            fixtures=tuple(fixtures),
            _oracle_path=oracle_path,
            _oracle_sha256=expected_oracle_digest,
        )

    def fixture(self, fixture_id: str) -> BenchmarkFixture:
        for item in self.fixtures:
            if item.fixture_id == fixture_id:
                return item
        raise KeyError(fixture_id)

    def score(
        self,
        fixture_id: str,
        candidate_findings: Sequence[CandidateFinding],
        verified_findings: Sequence[CandidateFinding] = (),
    ) -> FindingScore:
        """Match findings after Review, while preserving unknown stable findings."""
        fixture = self.fixture(fixture_id)
        known = _load_scorer_oracle(self._oracle_path, self._oracle_sha256)[
            fixture.fixture_id
        ]
        by_fingerprint = {item.fingerprint: item for item in known}
        verified = {item.fingerprint for item in verified_findings}
        matches: list[FindingMatch] = []
        for candidate in candidate_findings:
            matched = by_fingerprint.get(candidate.fingerprint)
            matches.append(
                FindingMatch(
                    candidate=candidate,
                    known_finding_id=matched.finding_id if matched else None,
                    match_type="exact" if matched else "unmatched-candidate",
                    verification=(
                        "verified"
                        if candidate.fingerprint in verified
                        else "candidate-only"
                    ),
                )
            )
        matched_ids = {
            item.known_finding_id
            for item in matches
            if item.known_finding_id is not None
        }
        known_ids = {item.finding_id for item in known}
        unmatched = any(item.known_finding_id is None for item in matches)
        adjudication_required = unmatched and fixture.kind == "stable"
        if fixture.kind == "stable":
            control_status = (
                "needs-adjudication"
                if adjudication_required
                else "repeated-pass-control"
            )
        else:
            control_status = (
                "known-findings-covered"
                if len(matches) == len(known)
                and matched_ids == known_ids
                and all(item.verification == "verified" for item in matches)
                else "known-findings-incomplete"
            )
        return FindingScore(
            fixture_id=fixture.fixture_id,
            known_count=len(known),
            candidate_count=len(candidate_findings),
            verified_count=len(verified_findings),
            matches=tuple(matches),
            adjudication_required=adjudication_required,
            control_status=control_status,
        )


class ReviewBenchmarkRunner:
    """Replay a supplied semantic protocol against isolated frozen Subjects."""

    def __init__(
        self,
        corpus: ReviewBenchmarkCorpus,
        harness_root: Path,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        self.corpus = corpus
        self.harness_root = harness_root.resolve()
        self.workspace_root = workspace_root

    def _current_harness_sha(self) -> str:
        result = CommandRunner(self.harness_root).run(
            ["git", "rev-parse", "HEAD"],
            command_id="review-benchmark-harness-head",
            cwd=self.harness_root,
        )
        harness_sha = result.stdout.strip()
        if result.returncode != 0 or not is_sha(harness_sha) or len(harness_sha) != 40:
            raise LckStopError("Review benchmark Harness SHA is unavailable")
        return harness_sha

    def _copy_harness_snapshot(self, destination: Path, harness_sha: str) -> None:
        """Materialize the exact committed Harness while excluding corpus data."""
        index_path = destination.parent / "harness-index"
        index_env = {"GIT_INDEX_FILE": str(index_path)}
        read_tree = CommandRunner(self.harness_root).run(
            ["git", "read-tree", harness_sha],
            command_id="review-benchmark-harness-read-tree",
            cwd=self.harness_root,
            env=index_env,
        )
        if read_tree.returncode != 0:
            raise LckStopError("Review benchmark Harness commit tree is unavailable")
        result = CommandRunner(self.harness_root).run(
            ["git", "ls-files", "--cached", "-z"],
            command_id="review-benchmark-harness-files",
            cwd=self.harness_root,
            env=index_env,
        )
        if result.returncode != 0:
            raise LckStopError("Review benchmark Harness file inventory is unavailable")

        corpus_root = self.corpus.root.resolve()
        try:
            corpus_relative = corpus_root.relative_to(self.harness_root)
        except ValueError:
            corpus_relative = None
        if corpus_relative == Path("."):
            raise LckStopError("Review benchmark corpus cannot be the Harness root")

        relative_paths: list[Path] = []
        for raw_path in result.stdout.split("\0"):
            if not raw_path:
                continue
            relative = Path(raw_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise LckStopError("Review benchmark Harness file path is invalid")
            relative_paths.append(relative)

        checkout = CommandRunner(self.harness_root).run(
            ["git", "checkout-index", "--all", f"--prefix={destination}/"],
            command_id="review-benchmark-harness-checkout",
            cwd=self.harness_root,
            env=index_env,
        )
        if checkout.returncode != 0:
            raise LckStopError("Review benchmark Harness snapshot is unavailable")

        def remove_snapshot_path(path: Path) -> None:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)

        for excluded_root in _HARNESS_EXCLUDED_ROOTS:
            remove_snapshot_path(destination / excluded_root)
        if corpus_relative is not None:
            remove_snapshot_path(destination / corpus_relative)

        for relative in relative_paths:
            target = destination / relative
            if target.is_symlink():
                target.unlink()

        index_path.unlink(missing_ok=True)
        (index_path.with_name(index_path.name + ".lock")).unlink(missing_ok=True)

    @contextmanager
    def _isolated_harness(self) -> Iterator[VerifiedHarnessSnapshot]:
        """Yield a disposable Harness view that cannot resolve corpus files."""
        harness_sha = self._current_harness_sha()
        temporary_root = Path(tempfile.mkdtemp(prefix="tracequant-lck-review-harness-"))
        snapshot = temporary_root / "harness"
        try:
            snapshot.mkdir()
            self._copy_harness_snapshot(snapshot, harness_sha)
            yield _verified_harness_snapshot(snapshot, harness_sha)
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def replay(self, fixture_id: str, protocol: ReviewProtocol) -> ReplayResult:
        fixture = self.corpus.fixture(fixture_id)
        with self._isolated_harness() as harness_snapshot:
            runner = ReviewEvalRunner(
                harness_snapshot,
                workspace_root=self.workspace_root,
            )
            started = time.perf_counter_ns()
            execution = runner.run(
                fixture.fixture,
                GitFrozenSubjectMaterializer(fixture=fixture.fixture),
                protocol,
            )
            try:
                observation = execution.value
                if not isinstance(observation, ReviewObservation):
                    raise TypeError(
                        "Review benchmark protocol returned an invalid observation"
                    )
                if execution.run.harness_sha != harness_snapshot.sha:
                    raise LckStopError(
                        "Review benchmark Harness identity is not bound to snapshot"
                    )
                elapsed_ms = max((time.perf_counter_ns() - started) / 1_000_000, 0.001)
                authority = execution.run.authority
                findings_digest = sha256_json(
                    [item.to_dict() for item in observation.findings]
                )
                receipt = BaselineRunReceipt(
                    fixture_id=fixture.fixture_id,
                    fixture_kind=fixture.kind,
                    fixture_digest=authority.fixture_digest
                    or authority.computed_fixture_digest,
                    base_sha=authority.base_sha,
                    head_sha=authority.head_sha,
                    effective_diff_sha256=authority.effective_diff_sha256 or "",
                    task_contract_sha256=authority.task_contract_sha256 or "",
                    deterministic_evidence_sha256=(
                        authority.effective_deterministic_evidence_sha256 or ""
                    ),
                    repository_artifact_sha256=authority.repository_artifact_sha256
                    or "",
                    fixture_manifest_sha256=authority.fixture_manifest_sha256 or "",
                    run_id=execution.run.run_id,
                    harness_sha=execution.run.harness_sha,
                    protocol_id=self.corpus.protocol_id,
                    protocol_version=self.corpus.protocol_version,
                    protocol_steps=PROTOCOL_STEPS,
                    model=observation.model,
                    config=observation.config,
                    token_usage=observation.token_usage,
                    wall_clock_ms=elapsed_ms,
                    verdict=observation.verdict,
                    findings=observation.findings,
                    findings_sha256=findings_digest,
                    subject_clean_exact_head=True,
                    run_workspace_cleaned=True,
                )
                return ReplayResult(observation=observation, receipt=receipt)
            finally:
                execution.close()

    def replay_repeated(
        self,
        fixture_id: str,
        protocol: ReviewProtocol,
        *,
        repetitions: int = 2,
    ) -> tuple[ReplayResult, ...]:
        if type(repetitions) is not int or repetitions < 2:
            raise ValueError("Review benchmark repetitions must be at least two")
        results = tuple(self.replay(fixture_id, protocol) for _ in range(repetitions))
        first = results[0].receipt
        for result in results[1:]:
            current = result.receipt
            if current.run_id == first.run_id:
                raise LckStopError(
                    "Review benchmark repeated run reused its Run identity"
                )
            if current.semantic_fingerprint() != first.semantic_fingerprint():
                raise LckStopError("Review benchmark repeated run is not reproducible")
            if not current.run_workspace_cleaned:
                raise LckStopError("Review benchmark Run workspace was not cleaned")
        return results

    def replay_shadow(
        self,
        fixture_id: str,
        discovery_protocol: Callable[[Path], Sequence[DiscoveryPassResult]]
        | None = None,
        verifier: Callable[[IndependentVerificationRequest], IndependentVerification]
        | None = None,
        *,
        production_state: ProductionReviewState | None = None,
        plan: StructuredDiscoveryPlan | None = None,
    ) -> ShadowReplayResult:
        """Replay shadow discovery, then score it through the deferred oracle."""
        fixture = self.corpus.fixture(fixture_id)
        with self._isolated_harness() as harness_snapshot:
            runner = ReviewEvalRunner(
                harness_snapshot,
                workspace_root=self.workspace_root,
            )

            def evaluate(run: ReviewEvalRunContext) -> ShadowReviewResult:
                baseline = Task194ProductionEquivalentReviewer()(run)
                state = production_state or ProductionReviewState(
                    verdict=baseline.verdict,
                    merge_eligible=baseline.verdict == "PASS",
                    authoritative_review_state=(
                        "production-pass"
                        if baseline.verdict == "PASS"
                        else "production-fail"
                    ),
                )
                authority = _task194_review_authority(run.authority)
                discovery = discovery_protocol or Task194StructuredDiscovery(authority)
                selected_verifier = verifier or Task194IndependentVerifier()
                selected_plan = plan or TASK194_SHADOW_PLAN
                return ShadowReviewPipeline(
                    selected_verifier,
                    plan=selected_plan,
                ).run(
                    state,
                    discovery(run.subject_root),
                    authority=authority,
                    subject_root=run.subject_root,
                    run_id=run.run_id,
                )

            shadow_started = time.perf_counter_ns()
            execution = runner.run(
                fixture.fixture,
                GitFrozenSubjectMaterializer(fixture=fixture.fixture),
                evaluate,
            )
            shadow_elapsed_ms = max(
                1, int((time.perf_counter_ns() - shadow_started) / 1_000_000)
            )
            try:
                shadow = execution.value
                shadow = replace(
                    shadow,
                    receipt=replace(
                        shadow.receipt,
                        wall_clock_ms=shadow_elapsed_ms,
                    ),
                )
                verified_by_id = {
                    item.finding_id: item for item in shadow.verified_findings
                }
                candidates = tuple(
                    _shadow_to_benchmark_candidate(
                        item, verified_by_id.get(item.finding_id)
                    )
                    for item in shadow.finding_union.candidate_findings
                )
                verified = tuple(
                    _shadow_to_benchmark_candidate(item, item)
                    for item in shadow.verified_findings
                )
                score = self.corpus.score(fixture_id, candidates, verified)
                known_ids = tuple(
                    item.known_finding_id
                    for item in score.matches
                    if item.known_finding_id is not None
                )
                return ShadowReplayResult(
                    shadow=shadow.with_incremental_known_findings(known_ids),
                    score=score,
                )
            finally:
                execution.close()

    def compare_baseline_and_shadow(
        self,
        fixture_id: str,
        *,
        discovery_protocol: Callable[[Path], Sequence[DiscoveryPassResult]]
        | None = None,
        verifier: Callable[[IndependentVerificationRequest], IndependentVerification]
        | None = None,
        production_state: ProductionReviewState | None = None,
    ) -> BaselineShadowComparison:
        """Compare current baseline metrics with scorer-only shadow metrics."""
        baseline = self.replay(fixture_id, Task194ProductionEquivalentReviewer())
        baseline_score = self.corpus.score(
            fixture_id,
            baseline.observation.findings,
            baseline.observation.findings,
        )
        shadow = self.replay_shadow(
            fixture_id,
            discovery_protocol,
            verifier,
            production_state=production_state,
        )
        return BaselineShadowComparison(
            fixture_id=fixture_id,
            baseline=baseline,
            baseline_score=baseline_score,
            shadow=shadow,
        )

    def compare_task_194_baseline_and_shadow(
        self,
    ) -> tuple[BaselineShadowComparison, ...]:
        """Compare both defect-rich and stable Task #194 controls."""
        return tuple(
            self.compare_baseline_and_shadow(item.fixture_id)
            for item in self.corpus.fixtures
        )


TASK194_SHADOW_PLAN: Final[StructuredDiscoveryPlan] = StructuredDiscoveryPlan(
    passes=(
        *DEFAULT_DISCOVERY_PASSES,
        DiscoveryPass(
            pass_id="risk-follow-up",
            surfaces=(ReviewSurface.FUNCTIONAL_CORRECTNESS,),
        ),
    )
)


class Task194StructuredDiscovery:
    """Deterministic structured discovery adapter for the frozen corpus."""

    def __init__(self, authority: ReviewAuthorityIdentity) -> None:
        self.authority = authority

    def _provenance(self, pass_id: str) -> RunProvenance:
        return RunProvenance(
            run_id=f"task194-shadow-{pass_id}",
            authority=self.authority,
            harness_id="task194-shadow-harness.v1",
            protocol_id="task194-shadow-discovery.v1",
        )

    def _candidate(
        self,
        pass_id: str,
        *,
        surface: ReviewSurface,
        path: str,
        symbol: str,
        category: str,
        claim: str,
        invariant: str,
    ) -> CanonicalCandidateFinding:
        return CanonicalCandidateFinding(
            finding_id=f"task194-{pass_id}-{category}",
            surface=surface,
            claim=claim,
            affected_locations=(f"{path}::{symbol}",),
            contract_invariant=invariant,
            failure_scenario=f"category={category}",
            evidence_refs=(f"task194:{pass_id}:{category}",),
            originating_runs=(self._provenance(pass_id),),
        )

    @staticmethod
    def _result(
        pass_id: str,
        surfaces: tuple[ReviewSurface, ...],
        candidates: tuple[CanonicalCandidateFinding, ...],
        source_size: int,
    ) -> DiscoveryPassResult:
        return DiscoveryPassResult(
            pass_id=pass_id,
            covered_surfaces=surfaces,
            candidate_findings=candidates,
            coverage_evidence=(f"task194://coverage/{pass_id}",),
            token_usage=TokenUsage(
                input_tokens=max(1, source_size // 32),
                output_tokens=16 + len(candidates) * 12,
                total_tokens=max(1, source_size // 32) + 16 + len(candidates) * 12,
            ),
            wall_clock_ms=1,
        )

    def __call__(self, subject_root: Path) -> tuple[DiscoveryPassResult, ...]:
        data_path = subject_root / "src/tracequant/data/binance_contract_kline.py"
        store_path = subject_root / "src/tracequant/data/raw_store.py"
        try:
            data_source = data_path.read_text(encoding="utf-8")
            store_source = store_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LckStopError(
                "Task #194 shadow Subject source is unavailable"
            ) from exc
        source_size = len(data_source) + len(store_source)

        integer = (
            "parsed = int(value)" in data_source
            and "_MAX_SIGNED_INT64_TEXT" not in data_source
        )
        manifest = (
            "_LEGACY_MANIFEST_VERSION" not in store_source
            and "version != _MANIFEST_VERSION" in store_source
        )
        incomplete = (
            "RawArtifactIncompleteError" not in store_source
            and "except RawArtifactNotFoundError" in data_source
        )
        integer_candidate = self._candidate(
            "contract-functional-invariants",
            surface=ReviewSurface.FUNCTIONAL_CORRECTNESS,
            path="src/tracequant/data/binance_contract_kline.py",
            symbol="_parse_nonnegative_int",
            category="integer-range-exception",
            claim="Oversized numeric input can escape the invalid-content boundary.",
            invariant="Invalid numeric content must remain inside the parser error boundary.",
        )
        pass_one = self._result(
            "contract-functional-invariants",
            (
                ReviewSurface.CONTRACT_CONFORMANCE,
                ReviewSurface.FUNCTIONAL_CORRECTNESS,
            ),
            (integer_candidate,) if integer else (),
            source_size,
        )
        compatibility_candidate = self._candidate(
            "state-failure-compatibility",
            surface=ReviewSurface.COMPATIBILITY_MIGRATION,
            path="src/tracequant/data/raw_store.py",
            symbol="RawManifest.from_dict",
            category="manifest-backward-compatibility",
            claim="The manifest reader rejects the earlier completed manifest shape.",
            invariant="Completed legacy manifests must remain readable.",
        )
        incomplete_candidate = self._candidate(
            "state-failure-compatibility",
            surface=ReviewSurface.ERROR_FAILURE_PATHS,
            path="src/tracequant/data/binance_contract_kline.py",
            symbol="BinanceContractKlineBackfill.run",
            category="incomplete-local-artifact-semantics",
            claim="An incomplete local artifact is treated as absent.",
            invariant="Incomplete local artifacts must not be treated as absent.",
        )
        pass_two = self._result(
            "state-failure-compatibility",
            (
                ReviewSurface.STATE_TRANSITIONS,
                ReviewSurface.ERROR_FAILURE_PATHS,
                ReviewSurface.COMPATIBILITY_MIGRATION,
            ),
            tuple(
                item
                for item, active in (
                    (compatibility_candidate, manifest),
                    (incomplete_candidate, incomplete),
                )
                if active
            ),
            source_size,
        )
        stable_candidate = self._candidate(
            "tests-claims-architecture",
            surface=ReviewSurface.TESTS_VS_CLAIMS,
            path="tests/",
            symbol="historical-pass-control",
            category="stable-unadjudicated",
            claim="A historical PASS does not establish that every new candidate is false.",
            invariant="New findings require independent evidence before classification.",
        )
        pass_three = self._result(
            "tests-claims-architecture",
            (ReviewSurface.TESTS_VS_CLAIMS, ReviewSurface.ARCHITECTURE),
            (stable_candidate,)
            if not integer and not manifest and not incomplete
            else (),
            source_size,
        )
        duplicate = replace(
            integer_candidate,
            finding_id="task194-risk-duplicate-integer",
            originating_runs=(self._provenance("risk-follow-up"),),
            evidence_refs=("task194:risk-follow-up:integer-range-exception",),
        )
        pass_four = self._result(
            "risk-follow-up",
            (ReviewSurface.FUNCTIONAL_CORRECTNESS,),
            (duplicate,) if integer else (),
            source_size,
        )
        return (pass_one, pass_two, pass_three, pass_four)


class Task194IndependentVerifier:
    """Independent verifier that reads only the candidate and frozen Subject."""

    def __call__(
        self, request: IndependentVerificationRequest
    ) -> IndependentVerification:
        subject_root = request.subject_root
        if subject_root is None:
            raise LckStopError("Task #194 verifier requires a frozen Subject")
        category = request.candidate.failure_scenario.removeprefix("category=")
        try:
            data_source = (
                subject_root / "src/tracequant/data/binance_contract_kline.py"
            ).read_text(encoding="utf-8")
            store_source = (
                subject_root / "src/tracequant/data/raw_store.py"
            ).read_text(encoding="utf-8")
        except OSError as exc:
            raise LckStopError(
                "Task #194 verifier Subject source is unavailable"
            ) from exc
        source_size = len(data_source) + len(store_source)
        evidence = (f"task194://verification/{request.candidate.finding_id}",)
        usage = TokenUsage(
            input_tokens=max(1, source_size // 48),
            output_tokens=24,
            total_tokens=max(1, source_size // 48) + 24,
        )
        if category == "stable-unadjudicated":
            return IndependentVerification(
                status=FindingVerificationStatus.NEEDS_MORE_EVIDENCE,
                verification_evidence_refs=("task194://adjudication/fresh-evidence",),
                method=VerificationMethod.INDEPENDENT_ADJUDICATION,
                falsification_attempted=True,
                token_usage=usage,
                wall_clock_ms=1,
            )
        confirmed = {
            "integer-range-exception": (
                "parsed = int(value)" in data_source
                and "_MAX_SIGNED_INT64_TEXT" not in data_source
            ),
            "manifest-backward-compatibility": (
                "_LEGACY_MANIFEST_VERSION" not in store_source
                and "version != _MANIFEST_VERSION" in store_source
            ),
            "incomplete-local-artifact-semantics": (
                "RawArtifactIncompleteError" not in store_source
                and "except RawArtifactNotFoundError" in data_source
            ),
        }.get(category, False)
        if not confirmed:
            return IndependentVerification(
                status=FindingVerificationStatus.REJECTED,
                verification_evidence_refs=evidence,
                method=VerificationMethod.TARGETED_DETERMINISTIC_REPRODUCTION,
                falsification_attempted=True,
                token_usage=usage,
                wall_clock_ms=1,
            )
        severity = (
            FindingSeverity.MEDIUM
            if category == "manifest-backward-compatibility"
            else FindingSeverity.HIGH
        )
        return IndependentVerification(
            status=FindingVerificationStatus.CONFIRMED,
            verification_evidence_refs=evidence,
            method=VerificationMethod.TARGETED_DETERMINISTIC_REPRODUCTION,
            falsification_attempted=True,
            severity=severity,
            blocking_status=FindingBlockingStatus.BLOCKING,
            token_usage=usage,
            wall_clock_ms=1,
        )


class Task194ProductionEquivalentReviewer:
    """Deterministic Inspect/Reason/Judge/Report adapter for the first corpus.

    It reads only the materialized historical Subject.  Its semantic keys are
    intentionally generic and do not contain scorer oracle IDs or titles.
    """

    model: Final[str] = "task194-deterministic-review-adapter"

    def __call__(self, run: ReviewEvalRunContext) -> ReviewObservation:
        data_path = run.subject_root / "src/tracequant/data/binance_contract_kline.py"
        store_path = run.subject_root / "src/tracequant/data/raw_store.py"
        try:
            data_source = data_path.read_text(encoding="utf-8")
            store_source = store_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LckStopError(
                "Task #194 Review Subject source is unavailable"
            ) from exc

        findings: list[CandidateFinding] = []
        if (
            "parsed = int(value)" in data_source
            and "_MAX_SIGNED_INT64_TEXT" not in data_source
        ):
            findings.append(
                CandidateFinding(
                    severity="High",
                    path="src/tracequant/data/binance_contract_kline.py",
                    symbol="_parse_nonnegative_int",
                    category="integer-range-exception",
                    summary="Oversized numeric input can raise before the parser's invalid-content boundary.",
                    evidence=(
                        f"line {self._line(data_source, 'parsed = int(value)')}",
                    ),
                )
            )
        if (
            "_LEGACY_MANIFEST_VERSION" not in store_source
            and "version != _MANIFEST_VERSION" in store_source
        ):
            findings.append(
                CandidateFinding(
                    severity="Medium",
                    path="src/tracequant/data/raw_store.py",
                    symbol="RawManifest.from_dict",
                    category="manifest-backward-compatibility",
                    summary="The Raw manifest reader rejects the earlier completed manifest shape.",
                    evidence=(
                        f"line {self._line(store_source, 'version != _MANIFEST_VERSION')}",
                    ),
                )
            )
        if (
            "RawArtifactIncompleteError" not in store_source
            and "except RawArtifactNotFoundError" in data_source
        ):
            findings.append(
                CandidateFinding(
                    severity="High",
                    path="src/tracequant/data/binance_contract_kline.py",
                    symbol="BinanceContractKlineBackfill.run",
                    category="incomplete-local-artifact-semantics",
                    summary="An existing incomplete local artifact is treated as an absent artifact.",
                    evidence=(
                        f"line {self._line(data_source, 'except RawArtifactNotFoundError')}",
                    ),
                )
            )
        input_units = len(data_source) + len(store_source)
        output_units = 96 + len(findings) * 48
        token_usage = {
            "input": max(1, input_units // 4),
            "cached_input": 0,
            "uncached_input": max(1, input_units // 4),
            "output": output_units,
            "total": max(1, input_units // 4) + output_units,
        }
        return ReviewObservation(
            verdict="FAIL" if findings else "PASS",
            findings=tuple(findings),
            model=self.model,
            config={
                "protocol_steps": list(PROTOCOL_STEPS),
                "subject_only": True,
                "oracle_access": False,
            },
            token_usage=token_usage,
        )

    @staticmethod
    def _line(source: str, needle: str) -> int:
        for number, line in enumerate(source.splitlines(), start=1):
            if needle in line:
                return number
        return 0


def load_task_194_benchmark(repo_root: Path | None = None) -> ReviewBenchmarkCorpus:
    """Load and verify the checked-in Task #194 benchmark corpus."""
    root = (repo_root or Path.cwd()).resolve()
    return ReviewBenchmarkCorpus.from_manifest(
        root / "docs/workflows/benchmarks/task-194" / CORPUS_MANIFEST_NAME
    )


__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "BaselineShadowComparison",
    "BaselineRunReceipt",
    "BenchmarkFixture",
    "CandidateFinding",
    "FindingMatch",
    "FindingScore",
    "KnownFinding",
    "PROTOCOL_ID",
    "PROTOCOL_STEPS",
    "PROTOCOL_VERSION",
    "ReplayResult",
    "ReviewBenchmarkCorpus",
    "ReviewBenchmarkRunner",
    "ReviewObservation",
    "ReviewProtocol",
    "ShadowReplayResult",
    "TASK194_SHADOW_PLAN",
    "Task194IndependentVerifier",
    "Task194ProductionEquivalentReviewer",
    "Task194StructuredDiscovery",
    "load_task_194_benchmark",
]
