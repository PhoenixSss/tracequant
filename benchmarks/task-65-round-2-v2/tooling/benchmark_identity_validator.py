"""Conductor-side benchmark identity validator for task-65-round-2-v2.

Generation A has no ``pr_resolve.py`` in its native fixture, and its fixture
must not be modified to add one.  The equivalent experimental identity
requirement for Arm A (and, for uniformity, any Arm) is enforced by this
conductor-side validator, which mechanically verifies the same fields that
``pr_resolve.py`` verifies for B/C/D:

- ``baseRefName`` == expected control-base branch;
- ``baseRefOid`` == ``ARM_CONTROL_BASE_SHA``;
- ``headRefName`` == expected business branch;
- ``headRefOid`` == expected final business head;
- ``isDraft`` == false.

Any mismatch -> ``IDENTITY INVALID`` (fail closed).  This validator is a
benchmark protocol identity validation, NOT a Generation A workflow
modification and NOT a Generation A native workflow capability.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchmark_common import BenchmarkError, gate, load_json

REQUIRED_FIELDS: tuple[str, ...] = (
    "baseRefName",
    "baseRefOid",
    "headRefName",
    "headRefOid",
    "isDraft",
)


def validate_pr_identity(
    pr: dict[str, Any],
    *,
    expected_base_branch: str,
    expected_base_sha: str,
    expected_head_branch: str,
    expected_head_sha: str,
    number: int | None = None,
) -> dict[str, Any]:
    """Validate a PR identity document; returns a gate report (fail closed)."""
    if not isinstance(pr, dict):
        raise BenchmarkError("PR identity document is not an object (fail closed)")
    missing = [field for field in REQUIRED_FIELDS if field not in pr]
    if missing:
        raise BenchmarkError(
            f"PR identity document missing required fields: {missing} (fail closed)"
        )

    checks: list[dict[str, Any]] = [
        gate(
            "base_ref_name",
            "pass" if pr["baseRefName"] == expected_base_branch else "fail",
            None
            if pr["baseRefName"] == expected_base_branch
            else f"expected {expected_base_branch!r}, observed {pr['baseRefName']!r}",
        ),
        gate(
            "base_ref_oid",
            "pass" if pr["baseRefOid"] == expected_base_sha else "fail",
            None
            if pr["baseRefOid"] == expected_base_sha
            else f"expected {expected_base_sha}, observed {pr['baseRefOid']}",
        ),
        gate(
            "head_ref_name",
            "pass" if pr["headRefName"] == expected_head_branch else "fail",
            None
            if pr["headRefName"] == expected_head_branch
            else f"expected {expected_head_branch!r}, observed {pr['headRefName']!r}",
        ),
        gate(
            "head_ref_oid",
            "pass" if pr["headRefOid"] == expected_head_sha else "fail",
            None
            if pr["headRefOid"] == expected_head_sha
            else f"expected {expected_head_sha}, observed {pr['headRefOid']}",
        ),
        gate("is_draft_false", "pass" if pr["isDraft"] is False else "fail"),
    ]
    if number is not None:
        checks.append(
            gate(
                "pr_number",
                "pass" if pr.get("number") == number else "fail",
                None
                if pr.get("number") == number
                else f"expected {number}, observed {pr.get('number')}",
            )
        )

    failed = [check for check in checks if check["status"] == "fail"]
    return {
        "protocol_identity": "task-65-round-2-v2",
        "verdict": "IDENTITY INVALID (fail closed)" if failed else "IDENTITY VALID",
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Conductor-side PR identity validation (Arm A equivalent of "
        "pr_resolve.py; uniform for all arms)."
    )
    parser.add_argument("--pr-json", required=True, help="gh pr view --json output")
    parser.add_argument("--expected-base-branch", required=True)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--expected-head-branch", required=True)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--expected-pr-number", type=int)
    parser.add_argument("--out", help="optional output path")
    args = parser.parse_args(argv)

    try:
        pr = load_json(Path(args.pr_json))
        report = validate_pr_identity(
            pr,
            expected_base_branch=args.expected_base_branch,
            expected_base_sha=args.expected_base_sha,
            expected_head_branch=args.expected_head_branch,
            expected_head_sha=args.expected_head_sha,
            number=args.expected_pr_number,
        )
    except BenchmarkError as exc:
        print(f"BENCHMARK IDENTITY VALIDATOR FAIL CLOSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if report["verdict"] == "IDENTITY VALID" else 2


if __name__ == "__main__":
    sys.exit(main())
