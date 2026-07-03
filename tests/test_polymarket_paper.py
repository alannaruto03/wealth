import pytest

from wealth.polymarket.clob import OrderBook
from wealth.polymarket.gamma import MarketInfo
from wealth.polymarket.paper import PaperExecutor


def make_market(slug="btc-updown-15m-100", up="UP", down="DN"):
    return MarketInfo(
        slug=slug, condition_id="0xc", question="Bitcoin Up or Down?",
        token_id_up=up, token_id_down=down,
        start_ts=0.0, end_ts=900.0, series="15m",
    )


def make_book(token_id="UP", bids=((0.50, 100), (0.48, 200)),
              asks=((0.52, 100), (0.55, 200))):
    return OrderBook(token_id=token_id, bids=[list(b) for b in bids],
                     asks=[list(a) for a in asks], ts=1.0)


def test_buy_walks_book_beyond_first_level():
    ex = PaperExecutor(starting_cash=1000.0)
    fill = ex.buy(make_market(), "UP", limit_price=0.60, size=150, book=make_book())
    assert fill.status == "filled"
    assert fill.size == 150
    # 100 @ 0.52 + 50 @ 0.55 -> avg above best ask
    assert fill.avg_price > 0.52
    assert fill.notional == pytest.approx(0.52 * 100 + 0.55 * 50)
    assert ex.get_cash() == pytest.approx(1000.0 - fill.notional)
    pos = ex.get_positions()["UP"]
    assert pos.size == 150
    assert pos.avg_price == pytest.approx(fill.avg_price)


def test_buy_partial_at_limit():
    ex = PaperExecutor(starting_cash=1000.0)
    fill = ex.buy(make_market(), "UP", limit_price=0.52, size=150, book=make_book())
    assert fill.status == "partial"
    assert fill.size == 100  # only the 0.52 level is within limit


def test_buy_rejected_when_no_book_or_size():
    ex = PaperExecutor(starting_cash=1000.0)
    assert ex.buy(make_market(), "UP", 0.5, 10, book=None).status == "rejected"
    assert ex.buy(make_market(), "UP", 0.5, 0, book=make_book()).status == "rejected"
    # limit below best ask -> nothing fillable
    assert ex.buy(make_market(), "UP", 0.10, 10, book=make_book()).status == "rejected"


def test_buy_fee_applied():
    ex = PaperExecutor(starting_cash=1000.0, fee_bps=100.0)  # 1%
    fill = ex.buy(make_market(), "UP", 0.52, 100, book=make_book())
    assert fill.fee == pytest.approx(52.0 * 0.01)
    assert ex.get_cash() == pytest.approx(1000.0 - 52.0 - 0.52)


def test_buy_capped_by_cash():
    ex = PaperExecutor(starting_cash=26.0)
    fill = ex.buy(make_market(), "UP", 0.60, 100, book=make_book())
    # can only afford 26/0.52 = 50 shares
    assert fill.size == pytest.approx(50.0)
    assert ex.get_cash() == pytest.approx(0.0)


def test_sell_reduces_and_realizes_pnl():
    ex = PaperExecutor(starting_cash=1000.0)
    ex.buy(make_market(), "UP", 0.52, 100, book=make_book())
    fill = ex.sell(make_market(), "UP", limit_price=0.45, size=60, book=make_book())
    assert fill.status == "filled"
    assert fill.avg_price == pytest.approx(0.50)  # best bid level covers 60
    assert ex.realized_pnl == pytest.approx(60 * (0.50 - 0.52))
    assert ex.get_positions()["UP"].size == pytest.approx(40)


def test_sell_clamps_to_held_size_and_rejects_unheld():
    ex = PaperExecutor(starting_cash=1000.0)
    assert ex.sell(make_market(), "UP", 0.4, 10, book=make_book()).status == "rejected"
    ex.buy(make_market(), "UP", 0.52, 50, book=make_book())
    fill = ex.sell(make_market(), "UP", 0.40, 500, book=make_book())
    assert fill.size == pytest.approx(50)
    assert "UP" not in ex.get_positions()


def test_settle_pays_winner_and_zeroes_loser():
    ex = PaperExecutor(starting_cash=1000.0)
    m = make_market()
    up_book = make_book("UP")
    dn_book = make_book("DN")
    ex.buy(m, "UP", 0.60, 100, book=up_book)   # 100 @ 0.52 = $52
    ex.buy(m, "DN", 0.60, 50, book=dn_book)
    cash_before = ex.get_cash()
    pnl = ex.settle(m, winning_token_id="UP")
    # UP pays 100*$1, DN pays 0
    assert ex.get_cash() == pytest.approx(cash_before + 100.0)
    assert pnl == pytest.approx((100.0 - 52.0) + (0.0 - 26.0))
    assert ex.get_positions() == {}
    assert ex.realized_pnl == pytest.approx(pnl)


def test_equity_marks_positions():
    ex = PaperExecutor(starting_cash=1000.0)
    ex.buy(make_market(), "UP", 0.52, 100, book=make_book())
    eq = ex.equity({"UP": 0.55})
    assert eq == pytest.approx(1000.0 - 52.0 + 100 * 0.55)
    # missing mark falls back to avg cost
    assert ex.equity({}) == pytest.approx(1000.0)


def test_state_roundtrip(tmp_path):
    path = str(tmp_path / "exec.json")
    ex = PaperExecutor(starting_cash=500.0, state_path=path)
    ex.buy(make_market(), "UP", 0.52, 100, book=make_book())
    ex.meta["period_open"] = {"btc-updown-15m-100": 109000.0}
    ex.save_meta()

    ex2 = PaperExecutor(starting_cash=999.0, state_path=path)
    assert ex2.get_cash() == pytest.approx(ex.get_cash())
    assert ex2.get_positions()["UP"].size == pytest.approx(100)
    assert ex2.get_positions()["UP"].avg_price == pytest.approx(0.52)
    assert ex2.meta["period_open"]["btc-updown-15m-100"] == 109000.0
