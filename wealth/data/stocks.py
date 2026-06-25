"""US stocks/ETFs OHLCV via yfinance."""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from wealth.data.base import DataProvider

_TIMEFRAME_TO_INTERVAL = {
    "1d": "1d",
    "1h": "1h",
    "1wk": "1wk",
    "1w": "1wk",
    "1mo": "1mo",
}


class YFinanceProvider(DataProvider):
    def fetch_ohlcv(
        self,
        symbols: List[str],
        start: Optional[str] = None,
        end: Optional[str] = None,
        timeframe: str = "1d",
    ) -> Dict[str, pd.DataFrame]:
        import yfinance as yf

        interval = _TIMEFRAME_TO_INTERVAL.get(timeframe, "1d")
        out: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            df = yf.download(
                sym,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=True,
                progress=False,
            )
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                # yfinance returns a (field, ticker) column MultiIndex for one symbol too.
                df.columns = df.columns.get_level_values(0)
            out[sym] = self._normalize(df)
        return out
