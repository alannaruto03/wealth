"""Executor seam: the strategy/runner talk to this, paper and live implement it.

Same philosophy as wealth.broker.base — the runner only ever sees this
interface, so paper -> live is a config change, not a code change.
"""
from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_order_seq = itertools.count(1)


@dataclass
class OpenOrder:
    token: str            # "UP" | "DOWN"
    side: str             # "buy" | "sell"
    price: float
    size: float           # shares
    kind: str             # "maker" | "taker" | "pair"
    order_id: str = field(default_factory=lambda: f"o{next(_order_seq)}")
    filled: float = 0.0
    ts: float = 0.0

    @property
    def remaining(self) -> float:
        return self.size - self.filled


@dataclass
class Fill:
    token: str
    side: str
    price: float
    size: float
    fee: float
    kind: str
    order_id: str
    ts: float


class Executor(ABC):
    """Places/cancels orders and tracks cash + per-token share inventory."""

    @abstractmethod
    def place(self, token: str, side: str, price: float, size: float,
              kind: str, ts: float) -> Optional[OpenOrder]:
        """Rest a maker order (kind='maker') or fill at market (taker/pair)."""

    @abstractmethod
    def cancel(self, order_id: str, ts: float) -> None: ...

    @abstractmethod
    def cancel_all(self, ts: float) -> None: ...

    @abstractmethod
    def open_orders(self) -> List[OpenOrder]: ...

    @abstractmethod
    def position(self, token: str) -> float: ...

    @abstractmethod
    def cash(self) -> float: ...

    @abstractmethod
    def settle_window(self, outcome: str, ts: float) -> float:
        """Pay $1 per winning share, zero the inventory; return the payout."""

    def drain_fills(self) -> List[Fill]:
        """Fills since the last call (default: none)."""
        return []

    def positions(self) -> Dict[str, float]:
        return {t: self.position(t) for t in ("UP", "DOWN")}
