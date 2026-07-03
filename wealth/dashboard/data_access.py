"""Data access for the dashboard — reuses the bot's existing APIs.

Pure functions (no Streamlit imports) so they are unit-testable. The app layer
adds caching on top of `run_backtest` / `run_tuning`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from wealth.config import BotConfig
from wealth.engine.backtest import Backtester
from wealth.engine.costs import CostModel
from wealth.live.journal import Journal
from wealth.metrics import performance as perf
from wealth.strategies.base import get_strategy


# --------------------------------------------------------------------------- #
# config + journal + positions
# --------------------------------------------------------------------------- #
def load_config(path: str) -> BotConfig:
    return BotConfig.from_yaml(path)


@dataclass
class JournalView:
    equity: pd.Series
    ticks: pd.DataFrame      # one row per tick (timestamp, bar_ts, status, cash, equity)
    orders: pd.DataFrame     # one row per order (timestamp, symbol, side, quantity, price)
    last_prices: Dict[str, float]
    target_weights: Dict[str, float]


def load_journal(cfg: BotConfig) -> JournalView:
    journal = Journal(cfg.journal_path)
    records = journal.records()
    return _journal_view(records)


def _journal_view(records: List[dict]) -> JournalView:
    ticks_rows, order_rows = [], []
    last_prices: Dict[str, float] = {}
    target_weights: Dict[str, float] = {}
    for r in records:
        if r.get("event") != "tick":
            continue
        ticks_rows.append(
            {
                "timestamp": r.get("timestamp"),
                "bar_ts": r.get("bar_ts"),
                "status": r.get("status"),
                "cash": r.get("cash"),
                "equity": r.get("equity"),
            }
        )
        if r.get("prices"):
            last_prices = r["prices"]
        if r.get("target_weights"):
            target_weights = r["target_weights"]
        for o in r.get("orders", []) or []:
            order_rows.append({"timestamp": r.get("timestamp"), **o})

    ticks = pd.DataFrame(ticks_rows)
    orders = pd.DataFrame(order_rows)
    equity = pd.Series(dtype=float, name="equity")
    if not ticks.empty and ticks["equity"].notna().any():
        eq = ticks.dropna(subset=["equity"]).copy()
        eq.index = pd.to_datetime(eq["timestamp"])
        equity = eq["equity"].astype(float)
        equity.name = "equity"
    return JournalView(equity, ticks, orders, last_prices, target_weights)


def load_positions(cfg: BotConfig, last_prices: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """Read broker state JSON and mark positions to last known prices."""
    path = cfg.state_path
    if not os.path.exists(path):
        return pd.DataFrame(columns=["symbol", "quantity", "avg_price", "price", "value", "unrealized"])
    with open(path) as f:
        state = json.load(f)
    last_prices = last_prices or {}
    rows = []
    for sym, p in state.get("positions", {}).items():
        qty = float(p.get("quantity", 0.0))
        avg = float(p.get("avg_price", 0.0))
        price = float(last_prices.get(sym, avg) or avg)
        rows.append(
            {
                "symbol": sym,
                "quantity": qty,
                "avg_price": avg,
                "price": price,
                "value": qty * price,
                "unrealized": qty * (price - avg),
            }
        )
    return pd.DataFrame(rows)


def account_cash(cfg: BotConfig) -> float:
    path = cfg.state_path
    if not os.path.exists(path):
        return cfg.cash
    with open(path) as f:
        return float(json.load(f).get("cash", cfg.cash))


# --------------------------------------------------------------------------- #
# backtest + tuning (mirror cmd_backtest / cmd_tune wiring)
# --------------------------------------------------------------------------- #
@dataclass
class BacktestView:
    equity: pd.Series
    benchmark: pd.Series           # equal-weight buy & hold
    weights: pd.DataFrame
    prices: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict


def run_backtest(
    market: str,
    symbols: List[str],
    strategy: str,
    params: dict,
    start: Optional[str],
    end: Optional[str],
    cash: float,
    commission_bps: float,
    slippage_bps: float,
    timeframe: str = "1d",
    periods_per_year: Optional[float] = None,
) -> BacktestView:
    from wealth.data.base import get_provider

    provider = get_provider(market)
    data = provider.fetch_ohlcv(symbols, start=start, end=end, timeframe=timeframe)
    if not data:
        raise RuntimeError("no data returned for the requested symbols/range")
    strat = get_strategy(strategy, **(params or {}))
    bt = Backtester(cost_model=CostModel(commission_bps, slippage_bps), cash=cash)
    result = bt.run(strat, data)
    return _backtest_view(result, cash, periods_per_year)


def _backtest_view(result, cash: float, periods_per_year) -> BacktestView:
    bench = buy_and_hold(result.prices, cash)
    trades = trades_frame(result.trades)
    metrics = perf.summary(
        result.equity, result.trades, result.weights, periods_per_year=periods_per_year
    )
    return BacktestView(result.equity, bench, result.weights, result.prices, trades, metrics)


def buy_and_hold(prices: pd.DataFrame, cash: float) -> pd.Series:
    """Equal-weight buy-and-hold benchmark, normalized to ``cash``."""
    if prices is None or prices.empty:
        return pd.Series(dtype=float, name="buy_hold")
    norm = prices.div(prices.iloc[0])
    bench = norm.mean(axis=1) * cash
    bench.name = "buy_hold"
    return bench


def trades_frame(trades) -> pd.DataFrame:
    rows = []
    for t in trades or []:
        rows.append(
            {
                "timestamp": getattr(t, "timestamp", None),
                "symbol": getattr(t, "symbol", None),
                "side": "buy" if getattr(t, "side", 1) > 0 else "sell",
                "quantity": getattr(t, "quantity", None),
                "price": getattr(t, "price", None),
                "cost": getattr(t, "cost", None),
                "pnl": getattr(t, "pnl", None),
            }
        )
    return pd.DataFrame(rows)


def run_tuning(cfg: BotConfig, metric: str = "calmar", folds: int = 4,
               start: Optional[str] = None, end: Optional[str] = None):
    from wealth.data.base import get_provider
    from wealth.tuning.walkforward import walk_forward
    from wealth.cli import _default_grid

    provider = get_provider(cfg.market, exchange=cfg.exchange) if cfg.market == "crypto" \
        else get_provider(cfg.market)
    data = provider.fetch_ohlcv(cfg.symbols, start=start, end=end, timeframe=cfg.timeframe)
    if not data:
        raise RuntimeError("no data for tuning")
    grid = cfg.tune_grid or _default_grid(cfg.strategy)
    return walk_forward(
        cfg.strategy, data, grid, metric=metric, n_folds=folds,
        cost_model=CostModel(cfg.commission_bps, cfg.slippage_bps), cash=cfg.cash,
        periods_per_year=cfg.periods_per_year,
    )


def step_tick(cfg: BotConfig) -> dict:
    """Run a single bot tick using the same wiring as `wealth run --once`."""
    from wealth.data.base import get_provider
    from wealth.cli import _build_broker
    from wealth.live.runner import Runner

    provider = get_provider(cfg.market, exchange=cfg.exchange) if cfg.market == "crypto" \
        else get_provider(cfg.market)
    broker = _build_broker(cfg, provider)
    strat = get_strategy(cfg.strategy, **cfg.params)
    journal = Journal(cfg.journal_path)
    runner = Runner(
        strat, broker, provider, symbols=cfg.symbols, timeframe=cfg.timeframe,
        lookback_bars=cfg.lookback_bars, journal=journal,
    )
    return runner.tick()


# --------------------------------------------------------------------------- #
# analytics helpers
# --------------------------------------------------------------------------- #
def monthly_returns(equity: pd.Series) -> pd.DataFrame:
    """Year x month table of monthly returns (fraction)."""
    if equity is None or len(equity) < 2:
        return pd.DataFrame()
    eq = equity.copy()
    eq.index = pd.to_datetime(eq.index)
    monthly = eq.resample("ME").last()
    rets = monthly.pct_change().dropna()
    if rets.empty:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {"year": rets.index.year, "month": rets.index.month, "ret": rets.values}
    )
    pivot = frame.pivot_table(index="year", columns="month", values="ret")
    pivot.columns = [pd.Timestamp(2000, int(m), 1).strftime("%b") for m in pivot.columns]
    return pivot


# --------------------------------------------------------------------------- #
# demo data (offline / empty-journal fallback)
# --------------------------------------------------------------------------- #
def demo_backtest(seed: int = 7, n: int = 720, cash: float = 10_000.0) -> BacktestView:
    """Deterministic synthetic backtest so the UI is fully populated offline."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    drift = rng.normal(0.0009, 0.022, n)
    price = 100 * np.exp(np.cumsum(drift))
    s = pd.Series(price, index=idx)
    data = {"BTC/USDT": pd.DataFrame(
        {"open": s, "high": s, "low": s, "close": s, "volume": 1.0})}
    strat = get_strategy("trend_breakout", entry_n=20, exit_n=10)
    bt = Backtester(cost_model=CostModel(5, 5), cash=cash)
    result = bt.run(strat, data)
    return _backtest_view(result, cash, periods_per_year=365)


