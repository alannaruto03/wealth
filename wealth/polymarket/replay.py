"""Replay recorded book snapshots through the real runner — the honest backtest.

The replay swaps the network-facing pieces (Gamma, CLOB, spot feed, clock) for
recording-driven fakes and drives the *same* PolymarketRunner + EdgeSignal +
RiskManager + PaperExecutor code path used live. Fills still walk the recorded
books, so results carry real depth costs (but remain optimistic: snapshots
have no queue or latency).
"""
from __future__ import annotations

import json
from itertools import groupby
from typing import Dict, List, Optional

from wealth.live.journal import Journal
from wealth.polymarket.clob import OrderBook
from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.gamma import MarketInfo
from wealth.polymarket.paper import PaperExecutor
from wealth.polymarket.runner import PolymarketRunner


def _book_from_payload(token_id: str, payload: Optional[Dict]) -> Optional[OrderBook]:
    if not payload:
        return None
    return OrderBook(
        token_id=token_id,
        bids=[tuple(l) for l in payload.get("bids", [])],
        asks=[tuple(l) for l in payload.get("asks", [])],
        ts=payload.get("ts", 0.0),
    )


def _market_from_record(rec: Dict) -> MarketInfo:
    return MarketInfo(
        slug=rec["slug"], condition_id="", question=rec.get("question", ""),
        token_id_up=rec["token_up"], token_id_down=rec["token_down"],
        start_ts=rec.get("start_ts", 0.0), end_ts=rec["end_ts"],
        series=rec.get("series", ""), neg_risk=rec.get("neg_risk", False),
        price_to_beat=rec.get("period_open"),
    )


class _Clock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class ReplaySpot:
    """SpotFeed stand-in fed from recording lines."""

    def __init__(self, clock: _Clock):
        self.clock = clock
        self.spot: Optional[float] = None
        self.spot_ts = 0.0
        self._sigma: Optional[float] = None

    def set(self, spot: float, sigma: Optional[float], ts: float) -> None:
        self.spot, self._sigma, self.spot_ts = spot, sigma, ts

    def refresh(self) -> None:  # runner calls this; data is pushed instead
        return None

    @property
    def sigma(self) -> Optional[float]:
        return self._sigma

    def age_s(self, now: Optional[float] = None) -> float:
        return (now if now is not None else self.clock()) - self.spot_ts


class ReplayClob:
    def __init__(self):
        self.books: Dict[str, OrderBook] = {}

    def set_book(self, token_id: str, book: Optional[OrderBook]) -> None:
        if book is None:
            self.books.pop(token_id, None)
        else:
            self.books[token_id] = book

    def get_book(self, token_id: str) -> OrderBook:
        return self.books[token_id]  # KeyError -> runner treats as missing book


class ReplayGamma:
    def __init__(self):
        self.current: Dict[str, MarketInfo] = {}

    def set_markets(self, markets: List[MarketInfo]) -> None:
        self.current = {m.slug: m for m in markets}

    def discover(self, series: List[str], now: float) -> List[MarketInfo]:
        return [m for m in self.current.values() if m.series in series]


def load_recording(path: str) -> List[Dict]:
    records: List[Dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    records.sort(key=lambda r: r["ts"])
    return records


def replay_run(cfg: PolymarketConfig, recording_path: str,
               journal: Optional[Journal] = None) -> PolymarketRunner:
    """Drive the runner across a recording; returns the runner (executor holds PnL)."""
    records = load_recording(recording_path)
    if not records:
        raise ValueError(f"empty recording: {recording_path}")

    clock = _Clock()
    spot = ReplaySpot(clock)
    clob = ReplayClob()
    gamma = ReplayGamma()
    executor = PaperExecutor(starting_cash=cfg.cash, fee_bps=cfg.fee_bps)
    runner = PolymarketRunner(cfg, gamma, clob, spot, executor,
                              journal=journal, clock=clock)

    for ts, group in groupby(records, key=lambda r: r["ts"]):
        rows = list(group)
        clock.t = ts
        markets = [_market_from_record(r) for r in rows]
        gamma.set_markets(markets)
        spot.set(rows[0]["spot"], rows[0].get("sigma"), ts)
        for r in rows:
            clob.set_book(r["token_up"], _book_from_payload(r["token_up"], r.get("book_up")))
            clob.set_book(r["token_down"], _book_from_payload(r["token_down"], r.get("book_down")))
        runner._last_discovery = -1e12  # rediscover with the fresh market set
        runner.tick()

    # Final settlement pass past the last expiry so open positions resolve.
    last = records[-1]
    clock.t = max(r["end_ts"] for r in records) + 1.0
    spot.set(last["spot"], last.get("sigma"), clock.t)
    gamma.set_markets([])
    runner.tick()
    return runner
