"""Snapshot publisher: journal + state -> compact JSON -> secret GitHub gist.

This is the feed for the static live-view page (web/index.html, hosted on
Vercel or any static host). A secret gist is unlisted but readable by anyone
holding its id, so the id in the page URL acts as the access key — fine for
read-only paper-trading stats; delete the gist to rotate.

The token comes from the environment only (default WEALTH_PUBLISH_TOKEN) and
needs nothing beyond gist read/write scope.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

import requests

from wealth.dashboard.data_access import polymarket_view
from wealth.polymarket.config import PolymarketConfig

GIST_FILENAME = "wealth-live.json"
MAX_EQUITY_POINTS = 500
MAX_RESOLUTIONS = 30


def _downsample(points: List[list], limit: int = MAX_EQUITY_POINTS) -> List[list]:
    """Thin a [ts, value] series to <= limit points, keeping both endpoints."""
    n = len(points)
    if n <= limit:
        return points
    step = (n - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def _label_positions(state: Dict) -> List[Dict]:
    labels: Dict[str, str] = {}
    for slug, info in state.get("meta", {}).get("markets", {}).items():
        labels[str(info.get("token_up", ""))] = f"{slug}:UP"
        labels[str(info.get("token_down", ""))] = f"{slug}:DOWN"
    out = []
    for token_id, p in state.get("positions", {}).items():
        out.append({
            "symbol": labels.get(token_id, p.get("market_slug", token_id)),
            "size": round(float(p.get("size", 0.0)), 2),
            "avg_price": round(float(p.get("avg_price", 0.0)), 4),
        })
    return out


def build_snapshot(records: List[dict], state: Dict, cfg: PolymarketConfig) -> Dict:
    """Everything the live page renders, in one small JSON document."""
    pv = polymarket_view(records)

    equity_points: List[list] = []
    last_tick_ts = None
    for r in records:
        if r.get("event") == "tick" and r.get("equity") is not None:
            equity_points.append([r.get("timestamp"), round(float(r["equity"]), 2)])
            last_tick_ts = r.get("timestamp")

    resolutions = [
        {"timestamp": row["timestamp"], "slug": row["slug"],
         "outcome": row["outcome"], "pnl": round(float(row["pnl"]), 2)}
        for row in pv.resolutions.tail(MAX_RESOLUTIONS).to_dict("records")
    ]

    equity_now = equity_points[-1][1] if equity_points else float(state.get("cash", cfg.cash))
    return {
        "updated_at": last_tick_ts,
        "mode": cfg.mode,
        "series": cfg.series,
        "kpis": {
            "equity": equity_now,
            "cash": round(float(state.get("cash", cfg.cash)), 2),
            "realized_pnl": round(pv.realized_pnl, 2),
            "wins": pv.wins,
            "losses": pv.losses,
            "win_rate": round(pv.win_rate, 4) if pv.win_rate is not None else None,
            "resolved": int(len(pv.resolutions)),
        },
        "equity_curve": _downsample(equity_points),
        "positions": _label_positions(state),
        "resolutions": resolutions,
        "risk_blocks": pv.risk_blocks,
        "spot": pv.spot,
        "sigma": pv.sigma,
        "fair": pv.fair,
    }


class GistPublisher:
    """Create-once-then-update a secret gist holding the snapshot."""

    def __init__(self, token: str, gist_id: Optional[str] = None,
                 session: Optional[requests.Session] = None,
                 api_url: str = "https://api.github.com", timeout: float = 10.0):
        if not token:
            raise ValueError("gist publishing needs a GitHub token with gist scope")
        self.token = token
        self.gist_id = gist_id
        self.session = session or requests.Session()
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def publish(self, snapshot: Dict) -> str:
        """Upload the snapshot; returns the gist id (created on first call)."""
        payload = {
            "files": {GIST_FILENAME: {"content": json.dumps(snapshot, default=str)}},
        }
        if self.gist_id:
            resp = self.session.patch(
                f"{self.api_url}/gists/{self.gist_id}",
                json=payload, headers=self._headers(), timeout=self.timeout,
            )
        else:
            payload["description"] = "wealth polymarket bot — live snapshot"
            payload["public"] = False
            resp = self.session.post(
                f"{self.api_url}/gists",
                json=payload, headers=self._headers(), timeout=self.timeout,
            )
        resp.raise_for_status()
        if not self.gist_id:
            self.gist_id = resp.json()["id"]
        return self.gist_id

    def page_url(self, base: str = "https://YOUR-PROJECT.vercel.app") -> str:
        return f"{base}/?gist={self.gist_id}"
