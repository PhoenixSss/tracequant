from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Final, cast

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LoggerConfig, LogLevel
from nautilus_trader.model import (
    AccountType,
    AggressorSide,
    Bar,
    CryptoPerpetual,
    Currency,
    FundingRateUpdate,
    InstrumentId,
    MarkPriceUpdate,
    Money,
    OmsType,
    OrderFilled,
    Quantity,
    TradeId,
    TraderId,
    TradeTick,
    Venue,
)
from nautilus_trader.persistence import ParquetDataCatalog

from tracequant.integrations import nautilus as nautilus_integration
from tracequant.integrations.nautilus import (
    EXPECTED_VERSION,
    UPSTREAM_RELEASE_IDENTITY,
    distribution_version,
    stage2_artifact,
)
from tracequant.integrations.nautilus import stage2_btceth as nautilus_stage2_btceth
from tracequant.integrations.nautilus.strategies import (
    stage3_momentum as momentum_strategy,
)
from tracequant.integrations.nautilus.strategies.stage3_momentum import (
    BASE_MAKER_FEE,
    BASE_TAKER_FEE,
    MOMENTUM_TARGET_NOTIONAL_USDT,
    MOMENTUM_THRESHOLD,
    STAGE3_BAR_OPEN_TRADE_ID_PREFIX,
    Stage3MomentumError,
    Stage3MomentumParameters,
    Stage3MomentumStrategy,
    momentum_signal,
    target_quantity,
    unsettled_orders,
)
from tracequant.research import source_schema as research_source_schema
from tracequant.research import stage3_features
from tracequant.research import views as research_views
from tracequant.research.stage3_features import (
    FEATURE_LOOKBACK_HOURS,
    FEATURE_SCHEMA_DIGEST,
    FeatureObservation,
    Stage3Config,
    Stage3DataError,
    load_accepted_feature_window,
    load_stage3_config,
)
from tracequant.research.views import load_funding
from tracequant.source_data import stage2_btceth as source_stage2_btceth
from tracequant.source_data.stage2_btceth import (
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    datetime_to_nanos,
    parse_utc,
    stage2_bar_type_str,
)

STAGE3_MOMENTUM_SCHEMA: Final = "tracequant-stage3-momentum-v1"
STAGE3_REQUIREMENTS_RELATIVE_PATH: Final = (
    "docs/product/stage-3-strategy-and-model-requirements.md"
)
STAGE3_REQUIREMENTS_BASE_SHA: Final = "cb863767ca7d2ef48b1e6ce3dd5c85d941b7a5a4"
STAGE3_REQUIREMENTS_BLOB_SHA: Final = "65a9f4fc5d6c121f93f39a5e7f5095b16e884eb5"
STAGE3_MOMENTUM_START: Final = "2022-01-01T00:00:00Z"
STAGE3_MOMENTUM_END: Final = "2023-01-01T00:00:00Z"


class _FixedDecimals:
    STARTING_USDT: Final = Decimal("100000")
    DEFAULT_LEVERAGE: Final = Decimal("1")


STAGE3_STARTING_USDT: Final = _FixedDecimals.STARTING_USDT
STAGE3_DEFAULT_LEVERAGE: Final = _FixedDecimals.DEFAULT_LEVERAGE
STAGE3_TRADER_ID: Final = "TRACEQUANT-STAGE3-001"
STAGE3_VENUE: Final = "BINANCE"
OFFLINE_BACKTEST_ONLY: Final = True
LIVE_NOT_APPROVED: Final = True


@dataclass(frozen=True)
class Stage3MomentumReports:
    decisions: tuple[dict[str, object], ...]
    associations: tuple[dict[str, object], ...]
    orders: tuple[dict[str, object], ...]
    fills: tuple[dict[str, object], ...]
    positions: tuple[dict[str, object], ...]
    account: dict[str, object]
    result: dict[str, object]
    summary: dict[str, object]
    funding: dict[str, object]
    terminal: dict[str, object]


@dataclass(frozen=True)
class Stage3MomentumOutcome:
    reports: Stage3MomentumReports
    partition: Path
    run_identity: str
    result_digest: str


@dataclass(frozen=True)
class _LoadedStage3Data:
    instruments: tuple[CryptoPerpetual, ...]
    bars: tuple[Bar, ...]
    marks: tuple[MarkPriceUpdate, ...]
    funding: tuple[FundingRateUpdate, ...]
    fee_provenance: tuple[dict[str, object], ...]


def base_momentum_parameters() -> Stage3MomentumParameters:
    return Stage3MomentumParameters(
        evaluation_start_ns=datetime_to_nanos(parse_utc(STAGE3_MOMENTUM_START)),
        evaluation_end_ns=datetime_to_nanos(parse_utc(STAGE3_MOMENTUM_END)),
    )


def run_stage3_momentum_backtest(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
) -> Stage3MomentumOutcome:
    """Run the fixed 2022 BTC/ETH momentum baseline from the accepted catalog."""
    parameters = base_momentum_parameters()
    parameters.validate()
    _require_runtime()
    _require_requirements_baseline()
    _require_external_run_root(config.run_root, catalog_path=config.catalog_path)
    _claim_run_root(config.run_root)
    try:
        start = parse_utc(STAGE3_MOMENTUM_START)
        end = parse_utc(STAGE3_MOMENTUM_END)
        context_start = start - timedelta(hours=FEATURE_LOOKBACK_HOURS)
        try:
            expected = load_accepted_feature_window(
                config,
                acceptance_record_path=acceptance_record_path,
                start=context_start,
                end=end,
                decision_start=start,
                mode="evaluation",
            )
            loaded = _load_native_stage3_data(
                config.catalog_path,
                start=context_start,
                end=end,
            )
        except Stage3DataError as exc:
            raise Stage3MomentumError(f"accepted Stage 3 input failed: {exc}") from exc
        reports = _run_engine(loaded, parameters)
        _require_runtime_feature_parity(reports.decisions, expected, loaded.instruments)
        run_identity = _run_identity(config, parameters)
        result_digest = _canonical_digest(_business_result(reports))
        outcome = Stage3MomentumOutcome(
            reports=reports,
            partition=config.run_root,
            run_identity=run_identity,
            result_digest=result_digest,
        )
        _write_outcome(outcome, config, parameters, loaded.fee_provenance)
        return outcome
    except Exception:
        _release_empty_run_root(config.run_root)
        raise


