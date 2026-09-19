from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Final, Literal, cast

from nautilus_trader.model import (
    Bar,
    BarType,
    CryptoPerpetual,
    FundingRateUpdate,
    InstrumentId,
    MarkPriceUpdate,
    OmsType,
    OrderSide,
    Quantity,
)
from nautilus_trader.trading import Strategy, StrategyConfig

from tracequant.research.stage3_features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_DIGEST,
    HOUR_NS,
    BarProjection,
    FundingProjection,
    IncrementalFeatureState,
    MarkProjection,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_INSTRUMENT_IDS,
    stage2_bar_type_str,
)

MOMENTUM_LOOKBACK_HOURS: Final = 24


class _FixedDecimals:
    MOMENTUM_THRESHOLD: Final = Decimal("0.005")
    MOMENTUM_TARGET_NOTIONAL_USDT: Final = Decimal("10000")
    BASE_MAKER_FEE: Final = Decimal("0.0002")
    BASE_TAKER_FEE: Final = Decimal("0.0004")


MOMENTUM_THRESHOLD: Final = _FixedDecimals.MOMENTUM_THRESHOLD
MOMENTUM_TARGET_NOTIONAL_USDT: Final = _FixedDecimals.MOMENTUM_TARGET_NOTIONAL_USDT
BASE_MAKER_FEE: Final = _FixedDecimals.BASE_MAKER_FEE
BASE_TAKER_FEE: Final = _FixedDecimals.BASE_TAKER_FEE

_RET_24H_INDEX: Final = 2


class Stage3MomentumError(ValueError):
    """Raised when the fixed Stage 3 momentum contract cannot be executed."""


@dataclass(frozen=True)
class Stage3MomentumParameters:
    """Typed, fixed parameters shared by the runner and its manifest."""

    evaluation_start_ns: int
    evaluation_end_ns: int
    instrument_ids: tuple[str, ...] = STAGE2_INSTRUMENT_IDS
    lookback_hours: int = MOMENTUM_LOOKBACK_HOURS
    threshold: Decimal = MOMENTUM_THRESHOLD
    target_notional_usdt: Decimal = MOMENTUM_TARGET_NOTIONAL_USDT
    maker_fee: Decimal = BASE_MAKER_FEE
    taker_fee: Decimal = BASE_TAKER_FEE

    def validate(self) -> None:
        if FEATURE_NAMES[_RET_24H_INDEX] != "ret_24h":
            raise Stage3MomentumError(
                "Stage 3 feature order no longer matches momentum"
            )
        if self.instrument_ids != STAGE2_INSTRUMENT_IDS:
            raise Stage3MomentumError("momentum instruments do not match Stage 3")
        if self.lookback_hours != MOMENTUM_LOOKBACK_HOURS:
            raise Stage3MomentumError("momentum lookback is not the frozen 24h")
        if self.threshold != MOMENTUM_THRESHOLD:
            raise Stage3MomentumError("momentum threshold is not the frozen 0.5%")
        if self.target_notional_usdt != MOMENTUM_TARGET_NOTIONAL_USDT:
            raise Stage3MomentumError("target notional is not the frozen 10000 USDT")
        if self.maker_fee != BASE_MAKER_FEE or self.taker_fee != BASE_TAKER_FEE:
            raise Stage3MomentumError("momentum fees do not match the frozen base fees")
        if self.evaluation_end_ns <= self.evaluation_start_ns:
            raise Stage3MomentumError("momentum evaluation window is inverted")


Signal = Literal["long", "flat", "short"]


def momentum_signal(ret_24h: float, threshold: Decimal) -> Signal:
    value = Decimal(str(ret_24h))
    if value > threshold:
        return "long"
    if value < -threshold:
        return "short"
    return "flat"


def target_quantity(
    *,
    signal: Signal,
    close: Decimal,
    instrument: CryptoPerpetual,
    target_notional: Decimal,
) -> Decimal:
    """Return the signed target, floored toward zero on the frozen increment."""
    if signal == "flat":
        return Decimal(0)
    if close <= 0 or target_notional <= 0:
        raise Stage3MomentumError("momentum sizing inputs must be positive")
    increment = instrument.size_increment.as_decimal()
    if increment <= 0:
        raise Stage3MomentumError("instrument size increment must be positive")
    units = (target_notional / close / increment).to_integral_value(rounding=ROUND_DOWN)
    absolute = units * increment
    if absolute <= 0:
        raise Stage3MomentumError("target quantity is below one size increment")
    native_quantity = _instrument_quantity(instrument, absolute)
    native = native_quantity.as_decimal()
    if native != absolute or native_quantity.precision > instrument.size_precision:
        raise Stage3MomentumError("target quantity violates instrument precision")
    if instrument.min_quantity is not None:
        minimum = instrument.min_quantity.as_decimal()
        if absolute < minimum:
            raise Stage3MomentumError("target quantity is below instrument minimum")
    if instrument.max_quantity is not None:
        maximum = instrument.max_quantity.as_decimal()
        if absolute > maximum:
            raise Stage3MomentumError("target quantity exceeds instrument maximum")
    return absolute if signal == "long" else -absolute


