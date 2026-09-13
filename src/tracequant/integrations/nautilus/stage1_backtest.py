from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LoggerConfig, LogLevel
from nautilus_trader.model import (
    AccountType,
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Money,
    OmsType,
    Price,
    Quantity,
    Symbol,
    TraderId,
    Venue,
)

from tracequant.integrations.nautilus import (
    EXPECTED_VERSION,
    UPSTREAM_RELEASE_IDENTITY,
    distribution_version,
)
from tracequant.integrations.nautilus.stage1_btcusdt import (
    read_selected_btcusdt_history,
)
from tracequant.integrations.nautilus.strategies.stage1_ma_cross import (
    STAGE1_FAST_SMA_PERIOD,
    STAGE1_SLOW_SMA_PERIOD,
    STAGE1_TRADE_SIZE,
    Stage1MaCross,
)
from tracequant.source_data.stage1_btcusdt import STAGE1_BAR_TYPE, STAGE1_INSTRUMENT_ID

STAGE1_STARTING_USDT: Final = "100000"
STAGE1_MAKER_FEE: Final = "0.0002"
STAGE1_TAKER_FEE: Final = "0.0004"
STAGE1_DEFAULT_LEVERAGE: Final = "1"
STAGE1_TRADER_ID: Final = "TRACEQUANT-001"
STAGE1_VENUE: Final = "BINANCE"
STAGE1_RUN_ENVIRONMENT: Final = "offline"
STAGE1_RUN_CODE: Final = "stage1-ma-cross"
STAGE1_RUN_LOCK: Final = "stage1"
STAGE1_RUN_MODE: Final = "backtest"
STAGE1_RUN_ID: Final = "stage1-offline"
OFFLINE_BACKTEST_ONLY: Final = True
LIVE_NOT_APPROVED: Final = True


class Stage1BacktestError(ValueError):
    """Raised when the stage 1 offline backtest cannot run or report."""


@dataclass(frozen=True)
class Stage1BacktestReports:
    orders: tuple[dict[str, object], ...]
    fills: tuple[dict[str, object], ...]
    positions: tuple[dict[str, object], ...]
    account: dict[str, object]
    summary: dict[str, object]


@dataclass(frozen=True)
class Stage1BacktestOutcome:
    reports: Stage1BacktestReports
    catalog_checksum_sha256: str
    partition: Path
    nautilus_version: str


def stage1_run_partition(run_root: Path) -> Path:
    if not run_root.is_absolute():
        raise Stage1BacktestError("run_root must be an absolute path")
    if not run_root.is_dir():
        raise Stage1BacktestError("run_root must be an existing directory")
    return (
        run_root
        / STAGE1_RUN_ENVIRONMENT
        / STAGE1_RUN_CODE
        / "nautilus"
        / UPSTREAM_RELEASE_IDENTITY
        / STAGE1_RUN_LOCK
        / STAGE1_RUN_MODE
        / STAGE1_RUN_ID
    )


def require_locked_nautilus_runtime() -> str:
    actual = distribution_version()
    if actual != EXPECTED_VERSION:
        raise Stage1BacktestError(
            "installed Nautilus version does not match the locked runtime"
        )
    return actual


def stage1_btcusdt_perpetual() -> CryptoPerpetual:
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(STAGE1_INSTRUMENT_ID),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.10"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        maker_fee=Decimal(STAGE1_MAKER_FEE),
        taker_fee=Decimal(STAGE1_TAKER_FEE),
    )


def canonical_business_result(outcome: Stage1BacktestOutcome) -> dict[str, object]:
    return {
        "account": outcome.reports.account,
        "catalog_checksum_sha256": outcome.catalog_checksum_sha256,
        "fills": list(outcome.reports.fills),
        "nautilus_version": outcome.nautilus_version,
        "orders": list(outcome.reports.orders),
        "positions": list(outcome.reports.positions),
        "runtime_identity": UPSTREAM_RELEASE_IDENTITY,
        "summary": dict(sorted(outcome.reports.summary.items())),
        "trade_size": STAGE1_TRADE_SIZE,
        "fast_sma_period": STAGE1_FAST_SMA_PERIOD,
        "slow_sma_period": STAGE1_SLOW_SMA_PERIOD,
        "starting_usdt": STAGE1_STARTING_USDT,
        "maker_fee": STAGE1_MAKER_FEE,
        "taker_fee": STAGE1_TAKER_FEE,
    }