def canonical_business_result(outcome: Stage3MomentumOutcome) -> dict[str, object]:
    return {
        **_business_result(outcome.reports),
        "result_digest": outcome.result_digest,
        "run_identity": outcome.run_identity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 3 fixed-parameter momentum Nautilus backtest"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--acceptance-record", type=Path, required=True)
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[4]
    config = load_stage3_config(args.config, repository_root=repository_root)
    outcome = run_stage3_momentum_backtest(
        config,
        acceptance_record_path=args.acceptance_record,
    )
    json.dump(canonical_business_result(outcome), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _require_runtime() -> None:
    if distribution_version() != EXPECTED_VERSION:
        raise Stage3MomentumError(
            "installed Nautilus version does not match the locked runtime"
        )


def _require_requirements_baseline() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    path = repository_root / STAGE3_REQUIREMENTS_RELATIVE_PATH
    if not path.is_file():
        raise Stage3MomentumError("Stage 3 requirements baseline is missing")
    content = path.read_bytes()
    header = f"blob {len(content)}\0".encode()
    blob_sha = hashlib.sha1(header + content, usedforsecurity=False).hexdigest()
    if blob_sha != STAGE3_REQUIREMENTS_BLOB_SHA:
        raise Stage3MomentumError("Stage 3 requirements baseline blob has drifted")


def _require_empty_run_root(run_root: Path) -> None:
    if not run_root.is_absolute():
        raise Stage3MomentumError("run_root must be an absolute external path")
    if run_root.exists():
        raise Stage3MomentumError("run_root must be a new empty identity partition")


def _require_external_run_root(run_root: Path, *, catalog_path: Path) -> None:
    _require_empty_run_root(run_root)
    resolved = run_root.resolve(strict=False)
    repository = Path(__file__).resolve().parents[4]
    catalog = catalog_path.resolve(strict=False)
    if resolved == repository or repository in resolved.parents:
        raise Stage3MomentumError("run_root must be outside the repository")
    if (
        resolved == catalog
        or catalog in resolved.parents
        or resolved in catalog.parents
    ):
        raise Stage3MomentumError("run_root must not overlap the Nautilus catalog")
    if any(part.lower() == "latest" for part in resolved.parts):
        raise Stage3MomentumError("run_root must not use a latest alias")


def _claim_run_root(run_root: Path) -> None:
    run_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_root.mkdir()
    except FileExistsError as exc:
        raise Stage3MomentumError(
            "run_root must be a new empty identity partition"
        ) from exc


def _release_empty_run_root(run_root: Path) -> None:
    try:
        run_root.rmdir()
    except OSError:
        # A non-empty partition is immutable failure evidence and must not be
        # removed or reused after a partial output commit.
        pass


def _load_native_stage3_data(
    catalog_path: Path,
    *,
    start: datetime,
    end: datetime,
) -> _LoadedStage3Data:
    catalog = ParquetDataCatalog(str(catalog_path))
    catalog_instruments = tuple(
        item for item in catalog.instruments() if str(item.id) in STAGE2_INSTRUMENT_IDS
    )
    instruments_by_id = {str(item.id): item for item in catalog_instruments}
    if set(instruments_by_id) != set(STAGE2_INSTRUMENT_IDS):
        raise Stage3MomentumError("catalog instrument identity does not match Stage 3")
    catalog_instruments = tuple(
        instruments_by_id[value] for value in STAGE2_INSTRUMENT_IDS
    )
    snapshot_by_id = _snapshot_instruments(catalog_path)
    effective: list[CryptoPerpetual] = []
    provenance: list[dict[str, object]] = []
    for item in catalog_instruments:
        if not isinstance(item, CryptoPerpetual):
            raise Stage3MomentumError("catalog instrument is not CryptoPerpetual")
        payload = item.to_dict()
        catalog_maker = payload.get("maker_fee")
        catalog_taker = payload.get("taker_fee")
        snapshot_payload = snapshot_by_id[str(item.id)]
        snapshot_maker = snapshot_payload.get("maker_fee")
        snapshot_taker = snapshot_payload.get("taker_fee")
        payload["maker_fee"] = str(BASE_MAKER_FEE)
        payload["taker_fee"] = str(BASE_TAKER_FEE)
        bound = CryptoPerpetual.from_dict(payload)
        if bound.maker_fee != BASE_MAKER_FEE or bound.taker_fee != BASE_TAKER_FEE:
            raise Stage3MomentumError("effective fee binding did not round-trip")
        effective.append(bound)
        provenance.append(
            {
                "effective_maker_fee": str(bound.maker_fee),
                "effective_taker_fee": str(bound.taker_fee),
                "instrument_id": str(bound.id),
                "catalog_maker_fee": catalog_maker,
                "catalog_maker_matches_base": (
                    catalog_maker is not None
                    and Decimal(str(catalog_maker)) == BASE_MAKER_FEE
                ),
                "catalog_matches_snapshot": (
                    catalog_maker == snapshot_maker and catalog_taker == snapshot_taker
                ),
                "catalog_taker_fee": catalog_taker,
                "catalog_taker_matches_base": (
                    catalog_taker is not None
                    and Decimal(str(catalog_taker)) == BASE_TAKER_FEE
                ),
                "snapshot_maker_fee": snapshot_maker,
                "snapshot_maker_matches_base": (
                    snapshot_maker is not None
                    and Decimal(str(snapshot_maker)) == BASE_MAKER_FEE
                ),
                "snapshot_taker_fee": snapshot_taker,
                "snapshot_taker_matches_base": (
                    snapshot_taker is not None
                    and Decimal(str(snapshot_taker)) == BASE_TAKER_FEE
                ),
            }
        )

    start_ns = datetime_to_nanos(start)
    end_ns = datetime_to_nanos(end)
    inclusive_end = end_ns - 1
    bars: list[Bar] = []
    marks: list[MarkPriceUpdate] = []
    funding: list[FundingRateUpdate] = []
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        bars.extend(
            catalog.query_bars(
                identifiers=[stage2_bar_type_str(instrument_id, "1h")],
                start=start_ns,
                end=inclusive_end,
            )
        )
        marks.extend(
            catalog.query_mark_price_updates(
                instrument_ids=[instrument_id],
                start=start_ns,
                end=inclusive_end,
            )
        )
        frame = load_funding(catalog_path, instrument_id, start, end).collect()
        for row in frame.to_dicts():
            funding.append(
                FundingRateUpdate(
                    instrument_id=InstrumentId.from_str(instrument_id),
                    rate=Decimal(_required_row_string(row, "rate")),
                    ts_event=_required_row_int(row, "ts_event"),
                    ts_init=_required_row_int(row, "ts_init"),
                    interval=_optional_row_int(row, "interval"),
                    next_funding_ns=_optional_row_int(row, "next_funding_ns"),
                )
            )
    if not bars or not marks or not funding:
        raise Stage3MomentumError("accepted Stage 3 run data is incomplete")
    return _LoadedStage3Data(
        instruments=tuple(effective),
        bars=tuple(bars),
        marks=tuple(marks),
        funding=tuple(funding),
        fee_provenance=tuple(provenance),
    )


def _snapshot_instruments(catalog_path: Path) -> dict[str, Mapping[str, object]]:
    path = catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Stage3MomentumError("instrument snapshot is not readable JSON") from exc
    if not isinstance(payload, dict) or not isinstance(
        payload.get("instruments"), list
    ):
        raise Stage3MomentumError("instrument snapshot has no instrument records")
    result: dict[str, Mapping[str, object]] = {}
    for raw in payload["instruments"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise Stage3MomentumError("instrument snapshot record is invalid")
        result[raw["id"]] = raw
    if set(result) != set(STAGE2_INSTRUMENT_IDS):
        raise Stage3MomentumError("instrument snapshot identity does not match Stage 3")
    return result


def _run_engine(
    loaded: _LoadedStage3Data,
    parameters: Stage3MomentumParameters,
) -> Stage3MomentumReports:
    usdt = Currency.from_str("USDT")
    venue = Venue(STAGE3_VENUE)
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId.from_str(STAGE3_TRADER_ID),
            bypass_logging=True,
            run_analysis=False,
            logging=LoggerConfig(bypass_logging=True, stdout_level=LogLevel.OFF),
        )
    )
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money.from_str(f"{STAGE3_STARTING_USDT} USDT")],
        base_currency=usdt,
        default_leverage=STAGE3_DEFAULT_LEVERAGE,
        bar_execution=True,
        use_random_ids=False,
    )
    for instrument in loaded.instruments:
        engine.add_instrument(instrument)
    opening_ticks = _bar_open_trade_ticks(
        loaded.bars,
        loaded.instruments,
        target_notional=parameters.target_notional_usdt,
    )
    unsorted_events: list[Bar | MarkPriceUpdate | FundingRateUpdate | TradeTick] = [
        *loaded.marks,
        *loaded.funding,
        *opening_ticks,
        *loaded.bars,
    ]
    events = sorted(
        unsorted_events,
        key=lambda item: (
            int(item.ts_event),
            # At an exact timestamp collision, the B_1-open execution belongs
            # to the newly opened interval and must precede its funding event.
            0
            if isinstance(item, MarkPriceUpdate)
            else 1
            if isinstance(item, TradeTick)
            else 2
            if isinstance(item, FundingRateUpdate)
            else 3,
            str(getattr(item, "instrument_id", getattr(item, "bar_type", ""))),
        ),
    )
    engine.add_data(events, sort=True)
    strategy = Stage3MomentumStrategy(parameters)
    engine.add_strategy(strategy)
    try:
        engine.run()
        if strategy.fatal_error is not None:
            raise Stage3MomentumError(
                f"momentum Strategy stopped on an event error: {strategy.fatal_error}"
            )
        reports = _collect_reports(
            engine, strategy, loaded.marks, loaded.funding, venue, usdt
        )
    finally:
        engine.dispose()
    return reports


