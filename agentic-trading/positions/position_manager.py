from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd
import numpy as np
import config


@dataclass
class Tranche:
    date: pd.Timestamp
    price: float
    shares: float
    cost: float


@dataclass
class Runner:
    shares: float
    trail_atr_mult: float
    highest_price: float
    stop_price: float
    label: str   # 'A' or 'B'


@dataclass
class Position:
    ticker: str
    entry_date: pd.Timestamp
    tranches: List[Tranche] = field(default_factory=list)
    runners: List[Runner] = field(default_factory=list)
    add_count: int = 0
    t1_hit: bool = False
    t2_hit: bool = False
    t3_hit: bool = False
    runners_active: bool = False
    initial_stop: float = 0.0
    intended_total_value: float = 0.0
    closed: bool = False
    close_date: Optional[pd.Timestamp] = None
    realized_pnl: float = 0.0

    @property
    def total_shares(self) -> float:
        tr_shares = sum(t.shares for t in self.tranches)
        ru_shares = sum(r.shares for r in self.runners)
        return tr_shares + ru_shares

    @property
    def avg_cost(self) -> float:
        total_cost = sum(t.cost for t in self.tranches)
        total_shares = sum(t.shares for t in self.tranches)
        if total_shares == 0:
            return 0.0
        return total_cost / total_shares

    @property
    def market_value(self, price: float = 0.0) -> float:
        return self.total_shares * price

    def unrealized_pnl(self, current_price: float) -> float:
        tr_pnl = sum((current_price - t.price) * t.shares for t in self.tranches)
        ru_pnl = sum((current_price - (t.price)) * t.shares
                     for t in self.tranches for _ in [1] if False)
        runner_cost = sum(r.shares * self._runner_avg_cost() for r in self.runners)
        runner_val  = sum(r.shares * current_price for r in self.runners)
        return tr_pnl + (runner_val - runner_cost)

    def _runner_avg_cost(self) -> float:
        return self.avg_cost

    def pct_gain(self, current_price: float) -> float:
        ac = self.avg_cost
        if ac == 0:
            return 0.0
        return (current_price - ac) / ac

    def days_open(self, current_date: pd.Timestamp) -> int:
        return (current_date - self.entry_date).days


