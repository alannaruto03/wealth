"""LiveExecutor — real orders on the Polymarket CLOB via py-clob-client.

Same interface as PaperExecutor, so going live is a config change. The private
key is read from the environment (never from config files) and py-clob-client
is imported lazily so paper mode works without the extra installed.

v1 limitations, on purpose:
- Positions are tracked locally in the same JSON state as paper (reconcile
  against https://data-api.polymarket.com/positions?user=<funder> manually).
- settle() only books the expected outcome; redeeming winning shares is an
  on-chain CTF action you perform in the Polymarket UI (or with the CTF
  contract directly).
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional

from wealth.polymarket.clob import OrderBook
from wealth.polymarket.config import PolymarketConfig
from wealth.polymarket.executor import Fill, PolymarketExecutor, TokenPosition
from wealth.polymarket.gamma import MarketInfo
from wealth.polymarket.paper import PaperExecutor

POLYGON_CHAIN_ID = 137


class LiveExecutor(PolymarketExecutor):
    def __init__(self, cfg: PolymarketConfig):
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
            from py_clob_client.order_builder.constants import BUY, SELL
        except ImportError as exc:  # pragma: no cover - exercised via message test
            raise ImportError(
                "live mode needs py-clob-client — install with: pip install -e '.[polymarket]'"
            ) from exc

        key = os.environ.get(cfg.private_key_env, "")
        if not key:
            raise ValueError(
                f"live mode needs a Polygon private key in ${cfg.private_key_env}"
            )
        self._types = {"OrderArgs": OrderArgs, "OrderType": OrderType,
                       "Options": PartialCreateOrderOptions, "BUY": BUY, "SELL": SELL}
        self.cfg = cfg
        self.client = ClobClient(
            cfg.clob_url, key=key, chain_id=POLYGON_CHAIN_ID,
            signature_type=cfg.signature_type, funder=cfg.funder,
        )
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        # Local book mirrors paper-state persistence (cash tracked from fills;
        # top up/withdrawals require editing the state file or resetting).
        self._local = PaperExecutor(starting_cash=cfg.cash, fee_bps=cfg.fee_bps,
                                    state_path=cfg.state_path)

    # -- local mirror -----------------------------------------------------------
    @property
    def meta(self) -> Dict:
        return self._local.meta

    def save_meta(self) -> None:
        self._local.save_meta()

    def get_cash(self) -> float:
        return self._local.get_cash()

    def get_positions(self) -> Dict[str, TokenPosition]:
        return self._local.get_positions()

    # -- order placement ----------------------------------------------------------
    def _post(self, market: MarketInfo, token_id: str, side_const, limit_price: float,
              size: float) -> dict:
        t = self._types
        order_args = t["OrderArgs"](token_id=token_id, price=round(limit_price, 3),
                                    size=round(size, 2), side=side_const)
        options = t["Options"](neg_risk=market.neg_risk) if market.neg_risk else None
        signed = self.client.create_order(order_args, options)
        order_type = getattr(t["OrderType"], self.cfg.order_type)
        return self.client.post_order(signed, order_type)

    @staticmethod
    def _fill_from_response(resp: dict, token_id: str, side: str,
                            limit_price: float, requested: float) -> Fill:
        """Translate a post_order response into a Fill.

        FOK either fills fully or errors; for GTC we take makingAmount/
        takingAmount when present and otherwise assume resting (size 0 now).
        """
        if not resp or resp.get("errorMsg"):
            return Fill(token_id, side, 0.0, 0.0, 0.0, 0.0,
                        f"rejected:{(resp or {}).get('errorMsg', 'no_response')}")
        status = resp.get("status", "")
        if status in ("matched", "success") or resp.get("success"):
            taking = resp.get("takingAmount")
            making = resp.get("makingAmount")
            try:
                if side == "buy":
                    filled = float(taking) if taking else requested
                    notional = float(making) if making else requested * limit_price
                else:
                    filled = float(making) if making else requested
                    notional = float(taking) if taking else requested * limit_price
            except (TypeError, ValueError):
                filled, notional = requested, requested * limit_price
            avg = notional / filled if filled else 0.0
            return Fill(token_id, side, filled, avg, notional, 0.0, "filled")
        return Fill(token_id, side, 0.0, 0.0, 0.0, 0.0, f"resting:{status}")

    def buy(self, market: MarketInfo, token_id: str, limit_price: float,
            size: float, book: Optional[OrderBook] = None) -> Fill:
        resp = self._post(market, token_id, self._types["BUY"], limit_price, size)
        fill = self._fill_from_response(resp, token_id, "buy", limit_price, size)
        if fill.size > 0:
            self._local.cash -= fill.notional + fill.fee
            self._apply_local(market, token_id, fill.size, fill.avg_price)
        return fill

    def sell(self, market: MarketInfo, token_id: str, limit_price: float,
             size: float, book: Optional[OrderBook] = None) -> Fill:
        resp = self._post(market, token_id, self._types["SELL"], limit_price, size)
        fill = self._fill_from_response(resp, token_id, "sell", limit_price, size)
        if fill.size > 0:
            pos = self._local.positions.get(token_id)
            avg_cost = pos.avg_price if pos else 0.0
            self._local.cash += fill.notional - fill.fee
            self._local.realized_pnl += fill.notional - fill.fee - fill.size * avg_cost
            self._apply_local(market, token_id, -fill.size, avg_cost)
        return fill

    def _apply_local(self, market: MarketInfo, token_id: str, delta: float,
                     price: float) -> None:
        pos = self._local.positions.get(token_id)
        current = pos.size if pos else 0.0
        new_size = current + delta
        if new_size <= 1e-9:
            self._local.positions.pop(token_id, None)
        elif delta > 0:
            avg = ((current * pos.avg_price + delta * price) / new_size
                   if pos else price)
            self._local.positions[token_id] = TokenPosition(token_id, market.slug,
                                                            new_size, avg)
        else:
            self._local.positions[token_id] = TokenPosition(token_id, market.slug,
                                                            new_size, pos.avg_price)
        self._local._save()

    def settle(self, market: MarketInfo, winning_token_id: Optional[str]) -> float:
        # Books the expected payout locally; on-chain redemption is manual (v1).
        return self._local.settle(market, winning_token_id)
