"""Snapshot builder + gist publisher + runner integration, all offline."""
import json

import pytest

from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.publisher import (
    GIST_FILENAME,
    MAX_EQUITY_POINTS,
    GistPublisher,
    _downsample,
    build_snapshot,
)


def tick(ts, equity, **extra):
    rec = {"event": "tick", "timestamp": ts, "status": "hold", "cash": equity,
           "equity": equity, "prices": {}, "orders": []}
    rec.update(extra)
    return rec


STATE = {
    "cash": 950.0,
    "positions": {
        "TOKUP": {"market_slug": "btc-updown-15m-1", "size": 100.0, "avg_price": 0.52},
    },
    "meta": {"markets": {"btc-updown-15m-1": {
        "token_up": "TOKUP", "token_down": "TOKDN",
        "period_open": 100000.0, "end_ts": 900.0,
    }}},
}


def test_downsample_caps_and_keeps_endpoints():
    pts = [[str(i), float(i)] for i in range(2000)]
    out = _downsample(pts)
    assert len(out) <= MAX_EQUITY_POINTS
    assert out[0] == pts[0]
    assert out[-1] == pts[-1]
    # short series untouched
    assert _downsample(pts[:100]) == pts[:100]


def test_build_snapshot_schema():
    records = [
        {"event": "market_open", "timestamp": "t0", "slug": "m1"},
        tick("2026-07-03T09:00:00+00:00", 1000.0, spot=100000.0, sigma=0.0005,
             fair={"m1": 0.55}),
        tick("2026-07-03T09:01:00+00:00", 1010.0),
        {"event": "resolution", "timestamp": "2026-07-03T09:15:00+00:00",
         "slug": "m1", "outcome": "up", "pnl": 48.0},
        {"event": "risk_block", "reason": "exposure_cap"},
    ]
    cfg = PolymarketConfig()
    s = build_snapshot(records, STATE, cfg)
    assert s["updated_at"] == "2026-07-03T09:01:00+00:00"
    assert s["mode"] == "paper"
    assert s["kpis"]["equity"] == 1010.0
    assert s["kpis"]["cash"] == 950.0
    assert s["kpis"]["realized_pnl"] == 48.0
    assert s["kpis"]["wins"] == 1 and s["kpis"]["losses"] == 0
    assert s["kpis"]["win_rate"] == 1.0
    assert s["equity_curve"] == [["2026-07-03T09:00:00+00:00", 1000.0],
                                 ["2026-07-03T09:01:00+00:00", 1010.0]]
    assert s["positions"] == [{"symbol": "btc-updown-15m-1:UP", "size": 100.0,
                               "avg_price": 0.52}]
    assert s["resolutions"][0]["slug"] == "m1"
    assert s["risk_blocks"] == {"exposure_cap": 1}
    assert s["spot"] == 100000.0
    assert s["fair"] == {"m1": 0.55}
    json.dumps(s)  # fully serializable


def test_build_snapshot_empty():
    cfg = PolymarketConfig(cash=777.0)
    s = build_snapshot([], {}, cfg)
    assert s["kpis"]["equity"] == 777.0
    assert s["equity_curve"] == []
    assert s["updated_at"] is None


def test_build_snapshot_downsamples():
    records = [tick(f"2026-07-03T09:{i//60:02d}:{i%60:02d}+00:00", 1000.0 + i)
               for i in range(1200)]
    s = build_snapshot(records, {}, PolymarketConfig())
    assert len(s["equity_curve"]) <= MAX_EQUITY_POINTS
    assert s["equity_curve"][-1][1] == 1000.0 + 1199


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self.payload


class FakeGistSession:
    def __init__(self):
        self.posts = []
        self.patches = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json, headers))
        return FakeResponse({"id": "abc123"})

    def patch(self, url, json=None, headers=None, timeout=None):
        self.patches.append((url, json, headers))
        return FakeResponse({"id": "abc123"})


def test_gist_publisher_creates_then_patches():
    session = FakeGistSession()
    pub = GistPublisher("tok", session=session, api_url="https://api.test")
    gist_id = pub.publish({"hello": 1})
    assert gist_id == "abc123"
    assert len(session.posts) == 1
    url, payload, headers = session.posts[0]
    assert url == "https://api.test/gists"
    assert payload["public"] is False
    assert GIST_FILENAME in payload["files"]
    assert headers["Authorization"] == "Bearer tok"

    pub.publish({"hello": 2})
    assert len(session.patches) == 1
    assert session.patches[0][0] == "https://api.test/gists/abc123"
    assert len(session.posts) == 1  # no second create


def test_gist_publisher_requires_token():
    with pytest.raises(ValueError):
        GistPublisher("")


def test_gist_publisher_surfaces_http_errors():
    class FailingSession(FakeGistSession):
        def post(self, url, **kw):
            return FakeResponse({}, status=401)

    pub = GistPublisher("tok", session=FailingSession(), api_url="https://api.test")
    with pytest.raises(RuntimeError):
        pub.publish({})


def test_config_publish_defaults_off():
    cfg = PolymarketConfig()
    assert cfg.publish is False
    assert cfg.publish_every_s == 60.0
