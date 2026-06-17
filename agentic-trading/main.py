import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import warnings
warnings.filterwarnings('ignore')

import config
from backtest.engine import BacktestEngine
from backtest.results import compute_metrics, print_report


def main():
    print("\n" + "=" * 65)
    print("  AGENTIC TRADING SYSTEM — BACKTEST")
    print(f"  Universe : {len(config.UNIVERSE)} stocks")
    print(f"  Capital  : ${config.STARTING_CAPITAL:,.0f}")
    print(f"  Period   : {config.START_DATE}  →  {config.END_DATE}")
    print("=" * 65 + "\n")

    engine = BacktestEngine()
    engine.run()

    metrics = compute_metrics(engine.equity_curve, engine.trade_log,
                               config.STARTING_CAPITAL)
    print_report(metrics, engine.equity_curve, engine.trade_log)


if __name__ == '__main__':
    main()
