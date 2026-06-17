import pandas as pd
import numpy as np
from typing import List
from tabulate import tabulate
import config


def compute_metrics(equity_curve: List[dict], trade_log: List[dict],
                    starting_capital: float) -> dict:
    if not equity_curve:
        return {}

    eq = pd.DataFrame(equity_curve).set_index('date')
    eq.index = pd.to_datetime(eq.index)
    values = eq['portfolio_value']

    total_return = (values.iloc[-1] - starting_capital) / starting_capital
    days = (eq.index[-1] - eq.index[0]).days
    annual_return = (1 + total_return) ** (365 / max(days, 1)) - 1

    daily_ret = values.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)
              if daily_ret.std() != 0 else 0)

    neg_ret = daily_ret[daily_ret < 0]
    sortino = (daily_ret.mean() / neg_ret.std() * np.sqrt(252)
               if len(neg_ret) > 0 and neg_ret.std() != 0 else 0)

    rolling_max = values.cummax()
    drawdown = (values - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    # Trade-level stats
    tlog = pd.DataFrame(trade_log)
    entries = tlog[tlog['action'] == 'ENTRY'] if not tlog.empty else pd.DataFrame()
    exit_actions = ['HARD_STOP', 'AVG_COST_STOP', 'THESIS_CRACK',
                    'DEAD_MONEY', 'RUNNER_EXIT']
    exits = tlog[tlog['action'].isin(exit_actions + ['TAKE_PROFIT_T3'])] \
        if not tlog.empty else pd.DataFrame()

    wins = exits[exits.get('pnl', pd.Series()) > 0] if 'pnl' in exits.columns else pd.DataFrame()
    losses = exits[exits.get('pnl', pd.Series()) <= 0] if 'pnl' in exits.columns else pd.DataFrame()

    win_rate = len(wins) / len(exits) if len(exits) > 0 else 0
    avg_win = wins['pnl'].mean() if len(wins) > 0 else 0
    avg_loss = losses['pnl'].mean() if len(losses) > 0 else 0
    best_trade = exits['pnl'].max() if 'pnl' in exits.columns and len(exits) > 0 else 0
    worst_trade = exits['pnl'].min() if 'pnl' in exits.columns and len(exits) > 0 else 0
    avg_days = exits['days'].mean() if 'days' in exits.columns and len(exits) > 0 else 0

    return {
        'starting_capital': starting_capital,
        'ending_capital': values.iloc[-1],
        'total_return_pct': total_return * 100,
        'annualized_return_pct': annual_return * 100,
        'sharpe_ratio': sharpe,
        'sortino_ratio': sortino,
        'max_drawdown_pct': max_drawdown * 100,
        'total_trades': len(entries),
        'win_rate_pct': win_rate * 100,
        'avg_win_usd': avg_win,
        'avg_loss_usd': avg_loss,
        'best_trade_usd': best_trade,
        'worst_trade_usd': worst_trade,
        'avg_hold_days': avg_days,
    }


def print_equity_curve(equity_curve: List[dict], width: int = 60):
    if not equity_curve:
        return
    values = [e['portfolio_value'] for e in equity_curve]
    lo, hi = min(values), max(values)
    height = 12
    chart = [[' '] * width for _ in range(height)]

    for x, val in enumerate(values):
        col = int((x / len(values)) * (width - 1))
        row = int(((val - lo) / (hi - lo + 1e-9)) * (height - 1))
        row = height - 1 - row
        chart[row][col] = '█'

    print("\n  EQUITY CURVE")
    print(f"  ${hi:>10,.0f} ┐")
    for row in chart:
        print("              │" + ''.join(row))
    print(f"  ${lo:>10,.0f} ┘")
    dates = [e['date'] for e in equity_curve]
    print(f"              {str(dates[0])[:10]}{'':>30}{str(dates[-1])[:10]}")


def print_trade_log(trade_log: List[dict], max_rows: int = 30):
    if not trade_log:
        print("  No trades executed.")
        return

    rows = []
    for t in trade_log[-max_rows:]:
        date_str = str(t.get('date', ''))[:10]
        pnl = t.get('pnl', '')
        pnl_str = f"${pnl:,.0f}" if isinstance(pnl, (int, float)) else ''
        pct_str = f"{t['pct']*100:.1f}%" if 'pct' in t and t['pct'] else ''
        rows.append([
            date_str,
            t.get('ticker', ''),
            t.get('action', ''),
            f"${t.get('price', 0):.2f}",
            pnl_str,
            pct_str,
        ])

    print(tabulate(rows,
                   headers=['Date', 'Ticker', 'Action', 'Price', 'PnL', 'Gain%'],
                   tablefmt='simple'))


def print_report(metrics: dict, equity_curve: List[dict],
                 trade_log: List[dict]):
    print("\n" + "=" * 65)
    print("  AGENTIC TRADING BACKTEST RESULTS")
    print(f"  Period: {config.START_DATE}  →  {config.END_DATE}")
    print("=" * 65)

    gain_sign = "+" if metrics.get('total_return_pct', 0) >= 0 else ""
    print(f"\n  {'Starting Capital':<30} ${metrics['starting_capital']:>12,.2f}")
    print(f"  {'Ending Capital':<30} ${metrics['ending_capital']:>12,.2f}")
    print(f"  {'Total Return':<30} {gain_sign}{metrics['total_return_pct']:>11.2f}%")
    print(f"  {'Annualized Return':<30} {gain_sign}{metrics['annualized_return_pct']:>11.2f}%")
    print(f"  {'Sharpe Ratio':<30} {metrics['sharpe_ratio']:>12.2f}")
    print(f"  {'Sortino Ratio':<30} {metrics['sortino_ratio']:>12.2f}")
    print(f"  {'Max Drawdown':<30} {metrics['max_drawdown_pct']:>11.2f}%")

    print(f"\n  {'Total Trades':<30} {metrics['total_trades']:>12}")
    print(f"  {'Win Rate':<30} {metrics['win_rate_pct']:>11.1f}%")
    print(f"  {'Avg Win':<30} ${metrics['avg_win_usd']:>12,.2f}")
    print(f"  {'Avg Loss':<30} ${metrics['avg_loss_usd']:>12,.2f}")
    print(f"  {'Best Trade':<30} ${metrics['best_trade_usd']:>12,.2f}")
    print(f"  {'Worst Trade':<30} ${metrics['worst_trade_usd']:>12,.2f}")
    print(f"  {'Avg Hold Days':<30} {metrics['avg_hold_days']:>12.1f}")

    print_equity_curve(equity_curve)

    print("\n  TRADE LOG (last 30 actions)")
    print("-" * 65)
    print_trade_log(trade_log)
    print("=" * 65)