def demo_journal_view(seed: int = 7) -> JournalView:
    """Synthetic journal view derived from the demo backtest equity curve."""
    bv = demo_backtest(seed)
    eq = bv.equity
    ticks = pd.DataFrame(
        {
            "timestamp": eq.index,
            "bar_ts": eq.index.astype(str),
            "status": "traded",
            "cash": np.nan,
            "equity": eq.values,
        }
    )
    # A few representative orders from the demo trades.
    orders = bv.trades.head(12).copy()
    last_price = float(bv.prices.iloc[-1, 0])
    return JournalView(
        equity=eq,
        ticks=ticks,
        orders=orders,
        last_prices={"BTC/USDT": last_price},
        target_weights={"BTC/USDT": 1.0},
    )


# --------------------------------------------------------------------------- #
# Polymarket bots (15-minute maker + value bot)
# --------------------------------------------------------------------------- #
@dataclass
class PolyView:
    equity: pd.Series
    windows: pd.DataFrame     # one row per settled window (slug, outcome, pnl, fills)
    fills: pd.DataFrame       # one row per fill (token, side, price, size, fee, kind)
    fill_counts: Dict[str, int]
    fees_paid: float


def load_poly_view(journal_path: str) -> PolyView:
    records = Journal(journal_path).records() if os.path.exists(journal_path) else []
    return _poly_view(records)


