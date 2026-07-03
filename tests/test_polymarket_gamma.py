import json

import pytest

from wealth.polymarket.gamma import (
    FIFTEEN_MIN_S,
    GammaClient,
    hourly_slug_candidates,
    parse_market,
    slug_15m,
)


def make_raw(slug="btc-updown-15m-1783069200", outcomes=("Up", "Down"),
             tokens=("111", "222"), end="2026-07-03T09:15:00Z", **extra):
    raw = {
        "slug": slug,
        "conditionId": "0xcond",
        "question": "Bitcoin Up or Down?",
        "outcomes": json.dumps(list(outcomes)),
        "clobTokenIds": json.dumps(list(tokens)),
        "endDate": end,
        "negRisk": False,
        "closed": False,
    }
    raw.update(extra)
    return raw


def test_slug_15m_math():
    now = 1783069337.0
    period_start = int(now // FIFTEEN_MIN_S * FIFTEEN_MIN_S)
    assert period_start == 1783069200
    assert slug_15m(period_start) == "btc-updown-15m-1783069200"


def test_hourly_slug_candidates_format():
    # 2026-07-03 19:00:00 UTC == 3pm ET (EDT)
    candidates = hourly_slug_candidates(1783105200)
    assert "btc-updown-1h-1783105200" in candidates
    assert "bitcoin-up-or-down-july-3-3pm-et" in candidates


def test_parse_market_json_string_fields():
    m = parse_market(make_raw(), "15m")
    assert m.token_id_up == "111"
    assert m.token_id_down == "222"
    assert m.end_ts == pytest.approx(1783070100.0)
    # startDate absent -> end - period
    assert m.start_ts == pytest.approx(m.end_ts - FIFTEEN_MIN_S)


def test_parse_market_maps_tokens_by_outcome_order():
    m = parse_market(make_raw(outcomes=("Down", "Up")), "15m")
    assert m.token_id_up == "222"
    assert m.token_id_down == "111"


def test_parse_market_accepts_yes_no():
    m = parse_market(make_raw(outcomes=("Yes", "No")), "15m")
    assert m.token_id_up == "111"


def test_parse_market_rejects_bad_shapes():
    with pytest.raises(ValueError):
        parse_market(make_raw(tokens=("111",), outcomes=("Up",)), "15m")
    with pytest.raises(ValueError):
        parse_market(make_raw(outcomes=("Foo", "Bar")), "15m")
    bad = make_raw()
    del bad["endDate"]
    with pytest.raises(ValueError):
        parse_market(bad, "15m")


def test_parse_market_uses_event_start_time():
    raw = make_raw(eventStartTime="2026-07-03T09:00:00Z")
    m = parse_market(raw, "15m")
    assert m.start_ts == pytest.approx(1783069200.0)


def test_parse_market_ignores_stale_start_date():
    # listing date weeks before the period must not be used as period start
    raw = make_raw(startDate="2026-06-01T00:00:00Z")
    m = parse_market(raw, "15m")
    assert m.start_ts == pytest.approx(m.end_ts - FIFTEEN_MIN_S)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    """Answers /markets?slug= exactly, and /markets scans with a canned list."""

    def __init__(self, by_slug=None, scan=None):
        self.by_slug = by_slug or {}
        self.scan = scan or []
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if params and "slug" in params:
            match = self.by_slug.get(params["slug"])
            return FakeResponse([match] if match else [])
        return FakeResponse(self.scan)


def test_discover_finds_current_and_next_15m():
    now = 1783069337.0  # inside period 1783069200
    cur = make_raw(slug=slug_15m(1783069200), end="2026-07-03T09:15:00Z")
    nxt = make_raw(slug=slug_15m(1783070100), end="2026-07-03T09:30:00Z",
                   tokens=("333", "444"))
    client = GammaClient("https://gamma.test",
                         session=FakeSession(by_slug={cur["slug"]: cur, nxt["slug"]: nxt}))
    found = client.discover(["15m"], now)
    assert [m.slug for m in found] == [cur["slug"], nxt["slug"]]
    assert found[1].token_id_up == "333"


def test_discover_scan_fallback_by_end_date():
    now = 1783069337.0
    # slug lookup misses; scan has a market whose endDate matches the period end
    scan_hit = make_raw(slug="weird-new-slug-format",
                        end="2026-07-03T09:15:00Z")
    scan_miss = make_raw(slug="btc-updown-15m-other", end="2026-07-03T11:00:00Z")
    unrelated = make_raw(slug="will-aliens-land", end="2026-07-03T09:15:00Z")
    unrelated["question"] = "Will aliens land?"
    client = GammaClient("https://gamma.test",
                         session=FakeSession(scan=[unrelated, scan_miss, scan_hit]))
    found = client.discover(["15m"], now)
    assert [m.slug for m in found] == ["weird-new-slug-format"]


def test_discover_skips_closed_markets():
    now = 1783069337.0
    cur = make_raw(slug=slug_15m(1783069200))
    cur["closed"] = True
    client = GammaClient("https://gamma.test", session=FakeSession(by_slug={cur["slug"]: cur}))
    assert client.discover(["15m"], now) == []


def test_price_to_beat_extracted():
    m = parse_market(make_raw(priceToBeat="109350.25"), "15m")
    assert m.price_to_beat == pytest.approx(109350.25)
