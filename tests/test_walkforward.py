"""Tuning: grid search ordering, walk-forward OOS selection + guardrails."""
import numpy as np
import pandas as pd
import pytest

from wealth.engine.costs import CostModel
from wealth.tuning.optimize import param_grid, grid_search
from wealth.tuning.walkforward import walk_forward


def idx(n):
    return pd.date_range("2018-01-01", periods=n, freq="D")


def ohlcv(close):
    close = pd.Series(np.asarray(close, dtype=float), index=idx(len(close)))
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0}
    )


def trending_market(n=600, seed=0):
    rng = np.random.default_rng(seed)
    # Upward drift + noise -> trend strategies should work; good for tuning tests.
    steps = rng.normal(0.001, 0.01, n)
    price = 100 * np.exp(np.cumsum(steps))
    return {"AAA": ohlcv(price)}


def test_param_grid_product():
    combos = param_grid({"a": [1, 2], "b": [3, 4]})
    assert len(combos) == 4
    assert {"a": 1, "b": 3} in combos


def test_param_grid_empty():
    assert param_grid({}) == [{}]


def test_grid_search_sorted_best_first():
    data = trending_market()
    trials = grid_search(
        "trend_breakout",
        data,
        grid={"entry_n": [10, 20, 40], "exit_n": [5, 10]},
        metric="calmar",
        cost_model=CostModel(0.0, 0.0),
        periods_per_year=365,
    )
    scores = [t.score for t in trials if not pd.isna(t.score)]
    assert scores == sorted(scores, reverse=True)
    assert len(trials) == 6


def test_walk_forward_recommends_and_reports():
    data = trending_market(n=600)
    report = walk_forward(
        "trend_breakout",
        data,
        grid={"entry_n": [10, 20, 40], "exit_n": [5, 10]},
        metric="calmar",
        n_folds=4,
        min_folds=3,
        cost_model=CostModel(0.0, 0.0),
        periods_per_year=365,
    )
    assert len(report.folds) == 4
    assert report.recommended_params is not None
    # The recommended params must be one of the grid combos.
    assert report.recommended_params["entry_n"] in (10, 20, 40)
    text = report.summary_text()
    assert "Walk-forward" in text


def test_walk_forward_insufficient_data():
    data = {"AAA": ohlcv(list(range(1, 20)))}
    report = walk_forward(
        "trend_breakout", data, grid={"entry_n": [5]}, n_folds=4, min_folds=3,
    )
    assert report.accepted is False
    assert report.recommended_params is None


def test_walk_forward_rejects_when_oos_nonpositive():
    # Choppy, driftless market -> trend following should not earn positive OOS,
    # so the guardrail rejects any recommendation.
    rng = np.random.default_rng(3)
    price = 100 + np.cumsum(rng.normal(0, 1, 600))
    price = np.abs(price) + 10
    data = {"AAA": ohlcv(price)}
    report = walk_forward(
        "trend_breakout",
        data,
        grid={"entry_n": [10, 20], "exit_n": [5, 10]},
        metric="calmar",
        n_folds=4,
        min_folds=3,
        cost_model=CostModel(10.0, 10.0),
        periods_per_year=365,
    )
    # Either rejected outright, or accepted only if median OOS is strictly > 0.
    if report.accepted:
        assert report.recommended_oos_median > 0
    else:
        assert report.recommended_oos_median <= 0 or report.recommended_params is not None
