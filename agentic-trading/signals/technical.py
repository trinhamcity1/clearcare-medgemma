import pandas as pd
import numpy as np
from typing import Tuple, List
import config


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k: int = 14, d: int = 3) -> Tuple[pd.Series, pd.Series]:
    lo = low.rolling(k).min()
    hi = high.rolling(k).max()
    pct_k = 100 * (close - lo) / (hi - lo).replace(0, np.nan)
    pct_d = pct_k.rolling(d).mean()
    return pct_k, pct_d


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def bollinger(close: pd.Series, period: int = 20,
              std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std()
    return mid + std * sd, mid, mid - std * sd


def keltner(high: pd.Series, low: pd.Series, close: pd.Series,
            period: int = 20, mult: float = 1.5) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(period).mean()
    a = atr(high, low, close, period)
    return mid + mult * a, mid, mid - mult * a


def ttm_squeeze(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 20) -> pd.Series:
    bb_up, _, bb_lo = bollinger(close, period)
    kc_up, _, kc_lo = keltner(high, low, close, period)
    return (bb_up < kc_up) & (bb_lo > kc_lo)


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def relative_strength(stock_close: pd.Series, spy_close: pd.Series,
                       period: int = 10) -> pd.Series:
    stock_ret = stock_close.pct_change(period)
    spy_ret = spy_close.pct_change(period)
    return stock_ret - spy_ret


def swing_lows(low: pd.Series, window: int = 5) -> pd.Series:
    result = pd.Series(False, index=low.index)
    arr = low.values
    for i in range(window, len(arr) - window):
        if arr[i] == arr[max(0, i - window):i + window + 1].min():
            result.iloc[i] = True
    return result


def fibonacci_levels(high: float, low: float) -> List[float]:
    rng = high - low
    return [high - r * rng for r in config.FIBO_LEVELS]


def find_support_confluence(price: float, close: pd.Series, high: pd.Series,
                             low: pd.Series, idx: int) -> int:
    zone = price * config.SUPPORT_ZONE_PCT
    count = 0

    # Swing lows
    low_slice = low.iloc[:idx + 1]
    sl = swing_lows(low_slice)
    recent_lows = low_slice[sl].tail(10)
    if any(abs(price - lv) <= zone for lv in recent_lows):
        count += 1

    # Moving averages
    for period in [20, 50, 200]:
        if idx >= period:
            ma_val = close.iloc[idx - period + 1:idx + 1].mean()
            if abs(price - ma_val) <= zone:
                count += 1
                break

    # Fibonacci (last 60 bars)
    lookback = min(60, idx)
    if lookback > 5:
        recent_high = high.iloc[idx - lookback:idx + 1].max()
        recent_low  = low.iloc[idx - lookback:idx + 1].min()
        fibs = fibonacci_levels(recent_high, recent_low)
        if any(abs(price - f) <= zone for f in fibs):
            count += 1

    # Volume node (high volume area)
    if idx >= 20:
        vol_slice = close.iloc[idx - 20:idx + 1]
        vol_mean = vol_slice.mean()
        if abs(price - vol_mean) <= zone * 2:
            count += 1

    return count


def add_all_indicators(df: pd.DataFrame, spy_close: pd.Series) -> pd.DataFrame:
    df = df.copy()
    df['RSI']       = rsi(df['Close'], config.RSI_PERIOD)
    df['STOCH_K'], df['STOCH_D'] = stochastic(
        df['High'], df['Low'], df['Close'], config.STOCH_K, config.STOCH_D)
    df['ATR']       = atr(df['High'], df['Low'], df['Close'], config.ATR_PERIOD)
    df['SMA20']     = sma(df['Close'], 20)
    df['SMA50']     = sma(df['Close'], 50)
    df['SMA200']    = sma(df['Close'], 200)
    df['VOL_SMA20'] = sma(df['Volume'], config.VOL_SMA_LONG)
    df['SQUEEZE']   = ttm_squeeze(df['High'], df['Low'], df['Close'])
    df['VOL_RATIO'] = df['Volume'] / df['VOL_SMA20']

    spy_aligned = spy_close.reindex(df.index, method='ffill')
    df['REL_STR']   = relative_strength(df['Close'], spy_aligned)

    return df
