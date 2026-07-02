"""PaperExecutor: simulated fills against the LIVE order book.

Deliberately pessimistic so paper PnL is a bound, not a fantasy:

- Resting (maker) buys fill only when the live best ask crosses down to or
  through our price — and we pay OUR price, for at most the displayed size.
  No queue-priority credit, no fills from invisible flow.
- Resting sells mirror that against the best bid.
- Taker/pair orders fill immediately at the current best price for at most
  the displayed size, and pay the p*(1-p) taker fee.
- Settlement pays $1 per winning share, zero per losing share.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from wealth.polymarket.execution import Executor, Fill, OpenOrder
from wealth.polymarket.feeds import BookTop
from wealth.polymarket.fees import taker_fee


class PaperExecutor(Executor):
    def __init__(self, starting_cash: float, taker_fee_rate: float = 0.072):
        self._cash = starting_cash
        self.taker_fee_rate = taker_fee_rate
        self._orders: Dict[str, OpenOrder] = {}
        self._inv: Dict[str, float] = {"UP": 0.0, "DOWN": 0.0}
        self._fills: List[Fill] = []
        self.fees_paid = 0.0

    # -- Executor interface ---------------------------------------------------
    def place(self, token: str, side: str, price: float, size: float,
              kind: str, ts: float) -> Optional[OpenOrder]:
        order = OpenOrder(token=token, side=side, price=price, size=size,
                          kind=kind, ts=ts)
        if kind in ("taker", "pair"):
            self._fill(order, price, size, taker=True, ts=ts)
            return order
        if side == "sell" and size > self._inv[token] + 1e-9:
            return None  # no shorting outcome tokens
        self._orders[order.order_id] = order
        return order

    def cancel(self, order_id: str, ts: float) -> None:
        self._orders.pop(order_id, None)

    def cancel_all(self, ts: float) -> None:
        self._orders.clear()

    def open_orders(self) -> List[OpenOrder]:
        return list(self._orders.values())

    def position(self, token: str) -> float:
        return self._inv[token]

    def cash(self) -> float:
        return self._cash

    def settle_window(self, outcome: str, ts: float) -> float:
        self.cancel_all(ts)
        payout = self._inv.get(outcome, 0.0) * 1.0
        self._cash += payout
        self._inv = {"UP": 0.0, "DOWN": 0.0}
        return payout

    def drain_fills(self) -> List[Fill]:
        out, self._fills = self._fills, []
        return out

    # -- fill engine ------------------------------------------------------------
    def on_book(self, token: str, top: BookTop, ts: Optional[float] = None) -> None:
        """Check resting orders for ``token`` against a fresh book top."""
        ts = ts if ts is not None else time.time()
        for order in list(self._orders.values()):
            if order.token != token:
                continue
            if order.side == "buy" and top.ask is not None and top.ask <= order.price:
                size = min(order.remaining, top.ask_size)
                self._fill(order, order.price, size, taker=False, ts=ts)
            elif order.side == "sell" and top.bid is not None and top.bid >= order.price:
                size = min(order.remaining, top.bid_size)
                self._fill(order, order.price, size, taker=False, ts=ts)

    def _fill(self, order: OpenOrder, price: float, size: float,
              taker: bool, ts: float) -> None:
        if size <= 0:
            return
        if order.side == "buy":
            fee_per_share = (taker_fee(1.0, price, self.taker_fee_rate)
                             if taker else 0.0)
            unit_cost = price + fee_per_share
            if unit_cost * size > self._cash + 1e-9:
                size = max(0.0, self._cash / unit_cost) if unit_cost > 0 else 0.0
                if size <= 0:
                    return
            fee = fee_per_share * size
            self._cash -= price * size + fee
            self._inv[order.token] += size
        else:
            avail = min(size, self._inv[order.token])
            if avail <= 0:
                return
            size = avail
            fee = taker_fee(size, price, self.taker_fee_rate) if taker else 0.0
            self._cash += price * size - fee
            self._inv[order.token] -= size
        self.fees_paid += fee
        order.filled += size
        if order.remaining <= 1e-9:
            self._orders.pop(order.order_id, None)
        self._fills.append(Fill(
            token=order.token, side=order.side, price=price, size=size,
            fee=fee, kind=order.kind, order_id=order.order_id, ts=ts))
