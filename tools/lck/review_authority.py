"""Nominal authority contract for the production Review path."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .common import is_sha
from .models import LckStopError, LiveState


def _required_sha(value: Any, *, field: str) -> str:
    if not is_sha(value):
        raise LckStopError(f"Review authority {field} is unavailable")
    return str(value)


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


def require_live_review_authority(value: Any) -> LiveReviewAuthority:
    """Enforce the production Review authority boundary."""
    if type(value) is not LiveReviewAuthority:
        raise TypeError("production Review requires LiveReviewAuthority")
    return value


__all__ = [
    "LiveReviewAuthority",
    "require_live_review_authority",
]
