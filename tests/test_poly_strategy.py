"""HybridMaker decisions on synthetic states (pure, no network)."""
from wealth.polymarket.config import PolyBotConfig
from wealth.polymarket.feeds import BookTop
from wealth.polymarket.strategy import HybridMaker, MarketState, round_to_tick


def _cfg(**kw):
    return PolyBotConfig(**kw)


def _state(**kw):
    base = dict(
        now=0.0, window_end=600.0, price_to_beat=100_000.0, spot=100_000.0,
        sigma_s=1e-4,
        up=BookTop(bid=0.48, ask=0.52, bid_size=100, ask_size=100, ts=0.0),
        down=BookTop(bid=0.48, ask=0.52, bid_size=100, ask_size=100, ts=0.0),
        tick=0.01,
    )
    base.update(kw)
    return MarketState(**base)


def test_quotes_two_sided_bids_below_fair():
    strat = HybridMaker(_cfg())
    d = strat.decide(_state())
    assert not d.pull_all
    makers = [o for o in d.orders if o.kind == "maker"]
    tokens = {o.token for o in makers}
    assert tokens == {"UP", "DOWN"} and all(o.side == "buy" for o in makers)
    up_bid = next(o for o in makers if o.token == "UP")
    assert up_bid.price <= 0.5 - _cfg().min_half_spread + 1e-9
    # both bids sum below $1: filling both = complete set below par
    down_bid = next(o for o in makers if o.token == "DOWN")
    assert up_bid.price + down_bid.price < 1.0


def test_impulse_pulls_all_quotes():
    strat = HybridMaker(_cfg(impulse_bps=8))
    d = strat.decide(_state(impulse_bps=9.0))
    assert d.pull_all and not d.orders


def test_stale_feed_pulls_all_quotes():
    strat = HybridMaker(_cfg())
    d = strat.decide(_state(feed_stale=True))
    assert d.pull_all


def test_endgame_no_maker_quotes():
    strat = HybridMaker(_cfg(no_quote_final_s=45))
    d = strat.decide(_state(now=570.0))  # 30s left
    assert d.pull_all
    assert not [o for o in d.orders if o.kind == "maker"]


def test_complete_set_capture():
    strat = HybridMaker(_cfg(complete_set_margin=0.015))
    s = _state(up=BookTop(bid=0.40, ask=0.45, bid_size=10, ask_size=30, ts=0),
               down=BookTop(bid=0.48, ask=0.50, bid_size=10, ask_size=20, ts=0))
    d = strat.decide(s)
    pairs = [o for o in d.orders if o.kind == "pair"]
    assert {o.token for o in pairs} == {"UP", "DOWN"}
    assert all(o.size == 20 for o in pairs)  # min of displayed sizes


def test_taker_requires_edge_beyond_fee():
    # spot way above strike, little time left -> fair ~ 1, ask cheap
    cfg = _cfg(taker_edge_margin=0.04, no_quote_final_s=45)
    strat = HybridMaker(cfg)
    s = _state(now=540.0, spot=100_500.0,
               up=BookTop(bid=0.80, ask=0.85, bid_size=50, ask_size=50, ts=0),
               down=BookTop(bid=0.10, ask=0.15, bid_size=50, ask_size=50, ts=0))
    d = strat.decide(s)
    takes = [o for o in d.orders if o.kind == "taker"]
    assert takes and takes[0].token == "UP" and takes[0].price == 0.85
    # same book but fair value near the ask: no take
    s2 = _state(now=60.0, spot=100_010.0,
                up=BookTop(bid=0.80, ask=0.85, bid_size=50, ask_size=50, ts=0))
    d2 = strat.decide(s2)
    assert not [o for o in d2.orders if o.kind == "taker" and o.token == "UP"]


def test_inventory_skew_leans_quotes():
    cfg = _cfg(inventory_skew=0.04, max_inventory=100)
    strat = HybridMaker(cfg)
    flat = strat.decide(_state())
    long_up = strat.decide(_state(inv_up=100.0))
    bid = lambda d, t: next(o.price for o in d.orders
                            if o.kind == "maker" and o.token == t and o.side == "buy")
    # long UP -> lean UP bid down (less eager to add), DOWN bid up
    assert bid(long_up, "UP") <= bid(flat, "UP")
    assert bid(long_up, "DOWN") >= bid(flat, "DOWN")
    # and an unload ask for the UP inventory appears
    assert any(o.side == "sell" and o.token == "UP" for o in long_up.orders)


def test_maker_bid_never_crosses_the_ask():
    strat = HybridMaker(_cfg(min_half_spread=0.02))
    s = _state(up=BookTop(bid=0.30, ask=0.31, bid_size=10, ask_size=10, ts=0),
               spot=100_200.0)  # fair well above the ask
    d = strat.decide(s)
    for o in d.orders:
        if o.kind == "maker" and o.token == "UP" and o.side == "buy":
            assert o.price <= 0.31 - 0.01 + 1e-9


def test_round_to_tick():
    assert round_to_tick(0.4849, 0.01, up=False) == 0.48
    assert round_to_tick(0.4849, 0.01, up=True) == 0.49
    assert round_to_tick(0.48, 0.01, up=True) == 0.48
