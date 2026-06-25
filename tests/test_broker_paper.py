"""PaperBroker fills, accounting, persistence, and weight->order translation."""
import pytest

from wealth.broker.paper import PaperBroker
from wealth.engine.costs import CostModel


def make_broker(prices, cash=10_000.0, cost=CostModel(0.0, 0.0), state_path=None):
    book = dict(prices)
    return PaperBroker(
        price_fn=lambda s: book[s],
        starting_cash=cash,
        cost_model=cost,
        state_path=state_path,
    ), book


def test_buy_then_sell_pnl():
    broker, book = make_broker({"BTC/USDT": 100.0})
    broker.submit_order("BTC/USDT", "buy", 10.0)
    assert broker.cash == pytest.approx(9_000.0)
    assert broker.get_positions()["BTC/USDT"].quantity == pytest.approx(10.0)
    book["BTC/USDT"] = 120.0
    broker.submit_order("BTC/USDT", "sell", 10.0)
    assert broker.cash == pytest.approx(10_200.0)
    assert "BTC/USDT" not in broker.get_positions()


def test_equity_marks_to_market():
    broker, book = make_broker({"ETH/USDT": 50.0})
    broker.submit_order("ETH/USDT", "buy", 20.0)  # spend 1000
    assert broker.get_account()["equity"] == pytest.approx(10_000.0)
    book["ETH/USDT"] = 75.0
    assert broker.get_account()["equity"] == pytest.approx(10_500.0)


def test_costs_charged():
    broker, _ = make_broker({"BTC/USDT": 100.0}, cost=CostModel(10.0, 0.0))
    broker.submit_order("BTC/USDT", "buy", 1.0)
    # 100 notional * 10bps = 0.1 commission, plus 100 for the unit.
    assert broker.cash == pytest.approx(10_000.0 - 100.0 - 0.1)


def test_target_weights_to_orders_opens_position():
    broker, _ = make_broker({"BTC/USDT": 100.0, "ETH/USDT": 50.0}, cash=10_000.0)
    orders = broker.target_weights_to_orders(
        {"BTC/USDT": 0.5, "ETH/USDT": 0.5},
        {"BTC/USDT": 100.0, "ETH/USDT": 50.0},
    )
    assert len(orders) == 2
    pos = broker.get_positions()
    assert pos["BTC/USDT"].quantity == pytest.approx(50.0)   # 5000/100
    assert pos["ETH/USDT"].quantity == pytest.approx(100.0)  # 5000/50


def test_target_weights_rebalance_to_cash():
    broker, _ = make_broker({"BTC/USDT": 100.0}, cash=10_000.0)
    broker.target_weights_to_orders({"BTC/USDT": 1.0}, {"BTC/USDT": 100.0})
    assert broker.get_positions()["BTC/USDT"].quantity == pytest.approx(100.0)
    # Now target flat -> should sell everything.
    broker.target_weights_to_orders({"BTC/USDT": 0.0}, {"BTC/USDT": 100.0})
    assert "BTC/USDT" not in broker.get_positions()


def test_state_persistence(tmp_path):
    path = str(tmp_path / "state.json")
    broker, _ = make_broker({"BTC/USDT": 100.0}, state_path=path)
    broker.submit_order("BTC/USDT", "buy", 3.0)
    # New broker instance reloads the saved book.
    reloaded = PaperBroker(price_fn=lambda s: 100.0, starting_cash=10_000.0, state_path=path)
    assert reloaded.cash == pytest.approx(broker.cash)
    assert reloaded.get_positions()["BTC/USDT"].quantity == pytest.approx(3.0)
