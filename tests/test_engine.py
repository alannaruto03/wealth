"""Engine correctness: PnL accounting, no-lookahead, cost monotonicity."""
import numpy as np
import pandas as pd
import pytest

from wealth.engine.backtest import Backtester
from wealth.engine.costs import CostModel
from wealth.engine.portfolio import Portfolio
from wealth.strategies.base import Strategy, register, STRATEGY_REGISTRY


def idx(n):
    return pd.date_range("2020-01-01", periods=n, freq="D")


def ohlcv(close):
    close = pd.Series(list(close), index=idx(len(close)), dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0}
    )


class AlwaysLong(Strategy):
    """Target 100% long in the single symbol every bar."""
    name = "_always_long"

    def generate_weights(self, data):
        closes = self._close_frame(data)
        return pd.DataFrame(1.0, index=closes.index, columns=closes.columns)


def test_buy_and_hold_matches_price_relative_zero_cost():
    prices = [100.0, 110.0, 121.0, 133.1]
    data = {"X": ohlcv(prices)}
    bt = Backtester(cost_model=CostModel(0.0, 0.0), cash=1000.0)
    res = bt.run(AlwaysLong(), data)
    # Signal shifts by one bar: we are flat on bar 0, fully invested from bar 1.
    # Entering at price[1]=110, equity then tracks the price relative from there.
    # Final/han d-computed: invested 1000 at 110 -> 9.0909 units; value at 133.1.
    units = 1000.0 / 110.0
    expected_final = units * 133.1
    assert res.final_equity == pytest.approx(expected_final, rel=1e-9)


def test_costs_reduce_equity():
    prices = list(100 + np.arange(20, dtype=float))
    data = {"X": ohlcv(prices)}
    free = Backtester(cost_model=CostModel(0.0, 0.0), cash=1000.0).run(AlwaysLong(), data)
    costly = Backtester(cost_model=CostModel(10.0, 10.0), cash=1000.0).run(AlwaysLong(), data)
    assert costly.final_equity < free.final_equity


def test_no_lookahead_first_bar_flat():
    prices = [100.0, 200.0, 50.0]
    data = {"X": ohlcv(prices)}
    res = Backtester(cost_model=CostModel(0.0, 0.0), cash=1000.0).run(AlwaysLong(), data)
    # Bar 0 must be untouched by any signal -> equity equals starting cash.
    assert res.equity.iloc[0] == pytest.approx(1000.0)


def test_portfolio_realized_pnl_roundtrip():
    pf = Portfolio(cash=1000.0, cost_model=CostModel(0.0, 0.0))
    # Buy 10 @ 100, then sell 10 @ 120 -> realized +200.
    pf._execute("t1", "X", 10.0, 100.0)
    assert pf.cash == pytest.approx(0.0)
    assert pf.positions["X"] == pytest.approx(10.0)
    pf._execute("t2", "X", 0.0, 120.0)
    assert pf.positions["X"] == pytest.approx(0.0)
    assert pf.cash == pytest.approx(1200.0)
    closing = [t for t in pf.trades if t.pnl is not None]
    assert closing[-1].pnl == pytest.approx(200.0)


def test_portfolio_equity_marks_to_market():
    pf = Portfolio(cash=1000.0, cost_model=CostModel(0.0, 0.0))
    pf._execute("t1", "X", 5.0, 100.0)  # spend 500, hold 5 units
    assert pf.equity({"X": 100.0}) == pytest.approx(1000.0)
    assert pf.equity({"X": 120.0}) == pytest.approx(1100.0)


def test_partial_reduce_keeps_avg_cost():
    pf = Portfolio(cash=10000.0, cost_model=CostModel(0.0, 0.0))
    pf._execute("t1", "X", 10.0, 100.0)
    pf._execute("t2", "X", 4.0, 150.0)   # reduce 6 units @150 -> realized 6*(150-100)=300
    closing = [t for t in pf.trades if t.pnl is not None]
    assert closing[-1].pnl == pytest.approx(300.0)
    assert pf.avg_cost["X"] == pytest.approx(100.0)
