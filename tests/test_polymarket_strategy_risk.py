import pytest

from wealth.polymarket.clob import OrderBook
from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.executor import TokenPosition
from wealth.polymarket.gamma import MarketInfo
from wealth.polymarket.risk import RiskManager
from wealth.polymarket.strategy import EdgeSignal, TradeIntent

NOW = 1000.0


def make_cfg(**kw):
    defaults = dict(
        edge_threshold=0.03, min_seconds_to_expiry=20.0, max_spread=0.05,
        min_book_depth_usd=10.0, stale_book_max_s=10.0, fee_bps=0.0,
        kelly_fraction=0.25, max_stake_per_trade=50.0,
        max_market_exposure=100.0, max_total_exposure=300.0,
        daily_loss_limit=0.05,
    )
    defaults.update(kw)
    return PolymarketConfig(**defaults)


def make_market(slug="m1", expiry=NOW + 600):
    return MarketInfo(slug=slug, condition_id="c", question="q",
                      token_id_up="UP", token_id_down="DN",
                      start_ts=NOW - 300, end_ts=expiry, series="15m")


def make_book(token, bid=0.50, ask=0.52, size=500, ts=NOW):
    return OrderBook(token_id=token, bids=[(bid, size)], asks=[(ask, size)], ts=ts)


def books(bid_up=0.50, ask_up=0.52, bid_dn=0.46, ask_dn=0.48, **kw):
    return make_book("UP", bid_up, ask_up, **kw), make_book("DN", bid_dn, ask_dn, **kw)


def test_entry_up_when_fair_exceeds_ask():
    sig = EdgeSignal(make_cfg())
    up, dn = books()
    intents, _ = sig.intents(make_market(), fair_up=0.60, book_up=up, book_down=dn,
                             positions={}, now=NOW)
    assert len(intents) == 1
    it = intents[0]
    assert (it.token_id, it.side) == ("UP", "buy")
    assert it.limit_price == 0.52
    assert it.edge == pytest.approx(0.08)


def test_entry_down_when_fair_low():
    sig = EdgeSignal(make_cfg())
    up, dn = books()
    intents, _ = sig.intents(make_market(), fair_up=0.40, book_up=up, book_down=dn,
                             positions={}, now=NOW)
    assert [(i.token_id, i.side) for i in intents] == [("DN", "buy")]
    assert intents[0].edge == pytest.approx(0.60 - 0.48)


def test_no_entry_within_threshold():
    sig = EdgeSignal(make_cfg())
    up, dn = books()
    intents, _ = sig.intents(make_market(), fair_up=0.54, book_up=up, book_down=dn,
                             positions={}, now=NOW)
    assert intents == []


def test_fee_reduces_edge():
    sig = EdgeSignal(make_cfg(fee_bps=600.0))  # 0.08 raw edge - 0.06 fee < 0.03 threshold
    up, dn = books()
    intents, _ = sig.intents(make_market(), fair_up=0.60, book_up=up, book_down=dn,
                             positions={}, now=NOW)
    assert intents == []


def test_never_buys_both_sides():
    # Pathological books where both sides look cheap: only the better edge trades.
    sig = EdgeSignal(make_cfg())
    up, dn = books(ask_up=0.40, ask_dn=0.40)
    intents, _ = sig.intents(make_market(), fair_up=0.55, book_up=up, book_down=dn,
                             positions={}, now=NOW)
    assert len(intents) == 1
    assert intents[0].token_id == "UP"  # edge 0.15 vs 0.05


def test_guard_near_expiry():
    sig = EdgeSignal(make_cfg())
    up, dn = books()
    intents, guards = sig.intents(make_market(expiry=NOW + 5), fair_up=0.99,
                                  book_up=up, book_down=dn, positions={}, now=NOW)
    assert intents == []
    assert any(g.reason == "near_expiry" for g in guards)


def test_guard_wide_spread():
    sig = EdgeSignal(make_cfg())
    up, dn = books(bid_up=0.30, ask_up=0.52)
    intents, guards = sig.intents(make_market(), fair_up=0.99, book_up=up, book_down=dn,
                                  positions={}, now=NOW)
    assert all(i.token_id != "UP" for i in intents)
    assert any(g.reason.startswith("wide_spread") for g in guards)


def test_guard_stale_book():
    sig = EdgeSignal(make_cfg())
    up, dn = books(ts=NOW - 60)
    intents, guards = sig.intents(make_market(), fair_up=0.99, book_up=up, book_down=dn,
                                  positions={}, now=NOW)
    assert intents == []
    assert any(g.reason.startswith("stale_book") for g in guards)


def test_guard_thin_book():
    sig = EdgeSignal(make_cfg(min_book_depth_usd=1000.0))
    up, dn = books(size=10)  # 0.52*10 = $5.2 depth
    intents, guards = sig.intents(make_market(), fair_up=0.99, book_up=up, book_down=dn,
                                  positions={}, now=NOW)
    assert intents == []
    assert any(g.reason == "thin_book" for g in guards)


