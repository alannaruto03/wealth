"""Crypto execution via ccxt — live or exchange testnet (sandbox).

Set ``mode='paper'`` to route through the exchange sandbox/testnet (safe), or
``mode='live'`` for real funds. API keys come from the environment, never code:
  WEALTH_CCXT_KEY, WEALTH_CCXT_SECRET

This is a thin wrapper; market orders only. It is intentionally not exercised by
the default paper workflow (PaperBroker is the default) and is here so going
live is a config switch.
"""
from __future__ import annotations

import os
from typing import Dict

from wealth.broker.base import Broker, Order, Position


class CCXTBroker(Broker):
    def __init__(self, exchange: str = "binance", mode: str = "paper", quote: str = "USDT"):
        import ccxt

        self.quote = quote
        klass = getattr(ccxt, exchange)
        self.client = klass(
            {
                "apiKey": os.environ.get("WEALTH_CCXT_KEY", ""),
                "secret": os.environ.get("WEALTH_CCXT_SECRET", ""),
                "enableRateLimit": True,
            }
        )
        if mode == "paper":
            # Exchange testnet/sandbox — no real money.
            self.client.set_sandbox_mode(True)
        self.mode = mode

    def get_price(self, symbol: str) -> float:
        return float(self.client.fetch_ticker(symbol)["last"])

    def get_account(self) -> Dict[str, float]:
        bal = self.client.fetch_balance()
        cash = float(bal.get("total", {}).get(self.quote, 0.0))
        equity = cash
        for sym, pos in self.get_positions().items():
            equity += pos.quantity * self.get_price(sym)
        return {"cash": cash, "equity": equity}

    def get_positions(self) -> Dict[str, Position]:
        bal = self.client.fetch_balance().get("total", {})
        out: Dict[str, Position] = {}
        for asset, qty in bal.items():
            if asset == self.quote or not qty:
                continue
            symbol = f"{asset}/{self.quote}"
            # avg_price is not tracked by the exchange balance; left at 0.
            out[symbol] = Position(symbol, float(qty), 0.0)
        return out

    def submit_order(self, symbol: str, side: str, quantity: float) -> Order:
        result = self.client.create_order(symbol, "market", side, quantity)
        price = float(result.get("average") or result.get("price") or self.get_price(symbol))
        return Order(symbol=symbol, side=side, quantity=quantity, price=price)
