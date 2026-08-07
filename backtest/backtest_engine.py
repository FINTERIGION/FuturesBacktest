"""
Backtest Engine Module
Responsible for:
  - Custom analyzers (equity curve, daily returns, position records)
  - Trade log collection and CSV export
  - Metrics calculation (Sharpe, max drawdown, recovery period, win rate, etc.)
  - Running Cerebro and returning structured results
"""

import os
import math
import datetime
import csv
import backtrader as bt
import backtrader.analyzers as btanalyzers
import pandas as pd
import numpy as np


# ==============================================================================
# Futures cost model and custom analyzers
# ==============================================================================

class FuturesCommissionInfo(bt.CommissionInfo):
    """Commission model for percentage-margined futures contracts.

    Backtrader's ``margin`` parameter is an absolute currency amount per lot,
    not a margin ratio. ``get_margin`` keeps the required margin dynamic as
    the price changes, and the fee is charged on contract notional.
    """

    params = (
        ('margin_rate', 0.10),
    )

    def get_margin(self, price):
        """Return margin per lot: price × multiplier × margin rate."""
        return price * self.p.mult * self.p.margin_rate

    def _getcommission(self, size, price, pseudoexec):
        """Return fee: lots × price × multiplier × commission rate."""
        return abs(size) * price * self.p.mult * self.p.commission


class DailyEquityAnalyzer(bt.Analyzer):
    """
    Records, for every bar: date, total account equity, current position, and daily return.
    """

    def start(self):
        self.equity_records = []      # [(date, equity, position)]
        self._prev_equity = self.strategy.broker.getvalue()

    def next(self):
        dt = self.strategy.datas[0].datetime.date(0)
        equity = self.strategy.broker.getvalue()
        pos = self.strategy.position.size
        daily_return = (equity - self._prev_equity) / self._prev_equity if self._prev_equity else 0.0
        self.equity_records.append({
            'date': dt,
            'equity': equity,
            'position': pos,
            'daily_return': daily_return,
        })
        self._prev_equity = equity

    def get_analysis(self):
        return self.equity_records


class TradeLogAnalyzer(bt.Analyzer):
    """
    Collects the open and close records of every completed trade.
    Fields:
      trade_id, open_date, close_date, direction,
      open_price, close_price, size,
      gross_pnl, commission, net_pnl, margin_used
    """

    def __init__(self):
        self.trades = []
        # trade.ref -> {'direction': str, 'open_price': float, 'close_price': float}
        self._trade_info = {}

    def notify_order(self, order):
        """
        Track direction and execution prices from completed orders.
        First order on a trade = open; subsequent closing order = close.
        """
        if order.status != order.Completed:
            return
        tid = order.tradeid
        if tid not in self._trade_info:
            # First order for this trade -> record open direction, price, and size
            self._trade_info[tid] = {
                'direction':   'long' if order.isbuy() else 'short',
                'open_price':  order.executed.price,
                'close_price': order.executed.price,   # placeholder
                'size':        order.executed.size,
            }
        else:
            # Subsequent (closing) order -> update close price
            self._trade_info[tid]['close_price'] = order.executed.price

    def notify_trade(self, trade):
        if not trade.isclosed:
            return

        tid = trade.tradeid
        info = self._trade_info.pop(tid, {})
        direction   = info.get('direction',   'long')
        open_price  = info.get('open_price',  trade.price)
        close_price = info.get('close_price', trade.price)

        size = abs(info.get('size', trade.size)) or 1

        mult   = self.strategy.p.contract_multiplier
        margin = self.strategy.p.margin_rate

        # Calculate margin from entry price and actual size
        margin_used = round(
            open_price * size * mult * margin,
            4
        )

        self.trades.append({
            'trade_id':    trade.ref,
            'open_date':   bt.num2date(trade.dtopen).date(),
            'close_date':  bt.num2date(trade.dtclose).date(),
            'direction':   direction,
            'open_price':  round(open_price, 4),
            'close_price': round(close_price, 4),
            'size':        size,
            'gross_pnl':   round(trade.pnl, 4),
            'commission':  round(trade.commission, 4),
            'net_pnl':     round(trade.pnlcomm, 4),
            'margin_used': margin_used,
        })

    def get_analysis(self):
        return self.trades


# ==============================================================================
# Backtest engine main class
# ==============================================================================

