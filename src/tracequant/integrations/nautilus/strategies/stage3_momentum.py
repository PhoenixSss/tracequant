from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Final, Literal, cast

from nautilus_trader.model import (
    Bar,
    BarType,
    ClientOrderId,
    CryptoPerpetual,
    FundingRateUpdate,
    InstrumentId,
    MarkPriceUpdate,
    OmsType,
    OrderFilled,
    OrderSide,
    PositionChanged,
    PositionClosed,
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


@dataclass
class _ReversalState:
    close_decision: dict[str, object]
    latest_decision: dict[str, object]
    close_order_id: str
    phase: Literal["closing", "flat_confirmed", "opening"] = "closing"
    flat_confirmation_sequence: int | None = None
    flat_confirmation_ts: int | None = None

    def replace_target(self, decision: dict[str, object]) -> None:
        self.latest_decision = decision
        self.close_decision["reversal_open_decision_id"] = decision["decision_id"]


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


def unsettled_orders(
    cache: object,
    *,
    instrument_id: InstrumentId | None = None,
) -> tuple[object, ...]:
    """Return every non-terminal order, including local and in-flight states."""
    orders_method = getattr(cache, "orders", None)
    if not callable(orders_method):
        raise Stage3MomentumError("Nautilus cache cannot prove order state")
    orders = orders_method(instrument_id=instrument_id)
    result: list[object] = []
    for order in orders:
        closed = getattr(order, "is_closed", None)
        closed = closed() if callable(closed) else closed
        if not isinstance(closed, bool):
            raise Stage3MomentumError("Nautilus order has unknown terminal state")
        if not closed:
            result.append(order)
    return tuple(result)


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
        self._reversals: dict[str, _ReversalState] = {}
        self._event_sequence = 0
        self.fill_event_sequences: dict[tuple[str, str], int] = {}

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

    def on_order_filled(self, event: OrderFilled) -> None:
        try:
            key = (str(event.client_order_id), str(event.trade_id))
            if key in self.fill_event_sequences:
                raise Stage3MomentumError("duplicate native fill identity")
            self.fill_event_sequences[key] = self._next_event_sequence()
            self._observe_reversal_confirmation(
                str(event.instrument_id),
                event_ts=int(event.ts_event),
                confirmation_kind=type(event).__name__,
            )
        except Exception as exc:
            self.fatal_error = f"{type(exc).__name__}: {exc}"
            raise

    def on_position_changed(self, event: PositionChanged) -> None:
        try:
            self._observe_reversal_confirmation(
                str(event.instrument_id),
                event_ts=int(event.ts_event),
                confirmation_kind=type(event).__name__,
            )
        except Exception as exc:
            self.fatal_error = f"{type(exc).__name__}: {exc}"
            raise

    def on_position_closed(self, event: PositionClosed) -> None:
        try:
            self._observe_reversal_confirmation(
                str(event.instrument_id),
                event_ts=int(event.ts_event),
                confirmation_kind=type(event).__name__,
            )
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
            self._advance_confirmed_reversal(
                instrument_id,
                execution_ts=decision_ts,
            )
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
            if unsettled_orders(self.cache, instrument_id=native_id):
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
            "decision_sequence": self._next_event_sequence(),
            "decision_ts": decision_ts,
            "delta_qty": str(delta),
            "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
            "instrument_id": instrument_id,
            "order_ids": [],
            "order_intents": [],
            "reason": "target_aligned",
            "ret_24h": repr(ret_24h),
            "signal": signal,
            "target_qty": str(target),
        }
        reversal = self._reversals.get(instrument_id)
        if reversal is not None and reversal.phase != "opening":
            record["action"] = "reversal_target_update"
            record["reason"] = (
                "no_in_window_next_bar"
                if decision_ts + HOUR_NS >= self.parameters.evaluation_end_ns
                else (
                    "awaiting_flat_confirmation"
                    if reversal.phase == "closing"
                    else "awaiting_first_executable_event_after_flat"
                )
            )
            reversal.replace_target(record)
            self.decisions.append(record)
            return
        # The evaluation window is half-open. A decision without an in-window
        # B_1 is observable, but it cannot create an order or an outside fill.
        if decision_ts + HOUR_NS >= self.parameters.evaluation_end_ns:
            record["reason"] = "no_in_window_next_bar"
            self.decisions.append(record)
            return
        if unsettled_orders(self.cache, instrument_id=native_id):
            # A direct order submitted earlier in this on_bar callback settles
            # only after the callback. Preserve the newer target for its own
            # B_1; _execute_pending will reconcile it against the then-current
            # Nautilus position instead of dropping the decision here.
            record["action"] = "queued"
            record["queue_reason"] = "unsettled_order"
            record["reason"] = "awaiting_b1"
            self._pending[instrument_id] = record
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
        if unsettled_orders(self.cache, instrument_id=native_id):
            record["action"] = "none"
            record["reason"] = "b1_unsettled_order"
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
        record["action"] = "reversal_close" if reversing else "submit_delta"
        record["reason"] = "submitted"
        record["submitted_qty"] = str(quantity)
        record["submitted_side"] = side.name
        if reversing:
            reversal = _ReversalState(
                close_decision=record,
                latest_decision=record,
                close_order_id=str(order.client_order_id),
            )
            self._reversals[instrument_id] = reversal
            self._record_order_intent(
                record,
                order_id=str(order.client_order_id),
                leg="reversal_close",
                expected_fill_ts=execution_ts,
            )
        else:
            self._record_order_intent(
                record,
                order_id=str(order.client_order_id),
                leg="direct",
                expected_fill_ts=execution_ts,
            )
        self.submit_order(order)

    @property
    def pending_reversal_count(self) -> int:
        return len(self._reversals)

    def _observe_reversal_confirmation(
        self,
        instrument_id: str,
        *,
        event_ts: int,
        confirmation_kind: str,
    ) -> None:
        reversal = self._reversals.get(instrument_id)
        if reversal is None or reversal.phase != "closing":
            return
        native_id = self._instrument_ids[instrument_id]
        if unsettled_orders(self.cache, instrument_id=native_id):
            return
        close_order = self.cache.order(ClientOrderId.from_str(reversal.close_order_id))
        if (
            close_order is None
            or _named(getattr(close_order, "status")) != "FILLED"
            or getattr(close_order, "filled_qty") != getattr(close_order, "quantity")
        ):
            return
        positions = self.cache.positions_open(instrument_id=native_id)
        if len(positions) > 1:
            raise Stage3MomentumError("NETTING cache has multiple open positions")
        if positions:
            return
        reversal.phase = "flat_confirmed"
        reversal.flat_confirmation_sequence = self._next_event_sequence()
        reversal.flat_confirmation_ts = event_ts
        reversal.close_decision["flat_confirmation_kind"] = confirmation_kind
        reversal.close_decision["flat_confirmation_sequence"] = (
            reversal.flat_confirmation_sequence
        )
        reversal.close_decision["flat_confirmation_ts"] = event_ts
        self._submit_reversal_open(instrument_id, execution_ts=event_ts)

    def _advance_confirmed_reversal(
        self,
        instrument_id: str,
        *,
        execution_ts: int,
    ) -> None:
        reversal = self._reversals.get(instrument_id)
        if reversal is not None and reversal.phase == "flat_confirmed":
            self._submit_reversal_open(instrument_id, execution_ts=execution_ts)

    def _submit_reversal_open(
        self,
        instrument_id: str,
        *,
        execution_ts: int,
    ) -> None:
        reversal = self._reversals.get(instrument_id)
        if reversal is None or reversal.phase != "flat_confirmed":
            return
        record = reversal.latest_decision
        decision_ts = cast(int, record["decision_ts"])
        if decision_ts + HOUR_NS >= self.parameters.evaluation_end_ns:
            record["action"] = "none"
            record["reason"] = "no_in_window_reversal_open"
            self._reversals.pop(instrument_id, None)
            return
        if execution_ts <= decision_ts:
            record["reason"] = "awaiting_first_event_after_updated_decision"
            return
        native_id = self._instrument_ids[instrument_id]
        if unsettled_orders(self.cache, instrument_id=native_id):
            record["reason"] = "flat_confirmation_has_unsettled_order"
            return
        positions = self.cache.positions_open(instrument_id=native_id)
        if len(positions) > 1:
            raise Stage3MomentumError("NETTING cache has multiple open positions")
        if positions:
            record["reason"] = "flat_confirmation_has_position"
            return
        target = Decimal(cast(str, record["target_qty"]))
        if target == 0:
            record["action"] = "none"
            record["reason"] = "latest_reversal_target_is_flat"
            self._reversals.pop(instrument_id, None)
            return
        instrument = self._instruments[instrument_id]
        absolute = abs(target)
        if absolute < minimum_order_quantity(instrument):
            raise Stage3MomentumError("reversal target is below minimum")
        quantity = _instrument_quantity(instrument, absolute)
        if (
            quantity.as_decimal() != absolute
            or quantity.precision > instrument.size_precision
        ):
            raise Stage3MomentumError("reversal target violates instrument precision")
        side = OrderSide.BUY if target > 0 else OrderSide.SELL
        order = self.order_factory.market(
            native_id,
            side,
            quantity,
            reduce_only=False,
        )
        reversal.phase = "opening"
        if record is not reversal.close_decision:
            record["action"] = "reversal_open"
        record["reason"] = "submitted_after_flat_confirmation"
        record["current_qty_at_execution"] = "0"
        record["delta_qty_at_execution"] = str(target)
        record["flat_confirmation_sequence"] = reversal.flat_confirmation_sequence
        record["flat_confirmation_ts"] = reversal.flat_confirmation_ts
        record["submitted_qty"] = str(quantity)
        record["submitted_side"] = side.name
        self._record_order_intent(
            record,
            order_id=str(order.client_order_id),
            leg="reversal_open",
            expected_fill_ts=execution_ts,
            flat_confirmation_sequence=reversal.flat_confirmation_sequence,
        )
        reversal.close_decision["reversal_open_decision_id"] = record["decision_id"]
        reversal.close_decision["reversal_open_order_id"] = str(order.client_order_id)
        self.submit_order(order)
        if self._reversals.get(instrument_id) is reversal:
            self._reversals.pop(instrument_id, None)

    def _record_order_intent(
        self,
        record: dict[str, object],
        *,
        order_id: str,
        leg: Literal["direct", "reversal_close", "reversal_open"],
        expected_fill_ts: int,
        flat_confirmation_sequence: int | None = None,
    ) -> None:
        submit_sequence = self._next_event_sequence()
        cast(list[str], record["order_ids"]).append(order_id)
        intent: dict[str, object] = {
            "expected_fill_ts": expected_fill_ts,
            "leg": leg,
            "order_id": order_id,
            "submit_sequence": submit_sequence,
        }
        if flat_confirmation_sequence is not None:
            intent["flat_confirmation_sequence"] = flat_confirmation_sequence
        cast(list[dict[str, object]], record["order_intents"]).append(intent)

    def _next_event_sequence(self) -> int:
        self._event_sequence += 1
        return self._event_sequence


def _signed_position_quantity(position: object) -> Decimal:
    quantity = cast(Quantity, getattr(position, "quantity")).as_decimal()
    if getattr(position, "is_short"):
        return -quantity
    if getattr(position, "is_long"):
        return quantity
    raise Stage3MomentumError("open position has unknown side")


def _instrument_quantity(instrument: CryptoPerpetual, absolute: Decimal) -> Quantity:
    return Quantity.from_str(f"{absolute:.{instrument.size_precision}f}")


def _named(value: object) -> str:
    name = getattr(value, "name", None)
    return name if isinstance(name, str) and name else str(value)


def _decision_id(
    instrument_id: str,
    decision_ts: int,
    signal: Signal,
    target: Decimal,
    close: Decimal,
) -> str:
    value = f"{instrument_id}|{decision_ts}|{signal}|{target}|{close}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