def minimum_order_quantity(instrument: CryptoPerpetual) -> Decimal:
    increment = instrument.size_increment.as_decimal()
    if instrument.min_quantity is None:
        return increment
    return max(increment, instrument.min_quantity.as_decimal())


class Stage3MomentumStrategy(Strategy):
    """BTC/ETH 24h momentum Strategy using only Nautilus trading state."""

    def __init__(self, parameters: Stage3MomentumParameters) -> None:
        parameters.validate()
        super().__init__(
            StrategyConfig(
                oms_type=OmsType.NETTING,
                use_uuid_client_order_ids=False,
            )
        )
        self.parameters = parameters
        self.decisions: list[dict[str, object]] = []
        self.fatal_error: str | None = None
        self._instrument_ids = {
            value: InstrumentId.from_str(value) for value in parameters.instrument_ids
        }
        self._bar_types = {
            value: BarType.from_str(stage2_bar_type_str(value, "1h"))
            for value in parameters.instrument_ids
        }
        self._feature_states = {
            value: IncrementalFeatureState(value) for value in parameters.instrument_ids
        }
        self._instruments: dict[str, CryptoPerpetual] = {}
        self._pending: dict[str, dict[str, object]] = {}

    def on_start(self) -> None:
        try:
            self._start()
        except Exception as exc:
            self.fatal_error = f"{type(exc).__name__}: {exc}"
            raise

    def _start(self) -> None:
        for value in self.parameters.instrument_ids:
            native_id = self._instrument_ids[value]
            instrument = self.cache.instrument(native_id)
            if not isinstance(instrument, CryptoPerpetual):
                raise Stage3MomentumError(
                    "momentum instrument is not a Nautilus CryptoPerpetual"
                )
            if (
                instrument.maker_fee != self.parameters.maker_fee
                or instrument.taker_fee != self.parameters.taker_fee
            ):
                raise Stage3MomentumError(
                    "effective instrument fees are not explicitly bound"
                )
            self._instruments[value] = instrument
            self.subscribe_mark_prices(native_id)
            self.subscribe_funding_rates(native_id)
            self.subscribe_bars(self._bar_types[value])

    def on_mark_price(self, event: MarkPriceUpdate) -> None:
        try:
            instrument_id = str(event.instrument_id)
            state = self._feature_states.get(instrument_id)
            if state is None:
                raise Stage3MomentumError("mark instrument is outside Stage 3")
            state.push_mark(
                MarkProjection(
                    instrument_id=instrument_id,
                    value=str(event.value),
                    ts_event=int(event.ts_event),
                )
            )
        except Exception as exc:
            self.fatal_error = f"{type(exc).__name__}: {exc}"
            raise

    def on_funding_rate(self, event: FundingRateUpdate) -> None:
        try:
            instrument_id = str(event.instrument_id)
            state = self._feature_states.get(instrument_id)
            if state is None:
                raise Stage3MomentumError("funding instrument is outside Stage 3")
            state.push_funding(
                FundingProjection(
                    instrument_id=instrument_id,
                    rate=str(event.rate),
                    ts_event=int(event.ts_event),
                )
            )
        except Exception as exc:
            self.fatal_error = f"{type(exc).__name__}: {exc}"
            raise

    def on_bar(self, bar: Bar) -> None:
        try:
            self._handle_bar(bar)
        except Exception as exc:
            self.fatal_error = f"{type(exc).__name__}: {exc}"
            raise

    def _handle_bar(self, bar: Bar) -> None:
        instrument_id = str(bar.bar_type.instrument_id)
        state = self._feature_states.get(instrument_id)
        if state is None or bar.bar_type != self._bar_types.get(instrument_id):
            raise Stage3MomentumError("decision bar is outside the Stage 3 contract")
        decision_ts = int(bar.ts_event)
        tradable = (
            self.parameters.evaluation_start_ns
            <= decision_ts
            < self.parameters.evaluation_end_ns
        )
        if tradable:
            self._execute_pending(instrument_id, execution_ts=decision_ts)
        observation = state.push_bar(
            BarProjection(
                instrument_id=instrument_id,
                bar_type=str(bar.bar_type),
                open=str(bar.open),
                high=str(bar.high),
                low=str(bar.low),
                close=str(bar.close),
                volume=str(bar.volume),
                ts_event=decision_ts,
            ),
            tradable=tradable,
        )
        if not tradable:
            return
        if observation.status != "ready":
            raise Stage3MomentumError(
                "first tradable momentum decision is not feature-ready"
            )
        values = observation.require_ready()
        signal = momentum_signal(values[_RET_24H_INDEX], self.parameters.threshold)
        instrument = self._instruments[instrument_id]
        target = target_quantity(
            signal=signal,
            close=Decimal(str(bar.close)),
            instrument=instrument,
            target_notional=self.parameters.target_notional_usdt,
        )
        self._queue_decision(
            instrument_id=instrument_id,
            decision_ts=decision_ts,
            close=Decimal(str(bar.close)),
            ret_24h=values[_RET_24H_INDEX],
            signal=signal,
            target=target,
        )

    def on_stop(self) -> None:
        for native_id in self._instrument_ids.values():
            if self.cache.orders_open(instrument_id=native_id):
                self.cancel_all_orders(native_id)

    def _queue_decision(
        self,
        *,
        instrument_id: str,
        decision_ts: int,
        close: Decimal,
        ret_24h: float,
        signal: Signal,
        target: Decimal,
    ) -> None:
        native_id = self._instrument_ids[instrument_id]
        positions = self.cache.positions_open(instrument_id=native_id)
        if len(positions) > 1:
            raise Stage3MomentumError("NETTING cache has multiple open positions")
        current = _signed_position_quantity(positions[0]) if positions else Decimal(0)
        delta = target - current
        record: dict[str, object] = {
            "action": "none",
            "close": str(close),
            "current_qty": str(current),
            "decision_id": _decision_id(
                instrument_id, decision_ts, signal, target, close
            ),
            "decision_ts": decision_ts,
            "delta_qty": str(delta),
            "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
            "instrument_id": instrument_id,
            "order_ids": [],
            "reason": "target_aligned",
            "ret_24h": repr(ret_24h),
            "signal": signal,
            "target_qty": str(target),
        }
        # The evaluation window is half-open. A decision without an in-window
        # B_1 is observable, but it cannot create an order or an outside fill.
        if decision_ts + HOUR_NS >= self.parameters.evaluation_end_ns:
            record["reason"] = "no_in_window_next_bar"
            self.decisions.append(record)
            return
        if self.cache.orders_open(instrument_id=native_id):
            record["reason"] = "open_order_pending"
            self.decisions.append(record)
            return
        if delta == 0:
            self.decisions.append(record)
            return

        instrument = self._instruments[instrument_id]
        if abs(delta) < minimum_order_quantity(instrument):
            record["reason"] = "delta_below_minimum"
            self.decisions.append(record)
            return
        record["action"] = "queued"
        record["reason"] = "awaiting_b1"
        self._pending[instrument_id] = record
        self.decisions.append(record)

    def _execute_pending(self, instrument_id: str, *, execution_ts: int) -> None:
        record = self._pending.pop(instrument_id, None)
        if record is None:
            return
        native_id = self._instrument_ids[instrument_id]
        record["execution_ts"] = execution_ts
        if self.cache.orders_open(instrument_id=native_id):
            record["action"] = "none"
            record["reason"] = "b1_open_order_unavailable"
            return
        positions = self.cache.positions_open(instrument_id=native_id)
        if len(positions) > 1:
            raise Stage3MomentumError("NETTING cache has multiple open positions")
        current = _signed_position_quantity(positions[0]) if positions else Decimal(0)
        target = Decimal(cast(str, record["target_qty"]))
        delta = target - current
        record["current_qty_at_execution"] = str(current)
        record["delta_qty_at_execution"] = str(delta)
        if delta == 0:
            record["action"] = "none"
            record["reason"] = "target_aligned_at_b1"
            return
        instrument = self._instruments[instrument_id]
        reversing = current != 0 and target != 0 and (current > 0) != (target > 0)
        executable_delta = -current if reversing else delta
        absolute = abs(executable_delta)
        if absolute < minimum_order_quantity(instrument):
            record["action"] = "none"
            record["reason"] = "delta_below_minimum_at_b1"
            return
        quantity = _instrument_quantity(instrument, absolute)
        if (
            quantity.as_decimal() != absolute
            or quantity.precision > instrument.size_precision
        ):
            raise Stage3MomentumError("order delta violates instrument precision")
        side = OrderSide.BUY if executable_delta > 0 else OrderSide.SELL
        reduce_only = current != 0 and (
            reversing or target == 0 or abs(target) < abs(current)
        )
        order = self.order_factory.market(
            native_id,
            side,
            quantity,
            reduce_only=reduce_only,
        )
        self.submit_order(order)
        record["action"] = "reversal_close" if reversing else "submit_delta"
        record["order_ids"] = [str(order.client_order_id)]
        record["reason"] = "submitted"
        record["submitted_qty"] = str(quantity)
        record["submitted_side"] = side.name


def _signed_position_quantity(position: object) -> Decimal:
    quantity = cast(Quantity, getattr(position, "quantity")).as_decimal()
    if getattr(position, "is_short"):
        return -quantity
    if getattr(position, "is_long"):
        return quantity
    raise Stage3MomentumError("open position has unknown side")


def _instrument_quantity(instrument: CryptoPerpetual, absolute: Decimal) -> Quantity:
    return Quantity.from_str(f"{absolute:.{instrument.size_precision}f}")


def _decision_id(
    instrument_id: str,
    decision_ts: int,
    signal: Signal,
    target: Decimal,
    close: Decimal,
) -> str:
    value = f"{instrument_id}|{decision_ts}|{signal}|{target}|{close}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
