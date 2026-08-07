# SA-Futures

A backtesting framework for **Zhengzhou Commodity Exchange SA (Soda Ash) futures**, built on [Backtrader](https://www.backtrader.com/).

It downloads historical data from the exchange, builds open-interest–weighted daily bars, runs futures-aware backtests (margin, commission, contract multiplier), and exports equity curves, trade logs, and signal charts.

## Features

- **Data pipeline** — fetch CZCE SA history, clean contract-level OHLC, and aggregate to OI-weighted continuous series
- **Futures cost model** — percentage margin, commission on notional, configurable contract multiplier
- **Strategy API** — inherit `FuturesStrategyBase` for buy/sell/close helpers and signal logging
- **Metrics & reports** — Sharpe, max drawdown, win rate, trade CSV, optional R-multiple alpha report
- **Charts** — equity, returns, position, price & signals, summary plots
- **Private strategies** — keep research code under `backtest/strategies/`

## Requirements

- Python 3.8+
- Dependencies listed in `requirements.txt`:

```
backtrader
pandas
matplotlib
numpy
```

## Installation

```bash
cd SA-Futures
pip install -r requirements.txt
```

## Quick Start

### 1. Download / refresh data

Data is pulled from CZCE and written to `data/SA.csv` and `data/SA_weighted.csv`.

```python
from update import DataUpdate

DataUpdate('SA').update()
```

Or enable refresh when running a backtest by setting `UPDATE_DATA = True` in `backtest/main.py`.

### 2. Run a backtest

Edit the configuration block at the top of `backtest/main.py` (strategy, dates, cash, commission, margin, multiplier), then:

```bash
python backtest/main.py
```

Outputs land in `backtest/results/` (charts, trade log, and optional alpha CSV).

### 3. Switch strategies

Public examples:

```python
from strategies.double_ma import DoubleMaStrategy
from strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from strategies.my_strategy import MyStrategy

STRATEGY = DoubleMaStrategy
```

## Project Layout

```
SA-Futures/
├── update.py                 # CZCE data download & OI-weighted aggregation
├── requirements.txt
├── LICENSE
├── data/                     # Generated CSVs (gitignored)
└── backtest/
    ├── main.py               # Backtest entry point & config
    ├── data_manager.py       # Load / filter / feed data into Backtrader
    ├── backtest_engine.py    # Cerebro runner, analyzers, metrics
    ├── plotting.py           # Chart generation
    ├── strategies/           # Base, examples (tracked) + private modules (gitignored)
    └── results/              # Backtest outputs (gitignored)
```

## Writing a Strategy

1. Subclass `FuturesStrategyBase` from `strategies.base`.
2. Define indicators in `__init__` and logic in `next()`.
3. Use `buy_signal()`, `sell_signal()`, `close_signal()`, and `get_position_size()`.
4. Add research strategies under `backtest/strategies/` (one class per file; gitignored), or start from `strategies/my_strategy.py`.

Minimal sketch:

```python
from strategies.base import FuturesStrategyBase
import backtrader.indicators as btind

class MyStrategy(FuturesStrategyBase):
    params = (('period', 20),)

    def __init__(self):
        super().__init__()
        self.sma = btind.SMA(self.data.close, period=self.p.period)

    def next(self):
        if self._pending_order:
            return
        pos = self.get_position_size()
        if pos == 0 and self.data.close[0] > self.sma[0]:
            self.buy_signal()
        elif pos > 0 and self.data.close[0] < self.sma[0]:
            self.close_signal()
```

Available bar fields: `open`, `high`, `low`, `close`, `volume`, `openinterest` (OI), and custom line `settle`.

## Configuration Reference

| Parameter | Meaning | Typical default |
|-----------|---------|-----------------|
| `START_DATE` / `END_DATE` | Backtest window | `2020-01-01` … |
| `INITIAL_CASH` | Starting equity (CNY) | `100000` |
| `COMMISSION_RATE` | Fee on notional | `0.0002` (0.02%) |
| `MARGIN_RATE` | Margin ratio | `0.15` (15%) |
| `CONTRACT_MULTIPLIER` | Tons per lot (SA) | `20` |
| `TRADE_SIZE` | Lots per trade (if strategy uses it) | `1` |
| `UPDATE_DATA` | Re-download from CZCE | `False` |
| `STRATEGY_PARAMS` | Override strategy `params` | `{}` |

## Data Notes

- Source: [CZCE](http://www.czce.com.cn/) historical futures files for symbol **SA**.
- Contract-level history is cleaned and saved as `data/SA.csv`.
- Daily continuous series uses open-interest weighting → `data/SA_weighted.csv`.
- `data/` and `cache/` are local artifacts; do not commit them.

## License

MIT License — see [LICENSE](LICENSE).
