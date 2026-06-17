import pandas as pd
import config
from positions.position_manager import Position


def check_hard_stop(pos: Position, current_price: float) -> bool:
    return current_price <= pos.initial_stop


def check_avg_cost_stop(pos: Position, current_price: float) -> bool:
    if pos.avg_cost == 0:
        return False
    drawdown = (pos.avg_cost - current_price) / pos.avg_cost
    return drawdown >= config.AVG_COST_STOP_PCT


def check_thesis_crack(df: pd.DataFrame, idx: int) -> bool:
    if idx < 1:
        return False
    price_chg = (df['Close'].iloc[idx] - df['Close'].iloc[idx - 1]) / df['Close'].iloc[idx - 1]
    vol_ratio = df['VOL_RATIO'].iloc[idx]
    # Big down day on high volume = potential thesis crack
    return price_chg < -0.10 and vol_ratio > 2.5


def check_time_stop(pos: Position, current_date: pd.Timestamp) -> str:
    days = (current_date - pos.entry_date).days
    if days > config.REEVAL_DAYS:
        return 'REEVAL'
    if days > config.MAX_ADD_DAYS:
        return 'NO_MORE_ADDS'
    return 'OK'


def check_dead_money(pos: Position, current_price: float,
                     current_date: pd.Timestamp) -> bool:
    days = (current_date - pos.entry_date).days
    if days < config.DEAD_MONEY_DAYS:
        return False
    gain = pos.pct_gain(current_price)
    return 0 <= gain < config.DEAD_MONEY_MIN_GAIN


def check_breakout_add(pos: Position, current_price: float,
                        df: pd.DataFrame, idx: int) -> bool:
    entry_price = pos.tranches[0].price if pos.tranches else 0
    if entry_price == 0:
        return False
    above_entry = current_price > entry_price * 1.02
    vol_ratio = df['VOL_RATIO'].iloc[idx]
    high_vol = vol_ratio > 1.5
    return above_entry and high_vol and pos.add_count == 0


def evaluate_all_stops(pos: Position, current_price: float,
                        current_date: pd.Timestamp,
                        df: pd.DataFrame, idx: int) -> dict:
    time_status = check_time_stop(pos, current_date)
    return {
        'hard_stop':    check_hard_stop(pos, current_price),
        'avg_cost_stop': check_avg_cost_stop(pos, current_price),
        'thesis_crack': check_thesis_crack(df, idx),
        'time_status':  time_status,
        'dead_money':   check_dead_money(pos, current_price, current_date),
        'breakout_add': check_breakout_add(pos, current_price, df, idx),
    }
