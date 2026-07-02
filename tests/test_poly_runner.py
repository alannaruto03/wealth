"""End-to-end window lifecycle on the PolyRunner — offline, compressed time.

Exercises the real orchestration path (_trade_window: state -> strategy ->
risk -> reconcile -> fills -> resolution -> settle -> journal) with a
synthetic market, injected feed data, and a stubbed Gamma client.
"""
import asyncio
import time

from wealth.live.journal import Journal
from wealth.polymarket.config import PolyBotConfig
from wealth.polymarket.discovery import UpDownMarket
from wealth.polymarket.feeds import BookTop
from wealth.polymarket.runner import PolyRunner


class StubGamma:
    """Returns the market as resolved once asked after window end."""

    def __init__(self, outcome="UP"):
        self.outcome = outcome

    def refresh(self, market):
        market.closed = True
        market.outcome = self.outcome
        return market

    def close(self):
        pass


def _runner(tmp_path, **cfg_kw):
    cfg = PolyBotConfig(
        cash=500.0, poll_interval_s=0.05, resolution_timeout_s=5.0,
        state_dir=str(tmp_path), no_quote_final_s=1.0,
        taker_final_cutoff_s=0.5, feed_stale_s=30.0, **cfg_kw)
    journal = Journal(cfg.journal_path)
    r = PolyRunner(cfg, journal, verbose=False)
    r.gamma = StubGamma()
    return r, journal


def _market(now, seconds=2.0):
    return UpDownMarket(
        slug="btc-updown-15m-test", condition_id="0x1",
        token_up="tokUP", token_down="tokDOWN",
        start_ts=int(now), end_ts=now + seconds, tick=0.01)


def test_window_lifecycle_complete_set(tmp_path):
    r, journal = _runner(tmp_path)
    now = time.time()
    # live-ish data: fresh spot and a book offering a complete set below $1
    r.data.record_spot(now, 100_000.0)
    r.data.books["tokUP"] = BookTop(bid=0.44, ask=0.46, bid_size=50,
                                    ask_size=50, ts=now)
    r.data.books["tokDOWN"] = BookTop(bid=0.48, ask=0.50, bid_size=50,
                                      ask_size=50, ts=now)

    asyncio.run(r._trade_window(_market(now)))

    recs = journal.records()
    events = [x["event"] for x in recs]
    assert "window_start" in events and "window_settle" in events
    settle = next(x for x in recs if x["event"] == "window_settle")
    assert settle["outcome"] == "UP"
    # complete set at 0.96: profit is locked in regardless of outcome
    assert settle["pnl"] > 0
    fills = [x for x in recs if x["event"] == "fill"]
    assert {f["token"] for f in fills} == {"UP", "DOWN"}
    assert r.executor.open_orders() == []
    # equity tick emitted in the shape the shared Journal understands
    assert not journal.equity_curve().empty


def test_window_lifecycle_maker_quotes_no_fill(tmp_path):
    r, journal = _runner(tmp_path)
    now = time.time()
    r.data.record_spot(now, 100_000.0)
    # wide, fair book: bot should quote but nothing should cross
    r.data.books["tokUP"] = BookTop(bid=0.45, ask=0.55, bid_size=50,
                                    ask_size=50, ts=now)
    r.data.books["tokDOWN"] = BookTop(bid=0.45, ask=0.55, bid_size=50,
                                      ask_size=50, ts=now)

    asyncio.run(r._trade_window(_market(now)))

    settle = next(x for x in journal.records()
                  if x["event"] == "window_settle")
    assert settle["pnl"] == 0.0          # no fills, no loss
    assert settle["fills"] == {"maker": 0, "taker": 0, "pair": 0}
    assert r.executor.cash() == 500.0


def test_daily_loss_kill_switch(tmp_path):
    r, journal = _runner(tmp_path, daily_loss_limit=10.0)
    r.daily_pnl = -11.0
    now = time.time()
    r.data.record_spot(now, 100_000.0)
    r.data.books["tokUP"] = BookTop(bid=0.44, ask=0.46, bid_size=50,
                                    ask_size=50, ts=now)
    r.data.books["tokDOWN"] = BookTop(bid=0.48, ask=0.50, bid_size=50,
                                      ask_size=50, ts=now)

    asyncio.run(r._trade_window(_market(now)))

    settle = next(x for x in journal.records()
                  if x["event"] == "window_settle")
    # risk manager halts everything: no orders reach the book
    assert settle["fills"] == {"maker": 0, "taker": 0, "pair": 0}
    assert settle["pnl"] == 0.0
