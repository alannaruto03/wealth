"""Vectorized-ish backtester with next-bar execution (no lookahead).

Flow per run:
  1. Ask the strategy for target weights over the full history.
  2. SHIFT weights by one bar so a signal computed on bar t executes on t+1.
  3. Walk bars: mark to market, then rebalance to that bar's (shifted) target.

The single most important correctness property is the shift: the equity at
bar t can never depend on information from bar t.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from wealth.engine.costs import CostModel
from wealth.engine.portfolio import Portfolio, Trade
from wealth.strategies.base import Strategy


@dataclass
class BacktestResult:
    equity: pd.Series
    weights: pd.DataFrame          # the executed (shifted) target weights
    trades: list[Trade]
    prices: pd.DataFrame

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else 0.0


class Backtester:
    def __init__(self, cost_model: Optional[CostModel] = None, cash: float = 100_000.0):
        self.cost_model = cost_model or CostModel()
        self.cash = float(cash)

    def run(self, strategy: Strategy, data: Dict[str, pd.DataFrame]) -> BacktestResult:
        prices = pd.DataFrame(
            {sym: df["close"] for sym, df in data.items()}
        ).sort_index().dropna(how="all")
        symbols = list(prices.columns)

        raw_weights = strategy.generate_weights(data).reindex(
            index=prices.index, columns=symbols
        ).fillna(0.0)

        # Next-bar execution: today's weight is applied to tomorrow's bar.
        exec_weights = raw_weights.shift(1).fillna(0.0)

        portfolio = Portfolio(cash=self.cash, cost_model=self.cost_model)
        equity_curve = []

        for ts, price_row in prices.iterrows():
            price_map = {s: float(price_row[s]) for s in symbols if pd.notna(price_row[s])}
            # 1. mark to market BEFORE trading this bar
            # 2. rebalance to this bar's executed target
            target = {s: float(exec_weights.at[ts, s]) for s in symbols}
            portfolio.rebalance(ts, target, price_map)
            equity_curve.append(portfolio.equity(price_map))

        equity = pd.Series(equity_curve, index=prices.index, name="equity")
        return BacktestResult(
            equity=equity,
            weights=exec_weights,
            trades=portfolio.trades,
            prices=prices,
        )
