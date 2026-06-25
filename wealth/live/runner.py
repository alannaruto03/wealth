"""The bot's single tick: data -> weights -> diff vs positions -> orders.

Reuses the *exact same* Strategy.generate_weights as the backtester, then takes
the last row (today's target). It diffs against the broker's current positions
and submits orders to close the gap. Idempotent per bar: if the latest bar has
already been traded (per the journal), the tick is a no-op so restarts and
double-fires never double-trade.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from wealth.broker.base import Broker, Order
from wealth.data.base import DataProvider
from wealth.live.journal import Journal
from wealth.strategies.base import Strategy


class Runner:
    def __init__(
        self,
        strategy: Strategy,
        broker: Broker,
        provider: DataProvider,
        symbols: List[str],
        timeframe: str = "1d",
        lookback_bars: int = 400,
        journal: Optional[Journal] = None,
    ):
        self.strategy = strategy
        self.broker = broker
        self.provider = provider
        self.symbols = symbols
        self.timeframe = timeframe
        self.lookback_bars = lookback_bars
        self.journal = journal

    def _fetch_window(self) -> Dict[str, pd.DataFrame]:
        data = self.provider.fetch_ohlcv(self.symbols, timeframe=self.timeframe)
        # Keep only the trailing window the strategy needs.
        return {s: df.tail(self.lookback_bars) for s, df in data.items() if not df.empty}

    def tick(self, force: bool = False) -> Dict:
        data = self._fetch_window()
        if not data:
            record = {"event": "tick", "status": "no_data"}
            if self.journal:
                self.journal.append(record)
            return record

        weights = self.strategy.generate_weights(data)
        bar_ts = str(weights.index[-1])

        # Idempotency: skip if we've already traded this bar.
        if not force and self.journal and self.journal.last_bar() == bar_ts:
            return {"event": "tick", "status": "already_traded", "bar_ts": bar_ts}

        target = {s: float(weights.iloc[-1].get(s, 0.0)) for s in self.symbols}
        prices = {s: self.broker.get_price(s) for s in self.symbols}

        orders: List[Order] = self.broker.target_weights_to_orders(target, prices)
        account = self.broker.get_account()

        record = {
            "event": "tick",
            "timestamp": str(pd.Timestamp.now()),
            "bar_ts": bar_ts,
            "status": "traded",
            "target_weights": target,
            "prices": prices,
            "orders": [
                {"symbol": o.symbol, "side": o.side, "quantity": o.quantity, "price": o.price}
                for o in orders
            ],
            "cash": account["cash"],
            "equity": account["equity"],
        }
        if self.journal:
            self.journal.append(record)
        return record
