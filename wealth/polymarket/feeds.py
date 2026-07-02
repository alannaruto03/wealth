"""Live market data: Binance spot, Polymarket RTDS (Chainlink), CLOB books.

Every feed is WebSocket-first with a REST-polling fallback, so the bot
degrades gracefully in environments where raw WS is blocked (e.g. behind an
HTTPS proxy). REST cadence is ~1s — fine for paper trading; production
quoting wants the WS paths.

Feeds only maintain state; the runner reads it. All tasks are supervised:
a WS task that dies falls back to polling instead of killing the bot.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple

import httpx

BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443/ws"
CLOB_REST = "https://clob.polymarket.com"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RTDS_WS = "wss://ws-live-data.polymarket.com"

_BINANCE_SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}


@dataclass
class BookTop:
    bid: Optional[float] = None
    bid_size: float = 0.0
    ask: Optional[float] = None
    ask_size: float = 0.0
    ts: float = 0.0

    @property
    def mid(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        return self.bid if self.bid is not None else self.ask


@dataclass
class MarketData:
    """Shared live state: spot ticks, Chainlink marks, per-token book tops."""
    asset: str = "BTC"
    spot: Optional[float] = None
    spot_ts: float = 0.0
    chainlink: Optional[float] = None
    chainlink_ts: float = 0.0
    books: Dict[str, BookTop] = field(default_factory=dict)
    ticks: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=600))

    def record_spot(self, ts: float, price: float) -> None:
        self.spot, self.spot_ts = price, ts
        self.ticks.append((ts, price))

    def recent_move_bps(self, window_s: float, now: Optional[float] = None) -> float:
        """Largest abs move (bps) between now's price and any tick in window."""
        if self.spot is None or not self.ticks:
            return 0.0
        now = now if now is not None else time.time()
        cutoff = now - window_s
        worst = 0.0
        for ts, px in reversed(self.ticks):
            if ts < cutoff:
                break
            if px > 0:
                worst = max(worst, abs(self.spot / px - 1.0) * 1e4)
        return worst

    def spot_stale(self, max_age_s: float, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        return self.spot is None or (now - self.spot_ts) > max_age_s

    def book(self, token_id: str) -> BookTop:
        return self.books.get(token_id, BookTop())


def _best_of(levels, pick) -> Tuple[Optional[float], float]:
    """(price, size) of the best level; tolerant of unsorted lists."""
    best_p, best_s = None, 0.0
    for lvl in levels or []:
        try:
            p, s = float(lvl["price"]), float(lvl["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if s <= 0:
            continue
        if best_p is None or pick(p, best_p):
            best_p, best_s = p, s
    return best_p, best_s


class FeedManager:
    """Runs the feed tasks for one window and keeps MarketData fresh."""

    def __init__(self, data: MarketData, poll_interval_s: float = 1.0,
                 prefer_websocket: bool = True):
        self.data = data
        self.poll_interval_s = poll_interval_s
        self.prefer_websocket = prefer_websocket
        self._http = httpx.AsyncClient(timeout=10.0)
        self._tasks: list = []
        self._token_ids: list = []

    async def start(self, token_ids) -> None:
        self._token_ids = list(token_ids)
        self._tasks.append(asyncio.create_task(self._supervise(
            self._binance_ws, self._binance_poll, name="binance")))
        self._tasks.append(asyncio.create_task(self._supervise(
            self._rtds_ws, None, name="rtds")))
        self._tasks.append(asyncio.create_task(self._supervise(
            self._clob_ws, self._books_poll, name="clob")))

    async def set_tokens(self, token_ids) -> None:
        """Switch order-book subscriptions to a new window's tokens."""
        self._token_ids = list(token_ids)
        # WS book subscriptions are per-connection; simplest robust behaviour
        # is to restart the book task. Polling picks the new list up directly.
        for t in self._tasks:
            if getattr(t, "_feed_name", "") == "clob":
                t.cancel()
        self._tasks = [t for t in self._tasks if not t.done() and not t.cancelled()]
        self._tasks.append(asyncio.create_task(self._supervise(
            self._clob_ws, self._books_poll, name="clob")))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await self._http.aclose()

    # -- supervision ---------------------------------------------------------
    async def _supervise(self, ws_fn, poll_fn, name: str) -> None:
        task = asyncio.current_task()
        if task is not None:
            task._feed_name = name  # type: ignore[attr-defined]
        if self.prefer_websocket:
            failures = 0
            while failures < 3:
                try:
                    await ws_fn()      # clean close -> reconnect
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures += 1      # repeated errors -> REST fallback
                await asyncio.sleep(1.0)
        if poll_fn is not None:
            while True:
                try:
                    await poll_fn()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(self.poll_interval_s)

    # -- Binance spot ---------------------------------------------------------
    async def _binance_ws(self) -> None:
        import websockets  # optional import; failure triggers REST fallback

        symbol = _BINANCE_SYMBOL[self.data.asset].lower()
        url = f"{BINANCE_WS}/{symbol}@bookTicker"
        async with websockets.connect(url, open_timeout=8) as ws:
            async for raw in ws:
                m = json.loads(raw)
                bid, ask = float(m.get("b", 0)), float(m.get("a", 0))
                if bid > 0 and ask > 0:
                    self.data.record_spot(time.time(), (bid + ask) / 2.0)

    async def _binance_poll(self) -> None:
        symbol = _BINANCE_SYMBOL[self.data.asset]
        while True:
            r = await self._http.get(
                f"{BINANCE_REST}/api/v3/ticker/bookTicker", params={"symbol": symbol})
            r.raise_for_status()
            m = r.json()
            bid, ask = float(m.get("bidPrice", 0)), float(m.get("askPrice", 0))
            if bid > 0 and ask > 0:
                self.data.record_spot(time.time(), (bid + ask) / 2.0)
            await asyncio.sleep(self.poll_interval_s)

    # -- Polymarket RTDS (Chainlink settlement feed) --------------------------
    async def _rtds_ws(self) -> None:
        """Best-effort: parses tolerantly; settlement truth is Gamma anyway."""
        import websockets

        sym = f"{self.data.asset.lower()}/usd"
        async with websockets.connect(RTDS_WS, open_timeout=8) as ws:
            sub = {"action": "subscribe",
                   "subscriptions": [{"topic": "crypto_prices", "type": "update"}]}
            await ws.send(json.dumps(sub))
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                payload = m.get("payload", m)
                symbol = str(payload.get("symbol", "")).lower()
                if sym.split("/")[0] not in symbol:
                    continue
                value = payload.get("value", payload.get("price"))
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    continue
                source = str(payload.get("source", "")).lower()
                now = time.time()
                if "chainlink" in source:
                    self.data.chainlink, self.data.chainlink_ts = price, now
                elif "binance" in source and self.data.spot_stale(2.0, now):
                    self.data.record_spot(now, price)
                else:  # unlabeled: still useful as a chainlink-ish mark
                    self.data.chainlink, self.data.chainlink_ts = price, now

    # -- CLOB order books ------------------------------------------------------
    def _apply_book(self, token_id: str, book: dict) -> None:
        bid, bid_sz = _best_of(book.get("bids"), lambda p, b: p > b)
        ask, ask_sz = _best_of(book.get("asks"), lambda p, b: p < b)
        self.data.books[token_id] = BookTop(
            bid=bid, bid_size=bid_sz, ask=ask, ask_size=ask_sz, ts=time.time())

    async def _clob_ws(self) -> None:
        import websockets

        if not self._token_ids:
            raise RuntimeError("no tokens to subscribe")
        async with websockets.connect(CLOB_WS, open_timeout=8) as ws:
            await ws.send(json.dumps(
                {"assets_ids": self._token_ids, "type": "market"}))
            async for raw in ws:
                try:
                    msgs = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msgs, dict):
                    msgs = [msgs]
                for m in msgs:
                    if m.get("event_type") == "book" and m.get("asset_id"):
                        self._apply_book(m["asset_id"],
                                         {"bids": m.get("bids") or m.get("buys"),
                                          "asks": m.get("asks") or m.get("sells")})

    async def _books_poll(self) -> None:
        while True:
            for token_id in list(self._token_ids):
                r = await self._http.get(
                    f"{CLOB_REST}/book", params={"token_id": token_id})
                if r.status_code == 200:
                    self._apply_book(token_id, r.json())
            await asyncio.sleep(self.poll_interval_s)
