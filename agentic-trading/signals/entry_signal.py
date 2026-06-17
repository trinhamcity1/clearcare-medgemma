import pandas as pd
import numpy as np
import config
from signals.technical import find_support_confluence


def score_entry(df: pd.DataFrame, idx: int) -> int:
    if idx < 20:
        return 0

    score = 0
    row = df.iloc[idx]

    # Signal 1: RSI in healthy zone (not overbought, not in freefall)
    if not pd.isna(row.get('RSI', np.nan)):
        if config.RSI_LOW <= row['RSI'] <= config.RSI_HIGH:
            score += 1

    # Signal 2: Stochastic bullish crossover below 80
    if idx >= 1:
        prev = df.iloc[idx - 1]
        k_now, d_now = row.get('STOCH_K', np.nan), row.get('STOCH_D', np.nan)
        k_prev, d_prev = prev.get('STOCH_K', np.nan), prev.get('STOCH_D', np.nan)
        if not any(pd.isna(v) for v in [k_now, d_now, k_prev, d_prev]):
            if k_now > d_now and k_prev <= d_prev and k_now < 80:
                score += 1

    # Signal 3: Volume drying up on pullback (below 20-day avg)
    if not pd.isna(row.get('VOL_RATIO', np.nan)):
        if row['VOL_RATIO'] < 0.8:
            score += 1

    # Signal 4: TTM Squeeze active (coiled spring)
    if row.get('SQUEEZE', False):
        score += 1

    # Signal 5: Price near support confluence (2+ signals)
    price = row['Close']
    confluence = find_support_confluence(
        price, df['Close'], df['High'], df['Low'], idx)
    if confluence >= 2:
        score += 1

    # Signal 6: Relative strength vs SPY (stock holding up better)
    if not pd.isna(row.get('REL_STR', np.nan)):
        if row['REL_STR'] > -0.02:   # less than 2% underperformance
            score += 1

    return score


def calculate_rr(entry_price: float, stop_price: float,
                 target_price: float) -> float:
    risk = entry_price - stop_price
    reward = target_price - entry_price
    if risk <= 0:
        return 0.0
    return reward / risk


def get_entry_decision(df: pd.DataFrame, idx: int,
                       catalyst_score: int) -> dict:
    signal_score = score_entry(df, idx)
    price = df['Close'].iloc[idx]
    atr_val = df['ATR'].iloc[idx] if not pd.isna(df['ATR'].iloc[idx]) else price * 0.03

    stop_price   = price * (1 - config.HARD_STOP_PCT)
    target_price = price * (1 + config.PROFIT_T1)
    rr = calculate_rr(price, stop_price, target_price)

    enter = (
        signal_score >= config.MIN_ENTRY_SIGNALS and
        catalyst_score >= 4 and
        rr >= config.MIN_RR_RATIO
    )

    return {
        'enter': enter,
        'signal_score': signal_score,
        'catalyst_score': catalyst_score,
        'entry_price': price,
        'stop_price': stop_price,
        'atr': atr_val,
        'rr': rr,
    }
