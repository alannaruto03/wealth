"""Broker abstraction: the seam between strategy decisions and real orders.

The same Broker interface backs paper trading (default), crypto via ccxt, and
stocks via alpaca. The live Runner only ever talks to this interface, so moving
from paper to live is a config change, not a code change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class Order:
    symbol: str
    side: str        # "buy" | "sell"
    quantity: float  # absolute units
    price: float     # fill price (market)
    cost: float = 0.0
    status: str = "filled"


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_price: float


class Broker(ABC):
    @abstractmethod
    def get_account(self) -> Dict[str, float]:
        """Return at least {'cash': float, 'equity': float}."""

    @abstractmethod
    def get_positions(self) -> Dict[str, Position]:
        """Return {symbol -> Position} for nonzero holdings."""

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """Latest market price for ``symbol``."""

    @abstractmethod
    def submit_order(self, symbol: str, side: str, quantity: float) -> Order:
        """Submit a market order for ``quantity`` (absolute) units."""

    def cancel_order(self, order_id: str) -> None:  # pragma: no cover - market orders fill now
        """No-op for immediate market-order brokers; overridden if needed."""
        return None

    # -- shared helper -------------------------------------------------------
    def target_weights_to_orders(
        self, target_weights: Dict[str, float], prices: Dict[str, float]
    ) -> List[Order]:
        """Diff target weights against current positions and execute the gap.

        Sells are executed before buys so freed cash funds the buys.
        """
        equity = self.get_account()["equity"]
        positions = self.get_positions()
        desired_qty: Dict[str, float] = {}
        for sym, price in prices.items():
            w = target_weights.get(sym, 0.0)
            desired_qty[sym] = (w * equity) / price if price > 0 else 0.0

        deltas = {}
        for sym in prices:
            current = positions[sym].quantity if sym in positions else 0.0
            deltas[sym] = desired_qty[sym] - current

        orders: List[Order] = []
        for sym in sorted(deltas, key=lambda s: deltas[s]):  # sells (neg) first
            delta = deltas[sym]
            if abs(delta) < 1e-9:
                continue
            side = "buy" if delta > 0 else "sell"
            orders.append(self.submit_order(sym, side, abs(delta)))
        return orders
