"""Data-provider interface and a small factory.

Providers return, per symbol, an OHLCV DataFrame indexed by a tz-naive
DatetimeIndex with lowercase columns: open, high, low, close, volume.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataProvider(ABC):
    """Fetch historical OHLCV bars for one or more symbols."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbols: List[str],
        start: Optional[str] = None,
        end: Optional[str] = None,
        timeframe: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        """Return {symbol -> OHLCV DataFrame}. Missing symbols are omitted."""

    def latest_price(self, symbol: str, timeframe: str = "1d") -> float:
        """Most recent close for ``symbol`` (used by the live runner/broker)."""
        data = self.fetch_ohlcv([symbol], timeframe=timeframe)
        df = data.get(symbol)
        if df is None or df.empty:
            raise RuntimeError(f"no price data for {symbol}")
        return float(df["close"].iloc[-1])

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        """Lowercase columns, keep OHLCV, ensure a sorted DatetimeIndex."""
        df = df.rename(columns={c: str(c).lower() for c in df.columns})
        keep = [c for c in OHLCV_COLUMNS if c in df.columns]
        df = df[keep].copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df.sort_index()


def get_provider(market: str, cache_dir: Optional[str] = "data_cache", **kwargs) -> DataProvider:
    """Factory: 'stocks' -> yfinance, 'crypto' -> ccxt. Optionally cached."""
    market = market.lower()
    if market in ("stocks", "stock", "equity", "equities"):
        from wealth.data.stocks import YFinanceProvider
        provider: DataProvider = YFinanceProvider(**kwargs)
    elif market in ("crypto", "cryptocurrency"):
        from wealth.data.crypto import CCXTProvider
        provider = CCXTProvider(**kwargs)
    else:
        raise ValueError(f"unknown market {market!r}; use 'stocks' or 'crypto'")

    if cache_dir:
        from wealth.data.cache import CachedProvider
        provider = CachedProvider(provider, cache_dir=cache_dir, market=market)
    return provider
