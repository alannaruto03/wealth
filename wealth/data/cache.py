"""On-disk parquet cache wrapping any DataProvider.

Makes backtests reproducible and offline-friendly: the first fetch hits the
network, subsequent identical requests read parquet. Cache key encodes market,
symbol, timeframe, and the requested date range.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Dict, List, Optional

import pandas as pd

from wealth.data.base import DataProvider


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


class CachedProvider(DataProvider):
    def __init__(self, inner: DataProvider, cache_dir: str = "data_cache", market: str = ""):
        self.inner = inner
        self.cache_dir = cache_dir
        self.market = market
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, symbol: str, start, end, timeframe: str) -> str:
        key = f"{self.market}|{symbol}|{timeframe}|{start}|{end}"
        digest = hashlib.sha1(key.encode()).hexdigest()[:10]
        name = f"{_safe(self.market)}_{_safe(symbol)}_{_safe(timeframe)}_{digest}.parquet"
        return os.path.join(self.cache_dir, name)

    def fetch_ohlcv(
        self,
        symbols: List[str],
        start: Optional[str] = None,
        end: Optional[str] = None,
        timeframe: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        out: Dict[str, pd.DataFrame] = {}
        missing: List[str] = []
        for sym in symbols:
            path = self._path(sym, start, end, timeframe)
            if os.path.exists(path):
                out[sym] = pd.read_parquet(path)
            else:
                missing.append(sym)

        if missing:
            fetched = self.inner.fetch_ohlcv(missing, start=start, end=end, timeframe=timeframe)
            for sym, df in fetched.items():
                path = self._path(sym, start, end, timeframe)
                try:
                    df.to_parquet(path)
                except Exception:
                    pass  # caching is best-effort; never fail a fetch over it
                out[sym] = df
        return out

    def latest_price(self, symbol: str, timeframe: str = "1d") -> float:
        # Live prices must never be cached/stale.
        return self.inner.latest_price(symbol, timeframe=timeframe)
