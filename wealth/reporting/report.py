"""Assemble a human-readable performance report from a backtest/journal."""
from __future__ import annotations

import os
from typing import Iterable, Optional

import pandas as pd

from wealth.metrics import performance as perf

_LABELS = [
    ("total_return", "Total return", "pct"),
    ("cagr", "CAGR", "pct"),
    ("volatility", "Volatility (ann.)", "pct"),
    ("sharpe", "Sharpe", "num"),
    ("sortino", "Sortino", "num"),
    ("max_drawdown", "Max drawdown", "pct"),
    ("max_drawdown_duration", "Max DD duration (bars)", "int"),
    ("calmar", "Calmar", "num"),
    ("win_rate", "Win rate", "pct"),
    ("exposure", "Exposure", "pct"),
    ("n_bars", "Bars", "int"),
]


def _fmt(value, kind: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    if kind == "pct":
        return f"{value * 100:.2f}%"
    if kind == "int":
        return f"{int(value)}"
    if value in (float("inf"), float("-inf")):
        return "inf"
    return f"{value:.3f}"


def build_report(
    equity: pd.Series,
    trades: Optional[Iterable] = None,
    weights: Optional[pd.DataFrame] = None,
    title: str = "Backtest report",
    periods_per_year: Optional[float] = None,
) -> str:
    s = perf.summary(equity, trades, weights, periods_per_year=periods_per_year)
    rows = [f"# {title}", "", "| Metric | Value |", "|---|---|"]
    for key, label, kind in _LABELS:
        rows.append(f"| {label} | {_fmt(s.get(key), kind)} |")
    if len(equity):
        rows.append(f"| Start | {equity.index[0]} |")
        rows.append(f"| End | {equity.index[-1]} |")
        rows.append(f"| Final equity | {equity.iloc[-1]:.2f} |")
    return "\n".join(rows)


def write_report(
    out_dir: str,
    equity: pd.Series,
    trades=None,
    weights=None,
    title: str = "Backtest report",
    periods_per_year: Optional[float] = None,
    make_plots: bool = True,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    md = build_report(equity, trades, weights, title, periods_per_year)
    summary_path = os.path.join(out_dir, "summary.md")
    with open(summary_path, "w") as f:
        f.write(md + "\n")

    if make_plots and len(equity) > 1:
        try:
            from wealth.reporting.plots import plot_equity, plot_drawdown

            plot_equity(equity, os.path.join(out_dir, "equity.png"), title)
            plot_drawdown(equity, os.path.join(out_dir, "drawdown.png"), title)
        except Exception as exc:  # plotting is optional; never fail the run
            print(f"(plotting skipped: {exc})")
    return summary_path
