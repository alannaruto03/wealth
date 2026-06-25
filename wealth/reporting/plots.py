"""matplotlib plots: equity curve and underwater (drawdown) chart."""
from __future__ import annotations

import pandas as pd

from wealth.metrics.performance import drawdown_series


def _setup():
    import matplotlib
    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt
    return plt


def plot_equity(equity: pd.Series, path: str, title: str = "Equity") -> None:
    plt = _setup()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(equity.index, equity.values, color="#1f77b4", linewidth=1.3)
    ax.set_title(f"{title} — equity curve")
    ax.set_ylabel("equity")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_drawdown(equity: pd.Series, path: str, title: str = "Drawdown") -> None:
    plt = _setup()
    dd = drawdown_series(equity) * 100.0
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.fill_between(dd.index, dd.values, 0, color="#d62728", alpha=0.4)
    ax.set_title(f"{title} — drawdown")
    ax.set_ylabel("drawdown %")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
