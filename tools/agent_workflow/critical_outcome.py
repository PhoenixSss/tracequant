#!/usr/bin/env python3
"""Task Critical Outcome contract parsing and deterministic verification.

The Issue body owns the semantic contract.  Execution is intentionally bounded:
the contract may name one repository pytest node id, but it cannot inject an
arbitrary command line into the workflow runtime.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from workflow_common import CommandRunner, ProgressReporter, safe_text

_SECTION_HEADING: Final = re.compile(r"^###\s+Critical Outcome\s*$", re.IGNORECASE)
_NEXT_HEADING: Final = re.compile(r"^#{1,6}\s+")
_FIELD_LINE: Final = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\*\*)?"
    r"(?P<key>Caller|Capability|Observable result|Verification test)"
    r"(?:\*\*)?\s*:\s*(?:\*\*)?\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_NODE_ID: Final = re.compile(
    r"^tests/(?:[A-Za-z0-9_.-]+/)*test_[A-Za-z0-9_.-]+\.py::test_[A-Za-z0-9_]+$"
)
_REQUIRED_KEYS: Final = (
    "caller",
    "capability",
    "observable_result",
    "verification_test",
)


class CriticalOutcomeError(ValueError):
    """The Task Critical Outcome contract is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class CriticalOutcomeContract:
    caller: str
    capability: str
    observable_result: str
    verification_test: str

    def to_dict(self) -> dict[str, str]:
        return {
            "caller": self.caller,
            "capability": self.capability,
            "observable_result": self.observable_result,
            "verification_test": self.verification_test,
        }


@dataclass(frozen=True)
class CriticalOutcomeResult:
    status: str
    contract: CriticalOutcomeContract
    exit_code: int
    summary: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "contract": self.contract.to_dict(),
            "exit_code": self.exit_code,
            "summary": self.summary,
        }


def _normalize_key(value: str) -> str:
    return value.casefold().replace(" ", "_")


def parse_critical_outcome(body: str | None) -> CriticalOutcomeContract:
    """Parse the single required ``### Critical Outcome`` section.

    The section uses four one-line fields so the semantic contract remains
    human-readable while the verification target stays machine-checkable.
    """
    if not isinstance(body, str) or not body.strip():
        raise CriticalOutcomeError("Task body is unavailable")

    lines = body.splitlines()
    section_starts = [
        index
        for index, line in enumerate(lines)
        if _SECTION_HEADING.fullmatch(line.strip())
    ]
    if len(section_starts) != 1:
        raise CriticalOutcomeError(
            "Task body must contain exactly one '### Critical Outcome' section"
        )

    values: dict[str, str] = {}
    for raw in lines[section_starts[0] + 1 :]:
        if _NEXT_HEADING.match(raw.strip()):
            break
        match = _FIELD_LINE.fullmatch(raw)
        if match is None:
            continue
        key = _normalize_key(match.group("key"))
        if key in values:
            raise CriticalOutcomeError(f"Critical Outcome field is duplicated: {key}")
        value = match.group("value").strip().strip("`").strip()
        if not value:
            raise CriticalOutcomeError(f"Critical Outcome field is empty: {key}")
        values[key] = value

    missing = [key for key in _REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise CriticalOutcomeError(
            "Critical Outcome is missing required fields: " + ", ".join(missing)
        )

    verification_test = values["verification_test"]
    if _NODE_ID.fullmatch(verification_test) is None:
        raise CriticalOutcomeError(
            "Verification test must be one pytest node id under tests/: "
            "tests/.../test_*.py::test_*"
        )

    return CriticalOutcomeContract(
        caller=values["caller"],
        capability=values["capability"],
        observable_result=values["observable_result"],
        verification_test=verification_test,
    )


def critical_outcome_snapshot(body: str | None) -> dict[str, Any]:
    """Return a compact structured contract without exposing the full Issue body."""
    try:
        contract = parse_critical_outcome(body)
    except CriticalOutcomeError as exc:
        return {"status": "invalid", "detail": str(exc)}
    return {"status": "valid", "contract": contract.to_dict()}


def contract_from_snapshot(value: Any) -> CriticalOutcomeContract:
    if not isinstance(value, Mapping) or value.get("status") != "valid":
        detail = value.get("detail") if isinstance(value, Mapping) else None
        raise CriticalOutcomeError(
            str(detail or "Critical Outcome contract is invalid")
        )
    contract = value.get("contract")
    if not isinstance(contract, Mapping):
        raise CriticalOutcomeError("Critical Outcome contract payload is unavailable")
    data = {key: contract.get(key) for key in _REQUIRED_KEYS}
    if not all(isinstance(item, str) and item for item in data.values()):
        raise CriticalOutcomeError("Critical Outcome contract payload is malformed")
    verification_test = str(data["verification_test"])
    if _NODE_ID.fullmatch(verification_test) is None:
        raise CriticalOutcomeError("Critical Outcome verification target is unsafe")
    return CriticalOutcomeContract(
        caller=str(data["caller"]),
        capability=str(data["capability"]),
        observable_result=str(data["observable_result"]),
        verification_test=verification_test,
    )


def verify_critical_outcome(
    repo_root: Path,
    runner: CommandRunner,
    contract: CriticalOutcomeContract,
    *,
    progress: ProgressReporter | None = None,
) -> CriticalOutcomeResult:
    """Run the bounded Task-specific Critical Outcome verifier."""
    file_part = contract.verification_test.split("::", 1)[0]
    target = (repo_root / file_part).resolve()
    try:
        target.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise CriticalOutcomeError(
            "Critical Outcome target escapes repository root"
        ) from exc
    if not target.is_file():
        raise CriticalOutcomeError(
            f"Critical Outcome verification test does not exist: {file_part}"
        )

    reporter = progress or ProgressReporter("critical-outcome")
    reporter.started("critical-outcome")
    try:
        result = runner.run(
            [
                "uv",
                "run",
                "--frozen",
                "pytest",
                "-q",
                contract.verification_test,
            ],
            command_id="lck-critical-outcome",
            validation=True,
            progress=lambda: reporter.heartbeat(
                "critical-outcome", command_id="lck-critical-outcome"
            ),
        )
    except BaseException:
        reporter.failed("critical-outcome")
        raise
    if result.returncode != 0:
        reporter.failed("critical-outcome")
    else:
        reporter.completed("critical-outcome")
    combined = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    summary = safe_text(combined[-1], limit=240) if combined else None
    return CriticalOutcomeResult(
        status="pass" if result.returncode == 0 else "fail",
        contract=contract,
        exit_code=result.returncode,
        summary=summary,
    )
