"""Dashboard data-access transforms and chart builders (no network, no Streamlit UI)."""
import json

import pandas as pd
import plotly.graph_objects as go
import pytest

from wealth.config import BotConfig
from wealth.dashboard import charts, components as ui, data_access as da


# --------------------------------------------------------------------------- #
# data_access
# --------------------------------------------------------------------------- #
def _write_journal(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_journal_view_parses_equity_and_orders():
    rows = [
        {"event": "tick", "timestamp": "2021-01-01T00:00:00", "bar_ts": "2021-01-01",
         "status": "traded", "cash": 5000, "equity": 10000,
         "target_weights": {"BTC/USDT": 1.0}, "prices": {"BTC/USDT": 100.0},
         "orders": [{"symbol": "BTC/USDT", "side": "buy", "quantity": 50, "price": 100.0}]},
        {"event": "tick", "timestamp": "2021-01-02T00:00:00", "bar_ts": "2021-01-02",
         "status": "traded", "cash": 0, "equity": 10500,
         "target_weights": {"BTC/USDT": 1.0}, "prices": {"BTC/USDT": 105.0}, "orders": []},
    ]
    view = da._journal_view(rows)
    assert list(view.equity.values) == [10000.0, 10500.0]
    assert len(view.orders) == 1
    assert view.last_prices == {"BTC/USDT": 105.0}
    assert view.target_weights == {"BTC/USDT": 1.0}


def test_load_journal_from_file(tmp_path):
    cfg = BotConfig(state_dir=str(tmp_path))
    _write_journal(
        cfg.journal_path,
        [{"event": "tick", "timestamp": "2021-01-01T00:00:00", "bar_ts": "b",
          "status": "traded", "equity": 10000, "cash": 0,
          "prices": {"BTC/USDT": 100.0}, "target_weights": {}, "orders": []}],
    )
    view = da.load_journal(cfg)
    assert view.equity.iloc[0] == 10000.0


def test_load_positions_marks_to_price(tmp_path):
    cfg = BotConfig(state_dir=str(tmp_path))
    with open(cfg.state_path, "w") as f:
        json.dump({"cash": 1000, "positions": {"BTC/USDT": {"quantity": 2, "avg_price": 100}}}, f)
    df = da.load_positions(cfg, {"BTC/USDT": 150.0})
    row = df.iloc[0]
    assert row["value"] == pytest.approx(300.0)
    assert row["unrealized"] == pytest.approx(100.0)  # 2 * (150-100)


def test_load_positions_empty_when_no_state(tmp_path):
    cfg = BotConfig(state_dir=str(tmp_path / "none"))
    df = da.load_positions(cfg)
    assert df.empty


def test_buy_and_hold_normalized():
    idx = pd.date_range("2021-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"A": [100.0, 110.0, 120.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    bench = da.buy_and_hold(prices, cash=1000.0)
    assert bench.iloc[0] == pytest.approx(1000.0)
    # avg of +20% and 0% -> +10%
    assert bench.iloc[-1] == pytest.approx(1100.0)


def test_monthly_returns_shape():
    idx = pd.date_range("2021-01-01", periods=120, freq="D")
    eq = pd.Series(range(100, 220), index=idx, dtype=float)
    pivot = da.monthly_returns(eq)
    assert not pivot.empty
    assert 2021 in pivot.index


def test_demo_backtest_populated():
    bv = da.demo_backtest()
    assert bv.equity.size > 100
    assert "total_return" in bv.metrics
    assert not bv.prices.empty


def test_demo_journal_and_positions():
    jv = da.demo_journal_view()
    assert jv.equity.size > 0
    pos = da.demo_positions()
    assert pos.iloc[0]["symbol"] == "BTC/USDT"


# --------------------------------------------------------------------------- #
# charts — every builder returns a Figure
# --------------------------------------------------------------------------- #
def test_chart_builders_return_figures():
    bv = da.demo_backtest()
    assert isinstance(charts.equity_area(bv.equity, bv.benchmark), go.Figure)
    assert isinstance(charts.drawdown_area(bv.equity), go.Figure)
    assert isinstance(charts.allocation_donut({"BTC/USDT": 0.6}), go.Figure)
    sym = bv.prices.columns[0]
    assert isinstance(
        charts.price_with_positions(bv.prices[sym], bv.weights[sym], sym), go.Figure)
    assert isinstance(charts.monthly_heatmap(da.monthly_returns(bv.equity)), go.Figure)


def test_charts_handle_empty():
    empty = pd.Series(dtype=float)
    assert isinstance(charts.equity_area(empty), go.Figure)
    assert isinstance(charts.drawdown_area(empty), go.Figure)
    assert isinstance(charts.allocation_donut({}), go.Figure)
    assert isinstance(charts.monthly_heatmap(pd.DataFrame()), go.Figure)


def test_allocation_donut_adds_cash():
    fig = charts.allocation_donut({"BTC/USDT": 0.5})
    labels = list(fig.data[0].labels)
    assert "Cash" in labels


# --------------------------------------------------------------------------- #
# components formatting
# --------------------------------------------------------------------------- #
def test_kpi_tone_classes():
    assert "neg" in ui.kpi("X", "-5%", tone=-0.05)
    assert "pos" in ui.kpi("X", "5%", tone=0.05)


def test_mode_badge_live_vs_paper():
    assert "LIVE" in ui.mode_badge("live")
    assert "PAPER" in ui.mode_badge("paper")


def test_metric_tiles_from_summary():
    tiles = ui.metric_tiles_from_summary(
        {"total_return": 0.1, "cagr": 0.05, "sharpe": 1.2, "max_drawdown": -0.2,
         "win_rate": 0.5, "exposure": 0.3}, equity_final=12345)
    assert len(tiles) == 7
    assert any("$12,345" in t for t in tiles)


# --------------------------------------------------------------------------- #
# app + cli wiring import cleanly
# --------------------------------------------------------------------------- #
def test_oos_bar_with_report():
    rng_data = da.demo_backtest()  # just to have data on hand; build a tiny report
    from wealth.tuning.walkforward import walk_forward
    from wealth.engine.costs import CostModel
    data = {"AAA": rng_data.prices.rename(columns={rng_data.prices.columns[0]: "close"})}
    # build a proper OHLCV frame for walk_forward
    close = rng_data.prices.iloc[:, 0]
    ohlcv = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    report = walk_forward("trend_breakout", {"AAA": ohlcv},
                          grid={"entry_n": [10, 20], "exit_n": [5, 10]},
                          n_folds=4, cost_model=CostModel(0, 0), periods_per_year=365)
    assert isinstance(charts.oos_bar(report), go.Figure)


def test_parse_params():
    from wealth.dashboard.app import _parse_params
    assert _parse_params("entry_n=20, exit_n=10") == {"entry_n": 20, "exit_n": 10}
    assert _parse_params("x=1.5, name=foo") == {"x": 1.5, "name": "foo"}
