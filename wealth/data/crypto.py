"""Crypto OHLCV via ccxt (paginated to cover long histories)."""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from wealth.data.base import DataProvider

_TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}


class CCXTProvider(DataProvider):
    def __init__(self, exchange: str = "binance", **client_kwargs):
        self.exchange_id = exchange
        self.client_kwargs = client_kwargs
        self._client = None

    def _exchange(self):
        if self._client is None:
            import ccxt

            klass = getattr(ccxt, self.exchange_id)
            self._client = klass({"enableRateLimit": True, **self.client_kwargs})
        return self._client

    def fetch_ohlcv(
        self,
        symbols: List[str],
        start: Optional[str] = None,
        end: Optional[str] = None,
        timeframe: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        ex = self._exchange()
        tf_ms = _TIMEFRAME_MS.get(timeframe, _TIMEFRAME_MS["1d"])
        since = int(pd.Timestamp(start).timestamp() * 1000) if start else None
        until = int(pd.Timestamp(end).timestamp() * 1000) if end else None

        out: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            rows = self._fetch_all(ex, sym, timeframe, since, until, tf_ms)
            if not rows:
                continue
            df = pd.DataFrame(
                rows, columns=["ts", "open", "high", "low", "close", "volume"]
            )
            df.index = pd.to_datetime(df["ts"], unit="ms")
            df = df.drop(columns=["ts"])
            out[sym] = self._normalize(df)
        return out

    @staticmethod
    def _fetch_all(ex, symbol, timeframe, since, until, tf_ms, limit=1000):
        """Page through fetch_ohlcv until ``until`` (or no more data)."""
        all_rows = []
        cursor = since
        while True:
            batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit)
            if not batch:
                break
            all_rows.extend(batch)
            last_ts = batch[-1][0]
            if until is not None and last_ts >= until:
                break
            if len(batch) < limit:
                break
            cursor = last_ts + tf_ms
        if until is not None:
            all_rows = [r for r in all_rows if r[0] <= until]
        # De-duplicate by timestamp (exchanges sometimes overlap pages).
        seen = {}
        for r in all_rows:
            seen[r[0]] = r
        return [seen[k] for k in sorted(seen)]