class PortfolioManager:
    def __init__(self, starting_capital: float):
        self.capital = starting_capital
        self.starting_capital = starting_capital
        self.positions: dict[str, Position] = {}
        self.closed_positions: List[Position] = []

    def portfolio_value(self, prices: dict) -> float:
        pos_val = sum(
            p.total_shares * prices.get(t, 0)
            for t, p in self.positions.items()
        )
        runner_val = sum(
            sum(r.shares * prices.get(t, 0) for r in p.runners)
            for t, p in self.positions.items()
        )
        return self.capital + pos_val + runner_val

    def position_market_value(self, ticker: str, price: float) -> float:
        if ticker not in self.positions:
            return 0.0
        p = self.positions[ticker]
        return p.total_shares * price

    def can_open(self, ticker: str, intended_value: float,
                 current_portfolio_value: float) -> bool:
        if ticker in self.positions:
            return False
        if len(self.positions) >= config.MAX_POSITIONS:
            return False
        if intended_value > self.capital:
            return False
        if intended_value / current_portfolio_value > config.MAX_POSITION_PCT:
            return False
        return True

    def open_position(self, ticker: str, date: pd.Timestamp,
                      entry_price: float, stop_price: float,
                      portfolio_value: float) -> Optional[Position]:
        intended_value = min(
            portfolio_value * config.MAX_POSITION_PCT,
            self.capital * 0.95
        )
        entry_value = intended_value * config.TRANCHE_ENTRY
        shares = entry_value / entry_price
        cost = shares * entry_price * (1 + config.COMMISSION_PCT)

        if cost > self.capital:
            return None

        self.capital -= cost
        pos = Position(
            ticker=ticker,
            entry_date=date,
            initial_stop=stop_price,
            intended_total_value=intended_value,
        )
        tranche = Tranche(date=date, price=entry_price,
                          shares=shares, cost=cost)
        pos.tranches.append(tranche)
        self.positions[ticker] = pos
        return pos

    def add_tranche(self, ticker: str, date: pd.Timestamp,
                    price: float) -> bool:
        if ticker not in self.positions:
            return False
        pos = self.positions[ticker]
        if pos.add_count >= config.MAX_ADDS:
            return False

        fractions = [config.TRANCHE_ADD1, config.TRANCHE_ADD2, config.TRANCHE_ADD3]
        frac = fractions[pos.add_count]
        value = pos.intended_total_value * frac
        shares = value / price
        cost = shares * price * (1 + config.COMMISSION_PCT)

        if cost > self.capital:
            return False

        self.capital -= cost
        pos.tranches.append(Tranche(date=date, price=price,
                                    shares=shares, cost=cost))
        pos.add_count += 1
        return True

    def add_breakout_tranche(self, ticker: str, date: pd.Timestamp,
                              price: float) -> bool:
        if ticker not in self.positions:
            return False
        pos = self.positions[ticker]
        value = pos.intended_total_value * config.TRANCHE_BREAKOUT
        shares = value / price
        cost = shares * price * (1 + config.COMMISSION_PCT)
        if cost > self.capital:
            return False
        self.capital -= cost
        pos.tranches.append(Tranche(date=date, price=price,
                                    shares=shares, cost=cost))
        return True

    def _sell_shares(self, ticker: str, shares: float,
                     price: float) -> float:
        pos = self.positions[ticker]
        proceeds = shares * price * (1 - config.COMMISSION_PCT)
        avg = pos.avg_cost
        pos.realized_pnl += (price - avg) * shares
        self.capital += proceeds

        remaining = shares
        new_tranches = []
        for t in pos.tranches:
            if remaining <= 0:
                new_tranches.append(t)
            elif t.shares <= remaining:
                remaining -= t.shares
            else:
                t.shares -= remaining
                t.cost = t.shares * t.price
                new_tranches.append(t)
                remaining = 0
        pos.tranches = new_tranches
        return proceeds

    def take_profit(self, ticker: str, date: pd.Timestamp,
                    price: float, target_level: int,
                    current_atr: float, squeeze_on: bool) -> float:
        if ticker not in self.positions:
            return 0.0
        pos = self.positions[ticker]
        total_sh = pos.total_shares
        sell_sh = total_sh * config.SELL_FRACTION
        proceeds = self._sell_shares(ticker, sell_sh, price)

        if target_level == 3 and not pos.runners_active:
            pos.runners_active = True
            trail_a_mult = config.SQUEEZE_ATR if squeeze_on else config.RUNNER_A_ATR
            trail_b_mult = config.SQUEEZE_ATR if squeeze_on else config.RUNNER_B_ATR
            remaining_sh = pos.total_shares
            half = remaining_sh / 2

            pos.runners.append(Runner(
                shares=half,
                trail_atr_mult=trail_a_mult,
                highest_price=price,
                stop_price=price - trail_a_mult * current_atr,
                label='A',
            ))
            pos.runners.append(Runner(
                shares=remaining_sh - half,
                trail_atr_mult=trail_b_mult,
                highest_price=price,
                stop_price=price - trail_b_mult * current_atr,
                label='B',
            ))
            pos.tranches = []

        return proceeds

    def update_runners(self, ticker: str, price: float,
                        current_atr: float) -> float:
        if ticker not in self.positions:
            return 0.0
        pos = self.positions[ticker]
        proceeds = 0.0
        alive = []
        for runner in pos.runners:
            runner.highest_price = max(runner.highest_price, price)
            runner.stop_price = max(
                runner.stop_price,
                runner.highest_price - runner.trail_atr_mult * current_atr
            )
            if price <= runner.stop_price:
                sell_val = runner.shares * price * (1 - config.COMMISSION_PCT)
                pos.realized_pnl += (price - pos.avg_cost) * runner.shares
                self.capital += sell_val
                proceeds += sell_val
            else:
                alive.append(runner)
        pos.runners = alive
        return proceeds

    def close_position(self, ticker: str, date: pd.Timestamp,
                        price: float) -> float:
        if ticker not in self.positions:
            return 0.0
        pos = self.positions[ticker]
        proceeds = 0.0

        for t in pos.tranches:
            val = t.shares * price * (1 - config.COMMISSION_PCT)
            pos.realized_pnl += (price - t.price) * t.shares
            self.capital += val
            proceeds += val

        for r in pos.runners:
            val = r.shares * price * (1 - config.COMMISSION_PCT)
            pos.realized_pnl += (price - pos.avg_cost) * r.shares
            self.capital += val
            proceeds += val

        pos.tranches = []
        pos.runners = []
        pos.closed = True
        pos.close_date = date
        self.closed_positions.append(pos)
        del self.positions[ticker]
        return proceeds
