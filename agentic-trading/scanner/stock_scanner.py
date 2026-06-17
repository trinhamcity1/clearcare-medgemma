import pandas as pd
import numpy as np
import config


def catalyst_score(df: pd.DataFrame, idx: int) -> int:
    if idx < config.VOL_SMA_LONG + 1:
        return 0

    prev_price_chg = abs(
        (df['Close'].iloc[idx - 1] - df['Close'].iloc[idx - 2]) /
        df['Close'].iloc[idx - 2]
    )
    prev_vol_ratio = df['VOL_RATIO'].iloc[idx - 1]

    score = 0
    if prev_price_chg > 0.08 and prev_vol_ratio > 3.0:
        score = 10   # monster move — earnings beat / major catalyst
    elif prev_price_chg > 0.05 and prev_vol_ratio > 2.5:
        score = 8
    elif prev_price_chg > config.CATALYST_PRICE_MOVE and prev_vol_ratio > config.CATALYST_VOL_MULT:
        score = 6
    elif prev_vol_ratio > 2.0:
        score = 4
    return score


def in_post_catalyst_pullback(df: pd.DataFrame, idx: int) -> bool:
    lookback = min(config.PULLBACK_LOOKBACK, idx)
    if lookback < 3:
        return False

    window = df['Close'].iloc[idx - lookback:idx + 1]
    peak_idx = window.idxmax()
    peak_pos = window.index.get_loc(peak_idx)

    if peak_pos == 0 or peak_pos == len(window) - 1:
        return False

    recent_high = window.max()
    current = df['Close'].iloc[idx]
    pullback_pct = (recent_high - current) / recent_high

    # Healthy pullback: 5-30% from recent high
    return 0.05 <= pullback_pct <= 0.35


def scan_universe(all_data: dict, spy_close: pd.Series,
                  date: pd.Timestamp, min_catalyst: int = 6) -> list:
    candidates = []

    for ticker, df in all_data.items():
        if date not in df.index:
            continue
        idx = df.index.get_loc(date)
        if idx < 30:
            continue

        score = catalyst_score(df, idx)
        if score < min_catalyst:
            continue

        # pullback preferred but not a hard gate — catalyst quality gates entry
        pb = in_post_catalyst_pullback(df, idx)
        if not pb and score < 8:
            continue

        candidates.append({
            'ticker': ticker,
            'catalyst_score': score,
            'price': df['Close'].iloc[idx],
            'atr': df['ATR'].iloc[idx] if not pd.isna(df['ATR'].iloc[idx]) else 0,
        })

    candidates.sort(key=lambda x: x['catalyst_score'], reverse=True)
    return candidates
