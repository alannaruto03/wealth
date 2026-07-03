"""Pure Plotly figure builders for the dashboard.

No Streamlit imports — every function takes data and returns a
``plotly.graph_objects.Figure``, so they are unit-testable. The dark template
is registered by ``theme.inject``; these set per-figure accents on top.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from wealth.dashboard import theme
from wealth.metrics.performance import drawdown_series


def _empty(msg: str = "No data") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(color=theme.MUTED, size=14))
    fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False), height=260)
    return fig


def equity_area(equity: pd.Series, benchmark: Optional[pd.Series] = None,
                height: int = 360) -> go.Figure:
    if equity is None or len(equity) == 0:
        return _empty("No equity data yet")
    up = equity.iloc[-1] >= equity.iloc[0]
    line = theme.ACCENT if up else theme.RED
    fill = "rgba(0,208,156,0.12)" if up else "rgba(255,77,77,0.12)"
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity.index, y=equity.values, name="Strategy", mode="lines",
        line=dict(color=line, width=2.2), fill="tozeroy", fillcolor=fill,
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f}<extra></extra>",
    ))
    if benchmark is not None and len(benchmark):
        fig.add_trace(go.Scatter(
            x=benchmark.index, y=benchmark.values, name="Buy & Hold", mode="lines",
            line=dict(color=theme.MUTED, width=1.4, dash="dot"),
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f}<extra></extra>",
        ))
    fig.update_layout(
        height=height, title="Equity curve",
        yaxis=dict(rangemode="tozero"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
    )
    return fig


def drawdown_area(equity: pd.Series, height: int = 220) -> go.Figure:
    if equity is None or len(equity) == 0:
        return _empty("No drawdown data")
    dd = drawdown_series(equity) * 100.0
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values, mode="lines", line=dict(color=theme.RED, width=1.2),
        fill="tozeroy", fillcolor="rgba(255,77,77,0.18)",
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1f}%<extra></extra>",
    ))
    fig.update_layout(height=height, title="Drawdown", yaxis=dict(ticksuffix="%"))
    return fig


def allocation_donut(target_weights: Dict[str, float], height: int = 300) -> go.Figure:
    weights = {k: float(v) for k, v in (target_weights or {}).items() if abs(float(v)) > 1e-9}
    invested = sum(abs(v) for v in weights.values())
    labels = list(weights.keys())
    values = [abs(v) for v in weights.values()]
    cash = max(0.0, 1.0 - invested)
    if cash > 1e-9:
        labels.append("Cash")
        values.append(cash)
    if not values:
        labels, values = ["Cash"], [1.0]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.62, sort=False,
        marker=dict(colors=theme.SEQUENCE + [theme.MUTED], line=dict(color=theme.BG, width=2)),
        textinfo="label+percent", textfont=dict(size=12),
        hovertemplate="%{label}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(height=height, title="Current allocation", showlegend=False)
    return fig


def price_with_positions(price: pd.Series, weights: pd.Series, symbol: str = "",
                         height: int = 320) -> go.Figure:
    if price is None or len(price) == 0:
        return _empty("No price data")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=price.index, y=price.values, mode="lines", name=symbol or "price",
        line=dict(color=theme.BLUE, width=1.6),
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<extra></extra>",
    ))
    # Shade bars where we hold a long position.
    if weights is not None and len(weights):
        held = weights.reindex(price.index).fillna(0.0) > 0
        fig.add_trace(go.Scatter(
            x=price.index, y=np.where(held, price.values, np.nan), mode="lines",
            name="in position", line=dict(color=theme.ACCENT, width=3),
            hoverinfo="skip",
        ))
    fig.update_layout(height=height, title=f"Price & positions {symbol}".strip(),
                      legend=dict(orientation="h", y=1.02, x=0))
    return fig


def monthly_heatmap(pivot: pd.DataFrame, height: int = 320) -> go.Figure:
    if pivot is None or pivot.empty:
        return _empty("Not enough history for monthly returns")
    z = pivot.values * 100.0
    fig = go.Figure(go.Heatmap(
        z=z, x=list(pivot.columns), y=[str(y) for y in pivot.index],
        colorscale=[[0, theme.RED], [0.5, "#1c2230"], [1, theme.ACCENT]],
        zmid=0, colorbar=dict(title="%", outlinewidth=0),
        hovertemplate="%{y} %{x}<br>%{z:.1f}%<extra></extra>",
        text=np.round(z, 1), texttemplate="%{text}", textfont=dict(size=10),
    ))
    fig.update_layout(height=height, title="Monthly returns (%)")
    return fig


def window_pnl_bars(windows: pd.DataFrame, height: int = 300) -> go.Figure:
    """Per-window PnL bars (green/red) with a cumulative PnL line overlay."""
    if windows is None or windows.empty or "pnl" not in windows:
        return _empty("No settled windows yet")
    pnl = windows["pnl"].astype(float)
    x = pd.to_datetime(windows["timestamp"]) if "timestamp" in windows \
        else pd.RangeIndex(len(pnl))
    colors = [theme.ACCENT if v >= 0 else theme.RED for v in pnl]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=pnl.values, name="Window PnL", marker_color=colors,
        hovertemplate="%{x}<br>%{y:+.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=pnl.cumsum().values, name="Cumulative", mode="lines",
        line=dict(color=theme.BLUE, width=1.8),
        hovertemplate="%{x}<br>%{y:+.2f}<extra></extra>",
    ))
    fig.update_layout(height=height, title="PnL per window",
                      legend=dict(orientation="h", y=1.02, x=0))
    return fig


def hit_rate_gauge(hit_rate: Optional[float], breakeven: float = 0.93,
                   height: int = 260) -> go.Figure:
    """Settled hit rate vs the break-even rate implied by the entry band."""
    if hit_rate is None:
        return _empty("No settled positions yet")
    color = theme.ACCENT if hit_rate >= breakeven else theme.RED
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta", value=hit_rate * 100.0,
        delta=dict(reference=breakeven * 100.0, suffix="%"),
        number=dict(suffix="%"),
        gauge=dict(
            axis=dict(range=[80, 100], ticksuffix="%"),
            bar=dict(color=color),
            threshold=dict(line=dict(color=theme.YELLOW, width=2),
                           thickness=0.8, value=breakeven * 100.0),
        ),
    ))
    fig.update_layout(height=height, title="Hit rate vs break-even")
    return fig


def oos_bar(report, height: int = 300) -> go.Figure:
    """Bar chart of in-sample vs out-of-sample score per walk-forward fold."""
    folds = getattr(report, "folds", []) or []
    if not folds:
        return _empty("No tuning folds")
    x = [f"Fold {f.fold}" for f in folds]
    is_scores = [f.in_sample_score for f in folds]
    oos_scores = [f.out_sample_score for f in folds]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=x, y=is_scores, name="In-sample", marker_color=theme.MUTED))
    fig.add_trace(go.Bar(x=x, y=oos_scores, name="Out-of-sample", marker_color=theme.ACCENT))
    fig.update_layout(height=height, barmode="group", title="Walk-forward: in- vs out-of-sample",
                      legend=dict(orientation="h", y=1.02, x=0))
    return fig
