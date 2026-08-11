# ruff: noqa: E402, I001

from __future__ import annotations

from typing import Any

import pytest

from benchmark_common import BenchmarkError  # type: ignore[import-not-found]
from benchmark_identity_validator import validate_pr_identity  # type: ignore[import-not-found]

BASE_BRANCH = "experiment/task65-v2-a-control-base"
BASE_SHA = "a" * 40
HEAD_BRANCH = "experiment/task65-v2-a-legacy-no-runner-codex"
HEAD_SHA = "b" * 40


def _pr(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "number": 77,
        "baseRefName": BASE_BRANCH,
        "baseRefOid": BASE_SHA,
        "headRefName": HEAD_BRANCH,
        "headRefOid": HEAD_SHA,
        "isDraft": False,
    }
    doc.update(overrides)
    return doc


def test_identity_valid_pass() -> None:
    report = validate_pr_identity(
        _pr(),
        expected_base_branch=BASE_BRANCH,
        expected_base_sha=BASE_SHA,
        expected_head_branch=HEAD_BRANCH,
        expected_head_sha=HEAD_SHA,
        number=77,
    )
    assert report["verdict"] == "IDENTITY VALID"
    assert all(c["status"] == "pass" for c in report["checks"])


def test_identity_invalid_on_retarget_to_default_branch() -> None:
    # The safety gate: PR retargeted to main -> IDENTITY INVALID.
    report = validate_pr_identity(
        _pr(baseRefName="main", baseRefOid="c" * 40),
        expected_base_branch=BASE_BRANCH,
        expected_base_sha=BASE_SHA,
        expected_head_branch=HEAD_BRANCH,
        expected_head_sha=HEAD_SHA,
    )
    assert report["verdict"] == "IDENTITY INVALID (fail closed)"
    failed = {c["name"] for c in report["checks"] if c["status"] == "fail"}
    assert "base_ref_name" in failed
    assert "base_ref_oid" in failed


def test_identity_invalid_on_head_mismatch() -> None:
    report = validate_pr_identity(
        _pr(headRefOid="d" * 40),
        expected_base_branch=BASE_BRANCH,
        expected_base_sha=BASE_SHA,
        expected_head_branch=HEAD_BRANCH,
        expected_head_sha=HEAD_SHA,
    )
    assert report["verdict"] == "IDENTITY INVALID (fail closed)"
    assert any(
        c["name"] == "head_ref_oid" and c["status"] == "fail" for c in report["checks"]
    )


def test_identity_invalid_on_draft() -> None:
    report = validate_pr_identity(
        _pr(isDraft=True),
        expected_base_branch=BASE_BRANCH,
        expected_base_sha=BASE_SHA,
        expected_head_branch=HEAD_BRANCH,
        expected_head_sha=HEAD_SHA,
    )
    assert report["verdict"] == "IDENTITY INVALID (fail closed)"
    assert any(
        c["name"] == "is_draft_false" and c["status"] == "fail"
        for c in report["checks"]
    )


def test_identity_invalid_on_number_mismatch() -> None:
    report = validate_pr_identity(
        _pr(number=78),
        expected_base_branch=BASE_BRANCH,
        expected_base_sha=BASE_SHA,
        expected_head_branch=HEAD_BRANCH,
        expected_head_sha=HEAD_SHA,
        number=77,
    )
    assert report["verdict"] == "IDENTITY INVALID (fail closed)"


def test_identity_missing_fields_fail_closed() -> None:
    with pytest.raises(BenchmarkError):
        validate_pr_identity(
            {"number": 77},
            expected_base_branch=BASE_BRANCH,
            expected_base_sha=BASE_SHA,
            expected_head_branch=HEAD_BRANCH,
            expected_head_sha=HEAD_SHA,
        )


def test_identity_non_object_fail_closed() -> None:
    with pytest.raises(BenchmarkError):
        validate_pr_identity(
            "not a dict",
            expected_base_branch=BASE_BRANCH,
            expected_base_sha=BASE_SHA,
            expected_head_branch=HEAD_BRANCH,
            expected_head_sha=HEAD_SHA,
        )
