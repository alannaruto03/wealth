"""Trading cost model: commission + slippage.

Both are expressed in basis points (1 bp = 0.01%) of the traded notional.
Applied identically in the backtester and the PaperBroker so simulated and
backtested results are consistent.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 5.0
    slippage_bps: float = 5.0

    def cost(self, price: float, quantity: float) -> float:
        """Total cost (>= 0) for trading ``quantity`` units at ``price``.

        ``quantity`` may be negative (a sell); cost depends on absolute notional.
        """
        notional = abs(price * quantity)
        return notional * (self.commission_bps + self.slippage_bps) / 1e4

    def fill_price(self, price: float, side: int) -> float:
        """Slippage-adjusted fill price. ``side`` = +1 buy, -1 sell.

        Buyers pay up, sellers receive less.
        """
        return price * (1.0 + side * self.slippage_bps / 1e4)
