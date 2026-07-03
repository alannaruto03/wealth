"""Recorder writes snapshots the replay engine can trade through end to end."""
import json

import pytest

from wealth.live.journal import Journal
from wealth.polymarket.clob import OrderBook
from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.gamma import MarketInfo
from wealth.polymarket.recorder import BookRecorder
from wealth.polymarket.replay import load_recording, replay_run

T0 = 1_783_069_200.0


def make_cfg(**kw):
    defaults = dict(
        series=["15m"], cash=1000.0, edge_threshold=0.03,
        min_seconds_to_expiry=20.0, max_spread=0.10, min_book_depth_usd=10.0,
        stale_spot_max_s=10.0, stale_book_max_s=10.0,
        kelly_fraction=0.25, max_stake_per_trade=50.0,
        max_market_exposure=100.0, max_total_exposure=300.0,
        daily_loss_limit=0.5,
    )
    defaults.update(kw)
    return PolymarketConfig(**defaults)


def market():
    return MarketInfo(slug="m-1", condition_id="c", question="Bitcoin Up or Down?",
                      token_id_up="UP1", token_id_down="DN1",
                      start_ts=T0, end_ts=T0 + 900, series="15m",
                      price_to_beat=100_000.0)


def book(token, bid, ask, size=1000, ts=0.0):
    return OrderBook(token_id=token, bids=[(bid, size)], asks=[(ask, size)], ts=ts)


def write_recording(tmp_path):
    """Three snapshots: flat, mispriced (spot up but book lags), converged."""
    rec = BookRecorder(str(tmp_path))
    m = market()
    info = {"period_open": 100_000.0}
    frames = [
        (T0 + 10, 100_000.0, 0.49, 0.51),
        (T0 + 20, 100_200.0, 0.49, 0.51),   # spot jumped, book stale -> edge
        (T0 + 30, 100_200.0, 0.54, 0.56),   # book converged to fair (~0.55)
    ]
    for ts, spot, bid, ask in frames:
        rec.snapshot(now=ts, market=m, info=info, spot=spot, sigma=0.0005,
                     book_up=book("UP1", bid, ask, ts=ts),
                     book_down=book("DN1", 1 - ask - 0.02, 1 - bid - 0.02, ts=ts))
    return rec.path


def test_recorder_writes_loadable_jsonl(tmp_path):
    path = write_recording(tmp_path)
    records = load_recording(path)
    assert len(records) == 3
    r = records[0]
    assert r["slug"] == "m-1"
    assert r["period_open"] == 100_000.0
    assert r["book_up"]["asks"] == [[0.51, 1000]]
    # every line is valid standalone json
    with open(path) as f:
        for line in f:
            json.loads(line)


def test_replay_trades_mispricing_and_settles(tmp_path):
    path = write_recording(tmp_path)
    journal = Journal(str(tmp_path / "replay_journal.jsonl"))
    runner = replay_run(make_cfg(), path, journal=journal)
    ex = runner.executor

    # bought UP on the stale-book frame, settled Up at expiry -> profit
    assert ex.get_positions() == {}
    assert ex.realized_pnl > 0
    ticks = [r for r in journal.records() if r.get("event") == "tick"]
    traded = [t for t in ticks if t["status"] == "traded"]
    assert len(traded) == 1
    assert traded[0]["orders"][0]["symbol"] == "m-1:UP"
    resolutions = [r for r in journal.records() if r.get("event") == "resolution"]
    assert len(resolutions) == 1
    assert resolutions[0]["outcome"] == "up"
    # final cash reflects $1 payout per share
    assert ex.get_cash() == pytest.approx(1000.0 + ex.realized_pnl)


def test_replay_uses_recorded_period_open(tmp_path):
    path = write_recording(tmp_path)
    journal = Journal(str(tmp_path / "j.jsonl"))
    runner = replay_run(make_cfg(), path, journal=journal)
    opens = [r for r in journal.records() if r.get("event") == "market_open"]
    assert len(opens) == 1
    assert opens[0]["period_open"] == 100_000.0
    assert opens[0]["period_open_source"] == "gamma"
    # everything settled by the final pass
    assert runner.executor.meta.get("markets", {}) == {}


def test_replay_empty_recording_raises(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(ValueError):
        replay_run(make_cfg(), str(empty))