def run_stage1_offline_backtest(
    catalog_root: Path, run_root: Path
) -> Stage1BacktestOutcome:
    nautilus_version = require_locked_nautilus_runtime()
    bars, provenance = read_selected_btcusdt_history(catalog_root)
    partition = stage1_run_partition(run_root)
    if partition.exists() and any(partition.iterdir()):
        raise Stage1BacktestError(
            "run partition already exists and must not be mutated"
        )
    venue = Venue(STAGE1_VENUE)
    usdt = Currency.from_str("USDT")
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId.from_str(STAGE1_TRADER_ID),
            bypass_logging=True,
            run_analysis=False,
            logging=LoggerConfig(bypass_logging=True, stdout_level=LogLevel.OFF),
        )
    )
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money.from_str(f"{STAGE1_STARTING_USDT} USDT")],
        base_currency=usdt,
        default_leverage=Decimal(STAGE1_DEFAULT_LEVERAGE),
        bar_execution=True,
        use_random_ids=False,
    )
    engine.add_instrument(stage1_btcusdt_perpetual())
    engine.add_data(list(bars))
    engine.add_strategy(Stage1MaCross())
    try:
        engine.run()
        reports = _collect_reports(engine, venue, usdt)
    finally:
        engine.dispose()
    outcome = Stage1BacktestOutcome(
        reports=reports,
        catalog_checksum_sha256=provenance.checksum_sha256,
        partition=partition,
        nautilus_version=nautilus_version,
    )
    _write_reports(outcome)
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 1 offline Nautilus MA-cross backtest"
    )
    parser.add_argument("--catalog-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args(argv)
    outcome = run_stage1_offline_backtest(args.catalog_root, args.run_root)
    json.dump(canonical_business_result(outcome), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _collect_reports(
    engine: BacktestEngine, venue: Venue, usdt: Currency
) -> Stage1BacktestReports:
    orders = tuple(_order_record(order, usdt) for order in engine.cache.orders())
    fills = tuple(
        _fill_record(order, usdt)
        for order in engine.cache.orders()
        if _is_filled(order)
    )
    positions = tuple(
        _position_record(position) for position in engine.cache.positions()
    )
    account = engine.cache.account_for_venue(venue)
    if account is None:
        raise Stage1BacktestError("account report is missing")
    total = account.balance_total(usdt)
    free = account.balance_free(usdt)
    locked = account.balance_locked(usdt)
    result = engine.get_result()
    summary: dict[str, object] = {
        str(key): str(value) for key, value in dict(result.summary).items()
    }
    return Stage1BacktestReports(
        orders=orders,
        fills=fills,
        positions=positions,
        account={
            "id": str(account.id),
            "type": _named(account.account_type),
            "base_currency": str(account.base_currency),
            "total": str(total),
            "free": str(free),
            "locked": str(locked),
        },
        summary=summary,
    )


def _write_reports(outcome: Stage1BacktestOutcome) -> None:
    partition = outcome.partition
    partition.mkdir(parents=True, exist_ok=True)
    payloads = {
        "orders.json": list(outcome.reports.orders),
        "fills.json": list(outcome.reports.fills),
        "positions.json": list(outcome.reports.positions),
        "account.json": outcome.reports.account,
        "summary.json": dict(sorted(outcome.reports.summary.items())),
        "manifest.json": {
            "bar_type": STAGE1_BAR_TYPE,
            "catalog_checksum_sha256": outcome.catalog_checksum_sha256,
            "fast_sma_period": STAGE1_FAST_SMA_PERIOD,
            "instrument_id": STAGE1_INSTRUMENT_ID,
            "live_not_approved": LIVE_NOT_APPROVED,
            "maker_fee": STAGE1_MAKER_FEE,
            "nautilus_version": outcome.nautilus_version,
            "offline_backtest_only": OFFLINE_BACKTEST_ONLY,
            "oms_type": "NETTING",
            "runtime_identity": UPSTREAM_RELEASE_IDENTITY,
            "slow_sma_period": STAGE1_SLOW_SMA_PERIOD,
            "starting_usdt": STAGE1_STARTING_USDT,
            "taker_fee": STAGE1_TAKER_FEE,
            "trade_size": STAGE1_TRADE_SIZE,
            "trader_id": STAGE1_TRADER_ID,
            "venue": STAGE1_VENUE,
        },
    }
    for name, payload in payloads.items():
        (partition / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _order_record(order: object, currency: Currency) -> dict[str, object]:
    return {
        "avg_px": _maybe_str(getattr(order, "avg_px", None)),
        "client_order_id": str(getattr(order, "client_order_id")),
        "commission": _commission(order, currency),
        "filled_qty": str(getattr(order, "filled_qty")),
        "quantity": str(getattr(order, "quantity")),
        "side": _named(getattr(order, "side")),
        "status": _named(getattr(order, "status")),
        "ts_init": int(getattr(order, "ts_init")),
        "ts_last": int(getattr(order, "ts_last")),
    }


def _fill_record(order: object, currency: Currency) -> dict[str, object]:
    return {
        "avg_px": _maybe_str(getattr(order, "avg_px", None)),
        "client_order_id": str(getattr(order, "client_order_id")),
        "commission": _commission(order, currency),
        "filled_qty": str(getattr(order, "filled_qty")),
        "side": _named(getattr(order, "side")),
        "status": _named(getattr(order, "status")),
        "ts_last": int(getattr(order, "ts_last")),
    }


def _commission(order: object, currency: Currency) -> str | None:
    commission = getattr(order, "commission", None)
    if commission is None:
        return None
    value = commission(currency) if callable(commission) else commission
    return _maybe_str(value)


def _position_record(position: object) -> dict[str, object]:
    ts_closed = getattr(position, "ts_closed", None)
    avg_px_close = getattr(position, "avg_px_close", None)
    return {
        "avg_px_close": None if avg_px_close is None else str(avg_px_close),
        "avg_px_open": str(getattr(position, "avg_px_open")),
        "id": str(getattr(position, "id")),
        "peak_qty": str(getattr(position, "peak_qty", getattr(position, "quantity"))),
        "quantity": str(getattr(position, "quantity")),
        "realized_pnl": _maybe_str(getattr(position, "realized_pnl", None)),
        "side": _named(getattr(position, "side")),
        "ts_closed": None if ts_closed is None else int(ts_closed),
        "ts_opened": int(getattr(position, "ts_opened")),
    }


def _is_filled(order: object) -> bool:
    filled = getattr(order, "filled_qty", None)
    return filled is not None and str(filled) not in {"0", "0.000", "0.0"}


def _named(value: object) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(value)


def _maybe_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
