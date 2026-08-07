"""
Backtest Main Module
====================
Usage:
  1. Modify the parameters in the "Backtest Configuration" section below
  2. Change the import + STRATEGY to the strategy class you want to run
  3. Run: python backtest/main.py

Directory structure:
  backtest/
    main.py            <- This file (backtest entry point; edit parameters here)
    strategies/        <- Base class, example strategies, and private modules
    data_manager.py    <- Data management module
    backtest_engine.py <- Backtest engine and metrics calculation
    plotting.py        <- Chart plotting module
    results/           <- Backtest output directory (auto-created)
"""

import csv
import datetime
import os
import statistics
import sys

# Ensure the project root is on sys.path (so update.py and similar imports resolve)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_manager   import DataManager
from backtest_engine import BacktestEngine
from plotting       import BacktestPlotter

# Import strategy (change here to pick a different strategy)
# Public examples:
#   from strategies.double_ma import DoubleMaStrategy
#   from strategies.rsi_mean_reversion import RsiMeanReversionStrategy
#   from strategies.my_strategy import MyStrategy
# Private strategies (extra files under strategies/, gitignored):
#   from strategies.your_strategy import YourStrategy
from strategies.double_ma import DoubleMaStrategy


# ==============================================================================
# * Backtest Configuration (edit values here)
# ==============================================================================

# --- Strategy selection ---
# DoubleMaStrategy: fast/slow MA crossover (example)
STRATEGY = DoubleMaStrategy

# --- Strategy-specific parameters (match the strategy's `params`; leave empty to use defaults) ---
STRATEGY_PARAMS = {
    # Defaults: fast_period=5, slow_period=20
    # 'fast_period': 5,
    # 'slow_period': 20,
}

# --- Backtest range ---
START_DATE = '2020-01-01'            # Start date 'YYYY-MM-DD'
END_DATE   = '2026-12-31'            # End date   'YYYY-MM-DD'

# --- Capital and trading parameters ---
INITIAL_CASH         = 100000        # Initial cash (CNY)
COMMISSION_RATE      = 0.0002        # Commission rate (0.02%)
MARGIN_RATE          = 0.15          # Margin ratio (15%)
CONTRACT_MULTIPLIER  = 20            # Contract multiplier (tons per lot)
TRADE_SIZE           = 1             # Lots per trade

# --- Data update ---
UPDATE_DATA = False                  # True = re-download data from the exchange

# --- Strategy name (used in file naming; use a distinct name per strategy) ---
STRATEGY_NAME = 'DoubleMA'

# --- Output directory ---
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')

# ==============================================================================
# Alpha report helpers
# ==============================================================================

def _print_alpha_report(alpha_log: list, results_dir: str, strategy_name: str) -> str:
    """Print / save per-trade R and long/short expectancy for pure-signal runs."""
    sep = "=" * 50
    print("\n" + sep)
    print("  Signal Alpha Report")
    print(sep)

    if not alpha_log:
        print("  No closed alpha trades.")
        print(sep)
        return ""

    rs = [t['r_multiple'] for t in alpha_log if t.get('r_multiple') is not None]
    nets = [t['net_pnl'] for t in alpha_log]
    wins = [t for t in alpha_log if t['net_pnl'] > 0]
    losses = [t for t in alpha_log if t['net_pnl'] < 0]

    avg_r = sum(rs) / len(rs) if rs else 0.0
    med_r = statistics.median(rs) if rs else 0.0
    expectancy = sum(nets) / len(nets)
    win_rate = len(wins) / len(alpha_log)

    print(f"  Trades            : {len(alpha_log):>12}")
    print(f"  Win Rate          : {win_rate * 100:>12.2f} %")
    print(f"  Expectancy        : {expectancy:>12.2f} CNY/trade")
    print(f"  Avg R (gross)     : {avg_r:>12.3f}")
    print(f"  Median R          : {med_r:>12.3f}")
    if rs:
        print(f"  Best / Worst R    : {max(rs):>6.2f} / {min(rs):.2f}")

    sizes = [t.get('size') for t in alpha_log if t.get('size') is not None]
    if sizes:
        print(
            f"  Size mean/max     : {sum(sizes)/len(sizes):>8.1f} / {max(sizes)}"
        )

    planned = [t['planned_risk_pct'] for t in alpha_log if t.get('planned_risk_pct') is not None]
    realized = [t['realized_pnl_pct'] for t in alpha_log if t.get('realized_pnl_pct') is not None]
    if planned:
        print(
            f"  Planned risk %    : mean={sum(planned)/len(planned):.2f}  "
            f"max={max(planned):.2f}"
        )
    if realized:
        worst = min(realized)
        print(
            f"  Realized PnL %    : mean={sum(realized)/len(realized):.2f}  "
            f"worst={worst:.2f}"
        )
        n_breach = sum(1 for x in realized if x < -5.0)
        print(f"  Trades loss > 5%  : {n_breach:>12}  (vs equity at entry)")

    for side in ('long', 'short'):
        subset = [t for t in alpha_log if t['direction'] == side]
        if not subset:
            continue
        side_nets = [t['net_pnl'] for t in subset]
        side_rs = [t['r_multiple'] for t in subset if t.get('r_multiple') is not None]
        side_wr = sum(1 for t in subset if t['net_pnl'] > 0) / len(subset)
        side_exp = sum(side_nets) / len(subset)
        side_r = sum(side_rs) / len(side_rs) if side_rs else 0.0
        print(
            f"  {side.capitalize():5}  n={len(subset):3d}  "
            f"WR={side_wr * 100:5.1f}%  "
            f"Exp={side_exp:8.2f}  "
            f"AvgR={side_r:6.3f}"
        )

    regimes = sorted({t.get('regime') for t in alpha_log if t.get('regime')})
    for regime in regimes:
        subset = [t for t in alpha_log if t.get('regime') == regime]
        if not subset:
            continue
        side_nets = [t['net_pnl'] for t in subset]
        side_rs = [t['r_multiple'] for t in subset if t.get('r_multiple') is not None]
        side_wr = sum(1 for t in subset if t['net_pnl'] > 0) / len(subset)
        side_exp = sum(side_nets) / len(subset)
        side_r = sum(side_rs) / len(side_rs) if side_rs else 0.0
        print(
            f"  {str(regime):5}  n={len(subset):3d}  "
            f"WR={side_wr * 100:5.1f}%  "
            f"Exp={side_exp:8.2f}  "
            f"AvgR={side_r:6.3f}"
        )

    # Annual net pnl
    by_year = {}
    for t in alpha_log:
        y = t['close_date'].year if hasattr(t['close_date'], 'year') else int(str(t['close_date'])[:4])
        by_year.setdefault(y, 0.0)
        by_year[y] += t['net_pnl']
    print("  Net PnL by year   :")
    for y in sorted(by_year):
        print(f"    {y}: {by_year[y]:>10.2f} CNY")

    print(sep)

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(results_dir, f"{strategy_name}_alpha_{ts}.csv")
    fieldnames = [
        'open_date', 'close_date', 'direction', 'regime', 'size',
        'open_price', 'close_price', 'stop_dist', 'r_multiple',
        'gross_pnl', 'net_pnl',
        'equity_at_entry', 'planned_risk_pct', 'realized_pnl_pct',
    ]
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(alpha_log)
    print(f"  Alpha log saved   : {path}")
    return path


