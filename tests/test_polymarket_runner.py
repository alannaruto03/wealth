"""Runner tests: fakes for gamma/clob/spot, virtual clock, no network."""
import json

import pytest

from wealth.dashboard.data_access import _journal_view
from wealth.live.journal import Journal
from wealth.polymarket.clob import OrderBook
from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.gamma import MarketInfo
from wealth.polymarket.paper import PaperExecutor
from wealth.polymarket.runner import PolymarketRunner

T0 = 1_783_069_200.0  # a 15m boundary


def make_cfg(**kw):
    defaults = dict(
        series=["15m"], cash=1000.0, interval_seconds=3,
        edge_threshold=0.03, min_seconds_to_expiry=20.0,
        max_spread=0.10, min_book_depth_usd=10.0,
        stale_spot_max_s=10.0, stale_book_max_s=10.0,
        kelly_fraction=0.25, max_stake_per_trade=50.0,
        max_market_exposure=100.0, max_total_exposure=300.0,
        daily_loss_limit=0.5,
    )
    defaults.update(kw)
    return PolymarketConfig(**defaults)


def make_market(slug="m-1", start=T0, end=T0 + 900, up="UP1", down="DN1"):
    return MarketInfo(slug=slug, condition_id="c", question="Bitcoin Up or Down?",
                      token_id_up=up, token_id_down=down,
                      start_ts=start, end_ts=end, series="15m")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeSpot:
    def __init__(self, clock, spot=100_000.0, sigma=0.0005):
        self.clock = clock
        self.spot = spot
        self.sigma = sigma
        self.spot_ts = clock.t

    def refresh(self):
        self.spot_ts = self.clock.t  # always fresh unless test overrides

    def age_s(self, now=None):
        return (now if now is not None else self.clock.t) - self.spot_ts


class FakeClob:
    def __init__(self, clock):
        self.clock = clock
        self.books = {}

    def set(self, token, bid, ask, size=1000):
        self.books[token] = (bid, ask, size)

    def get_book(self, token_id):
        bid, ask, size = self.books[token_id]
        return OrderBook(token_id=token_id, bids=[(bid, size)], asks=[(ask, size)],
                         ts=self.clock.t)


class FakeGamma:
    def __init__(self):
        self.markets = []

    def discover(self, series, now):
        return [m for m in self.markets if m.end_ts > now]


def build(tmp_path, cfg=None, spot=100_000.0, journal=True):
    cfg = cfg or make_cfg()
    clock = Clock(T0 + 60)
    gamma = FakeGamma()
    clob = FakeClob(clock)
    feed = FakeSpot(clock, spot=spot)
    executor = PaperExecutor(starting_cash=cfg.cash, fee_bps=cfg.fee_bps,
                             state_path=str(tmp_path / "exec.json"))
    j = Journal(str(tmp_path / "journal.jsonl")) if journal else None
    runner = PolymarketRunner(cfg, gamma, clob, feed, executor,
                              journal=j, clock=clock)
    return runner, clock, gamma, clob, feed, executor, j


def test_tick_trades_on_edge_and_journals(tmp_path):
    runner, clock, gamma, clob, feed, executor, journal = build(tmp_path)
    m = make_market()
    gamma.markets = [m]
    # spot well above the open it will record at first sight? period_open is
    # captured from current spot (100k). Push spot up after discovery so fair
    # rises while the book still prices UP at 50c.
    clob.set("UP1", 0.49, 0.51)
    clob.set("DN1", 0.47, 0.49)
    rec1 = runner.tick()  # discovery tick: period_open = 100_000
    feed.spot = 100_150.0  # BTC moved up; fair_up now >> ask
    clock.t += 3
    rec2 = runner.tick()

    assert rec2["status"] == "traded"
    assert len(rec2["orders"]) == 1
    order = rec2["orders"][0]
    assert order["symbol"] == "m-1:UP"
    assert order["side"] == "buy"
    assert order["price"] == pytest.approx(0.51)
    assert rec2["equity"] < 1000.0 + 1e-6  # cash spent, marked at best bid
    assert executor.get_positions()["UP1"].size > 0

    # journal shape round-trips through existing readers
    curve = journal.equity_curve()
    assert len(curve) == 2
    view = _journal_view(journal.records())
    assert len(view.ticks) == 2
    assert len(view.orders) == 1
    assert view.orders.iloc[0]["symbol"] == "m-1:UP"
    assert view.last_prices["m-1:UP"] == pytest.approx(0.50)


def test_no_trade_within_threshold(tmp_path):
    runner, clock, gamma, clob, *_ = build(tmp_path)
    gamma.markets = [make_market()]
    clob.set("UP1", 0.49, 0.51)
    clob.set("DN1", 0.47, 0.49)
    rec = runner.tick()  # spot == period_open -> fair 0.5, no edge vs 0.51 ask
    assert rec["status"] == "hold"
    assert rec["orders"] == []


def test_stale_spot_skips_trading(tmp_path):
    runner, clock, gamma, clob, feed, executor, journal = build(tmp_path)
    gamma.markets = [make_market()]
    clob.set("UP1", 0.49, 0.51)
    clob.set("DN1", 0.47, 0.49)
    feed.refresh = lambda: None  # spot never refreshes
    feed.spot_ts = clock.t - 60
    rec = runner.tick()
    assert rec["status"] == "stale_spot"
    assert "orders" not in rec or not rec.get("orders")


