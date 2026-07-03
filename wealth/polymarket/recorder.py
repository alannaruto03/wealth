"""BookRecorder: persist per-tick market snapshots for honest offline replay.

Historical CLOB depth isn't downloadable, so the only faithful backtest data
is what you record yourself. One JSONL line per market per tick carries
everything replay needs: spot, sigma, period open, both books.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional

from wealth.polymarket.clob import OrderBook
from wealth.polymarket.gamma import MarketInfo


class BookRecorder:
    def __init__(self, dir_path: str, filename: str = "books.jsonl"):
        self.path = os.path.join(dir_path, filename)
        os.makedirs(dir_path, exist_ok=True)

    @staticmethod
    def _book_payload(book: Optional[OrderBook]) -> Optional[Dict]:
        if book is None:
            return None
        return {"bids": book.bids, "asks": book.asks, "ts": book.ts}

    def snapshot(self, now: float, market: MarketInfo, info: Dict, spot: float,
                 sigma: Optional[float], book_up: Optional[OrderBook],
                 book_down: Optional[OrderBook]) -> None:
        record = {
            "ts": now,
            "slug": market.slug,
            "series": market.series,
            "question": market.question,
            "spot": spot,
            "sigma": sigma,
            "period_open": info.get("period_open"),
            "start_ts": market.start_ts,
            "end_ts": market.end_ts,
            "token_up": market.token_id_up,
            "token_down": market.token_id_down,
            "neg_risk": market.neg_risk,
            "book_up": self._book_payload(book_up),
            "book_down": self._book_payload(book_down),
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")
