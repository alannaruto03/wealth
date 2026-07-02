"""Slug grid math and Gamma market parsing (no network)."""
import json

from wealth.polymarket.discovery import (
    _parse_market, next_window_start, slug_for, window_start,
)


def test_window_grid_alignment():
    assert window_start(1768425300, 900) == 1768425300
    assert window_start(1768425300 + 899, 900) == 1768425300
    assert next_window_start(1768425300, 900) == 1768426200
    assert window_start(1000, 300) == 900


def test_slug_format():
    assert slug_for("BTC", "15m", 1768425300) == "btc-updown-15m-1768425300"
    assert slug_for("eth", "5m", 300) == "eth-updown-5m-300"


def _raw_market(closed=False, prices=None, outcomes=("Up", "Down")):
    return {
        "slug": "btc-updown-15m-1768425300",
        "conditionId": "0xabc",
        "clobTokenIds": json.dumps(["111", "222"]),
        "outcomes": json.dumps(list(outcomes)),
        "outcomePrices": json.dumps(prices) if prices else None,
        "closed": closed,
        "orderPriceMinTickSize": "0.001",
        "question": "Bitcoin Up or Down?",
    }


def test_parse_open_market():
    m = _parse_market(_raw_market(), 1768425300, 1768426200)
    assert m.token_up == "111" and m.token_down == "222"
    assert m.tick == 0.001
    assert m.outcome is None and not m.closed


def test_parse_reversed_outcome_order():
    raw = _raw_market(outcomes=("Down", "Up"))
    m = _parse_market(raw, 0, 900)
    assert m.token_up == "222" and m.token_down == "111"


def test_parse_resolved_market():
    m = _parse_market(_raw_market(closed=True, prices=["1", "0"]), 0, 900)
    assert m.outcome == "UP"
    m2 = _parse_market(_raw_market(closed=True, prices=["0", "1"]), 0, 900)
    assert m2.outcome == "DOWN"
