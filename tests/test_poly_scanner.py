"""Value scanner: candidate parsing, filters, edge model, ranking (no network)."""
import json
from datetime import datetime, timedelta, timezone

from wealth.polymarket.config import ValueBotConfig
from wealth.polymarket.scanner import (
    bias_bump, estimate_edge, parse_candidate, rank, resolved_winner,
)

NOW = datetime(2026, 7, 3, tzinfo=timezone.utc)


def _cfg(**kw):
    return ValueBotConfig(**kw)


def _raw(**overrides):
    m = {
        "slug": "will-x-happen",
        "question": "Will X happen?",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["101", "202"]),
        "bestBid": 0.94,
        "bestAsk": 0.95,
        "volumeNum": 50_000,
        "liquidityNum": 5_000,
        "endDate": (NOW + timedelta(days=5)).isoformat(),
        "closed": False,
        "active": True,
    }
    m.update(overrides)
    return m


def test_good_favorite_parses():
    c = parse_candidate(_raw(), _cfg(), now=NOW)
    assert c is not None
    assert c.outcome_index == 0 and c.token_id == "101"
    assert c.ask == 0.95
    assert c.edge > 0


def test_favorite_on_outcome_1():
    # outcome 0 is the longshot (bid 0.06/ask 0.08) -> favorite is outcome 1
    c = parse_candidate(_raw(bestBid=0.06, bestAsk=0.08), _cfg(), now=NOW)
    assert c is not None
    assert c.outcome_index == 1 and c.token_id == "202"
    assert abs(c.ask - 0.94) < 1e-9  # 1 - bid


def test_updown_slug_excluded():
    assert parse_candidate(_raw(slug="btc-updown-15m-1768425300"),
                           _cfg(), now=NOW) is None


def test_filters_exclude():
    cfg = _cfg()
    assert parse_candidate(_raw(volumeNum=100), cfg, now=NOW) is None
    assert parse_candidate(_raw(liquidityNum=10), cfg, now=NOW) is None
    assert parse_candidate(_raw(bestBid=0.80, bestAsk=0.95), cfg, now=NOW) is None  # spread
    assert parse_candidate(_raw(bestBid=0.50, bestAsk=0.51), cfg, now=NOW) is None  # not fav band
    assert parse_candidate(_raw(bestBid=0.985, bestAsk=0.99), cfg, now=NOW) is None  # too rich
    far = (NOW + timedelta(days=90)).isoformat()
    assert parse_candidate(_raw(endDate=far), cfg, now=NOW) is None  # too far out
    assert parse_candidate(_raw(closed=True), cfg, now=NOW) is None
    assert parse_candidate(_raw(endDate=None), cfg, now=NOW) is None
    assert parse_candidate(_raw(outcomes=json.dumps(["A", "B", "C"]),
                                clobTokenIds=json.dumps(["1", "2", "3"])),
                           cfg, now=NOW) is None  # not binary


def test_bias_bump_interpolates_down():
    cfg = _cfg(fav_min=0.90, fav_max=0.97, bias_bump_low=0.02, bias_bump_high=0.01)
    assert abs(bias_bump(0.90, cfg) - 0.02) < 1e-9
    assert abs(bias_bump(0.97, cfg) - 0.01) < 1e-9
    assert 0.01 < bias_bump(0.935, cfg) < 0.02


def test_edge_includes_fee_and_haircut():
    cfg = _cfg(bias_bump_low=0.02, bias_bump_high=0.02, haircut=0.005,
               event_fee_rate=0.035)
    p_true, edge = estimate_edge(0.95, cfg)
    assert abs(p_true - (0.95 + 0.02 - 0.005)) < 1e-9
    fee = 0.035 * 0.95 * 0.05
    assert abs(edge - (p_true - 0.95 - fee)) < 1e-9


def test_min_edge_filter():
    cfg = _cfg(bias_bump_low=0.001, bias_bump_high=0.001, haircut=0.01,
               min_edge=0.004)
    assert parse_candidate(_raw(), cfg, now=NOW) is None  # negative edge


def test_rank_orders_by_stake_score():
    a = parse_candidate(_raw(slug="a", liquidityNum=20_000), _cfg(), now=NOW)
    b = parse_candidate(_raw(slug="b", liquidityNum=600), _cfg(), now=NOW)
    ranked = rank([b, a])
    assert ranked[0].slug == "a"  # same edge, more liquidity first


def test_resolved_winner():
    assert resolved_winner({"closed": True, "outcomePrices": '["1", "0"]'}) == 0
    assert resolved_winner({"closed": True, "outcomePrices": '["0", "1"]'}) == 1
    assert resolved_winner({"closed": False, "outcomePrices": '["1", "0"]'}) is None
    assert resolved_winner({"closed": True, "outcomePrices": '["0.5", "0.5"]'}) is None
    assert resolved_winner({"closed": True}) is None
