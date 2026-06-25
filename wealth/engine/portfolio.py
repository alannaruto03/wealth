"""Portfolio accounting: cash, positions, equity, and trade records.

This is the PnL-correctness surface. The backtester drives it bar by bar:
  1. mark current positions to the bar's prices (equity update)
  2. rebalance to the bar's target weights, generating trades + costs

Realized PnL is tracked with average-cost basis so ``win_rate`` over closing
trades is meaningful.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from wealth.engine.costs import CostModel


@dataclass
class Trade:
    timestamp: object
    symbol: str
    side: int            # +1 buy, -1 sell
    quantity: float      # absolute units traded
    price: float         # slippage-adjusted fill price
    cost: float          # commission + slippage paid
    pnl: Optional[float] = None  # realized PnL on the closing portion, if any


@dataclass
class Portfolio:
    cash: float
    cost_model: CostModel = field(default_factory=CostModel)
    positions: Dict[str, float] = field(default_factory=dict)   # symbol -> units
    avg_cost: Dict[str, float] = field(default_factory=dict)    # symbol -> avg price
    trades: List[Trade] = field(default_factory=list)

    def equity(self, prices: Dict[str, float]) -> float:
        """Total mark-to-market value = cash + sum(position * price)."""
        val = self.cash
        for sym, qty in self.positions.items():
            if qty != 0.0 and sym in prices:
                val += qty * prices[sym]
        return val

    def _execute(self, timestamp, symbol: str, target_qty: float, price: float) -> None:
        """Trade ``symbol`` to reach ``target_qty`` units at market ``price``."""
        current = self.positions.get(symbol, 0.0)
        delta = target_qty - current
        if abs(delta) < 1e-12:
            return

        side = 1 if delta > 0 else -1
        fill = self.cost_model.fill_price(price, side)
        cost = self.cost_model.cost(price, delta)

        realized = self._update_position(symbol, delta, fill)

        # Cash: pay for buys (incl. cost), receive for sells (less cost).
        self.cash -= delta * fill
        self.cash -= cost

        self.trades.append(
            Trade(
                timestamp=timestamp,
                symbol=symbol,
                side=side,
                quantity=abs(delta),
                price=fill,
                cost=cost,
                pnl=realized,
            )
        )

    def _update_position(self, symbol: str, delta: float, fill: float) -> Optional[float]:
        """Apply a position change, returning realized PnL if the trade closes
        or reduces an existing position (average-cost basis)."""
        current = self.positions.get(symbol, 0.0)
        avg = self.avg_cost.get(symbol, 0.0)
        new_qty = current + delta
        realized: Optional[float] = None

        if current == 0.0 or (current > 0) == (delta > 0):
            # Opening or adding in the same direction -> blend average cost.
            total = current + delta
            if total != 0.0:
                self.avg_cost[symbol] = (current * avg + delta * fill) / total
        else:
            # Reducing or flipping -> realize PnL on the closed portion.
            closed = min(abs(delta), abs(current))
            direction = 1 if current > 0 else -1
            realized = direction * closed * (fill - avg)
            if (current > 0 and new_qty < 0) or (current < 0 and new_qty > 0):
                # Flipped through zero -> remaining opens at the fill price.
                self.avg_cost[symbol] = fill
            elif new_qty == 0.0:
                self.avg_cost[symbol] = 0.0
            # else: partial reduce keeps the same average cost.

        if abs(new_qty) < 1e-12:
            new_qty = 0.0
        self.positions[symbol] = new_qty
        return realized

    def rebalance(self, timestamp, target_weights: Dict[str, float], prices: Dict[str, float]) -> None:
        """Move toward ``target_weights`` (fraction of current equity) at ``prices``."""
        equity = self.equity(prices)
        if equity <= 0:
            return
        # Sell/reduce first so freed cash can fund buys (keeps it self-financing).
        targets = {}
        for sym, price in prices.items():
            w = target_weights.get(sym, 0.0)
            if price and price > 0:
                targets[sym] = (w * equity) / price
            else:
                targets[sym] = self.positions.get(sym, 0.0)

        def trade_delta(sym):
            return targets[sym] - self.positions.get(sym, 0.0)

        order = sorted(prices.keys(), key=lambda s: trade_delta(s))  # sells (neg) first
        for sym in order:
            self._execute(timestamp, sym, targets[sym], prices[sym])