def _poly_view(records: List[dict]) -> PolyView:
    win_rows, fill_rows, eq_rows = [], [], []
    counts: Dict[str, int] = {"maker": 0, "taker": 0, "pair": 0}
    fees = 0.0
    for r in records:
        ev = r.get("event")
        if ev == "window_settle":
            f = r.get("fills") or {}
            win_rows.append({
                "timestamp": r.get("timestamp"), "slug": r.get("slug"),
                "outcome": r.get("outcome"), "pnl": float(r.get("pnl", 0.0)),
                "payout": r.get("payout"), "cash": r.get("cash"),
                "maker": int(f.get("maker", 0)), "taker": int(f.get("taker", 0)),
                "pair": int(f.get("pair", 0)),
            })
            for k in counts:
                counts[k] += int(f.get(k, 0))
        elif ev == "fill":
            fill_rows.append({
                "timestamp": r.get("timestamp"), "slug": r.get("slug"),
                "token": r.get("token"), "side": r.get("side"),
                "price": r.get("price"), "size": r.get("size"),
                "fee": float(r.get("fee", 0.0)), "kind": r.get("kind"),
            })
            fees += float(r.get("fee", 0.0))
        elif ev == "tick" and r.get("equity") is not None:
            eq_rows.append((r.get("timestamp"), float(r["equity"])))

    equity = pd.Series(dtype=float, name="equity")
    if eq_rows:
        equity = pd.Series(
            [v for _, v in eq_rows],
            index=pd.to_datetime([t for t, _ in eq_rows]), name="equity")
    return PolyView(equity, pd.DataFrame(win_rows), pd.DataFrame(fill_rows),
                    counts, fees)


@dataclass
class ValueView:
    equity: pd.Series
    open_positions: pd.DataFrame   # slug, outcome_label, entry_price, stake, edge
    settled: pd.DataFrame          # slug, won, stake, payout, pnl
    cash: Optional[float]
    exposure: float
    hit_rate: Optional[float]
    total_pnl: float


def load_value_view(journal_path: str, positions_path: str) -> ValueView:
    records = Journal(journal_path).records() if os.path.exists(journal_path) else []
    positions, cash = [], None
    if os.path.exists(positions_path):
        with open(positions_path) as f:
            raw = json.load(f)
        positions = raw.get("positions", [])
        cash = raw.get("cash")
    return _value_view(records, positions, cash)


