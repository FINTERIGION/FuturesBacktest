"""Custom strategy template. Copy or edit for private research."""

import backtrader.indicators as btind

from strategies.base import FuturesStrategyBase


class MyStrategy(FuturesStrategyBase):
    """
    Custom strategy template.

    Usage:
      1. Define the indicators you need in __init__
      2. Implement trading logic in next()
      3. Or add a new file under strategies/ and import it from main.py
      4. Set STRATEGY = MyStrategy in main.py

    Available methods:
      self.buy_signal()         open long
      self.sell_signal()        open short
      self.close_signal()       close position
      self.get_position_size()  current position (positive=long, negative=short)

    Available data:
      self.data / self.weighted     OI-weighted series (datas[0])
      self.contracts['SA2505']      any real contract in the backtest window
      self.get_contract('SA2505')   same, or None if that code was not loaded
      self.data.close[0]            today's weighted close
      self.data.open / high / low / volume / openinterest / settle
    """

    params = (
        # Add strategy parameters here, e.g.:
        # ('fast', 5),
        # ('slow', 20),
    )

    def __init__(self):
        super().__init__()
        # Define indicators here, e.g.:
        # self.fast_ma = btind.SMA(self.data.close, period=self.p.fast)
        # self.slow_ma = btind.SMA(self.data.close, period=self.p.slow)
        pass

    def next(self):
        # Wait if there is a pending order
        if self._pending_order:
            return

        pos = self.get_position_size()

        # -------------------------------------------------------
        # Write your trading logic here.
        # Example: simple price momentum.
        # -------------------------------------------------------
        # if self.data.close[0] > self.data.close[-1]:  # price rose today
        #     if pos <= 0:
        #         if pos < 0:
        #             self.close_signal()
        #         else:
        #             self.buy_signal()
        # else:
        #     if pos > 0:
        #         self.close_signal()
        pass