# ==============================================================================
# Main program (usually no need to modify below)
# ==============================================================================

def main():
    print("=" * 60)
    print(f"  SA Futures Backtest Framework")
    print(f"  Strategy: {STRATEGY.__name__}  [{START_DATE} -> {END_DATE}]")
    print("=" * 60)

    # 1. Load data
    print("\n[1/4] Loading data ...")
    dm = DataManager(symbol='SA', update=UPDATE_DATA)
    data_feed = dm.get_bt_feed(start_date=START_DATE, end_date=END_DATE)
    price_df  = dm.load_dataframe(start_date=START_DATE, end_date=END_DATE)

    # 2. Configure backtest engine
    print("[2/4] Configuring backtest engine ...")
    config = {
        'initial_cash':        INITIAL_CASH,
        'commission_rate':     COMMISSION_RATE,
        'margin_rate':         MARGIN_RATE,
        'contract_multiplier': CONTRACT_MULTIPLIER,
        'trade_size':          TRADE_SIZE,
        'strategy_params':     STRATEGY_PARAMS,
        'results_dir':         RESULTS_DIR,
        'strategy_name':       STRATEGY_NAME,
    }
    engine = BacktestEngine(STRATEGY, data_feed, config)

    # 3. Run backtest
    print("[3/4] Running backtest ...\n")
    result = engine.run()

    # 3b. Pure-signal alpha report (when strategy provides alpha_log)
    alpha_log = getattr(result['strat'], 'alpha_log', None)
    alpha_path = ""
    if alpha_log is not None:
        alpha_path = _print_alpha_report(alpha_log, RESULTS_DIR, STRATEGY_NAME)

    # 4. Plot charts
    print("\n[4/4] Plotting charts ...")
    signal_log = result['strat'].signal_log
    plotter = BacktestPlotter(
        equity_records = result['equity_records'],
        trade_logs     = result['trade_logs'],
        price_df       = price_df,
        signal_log     = signal_log,
        metrics        = result['metrics'],
        config         = config,
    )
    chart_paths = plotter.plot_all()

    # 5. Final summary
    print("\n" + "=" * 60)
    print("  Chart file paths")
    print("=" * 60)
    labels = {
        'equity':   'Equity Curve   ',
        'returns':  'Return Curve   ',
        'position': 'Position Chart ',
        'signals':  'Price & Signals',
        'summary':  'Summary Chart  ',
    }
    for key, path in chart_paths.items():
        print(f"  {labels.get(key, key)}: {path}")
    print(f"  Trade Log       : {result['log_path']}")
    if alpha_path:
        print(f"  Alpha Log       : {alpha_path}")
    print("=" * 60)

    return result, chart_paths


if __name__ == '__main__':
    main()
