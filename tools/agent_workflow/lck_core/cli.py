from __future__ import annotations

import argparse
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from workflow_common import WorkflowToolError, print_json

from .closeout import CloseoutCompleter
from .delivery import DeliveryCompleter, DeliveryPreparer
from .models import LckStopError, LiveState, ResolutionStatus, ReviewStaleError
from .receipts import (
    AuditReceiptStore,
    _failure_fallback,
    _result_operation_id,
    _write_failure_receipt,
    _write_success_receipt,
)
from .remediation import (
    RemediationCompleter,
    RemediationNoChangeCompleter,
    RemediationPreparer,
)
from .review import MergePreflight, ReviewCompleter, ReviewPreparer
from .state import LiveStateResolver


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LCK v1 live state operations")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--repository")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("task", type=int)

    delivery = commands.add_parser("delivery")
    delivery_commands = delivery.add_subparsers(dest="delivery_command", required=True)
    prepare = delivery_commands.add_parser("prepare")
    prepare.add_argument("task", type=int)
    complete = delivery_commands.add_parser("complete")
    complete.add_argument("task", type=int)
    complete.add_argument("--commit-message", required=True)
    complete.add_argument("--summary", required=True)
    complete.add_argument("--risks", default="")

    review = commands.add_parser("review")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_prepare = review_commands.add_parser("prepare")
    review_prepare.add_argument("task", type=int)
    review_complete = review_commands.add_parser("complete")
    review_complete.add_argument("task", type=int)
    review_complete.add_argument("--review-id", required=True)
    review_complete.add_argument("--verdict", required=True, choices=("PASS", "FAIL"))
    review_complete.add_argument("--findings-file", type=Path)

    remediation = commands.add_parser("remediation")
    remediation_commands = remediation.add_subparsers(
        dest="remediation_command", required=True
    )
    remediation_prepare = remediation_commands.add_parser("prepare")
    remediation_prepare.add_argument("task", type=int)
    remediation_prepare.add_argument("--review-id", required=True)
    remediation_prepare.add_argument("--findings-file", type=Path)
    remediation_no_change = remediation_commands.add_parser("no-change")
    remediation_no_change.add_argument("task", type=int)
    remediation_no_change.add_argument("--review-id", required=True)
    remediation_no_change.add_argument("--summary", required=True)
    remediation_complete = remediation_commands.add_parser("complete")
    remediation_complete.add_argument("task", type=int)
    remediation_complete.add_argument("--review-id", required=True)
    remediation_complete.add_argument("--commit-message", required=True)
    remediation_complete.add_argument("--summary", required=True)
    remediation_complete.add_argument("--risks", default="")

    merge = commands.add_parser("merge")
    merge_commands = merge.add_subparsers(dest="merge_command", required=True)
    merge_preflight = merge_commands.add_parser("preflight")
    merge_preflight.add_argument("task", type=int)

    merge_preflight_alias = commands.add_parser("merge-preflight")
    merge_preflight_alias.add_argument("task", type=int)

    closeout = commands.add_parser("closeout")
    closeout.add_argument("task", type=int)
    return parser


def _cli_operation(args: argparse.Namespace) -> str:
    if args.command == "status":
        return "status"
    if args.command == "delivery":
        return f"delivery-{args.delivery_command}"
    if args.command == "review":
        return f"review-{args.review_command}"
    if args.command == "remediation":
        return f"remediation-{args.remediation_command}"
    if args.command in {"merge", "merge-preflight"}:
        return "merge-preflight"
    if args.command == "closeout":
        return "closeout"
    return "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    resolver = LiveStateResolver(
        args.repo_root,
        repository=args.repository,
    )
    operation = _cli_operation(args)
    task_number = args.task
    operation_id = uuid.uuid4().hex
    receipt_store = AuditReceiptStore(resolver.repo_root)
    handler: Any = resolver

    def emit_success(value: Any) -> int:
        nonlocal operation_id
        operation_id = _result_operation_id(value, operation_id)
        print_json(
            _write_success_receipt(
                value,
                operation=operation,
                task_number=task_number,
                operation_id=operation_id,
                store=receipt_store,
            ),
            pretty=True,
        )
        if isinstance(value, LiveState) and value.status is ResolutionStatus.STOP:
            return 2
        return 0

    try:
        if args.command == "status":
            return emit_success(resolver.resolve(task_number))
        if args.command == "delivery" and args.delivery_command == "prepare":
            handler = DeliveryPreparer(resolver)
            return emit_success(handler.prepare(task_number))
        if args.command == "delivery" and args.delivery_command == "complete":
            handler = DeliveryCompleter(resolver)
            return emit_success(
                handler.complete(
                    task_number,
                    commit_message=args.commit_message,
                    summary=args.summary,
                    risks=args.risks,
                )
            )
        if args.command == "review" and args.review_command == "prepare":
            handler = ReviewPreparer(resolver)
            return emit_success(handler.prepare(task_number))
        if args.command == "review" and args.review_command == "complete":
            handler = ReviewCompleter(resolver)
            return emit_success(
                handler.complete(
                    task_number,
                    args.review_id,
                    verdict=args.verdict,
                    findings_file=args.findings_file,
                )
            )
        if args.command == "remediation" and args.remediation_command == "prepare":
            handler = RemediationPreparer(resolver)
            return emit_success(
                handler.prepare(
                    task_number,
                    args.review_id,
                    findings_file=args.findings_file,
                )
            )
        if args.command == "remediation" and args.remediation_command == "no-change":
            handler = RemediationNoChangeCompleter(resolver)
            return emit_success(
                handler.complete(
                    task_number,
                    args.review_id,
                    summary=args.summary,
                )
            )
        if args.command == "remediation" and args.remediation_command == "complete":
            handler = RemediationCompleter(resolver)
            return emit_success(
                handler.complete(
                    task_number,
                    args.review_id,
                    commit_message=args.commit_message,
                    summary=args.summary,
                    risks=args.risks,
                )
            )
        if (
            args.command == "merge" and args.merge_command == "preflight"
        ) or args.command == "merge-preflight":
            handler = MergePreflight(resolver)
            return emit_success(handler.run(task_number))
        if args.command == "closeout":
            handler = CloseoutCompleter(resolver)
            return emit_success(handler.complete(task_number))
        raise LckStopError("unsupported LCK command")
    except ReviewStaleError as exc:
        try:
            payload = _write_failure_receipt(
                operation=operation,
                task_number=task_number,
                operation_id=operation_id,
                status="stale",
                code=exc.code,
                error=str(exc),
                handler=handler,
                store=receipt_store,
            )
        except WorkflowToolError as receipt_error:
            payload = _failure_fallback(
                operation=operation,
                task_number=task_number,
                operation_id=operation_id,
                status="stale",
                code=exc.code,
                error=str(exc),
                receipt_error=receipt_error,
                store=receipt_store,
            )
        print_json(payload)
        return 3
    except WorkflowToolError as exc:
        try:
            payload = _write_failure_receipt(
                operation=operation,
                task_number=task_number,
                operation_id=operation_id,
                status="stop",
                code=getattr(exc, "code", None),
                error=str(exc),
                handler=handler,
                store=receipt_store,
            )
        except WorkflowToolError as receipt_error:
            payload = _failure_fallback(
                operation=operation,
                task_number=task_number,
                operation_id=operation_id,
                status="stop",
                code=getattr(exc, "code", None),
                error=str(exc),
                receipt_error=receipt_error,
                store=receipt_store,
            )
        print_json(payload)
        return 2