def test_rollover_settles_and_promotes_next(tmp_path):
    runner, clock, gamma, clob, feed, executor, journal = build(tmp_path)
    m1 = make_market(slug="m-1", start=T0, end=T0 + 900)
    m2 = make_market(slug="m-2", start=T0 + 900, end=T0 + 1800, up="UP2", down="DN2")
    gamma.markets = [m1, m2]
    for tok_bid in [("UP1", 0.49, 0.51), ("DN1", 0.47, 0.49),
                    ("UP2", 0.49, 0.51), ("DN2", 0.47, 0.49)]:
        clob.set(*tok_bid)
    runner.tick()  # period_open m-1 = 100_000
    feed.spot = 100_150.0
    clock.t += 3
    runner.tick()  # buys UP1
    assert executor.get_positions()["UP1"].size > 0

    # jump past m-1 expiry with spot still above open -> UP wins
    clock.t = T0 + 901
    rec = runner.tick()
    assert "UP1" not in executor.get_positions()
    resolutions = [r for r in journal.records() if r.get("event") == "resolution"]
    assert len(resolutions) == 1
    assert resolutions[0]["slug"] == "m-1"
    assert resolutions[0]["outcome"] == "up"
    assert resolutions[0]["pnl"] > 0
    # m-2 still tracked and tradable
    assert "m-2" in runner.active
    assert "m-1" not in runner.active


def test_restart_settles_expired_position_from_state(tmp_path):
    cfg = make_cfg()
    runner, clock, gamma, clob, feed, executor, journal = build(tmp_path, cfg=cfg)
    gamma.markets = [make_market()]
    clob.set("UP1", 0.49, 0.51)
    clob.set("DN1", 0.47, 0.49)
    runner.tick()
    feed.spot = 100_150.0
    clock.t += 3
    runner.tick()
    assert executor.get_positions()["UP1"].size > 0
    cash_before = executor.get_cash()
    shares = executor.get_positions()["UP1"].size

    # fresh runner from persisted state, market long expired, no gamma results
    clock2 = Clock(T0 + 2000)
    gamma2 = FakeGamma()
    clob2 = FakeClob(clock2)
    feed2 = FakeSpot(clock2, spot=100_150.0)
    executor2 = PaperExecutor(starting_cash=cfg.cash, state_path=str(tmp_path / "exec.json"))
    runner2 = PolymarketRunner(cfg, gamma2, clob2, feed2, executor2,
                               journal=journal, clock=clock2)
    assert executor2.get_positions()["UP1"].size == pytest.approx(shares)
    runner2.tick()
    assert executor2.get_positions() == {}
    assert executor2.get_cash() == pytest.approx(cash_before + shares)  # $1/share payout


def test_run_forever_max_ticks_and_error_resilience(tmp_path):
    runner, clock, gamma, clob, feed, executor, journal = build(tmp_path)
    gamma.markets = [make_market()]
    clob.set("UP1", 0.49, 0.51)
    clob.set("DN1", 0.47, 0.49)
    calls = []
    boom = {"n": 0}

    original_tick = runner.tick

    def flaky_tick():
        if boom["n"] == 1:
            boom["n"] += 1
            raise ConnectionError("network blip")
        boom["n"] += 1
        return original_tick()

    runner.tick = flaky_tick
    runner.run_forever(max_ticks=3, sleep_fn=lambda s: calls.append(s))
    errors = [r for r in journal.records() if r.get("event") == "error"]
    assert len(errors) == 1
    assert "network blip" in errors[0]["error"]
    ticks = [r for r in journal.records() if r.get("event") == "tick"]
    assert len(ticks) == 2  # 3 loop iterations, 1 errored


def test_kill_switch_blocks_entries_via_runner(tmp_path):
    cfg = make_cfg(daily_loss_limit=0.0001)  # hair trigger: any mark-to-bid dip trips
    runner, clock, gamma, clob, feed, executor, journal = build(tmp_path, cfg=cfg)
    gamma.markets = [make_market()]
    clob.set("UP1", 0.49, 0.51)
    clob.set("DN1", 0.47, 0.49)
    runner.tick()
    feed.spot = 100_150.0
    clock.t += 3
    runner.tick()  # trades, pays spread -> equity dips below anchor
    clock.t += 3
    feed.spot = 100_300.0  # even more edge
    rec = runner.tick()
    assert rec["orders"] == []
    blocks = [r for r in journal.records() if r.get("event") == "risk_block"]
    assert any(b["reason"] == "kill_switch" for b in blocks)


def test_observe_only_runner_never_trades(tmp_path):
    cfg = make_cfg()
    clock = Clock(T0 + 60)
    gamma, clob, feed = FakeGamma(), FakeClob(clock), FakeSpot(clock)
    executor = PaperExecutor(starting_cash=cfg.cash)
    runner = PolymarketRunner(cfg, gamma, clob, feed, executor,
                              clock=clock, trade=False)
    gamma.markets = [make_market()]
    clob.set("UP1", 0.49, 0.51)
    clob.set("DN1", 0.47, 0.49)
    runner.tick()
    feed.spot = 100_500.0  # massive edge, but observe-only
    clock.t += 3
    rec = runner.tick()
    assert rec["status"] == "recording"
    assert rec["orders"] == []
    assert executor.get_positions() == {}
    assert executor.get_cash() == pytest.approx(cfg.cash)


def test_market_open_event_records_period_open(tmp_path):
    runner, clock, gamma, clob, feed, executor, journal = build(tmp_path)
    m = make_market()
    m.price_to_beat = 99_500.0  # gamma-provided open takes precedence over spot
    gamma.markets = [m]
    clob.set("UP1", 0.49, 0.51)
    clob.set("DN1", 0.47, 0.49)
    runner.tick()
    opens = [r for r in journal.records() if r.get("event") == "market_open"]
    assert len(opens) == 1
    assert opens[0]["period_open"] == 99_500.0
    assert opens[0]["period_open_source"] == "gamma"
    assert executor.meta["markets"]["m-1"]["period_open"] == 99_500.0
