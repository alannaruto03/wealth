"""Execution seam for binary event markets: the paper <-> live boundary.

The portfolio bot's Broker ABC deals in target weights and instant market
orders; Polymarket needs limit-priced buys of specific outcome tokens plus
settlement to $1/$0 at expiry, so it gets its own interface. The runner only
ever talks to PolymarketExecutor, keeping paper -> live a config change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional

from wealth.polymarket.clob import OrderBook
from wealth.polymarket.gamma import MarketInfo


@dataclass
class TokenPosition:
    token_id: str
    market_slug: str
    size: float        # shares held
    avg_price: float   # cost per share


@dataclass
class Fill:
    token_id: str
    side: str          # "buy" | "sell"
    size: float        # shares filled (0 if rejected)
    avg_price: float
    notional: float    # size * avg_price
    fee: float
    status: str        # "filled" | "partial" | "rejected"


class PolymarketExecutor(ABC):
    @abstractmethod
    def get_cash(self) -> float:
        """Free USDC."""

    @abstractmethod
    def get_positions(self) -> Dict[str, TokenPosition]:
        """{token_id -> TokenPosition} for nonzero holdings."""

    @abstractmethod
    def buy(self, market: MarketInfo, token_id: str, limit_price: float,
            size: float, book: Optional[OrderBook] = None) -> Fill:
        """Buy up to ``size`` shares at or below ``limit_price``."""

    @abstractmethod
    def sell(self, market: MarketInfo, token_id: str, limit_price: float,
             size: float, book: Optional[OrderBook] = None) -> Fill:
        """Sell up to ``size`` shares at or above ``limit_price``."""

    @abstractmethod
    def settle(self, market: MarketInfo, winning_token_id: Optional[str]) -> float:
        """Resolve the market: winning shares pay $1, losing pay $0.

        Returns realized PnL from settlement. ``winning_token_id`` may be None
        when the outcome is unknown (positions are then written off at cost —
        should not happen in practice).
        """

    def equity(self, marks: Dict[str, float]) -> float:
        """Cash plus positions marked at ``marks`` (token_id -> price)."""
        total = self.get_cash()
        for token_id, pos in self.get_positions().items():
            mark = marks.get(token_id, pos.avg_price)
            total += pos.size * mark
        return total
