import yfinance as yf
import pandas as pd
import numpy as np
import pickle
import warnings
from pathlib import Path
from typing import Dict, List, Optional

warnings.filterwarnings('ignore')

CACHE_DIR = Path(__file__).parent.parent / '.cache'
CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(ticker: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"{ticker}_{start}_{end}.pkl"


def download(ticker: str, start: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    path = _cache_path(ticker, start, end)
    if use_cache and path.exists():
        with open(path, 'rb') as f:
            return pickle.load(f)

    try:
        df = yf.download(ticker, start=start, end=end, auto_adjust=True,
                         progress=False, multi_level_index=False)
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # Normalize column names
    df.columns = [c.split()[0] if ' ' in str(c) else str(c) for c in df.columns]
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    df.dropna(subset=['Close'], inplace=True)

    if use_cache:
        with open(path, 'wb') as f:
            pickle.dump(df, f)

    return df


def download_universe(tickers: List[str], start: str, end: str,
                      use_cache: bool = True) -> Dict[str, pd.DataFrame]:
    data: Dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        df = download(ticker, start, end, use_cache)
        if not df.empty and len(df) >= 20:
            data[ticker] = df
    return data


def get_spy(start: str, end: str) -> pd.DataFrame:
    return download('SPY', start, end)


def get_vix(start: str, end: str) -> pd.DataFrame:
    return download('^VIX', start, end)
