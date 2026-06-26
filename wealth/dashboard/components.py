"""Reusable HTML/CSS UI fragments rendered through Streamlit markdown.

These build on the CSS classes defined in ``theme.CSS``.
"""
from __future__ import annotations

import math
from typing import List, Optional

from wealth.dashboard import theme


def _fmt_pct(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v * 100:.2f}%"


def _fmt_num(v) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "n/a" if v is None or math.isnan(v) else "∞"
    return f"{v:.2f}"


def _fmt_money(v) -> str:
    if v is None:
        return "n/a"
    return f"${v:,.0f}"


def kpi(label: str, value: str, sub: str = "", tone: Optional[float] = None) -> str:
    """One KPI tile. ``tone`` (a signed number) colors the value green/red."""
    cls = ""
    if tone is not None:
        cls = "pos" if tone >= 0 else "neg"
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return (
        f'<div class="kpi"><div class="label">{label}</div>'
        f'<div class="value {cls}">{value}</div>{sub_html}</div>'
    )


def kpi_row(st, tiles: List[str]) -> None:
    st.markdown(f'<div class="kpi-grid">{"".join(tiles)}</div>', unsafe_allow_html=True)


def hero(st, title: str, subtitle: str, mode: str) -> None:
    badge = mode_badge(mode)
    st.markdown(
        f'<div class="wealth-hero"><h1>{title}</h1>{badge}</div>'
        f'<p class="wealth-sub">{subtitle}</p>',
        unsafe_allow_html=True,
    )


def mode_badge(mode: str) -> str:
    is_live = str(mode).lower() == "live"
    cls = "badge-live" if is_live else "badge-paper"
    text = "LIVE — real funds" if is_live else "PAPER — simulated"
    return f'<span class="badge {cls}"><span class="dot"></span>{text}</span>'


def section(st, title: str) -> None:
    st.markdown(f'<div class="section">{title}</div>', unsafe_allow_html=True)


def metric_tiles_from_summary(summary: dict, equity_final: Optional[float] = None) -> List[str]:
    """Standard KPI set used on Overview / Backtest from a metrics summary dict."""
    tiles = []
    if equity_final is not None:
        tiles.append(kpi("Equity", _fmt_money(equity_final)))
    tiles += [
        kpi("Total return", _fmt_pct(summary.get("total_return")),
            tone=summary.get("total_return")),
        kpi("CAGR", _fmt_pct(summary.get("cagr")), tone=summary.get("cagr")),
        kpi("Sharpe", _fmt_num(summary.get("sharpe")), tone=summary.get("sharpe")),
        kpi("Max drawdown", _fmt_pct(summary.get("max_drawdown")),
            tone=summary.get("max_drawdown")),
        kpi("Win rate", _fmt_pct(summary.get("win_rate"))),
        kpi("Exposure", _fmt_pct(summary.get("exposure"))),
    ]
    return tiles
