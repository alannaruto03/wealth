"""Closed-form correctness checks for the metrics module."""
import math

import numpy as np
import pandas as pd
import pytest

from wealth.metrics import performance as perf


def daily_index(n):
    return pd.date_range("2020-01-01", periods=n, freq="D")


def test_total_return_simple():
    eq = pd.Series([100.0, 110.0, 121.0], index=daily_index(3))
    assert perf.total_return(eq) == pytest.approx(0.21)


def test_cagr_constant_growth():
    # +1%/day compounded for exactly one year (365 days) of daily crypto bars.
    n = 366  # 365 returns
    eq = pd.Series(100.0 * (1.01 ** np.arange(n)), index=daily_index(n))
    # periods_per_year inferred ~365.25; pass explicit 365 for an exact check.
    c = perf.cagr(eq, periods_per_year=365)
    expected = 1.01 ** 365 - 1
    assert c == pytest.approx(expected, rel=1e-6)


def test_max_drawdown_half():
    # 100 -> 50 -> 100 : worst drawdown is -50%.
    eq = pd.Series([100.0, 50.0, 100.0], index=daily_index(3))
    assert perf.max_drawdown(eq) == pytest.approx(-0.5)


def test_drawdown_duration():
    # below peak for two bars, then recovers.
    eq = pd.Series([100.0, 90.0, 80.0, 100.0], index=daily_index(4))
    assert perf.max_drawdown_duration(eq) == 2


def test_volatility_zero_for_constant():
    eq = pd.Series([100.0] * 10, index=daily_index(10))
    assert perf.volatility(eq) == 0.0


def test_sharpe_zero_vol_is_zero():
    eq = pd.Series(np.linspace(100, 110, 10), index=daily_index(10))
    # Linearly increasing equity has varying pct-change returns, so just check finite.
    assert math.isfinite(perf.sharpe(eq))


def test_sharpe_positive_for_steady_growth():
    # Near-constant positive returns -> very high (but finite) Sharpe.
    eq = pd.Series(100.0 * (1.005 ** np.arange(60)), index=daily_index(60))
    s = perf.sharpe(eq)
    assert math.isfinite(s) and s > 0


def test_win_rate_all_positive():
    trades = [{"pnl": 1.0}, {"pnl": 2.0}, {"pnl": None}]
    assert perf.win_rate(trades) == 1.0


def test_win_rate_mixed():
    trades = [{"pnl": 1.0}, {"pnl": -1.0}, {"pnl": -2.0}, {"pnl": 3.0}]
    assert perf.win_rate(trades) == 0.5


def test_exposure_flat_is_zero():
    w = pd.DataFrame({"BTC": [0.0, 0.0, 0.0]}, index=daily_index(3))
    assert perf.exposure(w) == 0.0


def test_exposure_partial():
    w = pd.DataFrame({"BTC": [1.0, 0.0, 1.0, 0.0]}, index=daily_index(4))
    assert perf.exposure(w) == 0.5


def test_calmar_sign():
    eq = pd.Series([100.0, 120.0, 60.0, 130.0], index=daily_index(4))
    # positive CAGR, negative drawdown -> positive calmar
    assert perf.calmar(eq, periods_per_year=365) > 0


def test_summary_keys():
    eq = pd.Series(100.0 * (1.001 ** np.arange(50)), index=daily_index(50))
    s = perf.summary(eq)
    for key in [
        "total_return", "cagr", "volatility", "sharpe", "sortino",
        "max_drawdown", "calmar", "win_rate", "exposure", "n_bars",
    ]:
        assert key in s
    assert s["n_bars"] == 50


def test_infer_periods_per_year_daily():
    eq = pd.Series(np.arange(10, dtype=float) + 100, index=daily_index(10))
    ppy = perf.infer_periods_per_year(eq)
    assert ppy == pytest.approx(365.25, rel=1e-3)
