"""LiveExecutor: real orders on the Polymarket CLOB (V2) — opt-in only.

Requires:
  - ``pip install -e '.[polymarket-live]'`` (py-clob-client-v2; the V1 SDK
    died with the April 2026 CLOB V2 / pUSD cutover)
  - ``mode: live`` in configs/polymarket.yaml (default is paper)
  - env: WEALTH_POLY_PK (Polygon private key). Optional for proxy wallets:
    WEALTH_POLY_FUNDER + WEALTH_POLY_SIG_TYPE.
  - a funded account (pUSD) with on-chain allowances approved, from a
    jurisdiction Polymarket serves (US IPs are geo-blocked for trading).

This adapter is intentionally thin and marked EXPERIMENTAL: it maps the
Executor interface onto the SDK but has not been exercised against a funded
account. Run paper first; read every line before trusting it with money.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

from wealth.polymarket.execution import Executor, Fill, OpenOrder

CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


class LiveExecutor(Executor):
    def __init__(self, token_ids: Dict[str, str], tick: float):
        pk = os.environ.get("WEALTH_POLY_PK")
        if not pk:
            raise RuntimeError(
                "live mode needs WEALTH_POLY_PK in the environment "
                "(and a funded Polymarket account). Use mode: paper otherwise.")
        try:
            from py_clob_client.client import ClobClient  # py-clob-client-v2
        except ImportError as e:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "live mode needs py-clob-client-v2: "
                "pip install -e '.[polymarket-live]'") from e

        kwargs = {"key": pk, "chain_id": POLYGON_CHAIN_ID}
        funder = os.environ.get("WEALTH_POLY_FUNDER")
        if funder:
            kwargs["funder"] = funder
            kwargs["signature_type"] = int(os.environ.get("WEALTH_POLY_SIG_TYPE", "2"))
        self.client = ClobClient(CLOB_HOST, **kwargs)
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        self.token_ids = token_ids          # {"UP": token_id, "DOWN": token_id}
        self.tick = tick
        self._local: Dict[str, OpenOrder] = {}
        self._fills: List[Fill] = []

    # -- helpers ---------------------------------------------------------------
    def _sdk(self):
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        return OrderArgs, OrderType, BUY, SELL

    # -- Executor interface -----------------------------------------------------
    def place(self, token: str, side: str, price: float, size: float,
              kind: str, ts: float) -> Optional[OpenOrder]:
        OrderArgs, OrderType, BUY, SELL = self._sdk()
        args = OrderArgs(
            token_id=self.token_ids[token],
            price=round(price, 6),
            size=round(size, 2),
            side=BUY if side == "buy" else SELL,
        )
        order_type = OrderType.FAK if kind in ("taker", "pair") else OrderType.GTC
        signed = self.client.create_order(args)
        resp = self.client.post_order(signed, order_type)
        if not resp or not resp.get("success"):
            return None
        order = OpenOrder(token=token, side=side, price=price, size=size,
                          kind=kind, ts=ts)
        order.order_id = resp.get("orderID", order.order_id)
        if kind == "maker":
            self._local[order.order_id] = order
        return order

    def cancel(self, order_id: str, ts: float) -> None:
        try:
            self.client.cancel(order_id)
        finally:
            self._local.pop(order_id, None)

    def cancel_all(self, ts: float) -> None:
        try:
            self.client.cancel_all()
        finally:
            self._local.clear()

    def open_orders(self) -> List[OpenOrder]:
        return list(self._local.values())

    def position(self, token: str) -> float:
        # Conservative: live position tracking should use the authenticated
        # user websocket / data API. This adapter tracks fills it has seen.
        return sum(f.size * (1 if f.side == "buy" else -1)
                   for f in self._all_fills if f.token == token)

    @property
    def _all_fills(self) -> List[Fill]:
        return getattr(self, "_fill_log", [])

    def cash(self) -> float:
        bal = self.client.get_balance_allowance()
        try:
            return float(bal.get("balance", 0)) / 1e6  # 6-decimals collateral
        except (TypeError, ValueError, AttributeError):
            return 0.0

    def settle_window(self, outcome: str, ts: float) -> float:
        # On-chain settlement is automatic (Chainlink Automation); winning
        # shares redeem to pUSD without action. Nothing to do locally.
        self.cancel_all(ts)
        return 0.0

    def drain_fills(self) -> List[Fill]:
        # Live fill tracking belongs on the user websocket channel; the
        # runner tolerates an empty list (journal shows orders, not fills).
        out, self._fills = self._fills, []
        if not hasattr(self, "_fill_log"):
            self._fill_log: List[Fill] = []
        self._fill_log.extend(out)
        return out
