"""Dual moving-average crossover example strategy."""

import backtrader.indicators as btind

from strategies.base import FuturesStrategyBase


class DoubleMaStrategy(FuturesStrategyBase):
    """
    Dual moving-average trend-following strategy (example).

    Logic:
      - Fast MA crosses above slow MA -> go long
      - Fast MA crosses below slow MA -> go short
      - On a reverse signal, close first, then open new position (executes next bar)
    """

    params = (
        ('fast_period', 5),
        ('slow_period', 20),
    )

    def __init__(self):
        super().__init__()
        self.fast_ma = btind.SMA(self.data.close, period=self.p.fast_period)
        self.slow_ma = btind.SMA(self.data.close, period=self.p.slow_period)
        self.crossover = btind.CrossOver(self.fast_ma, self.slow_ma)

    def next(self):
        # Wait if there is a pending order
        if self._pending_order:
            return

        pos = self.get_position_size()

        if self.crossover > 0:          # Golden cross -> go long
            if pos < 0:
                self.close_signal()     # Close short first
            elif pos == 0:
                self.buy_signal()

        elif self.crossover < 0:        # Death cross -> go short
            if pos > 0:
                self.close_signal()     # Close long first
            elif pos == 0:
                self.sell_signal()
