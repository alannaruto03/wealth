"""Parameter search over a strategy grid, scored by a backtest metric.

Pure in-sample optimization — used as a building block by the walk-forward
loop, which adds the crucial out-of-sample validation. Used alone it WILL
overfit; that is intentional separation of concerns.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd

from wealth.engine.backtest import Backtester
from wealth.engine.costs import CostModel
from wealth.metrics import performance as perf
from wealth.strategies.base import get_strategy


def param_grid(grid: Dict[str, List]) -> List[Dict]:
    """Cartesian product of {param -> [values]} into a list of param dicts."""
    if not grid:
        return [{}]
    keys = list(grid)
    combos = itertools.product(*(grid[k] for k in keys))
    return [dict(zip(keys, vals)) for vals in combos]


def score_metric(result, metric: str, periods_per_year: Optional[float]) -> float:
    s = perf.summary(result.equity, result.trades, result.weights, periods_per_year=periods_per_year)
    return float(s.get(metric, float("nan")))


@dataclass
class TrialResult:
    params: Dict
    score: float


def grid_search(
    strategy_name: str,
    data: Dict[str, pd.DataFrame],
    grid: Dict[str, List],
    metric: str = "calmar",
    cost_model: Optional[CostModel] = None,
    cash: float = 100_000.0,
    periods_per_year: Optional[float] = None,
) -> List[TrialResult]:
    """Backtest every parameter combo on ``data``; return trials sorted best-first."""
    bt = Backtester(cost_model=cost_model or CostModel(), cash=cash)
    trials: List[TrialResult] = []
    for params in param_grid(grid):
        strat = get_strategy(strategy_name, **params)
        result = bt.run(strat, data)
        score = score_metric(result, metric, periods_per_year)
        trials.append(TrialResult(params=params, score=score))

    def sort_key(t: TrialResult):
        return float("-inf") if pd.isna(t.score) else t.score

    return sorted(trials, key=sort_key, reverse=True)