def _value_view(records: List[dict], positions: List[dict],
                cash: Optional[float]) -> ValueView:
    settle_rows, eq_rows = [], []
    for r in records:
        ev = r.get("event")
        if ev == "value_settle":
            settle_rows.append({
                "timestamp": r.get("timestamp"), "slug": r.get("slug"),
                "question": r.get("question"),
                "outcome_label": r.get("outcome_label"),
                "won": bool(r.get("won")), "entry_price": r.get("entry_price"),
                "stake": float(r.get("stake", 0.0)),
                "payout": float(r.get("payout", 0.0)),
                "pnl": float(r.get("pnl", 0.0)),
            })
        elif ev == "tick" and r.get("equity") is not None:
            eq_rows.append((r.get("timestamp"), float(r["equity"])))

    equity = pd.Series(dtype=float, name="equity")
    if eq_rows:
        equity = pd.Series(
            [v for _, v in eq_rows],
            index=pd.to_datetime([t for t, _ in eq_rows]), name="equity")

    open_df = pd.DataFrame(positions)
    if not open_df.empty:
        keep = ["slug", "question", "outcome_label", "entry_price", "shares",
                "stake", "edge", "end_date"]
        open_df = open_df[[c for c in keep if c in open_df.columns]]

    settled = pd.DataFrame(settle_rows)
    hit_rate = None
    total_pnl = 0.0
    if not settled.empty:
        hit_rate = float(settled["won"].mean())
        total_pnl = float(settled["pnl"].sum())
    exposure = float(open_df["stake"].sum()) if "stake" in open_df else 0.0
    return ValueView(equity, open_df, settled, cash, exposure, hit_rate, total_pnl)


def demo_poly_view(seed: int = 11, n: int = 48) -> PolyView:
    """Synthetic 15-min bot journal: mostly small wins, occasional losses."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-06-01", periods=n, freq="15min")
    pnl = np.where(rng.random(n) < 0.72,
                   rng.uniform(0.1, 1.4, n), -rng.uniform(0.5, 3.0, n))
    records = []
    equity = 1_000.0
    for ts, p in zip(idx, pnl):
        equity += p
        slug = f"btc-updown-15m-{int(ts.timestamp())}"
        records.append({"event": "window_settle", "timestamp": str(ts),
                        "slug": slug, "outcome": rng.choice(["UP", "DOWN"]),
                        "pnl": float(p), "payout": 0.0, "cash": equity,
                        "fills": {"maker": int(rng.integers(0, 6)),
                                  "taker": int(rng.integers(0, 2)),
                                  "pair": int(rng.integers(0, 3))}})
        records.append({"event": "tick", "timestamp": str(ts),
                        "bar_ts": int(ts.timestamp()), "equity": equity})
    return _poly_view(records)


def demo_value_view(seed: int = 13, n_settled: int = 24, n_open: int = 8) -> ValueView:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-05-01", periods=n_settled, freq="D")
    records, cash = [], 1_000.0
    for i, ts in enumerate(idx):
        entry = float(rng.uniform(0.90, 0.97))
        stake = float(rng.uniform(5, 25))
        won = bool(rng.random() < 0.94)
        payout = stake / entry if won else 0.0
        pnl = payout - stake
        cash += pnl
        records.append({"event": "value_settle", "timestamp": str(ts),
                        "slug": f"demo-market-{i}", "question": f"Demo event {i}?",
                        "outcome_label": "Yes", "won": won, "entry_price": entry,
                        "stake": stake, "payout": payout, "pnl": pnl})
        records.append({"event": "tick", "timestamp": str(ts), "equity": cash})
    positions = [{
        "slug": f"demo-open-{i}", "question": f"Open demo event {i}?",
        "outcome_label": rng.choice(["Yes", "No"]),
        "entry_price": float(rng.uniform(0.90, 0.97)),
        "shares": 20.0, "stake": float(rng.uniform(5, 25)),
        "edge": float(rng.uniform(0.005, 0.02)),
        "end_date": str(pd.Timestamp("2026-07-10") + pd.Timedelta(days=int(i))),
    } for i in range(n_open)]
    return _value_view(records, positions, cash)


def demo_positions(cash: float = 10_000.0) -> pd.DataFrame:
    bv = demo_backtest()
    price = float(bv.prices.iloc[-1, 0])
    qty = (cash * 0.6) / price
    avg = float(bv.prices.iloc[-30, 0])
    return pd.DataFrame(
        [{
            "symbol": "BTC/USDT", "quantity": qty, "avg_price": avg,
            "price": price, "value": qty * price, "unrealized": qty * (price - avg),
        }]
    )
