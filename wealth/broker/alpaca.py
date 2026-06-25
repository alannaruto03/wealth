"""US stocks execution via Alpaca — paper or live.

Requires the optional ``alpaca-py`` dependency (``pip install wealth[stocks]``).
Keys from the environment, never code:
  WEALTH_ALPACA_KEY, WEALTH_ALPACA_SECRET

``mode='paper'`` uses Alpaca's paper endpoint (no real money). PaperBroker
remains the cross-market default; this exists so stocks can go live by config.
"""
from __future__ import annotations

import os
from typing import Dict

from wealth.broker.base import Broker, Order, Position


class AlpacaBroker(Broker):
    def __init__(self, mode: str = "paper"):
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
        except ImportError as exc:  # pragma: no cover - optional dep
            raise ImportError(
                "alpaca-py is required for stock trading: pip install 'wealth[stocks]'"
            ) from exc

        key = os.environ.get("WEALTH_ALPACA_KEY", "")
        secret = os.environ.get("WEALTH_ALPACA_SECRET", "")
        self.trading = TradingClient(key, secret, paper=(mode == "paper"))
        self.data = StockHistoricalDataClient(key, secret)
        self.mode = mode

    def get_price(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestQuoteRequest

        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quote = self.data.get_stock_latest_quote(req)[symbol]
        return float(quote.ask_price or quote.bid_price)

    def get_account(self) -> Dict[str, float]:
        acct = self.trading.get_account()
        return {"cash": float(acct.cash), "equity": float(acct.equity)}

    def get_positions(self) -> Dict[str, Position]:
        out: Dict[str, Position] = {}
        for p in self.trading.get_all_positions():
            out[p.symbol] = Position(p.symbol, float(p.qty), float(p.avg_entry_price))
        return out

    def submit_order(self, symbol: str, side: str, quantity: float) -> Order:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        result = self.trading.submit_order(req)
        price = float(getattr(result, "filled_avg_price", None) or self.get_price(symbol))
        return Order(symbol=symbol, side=side, quantity=quantity, price=price)
