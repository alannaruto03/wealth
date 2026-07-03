import pytest

from wealth.polymarket.clob import ClobReadClient, OrderBook, parse_book, walk_book


RAW_BOOK = {
    "bids": [
        {"price": "0.48", "size": "100"},
        {"price": "0.50", "size": "200"},   # unsorted on purpose
        {"price": "0.45", "size": "300"},
    ],
    "asks": [
        {"price": "0.55", "size": "150"},
        {"price": "0.52", "size": "100"},
        {"price": "0.60", "size": "500"},
    ],
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = payloads  # path -> payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        path = url.split("clob.test")[-1]
        return FakeResponse(self.payloads[path])


def test_parse_book_sorts_levels():
    book = parse_book("tok", RAW_BOOK, ts=123.0)
    assert book.best_bid == 0.50
    assert book.best_ask == 0.52
    assert book.mid == pytest.approx(0.51)
    assert book.spread == pytest.approx(0.02)
    assert book.ts == 123.0


def test_depth_usd():
    book = parse_book("tok", RAW_BOOK)
    # best ask level only: 0.52 * 100
    assert book.depth_usd("ask") == pytest.approx(52.0)
    # within 0.03 of best ask: include 0.55 * 150
    assert book.depth_usd("ask", within=0.03) == pytest.approx(52.0 + 82.5)
    assert book.depth_usd("bid") == pytest.approx(100.0)


def test_walk_book_single_level():
    filled, avg, notional = walk_book([(0.52, 100)], size=50, limit_price=0.52)
    assert filled == 50
    assert avg == pytest.approx(0.52)
    assert notional == pytest.approx(26.0)


def test_walk_book_multi_level_avg():
    levels = [(0.52, 100), (0.55, 150)]
    filled, avg, notional = walk_book(levels, size=200, limit_price=0.60)
    assert filled == 200
    assert notional == pytest.approx(0.52 * 100 + 0.55 * 100)
    assert avg == pytest.approx(notional / 200)


def test_walk_book_respects_limit_price():
    levels = [(0.52, 100), (0.55, 150)]
    filled, avg, _ = walk_book(levels, size=200, limit_price=0.52)
    assert filled == 100  # partial: second level above limit
    assert avg == pytest.approx(0.52)


def test_walk_book_bid_side():
    levels = [(0.50, 100), (0.48, 100)]  # bids, best first
    filled, avg, _ = walk_book(levels, size=150, limit_price=0.48, is_ask=False)
    assert filled == 150
    assert avg == pytest.approx((0.50 * 100 + 0.48 * 50) / 150)


def test_walk_book_empty():
    filled, avg, notional = walk_book([], size=10)
    assert (filled, avg, notional) == (0.0, 0.0, 0.0)


def test_client_get_book_and_prices():
    session = FakeSession({
        "/book": RAW_BOOK,
        "/midpoint": {"mid": "0.515"},
        "/price": {"price": "0.52"},
    })
    client = ClobReadClient("https://clob.test", session=session)
    book = client.get_book("tok123")
    assert isinstance(book, OrderBook)
    assert book.best_ask == 0.52
    assert client.get_midpoint("tok123") == pytest.approx(0.515)
    assert client.get_price("tok123", "buy") == pytest.approx(0.52)
    # token id passed through as query param
    assert session.calls[0][1]["token_id"] == "tok123"
    assert session.calls[2][1]["side"] == "BUY"
