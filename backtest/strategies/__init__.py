"""Strategies package: base class + public examples.

Put private research modules in this folder (gitignored).
Import them directly in main.py, e.g.::

    from strategies.your_strategy import YourStrategy
"""

from strategies.base import FuturesStrategyBase
from strategies.double_ma import DoubleMaStrategy
from strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from strategies.my_strategy import MyStrategy

__all__ = [
    'FuturesStrategyBase',
    'DoubleMaStrategy',
    'RsiMeanReversionStrategy',
    'MyStrategy',
]
