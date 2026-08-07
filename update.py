import pandas as pd
import urllib.request
from datetime import datetime
import os
import shutil

# Resolve paths relative to this file so cwd does not matter
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_ROOT_DIR, 'cache')
_DATA_DIR = os.path.join(_ROOT_DIR, 'data')

_EXPECTED_COLUMNS = [
    'date', 'contract', 'prev_settle', 'open', 'high', 'low', 'close', 'settle',
    'change1', 'change2', 'volume', 'oi', 'oi_change', 'turnover', 'delivery_settle',
]
_REQUIRED_COLUMNS = ['date', 'contract', 'open', 'high', 'low', 'close', 'oi', 'volume', 'settle']


class DataUpdate:
    def __init__(self, category:str):
        self.map_category = {
            'SA': ('CZCE', 2019),
        }
        self.category = category
        self.exchange = self.map_category[category][0]
        self.start_year = self.map_category[category][1]
        self.year = int(datetime.now().strftime('%Y'))

    def update(self):
        '''
        Update data from the exchange.
        '''
        if self.exchange == 'CZCE':
            # Create directory if it does not exist
            os.makedirs(_CACHE_DIR, exist_ok=True)
            os.makedirs(_DATA_DIR, exist_ok=True)
            
            # Download data from exchange
            for year in range(self.start_year, self.year + 1):
                url = f"http://www.czce.com.cn/cn/DFSStaticFiles/Future/{year}/FutureDataAllHistory/{self.category}FUTURES{year}.txt"
                if year < 2020:
                    url = f"http://www.czce.com.cn/cn/DFSStaticFiles/Future/{year}/FutureDataAllHistory/{self.category}.txt"
                path = os.path.join(_CACHE_DIR, f"{self.category}{year}.txt")
                try:
                    urllib.request.urlretrieve(url, path)
                    print(f"{self.category}{year} Update Done.")
                except (urllib.error.HTTPError, urllib.error.URLError):
                    print(f"{self.category}{year} Update Error.")
            
            # Merge data from cache folder
            all_data = []
            for year in range(self.start_year, self.year + 1):
                path = os.path.join(_CACHE_DIR, f"{self.category}{year}.txt")
                if not os.path.exists(path):
                    print(f"Warning: {path} does not exist, skipping ...")
                    continue
                df = pd.read_csv(path, sep='|', skiprows=2, header=None)
                if len(df.columns) >= len(_EXPECTED_COLUMNS):
                    df.columns = _EXPECTED_COLUMNS + [
                        f'unknown_{i}' for i in range(len(df.columns) - len(_EXPECTED_COLUMNS))
                    ]
                else:
                    df.columns = _EXPECTED_COLUMNS[:len(df.columns)]
                missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
                if missing:
                    print(
                        f"Warning: {path} missing required columns {missing}, skipping ..."
                    )
                    continue
                all_data.append(df)
            if not all_data:
                raise ValueError(
                    f"No usable cache files for {self.category}; "
                    f"need columns {_REQUIRED_COLUMNS}"
                )
            data = pd.concat(all_data, ignore_index=True)
            
            # Wash data and save csv file
            data = data.map(lambda x: x.strip() if isinstance(x, str) else x)
            data = data.map(lambda x: x.replace(',', '') if isinstance(x, str) else x)
            for col in data.columns:
                try:
                    data[col] = pd.to_numeric(data[col])
                except (ValueError, TypeError):
                    pass
            missing = [c for c in _REQUIRED_COLUMNS if c not in data.columns]
            if missing:
                raise KeyError(f"Merged data missing required columns: {missing}")
            data = data[_REQUIRED_COLUMNS]
            data.to_csv(os.path.join(_DATA_DIR, f"{self.category}.csv"), index=False)
            
            # Calculate weighted data
            weighted_data = data.dropna(subset=['oi', 'volume', 'open', 'high', 'low', 'close', 'settle'])
            weighted_data = weighted_data[(weighted_data['oi'] > 0) & (weighted_data['volume'] > 0)]
            grouped = weighted_data.groupby('date')
            weighted_result = pd.DataFrame()
            weighted_result['date'] = grouped['date'].first()
            weighted_result['open'] = grouped.apply(lambda x: (x['open'] * x['oi']).sum() / x['oi'].sum())
            weighted_result['high'] = grouped.apply(lambda x: (x['high'] * x['oi']).sum() / x['oi'].sum())
            weighted_result['low'] = grouped.apply(lambda x: (x['low'] * x['oi']).sum() / x['oi'].sum())
            weighted_result['close'] = grouped.apply(lambda x: (x['close'] * x['oi']).sum() / x['oi'].sum())
            weighted_result['settle'] = grouped.apply(lambda x: (x['settle'] * x['oi']).sum() / x['oi'].sum())
            weighted_result['oi'] = grouped['oi'].sum()
            weighted_result['volume'] = grouped['volume'].sum()
            weighted_result.to_csv(
                os.path.join(_DATA_DIR, f"{self.category}_weighted.csv"), index=False
            )
            
            # Clean up cache folder
            if os.path.exists(_CACHE_DIR):
                shutil.rmtree(_CACHE_DIR)
                print("Cache folder cleaned up.")

            return data