def test_guard_extreme_price():
    sig = EdgeSignal(make_cfg())
    up, dn = books(bid_up=0.991, ask_up=0.995)
    intents, guards = sig.intents(make_market(), fair_up=0.999, book_up=up, book_down=dn,
                                  positions={}, now=NOW)
    assert all(i.token_id != "UP" for i in intents)
    assert any(g.reason == "extreme_price" for g in guards)


def test_guard_empty_book():
    sig = EdgeSignal(make_cfg())
    empty = OrderBook(token_id="UP", bids=[], asks=[], ts=NOW)
    _, guards = sig.intents(make_market(), fair_up=0.99, book_up=empty,
                            book_down=books()[1], positions={}, now=NOW)
    assert any(g.reason.startswith("empty_book") for g in guards)


def test_take_profit_exit():
    sig = EdgeSignal(make_cfg(take_profit_edge=0.02))
    up, dn = books(bid_up=0.70)
    pos = {"UP": TokenPosition("UP", "m1", 100, 0.52)}
    intents, _ = sig.intents(make_market(), fair_up=0.60, book_up=up, book_down=dn,
                             positions=pos, now=NOW)
    sells = [i for i in intents if i.side == "sell"]
    assert len(sells) == 1
    assert sells[0].size == 100
    assert sells[0].limit_price == 0.70


def test_no_take_profit_when_disabled():
    sig = EdgeSignal(make_cfg(take_profit_edge=None))
    up, dn = books(bid_up=0.99, ask_up=0.995)
    pos = {"UP": TokenPosition("UP", "m1", 100, 0.52)}
    intents, _ = sig.intents(make_market(), fair_up=0.60, book_up=up, book_down=dn,
                             positions=pos, now=NOW)
    assert all(i.side != "sell" for i in intents)


# ---------------------------------------------------------------- risk manager


def buy_intent(fair=0.60, price=0.52, slug="m1"):
    return TradeIntent(market=make_market(slug=slug), token_id="UP", side="buy",
                       limit_price=price, fair=fair, edge=fair - price)


def test_risk_sizes_entry():
    rm = RiskManager(make_cfg(), meta={})
    d = rm.check(buy_intent(), positions={}, cash=1000.0, equity=1000.0, now=NOW)
    assert d.approved
    # kelly = (0.6-0.52)/0.48 = 1/6; quarter kelly * 1000 = 41.67
    assert d.stake_usd == pytest.approx(1000 * 0.25 * (0.08 / 0.48))
    assert d.shares == pytest.approx(d.stake_usd / 0.52)


def test_risk_exposure_caps_clip():
    rm = RiskManager(make_cfg(max_market_exposure=10.0), meta={})
    pos = {"X": TokenPosition("X", "m1", 10, 0.9)}  # $9 in m1 already
    d = rm.check(buy_intent(fair=0.9, price=0.5), positions=pos,
                 cash=1000.0, equity=1000.0, now=NOW)
    # headroom $1 -> below the 5-share/$1 minimum after clipping
    assert not d.approved
    assert d.reason in ("exposure_cap", "size_below_min")


def test_risk_total_exposure_blocks():
    rm = RiskManager(make_cfg(max_total_exposure=50.0), meta={})
    pos = {"X": TokenPosition("X", "other", 100, 0.5)}  # $50 total
    d = rm.check(buy_intent(), positions=pos, cash=1000.0, equity=1000.0, now=NOW)
    assert not d.approved
    assert d.reason == "exposure_cap"


def test_risk_kill_switch_trips_and_allows_exits():
    rm = RiskManager(make_cfg(daily_loss_limit=0.05), meta={})
    rm.roll_day_anchor(NOW, equity=1000.0)
    # 6% down -> tripped
    d = rm.check(buy_intent(), positions={}, cash=940.0, equity=940.0, now=NOW)
    assert not d.approved and d.reason == "kill_switch"
    # stays tripped even if equity recovers within the same day
    d2 = rm.check(buy_intent(), positions={}, cash=990.0, equity=990.0, now=NOW + 60)
    assert not d2.approved and d2.reason == "kill_switch"
    # exits still allowed
    exit_intent = TradeIntent(market=make_market(), token_id="UP", side="sell",
                              limit_price=0.5, fair=0.5, edge=0.0, size=10)
    assert rm.check(exit_intent, positions={}, cash=940.0, equity=940.0, now=NOW).approved


def test_risk_day_anchor_resets_next_day():
    rm = RiskManager(make_cfg(), meta={})
    rm.roll_day_anchor(NOW, equity=1000.0)
    assert rm.kill_switch_tripped(NOW, 900.0)
    next_day = NOW + 86_400
    rm.roll_day_anchor(next_day, equity=900.0)
    assert not rm.kill_switch_tripped(next_day, 900.0)
    d = rm.check(buy_intent(), positions={}, cash=900.0, equity=900.0, now=next_day)
    assert d.approved
