import sys, os, random, time
sys.path.insert(0, os.path.dirname(__file__))

import warnings
warnings.filterwarnings('ignore')

import config as cfg
import pandas as pd
from datetime import datetime, timedelta
from data.market_data import download_universe, get_spy, get_vix
from signals.technical import add_all_indicators
from backtest.engine import BacktestEngine
from backtest.results import compute_metrics

# ── Optimization period ────────────────────────────────────────────────
OPT_START  = '2025-01-01'
OPT_END    = '2026-06-17'
DATA_START = '2024-06-01'   # warmup window for indicators
TARGET_PCT = 100.0          # annual return target
N_RANDOM   = 150
N_HILLCLIMB = 50

# ── Parameter search space ─────────────────────────────────────────────
SPACE = {
    'MIN_ENTRY_SIGNALS':   [2, 3, 4],
    'MIN_PRICE':           [5, 8, 10, 15],
    'HARD_STOP_PCT':       [0.04, 0.05, 0.06, 0.07, 0.08, 0.10],
    'AVG_COST_STOP_PCT':   [0.30, 0.35, 0.40, 0.45],
    'PROFIT_T1':           [0.05, 0.07, 0.08, 0.10],
    'PROFIT_T2':           [0.12, 0.15, 0.18, 0.20, 0.25],
    'PROFIT_T3':           [0.20, 0.25, 0.30, 0.35, 0.40],
    'SELL_FRACTION':       [0.15, 0.20, 0.25, 0.30],
    'MAX_POSITIONS':       [3, 4, 5, 6],
    'MAX_ADDS':            [2, 3, 4],
    'DEAD_MONEY_DAYS':     [3, 5, 7, 10],
    'DEAD_MONEY_MIN_GAIN': [0.02, 0.03, 0.04, 0.05],
    'PORTFOLIO_HEAT_MAX':  [0.10, 0.15, 0.18, 0.20, 0.25, 0.30],
    'RUNNER_A_ATR':        [1.0, 1.5, 2.0, 2.5],
    'RUNNER_B_ATR':        [2.0, 3.0, 4.0, 5.0],
    'CATALYST_PRICE_MOVE': [0.02, 0.03, 0.04, 0.05],
    'CATALYST_VOL_MULT':   [1.2, 1.5, 1.8, 2.0, 2.5],
    'PULLBACK_LOOKBACK':   [10, 15, 20, 25],
}

# ── Scoring function ───────────────────────────────────────────────────
def score(m: dict) -> float:
    if not m or m.get('total_trades', 0) < 8:
        return -9999.0
    annual   = m.get('annualized_return_pct', -999)
    sharpe   = m.get('sharpe_ratio', 0)
    dd       = abs(m.get('max_drawdown_pct', 100))
    win_rate = m.get('win_rate_pct', 0)
    trades   = m.get('total_trades', 0)

    s  = annual * 1.5                          # primary driver
    s -= max(0, dd - 20) * 4                   # penalise drawdown > 20%
    s += sharpe * 10                            # reward risk-adjusted return
    s += max(0, win_rate - 45) * 0.3            # bonus for win rate > 45%
    s += min(trades, 60) * 0.1                  # slight bonus for activity
    return s

# ── Apply params to live config module ────────────────────────────────
def apply(params: dict):
    for k, v in params.items():
        setattr(cfg, k, v)
    cfg.START_DATE = OPT_START
    cfg.END_DATE   = OPT_END

# ── Single backtest run (reuses pre-loaded data) ───────────────────────
def run(preloaded: dict) -> dict:
    try:
        eng = BacktestEngine(preloaded=preloaded)
        eng.run()
        return compute_metrics(eng.equity_curve, eng.trade_log, cfg.STARTING_CAPITAL)
    except Exception:
        return {}

def random_params() -> dict:
    return {k: random.choice(v) for k, v in SPACE.items()}

def mutate(params: dict, strength: int = 2) -> dict:
    p = params.copy()
    for k in random.sample(list(SPACE.keys()), min(strength, len(SPACE))):
        p[k] = random.choice(SPACE[k])
    return p

# ── Pre-load market data once ──────────────────────────────────────────
def preload_data() -> dict:
    print("  Downloading & caching market data (one-time)...")
    spy = get_spy(DATA_START, OPT_END)
    vix = get_vix(DATA_START, OPT_END)

    if spy.empty:
        raise RuntimeError("Could not download SPY data.")

    spy_ind = add_all_indicators(spy, spy['Close'])
    raw     = download_universe(cfg.UNIVERSE, DATA_START, OPT_END)
    print(f"  Loaded {len(raw)} tickers  ({DATA_START} → {OPT_END})")

    spy_close = spy['Close']
    stocks = {t: add_all_indicators(df, spy_close) for t, df in raw.items()}

    return {'spy': spy_ind, 'vix': vix, 'stocks': stocks}

# ── Pretty-print one result row ────────────────────────────────────────
def fmt_row(i: int, total: int, m: dict, s: float, best: float, tag: str = '') -> str:
    annual = m.get('annualized_return_pct', -999)
    dd     = m.get('max_drawdown_pct', 0)
    sr     = m.get('sharpe_ratio', 0)
    tr     = m.get('total_trades', 0)
    flag   = '★ BEST' if s >= best - 0.01 else tag
    return (f"  [{i:>4}/{total}] "
            f"annual={annual:>7.1f}%  "
            f"dd={dd:>6.1f}%  "
            f"sharpe={sr:>5.2f}  "
            f"trades={tr:>3}  "
            f"score={s:>8.1f}  {flag}")

