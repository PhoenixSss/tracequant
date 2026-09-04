"""Nominal authority contracts for production Review and Review Eval.

The two authority types intentionally live next to, rather than inside, the
phase controllers.  A production Review target is derived from the current
GitHub PR.  An Eval target is supplied by a frozen fixture and never comes
from the live-state resolver.  Keeping these as unrelated dataclasses makes a
wrong authority source visible at the API boundary instead of silently
coercing one into the other.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from workflow_common import is_sha

from .models import LckStopError, LiveState


def _required_sha(value: Any, *, field: str) -> str:
    if not is_sha(value):
        raise LckStopError(f"Review authority {field} is unavailable")
    return str(value)


def _optional_digest(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise LckStopError(f"Review authority {field} is not a SHA-256 digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise LckStopError(f"Review authority {field} is not a SHA-256 digest") from exc
    return value.lower()


@dataclass(frozen=True, slots=True)
class LiveReviewAuthority:
    """The production Review authority derived from one current OPEN PR."""

    repository: str
    task_number: int
    pr_number: int
    base_sha: str
    head_sha: str
    task_body_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.repository, str) or not self.repository.strip():
            raise ValueError("live Review authority requires repository")
        if (
            not isinstance(self.task_number, int)
            or isinstance(self.task_number, bool)
            or self.task_number <= 0
        ):
            raise ValueError("live Review authority requires a positive Task number")
        if (
            not isinstance(self.pr_number, int)
            or isinstance(self.pr_number, bool)
            or self.pr_number <= 0
        ):
            raise ValueError("live Review authority requires a positive PR number")
        object.__setattr__(
            self,
            "base_sha",
            _required_sha(self.base_sha, field="base SHA"),
        )
        object.__setattr__(
            self,
            "head_sha",
            _required_sha(self.head_sha, field="head SHA"),
        )
        if not isinstance(self.task_body_sha256, str) or not self.task_body_sha256:
            raise ValueError("live Review authority requires Task Contract identity")

    @classmethod
    def from_state(
        cls,
        state: LiveState,
        task_contract: Mapping[str, Any],
    ) -> LiveReviewAuthority:
        """Derive production authority from live-resolved state only."""
        if not isinstance(state, LiveState):
            raise TypeError("production Review authority requires LiveState")
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            raise LckStopError("Review target has no current OPEN PR")
        pr_number = pr.get("number")
        if (
            not isinstance(pr_number, int)
            or isinstance(pr_number, bool)
            or pr_number <= 0
        ):
            raise LckStopError("Review target PR number is unavailable")
        base_sha = _required_sha(pr.get("baseRefOid"), field="base SHA")
        head_sha = _required_sha(pr.get("headRefOid"), field="head SHA")
        task_body_sha256 = task_contract.get("body_sha256")
        if not isinstance(task_body_sha256, str) or not task_body_sha256:
            raise LckStopError("Review target Task Contract identity is unavailable")
        if not isinstance(state.repository, str) or not state.repository:
            raise LckStopError("Review target repository identity is unavailable")
        return cls(
            repository=state.repository,
            task_number=state.issue_number,
            pr_number=pr_number,
            base_sha=base_sha,
            head_sha=head_sha,
            task_body_sha256=task_body_sha256,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "authority_kind": "live-pr",
            "repository": self.repository,
            "task_number": self.task_number,
            "pr_number": self.pr_number,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "task_body_sha256": self.task_body_sha256,
        }


@dataclass(frozen=True, slots=True)
class FrozenReviewAuthority:
    """Explicit authority for one frozen Review Eval Subject.

    Fixture packaging and manifest semantics deliberately remain outside this
    contract.  The current Task only needs a stable fixture identifier and
    explicit historical base/head identities.  Optional digests reserve
    identity slots for the fixture Task without allowing a live PR to fill
    them implicitly.
    """

    fixture_id: str
    base_sha: str
    head_sha: str
    effective_diff_sha256: str | None = None
    task_contract_sha256: str | None = None
    evidence_sha256: str | None = None
    repository_artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id.strip():
            raise ValueError("frozen Review Eval authority requires fixture_id")
        object.__setattr__(
            self,
            "base_sha",
            _required_sha(self.base_sha, field="frozen base SHA"),
        )
        object.__setattr__(
            self,
            "head_sha",
            _required_sha(self.head_sha, field="frozen head SHA"),
        )
        for field in (
            "effective_diff_sha256",
            "task_contract_sha256",
            "evidence_sha256",
            "repository_artifact_sha256",
        ):
            object.__setattr__(
                self,
                field,
                _optional_digest(getattr(self, field), field=field),
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FrozenReviewAuthority:
        """Parse only a frozen authority mapping; never a live PR mapping."""
        if not isinstance(value, Mapping):
            raise TypeError("frozen Review Eval authority must be a mapping")
        return cls(
            fixture_id=value.get("fixture_id", ""),
            base_sha=value.get("base_sha", ""),
            head_sha=value.get("head_sha", ""),
            effective_diff_sha256=value.get("effective_diff_sha256"),
            task_contract_sha256=value.get("task_contract_sha256"),
            evidence_sha256=value.get("evidence_sha256"),
            repository_artifact_sha256=value.get("repository_artifact_sha256"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "authority_kind": "frozen-fixture",
            "fixture_id": self.fixture_id,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
        }
        for field in (
            "effective_diff_sha256",
            "task_contract_sha256",
            "evidence_sha256",
            "repository_artifact_sha256",
        ):
            value = getattr(self, field)
            if value is not None:
                result[field] = value
        return result


def require_frozen_review_authority(value: Any) -> FrozenReviewAuthority:
    """Enforce the Eval entry boundary without accepting live authority."""
    if type(value) is not FrozenReviewAuthority:
        raise TypeError(
            "Review Eval requires FrozenReviewAuthority; live PR authority "
            "cannot be used as a frozen fixture"
        )
    return value


def require_live_review_authority(value: Any) -> LiveReviewAuthority:
    """Enforce the production entry boundary without accepting frozen authority."""
    if type(value) is not LiveReviewAuthority:
        raise TypeError(
            "production Review requires LiveReviewAuthority; frozen fixture "
            "authority cannot replace a current PR"
        )
    return value


__all__ = [
    "FrozenReviewAuthority",
    "LiveReviewAuthority",
    "require_frozen_review_authority",
    "require_live_review_authority",
]
