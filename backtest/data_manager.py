"""
Data Management Module
Responsible for loading, processing, and updating futures data.
"""

import os
import sys
import pandas as pd
import backtrader as bt
from datetime import datetime

# Add the project root to sys.path so update.py can be imported
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)


class SAWeightedData(bt.feeds.PandasData):
    """
    SA weighted data feed based on PandasData, adapted to the SA_weighted.csv format.
    Columns: date, open, high, low, close, settle, oi, volume

    Extra custom line:
      data.settle  - settlement price
    Built-in line mapping:
      openinterest -> oi column
    """
    # Add a custom `settle` line; oi maps to the built-in openinterest line.
    lines = ('settle',)

    params = (
        ('datetime',     None),      # index is the date
        ('open',         'open'),
        ('high',         'high'),
        ('low',          'low'),
        ('close',        'close'),
        ('volume',       'volume'),
        ('openinterest', 'oi'),      # built-in openinterest line reads the oi column
        ('settle',       'settle'),  # custom settle line
    )


class DataManager:
    """
    Data manager.
    Loads, filters, and updates futures weighted data.
    """

    DATA_DIR = os.path.join(ROOT_DIR, 'data')

    def __init__(self, symbol: str = 'SA', update: bool = False):
        """
        Parameters
        ----------
        symbol : str
            Symbol code, e.g. 'SA'.
        update : bool
            Whether to re-download and update data from the exchange.
        """
        self.symbol = symbol
        self.weighted_path = os.path.join(self.DATA_DIR, f'{symbol}_weighted.csv')

        if update:
            self._update_data()

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _update_data(self):
        """Call DataUpdate from update.py to re-download and regenerate weighted data."""
        try:
            from update import DataUpdate
            print(f"[DataManager] Updating {self.symbol} data ...")
            updater = DataUpdate(self.symbol)
            updater.update()
            print(f"[DataManager] {self.symbol} data update done. Path: {self.weighted_path}")
        except Exception as e:
            print(f"[DataManager] Data update failed: {e}")
            raise

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def load_dataframe(
        self,
        start_date: str = None,
        end_date: str = None,
    ) -> pd.DataFrame:
        """
        Load the weighted CSV data and return a DataFrame filtered by date.

        Parameters
        ----------
        start_date : str, optional
            Start date, formatted as 'YYYY-MM-DD'.
        end_date : str, optional
            End date, formatted as 'YYYY-MM-DD'.

        Returns
        -------
        pd.DataFrame
            Columns: date(index), open, high, low, close, settle, oi, volume.
        """
        if not os.path.exists(self.weighted_path):
            raise FileNotFoundError(
                f"Weighted data file not found: {self.weighted_path}\n"
                "Run update.py first, or pass update=True when constructing DataManager."
            )

        df = pd.read_csv(self.weighted_path, parse_dates=['date'])
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)

        # Date filter
        if start_date:
            df = df[df.index >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df.index <= pd.to_datetime(end_date)]

        if df.empty:
            raise ValueError(
                f"Filtered data is empty. Check that the date range "
                f"[{start_date}, {end_date}] falls within the available data."
            )

        return df

    def get_bt_feed(
        self,
        start_date: str = None,
        end_date: str = None,
    ) -> SAWeightedData:
        """
        Return a data feed that can be passed directly to backtrader's Cerebro.

        Parameters
        ----------
        start_date : str, optional
        end_date : str, optional

        Returns
        -------
        SAWeightedData
        """
        df = self.load_dataframe(start_date, end_date)
        feed = SAWeightedData(dataname=df)
        return feed

    def get_raw_dataframe(self) -> pd.DataFrame:
        """Return the full unfiltered weighted DataFrame (useful for plotting, etc.)."""
        return self.load_dataframe()
