"""wealth dashboard — Streamlit entry point.

Launch with:  wealth dashboard   (or)   streamlit run wealth/dashboard/app.py

The config path is read from the WEALTH_DASHBOARD_CONFIG env var (set by the CLI
launcher) or defaults to configs/bot.yaml; it can be changed in the sidebar.
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from wealth.dashboard import charts, components as ui, data_access as da, theme
from wealth.strategies.base import available_strategies


# --------------------------------------------------------------------------- #
# cached wrappers (keyed on hashable inputs)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def _cached_backtest(market, symbols, strategy, params_items, start, end, cash, comm, slip, tf):
    return da.run_backtest(
        market, list(symbols), strategy, dict(params_items), start, end,
        cash, comm, slip, timeframe=tf,
    )


@st.cache_data(show_spinner=False)
def _cached_demo():
    return da.demo_backtest()


# --------------------------------------------------------------------------- #
# sidebar — global controls
# --------------------------------------------------------------------------- #
def sidebar():
    st.sidebar.markdown("### ⚙️ Controls")
    default_cfg = os.environ.get("WEALTH_DASHBOARD_CONFIG", "configs/bot.yaml")
    cfg_path = st.sidebar.text_input("Config file", value=default_cfg)
    demo = st.sidebar.toggle(
        "Demo data", value=not _has_journal(cfg_path),
        help="Populate every panel with synthetic data (works offline).",
    )
    if st.sidebar.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.sidebar.caption(
        "Paper-first: no real money is traded unless the config is set to live."
    )
    return cfg_path, demo


def _has_journal(cfg_path: str) -> bool:
    try:
        cfg = da.load_config(cfg_path)
        return os.path.exists(cfg.journal_path) and bool(da.load_journal(cfg).equity.size)
    except Exception:
        return False


def _load_cfg(cfg_path: str):
    try:
        return da.load_config(cfg_path), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# --------------------------------------------------------------------------- #
# tabs
# --------------------------------------------------------------------------- #
def _effective_mode(cfg) -> str:
    """Paper unless the config really routes to a live venue. Works for both
    config flavors: the portfolio bot has a broker field, polymarket only mode."""
    if cfg is None:
        return "paper"
    broker = getattr(cfg, "broker", None)  # PolymarketConfig has no broker
    if broker is not None and broker == "paper":
        return "paper"
    return cfg.mode


def tab_overview(cfg, demo: bool):
    subtitle = "Polymarket BTC up/down — live performance" if (
        cfg is not None and da.is_polymarket(cfg)
    ) else "Systematic trading — live performance & research"
    ui.hero(st, "Wealth", subtitle, _effective_mode(cfg))

    if demo or cfg is None:
        bv = _cached_demo()
        equity, bench, summary = bv.equity, bv.benchmark, bv.metrics
        positions = da.demo_positions()
        weights = da.demo_journal_view().target_weights
    else:
        jv = da.load_journal(cfg)
        equity = jv.equity
        bench = None
        from wealth.metrics import performance as perf
        summary = perf.summary(
            equity, periods_per_year=getattr(cfg, "periods_per_year", None)
        ) if equity.size else {}
        positions = da.load_positions(cfg, jv.last_prices)
        weights = jv.target_weights

    final = float(equity.iloc[-1]) if equity is not None and equity.size else None
    ui.kpi_row(st, ui.metric_tiles_from_summary(summary, equity_final=final))

    c1, c2 = st.columns([2, 1])
    with c1:
        st.plotly_chart(charts.equity_area(equity, bench), use_container_width=True,
                        key="ov_equity")
        st.plotly_chart(charts.drawdown_area(equity), use_container_width=True, key="ov_dd")
    with c2:
        st.plotly_chart(charts.allocation_donut(weights), use_container_width=True,
                        key="ov_alloc")
        ui.section(st, "Open positions")
        _positions_table(positions, key="ov_pos")


def tab_live(cfg, demo: bool):
    ui.section(st, "Paper / live bot")
    if cfg is None:
        st.info("Load a valid config in the sidebar to see live bot state.")
        return

    if demo:
        jv = da.demo_journal_view()
        positions = da.demo_positions()
        st.caption("Showing demo data — toggle off in the sidebar to read your real journal.")
    else:
        jv = da.load_journal(cfg)
        positions = da.load_positions(cfg, jv.last_prices)

    cols = st.columns(4)
    final = float(jv.equity.iloc[-1]) if jv.equity.size else cfg.cash
    cols[0].markdown(ui.kpi("Equity", ui._fmt_money(final)), unsafe_allow_html=True)
    cols[1].markdown(ui.kpi("Open positions", str(len(positions))), unsafe_allow_html=True)
    unreal = float(positions["unrealized"].sum()) if not positions.empty else 0.0
    cols[2].markdown(ui.kpi("Unrealized PnL", ui._fmt_money(unreal), tone=unreal),
                     unsafe_allow_html=True)
    last_bar = jv.ticks["bar_ts"].iloc[-1] if not jv.ticks.empty else "—"
    cols[3].markdown(ui.kpi("Last bar", str(last_bar)[:16]), unsafe_allow_html=True)

    c1, c2 = st.columns([2, 1])
    with c1:
        st.plotly_chart(charts.equity_area(jv.equity), use_container_width=True,
                        key="live_equity")
    with c2:
        st.plotly_chart(charts.allocation_donut(jv.target_weights), use_container_width=True,
                        key="live_alloc")

    ui.section(st, "Open positions")
    _positions_table(positions, key="live_pos")

    ui.section(st, "Recent orders")
    if jv.orders is not None and not jv.orders.empty:
        st.dataframe(jv.orders.tail(20), use_container_width=True, hide_index=True,
                     key="live_orders")
    else:
        st.caption("No orders recorded yet.")

    if da.is_polymarket(cfg):
        ui.section(st, "Bot process")
        st.caption(
            "The polymarket bot ticks every few seconds — run it as its own process: "
            "`wealth polymarket run --config configs/polymarket.yaml`. "
            "This page reads its journal; hit Refresh in the sidebar for the latest."
        )
        return

    ui.section(st, "Manual control")
    st.caption("Run a single bot tick now (paper by default). Needs market data access.")
    disabled = demo
    if st.button("▶ Step one tick", disabled=disabled,
                 help="Disabled in demo mode" if disabled else None):
        try:
            rec = da.step_tick(cfg)
            st.success(f"Tick: {rec.get('status')} · equity {rec.get('equity', '—')}")
            st.json(rec)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Tick failed: {exc}")


def tab_polymarket(cfg, demo: bool):
    """Polymarket-specific view. Mobile-first: single column, responsive KPI
    grid, no side-by-side st.columns rows (those cramp on phones)."""
    ui.section(st, "BTC Up/Down markets")
    if cfg is None or not da.is_polymarket(cfg):
        st.info("Point the sidebar at configs/polymarket.yaml to see this tab.")
        return

    pv = da.load_polymarket_view(cfg)
    jv = da.load_journal(cfg)

    final = float(jv.equity.iloc[-1]) if jv.equity.size else cfg.cash
    ui.kpi_row(st, [
        ui.kpi("Equity", ui._fmt_money(final)),
        ui.kpi("Realized PnL", f"${pv.realized_pnl:,.2f}", tone=pv.realized_pnl),
        ui.kpi("Markets traded", str(pv.wins + pv.losses)),
        ui.kpi("Win rate", f"{pv.win_rate:.0%}" if pv.win_rate is not None else "—"),
    ])

    st.plotly_chart(charts.equity_area(jv.equity), use_container_width=True, key="pm_equity")

    ui.section(st, "Right now")
    if pv.spot is not None:
        line = f"**BTC spot** {pv.spot:,.0f}"
        if pv.sigma is not None:
            line += f"  ·  **σ/√s** {pv.sigma:.6f}"
        st.markdown(line)
        fair_txt = "  ·  ".join(f"{slug}: P(up) {p:.2f}" for slug, p in pv.fair.items()) or "—"
        st.caption(f"Model fair value — {fair_txt}")
    else:
        st.caption("No ticks yet — start the bot: "
                   "`wealth polymarket run --config configs/polymarket.yaml`")

    ui.section(st, "Resolved markets")
    if not pv.resolutions.empty:
        show = pv.resolutions.tail(25).iloc[::-1].copy()
        show["pnl"] = show["pnl"].round(2)
        st.dataframe(show, use_container_width=True, hide_index=True, key="pm_res")
    else:
        st.caption("No markets resolved yet.")

    if pv.risk_blocks:
        ui.section(st, "Risk blocks")
        st.dataframe(
            pd.DataFrame(
                sorted(pv.risk_blocks.items(), key=lambda kv: -kv[1]),
                columns=["reason", "count"],
            ),
            use_container_width=True, hide_index=True, key="pm_risk",
        )


def tab_backtest(cfg, demo: bool):
    ui.section(st, "Backtest")
    with st.form("backtest_form"):
        c = st.columns(4)
        market = c[0].selectbox("Market", ["crypto", "stocks"],
                                index=0 if (cfg is None or cfg.market == "crypto") else 1)
        default_syms = ",".join(cfg.symbols) if cfg else "BTC/USDT,ETH/USDT"
        symbols = c[1].text_input("Symbols", value=default_syms)
        strategy = c[2].selectbox("Strategy", available_strategies(),
                                  index=_strategy_index(cfg))
        cash = c[3].number_input("Cash", value=float(cfg.cash) if cfg else 10_000.0, step=1000.0)
        c2 = st.columns(4)
        start = c2[0].text_input("Start", value="2021-01-01")
        end = c2[1].text_input("End", value="2024-12-31")
        comm = c2[2].number_input("Commission bps", value=5.0, step=1.0)
        slip = c2[3].number_input("Slippage bps", value=5.0, step=1.0)
        params_text = st.text_input(
            "Params (k=v, comma-separated)",
            value=_params_text(cfg) if cfg else "entry_n=20, exit_n=10",
        )
        submitted = st.form_submit_button("Run backtest", use_container_width=True)

    if demo and not submitted:
        st.caption("Showing a demo backtest. Fill the form and run to use real data.")
        _render_backtest(_cached_demo(), "BTC/USDT")
        return

    if submitted:
        try:
            params = _parse_params(params_text)
            syms = tuple(s.strip() for s in symbols.split(",") if s.strip())
            with st.spinner("Running backtest…"):
                bv = _cached_backtest(
                    market, syms, strategy, tuple(sorted(params.items())),
                    start, end, float(cash), float(comm), float(slip), "1d",
                )
            _render_backtest(bv, syms[0] if syms else "")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backtest failed: {exc}")
            st.caption("Tip: this sandbox blocks live data; on your machine with internet it works. "
                       "Use Demo data to preview the UI.")


def tab_tuning(cfg, demo: bool):
    ui.section(st, "Walk-forward tuning")
    st.caption(
        "Parameters are optimized in-sample then validated on the *next, unseen* "
        "window. We recommend the most robust out-of-sample params — never the "
        "best in-sample fit. This is the guardrail against curve-fitting."
    )
    if cfg is None:
        st.info("Load a valid config to run tuning.")
        return
    c = st.columns(3)
    metric = c[0].selectbox("Metric", ["calmar", "sharpe", "sortino", "cagr"], index=0)
    folds = c[1].number_input("Folds", value=4, min_value=2, max_value=8, step=1)
    run = c[2].button("Run walk-forward", use_container_width=True)
    if run:
        if demo:
            st.warning("Tuning needs market data — turn off Demo data and run on your machine.")
            return
        try:
            with st.spinner("Walking forward…"):
                report = da.run_tuning(cfg, metric=metric, folds=int(folds))
            badge = "✅ ACCEPTED" if report.accepted else "⚠️ REJECTED"
            st.subheader(f"{badge} — {report.metric}")
            st.plotly_chart(charts.oos_bar(report), use_container_width=True, key="tune_oos")
            rows = [
                {"fold": f.fold, "in_sample": round(f.in_sample_score, 3),
                 "out_sample": round(f.out_sample_score, 3), "params": str(f.best_params)}
                for f in report.folds
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                         key="tune_folds")
            if report.accepted:
                st.success(f"Recommended params (review before applying): "
                           f"{report.recommended_params}  ·  median OOS "
                           f"{report.recommended_oos_median:.3f}")
            else:
                st.info("No robust out-of-sample improvement — keep current params. "
                        f"{report.reason}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Tuning failed: {exc}")


def tab_config(cfg, cfg_path: str):
    ui.section(st, "Configuration")
    if cfg is None:
        st.info("No valid config loaded.")
        return
    st.markdown(ui.mode_badge(_effective_mode(cfg)), unsafe_allow_html=True)
    if _effective_mode(cfg) != "paper":
        st.error("⚠️ This config is set to trade with real funds.")
    else:
        st.success("Safe: paper trading — simulated money on live prices/books.")

    st.write("**Current settings**")
    st.json({k: v for k, v in cfg.to_dict().items()})

    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            raw = f.read()
        edited = st.text_area("Edit YAML", value=raw, height=320)
        confirm_live = st.checkbox("I understand this may enable live trading", value=False)
        if st.button("💾 Save config"):
            try:
                import yaml
                if da.is_polymarket(cfg):
                    from wealth.polymarket.config import PolymarketConfig as ConfigCls
                else:
                    from wealth.config import BotConfig as ConfigCls
                test = yaml.safe_load(edited) or {}
                ConfigCls(**{k: v for k, v in test.items()})  # raises on bad keys/types
                if (test.get("mode") == "live" or test.get("broker") not in (None, "paper")) \
                        and not confirm_live:
                    st.warning("Tick the confirmation box to save a live-trading config.")
                else:
                    with open(cfg_path, "w") as f:
                        f.write(edited)
                    st.success("Saved. Refresh to reload.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Not saved — invalid config: {exc}")


# --------------------------------------------------------------------------- #
# small render helpers
# --------------------------------------------------------------------------- #
def _positions_table(positions: pd.DataFrame, key: str = "pos"):
    if positions is None or positions.empty:
        st.caption("Flat — no open positions.")
        return
    show = positions.copy()
    for col in ["quantity", "avg_price", "price", "value", "unrealized"]:
        if col in show:
            show[col] = show[col].astype(float).round(2)
    st.dataframe(show, use_container_width=True, hide_index=True, key=key)


def _render_backtest(bv, symbol: str):
    ui.kpi_row(st, ui.metric_tiles_from_summary(
        bv.metrics, equity_final=float(bv.equity.iloc[-1]) if bv.equity.size else None))
    c1, c2 = st.columns([2, 1])
    with c1:
        st.plotly_chart(charts.equity_area(bv.equity, bv.benchmark), use_container_width=True,
                        key="bt_equity")
        st.plotly_chart(charts.drawdown_area(bv.equity), use_container_width=True, key="bt_dd")
    with c2:
        if symbol and symbol in bv.prices.columns:
            w = bv.weights[symbol] if symbol in bv.weights.columns else None
            st.plotly_chart(
                charts.price_with_positions(bv.prices[symbol], w, symbol),
                use_container_width=True, key="bt_price",
            )
        st.plotly_chart(charts.monthly_heatmap(da.monthly_returns(bv.equity)),
                        use_container_width=True, key="bt_heatmap")
    ui.section(st, "Trades")
    if bv.trades is not None and not bv.trades.empty:
        st.dataframe(bv.trades.tail(50), use_container_width=True, hide_index=True,
                     key="bt_trades")
    else:
        st.caption("No trades generated.")


def _strategy_index(cfg) -> int:
    strats = available_strategies()
    if cfg and cfg.strategy in strats:
        return strats.index(cfg.strategy)
    return strats.index("trend_breakout") if "trend_breakout" in strats else 0


def _params_text(cfg) -> str:
    return ", ".join(f"{k}={v}" for k, v in (cfg.params or {}).items())


def _parse_params(text: str) -> dict:
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        try:
            out[k] = int(v)
        except ValueError:
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    st.set_page_config(page_title="wealth · trading dashboard", page_icon="📈", layout="wide")
    theme.inject(st)
    cfg_path, demo = sidebar()
    cfg, err = _load_cfg(cfg_path)
    if err:
        st.sidebar.error(f"Config error: {err}")

    if cfg is not None and da.is_polymarket(cfg):
        # Backtest/Tuning are portfolio-bot engines; polymarket gets its own tab.
        overview, live, polymarket, config = st.tabs(
            ["📊 Overview", "🤖 Live bot", "🎲 Polymarket", "⚙️ Config"]
        )
        with overview:
            tab_overview(cfg, demo)
        with live:
            tab_live(cfg, demo)
        with polymarket:
            tab_polymarket(cfg, demo)
        with config:
            tab_config(cfg, cfg_path)
        return

    overview, live, backtest, tuning, config = st.tabs(
        ["📊 Overview", "🤖 Live bot", "🧪 Backtest", "🎛️ Tuning", "⚙️ Config"]
    )
    with overview:
        tab_overview(cfg, demo)
    with live:
        tab_live(cfg, demo)
    with backtest:
        tab_backtest(cfg, demo)
    with tuning:
        tab_tuning(cfg, demo)
    with config:
        tab_config(cfg, cfg_path)


# Streamlit sets __name__ == "__main__" for the entry script, so this runs under
# `streamlit run` / `wealth dashboard` but NOT on plain import (e.g. in tests).
if __name__ == "__main__":
    main()
