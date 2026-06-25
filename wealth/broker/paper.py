"""PaperBroker — simulated fills on real live prices. The DEFAULT executor.

Holds simulated cash + positions, fills market orders at the latest price from
a price source, and applies the same commission/slippage model as the
backtester. State persists to JSON so the bot can stop and resume without
losing its book. Zero real money is ever involved.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Dict, Optional

from wealth.broker.base import Broker, Order, Position
from wealth.engine.costs import CostModel

# A price source is anything callable: symbol -> latest price.
PriceFn = Callable[[str], float]


class PaperBroker(Broker):
    def __init__(
        self,
        price_fn: PriceFn,
        starting_cash: float = 100_000.0,
        cost_model: Optional[CostModel] = None,
        state_path: Optional[str] = None,
    ):
        self.price_fn = price_fn
        self.cost_model = cost_model or CostModel()
        self.state_path = state_path
        self.cash = float(starting_cash)
        self.positions: Dict[str, Position] = {}
        self._last_prices: Dict[str, float] = {}
        if state_path and os.path.exists(state_path):
            self._load()

    # -- persistence ---------------------------------------------------------
    def _save(self) -> None:
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        payload = {
            "cash": self.cash,
            "positions": {
                s: {"quantity": p.quantity, "avg_price": p.avg_price}
                for s, p in self.positions.items()
            },
        }
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.state_path)

    def _load(self) -> None:
        with open(self.state_path) as f:
            payload = json.load(f)
        self.cash = float(payload.get("cash", self.cash))
        self.positions = {
            s: Position(s, d["quantity"], d["avg_price"])
            for s, d in payload.get("positions", {}).items()
        }

    # -- Broker interface ----------------------------------------------------
    def get_price(self, symbol: str) -> float:
        price = float(self.price_fn(symbol))
        self._last_prices[symbol] = price
        return price

    def get_positions(self) -> Dict[str, Position]:
        return {s: p for s, p in self.positions.items() if abs(p.quantity) > 1e-12}

    def get_account(self) -> Dict[str, float]:
        holdings = 0.0
        for sym, pos in self.positions.items():
            # Always mark to the current market price (live equity), not the
            # last fill. get_price refreshes the cache as a side effect.
            holdings += pos.quantity * self.get_price(sym)
        return {"cash": self.cash, "equity": self.cash + holdings}

    def submit_order(self, symbol: str, side: str, quantity: float) -> Order:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        sign = 1 if side == "buy" else -1
        mid = self.get_price(symbol)
        fill = self.cost_model.fill_price(mid, sign)
        cost = self.cost_model.cost(mid, quantity)

        self._apply_fill(symbol, sign * quantity, fill)
        self.cash -= sign * quantity * fill
        self.cash -= cost
        self._save()
        return Order(symbol=symbol, side=side, quantity=quantity, price=fill, cost=cost)

    def _apply_fill(self, symbol: str, signed_qty: float, fill: float) -> None:
        pos = self.positions.get(symbol)
        current = pos.quantity if pos else 0.0
        avg = pos.avg_price if pos else 0.0
        new_qty = current + signed_qty

        if current == 0.0 or (current > 0) == (signed_qty > 0):
            total = current + signed_qty
            new_avg = (current * avg + signed_qty * fill) / total if total != 0 else 0.0
        elif (current > 0 and new_qty < 0) or (current < 0 and new_qty > 0):
            new_avg = fill  # flipped through zero
        elif abs(new_qty) < 1e-12:
            new_avg = 0.0
        else:
            new_avg = avg  # partial reduce keeps basis

        if abs(new_qty) < 1e-12:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = Position(symbol, new_qty, new_avg)
