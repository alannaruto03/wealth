"""Performance metrics computed from an equity curve (and optional trades).

All functions are pure and take a pandas Series equity curve indexed by time.
The annualization factor is inferred from the index when possible, or passed
explicitly (252 for daily stocks, 365 for daily crypto, etc.).
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252
CRYPTO_DAYS = 365


def infer_periods_per_year(equity: pd.Series, default: float = TRADING_DAYS) -> float:
    """Estimate how many bars make up a year from the index spacing.

    Falls back to ``default`` when the index is not a usable DatetimeIndex or
    has fewer than two points.
    """
    idx = equity.index
    if not isinstance(idx, pd.DatetimeIndex) or len(idx) < 3:
        return default
    # Resolution-independent: median spacing as a Timedelta -> seconds.
    deltas = idx.to_series().diff().dropna()
    if len(deltas) == 0:
        return default
    median_seconds = float(deltas.median().total_seconds())
    if median_seconds <= 0:
        return default
    year_seconds = 365.25 * 24 * 3600
    return year_seconds / median_seconds


def to_returns(equity: pd.Series) -> pd.Series:
    """Simple period-over-period returns of an equity curve."""
    return equity.astype(float).pct_change().dropna()


def total_return(equity: pd.Series) -> float:
    equity = equity.astype(float)
    if len(equity) < 2 or equity.iloc[0] == 0:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series, periods_per_year: Optional[float] = None) -> float:
    """Compound annual growth rate."""
    equity = equity.astype(float)
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    ppy = periods_per_year or infer_periods_per_year(equity)
    n_periods = len(equity) - 1
    years = n_periods / ppy
    if years <= 0:
        return 0.0
    growth = equity.iloc[-1] / equity.iloc[0]
    if growth <= 0:
        return -1.0
    return float(growth ** (1.0 / years) - 1.0)


def volatility(equity: pd.Series, periods_per_year: Optional[float] = None) -> float:
    """Annualized standard deviation of returns."""
    rets = to_returns(equity)
    if len(rets) < 2:
        return 0.0
    ppy = periods_per_year or infer_periods_per_year(equity)
    return float(rets.std(ddof=1) * math.sqrt(ppy))


def sharpe(
    equity: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: Optional[float] = None,
) -> float:
    """Annualized Sharpe ratio. ``risk_free`` is an annual rate."""
    rets = to_returns(equity)
    if len(rets) < 2:
        return 0.0
    ppy = periods_per_year or infer_periods_per_year(equity)
    rf_per_period = risk_free / ppy
    excess = rets - rf_per_period
    sd = excess.std(ddof=1)
    if sd == 0:
        return 0.0
    return float(excess.mean() / sd * math.sqrt(ppy))


def sortino(
    equity: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: Optional[float] = None,
) -> float:
    """Annualized Sortino ratio (downside-deviation denominator)."""
    rets = to_returns(equity)
    if len(rets) < 2:
        return 0.0
    ppy = periods_per_year or infer_periods_per_year(equity)
    rf_per_period = risk_free / ppy
    excess = rets - rf_per_period
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf") if excess.mean() > 0 else 0.0
    # Downside deviation uses the full sample count, per Sortino's definition.
    dd = math.sqrt(float((downside ** 2).sum()) / len(excess))
    if dd == 0:
        return 0.0
    return float(excess.mean() / dd * math.sqrt(ppy))


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Drawdown at each point: equity / running-peak - 1 (<= 0)."""
    equity = equity.astype(float)
    running_peak = equity.cummax()
    return equity / running_peak - 1.0


def max_drawdown(equity: pd.Series) -> float:
    """Worst peak-to-trough decline as a negative fraction (e.g. -0.5)."""
    dd = drawdown_series(equity)
    if len(dd) == 0:
        return 0.0
    return float(dd.min())


def max_drawdown_duration(equity: pd.Series) -> int:
    """Longest run (in bars) spent below a prior peak."""
    dd = drawdown_series(equity)
    longest = 0
    current = 0
    for v in dd:
        if v < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return int(longest)


def calmar(equity: pd.Series, periods_per_year: Optional[float] = None) -> float:
    """CAGR divided by the absolute max drawdown."""
    mdd = max_drawdown(equity)
    if mdd == 0:
        return 0.0
    return float(cagr(equity, periods_per_year) / abs(mdd))


def win_rate(trades: Optional[Iterable]) -> float:
    """Fraction of closed round-trip trades with positive realized PnL.

    ``trades`` is an iterable of objects/dicts exposing ``pnl``. Trades with
    ``pnl is None`` (e.g. opening legs) are ignored.
    """
    if trades is None:
        return 0.0
    pnls = []
    for t in trades:
        pnl = t.get("pnl") if isinstance(t, dict) else getattr(t, "pnl", None)
        if pnl is not None:
            pnls.append(pnl)
    if not pnls:
        return 0.0
    wins = sum(1 for p in pnls if p > 0)
    return float(wins / len(pnls))


def exposure(weights: Optional[pd.DataFrame]) -> float:
    """Fraction of bars with any nonzero position across symbols."""
    if weights is None or len(weights) == 0:
        return 0.0
    active = (weights.abs().sum(axis=1) > 1e-12)
    return float(active.mean())


def summary(
    equity: pd.Series,
    trades: Optional[Iterable] = None,
    weights: Optional[pd.DataFrame] = None,
    risk_free: float = 0.0,
    periods_per_year: Optional[float] = None,
) -> dict:
    """Aggregate every metric into a single dict for reports."""
    ppy = periods_per_year or infer_periods_per_year(equity)
    return {
        "total_return": total_return(equity),
        "cagr": cagr(equity, ppy),
        "volatility": volatility(equity, ppy),
        "sharpe": sharpe(equity, risk_free, ppy),
        "sortino": sortino(equity, risk_free, ppy),
        "max_drawdown": max_drawdown(equity),
        "max_drawdown_duration": max_drawdown_duration(equity),
        "calmar": calmar(equity, ppy),
        "win_rate": win_rate(trades),
        "exposure": exposure(weights),
        "periods_per_year": ppy,
        "n_bars": int(len(equity)),
    }