def _bar_open_trade_ticks(
    bars: Sequence[Bar],
    instruments: Sequence[CryptoPerpetual],
    *,
    target_notional: Decimal,
) -> tuple[TradeTick, ...]:
    """Expose each accepted 1h bar open before Nautilus processes later OHLC prices."""
    by_id = {str(instrument.id): instrument for instrument in instruments}
    prior_target_capacity = {
        instrument_id: instrument.size_increment.as_decimal()
        for instrument_id, instrument in by_id.items()
    }
    ticks: list[TradeTick] = []
    for bar in sorted(
        bars,
        key=lambda item: (int(item.ts_event), str(item.bar_type.instrument_id)),
    ):
        instrument_id = str(bar.bar_type.instrument_id)
        instrument = by_id[instrument_id]
        capacity = prior_target_capacity[instrument_id]
        open_ts = (
            int(bar.ts_event)
            - stage3_features.HOUR_NS
            + stage3_features.STAGE2_CLOSE_OFFSET_MS * stage3_features.MS_NS
        )
        ticks.append(
            TradeTick(
                instrument_id=instrument.id,
                price=bar.open,
                size=Quantity.from_str(f"{capacity:.{instrument.size_precision}f}"),
                aggressor_side=AggressorSide.NO_AGGRESSOR,
                trade_id=TradeId.from_str(
                    STAGE3_BAR_OPEN_TRADE_ID_PREFIX
                    + hashlib.sha256(
                        f"{bar.bar_type.instrument_id}|{bar.ts_event}".encode()
                    ).hexdigest()[:16]
                ),
                ts_event=open_ts,
                ts_init=open_ts,
            )
        )
        close = Decimal(str(bar.close))
        increment = instrument.size_increment.as_decimal()
        closed_bar_target = (target_notional / close / increment).to_integral_value(
            rounding=ROUND_DOWN
        ) * increment
        prior_target_capacity[instrument_id] = max(capacity, closed_bar_target)
    return tuple(ticks)


