"""
Data Management Module
Responsible for loading, processing, and updating futures data.
"""

import os
import sys
import pandas as pd
import backtrader as bt

BACKTEST_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKTEST_DIR)
sys.path.insert(0, BACKTEST_DIR)

from data_update import DataUpdate
from roll_calendar import (
    build_date_contract_map,
    mapping_as_dates,
    normalize_contract_code,
)

_PRICE_COLS = ['open', 'high', 'low', 'close', 'settle']
_ALIGN_COLS = ['open', 'high', 'low', 'close', 'settle', 'oi', 'volume']


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
    Loads, filters, and updates futures weighted data and contract-level bars.
    """

    DATA_DIR = os.path.join(ROOT_DIR, 'data')

    def __init__(self, symbol: str = 'SA', update: bool = False):
        """
        Parameters
        ----------
        symbol : str
            Symbol code, e.g. 'SA'.
        update : bool
            Whether to refresh data from the exchange (incremental: current year).
        """
        self.symbol = symbol
        self.weighted_path = os.path.join(self.DATA_DIR, f'{symbol}_weighted.csv')
        self.raw_path = os.path.join(self.DATA_DIR, f'{symbol}.csv')

        if update:
            self._update_data()

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _update_data(self):
        """Refresh exchange history incrementally and regenerate weighted data."""
        try:
            print(f"[DataManager] Updating {self.symbol} data ...")
            DataUpdate(self.symbol).update()
            print(f"[DataManager] {self.symbol} data update done. Path: {self.weighted_path}")
        except Exception as e:
            print(f"[DataManager] Data update failed: {e}")
            raise

    def _feed_from_df(self, df: pd.DataFrame) -> SAWeightedData:
        return SAWeightedData(dataname=df)

    @staticmethod
    def _align_contract_ohlc(cdf: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
        """Reindex one contract onto the weighted calendar; ffill/bfill prices."""
        frame = cdf.copy()
        for col in _ALIGN_COLS:
            if col not in frame.columns:
                frame[col] = 0.0
        out = frame[_ALIGN_COLS].reindex(index)
        out[_PRICE_COLS] = out[_PRICE_COLS].ffill().bfill()
        out['oi'] = out['oi'].ffill().bfill().fillna(0)
        out['volume'] = out['volume'].fillna(0)
        return out

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
                "Run python backtest/data_update.py first, or pass update=True "
                "when constructing DataManager."
            )

        df = pd.read_csv(self.weighted_path, parse_dates=['date'])
        df.set_index('date', inplace=True)
        df.index = pd.to_datetime(df.index).normalize()
        df = df[~df.index.duplicated(keep='last')]
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

    def load_contracts_dataframe(self) -> pd.DataFrame:
        """Load contract-level CZCE history from ``data/{symbol}.csv``."""
        if not os.path.exists(self.raw_path):
            raise FileNotFoundError(
                f"Contract data file not found: {self.raw_path}\n"
                "Run python backtest/data_update.py first, or pass update=True "
                "when constructing DataManager."
            )

        df = pd.read_csv(self.raw_path)
        if 'date' not in df.columns or 'contract' not in df.columns:
            raise ValueError(
                f"{self.raw_path} must contain 'date' and 'contract' columns"
            )
        df['date'] = pd.to_datetime(df['date'].astype(str), errors='coerce')
        df = df.dropna(subset=['date'])
        df['contract'] = df['contract'].map(normalize_contract_code)
        df = df[df['contract'] != '']
        df.sort_values(['date', 'contract'], inplace=True)
        return df.reset_index(drop=True)

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

    @staticmethod
    def _codes_in_window(raw: pd.DataFrame, index: pd.DatetimeIndex) -> list:
        """Return contract codes with at least one bar inside ``index``, first-seen order."""
        if raw.empty or index.empty:
            return []
        start, end = index.min(), index.max()
        subset = raw[(raw['date'] >= start) & (raw['date'] <= end)]
        codes = []
        seen = set()
        for code in subset['contract']:
            if code in seen:
                continue
            seen.add(code)
            codes.append(code)
        return codes

    def _align_contracts(self, raw: pd.DataFrame, index: pd.DatetimeIndex,
                         codes: list) -> tuple:
        """Align listed contracts onto ``index``. Returns (feeds, aligned_frames)."""
        contract_feeds = {}
        aligned = {}
        for code in codes:
            cdf = raw[raw['contract'] == code].copy()
            if cdf.empty:
                print(f"[DataManager] Skipping {code}: no rows in SA.csv")
                continue
            cdf = cdf.set_index('date').sort_index()
            cdf.index = pd.to_datetime(cdf.index).normalize()
            cdf = cdf[~cdf.index.duplicated(keep='last')]
            aligned_df = self._align_contract_ohlc(cdf, index)
            if aligned_df[_PRICE_COLS].isna().all().all():
                print(f"[DataManager] Skipping {code}: no usable OHLC after align")
                continue
            aligned[code] = aligned_df
            contract_feeds[code] = self._feed_from_df(aligned_df)
        return contract_feeds, aligned

    def get_contract_bundle(
        self,
        start_date: str = None,
        end_date: str = None,
    ) -> dict:
        """Build weighted + all real-contract feeds for a backtest window.

        Every SA contract that prints inside the window is aligned onto the
        weighted calendar so the strategy can read it. Calendar 01/05/09
        contracts used for execution are a subset of that list.

        Returns
        -------
        dict
            weighted_df, weighted_feed, contract_feeds, contract_by_date,
            calendar_codes, exec_price_df
        """
        weighted_df = self.load_dataframe(start_date, end_date)
        raw = self.load_contracts_dataframe()

        mapping = build_date_contract_map(weighted_df.index, raw)
        calendar_codes = []
        seen_cal = set()
        for code in mapping.values():
            if code not in seen_cal:
                seen_cal.add(code)
                calendar_codes.append(code)

        if not calendar_codes:
            raise ValueError(
                "Roll calendar produced no contracts. Check data/SA.csv coverage."
            )

        codes = self._codes_in_window(raw, weighted_df.index)
        seen = set(codes)
        for code in calendar_codes:
            if code not in seen:
                codes.append(code)
                seen.add(code)

        contract_feeds, aligned = self._align_contracts(
            raw, weighted_df.index, codes
        )

        missing = [c for c in calendar_codes if c not in contract_feeds]
        if missing:
            raise ValueError(
                f"Calendar contracts have no aligned OHLC: {missing}"
            )

        exec_close = []
        exec_code = []
        for dt in weighted_df.index:
            key = pd.Timestamp(dt).normalize()
            code = mapping.get(key)
            if code is None:
                raise KeyError(
                    f"No calendar contract mapped for {key.date()}; "
                    "check data/SA.csv coverage"
                )
            exec_close.append(float(aligned[code].loc[dt, 'close']))
            exec_code.append(code)
        exec_price_df = pd.DataFrame(
            {'close': exec_close, 'contract': exec_code},
            index=weighted_df.index,
        )

        n_cal = len(calendar_codes)
        n_all = len(contract_feeds)
        print(
            f"[DataManager] Feeds: weighted + {n_all} contracts "
            f"({n_cal} calendar: {', '.join(calendar_codes)})"
        )

        return {
            'weighted_df': weighted_df,
            'weighted_feed': self._feed_from_df(weighted_df),
            'contract_feeds': contract_feeds,
            'contract_by_date': mapping_as_dates(mapping),
            'calendar_codes': calendar_codes,
            'exec_price_df': exec_price_df,
        }

    def get_raw_dataframe(self) -> pd.DataFrame:
        """Return the full unfiltered weighted DataFrame (useful for plotting, etc.)."""
        return self.load_dataframe()
