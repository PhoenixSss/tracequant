"""Bounded official DataTester entry for Stage 4 Binance Demo market data."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from nautilus_trader.adapters.binance import (
    BinanceDataClientConfig,
    BinanceDataClientFactory,
    BinanceEnvironment,
    BinanceInstrumentProviderConfig,
    BinanceProductType,
)
from nautilus_trader.common import Environment
from nautilus_trader.live import LiveNode
from nautilus_trader.model import (
    ClientId,
    CryptoPerpetual,
    InstrumentId,
    QuoteTick,
    TraderId,
    TradeTick,
)
from nautilus_trader.testkit import DataTesterConfig

from tracequant.integrations.nautilus import stage4_demo
from tracequant.integrations.nautilus.stage4_demo import (
    DEADLINE_SECONDS,
    DEMO_ENVIRONMENT,
    DEMO_INSTRUMENT_ID,
    NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
    NAUTILUS_DISTRIBUTION,
    NAUTILUS_UPSTREAM_COMMIT,
    NAUTILUS_VERSION,
    DeadlinePhase,
    DemoConfig,
    FrozenDemoConfig,
    RuntimeIdentity,
    capture_runtime_identity,
    freeze_demo_config,
    start_queue_deadline,
)
from tracequant.integrations.nautilus.stage4_demo_evidence import (
    EVIDENCE_SCHEMA,
    DemoEvidenceScenario,
    EvidenceClassification,
    canonical_stage4_demo_evidence_json,
    classify_stage4_observation,
    finalize_stage4_demo_evidence,
    stage4_demo_partition_path,
)

DATA_TESTER_BUILTIN: Final = "DataTester"
DATA_CLIENT_NAME: Final = "BINANCE"
DATA_TESTER_NODE_NAME: Final = "TRACEQUANT-STAGE4-DATA-TESTER"
DATA_TESTER_TRADER_ID: Final = "TRACEQUANT-001"
DATA_EVIDENCE_FILENAME: Final = "evidence.json"
_FUTURE_TOLERANCE_NS: Final = 1_000_000_000
_MAX_EVENT_AGE_NS: Final = 5_000_000_000
_POLL_INTERVAL_SECONDS: Final = 0.05
_DATA_DEADLINE_PHASES: Final = (
    DeadlinePhase.CONNECT,
    DeadlinePhase.READY,
    DeadlinePhase.DATA_OBSERVATION,
)


class Stage4DemoDataError(RuntimeError):
    """Raised when the bounded DataTester run cannot produce valid evidence."""


@dataclass(frozen=True)
class Stage4DemoDataPlan:
    """Closed plan containing only the approved official data-side components."""

    repository_root: Path
    runtime: RuntimeIdentity
    frozen_config: FrozenDemoConfig
    batch_id: str
    evidence_partition: Path
    instrument_id: InstrumentId
    data_client_config: BinanceDataClientConfig
    tester_config: DataTesterConfig
    builtin_actor: str
    deadline_seconds: tuple[tuple[DeadlinePhase, int], ...]


@dataclass(frozen=True)
class Stage4DemoDataObservation:
    """Nautilus-owned facts collected from one fresh DataTester node."""

    instrument: CryptoPerpetual
    quotes: tuple[QuoteTick, ...]
    trades: tuple[TradeTick, ...]
    quote_count: int
    trade_count: int
    started_at_ns: int
    ended_at_ns: int
    observed_at_ns: int


@dataclass(frozen=True)
class Stage4DemoDataFailure:
    """Bounded failure values already owned by EvidenceV1."""

    code: str
    phase: str
    diagnostic_codes: tuple[str, ...]


@dataclass(frozen=True)
class Stage4DemoDataOutcome:
    """A finalized EvidenceV1 record in its fresh external partition."""

    evidence_partition: Path
    evidence_path: Path
    evidence: dict[str, object]


def build_stage4_demo_data_plan(
    *,
    repository_root: Path,
    expected_runtime: RuntimeIdentity,
    evidence_root: Path,
    batch_id: str,
) -> Stage4DemoDataPlan:
    """Validate offline identity and build the singular rc4 DataTester plan."""
    root = Path(repository_root).resolve()
    observed_runtime = capture_runtime_identity(root)
    if observed_runtime != expected_runtime:
        raise Stage4DemoDataError("runtime identity drifted before DataTester planning")

    frozen_config = freeze_demo_config(DemoConfig())
    partition = stage4_demo_partition_path(
        repository_root=root,
        evidence_root=evidence_root,
        batch_id=batch_id,
        scenario=DemoEvidenceScenario.DATA_TESTER,
    )
    instrument_id = InstrumentId.from_str(DEMO_INSTRUMENT_ID)
    provider_config = BinanceInstrumentProviderConfig(
        load_all=False,
        load_ids=[DEMO_INSTRUMENT_ID],
    )
    data_client_config = BinanceDataClientConfig(
        product_type=BinanceProductType.USD_M,
        environment=BinanceEnvironment.DEMO,
        base_url_http=None,
        base_url_ws=None,
        api_key=None,
        api_secret=None,
        instrument_provider=provider_config,
    )
    tester_config = DataTesterConfig(
        client_id=ClientId.from_str(DATA_CLIENT_NAME),
        instrument_ids=[instrument_id],
        subscribe_quotes=True,
        subscribe_trades=True,
        subscribe_instrument=True,
        can_unsubscribe=True,
        log_data=False,
        log_events=False,
        log_commands=False,
    )
    return Stage4DemoDataPlan(
        repository_root=root,
        runtime=observed_runtime,
        frozen_config=frozen_config,
        batch_id=batch_id,
        evidence_partition=partition,
        instrument_id=instrument_id,
        data_client_config=data_client_config,
        tester_config=tester_config,
        builtin_actor=DATA_TESTER_BUILTIN,
        deadline_seconds=tuple(
            (phase, _deadline_seconds(phase)) for phase in _DATA_DEADLINE_PHASES
        ),
    )


def build_stage4_demo_data_evidence(
    plan: Stage4DemoDataPlan,
    observation: Stage4DemoDataObservation,
    *,
    failure: Stage4DemoDataFailure | None = None,
) -> dict[str, object]:
    """Adapt public DataTester observations into the frozen EvidenceV1 schema."""
    instrument = _instrument_payload(observation.instrument)
    timestamp_valid = _timestamps_are_valid(observation)
    counts_valid = (
        observation.quote_count >= len(observation.quotes) > 0
        and observation.trade_count >= len(observation.trades) > 0
    )
    identities_valid = _stream_identities_are_valid(observation)
    observations_valid = timestamp_valid and counts_valid and identities_valid

    resolved_failure = failure
    if resolved_failure is None and not observations_valid:
        if not counts_valid:
            diagnostics = []
            if not observation.quotes:
                diagnostics.append("NO_QUOTES")
            if not observation.trades:
                diagnostics.append("NO_TRADES")
            resolved_failure = Stage4DemoDataFailure(
                code="DATA_TIMEOUT",
                phase="data",
                diagnostic_codes=tuple(diagnostics or ["FAILED"]),
            )
        else:
            resolved_failure = Stage4DemoDataFailure(
                code="DATA_INVALID",
                phase="data",
                diagnostic_codes=("FAILED",),
            )

    classification = classify_stage4_observation(
        applicable=True,
        observed=counts_valid,
        conflicting=counts_valid and not observations_valid,
    ).value
    result = "PASS" if resolved_failure is None else "FAIL"
    terminal_state = "COMPLETE" if resolved_failure is None else "HALTED"
    payload: dict[str, object] = {
        "schema": EVIDENCE_SCHEMA,
        "source_commit": plan.runtime.source_commit,
        "dependency_lock_sha256": plan.runtime.dependency_lock_sha256,
        "runtime": {
            "distribution": NAUTILUS_DISTRIBUTION,
            "version": NAUTILUS_VERSION,
            "upstream_commit": NAUTILUS_UPSTREAM_COMMIT,
            "wheel_sha256": NAUTILUS_CP313_LINUX_X86_64_WHEEL_SHA256,
        },
        "environment": DEMO_ENVIRONMENT,
        "config_digest": plan.frozen_config.config_digest,
        "batch_id": plan.batch_id,
        "instrument": instrument,
        "scenario": DemoEvidenceScenario.DATA_TESTER.value,
        "started_at": _utc_timestamp(observation.started_at_ns),
        "ended_at": _utc_timestamp(observation.ended_at_ns),
        "result": result,
        "terminal_state": terminal_state,
        "observations": {
            "market_data": {
                "classification": classification,
                "quote_count": observation.quote_count,
                "trade_count": observation.trade_count,
                "timestamp_valid": timestamp_valid,
            },
            **_neutral_execution_observations(),
        },
        "cleanup": {
            "classification": EvidenceClassification.NOT_APPLICABLE.value,
            "active_order_count": 0,
            "pending_order_count": 0,
            "open_position_count": 0,
            "unresolved_unknown_count": 0,
            "final_net_quantity": "0",
        },
        "failure": (
            None
            if resolved_failure is None
            else {
                "code": resolved_failure.code,
                "phase": resolved_failure.phase,
                "diagnostic_codes": list(resolved_failure.diagnostic_codes),
            }
        ),
    }
    return finalize_stage4_demo_evidence(payload)


def write_stage4_demo_data_evidence(
    plan: Stage4DemoDataPlan,
    evidence: dict[str, object],
) -> Stage4DemoDataOutcome:
    """Write one validated record into a newly created external partition."""
    if plan.evidence_partition.exists():
        raise Stage4DemoDataError("DataTester evidence partition already exists")
    rendered = canonical_stage4_demo_evidence_json(evidence)
    plan.evidence_partition.parent.mkdir(parents=True, exist_ok=True)
    try:
        plan.evidence_partition.mkdir()
    except FileExistsError as exc:
        raise Stage4DemoDataError(
            "DataTester evidence partition already exists"
        ) from exc
    evidence_path = plan.evidence_partition / DATA_EVIDENCE_FILENAME
    try:
        with evidence_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.write("\n")
    except Exception:
        evidence_path.unlink(missing_ok=True)
        plan.evidence_partition.rmdir()
        raise
    return Stage4DemoDataOutcome(
        evidence_partition=plan.evidence_partition,
        evidence_path=evidence_path,
        evidence=evidence,
    )


def run_stage4_demo_data(
    *,
    repository_root: Path,
    expected_runtime: RuntimeIdentity,
    evidence_root: Path,
    batch_id: str,
) -> Stage4DemoDataOutcome:
    """Run the official DataTester once under the frozen bounded plan."""
    plan = build_stage4_demo_data_plan(
        repository_root=repository_root,
        expected_runtime=expected_runtime,
        evidence_root=evidence_root,
        batch_id=batch_id,
    )
    if plan.evidence_partition.exists():
        raise Stage4DemoDataError("DataTester evidence partition already exists")
    observation = asyncio.run(_observe_stage4_demo_data(plan))
    evidence = build_stage4_demo_data_evidence(plan, observation)
    return write_stage4_demo_data_evidence(plan, evidence)


async def _observe_stage4_demo_data(
    plan: Stage4DemoDataPlan,
) -> Stage4DemoDataObservation:
    node = _build_data_tester_node(plan)
    started_at_ns = time.time_ns()
    run_task: asyncio.Task[None] | None = None
    try:
        connect_deadline = start_queue_deadline(DeadlinePhase.CONNECT)
        run_task = asyncio.create_task(node.run_async())
        await _wait_until(
            lambda: node.is_running,
            deadline=connect_deadline,
            run_task=run_task,
            failure="DataTester did not connect before the fixed deadline",
        )

        ready_deadline = start_queue_deadline(DeadlinePhase.READY)
        await _wait_until(
            lambda: node.cache.instrument(plan.instrument_id) is not None,
            deadline=ready_deadline,
            run_task=run_task,
            failure="DataTester instrument was not ready before the fixed deadline",
        )

        data_deadline = start_queue_deadline(DeadlinePhase.DATA_OBSERVATION)
        await _wait_until(
            lambda: (
                node.cache.quote_count(plan.instrument_id) > 0
                and node.cache.trade_count(plan.instrument_id) > 0
            ),
            deadline=data_deadline,
            run_task=run_task,
            failure="DataTester did not observe quote and trade before the fixed deadline",
        )
        instrument = node.cache.instrument(plan.instrument_id)
        quote = node.cache.quote(plan.instrument_id)
        trade = node.cache.trade(plan.instrument_id)
        if (
            not isinstance(instrument, CryptoPerpetual)
            or not isinstance(quote, QuoteTick)
            or not isinstance(trade, TradeTick)
        ):
            raise Stage4DemoDataError("DataTester public cache facts are incomplete")
        return Stage4DemoDataObservation(
            instrument=instrument,
            quotes=(quote,),
            trades=(trade,),
            quote_count=node.cache.quote_count(plan.instrument_id),
            trade_count=node.cache.trade_count(plan.instrument_id),
            started_at_ns=started_at_ns,
            ended_at_ns=time.time_ns(),
            observed_at_ns=time.time_ns(),
        )
    finally:
        try:
            node.stop()
            if run_task is not None:
                cleanup_seconds = _deadline_seconds(DeadlinePhase.CLEANUP)
                await asyncio.wait_for(run_task, timeout=cleanup_seconds)
        except TimeoutError as exc:
            raise Stage4DemoDataError(
                "DataTester did not stop before the fixed cleanup deadline"
            ) from exc
        except Stage4DemoDataError:
            raise
        except Exception as exc:
            raise Stage4DemoDataError("DataTester stopped with an error") from exc
        finally:
            node.dispose()


def _build_data_tester_node(plan: Stage4DemoDataPlan) -> LiveNode:
    builder = (
        LiveNode.builder(
            DATA_TESTER_NODE_NAME,
            TraderId.from_str(DATA_TESTER_TRADER_ID),
            Environment.LIVE,
        )
        .with_timeout_connection(_deadline_seconds(DeadlinePhase.CONNECT))
        .with_timeout_disconnection_secs(_deadline_seconds(DeadlinePhase.CLEANUP))
        .with_delay_post_stop_secs(0)
        .with_delay_shutdown_secs(_deadline_seconds(DeadlinePhase.CLEANUP))
        .add_data_client(
            DATA_CLIENT_NAME,
            BinanceDataClientFactory(),
            plan.data_client_config,
        )
    )
    node = builder.build()
    node.add_builtin_actor(plan.builtin_actor, plan.tester_config)
    return node


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    deadline: stage4_demo.DemoDeadline,
    run_task: asyncio.Task[None],
    failure: str,
) -> None:
    while not predicate():
        if run_task.done():
            try:
                run_task.result()
            except Exception as exc:
                raise Stage4DemoDataError(failure) from exc
            raise Stage4DemoDataError(failure)
        if deadline.expired():
            raise Stage4DemoDataError(failure)
        await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, deadline.remaining_seconds()))


def _instrument_payload(instrument: CryptoPerpetual) -> dict[str, object]:
    if str(instrument.id) != DEMO_INSTRUMENT_ID:
        raise Stage4DemoDataError(
            "DataTester returned an instrument outside the allowlist"
        )
    minimum = instrument.min_quantity
    if minimum is None:
        raise Stage4DemoDataError("DataTester instrument minimum quantity is missing")
    return {
        "id": str(instrument.id),
        "price_precision": instrument.price_precision,
        "price_increment": str(instrument.price_increment.as_decimal()),
        "size_precision": instrument.size_precision,
        "size_increment": str(instrument.size_increment.as_decimal()),
        "minimum_quantity": str(minimum.as_decimal()),
        "maximum_quantity": (
            None
            if instrument.max_quantity is None
            else str(instrument.max_quantity.as_decimal())
        ),
        "minimum_notional": (
            None
            if instrument.min_notional is None
            else str(instrument.min_notional.as_decimal())
        ),
    }


def _stream_identities_are_valid(observation: Stage4DemoDataObservation) -> bool:
    quotes_valid = all(
        str(item.instrument_id) == DEMO_INSTRUMENT_ID for item in observation.quotes
    )
    trades_valid = all(
        str(item.instrument_id) == DEMO_INSTRUMENT_ID for item in observation.trades
    )
    return quotes_valid and trades_valid


def _timestamps_are_valid(observation: Stage4DemoDataObservation) -> bool:
    return _stream_timestamps_are_valid(
        tuple(item.ts_event for item in observation.quotes), observation.observed_at_ns
    ) and _stream_timestamps_are_valid(
        tuple(item.ts_event for item in observation.trades), observation.observed_at_ns
    )


def _stream_timestamps_are_valid(
    timestamps: Sequence[int],
    observed_at_ns: int,
) -> bool:
    previous: int | None = None
    for timestamp in timestamps:
        if (
            timestamp > observed_at_ns + _FUTURE_TOLERANCE_NS
            or observed_at_ns - timestamp > _MAX_EVENT_AGE_NS
            or (previous is not None and timestamp < previous)
        ):
            return False
        previous = timestamp
    return bool(timestamps)


def _neutral_execution_observations() -> dict[str, object]:
    not_applicable = EvidenceClassification.NOT_APPLICABLE.value
    return {
        "order": {
            "classification": not_applicable,
            "submitted": False,
            "accepted": False,
            "terminal": False,
            "active": False,
            "pending": False,
            "ambiguous": False,
        },
        "fill": {
            "classification": not_applicable,
            "complete": False,
            "partial": False,
            "late": False,
        },
        "position": {
            "classification": not_applicable,
            "open_count": 0,
            "final_net_quantity": "0",
        },
        "balance": {
            "classification": not_applicable,
            "before_observed": False,
            "after_observed": False,
            "explained_change": False,
        },
        "account_mode": {
            "classification": not_applicable,
            "operator_gate_confirmed": False,
            "canary_complete": False,
            "one_way_confirmed": False,
            "isolated_confirmed": False,
            "leverage_one_confirmed": False,
            "observed_initial_margin": None,
            "allowed_initial_margin_min": None,
            "allowed_initial_margin_max": None,
        },
    }


def _utc_timestamp(timestamp_ns: int) -> str:
    value = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _deadline_seconds(phase: DeadlinePhase) -> int:
    for candidate, seconds in DEADLINE_SECONDS:
        if candidate == phase:
            return seconds
    raise Stage4DemoDataError(f"unsupported Stage 4 deadline phase: {phase.value}")
