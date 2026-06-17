import pandas as pd
import numpy as np
from typing import Dict, List
import warnings
warnings.filterwarnings('ignore')

import config
from data.market_data import download_universe, get_spy, get_vix
from signals.technical import add_all_indicators, find_support_confluence
from scanner.regime_filter import market_regime
from scanner.stock_scanner import scan_universe, catalyst_score
from signals.entry_signal import get_entry_decision
from positions.position_manager import PortfolioManager
from risk.stop_loss import evaluate_all_stops


class BacktestEngine:
    def __init__(self):
        self.pm = PortfolioManager(config.STARTING_CAPITAL)
        self.equity_curve: List[dict] = []
        self.trade_log: List[dict] = []
        self.all_data: Dict[str, pd.DataFrame] = {}
        self.spy: pd.DataFrame = pd.DataFrame()
        self.vix: pd.DataFrame = pd.DataFrame()
        self.pending_entries: List[dict] = []

    # ------------------------------------------------------------------
    def load_data(self):
        print("  Downloading market data...")
        extra_start = '2025-07-01'  # need history for indicators

        self.spy = get_spy(extra_start, config.END_DATE)
        self.vix = get_vix(extra_start, config.END_DATE)

        raw = download_universe(config.UNIVERSE, extra_start, config.END_DATE)
        print(f"  Got data for {len(raw)} tickers")

        spy_close = self.spy['Close'] if not self.spy.empty else pd.Series(dtype=float)
        for ticker, df in raw.items():
            self.all_data[ticker] = add_all_indicators(df, spy_close)

        # Add indicators to SPY itself
        if not self.spy.empty:
            self.spy = add_all_indicators(self.spy, self.spy['Close'])

    # ------------------------------------------------------------------
    def run(self):
        self.load_data()

        if self.spy.empty:
            print("ERROR: Could not download SPY data.")
            return

        trading_dates = self.spy.loc[config.START_DATE:config.END_DATE].index
        print(f"  Simulating {len(trading_dates)} trading days\n")

        prices_snapshot: Dict[str, float] = {}

        for i, date in enumerate(trading_dates):
            spy_idx = self.spy.index.get_loc(date)

            # Current prices for all tickers
            prices_snapshot = {}
            for ticker, df in self.all_data.items():
                if date in df.index:
                    prices_snapshot[ticker] = float(df['Close'].loc[date])

            spy_price = float(self.spy['Close'].loc[date])
            prices_snapshot['SPY'] = spy_price

            # --- Execute pending entries at today's open ---
            self._execute_pending(date, prices_snapshot)

            # --- Check regime ---
            regime = market_regime(self.spy, self.vix, spy_idx)

            # --- Update open positions ---
            self._update_positions(date, prices_snapshot)

            # --- Scan for new entries (regime must be green, portfolio not too hot) ---
            heat_ok = self._portfolio_heat_ok(prices_snapshot)
            if regime == 'GREEN' and len(self.pm.positions) < config.MAX_POSITIONS and heat_ok:
                self._scan_entries(date, prices_snapshot)

            # --- Record equity ---
            pv = self.pm.portfolio_value(prices_snapshot)
            self.equity_curve.append({
                'date': date,
                'portfolio_value': pv,
                'cash': self.pm.capital,
                'regime': regime,
                'open_positions': len(self.pm.positions),
            })

        print(f"\n  Simulation complete. Trades executed: {len(self.trade_log)}")

    # ------------------------------------------------------------------
    def _execute_pending(self, date: pd.Timestamp,
                          prices: Dict[str, float]):
        executed = []
        for entry in self.pending_entries:
            ticker = entry['ticker']
            if ticker not in prices:
                continue
            if ticker in self.pm.positions:
                continue
            if len(self.pm.positions) >= config.MAX_POSITIONS:
                continue

            open_price = prices[ticker]
            pv = self.pm.portfolio_value(prices)
            stop = open_price * (1 - config.HARD_STOP_PCT)

            if self.pm.can_open(ticker, pv * config.MAX_POSITION_PCT, pv):
                pos = self.pm.open_position(ticker, date, open_price, stop, pv)
                if pos:
                    self.trade_log.append({
                        'ticker': ticker,
                        'action': 'ENTRY',
                        'date': date,
                        'price': open_price,
                        'signal_score': entry.get('signal_score', 0),
                        'catalyst_score': entry.get('catalyst_score', 0),
                    })
                    executed.append(ticker)

        self.pending_entries = [e for e in self.pending_entries
                                if e['ticker'] not in executed]

    # ------------------------------------------------------------------
    def _update_positions(self, date: pd.Timestamp,
                           prices: Dict[str, float]):
        to_close = []

        for ticker, pos in list(self.pm.positions.items()):
            if ticker not in prices:
                continue

            df = self.all_data[ticker]
            if date not in df.index:
                continue

            idx = df.index.get_loc(date)
            price = prices[ticker]
            atr_val = float(df['ATR'].iloc[idx]) if not pd.isna(df['ATR'].iloc[idx]) else price * 0.03
            squeeze = bool(df['SQUEEZE'].iloc[idx]) if 'SQUEEZE' in df.columns else False

            # Update trailing stops for runners
            runner_proceeds = self.pm.update_runners(ticker, price, atr_val)
            if runner_proceeds > 0 and not pos.tranches and not pos.runners:
                self.trade_log.append({
                    'ticker': ticker, 'action': 'RUNNER_EXIT',
                    'date': date, 'price': price,
                    'pnl': pos.realized_pnl,
                })
                to_close.append(ticker)
                continue

            # Evaluate stops
            stops = evaluate_all_stops(pos, price, date, df, idx)

            if stops['hard_stop'] or stops['avg_cost_stop'] or stops['thesis_crack']:
                reason = ('HARD_STOP' if stops['hard_stop']
                          else 'AVG_COST_STOP' if stops['avg_cost_stop']
                          else 'THESIS_CRACK')
                self.pm.close_position(ticker, date, price)
                self.trade_log.append({
                    'ticker': ticker, 'action': reason,
                    'date': date, 'price': price,
                    'pnl': pos.realized_pnl,
                    'days': pos.days_open(date),
                })
                to_close.append(ticker)
                continue

            if stops['dead_money']:
                self.pm.close_position(ticker, date, price)
                self.trade_log.append({
                    'ticker': ticker, 'action': 'DEAD_MONEY',
                    'date': date, 'price': price,
                    'pnl': pos.realized_pnl,
                    'days': pos.days_open(date),
                })
                to_close.append(ticker)
                continue

            # Profit taking
            pct = pos.pct_gain(price)
            if pct >= config.PROFIT_T3 and not pos.t3_hit:
                pos.t3_hit = True
                self.pm.take_profit(ticker, date, price, 3, atr_val, squeeze)
                self.trade_log.append({
                    'ticker': ticker, 'action': 'TAKE_PROFIT_T3',
                    'date': date, 'price': price, 'pct': pct,
                })
                if not pos.tranches and not pos.runners:
                    to_close.append(ticker)
            elif pct >= config.PROFIT_T2 and not pos.t2_hit:
                pos.t2_hit = True
                self.pm.take_profit(ticker, date, price, 2, atr_val, squeeze)
                self.trade_log.append({
                    'ticker': ticker, 'action': 'TAKE_PROFIT_T2',
                    'date': date, 'price': price, 'pct': pct,
                })
            elif pct >= config.PROFIT_T1 and not pos.t1_hit:
                pos.t1_hit = True
                self.pm.take_profit(ticker, date, price, 1, atr_val, squeeze)
                self.trade_log.append({
                    'ticker': ticker, 'action': 'TAKE_PROFIT_T1',
                    'date': date, 'price': price, 'pct': pct,
                })

            # Add tranches at support
            add_ok = (
                stops['time_status'] == 'OK' and
                pos.add_count < config.MAX_ADDS and
                pct < -0.04
            )
            if add_ok:
                conf = find_support_confluence(price, df['Close'],
                                               df['High'], df['Low'], idx)
                if conf >= 2:
                    vol_ok = float(df['VOL_RATIO'].iloc[idx]) < 1.0
                    if vol_ok:
                        added = self.pm.add_tranche(ticker, date, price)
                        if added:
                            self.trade_log.append({
                                'ticker': ticker, 'action': 'ADD_TRANCHE',
                                'date': date, 'price': price,
                                'add_num': pos.add_count,
                                'confluence': conf,
                            })

            # Breakout add
            if stops.get('breakout_add') and not pos.t1_hit:
                added = self.pm.add_breakout_tranche(ticker, date, price)
                if added:
                    self.trade_log.append({
                        'ticker': ticker, 'action': 'BREAKOUT_ADD',
                        'date': date, 'price': price,
                    })

    # ------------------------------------------------------------------
    def _portfolio_heat_ok(self, prices: dict) -> bool:
        if not self.pm.positions:
            return True
        total_cost = 0.0
        total_value = 0.0
        for ticker, pos in self.pm.positions.items():
            price = prices.get(ticker, 0)
            for t in pos.tranches:
                total_cost  += t.cost
                total_value += t.shares * price
            for r in pos.runners:
                total_cost  += r.shares * pos.avg_cost
                total_value += r.shares * price
        if total_cost == 0:
            return True
        unrealized_pct = (total_value - total_cost) / total_cost
        return unrealized_pct > -config.PORTFOLIO_HEAT_MAX

    # ------------------------------------------------------------------
    def _scan_entries(self, date: pd.Timestamp,
                       prices: Dict[str, float]):
        already_watching = {e['ticker'] for e in self.pending_entries}

        candidates = scan_universe(self.all_data, self.spy['Close'],
                                   date, min_catalyst=5)

        for cand in candidates:
            ticker = cand['ticker']
            if ticker in self.pm.positions or ticker in already_watching:
                continue
            if ticker not in self.all_data:
                continue

            df = self.all_data[ticker]
            if date not in df.index:
                continue

            idx = df.index.get_loc(date)
            decision = get_entry_decision(df, idx, cand['catalyst_score'])

            if decision['enter']:
                self.pending_entries.append({
                    'ticker': ticker,
                    'signal_score': decision['signal_score'],
                    'catalyst_score': decision['catalyst_score'],
                    'entry_price': decision['entry_price'],
                })
                if len(self.pending_entries) + len(self.pm.positions) >= config.MAX_POSITIONS:
                    break
