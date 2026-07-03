"""PaperExecutor — simulated fills against real Polymarket order books.

Fills walk the actual book snapshot level by level (never just the mid), so
paper fills pay realistic price impact. Still optimistic versus live: a
snapshot has no queue, no latency, no adverse selection — treat paper PnL as
an upper bound. State persists to JSON (same tmp + os.replace pattern as
PaperBroker) so restarts keep the book, plus per-market metadata the runner
needs to settle expired markets after a restart.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional

from wealth.polymarket.clob import OrderBook, walk_book
from wealth.polymarket.executor import Fill, PolymarketExecutor, TokenPosition
from wealth.polymarket.gamma import MarketInfo

EPS = 1e-9


class PaperExecutor(PolymarketExecutor):
    def __init__(self, starting_cash: float = 1_000.0, fee_bps: float = 0.0,
                 state_path: Optional[str] = None):
        self.fee_bps = fee_bps
        self.state_path = state_path
        self.cash = float(starting_cash)
        self.positions: Dict[str, TokenPosition] = {}
        self.realized_pnl = 0.0
        # Runner-owned metadata persisted alongside the book (period opens,
        # day-anchor equity for the kill switch, ...). Opaque to the executor.
        self.meta: Dict = {}
        if state_path and os.path.exists(state_path):
            self._load()

    # -- persistence ---------------------------------------------------------
    def _save(self) -> None:
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        payload = {
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "positions": {
                t: {"market_slug": p.market_slug, "size": p.size, "avg_price": p.avg_price}
                for t, p in self.positions.items()
            },
            "meta": self.meta,
        }
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.state_path)

    def _load(self) -> None:
        with open(self.state_path) as f:
            payload = json.load(f)
        self.cash = float(payload.get("cash", self.cash))
        self.realized_pnl = float(payload.get("realized_pnl", 0.0))
        self.positions = {
            t: TokenPosition(t, d["market_slug"], d["size"], d["avg_price"])
            for t, d in payload.get("positions", {}).items()
        }
        self.meta = payload.get("meta", {})

    def save_meta(self) -> None:
        """Persist after runner-side meta updates."""
        self._save()

    # -- interface -----------------------------------------------------------
    def get_cash(self) -> float:
        return self.cash

    def get_positions(self) -> Dict[str, TokenPosition]:
        return {t: p for t, p in self.positions.items() if p.size > EPS}

    def buy(self, market: MarketInfo, token_id: str, limit_price: float,
            size: float, book: Optional[OrderBook] = None) -> Fill:
        if size <= 0 or book is None or not book.asks:
            return Fill(token_id, "buy", 0.0, 0.0, 0.0, 0.0, "rejected")
        filled, avg, notional = walk_book(book.asks, size, limit_price, is_ask=True)
        # Never spend more than available cash (fee included).
        fee_rate = self.fee_bps / 10_000.0
        max_notional = self.cash / (1.0 + fee_rate) if self.cash > 0 else 0.0
        if notional > max_notional + EPS:
            scale = max_notional / notional if notional > 0 else 0.0
            filled *= scale
            notional *= scale
        if filled <= EPS:
            return Fill(token_id, "buy", 0.0, 0.0, 0.0, 0.0, "rejected")
        fee = notional * fee_rate
        self.cash -= notional + fee

        pos = self.positions.get(token_id)
        if pos and pos.size > EPS:
            total = pos.size + filled
            new_avg = (pos.size * pos.avg_price + notional) / total
            self.positions[token_id] = TokenPosition(token_id, market.slug, total, new_avg)
        else:
            self.positions[token_id] = TokenPosition(token_id, market.slug, filled, avg)
        self._save()
        status = "filled" if filled >= size - EPS else "partial"
        return Fill(token_id, "buy", filled, avg, notional, fee, status)

    def sell(self, market: MarketInfo, token_id: str, limit_price: float,
             size: float, book: Optional[OrderBook] = None) -> Fill:
        pos = self.positions.get(token_id)
        if pos is None or pos.size <= EPS or size <= 0 or book is None or not book.bids:
            return Fill(token_id, "sell", 0.0, 0.0, 0.0, 0.0, "rejected")
        size = min(size, pos.size)
        filled, avg, notional = walk_book(book.bids, size, limit_price, is_ask=False)
        if filled <= EPS:
            return Fill(token_id, "sell", 0.0, 0.0, 0.0, 0.0, "rejected")
        fee = notional * self.fee_bps / 10_000.0
        self.cash += notional - fee
        self.realized_pnl += notional - fee - filled * pos.avg_price

        remaining = pos.size - filled
        if remaining <= EPS:
            self.positions.pop(token_id, None)
        else:
            self.positions[token_id] = TokenPosition(token_id, market.slug, remaining, pos.avg_price)
        self._save()
        status = "filled" if filled >= size - EPS else "partial"
        return Fill(token_id, "sell", filled, avg, notional, fee, status)

    def settle(self, market: MarketInfo, winning_token_id: Optional[str]) -> float:
        pnl = 0.0
        for token_id in [market.token_id_up, market.token_id_down]:
            pos = self.positions.pop(token_id, None)
            if pos is None or pos.size <= EPS:
                continue
            payout = pos.size * 1.0 if token_id == winning_token_id else 0.0
            self.cash += payout
            pnl += payout - pos.size * pos.avg_price
        self.realized_pnl += pnl
        self._save()
        return pnl