# ── Write best params back to config.py ───────────────────────────────
def write_best_config(params: dict, metrics: dict):
    cfg_path = os.path.join(os.path.dirname(__file__), 'config.py')
    with open(cfg_path, 'r') as f:
        src = f.read()

    for k, v in params.items():
        import re
        val_str = f"'{v}'" if isinstance(v, str) else str(v)
        src = re.sub(
            rf'^({k}\s*=\s*).*$',
            rf'\g<1>{val_str}',
            src, flags=re.MULTILINE
        )

    with open(cfg_path, 'w') as f:
        f.write(src)

    print(f"\n  config.py updated with best parameters.")

# ── Main ───────────────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 72)
    print("  STRATEGY OPTIMIZER  —  TARGET: 100% ANNUAL RETURN")
    print(f"  Period  : {OPT_START}  →  {OPT_END}  (18 months)")
    print(f"  Universe: {len(cfg.UNIVERSE)} stocks")
    print(f"  Budget  : {N_RANDOM} random  +  {N_HILLCLIMB} hill-climb  = {N_RANDOM+N_HILLCLIMB} total")
    print("=" * 72 + "\n")

    preloaded = preload_data()
    print()

    results = []   # list of (score, metrics, params)
    best_score = -9999.0
    target_hit = False
    total = N_RANDOM + N_HILLCLIMB
    t0 = time.time()

    # ── Phase 1: Random search ─────────────────────────────────────────
    print("  ── PHASE 1: Random Search ──────────────────────────────────\n")
    for i in range(1, N_RANDOM + 1):
        params = random_params()
        apply(params)
        m = run(preloaded)
        s = score(m)

        if s > best_score:
            best_score = s
        results.append((s, m, params))
        results.sort(key=lambda x: x[0], reverse=True)

        annual = m.get('annualized_return_pct', -999)
        print(fmt_row(i, total, m, s, best_score))

        if annual >= TARGET_PCT:
            print(f"\n  ★★★  TARGET {TARGET_PCT:.0f}% ANNUAL REACHED at iteration {i}!  ★★★")
            target_hit = True
            break

    # ── Phase 2: Hill-climb from top 5 ────────────────────────────────
    if not target_hit:
        print(f"\n  ── PHASE 2: Hill-Climb from Top 5 ─────────────────────────\n")
        top5_params = [r[2] for r in results[:5]]

        for i in range(N_HILLCLIMB):
            base   = random.choice(top5_params)
            strength = 1 if i < N_HILLCLIMB // 2 else 2
            params = mutate(base, strength)
            apply(params)
            m = run(preloaded)
            s = score(m)

            if s > best_score:
                best_score = s
                top5_params.insert(0, params)
                top5_params = top5_params[:5]

            results.append((s, m, params))
            results.sort(key=lambda x: x[0], reverse=True)

            annual = m.get('annualized_return_pct', -999)
            print(fmt_row(N_RANDOM + i + 1, total, m, s, best_score))

            if annual >= TARGET_PCT:
                print(f"\n  ★★★  TARGET {TARGET_PCT:.0f}% ANNUAL REACHED at iteration {N_RANDOM+i+1}!  ★★★")
                target_hit = True
                break

    elapsed = time.time() - t0

    # ── Report top 5 ──────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  TOP 5 CONFIGURATIONS FOUND")
    print("=" * 72)

    for rank, (s, m, params) in enumerate(results[:5], 1):
        annual = m.get('annualized_return_pct', 0)
        sharpe = m.get('sharpe_ratio', 0)
        dd     = m.get('max_drawdown_pct', 0)
        wr     = m.get('win_rate_pct', 0)
        tr     = m.get('total_trades', 0)
        end_v  = m.get('ending_capital', 0)
        print(f"\n  ── RANK {rank} {'(★ TARGET MET)' if annual >= TARGET_PCT else ''}")
        print(f"     Annual Return : {annual:>8.2f}%")
        print(f"     Total Return  : {(end_v-100000)/1000:>8.2f}k  (${end_v:,.0f})")
        print(f"     Sharpe Ratio  : {sharpe:>8.2f}")
        print(f"     Max Drawdown  : {dd:>8.2f}%")
        print(f"     Win Rate      : {wr:>8.1f}%")
        print(f"     Trades        : {tr:>8}")
        print(f"     Score         : {s:>8.1f}")
        print(f"     Parameters:")
        for k, v in params.items():
            print(f"       {k:<25} = {v}")

    # ── Apply best to config.py ────────────────────────────────────────
    best_params  = results[0][2]
    best_metrics = results[0][1]
    write_best_config(best_params, best_metrics)

    print(f"\n  Elapsed: {elapsed:.0f}s  |  Best annual: {results[0][1].get('annualized_return_pct',0):.1f}%")
    print("=" * 72 + "\n")

    if not target_hit:
        print("  Target not reached in this run — best config saved.")
        print("  Re-run optimizer to continue searching from best config.")


if __name__ == '__main__':
    main()