def _collect_reports(
    engine: BacktestEngine,
    strategy: Stage3MomentumStrategy,
    mark_events: Sequence[MarkPriceUpdate],
    funding_events: Sequence[FundingRateUpdate],
    venue: Venue,
    usdt: Currency,
) -> Stage3MomentumReports:
    native_orders = tuple(engine.cache.orders())
    orders = tuple(_order_record(item, usdt) for item in native_orders)
    fills = _native_fill_records(native_orders, strategy.fill_event_sequences)
    _require_fee_and_execution_contract(strategy.decisions, orders, fills, usdt)
    if strategy.pending_reversal_count:
        raise Stage3MomentumError("terminal state contains unresolved reversal intent")
    native_positions = tuple(engine.cache.positions())
    native_position_snapshots = tuple(engine.cache.position_snapshots())
    position_records = [
        *(
            _position_record(item, is_snapshot=True)
            for item in native_position_snapshots
        ),
        *(_position_record(item, is_snapshot=False) for item in native_positions),
    ]
    position_records.sort(
        key=lambda item: (
            cast(str, item["instrument_id"]),
            cast(int, item["ts_opened"]),
            cast(str, item["id"]),
        )
    )
    positions = tuple(position_records)
    account = engine.cache.account_for_venue(venue)
    if account is None:
        raise Stage3MomentumError("Nautilus account result is missing")
    account_events = tuple(_account_event_record(item) for item in account.events)
    funding = _funding_report(account_events, funding_events)
    account_report: dict[str, object] = {
        "base_currency": str(account.base_currency),
        "events": list(account_events),
        "free": str(account.balance_free(usdt)),
        "id": str(account.id),
        "locked": str(account.balance_locked(usdt)),
        "total": str(account.balance_total(usdt)),
        "type": _named(account.account_type),
    }
    result: dict[str, object] = {
        str(key): str(value)
        for key, value in sorted(dict(engine.get_result().summary).items())
    }
    if unsettled_orders(engine.cache):
        raise Stage3MomentumError(
            "terminal state contains unexplained unsettled orders"
        )
    latest_marks = _latest_marks(
        engine, strategy.parameters.instrument_ids, mark_events
    )
    terminal_positions = tuple(
        _terminal_position(item, latest_marks) for item in engine.cache.positions_open()
    )
    terminal = {
        "open_order_count": 0,
        "positions": list(terminal_positions),
        "valuation_source": "Nautilus Position.unrealized_pnl(last mark)",
    }
    total_commission = sum(
        (
            _money_amount(cast(str, item["commission"]))
            for item in fills
            if item["commission"] is not None
        ),
        Decimal(0),
    )
    turnover_notional = sum(
        (
            Decimal(cast(str, item["last_qty"])) * Decimal(cast(str, item["last_px"]))
            for item in fills
        ),
        Decimal(0),
    )
    summary: dict[str, object] = {
        "fill_count": len(fills),
        "order_count": len(orders),
        "position_count": len(positions),
        "total_commission": str(total_commission),
        "total_funding": cast(str, funding["total_funding"]),
        "trade_count": sum(1 for item in positions if item["is_closed"] is True),
        "turnover": str(turnover_notional / STAGE3_STARTING_USDT),
    }
    associations = _associations(strategy.decisions, orders, fills)
    return Stage3MomentumReports(
        decisions=tuple(dict(item) for item in strategy.decisions),
        associations=associations,
        orders=orders,
        fills=fills,
        positions=positions,
        account=account_report,
        result=result,
        summary=summary,
        funding=funding,
        terminal=terminal,
    )


