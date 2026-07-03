"""EdgeSignal: fair value vs the book -> trade intents.

The strategy from the reference system: when the CLOB price for Up (or Down)
deviates from the model's fair probability by more than a threshold, cross the
spread and take it — speed over queue priority, since the edge decays as the
book readjusts. Exits either take profit when the book converges past fair, or
hold to resolution (default: these markets expire within the hour anyway).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from wealth.polymarket.clob import OrderBook
from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.executor import TokenPosition
from wealth.polymarket.gamma import MarketInfo


@dataclass
class TradeIntent:
    market: MarketInfo
    token_id: str
    side: str            # "buy" | "sell"
    limit_price: float
    fair: float          # fair value of THIS token
    edge: float
    size: Optional[float] = None  # shares; None for entries (sized later)
    reason: str = ""


@dataclass
class Guard:
    """A skipped market with the reason, for the journal."""
    market_slug: str
    reason: str


class EdgeSignal:
    def __init__(self, cfg: PolymarketConfig):
        self.cfg = cfg

    def _book_ok(self, book: Optional[OrderBook], now: float) -> Optional[str]:
        if book is None or book.best_bid is None or book.best_ask is None:
            return "empty_book"
        if book.age_s(now) > self.cfg.stale_book_max_s:
            return "stale_book"
        if book.spread is not None and book.spread > self.cfg.max_spread:
            return "wide_spread"
        return None

    def intents(
        self,
        market: MarketInfo,
        fair_up: float,
        book_up: Optional[OrderBook],
        book_down: Optional[OrderBook],
        positions: Dict[str, TokenPosition],
        now: float,
    ) -> tuple[List[TradeIntent], List[Guard]]:
        intents: List[TradeIntent] = []
        guards: List[Guard] = []
        fee = self.cfg.fee_bps / 10_000.0
        fair = {market.token_id_up: fair_up, market.token_id_down: 1.0 - fair_up}
        books = {market.token_id_up: book_up, market.token_id_down: book_down}

        # -- exits first (allowed even near expiry / on tripped kill switch) --
        if self.cfg.take_profit_edge is not None:
            for token_id, pos in positions.items():
                if token_id not in fair or pos.size <= 0:
                    continue
                book = books[token_id]
                problem = self._book_ok(book, now)
                if problem:
                    continue
                target = fair[token_id] + self.cfg.take_profit_edge
                if book.best_bid >= target:
                    intents.append(TradeIntent(
                        market=market, token_id=token_id, side="sell",
                        limit_price=book.best_bid, fair=fair[token_id],
                        edge=book.best_bid - fair[token_id],
                        size=pos.size, reason="take_profit",
                    ))

        # -- entries ----------------------------------------------------------
        if market.seconds_to_expiry(now) < self.cfg.min_seconds_to_expiry:
            guards.append(Guard(market.slug, "near_expiry"))
            return intents, guards

        best: Optional[TradeIntent] = None
        for token_id in (market.token_id_up, market.token_id_down):
            book = books[token_id]
            problem = self._book_ok(book, now)
            if problem:
                guards.append(Guard(market.slug, f"{problem}:{'up' if token_id == market.token_id_up else 'down'}"))
                continue
            ask = book.best_ask
            if not 0.01 <= ask <= 0.99:
                guards.append(Guard(market.slug, "extreme_price"))
                continue
            if book.depth_usd("ask") < self.cfg.min_book_depth_usd:
                guards.append(Guard(market.slug, "thin_book"))
                continue
            edge = fair[token_id] - ask - fee
            if edge <= self.cfg.edge_threshold:
                continue
            intent = TradeIntent(
                market=market, token_id=token_id, side="buy",
                limit_price=ask, fair=fair[token_id], edge=edge, reason="edge_entry",
            )
            if best is None or intent.edge > best.edge:
                best = intent  # never buy both sides of the same market
        if best is not None:
            intents.append(best)
        return intents, guards
