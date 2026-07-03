"""Read-only CLOB client and order-book math.

Order books come from the public, unauthenticated endpoints on
https://clob.polymarket.com — enough for paper trading and signal
generation. Live order placement lives in live.py behind py-clob-client.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import requests

Level = Tuple[float, float]  # (price, size in shares)


@dataclass
class OrderBook:
    token_id: str
    bids: List[Level] = field(default_factory=list)  # sorted best (highest) first
    asks: List[Level] = field(default_factory=list)  # sorted best (lowest) first
    ts: float = 0.0  # unix seconds when fetched

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def depth_usd(self, side: str, within: float = 0.0) -> float:
        """Notional resting within ``within`` of the best price on ``side``."""
        levels = self.asks if side == "ask" else self.bids
        if not levels:
            return 0.0
        best = levels[0][0]
        total = 0.0
        for price, size in levels:
            if abs(price - best) > within + 1e-12:
                break
            total += price * size
        return total

    def age_s(self, now: Optional[float] = None) -> float:
        return (now if now is not None else time.time()) - self.ts


def walk_book(levels: List[Level], size: float, limit_price: Optional[float] = None,
              is_ask: bool = True) -> Tuple[float, float, float]:
    """Consume ``size`` shares from ``levels``; return (filled, avg_price, notional).

    ``limit_price`` stops the walk at levels worse than the limit (higher than
    it for asks, lower for bids). Partial fills are allowed.
    """
    filled = 0.0
    notional = 0.0
    for price, avail in levels:
        if limit_price is not None:
            if is_ask and price > limit_price + 1e-12:
                break
            if not is_ask and price < limit_price - 1e-12:
                break
        take = min(size - filled, avail)
        if take <= 0:
            break
        filled += take
        notional += take * price
    avg = notional / filled if filled > 0 else 0.0
    return filled, avg, notional


def parse_book(token_id: str, raw: dict, ts: Optional[float] = None) -> OrderBook:
    """Build an OrderBook from the CLOB /book JSON (string prices/sizes)."""
    bids = sorted(
        ((float(l["price"]), float(l["size"])) for l in raw.get("bids", [])),
        key=lambda x: -x[0],
    )
    asks = sorted(
        ((float(l["price"]), float(l["size"])) for l in raw.get("asks", [])),
        key=lambda x: x[0],
    )
    return OrderBook(token_id=token_id, bids=bids, asks=asks,
                     ts=ts if ts is not None else time.time())


class ClobReadClient:
    def __init__(self, base_url: str = "https://clob.polymarket.com",
                 session: Optional[requests.Session] = None, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, **params) -> dict:
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_book(self, token_id: str) -> OrderBook:
        raw = self._get("/book", token_id=token_id)
        return parse_book(token_id, raw)

    def get_midpoint(self, token_id: str) -> Optional[float]:
        raw = self._get("/midpoint", token_id=token_id)
        mid = raw.get("mid")
        return float(mid) if mid is not None else None

    def get_price(self, token_id: str, side: str) -> Optional[float]:
        raw = self._get("/price", token_id=token_id, side=side.upper())
        price = raw.get("price")
        return float(price) if price is not None else None