def _require_fee_and_execution_contract(
    decisions: Sequence[Mapping[str, object]],
    orders: Sequence[Mapping[str, object]],
    fills: Sequence[Mapping[str, object]],
    currency: Currency,
) -> None:
    by_order = {cast(str, item["client_order_id"]): item for item in orders}
    fills_by_order: dict[str, list[Mapping[str, object]]] = {}
    for fill in fills:
        fills_by_order.setdefault(cast(str, fill["client_order_id"]), []).append(fill)
    submitted_order_ids: set[str] = set()
    for decision in decisions:
        if decision["reason"] == "b1_unsettled_order":
            raise Stage3MomentumError("a momentum intent could not execute on B_1")
        decision_ts = cast(int, decision["decision_ts"])
        decision_sequence = cast(int, decision["decision_sequence"])
        intents = cast(Sequence[Mapping[str, object]], decision["order_intents"])
        if [item["order_id"] for item in intents] != list(
            cast(Sequence[str], decision["order_ids"])
        ):
            raise Stage3MomentumError("decision order intent identity is inconsistent")
        for intent in intents:
            order_id = cast(str, intent["order_id"])
            if order_id in submitted_order_ids:
                raise Stage3MomentumError("order is associated with multiple decisions")
            submitted_order_ids.add(order_id)
            order = by_order.get(order_id)
            if order is None:
                raise Stage3MomentumError("submitted market order is missing")
            if order["status"] != "FILLED" or Decimal(
                cast(str, order["filled_qty"])
            ) != Decimal(cast(str, order["quantity"])):
                raise Stage3MomentumError(
                    "submitted market order was not completely filled: "
                    f"{order_id} status={order['status']} "
                    f"reason={order['last_event_reason']}"
                )
            native_fills = fills_by_order.get(order_id, [])
            if not native_fills:
                raise Stage3MomentumError(
                    f"submitted market order has no native fills: {order_id}"
                )
            native_quantity = sum(
                (Decimal(cast(str, fill["last_qty"])) for fill in native_fills),
                Decimal(0),
            )
            if native_quantity != Decimal(cast(str, order["quantity"])):
                raise Stage3MomentumError(
                    "native fill quantity does not complete the submitted intent"
                )
            expected_fill_ts = cast(int, intent["expected_fill_ts"])
            expected_fill_price = Decimal(cast(str, intent["expected_fill_price"]))
            submit_sequence = cast(int, intent["submit_sequence"])
            if submit_sequence <= decision_sequence:
                raise Stage3MomentumError(
                    "order submission does not follow its target decision"
                )
            leg = cast(str, intent["leg"])
            flat_sequence = intent.get("flat_confirmation_sequence")
            if leg == "reversal_open" and (
                not isinstance(flat_sequence, int) or submit_sequence <= flat_sequence
            ):
                raise Stage3MomentumError(
                    "reversal open was not ordered after flat confirmation"
                )
            for fill in native_fills:
                fill_ts = cast(int, fill["ts_event"])
                if fill_ts != expected_fill_ts:
                    raise Stage3MomentumError(
                        "market fill missed its first executable event"
                    )
                if fill_ts <= decision_ts:
                    raise Stage3MomentumError("market fill is not after its decision")
                if Decimal(cast(str, fill["last_px"])) != expected_fill_price:
                    raise Stage3MomentumError(
                        "market fill price does not match the B_1 open"
                    )
                fill_sequence = cast(int, fill["event_sequence"])
                if fill_sequence <= submit_sequence:
                    raise Stage3MomentumError(
                        "native fill event does not follow order submission"
                    )
                commission = fill["commission"]
                if commission is None:
                    raise Stage3MomentumError("native market fill has no commission")
                expected_amount = (
                    Decimal(cast(str, fill["last_qty"]))
                    * Decimal(cast(str, fill["last_px"]))
                    * BASE_TAKER_FEE
                )
                expected = Money.from_str(f"{expected_amount} {currency}").as_decimal()
                if _money_amount(cast(str, commission)) != expected:
                    raise Stage3MomentumError(
                        "market fill commission does not match the bound taker fee"
                    )
    if set(fills_by_order) != submitted_order_ids:
        raise Stage3MomentumError("native fills include an unassociated order")


def _require_runtime_feature_parity(
    decisions: Sequence[Mapping[str, object]],
    expected: Mapping[str, Sequence[FeatureObservation]],
    instruments: Sequence[CryptoPerpetual],
) -> None:
    by_id = {str(item.id): item for item in instruments}
    expected_rows = {
        (instrument_id, item.decision_ts): item
        for instrument_id, rows in expected.items()
        for item in rows
        if item.status == "ready" and item.tradable
    }
    if len(decisions) != len(expected_rows):
        raise Stage3MomentumError("runtime decision count differs from feature rows")
    for decision in decisions:
        key = (
            cast(str, decision["instrument_id"]),
            cast(int, decision["decision_ts"]),
        )
        row = expected_rows.get(key)
        if row is None:
            raise Stage3MomentumError("runtime decision has no accepted feature row")
        values = row.require_ready()
        ret_24h = values[stage3_features.FEATURE_NAMES.index("ret_24h")]
        if Decimal(cast(str, decision["ret_24h"])) != Decimal(str(ret_24h)):
            raise Stage3MomentumError("runtime ret_24h differs from accepted features")
        signal = momentum_signal(ret_24h, MOMENTUM_THRESHOLD)
        instrument = by_id[key[0]]
        target = target_quantity(
            signal=signal,
            close=Decimal(cast(str, decision["close"])),
            instrument=instrument,
            target_notional=MOMENTUM_TARGET_NOTIONAL_USDT,
        )
        if (
            decision["signal"] != signal
            or Decimal(cast(str, decision["target_qty"])) != target
        ):
            raise Stage3MomentumError("runtime target differs from accepted features")


