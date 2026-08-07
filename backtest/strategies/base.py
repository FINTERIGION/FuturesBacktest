"""Futures strategy base class for all SA backtest strategies."""

import backtrader as bt


class FuturesStrategyBase(bt.Strategy):
    """
    Futures strategy base class.

    Provides common helpers such as signal logging and trade logging.
    Subclasses implement the actual logic in `next()`.

    The backtest engine injects the following params via cerebro.addstrategy():
      - contract_multiplier : contract multiplier (default 20 tons/lot)
      - trade_size          : lots per trade (default 1)
      - margin_rate         : margin ratio (default 0.10)

    Subclasses can directly use:
      self.buy_signal()    open long
      self.sell_signal()   open short
      self.close_signal()  close position
    """

    params = (
        ('contract_multiplier', 20),
        ('trade_size', 1),
        ('margin_rate', 0.10),
        ('printlog', False),
    )

    def __init__(self):
        # Signal list (used by the plotting module): [(date, price, direction), ...]
        # direction: 'buy' | 'sell' | 'close'
        self.signal_log = []
        self._pending_order = None

    # ------------------------------------------------------------------
    # Lifecycle callbacks
    # ------------------------------------------------------------------

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        date = self.datas[0].datetime.date(0)

        if order.status == order.Completed:
            direction = 'buy' if order.isbuy() else 'sell'
            self.signal_log.append({
                'date': date,
                'price': order.executed.price,
                'direction': direction,
                'size': order.executed.size,
                'comm': order.executed.comm,
            })
            if self.p.printlog:
                print(
                    f"  [{date}] Filled: {'BUY' if order.isbuy() else 'SELL'} "
                    f"price={order.executed.price:.2f} "
                    f"size={order.executed.size} "
                    f"commission={order.executed.comm:.2f}"
                )
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            if self.p.printlog:
                print(f"  [{date}] Order not filled: {order.getstatusname()}")

        self._pending_order = None

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        if self.p.printlog:
            date = self.datas[0].datetime.date(0)
            print(f"  [{date}] Trade closed: pnl={trade.pnl:.2f}  net_pnl={trade.pnlcomm:.2f}")

    # ------------------------------------------------------------------
    # Utility methods (for subclasses)
    # ------------------------------------------------------------------

    def buy_signal(self, size=None):
        """Open long at market (futures long entry)"""
        if self._pending_order is None:
            self._pending_order = self.buy(size=size if size is not None else self.p.trade_size)

    def sell_signal(self, size=None):
        """Open short at market (futures short entry)"""
        if self._pending_order is None:
            self._pending_order = self.sell(size=size if size is not None else self.p.trade_size)

    def close_signal(self):
        """Close the current position"""
        if self._pending_order is None:
            self._pending_order = self.close()

    def get_position_size(self) -> int:
        """Return the current net position size (positive=long, negative=short, 0=flat)"""
        return self.position.size
