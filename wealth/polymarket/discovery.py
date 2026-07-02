"""Market discovery for the recurring Up-or-Down series via the Gamma API.

The markets live on a fixed Unix-epoch grid: a 15-minute BTC window starting
at ``ts`` (with ``ts % 900 == 0``) has the event/market slug
``btc-updown-15m-{ts}``. Resolution: UP wins when the Chainlink Data Streams
price at window end is >= the "price to beat" captured at window start
(ties resolve UP). Settlement is automated (Chainlink Automation) — the
Gamma market flips to closed with outcomePrices like ["1","0"] shortly after
the window ends.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"

_ASSET_SLUG = {"BTC": "btc", "ETH": "eth", "SOL": "sol", "XRP": "xrp"}


def window_start(now: Optional[float] = None, horizon_s: int = 900) -> int:
    """Start timestamp of the window containing ``now`` (grid-aligned)."""
    t = int(now if now is not None else time.time())
    return (t // horizon_s) * horizon_s


def next_window_start(now: Optional[float] = None, horizon_s: int = 900) -> int:
    return window_start(now, horizon_s) + horizon_s


def slug_for(asset: str, horizon: str, start_ts: int) -> str:
    return f"{_ASSET_SLUG[asset.upper()]}-updown-{horizon}-{start_ts}"


@dataclass
class UpDownMarket:
    """One window's market: the pair of outcome tokens and its metadata."""
    slug: str
    condition_id: str
    token_up: str
    token_down: str
    start_ts: int
    end_ts: int
    tick: float = 0.01
    question: str = ""
    closed: bool = False
    outcome: Optional[str] = None          # "UP" | "DOWN" once resolved
    raw: Dict = field(default_factory=dict)


def _parse_market(m: Dict, start_ts: int, end_ts: int) -> UpDownMarket:
    token_ids = m.get("clobTokenIds")
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)
    outcomes = m.get("outcomes")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    outcomes = [str(o).lower() for o in (outcomes or [])]
    # map token ids to Up/Down by outcome order; default to given order
    up_idx, down_idx = 0, 1
    if "up" in outcomes and "down" in outcomes:
        up_idx, down_idx = outcomes.index("up"), outcomes.index("down")

    outcome = None
    prices = m.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            prices = None
    if m.get("closed") and prices and len(prices) == 2:
        try:
            outcome = "UP" if float(prices[up_idx]) > float(prices[down_idx]) else "DOWN"
        except (TypeError, ValueError):
            outcome = None

    tick = 0.01
    for key in ("orderPriceMinTickSize", "minimum_tick_size", "minimumTickSize"):
        if m.get(key):
            try:
                tick = float(m[key])
                break
            except (TypeError, ValueError):
                pass

    return UpDownMarket(
        slug=m.get("slug", ""),
        condition_id=m.get("conditionId", m.get("condition_id", "")),
        token_up=str(token_ids[up_idx]) if token_ids else "",
        token_down=str(token_ids[down_idx]) if token_ids and len(token_ids) > 1 else "",
        start_ts=start_ts,
        end_ts=end_ts,
        tick=tick,
        question=m.get("question", ""),
        closed=bool(m.get("closed")),
        outcome=outcome,
        raw=m,
    )


class GammaClient:
    """Thin Gamma API client (no auth; data endpoints are not geo-blocked)."""

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.Client(base_url=GAMMA_BASE, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def market_by_slug(self, slug: str) -> Optional[Dict]:
        r = self._client.get("/markets", params={"slug": slug})
        r.raise_for_status()
        items: List[Dict] = r.json()
        return items[0] if items else None

    def get_window_market(
        self, asset: str, horizon: str, start_ts: int, horizon_s: int
    ) -> Optional[UpDownMarket]:
        """Fetch the market for one grid window; None if not listed yet."""
        slug = slug_for(asset, horizon, start_ts)
        m = self.market_by_slug(slug)
        if not m:
            return None
        return _parse_market(m, start_ts, start_ts + horizon_s)

    def wait_for_window(
        self,
        asset: str,
        horizon: str,
        start_ts: int,
        horizon_s: int,
        deadline: float,
        poll_s: float = 2.0,
    ) -> Optional[UpDownMarket]:
        """Poll until the window's market is listed (it can lag the grid)."""
        while time.time() < deadline:
            try:
                mkt = self.get_window_market(asset, horizon, start_ts, horizon_s)
            except httpx.HTTPError:
                mkt = None
            if mkt and mkt.token_up and mkt.token_down:
                return mkt
            time.sleep(poll_s)
        return None

    def refresh(self, market: UpDownMarket) -> UpDownMarket:
        """Re-fetch a market (used to poll for resolution after window end)."""
        m = self.market_by_slug(market.slug)
        if not m:
            return market
        return _parse_market(m, market.start_ts, market.end_ts)