def _funding_report(
    account_events: Sequence[Mapping[str, object]],
    funding_events: Sequence[FundingRateUpdate],
) -> dict[str, object]:
    funding_by_ts: dict[int, list[dict[str, object]]] = {}
    for item in funding_events:
        funding_by_ts.setdefault(int(item.ts_event), []).append(
            {
                "instrument_id": str(item.instrument_id),
                "interval": item.interval,
                "next_funding_ns": item.next_funding_ns,
                "rate": str(item.rate),
                "ts_event": int(item.ts_event),
            }
        )
    events_by_ts: dict[int, list[Mapping[str, object]]] = {}
    for event in account_events:
        events_by_ts.setdefault(cast(int, event["ts_event"]), []).append(event)
    prior = STAGE3_STARTING_USDT
    timeline: list[dict[str, object]] = []
    total = Decimal(0)
    for ts_event in sorted(events_by_ts):
        states = events_by_ts[ts_event]
        funding_delta = Decimal(0)
        funding_account_event_count = 0
        for state in states:
            ending = _account_event_total(state)
            if ts_event in funding_by_ts and state.get("reported") is True:
                funding_delta += ending - prior
                funding_account_event_count += 1
            prior = ending
        if ts_event not in funding_by_ts:
            continue
        if funding_account_event_count > len(funding_by_ts[ts_event]):
            raise Stage3MomentumError(
                "native funding produced more account events than rate updates"
            )
        total += funding_delta
        timeline.append(
            {
                "account_delta": str(funding_delta),
                "events": funding_by_ts[ts_event],
                "native_account_event_count": funding_account_event_count,
                "ts_event": ts_event,
            }
        )
    for ts_event in sorted(set(funding_by_ts).difference(events_by_ts)):
        timeline.append(
            {
                "account_delta": "0",
                "events": funding_by_ts[ts_event],
                "native_account_event_count": 0,
                "ts_event": ts_event,
            }
        )
    timeline.sort(key=lambda item: cast(int, item["ts_event"]))
    return {
        "derivation": (
            "Nautilus reported account-state deltas at native funding timestamps"
        ),
        "events": timeline,
        "total_funding": str(total),
    }


def _latest_marks(
    engine: BacktestEngine,
    instrument_ids: Sequence[str],
    mark_events: Sequence[MarkPriceUpdate],
) -> dict[str, tuple[object, int]]:
    result: dict[str, tuple[object, int]] = {}
    for value in instrument_ids:
        native_id = InstrumentId.from_str(value)
        mark = engine.cache.mark_price(native_id)
        if mark is None:
            raise Stage3MomentumError("terminal Nautilus mark is missing")
        mark_value = getattr(mark, "value", mark)
        # The cache owns the price; the final mark event owns its valuation time.
        updates = tuple(item for item in mark_events if item.instrument_id == native_id)
        if not updates:
            raise Stage3MomentumError("terminal mark provenance is missing")
        result[value] = (mark_value, int(updates[-1].ts_event))
    return result


def _terminal_position(
    position: object, latest_marks: Mapping[str, tuple[object, int]]
) -> dict[str, object]:
    instrument_id = str(getattr(position, "instrument_id"))
    mark, ts_event = latest_marks[instrument_id]
    return {
        "instrument_id": instrument_id,
        "mark": str(mark),
        "quantity": str(_native_signed_position_quantity(position)),
        "unrealized_pnl": str(getattr(position, "unrealized_pnl")(mark)),
        "valuation_ts": ts_event,
    }


def _native_signed_position_quantity(position: object) -> Decimal:
    quantity = cast(Decimal, getattr(position, "quantity").as_decimal())
    if getattr(position, "is_short"):
        return -quantity
    if getattr(position, "is_long"):
        return quantity
    raise Stage3MomentumError("terminal open position has unknown side")


