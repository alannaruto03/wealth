"""Runner: weights->orders, idempotency per bar, scheduler loop."""
import pandas as pd
import pytest

from wealth.broker.paper import PaperBroker
from wealth.data.base import DataProvider
from wealth.engine.costs import CostModel
from wealth.live.journal import Journal
from wealth.live.runner import Runner
from wealth.live.scheduler import Scheduler
from wealth.strategies.base import Strategy


def idx(n):
    return pd.date_range("2021-01-01", periods=n, freq="D")


class RampProvider(DataProvider):
    """Deterministic rising price series, last close = 100 + n - 1."""
    def __init__(self, n=60):
        self.n = n

    def fetch_ohlcv(self, symbols, start=None, end=None, timeframe="1d"):
        out = {}
        for s in symbols:
            close = pd.Series(100.0 + pd.RangeIndex(self.n), index=idx(self.n), dtype=float)
            out[s] = pd.DataFrame(
                {"open": close, "high": close, "low": close, "close": close, "volume": 1.0}
            )
        return out


class AllInStrategy(Strategy):
    name = "_allin"

    def generate_weights(self, data):
        closes = self._close_frame(data)
        return pd.DataFrame(1.0 / closes.shape[1], index=closes.index, columns=closes.columns)


def make_runner(tmp_path, n=60):
    provider = RampProvider(n)
    prices = {"BTC/USDT": 100.0 + n - 1}
    broker = PaperBroker(
        price_fn=lambda s: prices[s],
        starting_cash=10_000.0,
        cost_model=CostModel(0.0, 0.0),
        state_path=str(tmp_path / "broker.json"),
    )
    journal = Journal(str(tmp_path / "journal.jsonl"))
    runner = Runner(
        AllInStrategy(), broker, provider, symbols=["BTC/USDT"],
        timeframe="1d", lookback_bars=400, journal=journal,
    )
    return runner, broker, journal


def test_tick_opens_position(tmp_path):
    runner, broker, _ = make_runner(tmp_path)
    rec = runner.tick()
    assert rec["status"] == "traded"
    assert len(rec["orders"]) == 1
    assert broker.get_positions()["BTC/USDT"].quantity > 0


def test_tick_is_idempotent_per_bar(tmp_path):
    runner, broker, journal = make_runner(tmp_path)
    runner.tick()
    qty_after_first = broker.get_positions()["BTC/USDT"].quantity
    second = runner.tick()  # same bar -> no-op
    assert second["status"] == "already_traded"
    assert broker.get_positions()["BTC/USDT"].quantity == pytest.approx(qty_after_first)


def test_force_retrades(tmp_path):
    runner, _, _ = make_runner(tmp_path)
    runner.tick()
    forced = runner.tick(force=True)
    assert forced["status"] in ("traded",)


def test_journal_equity_curve(tmp_path):
    runner, _, journal = make_runner(tmp_path)
    runner.tick()
    curve = journal.equity_curve()
    assert len(curve) == 1
    assert curve.iloc[0] == pytest.approx(10_000.0)


def test_scheduler_runs_n_ticks(tmp_path):
    runner, _, journal = make_runner(tmp_path)
    sched = Scheduler(runner, interval_seconds=1)
    sched.run_forever(max_ticks=3, sleep_fn=lambda s: None)
    # Only the first tick trades; the rest are idempotent no-ops.
    traded = [r for r in journal.records() if r.get("status") == "traded"]
    assert len(traded) == 1
