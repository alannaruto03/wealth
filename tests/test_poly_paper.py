"""PaperExecutor: pessimistic fills, fees, complete-set lock, settlement."""
from wealth.polymarket.feeds import BookTop
from wealth.polymarket.paper import PaperExecutor


def _ex(cash=1000.0):
    return PaperExecutor(starting_cash=cash, taker_fee_rate=0.072)


def test_resting_buy_fills_only_on_cross_at_our_price():
    ex = _ex()
    ex.place("UP", "buy", 0.48, 10, "maker", ts=1)
    # ask above our bid: no fill
    ex.on_book("UP", BookTop(bid=0.45, ask=0.50, bid_size=50, ask_size=50), ts=2)
    assert ex.position("UP") == 0
    # ask crosses to our price: fill at OUR bid, capped by displayed size
    ex.on_book("UP", BookTop(bid=0.44, ask=0.48, bid_size=50, ask_size=4), ts=3)
    assert ex.position("UP") == 4
    fills = ex.drain_fills()
    assert len(fills) == 1 and fills[0].price == 0.48 and fills[0].fee == 0.0
    assert abs(ex.cash() - (1000 - 0.48 * 4)) < 1e-9
    assert len(ex.open_orders()) == 1  # remainder still resting


def test_resting_sell_needs_inventory_and_fills_on_bid_cross():
    ex = _ex()
    assert ex.place("UP", "sell", 0.60, 10, "maker", ts=1) is None  # no shorting
    ex.place("UP", "buy", 0.50, 10, "taker", ts=2)  # will be filled via _fill? no book
    # taker path fills immediately at given price
    assert ex.position("UP") == 10
    assert ex.place("UP", "sell", 0.60, 10, "maker", ts=3) is not None
    ex.on_book("UP", BookTop(bid=0.61, ask=0.65, bid_size=10, ask_size=10), ts=4)
    assert ex.position("UP") == 0
    sells = [f for f in ex.drain_fills() if f.side == "sell"]
    assert sells and sells[0].price == 0.60 and sells[0].fee == 0.0


def test_taker_fill_charges_fee_curve():
    ex = _ex()
    ex.place("UP", "buy", 0.50, 100, "taker", ts=1)
    # fee = 100 * 0.072 * 0.5 * 0.5 = 1.8
    assert abs(ex.cash() - (1000 - 50 - 1.8)) < 1e-9
    assert abs(ex.fees_paid - 1.8) < 1e-9


def test_complete_set_locks_profit():
    ex = _ex()
    ex.place("UP", "buy", 0.46, 50, "pair", ts=1)
    ex.place("DOWN", "buy", 0.50, 50, "pair", ts=1)
    cash_after_buys = ex.cash()
    payout = ex.settle_window("DOWN", ts=2)
    assert payout == 50.0
    # spent 48 + fees, got 50 back: profit regardless of outcome (ex fees)
    assert ex.cash() == cash_after_buys + 50.0
    assert ex.position("UP") == 0 and ex.position("DOWN") == 0


def test_settlement_pays_winner_only_and_clears_orders():
    ex = _ex()
    ex.place("UP", "buy", 0.30, 10, "taker", ts=1)
    ex.place("DOWN", "buy", 0.30, 20, "taker", ts=1)
    ex.place("UP", "buy", 0.10, 10, "maker", ts=1)
    payout = ex.settle_window("UP", ts=2)
    assert payout == 10.0
    assert ex.open_orders() == []


def test_cannot_spend_more_than_cash():
    ex = _ex(cash=5.0)
    ex.place("UP", "buy", 0.50, 1000, "taker", ts=1)
    assert ex.cash() >= -1e-9
    assert ex.position("UP") <= 10.0 + 1e-9


def test_cancel_removes_resting_order():
    ex = _ex()
    o = ex.place("UP", "buy", 0.40, 10, "maker", ts=1)
    ex.cancel(o.order_id, ts=2)
    ex.on_book("UP", BookTop(bid=0.30, ask=0.35, bid_size=99, ask_size=99), ts=3)
    assert ex.position("UP") == 0 and ex.open_orders() == []