def _associations(
    decisions: Sequence[Mapping[str, object]],
    orders: Sequence[Mapping[str, object]],
    fills: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    by_id = {cast(str, item["client_order_id"]): item for item in orders}
    fills_by_order: dict[str, list[Mapping[str, object]]] = {}
    for fill in fills:
        fills_by_order.setdefault(cast(str, fill["client_order_id"]), []).append(fill)
    result: list[dict[str, object]] = []
    for decision in decisions:
        for intent in cast(Sequence[Mapping[str, object]], decision["order_intents"]):
            order_id = cast(str, intent["order_id"])
            order = by_id[order_id]
            for fill in fills_by_order[order_id]:
                result.append(
                    {
                        "decision_id": decision["decision_id"],
                        "decision_sequence": decision["decision_sequence"],
                        "decision_ts": decision["decision_ts"],
                        "expected_fill_ts": intent["expected_fill_ts"],
                        "expected_fill_price": intent["expected_fill_price"],
                        "fill_event_sequence": fill["event_sequence"],
                        "fill_ts": fill["ts_event"],
                        "flat_confirmation_sequence": intent.get(
                            "flat_confirmation_sequence"
                        ),
                        "instrument_id": decision["instrument_id"],
                        "leg": intent["leg"],
                        "order_id": order_id,
                        "order_submit_sequence": intent["submit_sequence"],
                        "order_ts_init": order["ts_init"],
                        "trade_id": fill["trade_id"],
                    }
                )
    result.sort(key=lambda item: cast(int, item["fill_event_sequence"]))
    return tuple(result)


def _order_record(order: object, currency: Currency) -> dict[str, object]:
    last_event = getattr(order, "last_event", None)
    return {
        "avg_px": _maybe_str(getattr(order, "avg_px", None)),
        "client_order_id": str(getattr(order, "client_order_id")),
        "commission": _commission(order, currency),
        "filled_qty": str(getattr(order, "filled_qty")),
        "instrument_id": str(getattr(order, "instrument_id")),
        "last_event_reason": _maybe_str(getattr(last_event, "reason", None)),
        "quantity": str(getattr(order, "quantity")),
        "side": _named(getattr(order, "side")),
        "status": _named(getattr(order, "status")),
        "ts_init": int(getattr(order, "ts_init")),
        "ts_last": int(getattr(order, "ts_last")),
    }


def _native_fill_records(
    orders: Sequence[object],
    fill_event_sequences: Mapping[tuple[str, str], int],
) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for order in orders:
        for event in cast(Sequence[object], getattr(order, "events")()):
            if not isinstance(event, OrderFilled):
                continue
            key = (str(event.client_order_id), str(event.trade_id))
            sequence = fill_event_sequences.get(key)
            if sequence is None:
                raise Stage3MomentumError(
                    "native fill is missing its Strategy callback sequence"
                )
            records.append(_fill_record(event, event_sequence=sequence))
    records.sort(
        key=lambda item: (
            cast(int, item["ts_event"]),
            cast(int, item["event_sequence"]),
            cast(str, item["client_order_id"]),
            cast(str, item["trade_id"]),
        )
    )
    return tuple(records)


def _fill_record(fill: object, *, event_sequence: int) -> dict[str, object]:
    return {
        "client_order_id": str(getattr(fill, "client_order_id")),
        "commission": _maybe_str(getattr(fill, "commission", None)),
        "currency": str(getattr(fill, "currency")),
        "event_sequence": event_sequence,
        "instrument_id": str(getattr(fill, "instrument_id")),
        "last_px": str(getattr(fill, "last_px")),
        "last_qty": str(getattr(fill, "last_qty")),
        "liquidity_side": _named(getattr(fill, "liquidity_side")),
        "order_side": _named(getattr(fill, "order_side")),
        "order_type": _named(getattr(fill, "order_type")),
        "position_id": _maybe_str(getattr(fill, "position_id", None)),
        "trade_id": str(getattr(fill, "trade_id")),
        "ts_event": int(getattr(fill, "ts_event")),
        "ts_init": int(getattr(fill, "ts_init")),
        "venue_order_id": str(getattr(fill, "venue_order_id")),
    }


def _position_record(
    position: object,
    *,
    is_snapshot: bool,
) -> dict[str, object]:
    native_id = (
        _position_source_id(position) if is_snapshot else str(getattr(position, "id"))
    )
    opening_order_id = str(getattr(position, "opening_order_id"))
    closing_order_id = _maybe_str(getattr(position, "closing_order_id", None))
    closed = getattr(position, "is_closed", None)
    closed = closed() if callable(closed) else closed
    if not isinstance(closed, bool):
        raise Stage3MomentumError("Nautilus position has unknown terminal state")
    return {
        "avg_px_close": _maybe_str(getattr(position, "avg_px_close", None)),
        "avg_px_open": str(getattr(position, "avg_px_open")),
        "closing_order_id": closing_order_id,
        "id": f"{native_id}:{opening_order_id}" if is_snapshot else native_id,
        "instrument_id": str(getattr(position, "instrument_id")),
        "is_closed": closed,
        "is_snapshot": is_snapshot,
        "opening_order_id": opening_order_id,
        "peak_qty": str(getattr(position, "peak_qty", getattr(position, "quantity"))),
        "position_id": native_id,
        "quantity": str(getattr(position, "quantity")),
        "realized_pnl": _maybe_str(getattr(position, "realized_pnl", None)),
        "side": _named(getattr(position, "side")),
        "ts_closed": _maybe_int(getattr(position, "ts_closed", None)),
        "ts_opened": int(getattr(position, "ts_opened")),
    }


def _position_source_id(position: object) -> str:
    events_method = getattr(position, "events", None)
    if not callable(events_method):
        raise Stage3MomentumError("Nautilus position snapshot has no events")
    events = tuple(events_method())
    position_ids = {
        str(value)
        for event in events
        if (value := getattr(event, "position_id", None)) is not None
    }
    if len(position_ids) != 1:
        raise Stage3MomentumError(
            "Nautilus position snapshot has ambiguous source identity"
        )
    return next(iter(position_ids))


def _account_event_record(event: object) -> dict[str, object]:
    payload = dict(cast(Mapping[str, object], getattr(event, "to_dict")()))
    payload.pop("event_id", None)
    return payload


def _account_event_total(event: Mapping[str, object]) -> Decimal:
    balances = event.get("balances")
    if not isinstance(balances, list):
        raise Stage3MomentumError("Nautilus account event has no balances")
    for raw in balances:
        if isinstance(raw, dict) and raw.get("currency") == "USDT":
            return Decimal(str(raw["total"]))
    raise Stage3MomentumError("Nautilus account event has no USDT balance")


def _commission(order: object, currency: Currency) -> str | None:
    method = getattr(order, "commission", None)
    if method is None:
        return None
    return _maybe_str(method(currency) if callable(method) else method)


def _business_result(reports: Stage3MomentumReports) -> dict[str, object]:
    return {
        "account": reports.account,
        "associations": list(reports.associations),
        "decisions": list(reports.decisions),
        "fills": list(reports.fills),
        "funding": reports.funding,
        "orders": list(reports.orders),
        "positions": list(reports.positions),
        "result": reports.result,
        "summary": reports.summary,
        "terminal": reports.terminal,
    }


def _run_identity(config: Stage3Config, parameters: Stage3MomentumParameters) -> str:
    return _canonical_digest(
        {
            "config_identity": _config_identity_payload(config),
            "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
            "parameters": _parameter_payload(parameters),
            "relevant_code_digest": _relevant_code_digest(),
            "requirements_baseline": _requirements_payload(),
            "schema": STAGE3_MOMENTUM_SCHEMA,
        }
    )


def _config_identity_payload(config: Stage3Config) -> dict[str, object]:
    return {
        "acceptance_digest": config.acceptance_digest,
        "artifact_lock_sha256": hashlib.sha256(
            config.artifact_lock_path.read_bytes()
        ).hexdigest(),
        "dataset_digest": config.dataset_digest,
        "dataset_id": config.dataset_id,
        "instrument_snapshot_checksum": config.instrument_snapshot_checksum,
        "market_data_manifest_digest": config.market_data_manifest_digest,
        "runtime_identity": config.runtime_identity,
        "source_manifest_digest": config.source_manifest_digest,
    }


def _requirements_payload() -> dict[str, str]:
    return {
        "base_sha": STAGE3_REQUIREMENTS_BASE_SHA,
        "blob_sha": STAGE3_REQUIREMENTS_BLOB_SHA,
        "path": STAGE3_REQUIREMENTS_RELATIVE_PATH,
    }


def _parameter_payload(parameters: Stage3MomentumParameters) -> dict[str, object]:
    return {
        "base_funding_multiplier": "1",
        "evaluation_end_ns": parameters.evaluation_end_ns,
        "evaluation_start_ns": parameters.evaluation_start_ns,
        "instrument_ids": list(parameters.instrument_ids),
        "lookback_hours": parameters.lookback_hours,
        "maker_fee": str(parameters.maker_fee),
        "oms_type": "NETTING",
        "order_type": "MARKET",
        "starting_balance_usdt": str(STAGE3_STARTING_USDT),
        "taker_fee": str(parameters.taker_fee),
        "target_notional_usdt": str(parameters.target_notional_usdt),
        "threshold": str(parameters.threshold),
    }


def _write_outcome(
    outcome: Stage3MomentumOutcome,
    config: Stage3Config,
    parameters: Stage3MomentumParameters,
    fee_provenance: Sequence[Mapping[str, object]],
) -> None:
    partition = outcome.partition
    fee_provenance_payload = [dict(item) for item in fee_provenance]
    payloads: dict[str, object] = {
        "account.json": outcome.reports.account,
        "associations.json": list(outcome.reports.associations),
        "decisions.json": list(outcome.reports.decisions),
        "fee-provenance.json": fee_provenance_payload,
        "fills.json": list(outcome.reports.fills),
        "funding.json": outcome.reports.funding,
        "orders.json": list(outcome.reports.orders),
        "positions.json": list(outcome.reports.positions),
        "result.json": outcome.reports.result,
        "stage3_partition_identity.json": {
            "acceptance_digest": config.acceptance_digest,
            "dataset_id": config.dataset_id,
            "runtime_identity": config.runtime_identity,
        },
        "summary.json": outcome.reports.summary,
        "terminal.json": outcome.reports.terminal,
    }
    for name, payload in payloads.items():
        _write_json_exclusive(partition / name, payload)
    # The manifest is the completion marker and is committed last. Every file
    # uses exclusive creation so even an external writer cannot be overwritten.
    _write_json_exclusive(
        partition / "manifest.json",
        {
            "acceptance_digest": config.acceptance_digest,
            "dataset_digest": config.dataset_digest,
            "dataset_id": config.dataset_id,
            "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
            "fee_provenance": fee_provenance_payload,
            "instrument_snapshot_checksum": config.instrument_snapshot_checksum,
            "live_not_approved": LIVE_NOT_APPROVED,
            "market_data_manifest_digest": config.market_data_manifest_digest,
            "offline_backtest_only": OFFLINE_BACKTEST_ONLY,
            "parameters": _parameter_payload(parameters),
            "relevant_code_digest": _relevant_code_digest(),
            "requirements_baseline": _requirements_payload(),
            "result_digest": outcome.result_digest,
            "run_identity": outcome.run_identity,
            "runtime_identity": UPSTREAM_RELEASE_IDENTITY,
            "schema": STAGE3_MOMENTUM_SCHEMA,
            "source_manifest_digest": config.source_manifest_digest,
        },
    )


def _write_json_exclusive(path: Path, payload: object) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise Stage3MomentumError(
            f"run output already exists and cannot be overwritten: {path.name}"
        ) from exc


def _relevant_code_files() -> tuple[tuple[str, Path], ...]:
    return (
        ("integrations/nautilus/__init__.py", Path(nautilus_integration.__file__)),
        (
            "integrations/nautilus/stage2_artifact.py",
            Path(stage2_artifact.__file__),
        ),
        (
            "integrations/nautilus/stage2_btceth.py",
            Path(nautilus_stage2_btceth.__file__),
        ),
        ("integrations/nautilus/stage3_momentum.py", Path(__file__)),
        (
            "integrations/nautilus/strategies/stage3_momentum.py",
            Path(momentum_strategy.__file__),
        ),
        ("research/stage3_features.py", Path(stage3_features.__file__)),
        ("research/source_schema.py", Path(research_source_schema.__file__)),
        ("research/views.py", Path(research_views.__file__)),
        ("source_data/stage2_btceth.py", Path(source_stage2_btceth.__file__)),
    )


def _relevant_code_digest(
    files: Sequence[tuple[str, Path]] | None = None,
) -> str:
    paths = _relevant_code_files() if files is None else tuple(files)
    payload = [
        {
            "name": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in sorted(paths)
    ]
    return _canonical_digest(payload)


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _required_row_string(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise Stage3MomentumError(f"funding {key} is invalid")
    return value


def _required_row_int(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise Stage3MomentumError(f"funding {key} is invalid")
    return value


def _optional_row_int(row: Mapping[str, object], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise Stage3MomentumError(f"funding {key} is invalid")
    return value


def _money_amount(value: str) -> Decimal:
    return Decimal(value.split()[0])


def _named(value: object) -> str:
    name = getattr(value, "name", None)
    return name if isinstance(name, str) and name else str(value)


def _maybe_str(value: object) -> str | None:
    return None if value is None else str(value)


def _maybe_int(value: object) -> int | None:
    return None if value is None else int(cast(int, value))


if __name__ == "__main__":
    raise SystemExit(main())
