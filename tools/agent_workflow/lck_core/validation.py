from __future__ import annotations

import shutil
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from workflow_common import (
    ProgressReporter,
    WorkflowToolError,
    atomic_write_json,
    is_sha,
    read_json_text,
    stderr_tail,
)
from workflow_evidence import _normalize_checks

from .models import (
    LCK_SCHEMA_VERSION,
    LckStopError,
    LiveState,
    OperationSnapshot,
    ResolutionStatus,
)
from .state import (
    LiveStateResolver,
    _required_check_contract,
    _required_check_contract_for_snapshot,
)


class FormalValidationGate:
    """Run the repository-owned deterministic Delivery validation plan."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver
        self.last_payload: dict[str, Any] | None = None

    def run(self, base_sha: str) -> dict[str, Any]:
        self.last_payload = None
        if not is_sha(base_sha):
            raise LckStopError("formal validation base SHA is unavailable")
        tool = (
            self.resolver.repo_root
            / "tools"
            / "agent_workflow"
            / "workflow_validation.py"
        )
        reporter = ProgressReporter("workflow-validation")
        reporter.started("formal-validation")
        try:
            result = self.resolver.runner.run(
                [
                    sys.executable,
                    str(tool),
                    "run",
                    "--repo-root",
                    str(self.resolver.repo_root),
                    "--phase",
                    "delivery",
                    "--base-sha",
                    base_sha,
                    "--include-skill-validators",
                    "--require-skill-validator",
                ],
                command_id="lck-formal-delivery-validation",
                validation=True,
                progress=lambda: reporter.heartbeat(
                    "formal-validation",
                    command_id="lck-formal-delivery-validation",
                ),
            )
            if not result.stdout.strip():
                raise LckStopError(
                    "formal Delivery validation produced no structured result: "
                    + (result.stderr.strip() or f"exit {result.returncode}")
                )
            try:
                payload = read_json_text(
                    result.stdout, field="lck-formal-delivery-validation"
                )
            except WorkflowToolError as exc:
                raise LckStopError(str(exc)) from exc
            if not isinstance(payload, dict):
                raise LckStopError("formal Delivery validation result is not an object")
            # Keep the structured result available to the owning phase before
            # applying the gate.  A failed validation is still the evidence
            # needed by the failure Receipt.
            self.last_payload = payload
            if result.returncode != 0 or payload.get("status") != "pass":
                raise LckStopError(
                    "formal Delivery validation failed: "
                    + str(payload.get("status") or result.returncode)
                )
        except BaseException:
            reporter.failed("formal-validation")
            raise
        reporter.completed("formal-validation")
        return payload


class ReviewValidationGate:
    """Run current Review validation inside the isolated reviewed clone."""

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.resolver = resolver

    def _persist_validation_artifacts(
        self,
        review_root: Path,
        payload: dict[str, Any],
        *,
        base_sha: str,
        head_sha: str,
    ) -> dict[str, Any]:
        raw_output_dir = payload.get("output_dir")
        if not isinstance(raw_output_dir, str) or not raw_output_dir:
            raise LckStopError(
                "formal Review validation did not provide an output directory"
            )
        output_dir = Path(raw_output_dir)
        if output_dir.is_absolute():
            raise LckStopError(
                "formal Review validation output directory must be relative"
            )
        review_root = review_root.resolve()
        source = (review_root / output_dir).resolve()
        try:
            source.relative_to(review_root)
        except ValueError as exc:
            raise LckStopError(
                "formal Review validation output escaped the Review clone"
            ) from exc
        if not source.is_dir():
            raise LckStopError(
                "formal Review validation output directory is unavailable"
            )

        durable_root = (
            self.resolver.repo_root / ".workflow.local" / "lck" / "review-validation"
        ).resolve()
        durable_root.mkdir(parents=True, exist_ok=True)
        destination = durable_root / f"lck-review-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, destination)
        except OSError as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise LckStopError(
                f"cannot preserve formal Review validation artifacts: {exc}"
            ) from exc

        durable_relative = destination.relative_to(
            self.resolver.repo_root.resolve()
        ).as_posix()
        preserved = dict(payload)
        preserved["output_dir"] = durable_relative
        preserved["evidence_path"] = durable_relative
        preserved["validated_base_sha"] = base_sha
        preserved["validated_head_sha"] = head_sha
        commands = preserved.get("commands")
        if isinstance(commands, list):
            for command in commands:
                if not isinstance(command, dict):
                    continue
                raw_log_path = command.get("log_path")
                if not isinstance(raw_log_path, str) or not raw_log_path:
                    continue
                log_path = Path(raw_log_path)
                try:
                    log_relative = log_path.relative_to(output_dir)
                except ValueError as exc:
                    raise LckStopError(
                        "formal Review validation log path escaped its output directory"
                    ) from exc
                command["log_path"] = (Path(durable_relative) / log_relative).as_posix()
        evidence_file = (
            Path(durable_relative) / "lck-review-validation-result.json"
        ).as_posix()
        preserved["evidence_file"] = evidence_file
        atomic_write_json(destination / "lck-review-validation-result.json", preserved)
        return preserved

    def _persist_unstructured_failure(
        self,
        result: Any,
        *,
        base_sha: str,
        head_sha: str,
    ) -> dict[str, Any]:
        durable_root = (
            self.resolver.repo_root / ".workflow.local" / "lck" / "review-validation"
        ).resolve()
        durable_root.mkdir(parents=True, exist_ok=True)
        destination = durable_root / f"lck-review-{uuid.uuid4().hex}"
        destination.mkdir()
        durable_relative = destination.relative_to(
            self.resolver.repo_root.resolve()
        ).as_posix()
        diagnostic = stderr_tail(result.stderr or result.stdout, limit=2000)
        payload: dict[str, Any] = {
            "schema_version": LCK_SCHEMA_VERSION,
            "operation": "workflow-validation",
            "phase": "review",
            "status": "fail",
            "base_sha": base_sha,
            "validated_base_sha": base_sha,
            "validated_head_sha": head_sha,
            "commands": [
                {
                    "command_id": result.command_id,
                    "status": "fail",
                    "exit_code": result.returncode,
                    "diagnostic": diagnostic,
                }
            ],
            "output_dir": durable_relative,
            "evidence_path": durable_relative,
        }
        payload["evidence_file"] = (
            Path(durable_relative) / "lck-review-validation-result.json"
        ).as_posix()
        atomic_write_json(destination / "lck-review-validation-result.json", payload)
        return payload

    def run(self, review_root: Path, base_sha: str, head_sha: str) -> dict[str, Any]:
        if not is_sha(base_sha):
            raise LckStopError("formal Review validation base SHA is unavailable")
        if not is_sha(head_sha):
            raise LckStopError("formal Review validation head SHA is unavailable")
        tool = review_root / "tools" / "agent_workflow" / "workflow_validation.py"
        if not tool.is_file():
            raise LckStopError("reviewed head does not contain workflow_validation.py")
        reporter = ProgressReporter("review-prepare")
        reporter.started("formal-validation")
        try:
            result = self.resolver.runner.run(
                [
                    sys.executable,
                    str(tool),
                    "run",
                    "--repo-root",
                    str(review_root),
                    "--phase",
                    "review",
                    "--base-sha",
                    base_sha,
                    "--include-skill-validators",
                    "--require-skill-validator",
                ],
                command_id="lck-formal-review-validation",
                cwd=review_root,
                validation=True,
                progress=lambda: reporter.heartbeat(
                    "formal-validation",
                    command_id="lck-formal-review-validation",
                ),
            )
            if not result.stdout.strip():
                payload = self._persist_unstructured_failure(
                    result, base_sha=base_sha, head_sha=head_sha
                )
            else:
                try:
                    parsed = read_json_text(
                        result.stdout, field="lck-formal-review-validation"
                    )
                except WorkflowToolError:
                    parsed = None
                if not isinstance(parsed, dict):
                    payload = self._persist_unstructured_failure(
                        result, base_sha=base_sha, head_sha=head_sha
                    )
                else:
                    candidate = dict(parsed)
                    if result.returncode != 0 and candidate.get("status") == "pass":
                        candidate["status"] = "fail"
                    try:
                        payload = self._persist_validation_artifacts(
                            review_root,
                            candidate,
                            base_sha=base_sha,
                            head_sha=head_sha,
                        )
                    except LckStopError:
                        if candidate.get("status") != "fail" and result.returncode == 0:
                            raise
                        payload = self._persist_unstructured_failure(
                            result, base_sha=base_sha, head_sha=head_sha
                        )
        except BaseException:
            reporter.failed()
            raise
        if payload.get("status") == "pass":
            reporter.completed("formal-validation")
        else:
            reporter.failed("formal-validation")
        return payload


class DeliveryChecksGate:
    """Observe or strictly evaluate PR checks from an operation snapshot/query.

    This gate never resolves lifecycle state and never polls.  Delivery and
    Review Prepare use the non-blocking observation path; Review Complete and
    Merge Preflight use the strict evaluation path.
    """

    PR_FIELDS: Final = (
        "number,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
        "statusCheckRollup,mergeable,url"
    )

    def __init__(self, resolver: LiveStateResolver) -> None:
        self.runner = resolver.runner
        self.last_result: dict[str, Any] | None = None

    def _remember_result(self, result: dict[str, Any]) -> None:
        self.last_result = dict(result)

    @staticmethod
    def _required_names(required: Mapping[str, Any]) -> set[str]:
        return set(_required_check_contract(required))

    @staticmethod
    def _observed_categories(checks: Mapping[str, Any]) -> dict[str, str]:
        bounded = checks.get("items")
        if not isinstance(bounded, Mapping):
            return {}
        items = bounded.get("items")
        if not isinstance(items, list):
            return {}
        values: dict[str, str] = {}
        for item in items:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            category = item.get("category")
            if isinstance(name, str) and isinstance(category, str):
                values[name] = category
        return values

    @staticmethod
    def _pr_identity_from_pr(pr: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "number": pr.get("number"),
            "head_sha": pr.get("headRefOid"),
            "base_sha": pr.get("baseRefOid"),
        }

    @staticmethod
    def _pr_identity(state: LiveState) -> dict[str, Any]:
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            return {}
        return DeliveryChecksGate._pr_identity_from_pr(pr)

    @classmethod
    def _evaluate_pr_checks(
        cls,
        pr: Mapping[str, Any],
        required: Mapping[str, Any] | None,
        *,
        require_success: bool = True,
        capture: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        checks = _normalize_checks(pr.get("statusCheckRollup"))
        required_names = (
            cls._required_names(required) if required is not None else set()
        )
        config = required.get("configuration") if required is not None else None
        observed = cls._observed_categories(checks)
        try:
            failed = int(checks.get("failed", 0) or 0)
            unknown = int(checks.get("skipped_or_unknown", 0) or 0)
            pending = int(checks.get("pending", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise LckStopError("PR check summary is malformed") from exc

        failed_required = {
            name
            for name in required_names
            if observed.get(name) not in {None, "success"}
        }
        missing = required_names - set(observed)
        if failed > 0 or failed_required:
            check_state = "failed"
        elif unknown > 0:
            check_state = "unknown"
        elif pending > 0:
            check_state = "pending"
        elif missing:
            check_state = "missing"
        else:
            check_state = "pass"

        result = {
            "status": "pass" if require_success else "observed",
            "configuration": config,
            "required": sorted(required_names),
            "pr": cls._pr_identity_from_pr(pr),
            "checks": checks,
        }
        if not require_success:
            result.update(
                {
                    "gate": "non-blocking",
                    "check_state": check_state,
                }
            )
        if capture is not None:
            failed_result = dict(result)
            if require_success:
                failed_result["check_state"] = check_state
                if check_state != "pass":
                    failed_result["status"] = "fail"
            capture(failed_result)
        if require_success:
            if failed > 0 or unknown > 0:
                raise LckStopError("PR checks failed, cancelled, skipped, or unknown")
            if pending > 0:
                raise LckStopError(
                    "PR checks are pending; strict check gate is not satisfied"
                )
            if failed_required:
                raise LckStopError(
                    "required PR checks are not successful: "
                    + ", ".join(sorted(failed_required))
                )
            if missing:
                raise LckStopError(
                    "required PR checks are not present: " + ", ".join(sorted(missing))
                )
        return result

    def evaluate(self, snapshot: OperationSnapshot) -> dict[str, Any]:
        self.last_result = None
        state = snapshot.state
        if state.status is not ResolutionStatus.RESOLVED:
            raise LckStopError(
                "cannot evaluate checks from unresolved operation snapshot: "
                + "; ".join(state.stop_reasons)
            )
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            raise LckStopError("PR checks require one OPEN PR in operation snapshot")
        required = snapshot.required_checks
        if not isinstance(required, Mapping):
            raise LckStopError("required PR check configuration was not acquired")
        _required_check_contract_for_snapshot(snapshot)
        return self._evaluate_pr_checks(
            pr,
            required,
            require_success=True,
            capture=self._remember_result,
        )

    def observe(self, snapshot: OperationSnapshot) -> dict[str, Any]:
        """Return a bounded check observation without requiring CI success."""
        self.last_result = None
        state = snapshot.state
        if state.status is not ResolutionStatus.RESOLVED:
            raise LckStopError(
                "cannot observe checks from unresolved operation snapshot: "
                + "; ".join(state.stop_reasons)
            )
        pr = state.open_pr
        if not isinstance(pr, Mapping):
            raise LckStopError("PR checks require one OPEN PR in operation snapshot")
        return self._evaluate_pr_checks(
            pr,
            None,
            require_success=False,
            capture=self._remember_result,
        )

    def query_exact_pr(
        self,
        repository: str,
        pr_number: int,
        *,
        expected_head_sha: str,
        expected_base_sha: str,
        required_checks: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Targeted strict query for a PR created/updated by this operation."""
        return self._query_exact_pr(
            repository,
            pr_number,
            expected_head_sha=expected_head_sha,
            expected_base_sha=expected_base_sha,
            required_checks=required_checks,
            require_success=True,
        )

    def _query_exact_pr(
        self,
        repository: str,
        pr_number: int,
        *,
        expected_head_sha: str,
        expected_base_sha: str,
        required_checks: Mapping[str, Any] | None,
        require_success: bool,
    ) -> dict[str, Any]:
        self.last_result = None
        result = self.runner.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repository,
                "--json",
                self.PR_FIELDS,
            ],
            command_id="lck-checks-exact-pr",
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise LckStopError(
                "cannot query the exact PR after the PR effect: "
                + (result.stderr.strip() or f"exit {result.returncode}")
            )
        value = read_json_text(result.stdout, field="lck-checks-exact-pr")
        if not isinstance(value, Mapping):
            raise LckStopError("exact PR check query returned a non-object")
        if (
            value.get("number") != pr_number
            or str(value.get("state", "")).upper() != "OPEN"
            or value.get("isDraft") is not False
            or value.get("headRefOid") != expected_head_sha
            or value.get("baseRefOid") != expected_base_sha
        ):
            raise LckStopError("exact PR identity changed after the PR effect")
        return self._evaluate_pr_checks(
            value,
            required_checks,
            require_success=require_success,
            capture=self._remember_result,
        )

    def observe_exact_pr(
        self,
        repository: str,
        pr_number: int,
        *,
        expected_head_sha: str,
        expected_base_sha: str,
    ) -> dict[str, Any]:
        """Observe a post-effect PR without making CI success a Delivery gate."""
        return self._query_exact_pr(
            repository,
            pr_number,
            expected_head_sha=expected_head_sha,
            expected_base_sha=expected_base_sha,
            required_checks=None,
            require_success=False,
        )

    @staticmethod
    def _checks_postcondition(
        state: LiveState,
        checks_result: Mapping[str, Any],
    ) -> bool:
        """Compare a snapshot PR/check fact to a previously completed check gate."""
        if checks_result.get("status") != "pass":
            return False
        current_pr = state.open_pr
        gated_pr = checks_result.get("pr")
        if not isinstance(current_pr, Mapping) or not isinstance(gated_pr, Mapping):
            return False
        if DeliveryChecksGate._pr_identity_from_pr(current_pr) != {
            "number": gated_pr.get("number"),
            "head_sha": gated_pr.get("head_sha"),
            "base_sha": gated_pr.get("base_sha"),
        }:
            return False
        current_checks = _normalize_checks(current_pr.get("statusCheckRollup"))
        expected_checks = checks_result.get("checks")
        if not isinstance(expected_checks, Mapping):
            return False
        return current_checks == expected_checks
