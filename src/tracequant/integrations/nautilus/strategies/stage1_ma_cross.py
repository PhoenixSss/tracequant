from __future__ import annotations

from typing import Final

from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model import (
    Bar,
    BarType,
    InstrumentId,
    OmsType,
    OrderSide,
    Quantity,
)
from nautilus_trader.trading import Strategy, StrategyConfig

from tracequant.source_data.stage1_btcusdt import STAGE1_BAR_TYPE, STAGE1_INSTRUMENT_ID

STAGE1_FAST_SMA_PERIOD: Final = 10
STAGE1_SLOW_SMA_PERIOD: Final = 20
STAGE1_TRADE_SIZE: Final = "0.001"


class Stage1MaCross(Strategy):
    def __init__(self) -> None:
        super().__init__(
            StrategyConfig(
                oms_type=OmsType.NETTING,
                use_uuid_client_order_ids=False,
            )
        )
        self._instrument_id = InstrumentId.from_str(STAGE1_INSTRUMENT_ID)
        self._bar_type = BarType.from_str(STAGE1_BAR_TYPE)
        self._trade_size = Quantity.from_str(STAGE1_TRADE_SIZE)
        self._fast = SimpleMovingAverage(STAGE1_FAST_SMA_PERIOD)
        self._slow = SimpleMovingAverage(STAGE1_SLOW_SMA_PERIOD)

    def on_start(self) -> None:
        self.register_indicator_for_bars(self._bar_type, self._fast)
        self.register_indicator_for_bars(self._bar_type, self._slow)
        self.subscribe_bars(self._bar_type)

    def on_bar(self, bar: Bar) -> None:
        if bar.bar_type != self._bar_type:
            return
        if not self.indicators_initialized():
            return
        if self.cache.orders_open(instrument_id=self._instrument_id):
            return
        open_positions = self.cache.positions_open(instrument_id=self._instrument_id)
        if self._fast.value > self._slow.value:
            if not open_positions:
                order = self.order_factory.market(
                    self._instrument_id,
                    OrderSide.BUY,
                    self._trade_size,
                )
                self.submit_order(order)
        elif self._fast.value < self._slow.value:
            for position in open_positions:
                self.close_position(position)

    def on_stop(self) -> None:
        self.close_all_positions(self._instrument_id)
