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


# --------------------------------------------------------------------------- #
# polymarket support
# --------------------------------------------------------------------------- #
def _pm_cfg(tmp_path):
    from wealth.polymarket.config import PolymarketConfig

    return PolymarketConfig(state_dir=str(tmp_path))


def _write_pm_state(cfg):
    import os

    os.makedirs(cfg.state_dir, exist_ok=True)
    state = {
        "cash": 900.0,
        "realized_pnl": 0.0,
        "positions": {
            "TOKUP": {"market_slug": "btc-updown-15m-1", "size": 100.0, "avg_price": 0.52},
        },
        "meta": {"markets": {"btc-updown-15m-1": {
            "token_up": "TOKUP", "token_down": "TOKDN",
            "period_open": 100000.0, "end_ts": 900.0,
        }}},
    }
    with open(cfg.state_path, "w") as f:
        json.dump(state, f)


def test_is_polymarket(tmp_path):
    assert da.is_polymarket(_pm_cfg(tmp_path))
    assert not da.is_polymarket(BotConfig())


def test_load_positions_polymarket_shape(tmp_path):
    cfg = _pm_cfg(tmp_path)
    _write_pm_state(cfg)
    pos = da.load_positions(cfg, last_prices={"btc-updown-15m-1:UP": 0.60})
    assert len(pos) == 1
    row = pos.iloc[0]
    assert row["symbol"] == "btc-updown-15m-1:UP"
    assert row["quantity"] == 100.0
    assert row["avg_price"] == 0.52
    assert row["price"] == 0.60
    assert row["unrealized"] == pytest.approx(100 * (0.60 - 0.52))


def test_polymarket_view_aggregates_events():
    records = [
        {"event": "tick", "timestamp": "2026-07-03T09:00:00+00:00", "status": "hold",
         "spot": 100000.0, "sigma": 0.0005, "fair": {"m1": 0.55},
         "cash": 1000.0, "equity": 1000.0, "prices": {}, "orders": []},
        {"event": "resolution", "timestamp": "2026-07-03T09:15:00+00:00",
         "slug": "m1", "outcome": "up", "pnl": 40.0},
        {"event": "resolution", "timestamp": "2026-07-03T09:30:00+00:00",
         "slug": "m2", "outcome": "down", "pnl": -15.0},
        {"event": "resolution", "timestamp": "2026-07-03T09:45:00+00:00",
         "slug": "m3", "outcome": "up", "pnl": 0.0},   # no position held: not a loss
        {"event": "risk_block", "reason": "kill_switch"},
        {"event": "risk_block", "reason": "kill_switch"},
        {"event": "risk_block", "reason": "exposure_cap"},
    ]
    pv = da.polymarket_view(records)
    assert pv.spot == 100000.0
    assert pv.fair == {"m1": 0.55}
    assert len(pv.resolutions) == 3
    assert pv.realized_pnl == pytest.approx(25.0)
    assert (pv.wins, pv.losses) == (1, 1)
    assert pv.win_rate == pytest.approx(0.5)
    assert pv.risk_blocks == {"kill_switch": 2, "exposure_cap": 1}


def test_polymarket_view_empty():
    pv = da.polymarket_view([])
    assert pv.spot is None
    assert pv.win_rate is None
    assert pv.resolutions.empty


def test_load_config_returns_both_types(tmp_path):
    bot = tmp_path / "bot.yaml"
    bot.write_text("market: crypto\nsymbols: [BTC/USDT]\n")
    pm = tmp_path / "pm.yaml"
    pm.write_text("mode: paper\nseries: ['15m']\nedge_threshold: 0.05\n")
    assert isinstance(da.load_config(str(bot)), BotConfig)
    assert da.is_polymarket(da.load_config(str(pm)))


def _apptest_available():
    try:
        from streamlit.testing.v1 import AppTest  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _apptest_available(), reason="streamlit AppTest unavailable")
def test_app_renders_with_polymarket_config(tmp_path, monkeypatch):
    """Regression net for AttributeError crashes: every tab renders with a
    PolymarketConfig + a real polymarket journal, no exceptions."""
    from streamlit.testing.v1 import AppTest

    cfg_yaml = tmp_path / "polymarket.yaml"
    cfg_yaml.write_text(
        f"mode: paper\nseries: ['15m']\nedge_threshold: 0.05\nstate_dir: {tmp_path}\n"
    )
    _write_pm_state(_pm_cfg(tmp_path))
    _write_journal(str(tmp_path / "journal.jsonl"), [
        {"event": "market_open", "timestamp": "2026-07-03T09:00:00+00:00",
         "slug": "btc-updown-15m-1", "period_open": 100000.0},
        {"event": "tick", "timestamp": "2026-07-03T09:00:03+00:00", "bar_ts": "1",
         "status": "hold", "spot": 100000.0, "sigma": 0.0005,
         "fair": {"btc-updown-15m-1": 0.5}, "prices": {"btc-updown-15m-1:UP": 0.5},
         "orders": [], "cash": 1000.0, "equity": 1000.0},
        {"event": "tick", "timestamp": "2026-07-03T09:00:06+00:00", "bar_ts": "2",
         "status": "traded", "spot": 100100.0, "sigma": 0.0005,
         "fair": {"btc-updown-15m-1": 0.58},
         "prices": {"btc-updown-15m-1:UP": 0.52},
         "orders": [{"symbol": "btc-updown-15m-1:UP", "side": "buy",
                     "quantity": 100.0, "price": 0.52}],
         "cash": 948.0, "equity": 998.0},
        {"event": "resolution", "timestamp": "2026-07-03T09:15:00+00:00",
         "slug": "btc-updown-15m-1", "outcome": "up", "pnl": 48.0},
    ])
    monkeypatch.setenv("WEALTH_DASHBOARD_CONFIG", str(cfg_yaml))

    at = AppTest.from_file("wealth/dashboard/app.py", default_timeout=30)
    at.run()
    assert not at.exception, f"dashboard raised: {at.exception}"


@pytest.mark.skipif(not _apptest_available(), reason="streamlit AppTest unavailable")
def test_app_still_renders_with_bot_config(tmp_path, monkeypatch):
    cfg_yaml = tmp_path / "bot.yaml"
    cfg_yaml.write_text(f"market: crypto\nsymbols: [BTC/USDT]\nstate_dir: {tmp_path}\n")
    monkeypatch.setenv("WEALTH_DASHBOARD_CONFIG", str(cfg_yaml))
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("wealth/dashboard/app.py", default_timeout=30)
    at.run()
    assert not at.exception, f"dashboard raised: {at.exception}"