class BacktestEngine:
    """
    Wraps Cerebro configuration and execution, and produces structured results.

    Parameters
    ----------
    strategy_class : type
        Strategy class (subclass of FuturesStrategyBase).
    data_feed : bt.feeds.PandasData
        A pre-configured data feed.
    config : dict
        Backtest configuration. Keys:
          initial_cash         initial cash (default 100000)
          commission_rate      commission rate (default 0.0002)
          margin_rate          margin ratio (default 0.10)
          contract_multiplier  contract multiplier (default 20)
          trade_size           lots per trade (default 1)
          strategy_params      extra dict passed to the strategy (optional)
          results_dir          output directory (default backtest/results)
          strategy_name        strategy name (used in file naming)
    """

    DEFAULT_CONFIG = {
        'initial_cash':         100_000.0,
        'commission_rate':      0.0002,
        'margin_rate':          0.10,
        'contract_multiplier':  20,
        'trade_size':           1,
        'strategy_params':      {},
        'results_dir':          os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'results'
        ),
        'strategy_name':        'strategy',
    }

    def __init__(self, strategy_class, data_feed, config: dict = None):
        self.strategy_class = strategy_class
        self.data_feed = data_feed
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        os.makedirs(self.config['results_dir'], exist_ok=True)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Run the backtest and return a dict containing:
          cerebro, results, equity_records, trade_logs,
          metrics, log_path
        """
        cerebro = self._build_cerebro()
        print("[BacktestEngine] Starting backtest ...")
        results = cerebro.run()
        strat = results[0]

        equity_records = strat.analyzers.daily_equity.get_analysis()
        trade_logs     = strat.analyzers.trade_log.get_analysis()
        metrics        = self._calc_metrics(equity_records, trade_logs)

        log_path = self._save_trade_log(trade_logs)

        self._print_summary(metrics, log_path)

        return {
            'cerebro':        cerebro,
            'strat':          strat,
            'equity_records': equity_records,
            'trade_logs':     trade_logs,
            'metrics':        metrics,
            'log_path':       log_path,
        }

    # ------------------------------------------------------------------
    # Internal methods: build Cerebro
    # ------------------------------------------------------------------

    def _build_cerebro(self) -> bt.Cerebro:
        cfg = self.config
        cerebro = bt.Cerebro()

        # Data
        cerebro.adddata(self.data_feed)

        # Merge strategy parameters
        strat_params = {
            'contract_multiplier': cfg['contract_multiplier'],
            'trade_size':          cfg['trade_size'],
            'margin_rate':         cfg['margin_rate'],
        }
        strat_params.update(cfg.get('strategy_params', {}))
        cerebro.addstrategy(self.strategy_class, **strat_params)

        # Broker
        cerebro.broker.setcash(cfg['initial_cash'])

        # Futures margin is a percentage of notional; the fee is charged on
        # notional (price × lots × contract multiplier) on every execution.
        comm_info = FuturesCommissionInfo(
            commission=cfg['commission_rate'],
            mult=cfg['contract_multiplier'],
            margin_rate=cfg['margin_rate'],
            margin=1.0,  # ensures futures-like accounting in Backtrader
            commtype=bt.CommissionInfo.COMM_PERC,
            percabs=True,
            stocklike=False,
        )
        cerebro.broker.addcommissioninfo(comm_info)

        # Analyzers
        cerebro.addanalyzer(DailyEquityAnalyzer,  _name='daily_equity')
        cerebro.addanalyzer(TradeLogAnalyzer,     _name='trade_log')
        cerebro.addanalyzer(btanalyzers.SharpeRatio,
                            _name='sharpe',
                            riskfreerate=0.03,
                            annualize=True,
                            timeframe=bt.TimeFrame.Days)
        cerebro.addanalyzer(btanalyzers.DrawDown,  _name='drawdown')
        cerebro.addanalyzer(btanalyzers.Returns,   _name='returns')
        cerebro.addanalyzer(btanalyzers.TradeAnalyzer, _name='trade_analyzer')

        return cerebro

    # ------------------------------------------------------------------
    # Internal methods: compute metrics
    # ------------------------------------------------------------------

    def _calc_metrics(self, equity_records: list, trade_logs: list) -> dict:
        cfg = self.config
        initial_cash = cfg['initial_cash']

        if not equity_records:
            return {}

        final_equity    = equity_records[-1]['equity']
        total_return    = (final_equity - initial_cash) / initial_cash

        # Daily return series
        daily_returns = [r['daily_return'] for r in equity_records]
        dr_arr = np.array(daily_returns, dtype=float)

        # Sharpe ratio (annualized, risk-free rate 3%)
        risk_free_daily = 0.03 / 252
        excess = dr_arr - risk_free_daily
        sharpe = (
            excess.mean() / excess.std() * math.sqrt(252)
            if excess.std() > 1e-10 else 0.0
        )

        # Max drawdown
        equities = np.array([r['equity'] for r in equity_records], dtype=float)
        running_max = np.maximum.accumulate(equities)
        drawdowns = (running_max - equities) / running_max
        max_drawdown = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0

        # Maximum drawdown recovery period, measured from the peak before the
        # deepest trough until equity returns to that peak. A missing value
        # means the strategy had not recovered by the end of the sample.
        if max_drawdown <= 1e-12:
            max_drawdown_recovery_days = 0
        else:
            trough_idx = int(np.argmax(drawdowns))
            peak_equity = running_max[trough_idx]
            peak_idx = int(np.flatnonzero(
                equities[:trough_idx + 1] == peak_equity
            )[-1])
            recovery_indices = np.flatnonzero(
                equities[trough_idx + 1:] >= peak_equity
            ) + trough_idx + 1
            max_drawdown_recovery_days = (
                int(recovery_indices[0] - peak_idx)
                if len(recovery_indices) else None
            )

        # Trade statistics
        n_trades = len(trade_logs)
        if n_trades > 0:
            winning_trades = [t for t in trade_logs if t['net_pnl'] > 0]
            losing_trades  = [t for t in trade_logs if t['net_pnl'] < 0]
            win_rate = len(winning_trades) / n_trades

            avg_win  = (
                sum(t['net_pnl'] for t in winning_trades) / len(winning_trades)
                if winning_trades else 0.0
            )
            avg_loss = (
                abs(sum(t['net_pnl'] for t in losing_trades) / len(losing_trades))
                if losing_trades else 0.0
            )
            profit_loss_ratio = avg_win / avg_loss if avg_loss > 1e-10 else float('inf')
            expectancy = sum(t['net_pnl'] for t in trade_logs) / n_trades
            gross_wins = sum(t['net_pnl'] for t in winning_trades)
            gross_losses = abs(sum(t['net_pnl'] for t in losing_trades))
            profit_factor = (
                gross_wins / gross_losses if gross_losses > 1e-10 else float('inf')
            )
        else:
            win_rate = 0.0
            profit_loss_ratio = 0.0
            expectancy = 0.0
            profit_factor = 0.0
            avg_win = 0.0
            avg_loss = 0.0

        return {
            'initial_cash':       initial_cash,
            'final_equity':       round(final_equity, 2),
            'total_return':       round(total_return * 100, 4),   # %
            'sharpe_ratio':       round(sharpe, 4),
            'max_drawdown':       round(max_drawdown * 100, 4),   # %
            'max_drawdown_recovery_days': max_drawdown_recovery_days,
            'win_rate':           round(win_rate * 100, 4),       # %
            'profit_loss_ratio':  round(profit_loss_ratio, 4),
            'expectancy':         round(expectancy, 4),
            'profit_factor':      round(profit_factor, 4) if profit_factor != float('inf') else profit_factor,
            'avg_win':            round(avg_win, 4),
            'avg_loss':           round(avg_loss, 4),
            'n_trades':           n_trades,
            'n_winning':          len(winning_trades) if n_trades > 0 else 0,
            'n_losing':           len(losing_trades)  if n_trades > 0 else 0,
        }

    # ------------------------------------------------------------------
    # Internal methods: save trade log
    # ------------------------------------------------------------------

    def _save_trade_log(self, trade_logs: list) -> str:
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        name = self.config['strategy_name']
        filename = f"{name}_trades_{ts}.csv"
        filepath = os.path.join(self.config['results_dir'], filename)

        fieldnames = [
            'trade_id', 'open_date', 'close_date', 'direction',
            'open_price', 'close_price', 'size',
            'gross_pnl', 'commission', 'net_pnl', 'margin_used',
        ]

        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(trade_logs)

        print(f"[BacktestEngine] Trade log saved: {filepath}")
        return filepath

    # ------------------------------------------------------------------
    # Internal methods: print summary
    # ------------------------------------------------------------------

    @staticmethod
    def _print_summary(metrics: dict, log_path: str):
        sep = "=" * 50
        print(sep)
        print("  Backtest Result Summary")
        print(sep)
        print(f"  Initial Cash      : {metrics.get('initial_cash', 0):>12,.2f} CNY")
        print(f"  Final Equity      : {metrics.get('final_equity', 0):>12,.2f} CNY")
        print(f"  Total Return      : {metrics.get('total_return', 0):>12.4f} %")
        print(f"  Sharpe Ratio      : {metrics.get('sharpe_ratio', 0):>12.4f}")
        print(f"  Max Drawdown      : {metrics.get('max_drawdown', 0):>12.4f} %")
        recovery_days = metrics.get('max_drawdown_recovery_days')
        recovery_text = (
            f"{recovery_days} trading days"
            if recovery_days is not None else "Not recovered"
        )
        print(f"  MaxDD Recovery    : {recovery_text:>12}")
        print(f"  Win Rate          : {metrics.get('win_rate', 0):>12.4f} %")
        print(f"  Profit/Loss Ratio : {metrics.get('profit_loss_ratio', 0):>12.4f}")
        print(f"  Expectancy        : {metrics.get('expectancy', 0):>12.2f} CNY/trade")
        pf = metrics.get('profit_factor', 0)
        pf_text = f"{pf:.4f}" if pf != float('inf') else "inf"
        print(f"  Profit Factor     : {pf_text:>12}")
        print(f"  Total Trades      : {metrics.get('n_trades', 0):>12}")
        print(f"  Winning Trades    : {metrics.get('n_winning', 0):>12}")
        print(f"  Losing Trades     : {metrics.get('n_losing', 0):>12}")
        print(sep)
        print(f"  Trade Log         : {log_path}")
        print(sep)
